"""Independent v2 checks: data semantics decide table or figure, not decoration."""
import copy
from pathlib import Path
import unittest
from lxml import html as dom

from researchhelper.contracts import ContractError, fingerprint
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

    def saved_price_report(self):
        fixture = self.fixture(methods, 'MethodDependencyWorkflowTests')
        task, host, _ = fixture.prepare('observed_price_return_v1', price_endpoints=(4.416, 4.432))
        label_synthetic(fixture.agent, task)
        result = fixture.agent.start(task['id'], host)
        self.assertEqual(result['status'], '已完成', result.get('error'))
        return fixture.agent.export(task['id'])

    def method(self, method):
        fixture = self.fixture(methods, 'MethodDependencyWorkflowTests')
        task, host, _ = fixture.prepare(method)
        label_synthetic(fixture.agent, task)
        result = fixture.agent.start(task['id'], host)
        self.assertEqual(result['status'], '已完成', result.get('error'))
        return compose(result)

    def inline_inputs(self, content, raw_values):
        from tests.test_report_measurement_accessibility_offline import chart_data

        paper = render(content, ROOT)
        self.assertEqual(chart_data(paper), [])
        tree = dom.document_fromstring(paper)
        figure, = tree.xpath('//main//figure[@data-calculation-ref]')
        self.assertEqual(figure.get('data-presentation'), 'inline')
        self.assertEqual(len(figure.xpath('.//p[@class="measured-reading"]//*[@data-raw-value]')), len(raw_values))
        self.assertFalse(figure.xpath('.//table'))
        target, = figure.xpath('./figcaption/a/@href')
        table = tree.get_element_by_id(target[1:]).find('.//table')
        self.assertEqual(table.xpath('./tbody/tr/td[@data-raw-value]/text()'), raw_values)
        detailed = dom.document_fromstring(render(content, ROOT, report_profile='detailed'))
        self.assertEqual(detailed.xpath('//main//figure[@data-presentation="table"]//tbody/tr/td[@data-raw-value]/text()'), raw_values)
        return tree, table

    def test_two_price_endpoints_keep_exact_original_values_without_a_chart(self):
        content = self.saved_price_report()['content']
        figure, = research_figures(content).values()
        self.assertEqual(figure['presentation'], 'table')
        self.inline_inputs(content, ['4.416元', '4.432元'])

    def test_price_comparison_keeps_the_original_frozen_content(self):
        saved = self.saved_price_report()
        content = saved['content']
        before = copy.deepcopy(content)
        paper = render(content, ROOT)
        self.assertEqual(content, before)
        self.assertEqual(paper, saved['html'])
        tree = dom.document_fromstring(paper)
        self.assertIn('0.3623%', tree.text_content())
        for record in content['calculations']:
            self.assertIsNotNone(tree.get_element_by_id(record['id']))
        for record in content['evidence']:
            self.assertIsNotNone(tree.get_element_by_id('ev-' + record['id']))

    def test_same_time_price_and_nav_keep_distinct_labels_and_values(self):
        content = self.method('etf_nav_premium_v1')
        before = copy.deepcopy(content)
        figure = next(iter(research_figures(content).values()))
        self.assertEqual(figure['presentation'], 'table')
        self.assertEqual([row['label'] for row in figure['rows']], ['收盘价', '官方单位净值'])
        self.assertEqual([row['value'] for row in figure['rows']], [10.5, 10])
        tree, table = self.inline_inputs(content, ['10.5元', '10元'])
        self.assertIn('A类份额，CNY', table.get('aria-label'))
        self.assertIn('2026-09-30T15:00:00+08:00', tree.text_content())
        self.assertEqual(content, before)

    def test_single_period_financial_inputs_are_not_a_large_chart(self):
        content = self.method('gross_margin_v1')
        before = copy.deepcopy(content)
        figure = next(iter(research_figures(content).values()))
        self.assertEqual([(row['value'], row['unit']) for row in figure['rows']], [(100, '百万元'), (60, '百万元')])
        _, table = self.inline_inputs(content, ['100百万元', '60百万元'])
        self.assertIn('合并口径，CAS', table.get('aria-label'))
        self.assertEqual(content, before)

    def test_negative_cash_stays_negative_in_the_visible_reading_and_basis(self):
        fixture = self.fixture(methods, 'MethodDependencyWorkflowTests')
        task, host, _ = earnings.prepare_financial(fixture.agent, 'operating_cash_profit_ratio_v1', cash=-8)
        label_synthetic(fixture.agent, task)
        content = compose(fixture.agent.start(task['id'], host))
        figure = next(iter(research_figures(content).values()))
        self.assertEqual([row['value'] for row in figure['rows']], [-8, 12])
        tree, _ = self.inline_inputs(content, ['-8百万元', '12百万元'])
        figure, = tree.xpath('//main//figure[@data-presentation="inline"]')
        self.assertEqual(figure.xpath('.//*[@data-raw-value]/@data-raw-value'), ['-8', '12'])

    def test_uncited_scenario_stays_in_its_own_reading(self):
        from tests.test_report_measurement_accessibility_offline import chart_data
        fixture = self.fixture(documents, 'DocumentContentTests')
        task = fixture.agent.create_case('value')
        content = compose(fixture.agent.start(task['id']))
        for section in ('questions', 'theses'):
            for record in content[section]:
                if 'calculation_refs' in record:
                    record['calculation_refs'] = [ref for ref in record['calculation_refs'] if ref != 'rh_calc_stress']
        content['content_hash'] = fingerprint({key: value for key, value in content.items() if key != 'content_hash'})
        descriptors = research_figures(content)
        self.assertTrue(descriptors)
        self.assertTrue(all(item['presentation'] == 'table' for item in descriptors.values()))
        paper = render(content, ROOT)
        tree = dom.document_fromstring(paper)
        self.assertEqual(chart_data(paper), [])
        figures = {node.get('data-calculation-ref'): node for node in tree.xpath('//main//figure[@data-presentation="inline"]')}
        self.assertEqual(figures.keys(), descriptors.keys())
        for ref, descriptor in descriptors.items():
            node = figures[ref]
            self.assertIn(descriptor['title'], node.text_content())
            target, = node.xpath('./figcaption/a/@href')
            self.assertEqual(len(tree.get_element_by_id(target[1:]).xpath('.//table/tbody/tr')), len(descriptor['rows']))

    def test_fee_chart_groups_original_components_by_fund_not_by_row(self):
        from tests.test_report_measurement_accessibility_offline import chart_data
        fixture = self.fixture(fees, 'ManagementCustodyFeeTests')
        task, host = fixture.prepare()
        done = fixture.agent.start(task['id'], host)
        content = compose(done)
        before = copy.deepcopy(content)
        figure = next(iter(research_figures(content).values()))
        self.assertEqual(figure['presentation'], 'chart')
        self.assertEqual(figure['groups'], [{'name': 'ETF-A', 'indices': [0, 1]}, {'name': 'ETF-B', 'indices': [2, 3]}])
        self.assertEqual(figure['categories'], ['管理费', '托管费'])
        self.assertEqual([[figure['rows'][i]['value'] for i in group['indices']] for group in figure['groups']], [[.15, .05], [.5, .1]])
        paper = render(content, ROOT)
        tree = dom.document_fromstring(paper)
        plot = tree.xpath('//main//figure[@data-calculation-ref]//*[@role="img" and @aria-details]')
        self.assertEqual(len(plot), 1)
        table = tree.get_element_by_id(plot[0].get('aria-details')).find('.//table')
        self.assertEqual(table.xpath('./tbody/tr/td[@data-raw-value]/text()'), ['0.15%/年', '0.05%/年', '0.5%/年', '0.1%/年'])
        self.assertEqual(chart_data(paper)[0]['auto_visual']['scheme'], 'grouped_bar')
        self.assertEqual(content, before)
        self.assertEqual(fixture.agent.export(done['id'])['content'], content)

    def test_index_reading_preserves_variant_currency_and_base(self):
        fixture = self.fixture(indices, 'IndexChangeWorkflowTests')
        task, host = fixture.prepare('net_total_return', currency='美元')
        label_synthetic(fixture.agent, task)
        content = compose(fixture.agent.start(task['id'], host))
        before = copy.deepcopy(content)
        _, table = self.inline_inputs(content, ['1000点', '1050点'])
        self.assertIn('净全收益指数，美元，基期2004-12-31，基点100', table.get('aria-label'))
        self.assertEqual(content, before)

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
        content = self.saved_price_report()['content']
        content['calculations'][0]['inputs']['end_price'] = 999
        with self.assertRaises(ContractError):
            render(content, ROOT)


if __name__ == '__main__':
    unittest.main()
