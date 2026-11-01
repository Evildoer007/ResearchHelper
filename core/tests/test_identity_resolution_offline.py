"""Exact identity response replay and shared workflow tests; no external calls.

The two explicit column shapes were observed in isolated iFinD samples. These
fixtures test interpretation, not supplier permissions or historical identity.
"""

import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (FIELD_REGISTRY, FILING_REQUEST_REGISTRY,
    IDENTITY_REQUEST_REGISTRY, available_security_codes, data_request_contract,
    identity_resolutions, snapshot_record, validate_ifind_report_request)
from researchhelper.planning import input_fingerprint, question_specs
from researchhelper.reasoning import validate_plan

ROOT = Path(__file__).resolve().parents[2]


class IdentityResolutionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='identity-resolution-')
        self.addCleanup(self.folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(self.folder.name), ROOT, offline=False,
                                   source_credentials={'ifind': {'access_token': 'OFFLINE_FIXTURE_ACCESS'}})
        self.task = self.agent.create('核对浦发银行600000的报价与公告', objects=['浦发银行', '600000'],
            cutoff='2026-10-01T12:00:00+08:00',
            questions=[{'id': 'q1', 'question': '报价和公告原文是什么？', 'kind': 'fact'}])

    def params(self, mode='secname', query='浦发银行'):
        return {mode: query, 'functionpara': {'mode': mode, 'sectype': '', 'market': '', 'tradestatus': '0', 'isexact': '1'}}

    def response(self, mode='secname', query='浦发银行', codes=None):
        return {'errorcode': 0, 'inputParams': {'mode': mode, 'isexact': 1},
                'tables': [{mode: query, 'table': {'thscode': codes if codes is not None else ['600000.SH']}}]}

    def retain(self, mode='secname', query='浦发银行', codes=None, response=None):
        record = snapshot_record(self.task, 'ifind', 'get_thscode', self.params(mode, query),
                                 response if response is not None else self.response(mode, query, codes))
        record.update(question_ids=['q1'], data_need_ids=['identity_query'])
        self.task['source_records'].append(record)
        return record

    def plan(self, needs):
        return {'questions': question_specs(self.task), 'calculations': [], 'data_requests': needs}

    def need(self, operation, params, ident='lookup'):
        return {'id': ident, 'source': 'ifind', 'operation': operation, 'parameters': params,
                'question_ids': ['q1'], 'selection_reason': '核对原输入的同一证券'}

    def quote(self):
        return self.need('cmd_history_quotation', {'codes': '600000.SH', 'indicators': 'close',
            'startdate': '2026-09-30', 'enddate': '2026-09-30',
            'functionpara': {'Currency': 'RMB', 'CPS': '1', 'Fill': 'Omit'}}, 'quote')

    def catalogue(self):
        return self.need('report_query', {'codes': '600000.SH', 'functionpara': {'reportType': '901'},
            'beginrDate': '2026-09-01', 'endrDate': '2026-09-30',
            'outputpara': FILING_REQUEST_REGISTRY['ifind.report_query']['parameters']['outputpara']}, 'catalogue')

    def test_additive_tools_match_all_implemented_registries(self):
        contract = data_request_contract(self.task)
        self.assertEqual({tool['qualified_id_for_lookup_only'] for tool in contract['tools']},
                         set(FIELD_REGISTRY) | set(FILING_REQUEST_REGISTRY) | set(IDENTITY_REQUEST_REGISTRY))
        self.assertNotIn('basic_data_service', [tool['operation'] for tool in contract['tools']])

    def test_code_and_name_exact_shapes_resolve_without_rewriting_user_input(self):
        original = copy.deepcopy(self.task['original_input'])
        digest = self.task['input_hash']
        for mode, query in [('seccode', '600000'), ('secname', '浦发银行')]:
            self.retain(mode, query)
        values = identity_resolutions(self.task)
        self.assertEqual([r['status'] for r in values], ['代码映射已核验'] * 2)
        self.assertEqual(available_security_codes(self.task), ['600000.SH'])
        self.assertTrue(all(not r['security_type_verified'] and not r['historical_identity_verified'] for r in values))
        self.assertEqual(self.task['original_input'], original)
        self.assertEqual(self.task['input_hash'], digest)
        self.assertEqual(data_request_contract(self.task)['declared_codes'], [])

    def test_lookup_can_be_planned_but_downstream_code_cannot_be_guessed(self):
        identity = self.need('get_thscode', self.params())
        validate_plan(self.task, self.plan([identity]))
        for need in [self.quote(), self.catalogue()]:
            with self.subTest(operation=need['operation']), self.assertRaises(ContractError):
                validate_plan(self.task, self.plan([identity, need]))

    def test_verified_mapping_enables_quote_catalogue_and_tinyshare_shapes(self):
        self.retain()
        contract = data_request_contract(self.task)
        self.assertEqual(contract['callable_codes'], ['600000.SH'])
        validate_plan(self.task, self.plan([self.quote(), self.catalogue()]))
        validate_ifind_report_request(self.task, self.catalogue()['parameters'])
        for tool in contract['tools']:
            if tool['example']:
                validate_plan(self.task, self.plan([tool['example']]))

    def test_empty_duplicate_and_multiple_candidates_remain_explicit(self):
        for codes, expected in [([], '无匹配'), (['600000.SH', '600000.SH'], '代码映射已核验'),
                                (['600000.SH', '600001.SH'], '多结果待消歧')]:
            self.task['source_records'] = []
            self.retain(codes=codes)
            self.assertEqual(identity_resolutions(self.task)[0]['status'], expected)
            self.assertEqual(available_security_codes(self.task), ['600000.SH'] if expected == '代码映射已核验' else [])

    def test_unsupported_candidate_is_not_filtered_to_manufacture_uniqueness(self):
        for codes in [['600000.SH', 'ABC.US'], ['600001.SH'], [None], ['60000.SH']]:
            self.task['source_records'] = []
            self.retain(mode='seccode', query='600000', codes=codes)
            self.assertEqual(available_security_codes(self.task), [])

    def test_unknown_schema_echo_mismatch_and_inexact_mode_are_not_accepted(self):
        for payload in [
            {'errorcode': 0, 'tables': []},
            self.response(query='其他公司'),
            dict(self.response(), inputParams={'mode': 'seccode', 'isexact': 1}),
            dict(self.response(), inputParams={'mode': 'secname', 'isexact': True}),
            dict(self.response(), inputParams={'mode': 'secname', 'isexact': 0}),
            dict(self.response(), tables=[{'secname': '浦发银行', 'thscode': '600000.SH'}]),
            dict(self.response(), tables=[{'secname': '浦发银行', 'table': {'thsCodes': ['600000.SH']}}]),
        ]:
            self.task['source_records'] = []
            self.retain(response=payload)
            self.assertEqual(available_security_codes(self.task), [])

    def test_tampering_stale_input_and_cross_task_never_expand_scope(self):
        record = self.retain()
        original = copy.deepcopy(self.task)
        for field, value in [('hash', 'changed'), ('request_hash', 'changed'), ('id', 'foreign'),
                             ('task_id', 'foreign'), ('research_version', 2), ('identity_origin', {})]:
            self.task = copy.deepcopy(original)
            self.task['source_records'][0][field] = value
            self.assertEqual(available_security_codes(self.task), [], field)
        for field, value in [('id', 'other_task'), ('version', 2), ('input_hash', 'changed'),
                             ('cutoff', '2026-09-01'), ('question', '核对其他公司')]:
            self.task = copy.deepcopy(original)
            self.task[field] = value
            self.assertEqual(available_security_codes(self.task), [], field)
        self.task = copy.deepcopy(original)
        self.task['source_records'][0]['snapshot']['tables'][0]['table']['thscode'] = ['600001.SH']
        self.assertEqual(available_security_codes(self.task), [])
        self.assertEqual(record['snapshot']['tables'][0]['table']['thscode'], ['600000.SH'])

    def test_conflicting_exact_name_responses_invalidate_both_mappings(self):
        self.retain()
        self.retain(codes=['600001.SH'])
        self.assertEqual([r['status'] for r in identity_resolutions(self.task)], ['映射冲突待核验'] * 2)
        self.assertEqual(available_security_codes(self.task), [])

    def test_claimed_success_cannot_override_business_failure(self):
        self.retain(response=dict(self.response(), errorcode=-4102))
        self.task['source_records'][0].update(status='成功待核验', source_check='已通过')
        self.assertEqual(available_security_codes(self.task), [])

    def test_contract_is_read_only_and_never_dispatches_or_creates_evidence(self):
        self.retain()
        original = copy.deepcopy(self.task)
        with patch('urllib.request.urlopen') as transport:
            data_request_contract(self.task)
            identity_resolutions(self.task)
        transport.assert_not_called()
        self.assertEqual(self.task, original)
        self.assertEqual(self.task['evidence'], [])

    def test_verified_mapping_creates_eligible_supplement_without_resetting_budget(self):
        self.task['data_need_specs'] = [self.need('get_thscode', self.params())]
        self.task['input_hash'] = input_fingerprint(self.task)
        self.assertEqual(self.agent._supplement_options(self.task)['sources'], [])
        self.retain()
        before = copy.deepcopy(self.task['ledger'])
        self.assertEqual(self.agent._supplement_options(self.task)['source_question_ids'], {'ifind': ['q1']})
        self.assertEqual(self.task['ledger'], before)

    def test_plan_extension_rechecks_original_without_rewriting_acquisition_binding(self):
        record = self.retain()
        acquired = copy.deepcopy(record['identity_origin'])
        self.task['data_need_specs'].append(self.quote())
        self.task['input_hash'] = input_fingerprint(self.task)
        values = identity_resolutions(self.task)
        self.assertEqual(values[0]['status'], '代码映射已核验')
        self.assertEqual(values[0]['acquired_binding'], acquired)
        self.assertEqual(values[0]['binding']['input_hash'], self.task['input_hash'])
        self.assertEqual(record['identity_origin'], acquired)

    def test_host_flow_identity_then_quote_catalogue_retains_actual_calls_and_scope(self):
        responses = [self.response(), {'errorcode': 0, 'tables': [{'thscode': '600000.SH',
            'time': ['2026-09-30'], 'table': {'close': [10.0]}}]}, {'errorcode': 0, 'tables': []}]
        with patch.object(self.agent.collector, '_http_json', side_effect=responses) as transport:
            final = self.agent.start(self.task['id'], {'data_requests': [self.need('get_thscode', self.params()),
                self.quote(), self.catalogue()]}, defer_delivery=True)
        self.assertEqual(transport.call_count, 3)
        self.assertEqual([r['operation'] for r in final['source_records']], ['get_thscode', 'cmd_history_quotation', 'report_query'])
        self.assertEqual(final['ledger']['used']['data_requests'], 3)
        self.assertEqual(final['ledger']['used']['model_calls'], 0)
        self.assertEqual(available_security_codes(final), ['600000.SH'])
        self.assertIn('证券类型、业务暴露及历史身份仍需另行核验', final['identity_state'])
        self.assertEqual(final['objects'], ['浦发银行', '600000'])
        self.assertEqual(final['evidence'], [])

    def test_skill_uses_same_read_only_mapping_contract(self):
        self.retain()
        self.agent.archive.save(self.task)
        spec = importlib.util.spec_from_file_location('identity_skill_session', ROOT / 'core/session.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        session = module.Session.__new__(module.Session)
        session.archive = self.agent.archive
        self.assertEqual(session.tools(self.task['id'])['data_requests'], data_request_contract(self.task))


if __name__ == '__main__':
    unittest.main()
