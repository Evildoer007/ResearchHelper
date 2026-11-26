"""Names and header relationships for the actual sparse tables and fee chart."""
import copy
import json
import unittest
from lxml import html

from researchhelper.reports import render, research_figures
from tests import test_report_visual_selection_offline as fixtures


def chart_data(source):
    """Read this report's input, excluding the bundled library's capabilities."""
    tree=html.document_fromstring(source)
    scripts=[node.text for node in tree.xpath('//script[not(@src)]')
             if node.text and 'const figures=' in node.text]
    if len(scripts)!=1:
        raise AssertionError('Expected one report-specific chart input')
    result,_=json.JSONDecoder().raw_decode(scripts[0].split('const figures=',1)[1])
    return result


class MeasurementTables:
    """Follow each measurement to its inline or referenced original table."""
    def __init__(self, source):
        self.tables,self.headers=[],[]
        tree=html.document_fromstring(source)
        for figure in tree.xpath('//main//figure[@data-calculation-ref]'):
            tables=figure.xpath('.//table')
            if not tables:
                for ident in dict.fromkeys(figure.xpath('.//*[@aria-details]/@aria-details')):
                    targets=tree.xpath('//*[@id=$ident]',ident=ident)
                    if len(targets)!=1:
                        raise AssertionError('Measurement has no unique data target: '+ident)
                    tables.extend(targets[0].xpath('.//table'))
            for table in tables:
                self.tables.append(dict(table.attrib))
                self.headers.extend(dict(node.attrib) for node in table.xpath('./thead//th'))


class ReportMeasurementAccessibilityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReportVisualSelectionTests()
        self.addCleanup(self.fixture.doCleanups)

    def test_measurements_have_named_original_tables_and_column_headers(self):
        for method in ('observed_price_return_v1', 'etf_nav_premium_v1', 'gross_margin_v1'):
            with self.subTest(method=method):
                content = self.fixture.method(method)
                before = copy.deepcopy(content)
                paper = render(content, fixtures.ROOT)
                parsed = MeasurementTables(paper)
                descriptors = list(research_figures(content).values())
                self.assertEqual(len(parsed.tables), len(descriptors))
                for table, descriptor in zip(parsed.tables, descriptors):
                    self.assertIn(descriptor['title'], table['aria-label'])
                    self.assertIn(descriptor['unit'], table['aria-label'])
                    self.assertIn(descriptor['result_label'], table['aria-label'])
                self.assertTrue(parsed.headers)
                self.assertTrue(all(header['scope'] == 'col' for header in parsed.headers))
                tree=html.document_fromstring(paper)
                measurements=tree.xpath('//main//figure[@data-calculation-ref]')
                self.assertEqual(len(measurements),len(descriptors))
                self.assertEqual([node.get('data-presentation') for node in measurements],
                                 [descriptor['presentation'] for descriptor in descriptors])
                self.assertEqual(content, before)

    def test_frozen_two_price_content_and_original_export_remain_unchanged(self):
        saved = self.fixture.saved_price_report()
        content = saved['content']
        before = copy.deepcopy(content)
        paper = render(content, fixtures.ROOT)
        self.assertEqual(content, before)
        self.assertEqual(paper, saved['html'])
        table = MeasurementTables(paper).tables[0]
        self.assertIn('未复权收盘价起终点', table['aria-label'])
        self.assertIn('4.416元', paper)
        self.assertIn('4.432元', paper)
        self.assertNotIn('<script src=', paper)

    def test_fee_chart_and_its_data_table_share_the_same_accessible_measurement(self):
        fixture = self.fixture.fixture(fixtures.fees, 'ManagementCustodyFeeTests')
        task, host = fixture.prepare()
        done = fixture.agent.start(task['id'], host)
        content = fixtures.compose(done)
        paper = render(content, fixtures.ROOT)
        table = MeasurementTables(paper).tables[0]
        self.assertIn('管理费与托管费分项对比', table['aria-label'])
        tree=html.document_fromstring(paper)
        charts=tree.xpath('//main//figure[@data-calculation-ref]//*[@role="img" and @aria-details]')
        self.assertEqual(len(charts),1)
        self.assertEqual(charts[0].get('aria-label'),table['aria-label'])
        self.assertEqual(chart_data(paper)[0]['auto_visual']['scheme'],'grouped_bar')
        self.assertEqual(fixture.agent.export(done['id'])['content'], content)
