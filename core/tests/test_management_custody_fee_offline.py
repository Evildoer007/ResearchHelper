"""Four separately disclosed rates reach one auditable comparison, without models."""

import copy
from decimal import Decimal
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, METHOD_REGISTRY, calculate, validate_calculation_context
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import validate_calculation_context_basis
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.reasoning import validate_plan
from researchhelper.reports import compose
from tests import test_earnings_cash_methods_offline as earnings
from tests import test_knowledge_market_interfaces_offline as catalog_fixture

ROOT = Path(__file__).resolve().parents[2]
METHOD = 'management_custody_fee_comparison_v1'
VALUES = {'management_a': .15, 'custody_a': .05, 'management_b': .5, 'custody_b': .1}


class ManagementCustodyFeeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='fee-comparison-', dir=ROOT / 'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        network = patch('socket.socket.connect', side_effect=AssertionError('No network in fee comparison'))
        network.start()
        self.addCleanup(network.stop)

    def prepare(self):
        question = '比较两份合成ETF规则的管理费与托管费年费率合计，不推断收益。'
        task = self.agent.create(question, entry='ETF', intent='比较对象', objects=['ETF-A', 'ETF-B'],
            cutoff='2026-07-20T18:00:00+08:00', questions=[{'id': 'q1', 'question': question,
                'kind': 'comparison', 'method_needs': [METHOD]}])
        task['synthetic'] = True
        task['messages'][0]['synthetic'] = True
        self.agent.archive.save(task)
        specs = []
        for field, value in VALUES.items():
            component, side = field.split('_')
            fund = 'ETF-' + side.upper()
            label = '管理费' if component == 'management' else '托管费'
            text = f'合成验收原件，非真实基金规则。{fund}，{fund}份额，2026年度规则。{label}按基金资产净值计提，年费率为{value}%/年。发布于2026-07-19。'
            task = self.agent.add_material(task['id'], field + '.txt', text.encode())
            artifact = task['materials'][-1]
            anchor = {'kind': 'material', 'artifact_id': artifact['id'], 'snapshot_hash': artifact['snapshot_hash'],
                      'part': '段落1', 'quote': text}
            context = {'fund_id': fund, 'share_class': fund + '份额', 'period': '2026年度规则',
                       'fee_basis': 'net_assets', 'rate_period': 'annual',
                       'metric': 'management_fee' if component == 'management' else 'custody_fee'}
            specs.append({'id': 'ev_' + field, 'artifact_id': artifact['id'], 'object': fund,
                'record_locator': {'part': '段落1'}, 'text': text, 'period': '2026年度规则',
                'nature': '合同政策条款', 'raw_token': str(value), 'raw_value': value, 'value': value,
                'raw_unit': '%/年', 'unit': '%/年', 'conversion_factor': 1,
                'published_at': '2026-07-19', 'publication': {'at': '2026-07-19', 'basis': '原件明确发布日期',
                    'precision': '日', 'timezone': 'Asia/Shanghai', 'certainty': '已确认', 'basis_ref': anchor},
                'calculation_context': context, 'calculation_context_basis': {key: copy.deepcopy(anchor) for key in context},
                'question_ids': ['q1'], 'review': {'question_ids': ['q1'], 'allowed_natures': ['合同政策条款'],
                    'conditions': ['只比较两份合成规则的同期间管理托管费率'], 'reason': '四项原值与合同计费口径分别核对'}})
        host = {'evidence_specs': specs, 'calculations': [{'id': 'fee_compare', 'method': METHOD,
            'inputs': {field: 'ev_' + field for field in VALUES}, 'parameters': {'unit': '%/年', 'period': '2026年度规则'},
            'question_ids': ['q1']}], 'answers': {'q1': {'answer': '合成规则中A的两项年费率合计比B低0.40个百分点，不代表持有收益差。',
                'refs': [item['id'] for item in specs], 'calculation_refs': ['fee_compare']}}}
        return task, host

    def bindings(self, host):
        return {field: next(item for item in host['evidence_specs'] if item['id'] == ref)
                for field, ref in host['calculations'][0]['inputs'].items()}

    def test_decimal_recomputation_and_zero_rates(self):
        parameters = {'unit': '%/年', 'period': '2026年度规则'}
        expected = Decimal('.15') + Decimal('.05') - Decimal('.50') - Decimal('.10')
        output = calculate(METHOD, VALUES, parameters)
        self.assertAlmostEqual(output['value'], float(expected), delta=output['tolerance'])
        self.assertEqual(output['unit'], '百分点/年')
        self.assertEqual(calculate(METHOD, dict.fromkeys(VALUES, 0), parameters)['value'], 0)

    def test_missing_extra_nonfinite_negative_and_wrong_parameters_rejected(self):
        parameters = {'unit': '%/年', 'period': '2026年度规则'}
        for field in VALUES:
            for bad in (True, '0.15', None, math.nan, math.inf, -1):
                with self.subTest(field=field, bad=bad), self.assertRaises(ContractError):
                    calculate(METHOD, dict(VALUES, **{field: bad}), parameters)
            missing = dict(VALUES);missing.pop(field)
            with self.assertRaises(ContractError):
                calculate(METHOD, missing, parameters)
        for params in ({'unit': '%/年'}, dict(parameters, annualize=True), dict(parameters, unit='%')):
            with self.assertRaises(ContractError):
                calculate(METHOD, VALUES, params)
        with self.assertRaises(ContractError):
            calculate(METHOD, dict(VALUES, sales_service=.1), parameters)

    def test_period_class_fund_object_basis_and_metric_mismatch_rejected(self):
        _, host = self.prepare()
        params = host['calculations'][0]['parameters']
        bindings = self.bindings(host)
        validate_calculation_context(METHOD, bindings, params)
        for field, value in (('fund_id', 'ETF-B'), ('share_class', '另一份额'), ('period', '2025年度规则'),
                             ('fee_basis', 'subscription_amount'), ('rate_period', 'daily'), ('metric', 'sales_service_fee')):
            changed = copy.deepcopy(bindings)
            changed['custody_a']['calculation_context'][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_calculation_context(METHOD, changed, params)
        changed = copy.deepcopy(bindings);changed['custody_a']['object'] = 'ETF-B'
        with self.assertRaises(ContractError):
            validate_calculation_context(METHOD, changed, params)

    def test_context_anchors_cannot_borrow_another_component_label(self):
        task, host = self.prepare()
        item = copy.deepcopy(host['evidence_specs'][0])
        item.update(task_id=task['id'], research_version=task['version'])
        item['calculation_context']['metric'] = 'custody_fee'
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(item, task['materials'])
        item = copy.deepcopy(host['evidence_specs'][0])
        item.update(task_id=task['id'], research_version=task['version'])
        item['calculation_context_basis']['metric'] = copy.deepcopy(host['evidence_specs'][1]['calculation_context_basis']['metric'])
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(item, task['materials'])

    def test_known_method_plans_and_knowledge_share_one_definition(self):
        task, host = self.prepare()
        validate_plan(task, {'questions': [{'id': 'q1', 'question': task['question'], 'kind': 'comparison',
            'required': True, 'method_needs': [METHOD]}], 'data_requests': [], 'calculations': host['calculations']})
        package = ResearchKnowledge().read('ETF', task['question'], (METHOD,))
        binding = ResearchKnowledge().method_binding(package, METHOD)
        self.assertEqual(binding['knowledge_id'], METHOD_REGISTRY[METHOD]['knowledge_id'])
        self.assertEqual(binding['implementation_version'], 1)

    def test_four_originals_reach_same_version_frozen_math_report(self):
        task, host = self.prepare()
        final = self.agent.start(task['id'], host)
        self.assertEqual(final['status'], '已完成', {'error': final.get('error'), 'gaps': final['gaps']})
        self.assertEqual(len(final['evidence']), 4)
        self.assertEqual(len(final['calculations']), 1)
        calculation = final['calculations'][0]
        self.assertAlmostEqual(calculation['output']['value'], -.4)
        self.assertEqual(calculation['inputs'], VALUES)
        self.assertEqual(len(calculation['input_versions']), 4)
        self.assertEqual(calculation['implementation_version'], 1)
        self.assertTrue(calculation['method_binding'] and calculation['dependency_hash'])
        self.assertEqual(final['ledger']['used']['model_calls'], 0)
        self.assertEqual(final['ledger']['used']['data_requests'], 0)
        report = self.agent.export(task['id'])
        self.assertEqual(report['content']['calculations'], final['calculations'])
        self.assertIn('<math ', report['html'])
        self.assertIn('年度管理托管费率比较', report['html'])
        self.assertIn('不是全部费用率', report['html'])
        self.assertIn('基金A管理费年费率', report['html'])

    def test_missing_component_never_replaced_by_zero_or_model_total(self):
        task, host = self.prepare()
        host['evidence_specs'] = host['evidence_specs'][:-1]
        final = self.agent.start(task['id'], host)
        self.assertEqual(final['status'], '部分完成')
        self.assertEqual(final['calculations'], [])
        self.assertEqual(len(final['evidence']), 3)

    def test_frozen_report_rejects_changed_component_input(self):
        task, host = self.prepare()
        final = self.agent.start(task['id'], host)
        changed = copy.deepcopy(final)
        changed['calculations'][0]['inputs']['custody_a'] = .10
        with self.assertRaises(ContractError):
            compose(changed)


class FeeCatalogCompatibilityTests(unittest.TestCase):
    """Extend the catalog count without weakening earlier method or permission checks."""

    def test_earnings_dependencies_with_fifteen_catalog_entries(self):
        for method in earnings.METHODS:
            package = ResearchKnowledge().read('个股', '公司研究', methods=[method])
            self.assertIn(METHOD_REGISTRY[method]['knowledge_id'], package['index'])
            self.assertIn('rh_k_cash_profit', package['index'])
            self.assertTrue(ResearchKnowledge().method_binding(package, method))
        catalog = ResearchKnowledge().catalog()
        self.assertEqual(len(catalog), 15)
        self.assertIn(METHOD_REGISTRY[METHOD]['knowledge_id'], {entry['id'] for entry in catalog})

    def test_catalog_extension_preserves_original_app_boundary_assertions(self):
        fixture = catalog_fixture.KnowledgeMarketInterfacesTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        bootstrap = fixture.fixture.request('/api/bootstrap')[0]
        catalog = {entry['id'] for entry in bootstrap['knowledge']}
        self.assertEqual(len(catalog), 15)
        self.assertTrue({'rh_k_cash_profit', 'rh_k_rate_transmission', 'rh_k_index_structure',
                         'rh_k_etf_exposure', METHOD_REGISTRY[METHOD]['knowledge_id']}.issubset(catalog))
        self.assertEqual(bootstrap['field_registry']['tinyshare.daily_basic']['permission'], '未验收')
        task = fixture.app_task()
        self.assertEqual(task['status'], '部分完成', task.get('error'))
        self.assertIn('rh_k_cash_profit', task['knowledge']['index'])
        self.assertNotIn('rh_k_rate_transmission', task['knowledge']['index'])
        self.assertEqual(task['ledger']['used']['model_calls'], 0)
        self.assertFalse(task['evidence'] or task['theses'])


if __name__ == '__main__':
    unittest.main()
