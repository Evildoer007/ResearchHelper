"""Current one-page requirements retain the substance of earlier reader checks."""
import copy
import html
from html.parser import HTMLParser
import unittest

from researchhelper.reports import compose, render
from tests import test_document_content_offline as documents
from tests import test_report_structure_offline as structure


class Anchors(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.ids, self.links = [], []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if 'id' in values:
            self.ids.append(values['id'])
        if values.get('href', '').startswith('#'):
            self.links.append(values['href'][1:])


class RetainedSinglePageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = documents.DocumentContentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def study(self, key):
        done = self.fixture.agent.start(self.fixture.agent.create_case(key)['id'])
        self.assertEqual(done['status'], '已完成', done.get('error'))
        return done, compose(done)

    def test_short_title_retains_original_question_without_directory(self):
        done, content = self.study('etf')
        frozen = self.fixture.agent.export(done['id'])
        self.assertNotEqual(content['title'], content['question'])
        paper = render(content, documents.ROOT)
        self.assertIn('原始问题：'+html.escape(done['question'], quote=True), paper)
        self.assertNotIn('<nav', paper)
        self.assertNotIn('navigateSection', paper)
        self.assertEqual(self.fixture.agent.export(done['id']), frozen)

    def test_fact_only_answer_has_no_empty_methods_or_duplicated_summary(self):
        original = structure.ReportStructureTests()
        content = original.live_content()
        before = copy.deepcopy(content)
        paper = render(content, documents.ROOT)
        self.assertNotIn("id='methods'", paper)
        self.assertNotIn("id='summary'", paper)
        for question in content['questions']:
            self.assertEqual(paper.count(html.escape(question['answer'], quote=True)), 1)
        for evidence in content['evidence']:
            self.assertIn(html.escape(evidence['text'], quote=True), paper)
        self.assertEqual(content, before)

    def test_all_internal_references_have_unique_retained_targets(self):
        for key in ('mechanism', 'etf', 'value'):
            with self.subTest(key=key):
                _, content = self.study(key)
                paper = render(content, documents.ROOT)
                anchors = Anchors(paper)
                self.assertEqual(len(anchors.ids), len(set(anchors.ids)))
                self.assertTrue(set(anchors.links).issubset(set(anchors.ids)))
                self.assertNotIn('<nav', paper)
                self.assertIn('function revealReference', paper)

    def test_document_scope_risks_and_quotes_remain_without_empty_method(self):
        task = self.fixture.material_task()
        done = self.fixture.run_spec(task, self.fixture.spec(task))
        content = compose(done)
        paper = render(content, documents.ROOT)
        self.assertNotIn("id='methods'", paper)
        for target in ('risk', 'scope'):
            self.assertIn("id='"+target+"'", paper)
        for evidence in content['evidence']:
            self.assertEqual(paper.count(html.escape(evidence['text'], quote=True)), 1)
        self.assertIn(html.escape(content['scope'], quote=True), paper)

    def test_mechanism_keeps_judgment_counter_and_tracking_not_empty_methods(self):
        _, content = self.study('mechanism')
        paper = render(content, documents.ROOT)
        self.assertIn('<h1>'+html.escape(content['title'], quote=True)+'</h1>', paper)
        self.assertFalse(content['calculations'])
        self.assertNotIn("id='methods'", paper)
        for thesis in content['theses']:
            for field in ('judgment', 'investment_meaning', 'limitation'):
                self.assertIn(html.escape(thesis[field], quote=True), paper)
            for ref in thesis.get('counter', []):
                self.assertIn("href='#ev-"+ref+"'", paper)
        for track in content['tracking']:
            self.assertIn(html.escape(track['condition'], quote=True), paper)
        self.assertIn("id='scope'", paper)


if __name__ == '__main__':
    unittest.main()
