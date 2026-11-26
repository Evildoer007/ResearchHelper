"""Present frozen real results without republishing internal draft questions."""
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from researchhelper.contracts import fingerprint
from researchhelper.visual import render
from researchhelper.reports import READER_VERSION

ROOT = Path(__file__).resolve().parents[1]


def prepare_fixture():
    return json.loads((ROOT/'result/acceptance/one-sentence-research-mrul9i5w/report.json').read_text())


class MainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag == 'main': self.active = True
    def handle_endtag(self, tag):
        if tag == 'main': self.active = False
    def handle_data(self, data):
        if self.active: self.parts.append(data)


class ReportStatusPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = os.environ.get('RESEARCHHELPER_REAL_REPORT_FIXTURE')
        cls.content = json.loads(Path(fixture).read_text()) if fixture else prepare_fixture()
        cls.original_fingerprint = fingerprint(cls.content)
        resources = Path(os.environ.get('RESEARCHHELPER_SSE_ETF_RESOURCES', str(ROOT)))
        with patch('socket.socket.connect', side_effect=AssertionError('No calls in presentation checks')):
            cls.markup = render(cls.content, resources)
        reader = MainText()
        reader.feed(cls.markup)
        cls.visible = ''.join(reader.parts)

    def test_explicit_partial_status_is_not_relabelled_complete(self):
        self.assertTrue(any(q.get('required') and q['status'] != '已回答' for q in self.content['questions']))
        self.assertIn('研究状态：尚未完成。', self.visible)
        self.assertIn('不能替代完整投资判断', self.visible)

    def test_original_question_is_available_in_working_record_not_client_body(self):
        question = self.content['question']
        self.assertNotIn(question, self.visible)
        self.assertIn(html.escape(question, quote=True), self.markup)
        self.assertIn('未完成的必要问题', self.markup)
        self.assertIn('查看核验底稿', self.visible)

    def test_registered_percentage_uses_sufficient_display_precision(self):
        calculation = self.content['calculations'][0]
        self.assertEqual(calculation['method'], 'etf_daily_nav_premium_v1')
        self.assertIn('-0.0143%', self.markup)
        self.assertIn(str(calculation['output']['value']), self.markup)

    def test_original_input_values_are_not_rounded_or_recomputed(self):
        self.assertIn('4.1856', self.markup)
        self.assertIn('4.185', self.markup)
        for item in self.content['evidence']:
            self.assertIn(item['id'], self.markup)

    def test_reader_does_not_change_frozen_content_or_financial_verdict(self):
        from lxml import html as dom
        self.assertEqual(dom.document_fromstring(self.markup).get('data-reader-version'), READER_VERSION)
        self.assertEqual(fingerprint(self.content), self.original_fingerprint)
        self.assertEqual(len(self.content['calculations']), 1)
        self.assertEqual(len(self.content['theses']), 1)

    def test_paper_keeps_public_limits_sources_and_no_accordion(self):
        self.assertNotIn('<details', self.markup)
        self.assertIn('research-run-diagnostics', self.markup)
        self.assertIn('单一估值日', self.visible)
        self.assertIn('来源', self.visible)


if __name__ == '__main__':
    unittest.main()
