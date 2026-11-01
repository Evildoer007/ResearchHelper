"""Industry financial retrieval is batchable without approving unknown units."""
import copy
import hashlib
import json
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import ContractError
from researchhelper.financial_sources import (validate_financial_query, financial_query_response,
    financial_query_shapes, validate_financial_acquisition_role)
from researchhelper.output_schema import _planned_request_schemas, _matches
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.planning import input_fingerprint
from researchhelper.reports import compose

QUERY = '证券行业股票，2026年半年报营业收入，2026年半年报归属于母公司所有者的净利润'
FIXTURE = ROOT / 'result/acceptance/ifind-industry-financials-uugrfa9p/raw-response.json'


class IndustryFinancialBatchTests(unittest.TestCase):
    def setUp(self):
        self.task = dict(question='获取证券行业2026年半年报营业收入与归母净利润', objects=[],
            research_mode='current', cutoff='2026-10-21T10:00:00+08:00',
            financial_batch_query_version=1, financial_acquisition_route_version=1,
            object_discovery_version=2, discovery_planned_scope_version=1,
            questions=[dict(id='q1', question='核对证券行业半年报财务',
                evidence_needs=['营业收入', '归母净利润'], method_needs=[])])
        self.parameters = dict(searchstring=QUERY, searchtype='stock')

    def test_requested_sector_period_and_fields_are_registered_without_identity_fanout(self):
        result = validate_financial_query(self.task, self.parameters)
        self.assertEqual(result['query_kind'], 'financial_batch')
        self.assertEqual(result['topic'], '证券')
        self.assertEqual(result['period'], '20260630')
        self.assertEqual(result['fields'], ['营业收入', '归属于母公司所有者的净利润'])
        validate_financial_acquisition_role(self.task,
            dict(source='ifind', operation='smart_stock_picking', parameters=self.parameters, question_ids=['q1']))

    def test_legacy_tasks_do_not_silently_gain_a_new_request_contract(self):
        old = copy.deepcopy(self.task);old.pop('financial_batch_query_version')
        with self.assertRaises(ContractError):validate_financial_query(old, self.parameters)

    def test_unrelated_sector_future_period_or_unregistered_fields_are_rejected(self):
        for text in (QUERY.replace('证券', '煤炭'), QUERY.replace('2026', '2027'),
                     QUERY.replace('营业收入', '明日必涨'), QUERY + '，涨幅排名前十'):
            with self.subTest(query=text), self.assertRaises(ContractError):
                validate_financial_query(self.task, dict(searchstring=text, searchtype='stock'))

    def test_real_table_keeps_all_48_rows_and_original_columns_without_unit_inference(self):
        raw = FIXTURE.read_bytes()
        receipt = json.loads((FIXTURE.parent / 'receipt.json').read_text())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), receipt['response_sha256'])
        payload = json.loads(raw);before = copy.deepcopy(payload)
        result = financial_query_response(self.task, self.parameters, payload)
        self.assertEqual(result['row_count'], 48)
        self.assertEqual(result['returned_unique_codes'], 48)
        self.assertEqual(result['unmatched_fields'], [])
        self.assertFalse(result['unit_verified'] or result['numeric_evidence_registered']
                         or result['accounting_scope_verified'] or result['universe_completeness_verified'])
        self.assertEqual(result['returned_columns'], sorted(payload['tables'][0]['table']))
        self.assertEqual(payload, before)

    def test_missing_values_are_reported_without_deleting_other_rows(self):
        payload = json.loads(FIXTURE.read_text())
        payload['tables'][0]['table']['营业收入[20260630]'][0] = None
        result = financial_query_response(self.task, self.parameters, payload)
        self.assertEqual(result['row_count'], 48)
        self.assertEqual(result['missing_values'], [dict(table=0,row=0,code='000166.SZ',field='营业收入[20260630]')])

    def test_misaligned_columns_are_not_repaired_or_truncated(self):
        payload = json.loads(FIXTURE.read_text())
        payload['tables'][0]['table']['营业收入[20260630]'].pop()
        with self.assertRaises(ContractError):financial_query_response(self.task, self.parameters, payload)

    def test_batch_is_exposed_in_the_same_model_request_schema(self):
        tool = financial_query_shapes(self.task)[0]
        self.assertTrue(tool['financial_batch']['raw_only'])
        contract = dict(tools=[tool], callable_codes=[], question_ids=['q1'])
        schema = dict(anyOf=_planned_request_schemas(self.task, contract))
        wire = dict(id='batch_financials', source='ifind', operation='smart_stock_picking',
                    parameters=self.parameters, question_ids=['q1'], selection_reason='同一行业及期间的批量原始查询')
        self.assertTrue(_matches(wire, schema))

    def test_first_plan_and_later_navigation_offer_the_same_usable_batch_grammar(self):
        tool = financial_query_shapes(self.task)[0]
        contract = dict(tools=[tool], callable_codes=[], question_ids=['q1'])
        wire = dict(id='batch_financials', source='ifind', operation='smart_stock_picking',
                    parameters=self.parameters, question_ids=['q1'], selection_reason='同期间批量取回')
        with patch('researchhelper.evidence.data_request_contract', return_value=contract):
            schema = dict(anyOf=_planned_request_schemas(self.task))
        self.assertTrue(_matches(wire, schema))
        invalid = copy.deepcopy(wire)
        invalid['parameters']['searchstring'] = QUERY.replace('，', ',')
        self.assertFalse(_matches(invalid, schema))

    def test_new_default_policy_is_bound_and_frozen_without_rewriting_legacy_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = ResearchAgent(ResearchArchive(Path(directory)), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create(self.task['question'], research_mode='current')
            self.assertEqual(task['financial_batch_query_version'], 1)
            task['gaps'] = ['离线合成契约检查，不表示已取数']
            agent._adopt_method_knowledge(task)
            self.assertEqual(compose(task)['financial_batch_query_version'], 1)
            old = copy.deepcopy(task);old.pop('financial_batch_query_version')
            self.assertNotEqual(input_fingerprint(task), input_fingerprint(old))

    def test_unknown_policy_version_does_not_fall_back_to_a_looser_parser(self):
        for version in (True, '1', 2):
            task = dict(self.task, financial_batch_query_version=version)
            with self.assertRaises(ContractError):validate_financial_query(task, self.parameters)

    def test_duplicate_rows_and_conflicting_names_remain_explicit(self):
        payload = json.loads(FIXTURE.read_text())
        table = payload['tables'][0]['table']
        for key, values in table.items():values.append(values[0])
        table['股票简称'][-1] = '合成冲突名称'
        result = financial_query_response(self.task, self.parameters, payload)
        self.assertEqual(result['row_count'], 49)
        self.assertEqual(result['duplicate_codes'], ['000166.SZ'])
        self.assertEqual(result['conflicting_names'], ['000166.SZ'])
        self.assertFalse(result['numeric_evidence_registered'])

    def test_more_than_200_aligned_rows_are_not_truncated_and_zero_is_not_missing(self):
        table = {'股票代码':[f'60{i:04d}.SH' for i in range(201)],
                 '股票简称':[f'合成公司{i}' for i in range(201)],
                 '营业收入[20260630]':[0]*201,
                 '归属于母公司所有者的净利润[20260630]':[-1]*201}
        result = financial_query_response(self.task, self.parameters, dict(errorcode=0,tables=[dict(table=table)]))
        self.assertEqual(result['row_count'], 201)
        self.assertEqual(result['returned_unique_codes'], 201)
        self.assertEqual(result['missing_values'], [])


if __name__ == '__main__':
    unittest.main()
