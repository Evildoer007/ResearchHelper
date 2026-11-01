"""Reference-led figures; frozen evidence and original tests remain untouched."""
import copy
import json
import unittest
from researchhelper.reports import render, research_figures
from researchhelper.report_examples import example_figures, render_example
from tests import test_report_visual_selection_offline as fixtures


class ReferenceReportFigureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReportVisualSelectionTests()
        self.addCleanup(self.fixture.doCleanups)

    def test_two_prices_are_unconnected_observations_with_original_values(self):
        content = self.fixture.real()
        before = copy.deepcopy(content)
        figure = next(iter(research_figures(content).values()))
        self.assertEqual(figure['presentation'], 'chart')
        self.assertEqual(figure['kind'], 'points')
        self.assertEqual([row['value'] for row in figure['rows']], [4.416, 4.432])
        paper = render(content, fixtures.ROOT)
        self.assertIn("type:'scatter'", paper)
        self.assertNotIn("type:'line'", paper)
        self.assertIn('4.416元', paper)
        self.assertIn('4.432元', paper)
        self.assertEqual(before, content)

    def test_financial_comparison_retains_unit_scope_and_negative_value(self):
        content = self.fixture.method('gross_margin_v1')
        figure = next(iter(research_figures(content).values()))
        self.assertEqual(figure['presentation'], 'chart')
        self.assertEqual([row['value'] for row in figure['rows']], [100, 60])
        self.assertEqual([row['unit'] for row in figure['rows']], ['百万元', '百万元'])
        self.assertIn('合并口径，CAS', render(content, fixtures.ROOT))
        fixture = self.fixture.fixture(fixtures.methods, 'MethodDependencyWorkflowTests')
        task, host, _ = fixtures.earnings.prepare_financial(fixture.agent, 'operating_cash_profit_ratio_v1', cash=-8)
        fixtures.label_synthetic(fixture.agent, task)
        content = fixtures.compose(fixture.agent.start(task['id'], host))
        self.assertIn(-8, [row['value'] for row in next(iter(research_figures(content).values()))['rows']])
        self.assertIn('-8百万元', render(content, fixtures.ROOT))

    def test_nav_and_single_valuation_scenarios_stay_tables(self):
        content = self.fixture.method('etf_nav_premium_v1')
        self.assertTrue(all(item['presentation'] == 'table' for item in research_figures(content).values()))
        fixture = self.fixture.fixture(fixtures.documents, 'DocumentContentTests')
        task = fixture.agent.create_case('value')
        content = fixtures.compose(fixture.agent.start(task['id']))
        self.assertTrue(all(item['presentation'] == 'table' for item in research_figures(content).values()))

    def test_valuation_range_uses_only_disclosed_bounds_and_belongs_to_argument(self):
        catalog = json.loads((fixtures.ROOT/'products/app/data/report_catalog.json').read_text())['sources']
        figures = example_figures('semi', catalog['semi'])
        self.assertEqual(len(figures), 5)
        self.assertEqual(figures[-1]['presentation'], 'intervals')
        self.assertEqual([series['data'] for series in figures[-1]['series']], [[20.5, 14.3], [55.8, 19.9]])
        paper = render_example('semi', fixtures.ROOT)
        self.assertLess(paper.index('data-argument="2"'), paper.index('id="example-chart-4"'))
        self.assertIn('不是置信区间', paper)


if __name__ == '__main__':
    unittest.main()
