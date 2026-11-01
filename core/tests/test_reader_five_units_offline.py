"""Reader-five source-unit and immutable-content checks, independent of old tests."""
import copy
import html
import re
import unittest

from researchhelper.reports import compose, render, research_figures, READER_VERSION, TEMPLATE_VERSION
from tests import test_fee_source_units_offline as fees
from tests import test_document_report_index_offline as documents
from tests import test_pdf_quote_reflow_offline as pdf


class ReaderFiveUnitTests(unittest.TestCase):
    def fee_content(self, fields=None):
        fixture = fees.FeeSourceUnitsTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        task, host = fixture.prepare(fields)
        done = fixture.agent.start(task['id'], host)
        self.assertEqual(done['status'], '已完成', done.get('error'))
        return compose(done)

    def assert_figure_units(self, content):
        before = copy.deepcopy(content)
        figure = next(iter(research_figures(content).values()))
        evidence = {item['id']: item for item in content['evidence']}
        for row in figure['rows']:
            self.assertEqual(row['unit'], evidence[row['ref']]['unit'])
            self.assertEqual(row['value'], evidence[row['ref']]['value'])
        paper = render(content, pdf.ROOT)
        markup = re.search(r"<figure class='research-figure'.*?</figure>", paper, re.S).group()
        cells = re.findall(r"<td class='num'>(.*?)</td>", markup)
        self.assertEqual(cells, [html.escape(str(row['value']) + row['unit']) for row in figure['rows']])
        self.assertEqual(content, before)
        self.assertEqual(figure['unit'], '%/年')
        return cells

    def test_percent_source_rows_do_not_gain_annual_suffix(self):
        cells = self.assert_figure_units(self.fee_content())
        self.assertEqual(cells, ['0.15%', '0.05%', '0.5%', '0.1%'])

    def test_mixed_disclosures_preserve_per_row_units(self):
        cells = self.assert_figure_units(self.fee_content({'management_a', 'custody_b'}))
        self.assertEqual(cells, ['0.15%', '0.05%/年', '0.5%/年', '0.1%'])


class ReaderFiveFreezeTests(unittest.TestCase):
    def fixture(self, case):
        fixture = case()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_document_reader_five_keeps_content_hash_template_and_saved_export(self):
        fixture = self.fixture(documents.DocumentReportIndexTests)
        before = copy.deepcopy(fixture.content)
        paper = render(fixture.content, pdf.ROOT)
        self.assertEqual(READER_VERSION, 'rh-reader-5')
        self.assertEqual(TEMPLATE_VERSION, 'rh-paper-3')
        self.assertIn("data-reader-version='rh-reader-5'", paper)
        self.assertIn("<header class='report-header'>", paper)
        self.assertIn("研究截止：<time datetime='" + html.escape(fixture.content['cutoff']) + "'>", paper)
        self.assertEqual(fixture.content, before)
        self.assertEqual(fixture.fixture.agent.export(fixture.task_id), fixture.frozen)

    def test_pdf_reader_five_retains_exact_quote_and_original_export(self):
        fixture = self.fixture(pdf.PDFQuoteReflowTests)
        before = copy.deepcopy(fixture.content)
        paper = render(fixture.content, pdf.ROOT)
        self.assertIn("data-reader-version='rh-reader-5'", paper)
        self.assertIn('模板' + fixture.content['template_version'], paper)
        for evidence in fixture.content['evidence']:
            self.assertEqual(paper.count(html.escape(evidence['text'], quote=True)), 1)
        self.assertEqual(fixture.content, before)
        self.assertEqual(fixture.fixture.agent.export(fixture.done['id']), fixture.original_export)

    def test_fact_period_is_visible_but_not_part_of_object_heading(self):
        fixture = self.fixture(documents.DocumentReportIndexTests)
        task = fixture.fixture.agent.create_case('mechanism')
        content = compose(fixture.fixture.agent.start(task['id']))
        before = copy.deepcopy(content)
        paper = render(content, pdf.ROOT)
        for index, evidence in enumerate(content['evidence'], 1):
            heading = f"<h3>3.{index} {html.escape(evidence['object'])}</h3>"
            self.assertIn(heading + "<p class='figure-context'>" + html.escape(evidence['period']) + '</p>', paper)
        self.assertEqual(content, before)


if __name__ == '__main__':
    unittest.main()
