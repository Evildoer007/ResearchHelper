"""Visible document navigation and frozen content across reader versions."""
import copy
import unittest
from lxml import html

from researchhelper.contracts import fingerprint
from researchhelper.reports import compose, render, READER_VERSION, TEMPLATE_VERSION
from tests import test_document_content_offline as documents


class DocumentReportIndexTests(unittest.TestCase):
    def setUp(self):
        self.fixture=documents.DocumentContentTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        task=self.fixture.material_task()
        done=self.fixture.run_spec(task,self.fixture.spec(task))
        self.content=compose(done)
        self.frozen=self.fixture.agent.export(done['id'])
        self.task_id=done['id']

    def paper(self,content=None):
        content=copy.deepcopy(self.content if content is None else content)
        content['content_hash']=fingerprint({key:value for key,value in content.items() if key!='content_hash'})
        return render(content,documents.ROOT)

    def tree(self,content=None):return html.document_fromstring(self.paper(content))

    def test_exact_generated_answer_is_a_compact_index_not_duplicate_text(self):
        tree=self.tree()
        rows=tree.xpath('//main//article[@class="document-answer-index"]')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].find('h3').text_content(),'2.1 文件写了什么？')
        self.assertEqual(rows[0].xpath('.//a/@href'),['#ev-clause'])
        self.assertEqual(tree.find('.//main').text_content().count(self.content['evidence'][0]['text']),1)
        self.assertIsNotNone(tree.get_element_by_id('ev-clause'))

    def test_excerpt_heading_comes_from_its_explicit_question_reference(self):
        tree=self.tree()
        self.assertEqual(tree.get_element_by_id('ev-clause').find('h3').text_content(),'3.1 文件写了什么？')

    def test_shared_excerpt_retains_every_distinct_linked_question(self):
        content=copy.deepcopy(self.content)
        second=copy.deepcopy(content['questions'][0]);second.update(id='q2',question='原件载明什么生效时间？')
        content['questions'].append(second)
        tree=self.tree(content)
        self.assertEqual(len(tree.xpath('//main//article[@class="document-answer-index"]')),2)
        self.assertEqual(tree.get_element_by_id('ev-clause').find('h3').text_content(),
            '3.1 文件写了什么？；原件载明什么生效时间？')
        self.assertEqual(len(tree.xpath('//main//p[@class="document-content-quote"]')),1)

    def test_unreferenced_excerpt_retains_original_object_heading(self):
        content=copy.deepcopy(self.content);content['questions']=[]
        self.assertEqual(self.tree(content).get_element_by_id('ev-clause').find('h3').text_content(),'3.1 材料示例政策')

    def test_distinct_legacy_answer_is_never_elided_or_compacted(self):
        content=copy.deepcopy(self.content)
        content['questions'][0]['answer']='历史交付另有解释，不能因为摘录相近而删除。'
        tree=self.tree(content)
        self.assertFalse(tree.xpath('//main//article[@class="document-answer-index"]'))
        self.assertIn(content['questions'][0]['answer'],tree.find('.//main').text_content())
        self.assertIn(content['evidence'][0]['text'],tree.find('.//main').text_content())

    def test_missing_reference_does_not_hide_the_saved_answer(self):
        content=copy.deepcopy(self.content);content['questions'][0]['refs'].append('missing-original')
        tree=self.tree(content)
        self.assertFalse(tree.xpath('//main//article[@class="document-answer-index"]'))
        self.assertIn(' '.join(content['questions'][0]['answer'].split()),' '.join(tree.find('.//main').text_content().split()))

    def test_question_and_identity_are_escaped_in_both_locations(self):
        content=copy.deepcopy(self.content)
        content['questions'][0]['question']='<script>原件&核对</script>'
        content['evidence'][0]['id']="quote'unsafe";content['questions'][0]['refs']=["quote'unsafe"]
        tree=self.tree(content)
        self.assertEqual(tree.get_element_by_id("ev-quote'unsafe").find('h3').text_content(),
            '3.1 <script>原件&核对</script>')
        self.assertFalse(tree.xpath('//main//script'))
        self.assertIn("#ev-quote'unsafe",tree.xpath('//main//a/@href'))

    def test_normal_research_does_not_adopt_document_index_or_question_titles(self):
        done=self.fixture.agent.start(self.fixture.agent.create_case('mechanism')['id'])
        tree=self.tree(compose(done))
        self.assertFalse(tree.xpath('//main//*[@class="document-answer-index" or @class="document-content-quote"]'))
        # Research evidence is available in its separate source reader, while
        # the customer paper keeps its research structure.
        for item in done['evidence']:
            record=tree.get_element_by_id('ev-'+item['id'])
            self.assertIn(item['text'],' '.join(record.text_content().split()))

    def test_new_presentation_keeps_content_template_hash_and_saved_export(self):
        before=copy.deepcopy(self.content);tree=self.tree()
        self.assertEqual(tree.get('data-reader-version'),READER_VERSION)
        self.assertEqual(self.content['template_version'],TEMPLATE_VERSION)
        self.assertEqual(tree.find('body').get('data-report-template'),'report-standard')
        self.assertEqual(self.content,before)
        self.assertEqual(self.fixture.agent.export(self.task_id),self.frozen)
