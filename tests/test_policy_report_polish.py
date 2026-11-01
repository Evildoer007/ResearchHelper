"""Presentation-only checks on a complete saved policy report."""
import copy
import hashlib
from html.parser import HTMLParser
import json
import unittest

from tests import ROOT
from researchhelper.reports import report_heading
from researchhelper.visual import render, statement_paragraphs


SOURCE = ROOT / 'result/acceptance/one-sentence-research-11r37p0e/report.json'


class PaperMarkup(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.main = False
        self.links = []
        self.text = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag == 'main':
            self.main = True
        if self.main and tag == 'a':
            self.links.append(dict(attrs))

    def handle_endtag(self, tag):
        if tag == 'main':
            self.main = False

    def handle_data(self, value):
        if self.main:
            self.text.append(value)


class PolicyReportPolishTests(unittest.TestCase):
    def setUp(self):
        self.original = SOURCE.read_bytes()
        self.content = json.loads(self.original)
        self.question = self.content['questions'][0]

    def tearDown(self):
        self.assertEqual(SOURCE.read_bytes(), self.original)

    def test_heading_uses_the_single_adopted_official_document_not_the_entry_name(self):
        title = report_heading(self.content)
        self.assertIn('FOMC', title)
        self.assertIn('2026-06-17', title)
        self.assertNotEqual(title, '综合研究报告')

    def test_explicit_short_user_title_remains_authoritative(self):
        self.content['title'] = '用户指定标题'
        self.assertEqual(report_heading(self.content), '用户指定标题')

    def test_unknown_or_mixed_source_cannot_supply_an_official_heading(self):
        self.content['evidence'][0]['source'] = 'unknown'
        self.assertEqual(report_heading(self.content), '综合研究报告')

    def test_first_literal_quote_is_source_linked_and_not_rewritten(self):
        markup = ''.join(statement_paragraphs(self.question, 'answer', self.content))
        links = PaperMarkup('<main>' + markup + '</main>').links
        quoted = [link for link in links if link.get('class') == 'source-quotation']
        self.assertEqual(len(quoted), 5)
        self.assertTrue(all(link['href'].startswith('#ev-') for link in quoted))
        self.assertTrue(all(link['aria-haspopup'] == 'dialog' for link in quoted))

    def test_exact_same_carrier_is_cited_again_without_reprinting_the_paragraph(self):
        markup = ''.join(statement_paragraphs(self.question, 'answer', self.content))
        paper = PaperMarkup('<main>' + markup + '</main>')
        self.assertEqual(sum(link.get('class') == 'source-quotation-repeat' for link in paper.links), 1)
        policy = next(item['text'] for item in self.content['evidence'] if item['id'] == 'ev_fomc_policy_action_20260617')
        self.assertEqual(''.join(paper.text).count(policy), 1)
        self.assertEqual(self.question['answer'].count(policy), 2)

    def test_different_carriers_with_same_words_are_not_collapsed(self):
        duplicate = next(item for item in self.content['evidence'] if item['id'] == 'ev_fomc_rationale_20260617')
        duplicate['artifact_id'] = 'a_different_carrier'
        markup = ''.join(statement_paragraphs(self.question, 'answer', self.content))
        links = PaperMarkup('<main>' + markup + '</main>').links
        self.assertEqual(sum(link.get('class') == 'source-quotation-repeat' for link in links), 0)

    def test_unbound_or_legacy_text_is_not_subject_to_quote_presentation(self):
        self.content.pop('verbatim_evidence_segments_version')
        markup = ''.join(statement_paragraphs(self.question, 'answer', self.content))
        self.assertNotIn('source-quotation', markup)

    def test_full_report_groups_one_carrier_and_uses_the_actual_publisher(self):
        original = copy.deepcopy(self.content)
        page = render(self.content, ROOT)
        self.assertEqual(self.content, original)
        paper = PaperMarkup(page)
        body = ''.join(paper.text)
        self.assertIn('来源：美联储', body)
        self.assertNotIn('来源：official', body)
        self.assertEqual(sum(link.get('href') == '#references-q1-source-1' for link in paper.links), 1)
        self.assertTrue('同一原始载体' in page)
        self.assertTrue(self.question['answer'] in self.content['questions'][0]['answer'])

    def test_publisher_label_does_not_trust_hostname_substrings(self):
        from researchhelper.reports import source_publisher_label
        item = copy.deepcopy(self.content['evidence'][0])
        item['artifact']['snapshot']['official_origin']['url'] = 'https://www.federalreserve.gov.evil.invalid/test'
        self.assertEqual(source_publisher_label(item), '官方原文')
        self.assertEqual(source_publisher_label(dict(source='ifind')), 'iFinD')

    def test_frozen_report_hash_stays_identical_after_presentation(self):
        before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        render(self.content, ROOT)
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), before)

    def test_unbound_artifact_or_noncanonical_url_cannot_supply_a_heading(self):
        for key, value in (('artifact_id', 'foreign_original'), ('snapshot_hash', 'changed')):
            content = copy.deepcopy(self.content)
            content['evidence'][0][key] = value
            self.assertEqual(report_heading(content), '综合研究报告')
        for url in ('https://www.federalreserve.gov.evil.invalid/newsevents/pressreleases/monetary20260617a.htm',
                    'https://www.federalreserve.gov/newsevents/pressreleases/monetary20260617a.htm?token=untrusted'):
            content = copy.deepcopy(self.content)
            content['evidence'][0]['artifact']['snapshot']['official_origin']['url'] = url
            self.assertEqual(report_heading(content), '综合研究报告')

    def test_unique_words_and_numbers_are_preserved_exactly(self):
        bindings = self.question['numeric_integrity']['fields']['answer']['bindings']
        repeated = next(item for item in bindings if item['ref'] == 'ev_fomc_rationale_20260617')
        start, end = repeated['span']
        expected = self.question['answer'][:start] + '原文' + self.question['answer'][end:]
        markup = ''.join(statement_paragraphs(self.question, 'answer', self.content))
        self.assertEqual(''.join(PaperMarkup('<main>' + markup + '</main>').text), expected)

    def test_changed_text_hash_or_source_location_does_not_remove_a_passage(self):
        self.question['numeric_integrity']['fields']['answer']['text_hash'] = 'changed'
        markup = ''.join(statement_paragraphs(self.question, 'answer', self.content))
        self.assertNotIn('source-quotation', markup)
        self.assertEqual(''.join(PaperMarkup('<main>' + markup + '</main>').text), self.question['answer'])
        self.content = json.loads(self.original)
        duplicate = next(item for item in self.content['evidence'] if item['id'] == 'ev_fomc_rationale_20260617')
        duplicate['record_locator']['part'] = 'another_location'
        markup = ''.join(statement_paragraphs(self.content['questions'][0], 'answer', self.content))
        self.assertNotIn('source-quotation-repeat', markup)
