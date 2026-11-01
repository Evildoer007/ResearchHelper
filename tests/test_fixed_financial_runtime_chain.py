"""Retained real amounts and installed definitions through ordinary runtime.

The HTTP boundary only replays retained JSON. No real network, model call, PDF
download or pre-approved evidence is used. This is deterministic integration
acceptance, not a new supplier acquisition or an autonomous research report.
"""

import copy
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import (ContractError, FINANCIAL_INPUT_METRICS,
    fingerprint, validate_evidence, validate_calculation_context)
from researchhelper.evidence import (EvidenceCollector, api_cell_choices,
    expand_api_cell_selection, candidate_evidence, current_snapshot_reference)
from researchhelper.ifind_financial_evidence import basic_financial_contract
from researchhelper import ifind_financial_catalog


ROOT = Path(__file__).resolve().parents[1]
METHODS = tuple(FINANCIAL_INPUT_METRICS)


def retained_fixture():
    path = os.environ.get('RESEARCHHELPER_IFIND_METADATA_FIXTURE')
    if path:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    folder = ROOT / 'result/acceptance/fixed-financial-definitions-20261023-v2'
    receipt = json.loads((folder / 'receipt.json').read_text(encoding='utf-8'))
    request = next(row for row in receipt['requests'] if row['operation'] == 'basic_data_service')
    policy = ROOT / 'result/acceptance/ifind-financial-metadata-20261023/2-get_limit_policy.json'
    return dict(policy=json.loads(policy.read_text(encoding='utf-8')), request=request['parameters'],
                basic=json.loads((folder / request['file']).read_text(encoding='utf-8')))


class FixedFinancialRuntimeChainTests(unittest.TestCase):
    def setUp(self):
        guard = patch('socket.socket.connect', side_effect=AssertionError('No real network allowed'))
        guard.start()
        self.addCleanup(guard.stop)
        self.fixture = retained_fixture()
        directory = tempfile.TemporaryDirectory(prefix='rh-fixed-financial-chain-')
        self.addCleanup(directory.cleanup)
        resources = Path(os.environ.get('RESEARCHHELPER_IFIND_METADATA_RESOURCES', str(ROOT)))
        self.agent = ResearchAgent(ResearchArchive(directory.name), resources, offline=True,
                                   require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('核对600519.SH的2026年半年报合并财务原值并计算三项财务比率。',
            entry='混合', objects=['600519.SH'], research_mode='current', questions=[
                dict(id='q1', kind='fact', question='同口径财务金额及三个登记比率是多少？',
                     evidence_needs=['2026年半年报合并累计财务原值'], method_needs=list(METHODS))])
        self.assertEqual(self.task.get('fixed_financial_catalog_version'), 1)
        self.collector = EvidenceCollector(offline=False)
        self.calls = []
        transport = patch.object(self.collector, '_http_json', side_effect=self.replay)
        transport.start()
        self.addCleanup(transport.stop)
        self.collect('get_limit_policy', {})
        self.source = self.collect('basic_data_service', self.fixture['request'])

    def replay(self, task, request, timeout, event):
        operation = urlsplit(request.full_url).path.rsplit('/', 1)[-1]
        self.calls.append(operation)
        event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True,
                     synthetic_test_transport=True)
        if operation == 'get_limit_policy':
            return copy.deepcopy(self.fixture['policy'])
        if operation == 'basic_data_service':
            self.assertEqual(json.loads(request.data), self.fixture['request'])
            return copy.deepcopy(self.fixture['basic'])
        raise AssertionError('Unexpected request: ' + operation)

    def collect(self, operation, parameters):
        source = self.collector.external(self.task, 'ifind', operation, parameters,
            dict(access_token='synthetic-offline-transport-token'))
        self.task['source_records'].append(source)
        return source

    def verified_inputs(self):
        self.assertEqual(self.calls, ['get_limit_policy', 'basic_data_service'])
        self.assertTrue(all(event.get('synthetic_test_transport') is True
                            for event in self.task['ledger']['dispatches']))
        self.assertIsNotNone(current_snapshot_reference(self.task, self.source))
        self.assertEqual(self.source['snapshot'], self.fixture['basic'])
        choices = api_cell_choices(self.task, self.source)
        self.assertEqual(len(choices), 4)
        items = {}
        for choice in choices:
            review = dict(reason='采用当前取得且限定本主体、本报告期的原始合并累计金额；不追认历史可得。',
                question_ids=['q1'], allowed_natures=['已实现事实'],
                conditions=['单位元、人民币、企业会计准则、本年累计合并范围由受控依据限定。'],
                evidence_need_texts={'q1': ['2026年半年报合并累计财务原值']})
            spec = expand_api_cell_selection(self.task, dict(api_cell_selection=choice['candidate_id'],
                question_ids=['q1'], review=review))
            item = candidate_evidence(self.task, self.source, spec)
            self.assertEqual(item['applicability_check'], '待核验')
            ResearchAnalyzer().assess(self.task, item, review, self.task['source_records'])
            validate_evidence(item, self.task)
            self.task['evidence'].append(item)
            context = item['calculation_context']
            self.assertEqual(context['entity_id'], '600519.SH')
            self.assertEqual(context['period'], '2026年1-6月')
            self.assertEqual(context['accounting_scope'], 'consolidated')
            self.assertEqual(context['accounting_standard'], 'CAS')
            self.assertEqual(context['currency'], 'CNY')
            self.assertEqual(item['unit'], '元')
            self.assertEqual(item['conversion_factor'], 1)
            field = item['record_locator']['field']
            self.assertEqual(item['value'], self.fixture['basic']['tables'][0]['table'][field][0])
            items[context['metric']] = item
        self.assertEqual(set(items), {'revenue', 'operating_cost', 'net_profit', 'operating_cash_flow'})
        return items

    def test_real_entry_source_applicability_and_three_registered_calculations(self):
        items = self.verified_inputs()
        self.agent._adopt_method_knowledge(self.task)
        parameters = dict(amount_unit='元', currency='CNY', period='2026年1-6月',
                          accounting_scope='consolidated', accounting_standard='CAS')
        for method, roles in FINANCIAL_INPUT_METRICS.items():
            bindings = {role: items[metric] for role, metric in roles.items()}
            validate_calculation_context(method, bindings, parameters)
            self.agent._calculate_spec(self.task, dict(id='calc_' + method, method=method,
                inputs={role: item['id'] for role, item in bindings.items()},
                parameters=copy.deepcopy(parameters), question_ids=['q1']))
        self.assertEqual(len(self.task['calculations']), 3, self.task['gaps'])
        numbers = {metric: Decimal(str(item['value'])) for metric, item in items.items()}
        expected = dict(gross_margin_v1=(numbers['revenue']-numbers['operating_cost'])/numbers['revenue']*100,
            net_profit_margin_v1=numbers['net_profit']/numbers['revenue']*100,
            operating_cash_profit_ratio_v1=numbers['operating_cash_flow']/numbers['net_profit']*100)
        for result in self.task['calculations']:
            self.assertAlmostEqual(result['output']['value'], float(expected[result['method']]), places=10)
            self.assertEqual(set(result['input_bindings']), set(FINANCIAL_INPUT_METRICS[result['method']]))
            self.assertEqual(set(result['input_versions']), set(result['input_refs']))

    def test_calibration_cannot_expand_to_another_period_or_company(self):
        definitions = ifind_financial_catalog.evidence_definitions()
        self.assertEqual(basic_financial_contract(self.source, definitions)['status'], 'registered_constraints')
        for change in ('period', 'code'):
            record = copy.deepcopy(self.source)
            if change == 'period':
                for item in record['parameters']['indipara']:
                    item['indiparams'][0] = '20260930'
                for item in record['snapshot']['inputParams']['params']:
                    next(row for row in item['params'] if row['name'] == 'FDREPORT')['value'] = '20260930'
            else:
                record['parameters']['codes'] = '000001.SZ'
                record['snapshot']['tables'][0]['thscode'] = '000001.SZ'
            record['hash'] = fingerprint(record['snapshot'])
            record['request_hash'] = fingerprint({key: record[key] for key in ('source', 'operation', 'parameters')})
            result = basic_financial_contract(record, definitions)
            self.assertEqual(result['status'], 'raw_only', change)
            self.assertEqual(result['cells'], [])

    def test_context_tampering_and_historical_reuse_fail(self):
        items = self.verified_inputs()
        changed = copy.deepcopy(items['net_profit'])
        changed['calculation_context']['metric'] = 'parent_net_profit'
        with self.assertRaises(ContractError):
            validate_evidence(changed, self.task)
        old = copy.deepcopy(self.task)
        old['research_mode'] = 'historical'
        self.assertEqual(api_cell_choices(old, self.source), [])
