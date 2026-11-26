"""Draft figures preserve actual values, independent bases and complete tables."""
import copy
import hashlib
import json
from pathlib import Path
import re
import unittest

from tests import ROOT
from researchhelper.contracts import fingerprint
from researchhelper.drafts import draft_figures, render
from researchhelper.draft_sources import observations
from tests.test_draft_source_types import MACRO, WEIGHTS, record, task_for
from researchhelper.index_sources import parse_csi_workbook

REAL = ROOT/'result/acceptance/one-sentence-research-q3r0y_qo/task.json'


class DraftFigureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = hashlib.sha256(REAL.read_bytes()).hexdigest()
        task = json.loads(REAL.read_text())
        # Only presentation is exercised; never resume the old paid study.
        from researchhelper.drafts import compose
        cls.content = compose(task)

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(REAL.read_bytes()).hexdigest() == cls.original

    def test_all_five_actual_financial_series_include_cashflow(self):
        specs = draft_figures(self.content['draft']['facts'])
        self.assertEqual(len(specs), 5)
        self.assertIn('ths_ncf_from_oa_stock', {row['field'] for row in specs})
        self.assertTrue(all(row['type'] == 'bar' for row in specs))

    def test_monthly_series_is_a_line_of_nine_original_points(self):
        task = json.loads(MACRO.read_text())
        spec = draft_figures(observations(task))[0]
        self.assertEqual(spec['type'], 'line')
        self.assertEqual(spec['dates'], sorted(spec['dates']))
        self.assertEqual((spec['values'][0], spec['values'][-1]), (.2, .9))

    def test_missing_points_remain_gaps_instead_of_becoming_zero(self):
        facts = observations(json.loads(MACRO.read_text()))
        facts[4]['value'] = None
        spec = draft_figures(facts)[0]
        self.assertIsNone(spec['values'][spec['dates'].index(facts[4]['period'])])

    def test_all_missing_values_use_table_without_an_empty_curve(self):
        facts = observations(json.loads(MACRO.read_text()))
        for row in facts:
            row['value'] = None
        self.assertEqual(draft_figures(facts), [])

    def test_identical_overlap_does_not_hide_a_usable_series(self):
        facts = observations(json.loads(MACRO.read_text()))
        specs = draft_figures(facts + copy.deepcopy(facts))
        self.assertEqual(len(specs), 1)
        self.assertEqual(len(specs[0]['values']), 9)

    def test_conflicting_source_revisions_are_not_merged(self):
        facts = observations(json.loads(MACRO.read_text()))
        revised = copy.deepcopy(facts)
        for row in revised:
            row['artifact_id'] = 'different_revision'
        revised[0]['value'] += .1
        specs = draft_figures(facts + revised)
        self.assertEqual(len(specs), 2)
        self.assertEqual(len(specs[0]['dates']), 9)
        self.assertNotEqual(specs[0]['values'], specs[1]['values'])

    def test_index_chart_ranks_actual_top_fifteen_without_dropping_table_rows(self):
        parsed = parse_csi_workbook(WEIGHTS.read_bytes(), '000300.SH', 'weights')
        source = record(dict(parsed=parsed, role='weights'), 'index_file'); source['source'] = 'official'
        facts = observations(task_for(source))
        spec = draft_figures(facts)[0]
        self.assertTrue(spec['horizontal'])
        self.assertEqual((len(spec['values']), spec['original_rows']), (15, 300))
        self.assertEqual(spec['values'], sorted([row['value'] for row in facts], reverse=True)[:15])
        content = copy.deepcopy(self.content)
        content['draft']['facts'] = facts
        content['source_records'] = [dict(source,task_id=content['task_id'],research_version=content['research_version'])]
        content.pop('content_hash', None);content['content_hash'] = fingerprint(content)
        html = render(content, ROOT)
        from lxml import html as html_parser
        tree=html_parser.document_fromstring(html)
        self.assertEqual(len(tree.get_element_by_id('complete-report-data').xpath('.//tbody/tr')),300)
        self.assertFalse(tree.xpath('//main//figure'))
        self.assertEqual(len(spec['data_refs']),15)
        self.assertTrue('000001.SZ' in html)

    def test_actual_financial_report_keeps_cashflow_prose_and_original_amounts(self):
        before = copy.deepcopy(self.content)
        html = render(self.content, ROOT)
        self.assertEqual(self.content, before)
        # This older report has no registered arithmetic and no verified
        # amount unit. Preserve its prose and raw internal figures without
        # making absolute currency plots in the client publication.
        from lxml import html as html_parser
        client = html_parser.document_fromstring(html)
        self.assertFalse(client.xpath('//main//div[@class="chart"]'))
        detail = render(self.content, ROOT, report_profile='detailed')
        tree = html_parser.document_fromstring(detail)
        third = tree.get_element_by_id('logic-3')
        self.assertIn('经营活动现金流量净额', third.text_content())
        self.assertFalse(tree.xpath('//main//div[@class="chart"]'))
        source = tree.get_element_by_id('data')
        for row in self.content['draft']['facts']:
            self.assertTrue(source.xpath('.//tr[@data-observation-id=$ref]', ref=row['id']))
        for logic in self.content['draft']['logics']:
            self.assertIn(logic['analysis'], html)
            self.assertIn(logic['analysis'], detail)
        self.assertNotIn('<details', html)
        self.assertNotIn('<details', detail)
        self.assertEqual(self.content, before)


if __name__ == '__main__':
    unittest.main()
