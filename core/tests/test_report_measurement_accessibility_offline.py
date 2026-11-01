"""Names and header relationships for the actual sparse tables and fee chart."""
import copy
from html.parser import HTMLParser
import unittest

from researchhelper.reports import render, research_figures
from tests import test_report_visual_selection_offline as fixtures


class MeasurementTables(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.active, self.tables, self.headers = False, [], []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'figure' and 'data-calculation-ref' in values:
            self.active = True
        if self.active and tag == 'table':
            self.tables.append(values)
        if self.active and tag == 'th':
            self.headers.append(values)

    def handle_endtag(self, tag):
        if tag == 'figure':
            self.active = False


class ReportMeasurementAccessibilityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReportVisualSelectionTests()
        self.addCleanup(self.fixture.doCleanups)

    def test_sparse_tables_have_actual_names_and_column_headers_without_new_copy(self):
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
                self.assertNotIn("class='chart", paper)
                self.assertEqual(content, before)

    def test_real_frozen_content_and_original_export_remain_unchanged(self):
        content = self.fixture.real()
        before = copy.deepcopy(content)
        paper = render(content, fixtures.ROOT)
        self.assertEqual(content, before)
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
        self.assertIn("aria-label='"+table['aria-label']+"'", paper)
        self.assertIn("class='chart chart-grouped'", paper)
        self.assertEqual(fixture.agent.export(done['id'])['content'], content)
