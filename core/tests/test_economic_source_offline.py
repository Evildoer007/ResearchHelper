"""Actual EDB transport/plan/archive boundaries, using synthetic responses only."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.economic_sources import ECONOMIC_REQUEST_REGISTRY, declared_economic_indicators, validate_economic_request
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (CAPABILITIES, FIELD_REGISTRY, FILING_REQUEST_REGISTRY, IDENTITY_REQUEST_REGISTRY,
    EvidenceCollector, data_request_contract, snapshot_record, _source_extract)
from researchhelper.financial_sources import FINANCIAL_REQUEST_REGISTRY
from researchhelper.reasoning import evidence_contract, extractable_source_views, validate_plan

ROOT = Path(__file__).resolve().parents[2]
PARAMS = {'indicators': 'M001620253', 'startdate': '2025-01-01', 'enddate': '2026-10-02'}
PAYLOAD = {'errorcode': 0, 'tables': [{'thscode': 'M001620253', 'time': ['2025-12-31'], 'table': {'value': [123.4]}}]}


class EconomicSourceTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / 'result/acceptance/economic-source';directory.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(prefix='isolated-', dir=directory);self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=False,
            source_credentials={'ifind': {'access_token': 'SYNTHETIC_ACCESS'}})
        self.task = self.agent.create('读取本研究指标M001620253的原始序列', entry='宏观', objects=['M001620253'],
            cutoff='2026-10-03T00:30:00+14:00', questions=[{'id': 'q1', 'question': '核对指标的定义和原始序列', 'kind': 'fact'}])
        self.collector = EvidenceCollector(False)
        guard = patch('socket.socket.connect', side_effect=AssertionError('External network forbidden'))
        guard.start();self.addCleanup(guard.stop)

    def request(self, params=None):
        return {'id': 'economic_series', 'source': 'ifind', 'operation': 'edb_service',
                'parameters': copy.deepcopy(params or PARAMS), 'question_ids': ['q1']}

    def test_public_operations_match_all_executable_registries(self):
        expected = set(FIELD_REGISTRY) | set(IDENTITY_REQUEST_REGISTRY) | set(FILING_REQUEST_REGISTRY) | set(FINANCIAL_REQUEST_REGISTRY) | set(ECONOMIC_REQUEST_REGISTRY)
        actual = [source + '.' + item['operation'] for source, cap in CAPABILITIES.items() for item in cap['registered_operations']]
        self.assertEqual(set(actual), expected);self.assertEqual(len(actual), len(set(actual)))
        tool = next(item for item in CAPABILITIES['ifind']['registered_operations'] if item['operation'] == 'edb_service')
        self.assertTrue(tool['raw_only']);self.assertFalse(tool['internal']);self.assertEqual(tool['fields'], [])
        self.assertNotIn('edb_service', [item['operation'] for item in CAPABILITIES['ifind']['documented_only']])

    def test_only_explicit_ascii_indicator_ids_are_declared(self):
        task = copy.deepcopy(self.task);task['objects'] = ['600000.SH', 'M002826938', 'M１２３４５６７８９']
        task['question'] = '指标M001620253，不能从目录正文或模型记忆猜代码。'
        task['source_records'] = [{'snapshot': {'indicator': 'M123456789'}}]
        self.assertEqual(declared_economic_indicators(task), ['M001620253', 'M002826938'])

    def test_tool_inventory_covers_all_registered_families_not_supplier_marketing(self):
        contract = data_request_contract(self.task)
        expected = set(FIELD_REGISTRY) | set(FILING_REQUEST_REGISTRY) | set(IDENTITY_REQUEST_REGISTRY) | set(FINANCIAL_REQUEST_REGISTRY) | set(ECONOMIC_REQUEST_REGISTRY)
        actual = [tool['qualified_id_for_lookup_only'] for tool in contract['tools']]
        self.assertEqual(set(actual), expected);self.assertEqual(len(actual), len(set(actual)))
        self.assertNotIn('basic_data_service', [tool['operation'] for tool in contract['tools']])
        self.assertTrue(next(tool for tool in contract['tools'] if tool['operation'] == 'edb_service')['raw_only'])

    def test_no_guessing_from_macro_topic_or_documentation_example(self):
        task = copy.deepcopy(self.task);task.update(question='GDP增长说明了什么？', objects=['中国经济'])
        self.assertEqual(declared_economic_indicators(task), [])
        with self.assertRaises(ContractError):validate_economic_request(task, 'edb_service', PARAMS)
        self.assertIsNone(next(tool for tool in data_request_contract(task)['tools'] if tool['operation'] == 'edb_service')['example'])

    def test_invalid_indicator_fields_fail_before_auth_and_budget(self):
        for indicator in ('M002826938', 'M001620253,M001620253', 'M001620253;M002826938', '600000.SH', ' M001620253', 'M１２３４５６７８９'):
            task = copy.deepcopy(self.task)
            with self.subTest(indicator=indicator), patch.object(self.collector, '_http_json') as wire:
                with self.assertRaises(ContractError):
                    self.collector.external(task, 'ifind', 'edb_service', dict(PARAMS, indicators=indicator), {'refresh_token': 'SYNTHETIC_REFRESH'})
                wire.assert_not_called();self.assertEqual(task, self.task)

    def test_future_inverted_and_malformed_dates_fail_before_auth(self):
        for changes in ({'enddate': '2026-10-03'}, {'startdate': '2026-10-03'}, {'startdate': '20250101'}, {'enddate': '2026-09-31'}):
            with self.subTest(changes=changes), patch.object(self.collector, '_http_json') as wire:
                with self.assertRaises(ContractError):
                    self.collector.external(copy.deepcopy(self.task), 'ifind', 'edb_service', dict(PARAMS, **changes), {'refresh_token': 'SYNTHETIC_REFRESH'})
                wire.assert_not_called()

    def test_empty_extra_parameters_and_missing_cutoff_are_rejected(self):
        for parameters in ({}, dict(PARAMS, functionpara={}), dict(PARAMS, indicators='')):
            with self.assertRaises(ContractError):validate_economic_request(self.task, 'edb_service', parameters)
        task = copy.deepcopy(self.task);task.pop('cutoff')
        with self.assertRaises(ContractError):validate_economic_request(task, 'edb_service', PARAMS)

    def test_valid_request_uses_https_original_body_and_one_charge(self):
        with patch.object(self.collector, '_http_json', return_value=PAYLOAD) as wire:
            record = self.collector.external(self.task, 'ifind', 'edb_service', copy.deepcopy(PARAMS), {'access_token': 'SYNTHETIC_ACCESS'})
        request = wire.call_args.args[1]
        self.assertEqual(request.full_url, 'https://quantapi.51ifind.com/api/v1/edb_service')
        self.assertEqual(json.loads(request.data), PARAMS)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 1)
        self.assertEqual(record['snapshot'], PAYLOAD);self.assertTrue(record['economic_contract']['raw_only'])
        self.assertFalse(record['economic_contract']['numeric_evidence_registered'])
        self.assertNotIn('SYNTHETIC_ACCESS', json.dumps(record))

    def test_offline_collector_blocks_even_valid_request(self):
        with patch('researchhelper.evidence.urllib.request.urlopen') as wire:
            with self.assertRaises(ContractError):EvidenceCollector(True).external(self.task, 'ifind', 'edb_service', PARAMS, {})
            wire.assert_not_called()
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)

    def test_model_plan_uses_same_macro_scope_validator(self):
        proposed = {'questions': [{k: v for k, v in q.items() if k != 'status'} for q in self.task['questions']],
                    'data_requests': [self.request()], 'calculations': []}
        self.assertTrue(validate_plan(self.task, proposed))
        proposed['data_requests'][0]['parameters']['indicators'] = 'M002826938'
        with self.assertRaises(ContractError):validate_plan(self.task, proposed)

    def test_planner_shape_has_explicit_ids_timezone_and_raw_limitations(self):
        tool = next(item for item in data_request_contract(self.task)['tools'] if item['operation'] == 'edb_service')
        self.assertEqual(tool['declared_indicators'], ['M001620253']);self.assertEqual(tool['date_timezone'], 'Asia/Shanghai')
        self.assertEqual(tool['example']['parameters']['enddate'], '2026-10-02')
        self.assertTrue(tool['raw_only']);self.assertEqual(tool['numeric_fields'], [])

    def test_import_keeps_original_values_without_numeric_authority(self):
        record = snapshot_record(self.task, 'ifind', 'edb_service', PARAMS, PAYLOAD)
        self.assertEqual(record['snapshot'], PAYLOAD);self.assertEqual(record['hash'], fingerprint(PAYLOAD))
        self.task['source_records'] = [record]
        source = next(item for item in evidence_contract(self.task)['sources'] if item['artifact_id'] == record['id'])
        self.assertTrue(source['economic_raw_only']);self.assertFalse(source['numeric_evidence_registered'])
        self.assertEqual(extractable_source_views(self.task), [])

    def test_relabelled_raw_macro_number_cannot_become_evidence(self):
        record = snapshot_record(self.task, 'ifind', 'edb_service', PARAMS, PAYLOAD)
        item = {'artifact': record, 'snapshot_hash': record['hash'], 'snapshot': PAYLOAD,
                'record_locator': {'table': 0, 'row': 0, 'field': 'value'}, 'value': 123.4, 'unit': '%'}
        with self.assertRaisesRegex(ContractError, '宏观原始序列'):_source_extract(item, self.task)

    def test_host_cannot_reuse_cached_out_of_scope_macro_request(self):
        record = snapshot_record(self.task, 'ifind', 'edb_service', PARAMS, PAYLOAD)
        task = copy.deepcopy(self.task);task.update(question='只研究M002826938', objects=['M002826938'], source_records=[record])
        before = copy.deepcopy(task)
        with patch.object(self.agent.collector, 'external') as wire:
            with self.assertRaises(ContractError):self.agent._acquire_sources(task, [self.request()])
            wire.assert_not_called()
        self.assertEqual(task, before)

    def test_host_cached_request_preserves_binding_without_extra_dispatch(self):
        record = snapshot_record(self.task, 'ifind', 'edb_service', PARAMS, PAYLOAD)
        self.task['source_records'] = [record]
        self.task.update(status='运行中', batch=1)
        self.task = self.agent.archive.save(self.task)
        with patch.object(self.agent.collector, 'external') as wire:self.agent._acquire_sources(self.task, [self.request()])
        wire.assert_not_called();self.assertEqual(record['question_ids'], ['q1'])
        self.assertEqual(record['data_need_ids'], ['economic_series']);self.assertEqual(self.task['ledger']['used']['data_requests'], 0)

    def test_macro_supplement_does_not_require_a_security_code(self):
        task = copy.deepcopy(self.task);task['questions'][0]['evidence_needs'] = ['宏观指标原始来源']
        options = self.agent._supplement_options(task)
        self.assertEqual(options['sources'], ['ifind']);self.assertEqual(options['source_question_ids'], {'ifind': ['q1']})

    def test_skill_reads_the_same_tools_and_retained_raw_snapshot(self):
        spec = importlib.util.spec_from_file_location('economic_skill_fixture', ROOT / 'core/session.py')
        module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        project = tempfile.TemporaryDirectory(prefix='economic-skill-', dir=ROOT / 'result/acceptance/economic-source');self.addCleanup(project.cleanup)
        session = module.Session(project.name)
        task = session.create('核对M001620253的指标含义与原始序列', entry='宏观', objects=['M001620253'], cutoff='2026-10-02T18:30:00+08:00')
        tool = next(item for item in session.tools(task['id'])['data_requests']['tools'] if item['operation'] == 'edb_service')
        self.assertTrue(tool['raw_only'])
        record = session.snapshot(task['id'], 'ifind', 'edb_service', PARAMS, PAYLOAD)
        self.assertEqual(record['snapshot'], PAYLOAD);self.assertTrue(record['economic_contract']['raw_only'])
        self.assertEqual(session.status(task['id'])['ledger']['used']['data_requests'], 0)

    def test_full_host_workflow_saves_limited_report_without_fake_macro_claims(self):
        task = self.agent.add_snapshot(self.task['id'], 'ifind', 'edb_service', PARAMS, PAYLOAD)
        outcome = self.agent.start(task['id'], {'data_requests': [self.request()], 'evidence_specs': [],
            'answers': {}, 'theses': [], 'calculations': []})
        self.assertTrue(outcome.get('delivery'), outcome.get('error'))
        self.assertEqual(outcome['evidence'], []);self.assertEqual(outcome['calculations'], [])
        self.assertTrue(any(question['status'] != '已回答' for question in outcome['questions']))
        self.assertEqual(outcome['source_records'][0]['snapshot'], PAYLOAD)
        self.assertEqual(outcome['ledger']['used']['data_requests'], 0)
        report = self.agent.archive.report(outcome['id'], outcome['version'])['html']
        self.assertNotIn('123.4', report);self.assertIn('M001620253', report)


if __name__ == '__main__':unittest.main()
