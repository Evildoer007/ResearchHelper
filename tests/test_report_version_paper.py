"""Client paper stays concise while frozen version details remain readable.

Independent synthetic research and presentation tests. No supplier/model calls,
no edits to existing acceptance tests or previously frozen reports.
"""
import copy
import importlib
from html.parser import HTMLParser
import unittest

from researchhelper.contracts import fingerprint
from researchhelper.visual import render
# Source checks use the tests package; isolated Skill/App checks deliberately
# expose test modules only, without importing the repository bootstrap.
review_fixture = importlib.import_module((__package__ + '.' if __package__ else '') + 'test_judgment_review_workflow')


class ReportParts(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.main = False
        self.paper = []
        self.targets = set()
        self.links = set()
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'main' and values.get('id') == 'main': self.main = True
        if values.get('id'): self.targets.add(values['id'])
        if self.main and tag == 'a': self.links.add(values.get('href', ''))

    def handle_endtag(self, tag):
        if tag == 'main': self.main = False

    def handle_data(self, text):
        if self.main: self.paper.append(text)


class VersionPaperTests(unittest.TestCase):
    def setUp(self):
        self.fixture = review_fixture.JudgmentReviewDimensionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = self.fixture.scenario()
        self.frozen = self.fixture.agent.archive.report(self.task['id'], 2)
        self.content = copy.deepcopy(self.frozen['content'])

    def test_raw_calculation_parameters_stay_off_customer_paper(self):
        page = render(self.content, self.fixture.agent.resources)
        parsed = ReportParts(page)
        text = ''.join(parsed.paper)
        self.assertNotIn('price_basis', text)
        self.assertNotIn('tolerance', text)
        self.assertIn('price_basis', page)
        self.assertIn('tolerance', page)
        self.assertIn('#version-calculation-0', parsed.links)
        self.assertIn('version-calculation-0', parsed.targets)
        self.assertIn('原判断复核', text)
        self.assertIn('20%', text)

    def test_every_version_detail_keeps_a_customer_reader_entry(self):
        page = render(self.content, self.fixture.agent.resources)
        parsed = ReportParts(page)
        for name in ('version-evidence-changes', 'version-original-conditions'):
            self.assertIn('#' + name, parsed.links)
            self.assertIn(name, parsed.targets)
        self.assertNotIn('<details', page)
        self.assertNotIn('<summary', page)
        self.assertIn('<noscript>', page)
        self.assertEqual(self.fixture.agent.archive.report(self.task['id'], 2), self.frozen)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)

    def test_complete_before_and_after_text_is_retained_and_escaped(self):
        preview = copy.deepcopy(self.content)
        row = preview['changes']['calculation_rows'][0]
        row['details'].append(dict(label='原始范围', before_text='<img src=x onerror=bad()>',
                                   after_text='合成口径应完整保留。' * 30))
        preview['content_hash'] = fingerprint({key: value for key, value in preview.items() if key != 'content_hash'})
        page = render(preview, self.fixture.agent.resources)
        self.assertIn('&lt;img src=x onerror=bad()&gt;', page)
        self.assertNotIn('<img src=x onerror=bad()>', page)
        self.assertIn('合成口径应完整保留。' * 30, page)
        self.assertNotIn('合成口径应完整保留。', ''.join(ReportParts(page).paper))
        self.assertEqual(self.fixture.agent.archive.report(self.task['id'], 2), self.frozen)

    def test_knowledge_version_log_uses_the_same_supporting_reader(self):
        preview = copy.deepcopy(self.content)
        preview['changes']['knowledge_changes'] = [dict(id='synthetic_method', prior_version='1', current_version='2')]
        preview['content_hash'] = fingerprint({key: value for key, value in preview.items() if key != 'content_hash'})
        page = render(preview, self.fixture.agent.resources)
        parsed = ReportParts(page)
        self.assertIn('#version-knowledge-changes', parsed.links)
        self.assertIn('version-knowledge-changes', parsed.targets)
        self.assertIn('synthetic_method', page)
        self.assertNotIn('synthetic_method', ''.join(parsed.paper))


if __name__ == '__main__': unittest.main()
