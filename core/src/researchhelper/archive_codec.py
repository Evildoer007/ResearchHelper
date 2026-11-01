"""Private, content-addressed storage; logical research objects stay unchanged.

Only large strings and source snapshots leave the JSON body. Their immutable
values remain in this same SQLite transaction, never in a shared SDK or a file
chosen by a model. Older plain JSON bodies remain readable without rewriting.
"""
import hashlib
import json
import re
import sqlite3
import zlib

from .contracts import ContractError, canonical, fingerprint, copy_json_value

STORAGE_KEY = '_researchhelper_archive_storage'
REFERENCE_KEY = '_researchhelper_archive_value'
LITERAL_KEY = '_researchhelper_archive_literal'
ROOT_KEY = '_researchhelper_archive_root'
THRESHOLD = 8192
MAX_PAYLOAD_BYTES = 256 * 1024 * 1024
MAX_MEMO_BYTES = 64 * 1024 * 1024


def _put(db, value, memo=None):
    raw = canonical(value).encode()
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise ContractError('单份归档值超过本地存储上限，未保存')
    digest = hashlib.sha256(raw).hexdigest()
    if memo is not None and db.in_transaction and digest in memo['values']:
        if memo['values'][digest] != raw:
            raise ContractError('归档值指纹冲突或原件已变化，未保存')
        return {REFERENCE_KEY: digest}
    try:
        row = db.execute('SELECT byte_count,compressed FROM archive_values WHERE id=?', (digest,)).fetchone()
    except sqlite3.DatabaseError:
        raise ContractError('归档值存储缺失，不能返回研究') from None
    if row is None:
        db.execute('INSERT INTO archive_values VALUES(?,?,?)', (digest, len(raw), zlib.compress(raw)))
    else:
        if canonical(_get(db, digest)) != raw.decode():
            raise ContractError('归档值指纹冲突或原件已变化，未保存')
    if (memo is not None and db.in_transaction and digest not in memo['values']
            and memo['byte_count'] + len(raw) <= MAX_MEMO_BYTES):
        memo['values'][digest] = raw
        memo['byte_count'] += len(raw)
    return {REFERENCE_KEY: digest}


def _get(db, digest):
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ContractError('归档值引用格式无效')
    try:
        row = db.execute('SELECT byte_count,compressed FROM archive_values WHERE id=?', (digest,)).fetchone()
    except sqlite3.DatabaseError:
        raise ContractError('归档值存储缺失，不能返回研究') from None
    if not row or type(row[0]) is not int or not 0 <= row[0] <= MAX_PAYLOAD_BYTES or not isinstance(row[1], bytes):
        raise ContractError('归档值缺失或长度无效，不能返回研究')
    try:
        reader = zlib.decompressobj()
        raw = reader.decompress(row[1], row[0] + 1)
        if len(raw) != row[0] or not reader.eof or reader.unused_data or reader.unconsumed_tail:
            raise ValueError('invalid compressed boundary')
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('changed payload')
        value = json.loads(raw)
        if canonical(value).encode() != raw:
            raise ValueError('noncanonical payload')
        return value
    except (ValueError, TypeError, zlib.error, UnicodeError):
        raise ContractError('归档值完整性不一致，不能返回研究') from None


def encode_body(db, value):
    """Caller owns the transaction; failures roll back both references and rows."""
    changed = False
    # Snapshot-scoped only. Always derive canonical bytes from each live value;
    # never trust Python identity, a prior save or an archive approval flag.
    memo = {'values': {}, 'byte_count': 0}

    def encode(item, key=None):
        nonlocal changed
        # Presentation is already independently frozen in deliveries.html.
        # Keep its checkpoint self-contained, including legacy report readers.
        if isinstance(item, str) and key != 'pending_html' and len(item.encode()) >= THRESHOLD:
            changed = True
            return _put(db, item, memo)
        if key == 'snapshot' and isinstance(item, (dict, list)) and len(canonical(item).encode()) >= THRESHOLD:
            changed = True
            return _put(db, item, memo)
        if isinstance(item, dict):
            if REFERENCE_KEY in item or LITERAL_KEY in item:
                changed = True
                return {LITERAL_KEY: [[key, encode(child, key)] for key, child in sorted(item.items())]}
            return {key: encode(child, key) for key, child in item.items()}
        if isinstance(item, list):
            return [encode(child) for child in item]
        return item

    if not isinstance(value, dict):
        raise ContractError('归档正文须为研究对象，未保存')
    encoded = encode(value)
    metadata = {'version': 1, 'logical_sha256': fingerprint(value)}
    if STORAGE_KEY in value or REFERENCE_KEY in value or LITERAL_KEY in value:
        metadata['literal_root'] = True
        return canonical({STORAGE_KEY: metadata, ROOT_KEY: encoded})
    if not changed:
        return canonical(value)
    encoded[STORAGE_KEY] = metadata
    return canonical(encoded)


def decode_body(db, body):
    value = json.loads(body)
    metadata = value.get(STORAGE_KEY) if isinstance(value, dict) else None
    if not isinstance(metadata, dict) or 'logical_sha256' not in metadata:
        return value
    if (type(metadata.get('version')) is not int or metadata['version'] != 1
            or set(metadata) - {'version', 'logical_sha256', 'literal_root'}
            or not isinstance(metadata['logical_sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', metadata['logical_sha256'])
            or 'literal_root' in metadata and metadata['literal_root'] is not True):
        raise ContractError('归档正文版本或元数据无效')
    cache = {}

    def decode(item):
        if isinstance(item, list):
            return [decode(child) for child in item]
        if not isinstance(item, dict):
            return item
        if set(item) == {REFERENCE_KEY}:
            digest = item[REFERENCE_KEY]
            if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
                raise ContractError('归档值引用格式无效')
            if digest not in cache:
                cache[digest] = _get(db, digest)
            # Objects are detached: mutating one evidence cannot change another.
            return copy_json_value(cache[digest])
        if set(item) == {LITERAL_KEY}:
            pairs = item[LITERAL_KEY]
            if (not isinstance(pairs, list) or any(not isinstance(pair, list) or len(pair) != 2
                    or not isinstance(pair[0], str) for pair in pairs) or len({pair[0] for pair in pairs}) != len(pairs)):
                raise ContractError('归档字面对象格式无效')
            return {key: decode(child) for key, child in pairs}
        if REFERENCE_KEY in item or LITERAL_KEY in item:
            raise ContractError('归档值引用不能混入其他字段')
        return {key: decode(child) for key, child in item.items()}

    if metadata.get('literal_root') is True:
        if set(value) != {STORAGE_KEY, ROOT_KEY}:
            raise ContractError('归档字面根对象格式无效')
        restored = decode(value[ROOT_KEY])
    else:
        restored = decode({key: child for key, child in value.items() if key != STORAGE_KEY})
    if not isinstance(restored, dict) or fingerprint(restored) != metadata['logical_sha256']:
        raise ContractError('归档正文完整性不一致，不能返回研究')
    return restored
