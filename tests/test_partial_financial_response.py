"""Sparse originals stay sparse; valid received cells do not become approvals."""
import copy
import json
import unittest
from unittest.mock import patch

try:
    from . import test_ifind_metadata_runtime as controls
except ImportError:
    import test_ifind_metadata_runtime as controls

from researchhelper.contracts import ContractError
from researchhelper.evidence import current_query_reference
from researchhelper.financial_sources import (PARTIAL_FINANCIAL_POLICY, metadata_response,
    metadata_request_shapes, partial_financial_enabled)
from researchhelper.planning import input_fingerprint
from researchhelper.reports import compose
from researchhelper.source_inventory import inventory


class PartialFinancialResponseTests(unittest.TestCase):
    def setUp(self):
        self.control = controls.IFindMetadataRuntimeTests()
        self.control.setUpClass(); self.control.setUp()
        self.addCleanup(self.control.doCleanups)
        self.control.fixture = copy.deepcopy(self.control.fixture)
        self.task = self.control.task
        self.raw = self.control.fixture['basic']

    def collect(self):
        self.control.definitions()
        record = self.control.collect('basic_data_service', self.control.fixture['request'])
        record['question_ids'] = [self.task['questions'][0]['id']]
        return record

    def coverage(self, record):
        return record['metadata_contract']['parsed']

    def test_policy_is_bound_and_frozen_without_upgrading_an_old_task(self):
        self.assertEqual(self.task[PARTIAL_FINANCIAL_POLICY], 1)
        old = copy.deepcopy(self.task); old.pop(PARTIAL_FINANCIAL_POLICY)
        self.assertFalse(partial_financial_enabled(old))
        self.assertNotEqual(input_fingerprint(old), input_fingerprint(self.task))
        self.control.agent._adopt_method_knowledge(self.task)
        self.task.update(status='部分完成', gaps=['离线开关检查，不是实际研究。'])
        self.assertEqual(compose(self.task)[PARTIAL_FINANCIAL_POLICY], 1)

    def test_missing_company_keeps_all47_returned_companies_and_the_original_request(self):
        missing = self.raw['tables'].pop()['thscode']; before = copy.deepcopy(self.raw)
        record = self.collect(); coverage = self.coverage(record)
        self.assertEqual(record['status'], '成功待核验')
        self.assertEqual(record['snapshot'], before)
        self.assertEqual((coverage['requested_code_count'], coverage['returned_code_count']), (48, 47))
        self.assertEqual(coverage['returned_numeric_cell_count'], 94)
        self.assertEqual(coverage['missing_codes'], [missing])
        self.assertIsNotNone(current_query_reference(self.task, record))
        self.assertEqual(len(inventory(self.task)['records'][0]['groups']), 47)

    def test_omitted_column_is_not_filled_with_a_fake_null_or_zero(self):
        first = self.raw['tables'][0]; first['table'].pop('ths_revenue_stock')
        record = self.collect(); coverage = self.coverage(record)
        self.assertNotIn('ths_revenue_stock', record['snapshot']['tables'][0]['table'])
        self.assertEqual(coverage['missing_columns'], [dict(code=first['thscode'], field='ths_revenue_stock')])
        self.assertEqual(coverage['missing_values'], [])
        self.assertEqual(coverage['returned_numeric_cell_count'], 95)

    def test_a_code_without_a_value_table_keeps_two_explicit_missing_columns(self):
        self.raw['tables'][0].pop('table')
        record = self.collect()
        self.assertNotIn('table', record['snapshot']['tables'][0])
        self.assertEqual(len(self.coverage(record)['missing_columns']), 2)

    def test_null_and_zero_are_distinct_original_states(self):
        first = self.raw['tables'][0]['table']
        first['ths_revenue_stock'][0] = None; first['ths_np_atoopc_stock'][0] = 0
        record = self.collect(); coverage = self.coverage(record)
        self.assertEqual(len(coverage['missing_values']), 1)
        self.assertEqual(coverage['returned_numeric_cell_count'], 95)
        fields = inventory(self.task)['records'][0]['groups'][0]['fields']
        self.assertEqual({row['name']: row['status'] for row in fields},
            dict(ths_revenue_stock='empty_or_placeholder', ths_np_atoopc_stock='present_unverified'))

    def test_no_returned_tables_is_empty_not_a_fabricated_complete_universe(self):
        self.raw['tables'] = []
        record = self.collect(); coverage = self.coverage(record)
        self.assertEqual(record['status'], '成功但为空')
        self.assertEqual(coverage['returned_code_count'], 0)
        self.assertEqual(len(coverage['missing_codes']), 48)
        self.assertFalse(coverage['absence_proved'] or coverage['full_industry_universe_verified'])
        self.assertIsNone(current_query_reference(self.task, record))

    def test_foreign_code_is_still_quarantined(self):
        self.raw['tables'][0]['thscode'] = 'AAPL.US'
        record = self.collect()
        self.assertEqual(record['status'], '返回结构异常')
        self.assertIsNone(current_query_reference(self.task, record))

    def test_duplicate_code_cannot_substitute_for_a_missing_company(self):
        self.raw['tables'][-1] = copy.deepcopy(self.raw['tables'][0])
        record = self.collect()
        self.assertEqual(record['status'], '返回结构异常')
        self.assertIsNone(current_query_reference(self.task, record))

    def test_unrequested_column_is_not_silently_dropped(self):
        self.raw['tables'][0]['table']['unrequested_number'] = [1]
        record = self.collect()
        self.assertEqual(record['status'], '返回结构异常')
        self.assertIn('unrequested_number', record['snapshot']['tables'][0]['table'])

    def test_invalid_value_is_not_reclassified_as_a_missing_value(self):
        self.raw['tables'][0]['table']['ths_revenue_stock'] = [True]
        record = self.collect()
        self.assertEqual(record['status'], '返回结构异常')
        self.assertIsNone(current_query_reference(self.task, record))

    def test_partial_data_does_not_relax_the_reflected_report_period(self):
        self.raw['tables'].pop()
        self.raw['inputParams']['params'][0]['params'][1]['value'] = '20251231'
        record = self.collect()
        self.assertEqual(record['status'], '返回结构异常')

    def test_task_without_the_new_policy_keeps_the_old_strict_contract(self):
        self.task.pop(PARTIAL_FINANCIAL_POLICY)
        self.task['input_hash'] = input_fingerprint(self.task)
        self.raw['tables'].pop()
        record = self.collect()
        self.assertEqual(record['status'], '返回结构异常')

    def test_invalid_policy_is_rejected_before_any_request(self):
        for value in (True, '1', 2):
            self.task[PARTIAL_FINANCIAL_POLICY] = value
            with self.subTest(value=value), self.assertRaises(ContractError): metadata_request_shapes(self.task)
        self.assertEqual(self.control.calls, [])

    def test_sparse_coverage_survives_archive_restart_without_filling_rows(self):
        self.raw['tables'].pop(); record = self.collect()
        self.control.agent.archive.save(self.task)
        restored = self.control.agent.archive.get(self.task['id'])
        self.assertEqual(restored['source_records'][-1]['snapshot'], record['snapshot'])
        self.assertEqual(restored['ledger'], self.task['ledger'])
        self.assertEqual(inventory(restored), inventory(self.task))

    def test_received_numbers_do_not_become_approved_amounts_or_complete_answers(self):
        self.raw['tables'].pop(); record = self.collect(); coverage = self.coverage(record)
        self.assertFalse(coverage['financial_values_approved'] or coverage['units_verified']
            or coverage['currency_verified'] or coverage['response_coverage_complete'])
        self.assertEqual(self.task['evidence'], [])
        self.assertEqual(self.task['calculations'], [])

    def test_imported_sparse_response_cannot_claim_current_retrieval_coverage(self):
        self.raw['tables'].pop(); record = self.collect(); record.pop('acquisition')
        self.assertEqual(inventory(self.task)['records'], [])

    def test_inventory_recomputes_coverage_instead_of_trusting_mutable_derived_metadata(self):
        self.raw['tables'].pop(); record = self.collect()
        record['metadata_contract']['parsed']['returned_numeric_cell_count'] = 999999
        coverage = inventory(self.task)['records'][0]['retrieval_coverage']
        self.assertEqual(coverage['returned_numeric_cell_count'], 94)
        self.assertNotIn('13836472563', json.dumps(coverage))

    def test_all_value_columns_absent_still_explain_the_actual_retrieval_gap(self):
        for row in self.raw['tables']: row['table'] = {}
        record = self.collect()
        result = inventory(self.task)['records'][0]
        self.assertEqual(len(result['retrieval_coverage']['missing_columns']), 96)
        self.assertEqual(result['retrieval_coverage']['returned_numeric_cell_count'], 0)
        self.assertFalse(result['retrieval_coverage']['absence_proved'])
        self.assertEqual(self.coverage(record)['row_count'], 48)

    def test_standalone_legacy_parser_keeps_its_default_strict_shape(self):
        self.raw['tables'].pop()
        with self.assertRaises(ContractError):
            metadata_response('basic_data_service', self.control.fixture['request'], self.raw)

    def test_engine_reuses_the_same_sparse_original_without_spending_another_request(self):
        self.raw['tables'].pop(); record = self.collect(); before = len(self.control.calls)
        agent = self.control.agent; agent.collector = self.control.collector
        request = dict(id='reuse_sparse', source='ifind', operation='basic_data_service',
            parameters=self.control.fixture['request'], question_ids=[self.task['questions'][0]['id']],
            selection_reason='仅复用同任务同参数的已取得部分响应，不重复请求补齐')
        with patch.object(agent, '_save_model_checkpoint'), patch.object(agent, '_journal'):
            agent._acquire_sources(self.task, [request], metadata_dependency=True)
        self.assertEqual(len(self.control.calls), before)
        self.assertIn('reuse_sparse', record['data_need_ids'])

    def test_null_gap_order_stays_bound_to_the_request_after_restart(self):
        for field in self.raw['tables'][0]['table']: self.raw['tables'][0]['table'][field] = [None]
        record = self.collect(); expected = inventory(self.task)
        self.control.agent.archive.save(self.task)
        restored = self.control.agent.archive.get(self.task['id'])
        self.assertEqual(inventory(restored), expected)
        self.assertEqual([row['field'] for row in self.coverage(record)['missing_values']],
            [row['indicator'] for row in self.control.fixture['request']['indipara']])
