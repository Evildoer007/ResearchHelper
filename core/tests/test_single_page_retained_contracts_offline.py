"""Current one-page requirements retain the substance of earlier reader checks."""
import copy
import html
from html.parser import HTMLParser
from lxml import html as dom
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
        frozen = copy.deepcopy(self.fixture.agent.export(done['id']))
        paper = render(content, documents.ROOT)
        tree = dom.document_fromstring(paper)
        self.assertIn('原始问题：'+done['question'],tree.get_element_by_id('scope').text_content())
        self.assertFalse(tree.xpath('//main//nav'))
        self.assertEqual(len(tree.xpath('//aside[@class="report-toc"]/nav')),1)
        self.assertEqual(self.fixture.agent.export(done['id']),frozen)

    def test_fact_only_answer_has_no_empty_methods_or_duplicated_summary(self):
        fixture = structure.ReportStructureTests()
        self.addCleanup(fixture.doCleanups)
        _, saved = fixture.current_fact()
        content = saved['content']
        before = copy.deepcopy(content)
        paper = render(content, documents.ROOT)
        main = dom.document_fromstring(paper).find('.//main')
        self.assertFalse(main.xpath('.//*[@id="methods" or @id="summary"]'))
        for question in content['questions']:
            self.assertEqual(main.text_content().count(question['answer']), 1)
        for evidence in content['evidence']:
            self.assertIn(html.escape(evidence['text'], quote=True), paper)
        self.assertEqual(content, before)

    def test_all_internal_references_have_unique_retained_targets(self):
        for key in ('mechanism', 'etf', 'value'):
            with self.subTest(key=key):
                _, content = self.study(key)
                paper = render(content, documents.ROOT)
                anchors = Anchors(paper)
                self.assertEqual(len(anchors.ids),len(set(anchors.ids)))
                self.assertTrue(set(anchors.links).issubset(set(anchors.ids)))
                tree = dom.document_fromstring(paper)
                self.assertFalse(tree.xpath('//main//nav'))
                self.assertEqual(len(tree.xpath('//aside[@class="report-toc"]/nav')),1)
                surface=tree.get_element_by_id('report-inspector')
            dialogs=([surface] if surface.tag=='dialog' or surface.get('role')=='dialog'
                     else surface.xpath('.//dialog|.//*[@role="dialog"]'))
            self.assertEqual(len(dialogs),1)
            self.assertTrue(dialogs[0].get('aria-labelledby'))

    def test_document_scope_risks_and_quotes_remain_without_empty_method(self):
        task = self.fixture.material_task()
        done = self.fixture.run_spec(task,self.fixture.spec(task))
        content = compose(done)
        tree = dom.document_fromstring(render(content,documents.ROOT))
        self.assertFalse(tree.xpath('//main//*[@id="methods"]'))
        self.assertIn(content['scope'],tree.find('.//main').text_content())
        self.assertIn(content['scope'],tree.get_element_by_id('scope').text_content())
        quotes = tree.xpath('//main//p[@class="document-content-quote"]')
        self.assertCountEqual([node.text_content() for node in quotes],[e['text'] for e in content['evidence']])
        self.assertFalse(tree.xpath('//main//*[@id="risk" and not(.//li)]'))

    def test_mechanism_keeps_judgment_counter_and_tracking_not_empty_methods(self):
        _, content = self.study('mechanism')
        tree = dom.document_fromstring(render(content,documents.ROOT))
        main = tree.find('.//main')
        self.assertEqual(main.find('.//h1').text_content(),content['title'])
        self.assertFalse(content['calculations'])
        self.assertFalse(main.xpath('.//*[@id="methods"]'))
        for thesis in content['theses']:
            for field in ('judgment','investment_meaning','limitation'):
                self.assertIn(thesis[field],main.text_content())
            for ref in thesis.get('counter',[]):
                self.assertTrue(main.xpath('.//a[@href=$target]',target='#ev-'+ref))
        for track in content['tracking']:self.assertIn(track['condition'],main.text_content())
        self.assertIsNotNone(tree.get_element_by_id('scope'))


if __name__ == '__main__':
    unittest.main()
