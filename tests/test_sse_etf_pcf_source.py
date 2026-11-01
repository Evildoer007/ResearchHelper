"""Retained public exchange data, no model, supplier or fresh network calls."""
import base64
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.etf_sources import (POLICY, OPERATION, parse_snapshot, validate_snapshot,
    request_shapes, request_url, calculation_context)
from researchhelper.evidence import (EvidenceCollector, api_calculation_context_contract,
    api_cell_candidates, candidate_evidence, current_snapshot_reference, data_request_contract,
    direct_code_reference, validate_source_request, _source_extract)
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import validate_evidence
from researchhelper.output_schema import planning_output_schema
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import evidence_contract, extractable_source_views
from researchhelper.reports import compose
from researchhelper import argument_support


def prepare_fixture():
    payload = (ROOT/'result/acceptance/sse-etf-nav-definition-fc8tyqoa/public-response.json').read_bytes()
    return dict(original_base64=base64.b64encode(payload).decode('ascii'))


class SSEETFPcfSourceTests(unittest.TestCase):
    maxDiff = 1200
    @classmethod
    def setUpClass(cls):
        value = os.environ.get('RESEARCHHELPER_SSE_ETF_FIXTURE')
        cls.fixture = json.loads(Path(value).read_text()) if value else prepare_fixture()
        cls.payload = base64.b64decode(cls.fixture['original_base64'], validate=True)

    def setUp(self):
        network = patch('socket.socket.connect', side_effect=AssertionError('No network calls in replay'))
        network.start(); self.addCleanup(network.stop)
        folder = tempfile.TemporaryDirectory(prefix='rh-etf-pcf-'); self.addCleanup(folder.cleanup)
        resources = Path(os.environ.get('RESEARCHHELPER_SSE_ETF_RESOURCES', str(ROOT)))
        self.agent = ResearchAgent(ResearchArchive(folder.name), resources, offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('核对510300.SH实际申赎清单的净值和所属日，不把公告日当成估值日。',
            entry='ETF', objects=['510300.SH'], research_mode='current',
            questions=[dict(id='q1', kind='fact', question='核对本次清单的净值和所属日。')])
        self.parameters = dict(ts_code='510300.SH')

    def collect(self):
        def retained_payload(task, code, started, timeout, event, **kwargs):
            event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
            return self.payload
        collector = EvidenceCollector(offline=False)
        collector.original_keeper = self.agent.archive.retain_original
        with patch('researchhelper.source_transport.official_etf', side_effect=retained_payload):
            record = collector.external(self.task, 'official', OPERATION, self.parameters, {})
        self.task['source_records'].append(record)
        return record

    def test_actual_retained_exchange_record_keeps_both_date_roles_and_raw_bytes(self):
        snapshot = parse_snapshot(self.payload, '510300.SH', self.task['cutoff'])
        self.assertEqual(snapshot['data'], [dict(ts_code='510300.SH', trade_date='2026-10-21', unit_nav='4.1856')])
        self.assertEqual(snapshot['exchange_origin']['announcement_date'], '2026-10-22')
        self.assertEqual(snapshot['exchange_origin']['raw_token'], '￥4.1856')
        self.assertEqual(base64.b64decode(snapshot['original_base64']), self.payload)

    def test_request_is_visible_in_plan_and_uses_only_task_bound_codes(self):
        self.assertEqual(validate_source_request(self.task, 'official', OPERATION, self.parameters), self.parameters)
        self.assertEqual(request_shapes(self.task)[0]['available_parameter_sets'], [self.parameters])
        self.assertIn('official.etf_pcf', json.dumps(data_request_contract(self.task)))
        self.assertIn('etf_pcf', json.dumps(planning_output_schema(self.task)))

    def test_legacy_tasks_do_not_gain_the_channel(self):
        self.task.pop(POLICY)
        self.assertEqual(request_shapes(self.task), [])
        self.assertNotIn('etf_pcf', json.dumps(planning_output_schema(self.task)))
        with self.assertRaises(ContractError):
            validate_source_request(self.task, 'official', OPERATION, self.parameters)

    def test_unknown_parameters_markets_codes_and_policies_are_rejected(self):
        for parameters in (dict(ts_code='159919.SZ'), dict(ts_code='510300.SH', date='2026-10-21'),
                dict(ts_code='510500.SH'), dict(ts_code='https://example.com'), dict(ts_code='510300')):
            with self.subTest(parameters=parameters), self.assertRaises(ContractError):
                validate_source_request(self.task, 'official', OPERATION, parameters)
        for policy in (True, '1', 3, None):
            self.task[POLICY] = policy
            with self.subTest(policy=policy), self.assertRaises(ContractError):
                validate_source_request(self.task, 'official', OPERATION, self.parameters)

    def test_empty_duplicate_wrong_code_and_invalid_values_are_not_filled(self):
        original = json.loads(self.payload)
        variants = [dict(original, result=[]), dict(original, result=original['result']*2)]
        for key, values in dict(TRADE_CODE=['510500'], NAV=[None, '', '4.1856', '￥0', '￥NaN', '￥-1'],
                PRE_TRADING_DAY=['20261022', '20261301', '20261023'], TRADING_DAY=['2026-10-22']).items():
            for value in values:
                changed = copy.deepcopy(original); changed['result'][0][key] = value; variants.append(changed)
        for value in variants:
            with self.subTest(value=value['result'][:1]), self.assertRaises(ContractError):
                parse_snapshot(json.dumps(value, ensure_ascii=False).encode(), '510300.SH', self.task['cutoff'])

    def test_latest_response_cannot_be_relabelled_as_a_historical_window(self):
        with self.assertRaises(ContractError):
            parse_snapshot(self.payload, '510300.SH', '2026-10-16T23:59:59+08:00')
        self.assertNotIn('date', request_url('510300.SH'))

    def test_collector_keeps_original_and_one_data_charge_without_model_usage(self):
        record = self.collect()
        self.assertEqual(validate_snapshot(record, self.task['cutoff']), record['snapshot'])
        self.assertEqual(self.task['ledger']['used']['data_requests'], 1)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)
        self.assertIsNotNone(current_snapshot_reference(self.task, record))
        self.assertIsNotNone(direct_code_reference(self.task, record))

    def test_actual_nav_cell_enters_extraction_without_approving_evidence(self):
        record = self.collect()
        views = extractable_source_views(self.task)
        self.assertEqual([view['id'] for view in views], [record['id']])
        contract = evidence_contract(self.task, views)['sources'][0]
        self.assertEqual(contract['calculation_context_contract']['registered_numeric_fields'], ['unit_nav'])
        self.assertEqual(len(api_cell_candidates(record)['cells']), 1)
        self.assertEqual(self.task['evidence'], [])

    def test_context_is_previous_day_official_nav_not_iopv_or_publication_proof(self):
        record = self.collect()
        context = api_calculation_context_contract(record)
        self.assertEqual(context['status'], 'registered_constraints')
        self.assertEqual(context['fixed_fields'], dict(currency='CNY', valuation_date='2026-10-21',
            nav_kind='official', share_class='listed_etf_unit:510300.SH'))
        self.assertFalse(context['context_is_approval'])
        self.assertNotIn('published_at', context['basis_templates'])
        self.assertNotIn('valuation_time', context['fixed_fields'])

    def test_rehashed_normalized_value_or_dates_cannot_replace_original_fields(self):
        record = self.collect()
        for section, key, value in (('data', 'unit_nav', '99'), ('data', 'trade_date', '2026-10-22'),
                ('exchange_origin', 'valuation_date', '2026-10-22'), ('exchange_origin', 'raw_token', '￥99')):
            changed = copy.deepcopy(record)
            target = changed['snapshot'][section]
            if isinstance(target, list): target = target[0]
            target[key] = value
            changed['hash'] = fingerprint(changed['snapshot'])
            with self.subTest(key=key), self.assertRaises(ContractError):
                validate_snapshot(changed, self.task['cutoff'])
            self.assertEqual(api_calculation_context_contract(changed)['status'], 'unavailable')

    def test_policy_is_bound_and_frozen_without_changing_task_limits(self):
        before = input_fingerprint(self.task)
        old = copy.deepcopy(self.task); old.pop(POLICY)
        self.assertNotEqual(before, input_fingerprint(old))
        self.agent._adopt_method_knowledge(self.task)
        self.task['status'] = '部分完成'
        self.agent._gap(self.task, '取数渠道离线回放，不是实际模型报告', category='question_coverage')
        self.assertEqual(compose(self.task)[POLICY], 2)
        self.assertEqual(self.task['ledger']['limits'], dict(data_requests=100, model_calls=8, seconds=600))

    def test_offline_external_calls_remain_blocked(self):
        with self.assertRaises(ContractError):
            EvidenceCollector(offline=True).external(self.task, 'official', OPERATION, self.parameters, {})

    def proposal(self, record):
        source = evidence_contract(self.task, extractable_source_views(self.task))['sources'][0]
        context = dict(source['calculation_context_contract']['fixed_fields'],
            fund_id='510300.SH', observation_date='2026-10-21')
        return dict(id='nav_actual', artifact_id=record['id'], object='510300.SH',
            record_locator=dict(row=0, field='unit_nav'), nature='已实现事实',
            published_at=None, publication=dict(at=None, basis_ref=None, certainty='未知',
                precision='未知', timezone='未知', basis='只核对本次实际取得的版本，不证明历史可得。'),
            availability=current_snapshot_reference(self.task, record),
            direct_code_reference=direct_code_reference(self.task, record),
            api_value_reference=source['value_reference'],
            calculation_context=context, api_context_reference=source['compact_context_reference'],
            question_ids=['q1'])

    def test_numeric_source_and_applicability_checks_accept_only_current_observation(self):
        record = self.collect()
        spec = self.proposal(record)
        item = candidate_evidence(self.task, record, spec)
        self.assertEqual(item['source_check'], '通过')
        self.assertEqual(item['applicability_check'], '待核验')
        review = dict(question_ids=['q1'], allowed_natures=['已实现事实'],
            conditions=['仅说明本次取得清单所载2026年10月21日的单位净值。'],
            reason='原始交易代码、净值、前一交易日与登记单位一致，不用于历史可得或事件因果。')
        ResearchAnalyzer().assess(self.task, item, review, self.task['source_records'])
        validate_evidence(item, self.task)
        self.assertEqual(str(item['value']), '4.1856')
        self.assertIsNone(item['published_at'])
        self.assertEqual(item['calculation_context']['valuation_date'], '2026-10-21')
        self.assertEqual(self.task['evidence'], [])

    def test_wrong_valuation_day_share_currency_and_nav_kind_cannot_pass(self):
        record = self.collect()
        for field, value in dict(valuation_date='2026-10-22', share_class='A', currency='USD', nav_kind='IOPV').items():
            spec = self.proposal(record); spec['calculation_context'][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                candidate_evidence(self.task, record, spec)

    def test_current_nav_can_be_saved_and_exported_without_fabricating_a_series(self):
        record = self.collect()
        spec = self.proposal(record)
        spec['review'] = dict(question_ids=['q1'], allowed_natures=['已实现事实'],
            conditions=['仅核对本次清单载明的净值，不视作实时数据。'],
            reason='实际响应的交易代码、净值原值、单位及所属日一致。')
        self.agent.archive.save(self.task)
        pending = self.agent.start(self.task['id'], dict(evidence_specs=[spec], answers=dict(q1=dict(
            answer='净值原值见证据表，所属日来自清单的前一交易日字段；不是公告日或实时估值，不证明历史当时可得。',
            refs=['nav_actual']))), defer_delivery=True)
        self.assertTrue(pending.get('host_checkpoint'))
        self.assertFalse(pending.get('delivery'))
        request = self.agent.argument_review_inputs(self.task['id'])['request']
        self.agent.submit_argument_review(self.task['id'], dict(version=argument_support.VERSION,
            review_hash=request['review_hash'], questions=[dict(question_id='q1', status='answered',
                missing_evidence=[], reason='离线宿主复核控制，不是模型研究质量验收。')],
            reviews=[dict(target_id=target['id'], segments=[dict(text=target['text'], verdict='supported',
                basis_refs=['evidence:nav_actual'], explanation='仅核对原值与日期角色，不推断投资机会。')])
                for target in request['targets']]))
        done = self.agent.start(self.task['id'])
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertEqual(len(done['evidence']), 1)
        self.assertEqual(done['evidence'][0]['period'], '2026-10-21')
        report = self.agent.export(done['id'])
        self.assertEqual(report['content'][POLICY], 2)
        self.assertEqual(report['digest'], self.agent.export(done['id'])['digest'])
        self.assertEqual(report['content']['evidence'][0]['source'], 'official')
        self.assertEqual(report['content']['calculations'], [])
        self.assertEqual(done['ledger']['used']['model_calls'], 0)

    def test_api_bytes_survive_the_actual_archive_callback_and_restart(self):
        record = self.collect()
        self.agent.archive.save(self.task)
        restored = self.agent.archive.get(self.task['id'])
        actual = next(item for item in restored['source_records'] if item['id'] == record['id'])
        self.assertEqual(validate_snapshot(actual, restored['cutoff']), record['snapshot'])
        self.assertEqual(base64.b64decode(actual['snapshot']['original_base64']), self.payload)
        self.assertEqual(self.agent.archive.retained_originals(restored), [])

    def test_api_json_does_not_expand_the_user_material_upload_contract(self):
        with self.assertRaises(ContractError):
            self.agent.archive.retain_original(self.task, 'material', 'unregistered.json', self.payload)
        with self.assertRaises(ContractError):
            self.agent.archive.retain_original(self.task, 'material', 'unregistered.exe', self.payload)
