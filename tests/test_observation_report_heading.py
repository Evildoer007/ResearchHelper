"""Report labels use adopted metadata, with the original study kept frozen."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from researchhelper.reports import report_heading, render, check_content
from researchhelper.contracts import fingerprint
from researchhelper.visual import research_figures

ROOT=Path(__file__).resolve().parents[1]
RETAINED=ROOT/'result/acceptance/one-sentence-research-8jqdx045/report.json'
HTML=RETAINED.with_name('report.html')


class ObservationReportHeadingTests(unittest.TestCase):
    def setUp(self):
        self.originals={p:p.read_bytes() for p in (RETAINED,HTML)}
        self.content=json.loads(self.originals[RETAINED])
        guard=patch('socket.socket.connect',side_effect=AssertionError('Offline only'))
        guard.start();self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertTrue(all(p.read_bytes()==raw for p,raw in self.originals.items()))

    def test_actual_long_question_uses_adopted_series_name(self):
        self.assertEqual(report_heading(self.content),'中国CPI当月同比观察')

    def test_short_user_title_is_preserved(self):
        self.content['title']='九个月CPI观察'
        self.assertEqual(report_heading(self.content),'九个月CPI观察')

    def test_unverified_source_never_names_the_report(self):
        self.content['evidence'][0]['source_check']='待核验'
        self.assertEqual(report_heading(self.content),'综合研究报告')

    def test_different_country_or_frequency_is_not_merged(self):
        for key,value in (('country','中国香港'),('frequency','Y')):
            content=copy.deepcopy(self.content)
            content['evidence'][0]['economic_context'][key]=value
            self.assertEqual(report_heading(content),'综合研究报告')

    def test_missing_reference_is_not_silently_ignored(self):
        self.content['evidence'].pop()
        self.assertEqual(report_heading(self.content),'综合研究报告')

    def test_code_and_metadata_reference_must_agree(self):
        self.content['evidence'][0]['economic_context']['metadata_reference']['display_id']='OTHER'
        self.assertEqual(report_heading(self.content),'综合研究报告')

    def test_analytical_tasks_do_not_become_observation_reports(self):
        for kind in ('mechanism','investment','comparison'):
            content=copy.deepcopy(self.content);content['questions'][0]['kind']=kind
            self.assertEqual(report_heading(content),'综合研究报告')

    def test_unsafe_or_long_metadata_does_not_enter_heading(self):
        for name in ('<img src=x>','CPI\n当月同比','指标'*30):
            content=copy.deepcopy(self.content)
            for item in content['evidence']:item['economic_context']['name']=name
            self.assertEqual(report_heading(content),'综合研究报告')

    def test_render_keeps_frozen_content_original_and_chart_data(self):
        before=fingerprint(self.content);figures=research_figures(self.content)
        check_content(self.content)
        page=render(self.content,ROOT)
        self.assertIn('<h1>中国CPI当月同比观察</h1>',page)
        self.assertEqual(fingerprint(self.content),before)
        self.assertEqual(research_figures(self.content),figures)
        self.assertEqual(json.loads(self.originals[RETAINED])['title'],self.content['title'])


if __name__=='__main__':
    unittest.main()
