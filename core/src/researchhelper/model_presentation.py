"""Lossless JSON presentation for research analysis, never a data filter.

Repeated records use column paths and shared fields. Large identical values
use document-local references. Canonical research objects and output schemas
are not changed; restore() provides an independently checkable inverse.
"""
import copy
from collections import Counter

from .contracts import ContractError, canonical

TABLE = '$rh_table'
REFERENCE = '$rh_ref'
LITERAL = '$rh_literal'
RESERVED = {TABLE, REFERENCE, LITERAL}
GUIDE = (
    '输入若使用researchhelper.table_input版本1，payload是完整研究输入，shared是本次输入内的共享值。'
    '仅含$rh_ref的对象引用shared中对应编号，不是证据编号或批准。'
    '仅含$rh_table的对象表示原记录数组：common逐项为[字段路径,共同值]，'
    'columns为其余字段路径，每条rows按列对应；每个路径是逐级JSON键数组。'
    '共同值须应用于每一行，所有行都保留，不把行号当原件位置。'
    '仅含$rh_literal的对象按键值对还原普通对象。'
    '先按这些规则理解完整输入，再按原输出Schema作答；不输出此输入包装，不改变任何原值、引用、限制或条件。'
)


def _leaves(value, path=()):
    if isinstance(value, dict) and value:
        for key in sorted(value):
            yield from _leaves(value[key], path + (key,))
    else:
        yield path, value


def _tables(value):
    if isinstance(value, dict):
        if len(value) == 1 and RESERVED.intersection(value):
            return {LITERAL: [[key, _tables(item)] for key, item in value.items()]}
        return {key: _tables(item) for key, item in value.items()}
    if not isinstance(value, list):
        return value
    ordinary = [_tables(item) for item in value]
    if len(value) < 8 or not all(isinstance(item, dict) and item for item in value):
        return ordinary
    flattened = [dict(_leaves(item)) for item in value]
    paths = tuple(flattened[0])
    # Different fields (including absent versus null) remain ordinary records.
    if any(tuple(record) != paths for record in flattened):
        return ordinary
    common, columns = [], []
    for path in paths:
        first = canonical(flattened[0][path])
        if all(canonical(record[path]) == first for record in flattened[1:]):
            common.append([list(path), _tables(flattened[0][path])])
        else:
            columns.append(list(path))
    rows = [[_tables(record[tuple(path)]) for path in columns] for record in flattened]
    table = {TABLE: dict(common=common, columns=columns, rows=rows)}
    return table if len(canonical(table)) + 64 < len(canonical(ordinary)) else ordinary


def present(value):
    """Return a smaller reversible representation or an unchanged deep copy."""
    tabled = _tables(value)
    counts = Counter()

    def count(item):
        if isinstance(item, (dict, list, str)):
            encoded = canonical(item)
            if len(encoded) >= 256:
                counts[encoded] += 1
        if isinstance(item, (dict, list)):
            for child in item.values() if isinstance(item, dict) else item:
                count(child)
    count(tabled)
    shared, references = [], {}

    def intern(item):
        if not isinstance(item, (dict, list, str)):
            return item
        encoded = canonical(item)
        reusable = counts[encoded] > 1 and (len(encoded) - 24) * (counts[encoded] - 1) > 64
        if reusable and encoded in references:
            return {REFERENCE: references[encoded]}
        if isinstance(item, dict):
            result = {key: intern(child) for key, child in item.items()}
        elif isinstance(item, list):
            result = [intern(child) for child in item]
        else:
            result = item
        if reusable:
            index = len(shared)
            shared.append(result)
            references[encoded] = index
            return {REFERENCE: index}
        return result

    payload = intern(tabled)
    result = dict(format='researchhelper.table_input', version=1, shared=shared, payload=payload)
    if len(canonical(result)) + len(GUIDE) + 128 >= len(canonical(value)):
        return copy.deepcopy(value), False
    if canonical(restore(result)) != canonical(value):
        raise ContractError('研究输入展示无法完整还原，未派发模型请求')
    return result, True


def restore(document):
    """Strict inverse, including original literal keys and scalar types."""
    if (not isinstance(document, dict) or set(document) != {'format', 'version', 'shared', 'payload'}
            or document['format'] != 'researchhelper.table_input' or document['version'] != 1
            or not isinstance(document['shared'], list)):
        raise ContractError('研究表格输入格式无效')
    shared = document['shared']
    resolved = {}

    def decode(item, active=()):
        if isinstance(item, list):
            return [decode(child, active) for child in item]
        if not isinstance(item, dict):
            return item
        if set(item) == {REFERENCE}:
            index = item[REFERENCE]
            if type(index) is not int or not 0 <= index < len(shared) or index in active:
                raise ContractError('研究输入共享引用无效')
            if index not in resolved:
                resolved[index] = decode(shared[index], active + (index,))
            return copy.deepcopy(resolved[index])
        if set(item) == {LITERAL}:
            return {key: decode(child, active) for key, child in item[LITERAL]}
        if set(item) == {TABLE}:
            table = decode(item[TABLE], active)
            paths = [pair[0] for pair in table['common']] + table['columns']
            if any(not path or any(not isinstance(key, str) for key in path) for path in paths):
                raise ContractError('研究输入表格字段路径无效')
            records = []
            for row in table['rows']:
                if len(row) != len(table['columns']):
                    raise ContractError('研究输入表格行长度无效')
                record = {}
                for path, child in table['common'] + list(zip(table['columns'], row)):
                    target = record
                    for key in path[:-1]:
                        target = target.setdefault(key, {})
                    target[path[-1]] = copy.deepcopy(child)
                records.append(record)
            return records
        return {key: decode(child, active) for key, child in item.items()}

    return decode(document['payload'])
