"""Financial API acquisition, planning and archival using fabricated responses."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, budget, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (CAPABILITIES, FIELD_REGISTRY, EvidenceCollector,
                                    FILING_REQUEST_REGISTRY, IDENTITY_REQUEST_REGISTRY,
                                    candidate_evidence, data_request_contract, snapshot_record)
from researchhelper.financial_sources import FINANCIAL_RECORD_KEYS, FINANCIAL_REQUEST_REGISTRY
from researchhelper.planning import question_specs
from researchhelper.reasoning import evidence_contract, validate_plan
from researchhelper.reports import compose


ROOT = Path(__file__).resolve().parents[2]


class FinancialSourceWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.archive = ResearchArchive(Path(temporary.name))
        self.agent = ResearchAgent(self.archive, offline=True)
        self.task = self.agent.create('核对600000.SH的2026年半年报收入口径，不推断投资收益。',
            entry='个股', intent='解释变化', objects=['600000.SH'], cutoff='2026-10-02T18:00:00+08:00')
        self.params = {'ts_code': '600000.SH', 'fields': ','.join(FINANCIAL_RECORD_KEYS) + ',revenue',
            'start_date': '20260701', 'end_date': '20260930', 'period': '20260630', 'report_type': '1'}
        self.row = {'ts_code': '600000.SH', 'ann_date': '20260828', 'f_ann_date': '20260828',
            'end_date': '20260630', 'report_type': '1', 'comp_type': '2', 'update_flag': '1', 'revenue': 123.5}
        self.payload = {'code': 0, 'data': [self.row]}
        network = patch('socket.socket.connect', side_effect=AssertionError('Real network forbidden'))
        network.start()
        self.addCleanup(network.stop)

    def plan(self):
        return {'questions': question_specs(self.task), 'calculations': [], 'data_requests': [{
            'id': 'financial_income', 'source': 'tinyshare', 'operation': 'income', 'parameters': self.params,
            'question_ids': [self.task['questions'][0]['id']],
            'selection_reason': '核对已登记财务字段原始记录；iFinD财务字段未登记，不静默换源。'}]}

    def test_model_plan_uses_explicit_financial_family_and_selection_reason(self):
        validate_plan(self.task, self.plan())
        for change in ({'selection_reason': ''}, {'operation': 'income_vip'}, {'operation': 'tinyshare.income'},
                       {'parameters': dict(self.params, period='20261231')},
                       {'parameters': dict(self.params, report_type='2')},
                       {'parameters': dict(self.params, ts_code='600001.SH')},
                       {'source': 'ifind', 'operation': 'basic_data_service'}):
            proposal = self.plan()
            proposal['data_requests'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_plan(self.task, proposal)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)

    def test_tools_separate_raw_financial_fields_from_numeric_evidence(self):
        before = fingerprint(self.task)
        tools = data_request_contract(self.task)['tools']
        financial = [tool for tool in tools if tool['operation'] in ('income', 'cashflow')]
        self.assertEqual(len(financial), 2)
        for tool in financial:
            self.assertTrue(tool['raw_only'])
            self.assertFalse(tool['numeric_evidence_registered'])
            self.assertEqual(tool['numeric_fields'], [])
            self.assertTrue(tool['raw_fields'])
            self.assertIsNone(tool['example'])
            self.assertNotIn('trade_date', tool['required_record_fields'])
        self.assertEqual(fingerprint(self.task), before)
        self.assertNotIn('tinyshare.income', FIELD_REGISTRY)
        self.assertNotIn('tinyshare.cashflow', FIELD_REGISTRY)
        self.assertTrue({'income', 'cashflow'}.issubset(CAPABILITIES['tinyshare']['endpoints']))

    def test_tool_inventory_covers_all_registered_families_not_supplier_marketing(self):
        contract = data_request_contract(self.task)
        planned = {tool['qualified_id_for_lookup_only'] for tool in contract['tools']}
        required = set().union(FIELD_REGISTRY, FILING_REQUEST_REGISTRY, IDENTITY_REQUEST_REGISTRY, FINANCIAL_REQUEST_REGISTRY) - {'official.index_file', 'official.etf_pcf'}
        registered = {source + '.' + item['operation'] for source, capability in CAPABILITIES.items()
                      for item in capability['registered_operations']}
        self.assertTrue(required.issubset(planned))
        self.assertTrue(planned.issubset(registered))
        self.assertEqual(len(planned), len(contract['tools']))
        self.assertNotIn('basic_data_service', [tool['operation'] for tool in contract['tools']])

    def test_mock_provider_dispatch_keeps_parameters_raw_values_and_budget(self):
        collector = EvidenceCollector(offline=False)
        provider = Mock()
        client = provider.pro_api.return_value
        client.income.return_value.to_dict.return_value = [copy.deepcopy(self.row)]
        token = 'fixture-only-token-not-a-credential'
        with patch.object(collector, '_require_tinyshare', return_value=provider):
            record = collector.external(self.task, 'tinyshare', 'income', self.params, {'token': token})
        client.income.assert_called_once_with(**self.params)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 1)
        self.assertEqual(record['parameters'], self.params)
        self.assertEqual(record['snapshot']['data'][0], self.row)
        self.assertEqual(record['status'], '成功待核验')
        self.assertFalse(record['financial_source']['unit_verified'])
        self.assertFalse(record['financial_source']['historical_vintage_verified'])
        self.assertNotIn(token, json.dumps(record, ensure_ascii=False))
        self.assertEqual(record['source_check'], '待核验')

    def test_cashflow_mock_provider_uses_its_own_registered_field(self):
        collector = EvidenceCollector(offline=False)
        provider = Mock()
        params = dict(self.params, fields=','.join(FINANCIAL_RECORD_KEYS) + ',n_cashflow_act')
        row = dict(self.row, n_cashflow_act=-456.0)
        row.pop('revenue')
        provider.pro_api.return_value.cashflow.return_value.to_dict.return_value = [row]
        with patch.object(collector, '_require_tinyshare', return_value=provider):
            record = collector.external(self.task, 'tinyshare', 'cashflow', params, {'token': 'fixture'})
        provider.pro_api.return_value.cashflow.assert_called_once_with(**params)
        self.assertEqual(record['snapshot']['data'][0]['n_cashflow_act'], -456.0)
        self.assertFalse(record['financial_source']['numeric_evidence_registered'])

    def test_offline_guard_and_invalid_request_do_not_import_or_dispatch_sdk(self):
        for offline, params in ((True, self.params), (False, dict(self.params, report_type='6'))):
            collector = EvidenceCollector(offline=offline)
            with patch.object(collector, '_require_tinyshare') as imported, self.assertRaises(ContractError):
                collector.external(self.task, 'tinyshare', 'income', params, {'token': 'fixture'})
            imported.assert_not_called()
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)

    def test_expired_mock_authorization_spends_once_without_retry_or_fallback(self):
        collector = EvidenceCollector(offline=False)
        provider = Mock()
        provider.pro_api.return_value.income.side_effect = RuntimeError('synthetic credential expired')
        with patch.object(collector, '_require_tinyshare', return_value=provider), self.assertRaises(ContractError):
            collector.external(self.task, 'tinyshare', 'income', self.params, {'token': 'fixture'})
        provider.pro_api.return_value.income.assert_called_once_with(**self.params)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 1)
        self.assertEqual(self.task['ledger']['dispatches'][-1]['failure_code'], 'credential_expired')

    def test_mismatched_success_is_retained_without_promoting_it(self):
        payload = {'code': 0, 'data': [dict(self.row, ts_code='600001.SH')]}
        record = snapshot_record(self.task, 'tinyshare', 'income', self.params, payload)
        self.assertEqual(record['snapshot'], payload)
        self.assertEqual(record['hash'], fingerprint(payload))
        self.assertEqual(record['status'], '返回结构异常')
        self.assertTrue(record['financial_source']['response_issue'])
        self.assertFalse(record['financial_source']['numeric_evidence_registered'])

    def test_empty_business_failure_and_multiple_versions_are_not_hidden(self):
        for payload, status in (({'code': 0, 'data': []}, '成功但为空'),
                ({'code': -1, 'data': None}, '业务失败待按官方错误定义核查'),
                ({'code': 0, 'data': [self.row, dict(self.row, update_flag='0', revenue=None)]}, '成功待核验')):
            with self.subTest(status=status):
                record = snapshot_record(self.task, 'tinyshare', 'income', self.params, payload)
                self.assertEqual(record['status'], status)
                self.assertEqual(record['snapshot'], payload)
                self.assertFalse(record['financial_source']['historical_vintage_verified'])

    def test_spoofed_units_and_approval_flags_cannot_create_financial_evidence(self):
        record = snapshot_record(self.task, 'tinyshare', 'income', self.params, self.payload)
        record['financial_source'].update(raw_only=False, unit_verified=True, historical_vintage_verified=True)
        spec = {'id': 'fake_financial', 'artifact_id': record['id'], 'object': '600000.SH',
            'record_locator': {'row': 0, 'field': 'revenue'}, 'published_at': '2026-08-28',
            'period': '2026-06-30', 'nature': '已实现事实', 'unit': '百万元', 'raw_unit': '元',
            'raw_value': 123.5, 'value': .0001235, 'conversion_factor': .000001,
            'question_ids': [self.task['questions'][0]['id']], 'source_check': '通过', 'applicability_check': '通过'}
        with self.assertRaises(ContractError):
            candidate_evidence(self.task, record, spec)

    def test_extraction_contract_keeps_publication_and_numeric_approval_absent(self):
        self.task['source_records'] = [snapshot_record(self.task, 'tinyshare', 'income', self.params, self.payload)]
        item = evidence_contract(self.task)['sources'][0]
        self.assertIsNone(item['publication_reference'])
        self.assertTrue(item['financial_raw_only'])
        self.assertFalse(item['numeric_evidence_registered'])
        self.assertEqual(item['publication_candidates'], [])

    def test_archive_restart_and_frozen_partial_report_preserve_raw_rows(self):
        saved = self.agent.add_snapshot(self.task['id'], 'tinyshare', 'income', self.params, self.payload)
        done = self.agent.start(saved['id'], {'answers': {}, 'theses': []})
        self.assertEqual(done['status'], '部分完成', done.get('error'))
        self.assertEqual(done['evidence'], [])
        self.assertEqual(done['calculations'], [])
        frozen = self.archive.report(done['id'], 1)
        self.assertEqual(frozen['content']['source_records'][0]['snapshot'], self.payload)
        self.assertEqual(compose(done)['source_records'][0]['financial_source']['report_period'], '20260630')
        restored = ResearchAgent(ResearchArchive(self.archive.root), offline=True)
        self.assertEqual(restored.archive.get(done['id']), done)
        self.assertEqual(restored.archive.report(done['id'], 1), frozen)
        self.assertEqual(done['ledger']['used']['data_requests'], 0)
        self.assertEqual(done['ledger']['used']['model_calls'], 0)

    def test_skill_and_app_receive_the_same_read_only_financial_tool_contract(self):
        spec = importlib.util.spec_from_file_location('financial_skill', ROOT / 'core/session.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        session = module.Session(Path(temporary.name))
        task = session.create(question=self.task['question'], entry='个股', intent='解释变化',
            objects=['600000.SH'], cutoff=self.task['cutoff'])
        before = fingerprint(session.status(task['id']))
        skill_tools = session.tools(task['id'])['data_requests']['tools']
        app_tools = data_request_contract(task)['tools']
        self.assertEqual(skill_tools, app_tools)
        self.assertEqual(fingerprint(session.status(task['id'])), before)


if __name__ == '__main__':
    unittest.main()
