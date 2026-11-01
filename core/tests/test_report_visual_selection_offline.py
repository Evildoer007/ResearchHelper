"""Independent v2 checks: data semantics decide table or figure, not decoration."""
import copy
import json
from pathlib import Path
import unittest

from researchhelper.contracts import ContractError
from researchhelper.reports import compose, render, research_figures
from tests import test_document_content_offline as documents
from tests import test_method_dependency_workflow_offline as methods
from tests import test_management_custody_fee_offline as fees
from tests import test_index_change_workflow_offline as indices
from tests import test_earnings_cash_methods_offline as earnings

ROOT = Path(__file__).resolve().parents[2]


def label_synthetic(agent, task):
    """Label new isolated fixtures without switching on engine fixture bypasses."""
    latest = agent.archive.get(task['id'])
    latest['synthetic'] = True
    latest['messages'][0]['synthetic'] = True
    agent.archive.save(latest)
    return latest


class ReportVisualSelectionTests(unittest.TestCase):
    def fixture(self, module, name):
        fixture = getattr(module, name)()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def real(self):
        dispatch = json.loads((ROOT/'result/acceptance/numeric-contract-live-research/authorization-dispatch.json').read_text())
        return json.loads((Path(dispatch['project'])/'result/report.json').read_text())

    def method(self, method):
        fixture = self.fixture(methods, 'MethodDependencyWorkflowTests')
        task, host, _ = fixture.prepare(method)
        label_synthetic(fixture.agent, task)
        result = fixture.agent.start(task['id'], host)
        self.assertEqual(result['status'], '已完成', result.get('error'))
        return compose(result)

    def test_real_two_prices_are_visible_table_without_chart_or_hidden_values(self):
        content = self.real()
        self.assertEqual(next(iter(research_figures(content).values()))['presentation'], 'table')
        paper = render(content, ROOT)
        self.assertIn("data-presentation='table'", paper)
        self.assertIn('表1 未复权收盘价起终点', paper)
        self.assertIn('4.416元', paper)
        self.assertIn('4.432元', paper)
        self.assertNotIn("class='chart", paper)
        self.assertNotIn('查看图表数据', paper)

    def test_table_result_keeps_original_frozen_content(self):
        content = self.real()
        before = copy.deepcopy(content)
        paper = render(content, ROOT)
        self.assertEqual(content, before)
        self.assertIn('0.3623%', paper)
        for record in content['calculations']:
            self.assertIn("id='" + record['id'] + "'", paper)
        for record in content['evidence']:
            self.assertIn("id='ev-" + record['id'] + "'", paper)

    def test_same_time_price_and_nav_are_named_table_rows(self):
        content = self.method('etf_nav_premium_v1')
        figure = next(iter(research_figures(content).values()))
        self.assertEqual(figure['presentation'], 'table')
        self.assertEqual([row['label'] for row in figure['rows']], ['收盘价', '官方单位净值'])
        paper = render(content, ROOT)
        self.assertNotIn("class='chart", paper)
        self.assertIn('2026-09-30T15:00:00+08:00', paper)

    def test_single_period_financial_inputs_are_not_a_large_chart(self):
        content = self.method('gross_margin_v1')
        paper = render(content, ROOT)
        self.assertNotIn("class='chart", paper)
        self.assertIn('100百万元', paper)
        self.assertIn('60百万元', paper)
        self.assertIn('合并口径，CAS', paper)

    def test_negative_cash_stays_negative_in_visible_table(self):
        fixture = self.fixture(methods, 'MethodDependencyWorkflowTests')
        task, host, _ = earnings.prepare_financial(fixture.agent, 'operating_cash_profit_ratio_v1', cash=-8)
        label_synthetic(fixture.agent, task)
        paper = render(compose(fixture.agent.start(task['id'], host)), ROOT)
        self.assertIn('-8百万元', paper)
        self.assertNotIn("class='chart", paper)

    def test_single_scenarios_remain_separate_tables_without_assigned_probability(self):
        fixture = self.fixture(documents, 'DocumentContentTests')
        task = fixture.agent.create_case('value')
        content = compose(fixture.agent.start(task['id']))
        descriptors = research_figures(content)
        self.assertTrue(descriptors)
        self.assertTrue(all(item['presentation'] == 'table' for item in descriptors.values()))
        paper = render(content, ROOT)
        self.assertNotIn("class='chart", paper)
        for index in range(len(descriptors)):
            self.assertIn(f'表{index + 1} 剩余现金流情景现值', paper)

    def test_fee_chart_groups_original_components_by_fund_not_by_row(self):
        fixture = self.fixture(fees, 'ManagementCustodyFeeTests')
        task, host = fixture.prepare()
        content = compose(fixture.agent.start(task['id'], host))
        figure = next(iter(research_figures(content).values()))
        self.assertEqual(figure['presentation'], 'chart')
        self.assertEqual(figure['groups'], [{'name': 'ETF-A', 'indices': [0, 1]}, {'name': 'ETF-B', 'indices': [2, 3]}])
        self.assertEqual(figure['categories'], ['管理费', '托管费'])
        self.assertEqual([[figure['rows'][i]['value'] for i in group['indices']] for group in figure['groups']],
                         [[.15, .05], [.5, .1]])
        paper = render(content, ROOT)
        self.assertIn('图1 管理费与托管费分项对比', paper)
        self.assertIn("class='chart chart-grouped'", paper)
        self.assertNotIn("figure.kind==='points'", paper)
        self.assertIn('查看图表数据', paper)

    def test_index_table_preserves_variant_currency_and_base(self):
        fixture = self.fixture(indices, 'IndexChangeWorkflowTests')
        task, host = fixture.prepare('net_total_return', currency='美元')
        label_synthetic(fixture.agent, task)
        content = compose(fixture.agent.start(task['id'], host))
        paper = render(content, ROOT)
        self.assertNotIn("class='chart", paper)
        self.assertIn('净全收益指数，美元，基期2004-12-31，基点100', paper)

    def test_synthetic_label_does_not_bypass_context_validation(self):
        fixture = self.fixture(methods, 'MethodDependencyWorkflowTests')
        task, host, _ = fixture.prepare('gross_margin_v1')
        latest = label_synthetic(fixture.agent, task)
        self.assertFalse(latest.get('fixture'))
        host['evidence_specs'][0]['calculation_context']['currency'] = 'USD'
        result = fixture.agent.start(task['id'], host)
        self.assertFalse(result['calculations'])
        self.assertNotEqual(result['status'], '已完成')

    def test_tampered_values_are_rejected_before_presentation_selection(self):
        content = self.real()
        content['calculations'][0]['inputs']['end_price'] = 999
        with self.assertRaises(ContractError):
            render(content, ROOT)


if __name__ == '__main__':
    unittest.main()
