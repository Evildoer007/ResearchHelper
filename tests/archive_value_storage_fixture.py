"""Independent content-addressed Archive checks; private synthetic objects only."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from researchhelper.archive import ResearchArchive
from researchhelper.archive_codec import (STORAGE_KEY, REFERENCE_KEY, LITERAL_KEY, MAX_PAYLOAD_BYTES,
                                          encode_body, decode_body)
from researchhelper.contracts import ContractError, canonical


class ArchiveValueStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='independent-archive-values-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.archive = ResearchArchive(self.root)
        self.text = '独立归档软件夹具，不是实际研究或投资依据。' * 1200
        self.task = {'id': 'archive-values-task', 'version': 1, 'revision': 0, 'status': '待开始',
                     'materials': [], 'source_records': [], 'ledger': {'used': {'model_calls': 0, 'data_requests': 0}},
                     'evidence': [{'text': self.text}, {'text': self.text}]}

    def test_shared_large_strings_roundtrip_and_one_value(self):
        self.archive.save(self.task)
        self.assertEqual(self.archive.get(self.task['id']), self.task)
        self.assertEqual(self.archive.get(self.task['id'], 1), self.task)
        self.assertEqual(self.archive.list(), [self.task])
        with self.archive.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM archive_values').fetchone()[0], 1)
            body = db.execute('SELECT body FROM tasks').fetchone()[0]
        self.assertLess(len(body.encode()), len(canonical(self.task).encode()) // 4)
        self.assertNotIn(self.text, body)
        self.assertNotIn(STORAGE_KEY, self.archive.get(self.task['id']))

    def test_small_body_and_presentation_remain_plain_json(self):
        value = {'id': 'plain-task', 'pending_html': '<!doctype html>' + self.text, 'status': '待开始'}
        with self.archive.connect() as db:
            body = encode_body(db, value)
            self.assertEqual(body, canonical(value))
            self.assertEqual(decode_body(db, body), value)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM archive_values').fetchone()[0], 0)

    def test_plain_legacy_row_read_without_rewrite(self):
        legacy = copy.deepcopy(self.task)
        legacy['revision'] = 1
        raw = canonical(legacy)
        with self.archive.connect() as db:
            db.execute('INSERT INTO tasks VALUES(?,?,?)', (legacy['id'], 1, raw))
            db.execute('INSERT INTO versions VALUES(?,?,?)', (legacy['id'], 1, raw))
        self.assertEqual(self.archive.get(legacy['id']), legacy)
        with self.archive.connect() as db:
            self.assertEqual(db.execute('SELECT body FROM tasks').fetchone()[0], raw)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM archive_values').fetchone()[0], 0)

    def test_legacy_write_adopts_storage_not_business_version(self):
        self.task['revision'] = 1
        with self.archive.connect() as db:
            db.execute('INSERT INTO tasks VALUES(?,?,?)', (self.task['id'], 1, canonical(self.task)))
        self.archive.save(self.task)
        self.assertEqual((self.task['version'], self.task['revision']), (1, 2))
        self.assertEqual(self.archive.get(self.task['id'])['ledger'], self.task['ledger'])

    def test_source_snapshot_values_are_bound_and_detached(self):
        snapshot = {'rows': [{'field': '原件字面值', 'description': '独立数据口径'}] * 700}
        self.task['source_records'] = [{'snapshot': snapshot}, {'snapshot': copy.deepcopy(snapshot)}]
        self.archive.save(self.task)
        loaded = self.archive.get(self.task['id'])
        loaded['source_records'][0]['snapshot']['rows'][0]['field'] = '局部修改'
        self.assertEqual(loaded['source_records'][1]['snapshot']['rows'][0]['field'], '原件字面值')
        self.assertEqual(self.archive.get(self.task['id']), self.task)

    def test_reserved_user_keys_and_root_metadata_are_literal(self):
        for value in [
            {'nested': {REFERENCE_KEY: 'user-selected-text'}},
            {'nested': {LITERAL_KEY: [['user', self.text]]}},
            {STORAGE_KEY: {'version': 1, 'logical_sha256': 'user-value'}, 'nested': self.text},
            {REFERENCE_KEY: 'user-value', 'nested': self.text},
        ]:
            with self.archive.connect() as db:
                self.assertEqual(decode_body(db, encode_body(db, value)), value)

    def test_missing_or_corrupt_value_never_returns_a_task(self):
        self.archive.save(self.task)
        with self.archive.connect() as db:
            db.execute('UPDATE archive_values SET compressed=?', (b'not a compressed value',))
        with self.assertRaisesRegex(ContractError, '归档值完整性'):
            self.archive.get(self.task['id'])
        with self.archive.connect() as db:
            db.execute('DELETE FROM archive_values')
        with self.assertRaisesRegex(ContractError, '归档值缺失'):
            self.archive.get(self.task['id'])

    def test_claimed_oversize_and_wrong_size_are_rejected(self):
        self.archive.save(self.task)
        for size in [MAX_PAYLOAD_BYTES + 1, -1, 1]:
            with self.archive.connect() as db:
                db.execute('UPDATE archive_values SET byte_count=?', (size,))
            with self.assertRaises(ContractError):
                self.archive.get(self.task['id'])

    def test_wrong_reference_or_mixed_reference_fields_are_rejected(self):
        self.archive.save(self.task)
        with self.archive.connect() as db:
            original = db.execute('SELECT body FROM tasks').fetchone()[0]
        for reference in [{REFERENCE_KEY: '../private-path'}, {REFERENCE_KEY: '0' * 64},
                          {REFERENCE_KEY: '0' * 64, 'added': True}]:
            changed = json.loads(original)
            changed['evidence'][0]['text'] = reference
            with self.archive.connect() as db:
                db.execute('UPDATE tasks SET body=?', (canonical(changed),))
            with self.assertRaises(ContractError):
                self.archive.get(self.task['id'])

    def test_envelope_digest_binds_small_fields_as_well(self):
        self.archive.save(self.task)
        with self.archive.connect() as db:
            value = json.loads(db.execute('SELECT body FROM tasks').fetchone()[0])
            value['status'] = '已完成'
            db.execute('UPDATE tasks SET body=?', (canonical(value),))
        with self.assertRaisesRegex(ContractError, '归档正文完整性'):
            self.archive.get(self.task['id'])

    def test_transaction_failure_rolls_back_new_values_and_task(self):
        self.archive.save(self.task)
        before = copy.deepcopy(self.task)
        with self.archive.connect() as db:
            count = db.execute('SELECT COUNT(*) FROM archive_values').fetchone()[0]
            db.execute("CREATE TRIGGER reject_version BEFORE INSERT ON versions BEGIN SELECT RAISE(ABORT,'independent failure'); END")
        self.task['evidence'].append({'text': self.text + '新内容'})
        with self.assertRaises(sqlite3.IntegrityError):
            self.archive.save(self.task)
        self.assertEqual(self.archive.get(before['id']), before)
        with self.archive.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM archive_values').fetchone()[0], count)

    def test_stale_writer_cannot_adopt_values_or_reset_revision(self):
        self.archive.save(self.task)
        stale = copy.deepcopy(self.task)
        self.archive.save(self.task)
        stale['evidence'].append({'text': self.text + '迟到内容'})
        with self.assertRaises(ContractError):
            self.archive.save(stale)
        self.assertEqual(self.archive.get(self.task['id']), self.task)

    def test_original_checkpoint_bytes_and_metadata_survive_restart(self):
        self.archive.save(self.task)
        payload = self.text.encode()
        ident = self.archive.retain_original(self.task, 'material', '独立夹具.txt', payload)
        restarted = ResearchArchive(self.root)
        value, recovered = restarted.original(self.task, ident)
        self.assertEqual(recovered, payload)
        self.assertEqual(value['sha256'], hashlib.sha256(payload).hexdigest())
        metadata = restarted.retained_originals(self.task)
        self.assertEqual(len(metadata), 1)
        self.assertNotIn('original_base64', metadata[0])
        with self.assertRaises(ContractError):
            restarted.original(dict(self.task, version=2), ident)

    def test_other_archive_cannot_resolve_private_values(self):
        self.archive.save(self.task)
        with self.archive.connect() as db:
            body = db.execute('SELECT body FROM tasks').fetchone()[0]
        other = ResearchArchive(self.root / 'independent-store')
        with other.connect() as db:
            with self.assertRaises(ContractError):
                decode_body(db, body)

    def test_non_string_reference_and_missing_table_are_controlled(self):
        self.archive.save(self.task)
        with self.archive.connect() as db:
            original = db.execute('SELECT body FROM tasks').fetchone()[0]
            changed = json.loads(original)
            changed['evidence'][0]['text'] = {REFERENCE_KEY: []}
            with self.assertRaises(ContractError):
                decode_body(db, canonical(changed))
            db.execute('DROP TABLE archive_values')
            with self.assertRaises(ContractError):
                decode_body(db, original)

    def test_separate_archive_writers_cannot_overwrite_each_other(self):
        self.archive.save(self.task)
        second = ResearchArchive(self.root)
        barrier = threading.Barrier(2)
        outcomes = []

        def write(archive, text):
            task = copy.deepcopy(self.task)
            task['evidence'].append({'text': self.text + text})
            barrier.wait(timeout=5)
            try:
                archive.save(task)
                outcomes.append('saved')
            except ContractError:
                outcomes.append('stale')

        writers = [threading.Thread(target=write, args=(archive, str(index)))
                   for index, archive in enumerate((self.archive, second))]
        for writer in writers:
            writer.start()
        for writer in writers:
            writer.join(timeout=15)
            self.assertFalse(writer.is_alive())
        self.assertCountEqual(outcomes, ['saved', 'stale'])
        self.assertEqual(self.archive.get(self.task['id'])['revision'], 2)
        with self.archive.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM archive_values').fetchone()[0], 2)
