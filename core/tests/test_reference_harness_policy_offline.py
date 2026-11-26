"""Current graph contracts, with each superseded assertion independently checked."""
import copy
import json
import unittest
from lxml import html

from researchhelper.reports import compose, render, research_figures
from researchhelper.report_examples import example_figures, render_example
from tests import test_report_visual_selection_offline as fixtures
from tests.test_report_measurement_accessibility_offline import MeasurementTables, chart_data
from tests.test_report_argument_figures_offline import ArgumentFigures
from tests.test_report_editorial_figures_offline import Tables

ROOT = fixtures.ROOT


class ReferenceHarnessPolicyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReportVisualSelectionTests()
        self.addCleanup(self.fixture.doCleanups)

    def test_price_plot_occurs_once_beside_answer_and_preserves_all_bindings(self):
        content = self.fixture.saved_price_report()['content']
        before = copy.deepcopy(content)
        paper = render(content, ROOT)
        descriptor = next(iter(research_figures(content).values()))
        self.assertEqual(descriptor['kind'], 'points')
        self.assertEqual(descriptor['presentation'], 'chart')
        self.assertEqual([row['value'] for row in descriptor['rows']], [4.416, 4.432])
        ref = content['calculations'][0]['id']
        tree = html.document_fromstring(paper)
        figure, = tree.xpath('//main//figure[@data-calculation-ref=$ref]', ref=ref)
        answer = tree.get_element_by_id('answer-' + content['questions'][0]['id'])
        shared, = figure.xpath('ancestor::div[contains(concat(" ",normalize-space(@class)," ")," logic-reading-grid ")]')
        self.assertIn(answer, list(shared.iterdescendants()))
        order = list(shared.iterdescendants())
        self.assertLess(order.index(answer), order.index(figure))
        self.assertTrue(tree.get_element_by_id(ref).xpath('ancestor::*[@id="report-records"]'))
        plot, = figure.xpath('.//*[@aria-details]')
        detail = tree.get_element_by_id(plot.get('aria-details'))
        for row in descriptor['rows']:
            self.assertIn(str(row['value'])+row['unit'], detail.text_content())
            target = 'ev-' + row['ref']
            self.assertTrue(detail.xpath('.//a[@href=$href]', href='#' + target))
            self.assertEqual(len(tree.xpath('//*[@id=$target]', target=target)), 1)
        self.assertEqual(before, content)

    def test_financial_comparison_has_exact_values_and_accounting_basis(self):
        content = self.fixture.method('gross_margin_v1')
        before = copy.deepcopy(content)
        figure = next(iter(research_figures(content).values()))
        self.assertEqual(figure['presentation'], 'chart')
        self.assertEqual(figure['kind'], 'bar')
        self.assertEqual([row['value'] for row in figure['rows']], [100, 60])
        self.assertEqual([row['unit'] for row in figure['rows']], ['百万元']*2)
        self.assertEqual(figure['basis'], ['合并口径', 'CAS'])
        self.assertIn('40%', render(content, ROOT))
        self.assertEqual(before, content)

    def test_negative_cash_plot_and_its_data_table_remain_negative(self):
        fixture = self.fixture.fixture(fixtures.methods, 'MethodDependencyWorkflowTests')
        task, host, _ = fixtures.earnings.prepare_financial(fixture.agent, 'operating_cash_profit_ratio_v1', cash=-8)
        fixtures.label_synthetic(fixture.agent, task)
        content = compose(fixture.agent.start(task['id'], host))
        before = copy.deepcopy(content)
        figure = next(iter(research_figures(content).values()))
        self.assertEqual(figure['presentation'], 'chart')
        self.assertEqual(figure['rows'][0]['value'], -8)
        paper = render(content, ROOT)
        self.assertIn('-8百万元', paper)
        self.assertEqual(before, content)

    def test_fee_components_have_actual_object_groups_and_no_temporal_series(self):
        fixture = self.fixture.fixture(fixtures.fees, 'ManagementCustodyFeeTests')
        task, host = fixture.prepare()
        content = compose(fixture.agent.start(task['id'], host))
        before = copy.deepcopy(content)
        figure = next(iter(research_figures(content).values()))
        self.assertEqual(figure['groups'], [{'name': 'ETF-A', 'indices': [0, 1]}, {'name': 'ETF-B', 'indices': [2, 3]}])
        self.assertEqual(figure['categories'], ['管理费', '托管费'])
        self.assertEqual([[figure['rows'][index]['value'] for index in group['indices']] for group in figure['groups']],
                         [[.15, .05], [.5, .1]])
        paper = render(content, ROOT)
        selected=chart_data(paper)
        self.assertEqual(len(selected),1)
        self.assertEqual(selected[0]['auto_visual']['scheme'],'grouped_bar')
        self.assertEqual(selected[0]['auto_visual']['data']['labels'],figure['categories'])
        self.assertEqual(selected[0]['auto_visual']['data']['series'],[
            dict(name=group['name'],values=[figure['rows'][index]['value'] for index in group['indices']])
            for group in figure['groups']])
        self.assertEqual(before, content)

    def test_index_observations_retain_variant_currency_base_and_dates(self):
        fixture = self.fixture.fixture(fixtures.indices, 'IndexChangeWorkflowTests')
        task, host = fixture.prepare('net_total_return', currency='美元')
        fixtures.label_synthetic(fixture.agent, task)
        content = compose(fixture.agent.start(task['id'], host))
        before = copy.deepcopy(content)
        figure = next(iter(research_figures(content).values()))
        self.assertEqual((figure['kind'], figure['presentation']), ('points', 'chart'))
        self.assertEqual(figure['basis'], ['净全收益指数', '美元', '基期2004-12-31，基点100'])
        calculation = content['calculations'][0]
        self.assertEqual([row['label'] for row in figure['rows']],
                         [calculation['parameters'][key] for key in ('start_date', 'end_date')])
        selected=chart_data(render(content,ROOT))
        self.assertEqual(len(selected),1)
        self.assertEqual(selected[0]['kind'],'points')
        self.assertNotIn(selected[0]['auto_visual']['scheme'],('line','area','multi_line'))
        self.assertEqual(selected[0]['auto_visual']['data']['labels'],[row['label'] for row in figure['rows']])
        self.assertEqual(selected[0]['auto_visual']['data']['values'],[row['value'] for row in figure['rows']])
        self.assertEqual(before, content)

    def test_every_measurement_keeps_accessible_table_units_headers_and_evidence(self):
        for method in ('observed_price_return_v1', 'etf_nav_premium_v1', 'gross_margin_v1'):
            with self.subTest(method=method):
                content = self.fixture.method(method)
                before = copy.deepcopy(content)
                paper = render(content, ROOT)
                parsed = MeasurementTables(paper)
                figures = list(research_figures(content).values())
                self.assertEqual(len(parsed.tables), len(figures))
                self.assertTrue(all(header['scope'] == 'col' for header in parsed.headers))
                for table, figure in zip(parsed.tables, figures):
                    self.assertIn(figure['title'], table['aria-label'])
                    self.assertIn(figure['unit'], table['aria-label'])
                    self.assertIn(figure['result_label'], table['aria-label'])
                    for row in figure['rows']:
                        self.assertIn(str(row['value'])+row['unit'], paper)
                self.assertEqual(before, content)

    def test_five_ai_figures_have_matching_tables_and_original_lower_bound(self):
        catalog = json.loads((ROOT/'products/app/data/report_catalog.json').read_text())['sources']
        paper = render_example('semi', ROOT)
        self.assertEqual(Tables(paper).tables[:5], [figure['title'] for figure in example_figures('semi', catalog['semi'])])
        self.assertIn('>87+</td>', paper)
        self.assertIn('全年实际/最新预期（%）', paper)
        self.assertIn('scope="row"', paper)

    def test_all_six_figure_argument_bindings_are_complete_and_unique(self):
        expected = {'semi': {0: [0, 1], 1: [2, 3], 2: [4]},
                    'consumer': {0: [0], 1: [1]}, 'internet': {0: [0], 1: [1]},
                    'medicine': {0: [0], 1: []}, 'broker': {0: [], 1: [0], 2: []},
                    'ipo': {0: [0], 1: [], 2: []}}
        for key, binding in expected.items():
            with self.subTest(key=key):
                self.assertEqual(ArgumentFigures(render_example(key, ROOT)).bindings, binding)
                values = [number for group in binding.values() for number in group]
                self.assertEqual(len(values), len(set(values)))

    def test_actual_historical_series_are_not_pixel_reconstructed_or_interpolated(self):
        catalog = json.loads((ROOT/'products/app/data/report_catalog.json').read_text())['sources']
        income = example_figures('semi', catalog['semi'])[1]
        self.assertEqual(income['presentation'], 'endpoints')
        self.assertEqual(income['series'][0]['data'], [30, 9])
        self.assertIsNone(example_figures('medicine', catalog['medicine'])[0]['series'][0]['data'][-1])
        self.assertEqual(example_figures('consumer', catalog['consumer'])[0]['series'][0]['data'], [-28.1, 10.9, 13.7])
        self.assertEqual(example_figures('ipo', catalog['ipo'])[0]['kind'], 'timeline')
        script = (ROOT/'products/app/frontend/report-examples.js').read_text()
        self.assertNotRegex(script, r"series\s*:\s*\[\s*\{\s*type\s*:\s*['\"]line['\"]")
        self.assertIn("value===null?'-':value", script)


if __name__ == '__main__':
    unittest.main()
