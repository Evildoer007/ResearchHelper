"""Declared reading scales within quota; values and source approval do not."""
import copy
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import ContractError, budget, fingerprint
from researchhelper.source_reader import (acquisition_reading_requests, binding,
    reading_request_capacity, _validate_selection, _complete_declared_catalog, CONTEXT_LIMIT)


class DeclaredReadingCapacityTests(unittest.TestCase):
    def setUp(self):
        self.task = dict(id='synthetic_reading', version=1, input_hash='synthetic-input', cutoff='2026-10-21',
            acquisition_reading_intent_version=1, declared_reading_capacity_version=1,
            ledger=budget(dict(data_requests=100, model_calls=8, seconds=600)),
            questions=[dict(id='q1')], source_records=[], materials=[], data_need_specs=[])
        items = []
        for index in range(14):
            ident = 'synthetic_' + str(index)
            parameters = dict(catalog_id='synthetic_catalog', source_id=str(index))
            digest = fingerprint(dict(source='ifind', operation='filing_original', parameters=parameters))
            snapshot = dict(material=dict(parts=[dict(locator='段落1', text='合成读取夹具，不是公司财务数据')]))
            hashed = fingerprint(snapshot)
            need = dict(id=ident, source='ifind', operation='filing_original', parameters=parameters,
                question_ids=['q1'], reading_queries=['营业收入', '合并利润表'])
            self.task['data_need_specs'].append(need)
            self.task['source_records'].append(dict(id=ident, source='ifind', kind='filing',
                operation='filing_original', task_id=self.task['id'], research_version=1,
                status='成功待核验', hash=hashed, snapshot=snapshot, request_hash=digest,
                data_need_ids=[ident], question_ids=['q1']))
            self.task['ledger']['dispatches'].append(dict(kind='data_requests', fingerprint=digest, status='成功'))
            items.append(dict(id=ident, kind='filing', status='已解析', snapshot_hash=hashed,
                request_binding=dict(artifact_id=ident, snapshot_hash=hashed)))
        self.catalogue = dict(binding=binding(self.task), items=items, offset=0, next_offset=None, total_artifacts=14)

    def selections(self, task=None):
        with patch('researchhelper.evidence.validate_source_request', side_effect=lambda task, source, operation, params:params):
            return acquisition_reading_requests(task or self.task, self.catalogue, ['q1'])

    def test_fourteen_explicit_download_intents_are_not_dropped_by_the_old_twelve_limit(self):
        before = fingerprint(self.task)
        requests = self.selections()
        self.assertEqual(len(requests), 14)
        self.assertEqual(len(_validate_selection(self.task, requests, binding(self.task), self.task['source_records'])), 14)
        self.assertEqual(fingerprint(self.task), before)
        self.assertEqual(CONTEXT_LIMIT, 80_000)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)

    def test_old_task_keeps_its_original_twelve_request_contract(self):
        old = copy.deepcopy(self.task); old.pop('declared_reading_capacity_version')
        self.assertIsNone(self.selections(old))
        with self.assertRaises(ContractError):
            _validate_selection(old, self.selections(), binding(old), old['source_records'])

    def test_missing_or_changed_actual_download_receipt_is_not_repaired(self):
        task = copy.deepcopy(self.task); task['ledger']['dispatches'][0]['status'] = '失败'
        self.assertIsNone(self.selections(task))
        task = copy.deepcopy(self.task); task['source_records'][0]['hash'] = 'changed'
        with self.assertRaises(ContractError): self.selections(task)

    def test_unknown_capacity_version_cannot_expand_reading(self):
        for value in (True, '1', 2):
            with self.subTest(value=value), self.assertRaises(ContractError):
                reading_request_capacity(dict(self.task, declared_reading_capacity_version=value))

    def test_metadata_pages_are_merged_only_from_the_same_actual_selection(self):
        items = [dict(id='synthetic_' + str(index)) for index in range(25)]
        first = dict(binding=binding(self.task), offset=0, next_offset=24, total_artifacts=25, items=items[:24])
        second = dict(binding=binding(self.task), offset=24, next_offset=None, total_artifacts=25, items=items[24:])
        with patch('researchhelper.source_reader.catalog', side_effect=[first, second]) as pages:
            merged = _complete_declared_catalog(self.task, first, [item['id'] for item in items])
            self.assertEqual(len(merged['items']), 25)
            self.assertIsNone(merged['next_offset'])
            self.assertEqual(pages.call_count, 2)
        stale = dict(first, total_artifacts=26)
        with patch('researchhelper.source_reader.catalog', return_value=first):
            self.assertIsNone(_complete_declared_catalog(self.task, stale, [item['id'] for item in items]))
