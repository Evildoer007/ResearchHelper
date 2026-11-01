"""Client labels stay readable; frozen financial content and detail remain."""
import copy
import hashlib
import json
import unittest
from html.parser import HTMLParser

from tests import ROOT
from researchhelper.visual import render, research_figures
from researchhelper.contracts import METHOD_REGISTRY


SOURCE = ROOT / 'result/acceptance/one-sentence-research-rj9qwh4p/report.json'


class ClientText(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.main = False
        self.text = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag == 'main': self.main = True

    def handle_endtag(self, tag):
        if tag == 'main': self.main = False

    def handle_data(self, text):
        if self.main: self.text.append(text)


class NavClientCopyTests(unittest.TestCase):
    def setUp(self):
        self.original = SOURCE.read_bytes()
        self.content = json.loads(self.original)
        self.before = copy.deepcopy(self.content)
        self.page = render(self.content, ROOT)
        self.client = ''.join(ClientText(self.page).text)

    def tearDown(self):
        self.assertEqual(self.content, self.before)
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).digest(), hashlib.sha256(self.original).digest())

    def test_public_copy_has_human_share_currency_and_publisher_labels(self):
        self.assertNotIn('listed_etf_unit:', self.client)
        self.assertNotIn('CNY', self.client)
        self.assertIn('上市ETF交易份额，人民币', self.client)
        self.assertIn('来源：iFinD', self.client)

    def test_procedural_rules_are_available_in_details_not_client_body(self):
        definition = METHOD_REGISTRY['etf_daily_nav_premium_v1']
        note = definition.get('limitations') or definition['conditions']
        if isinstance(note, list): note = '；'.join(note)
        self.assertIn(note, self.page)
        self.assertNotIn('不得填补盘中时刻', self.client)
        self.assertIn('不代表同刻可成交价差或套利机会', self.client)
        self.assertIn('listed_etf_unit:159915.SZ', self.page)
        self.assertNotIn('<details', self.page)

    def test_raw_values_calculation_and_math_are_preserved(self):
        for value in (3.182, 3.1769):
            self.assertIn("data-raw-value='" + str(value) + "'", self.page)
        self.assertIn('0.16%', self.client)
        self.assertIn('<mfrac>', self.page)
        self.assertEqual(self.content['calculations'], self.before['calculations'])

    def test_renderer_does_not_rewrite_machine_context_or_duplicate_date_in_caption(self):
        figures = research_figures(self.content)
        figure = next(iter(figures.values()))
        self.assertIn('listed_etf_unit:159915.SZ', figure['basis'])
        self.assertIn('CNY', figure['basis'])
        self.assertEqual(figure['presentation'], 'table')
        context = self.page.split("<p class='figure-context'>", 1)[1].split('</p>', 1)[0]
        self.assertEqual(context.count('2026-03-31'), 1)


if __name__ == '__main__':
    unittest.main()
