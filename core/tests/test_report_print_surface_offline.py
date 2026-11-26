"""Independent print/readability regressions; no modification of old tests."""
import copy
import html
from pathlib import Path
import unittest
from lxml import html as dom

from researchhelper.contracts import fingerprint
from researchhelper.reports import compose, render
from tests import test_document_content_offline as documents

ROOT = Path(__file__).resolve().parents[2]


class ReportPrintSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = documents.DocumentContentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        task = self.fixture.agent.create_case('etf')
        done = self.fixture.agent.start(task['id'])
        self.content = compose(done)

    def paper(self, content=None):
        return render(self.content if content is None else content, ROOT)

    def test_chart_targets_and_sources_are_bound_for_resize_and_print(self):
        from tests.test_report_measurement_accessibility_offline import chart_data
        paper = self.paper()
        tree = dom.document_fromstring(paper)
        specs = chart_data(paper)
        self.assertTrue(specs)
        for spec in specs:
            nodes = tree.xpath('//main//*[@id=$ident]', ident=spec['target'])
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0].get('role'), 'img')
            reference = nodes[0].get('aria-details')
            self.assertTrue(reference)
            self.assertEqual(len(tree.xpath('//*[@id=$ident]', ident=reference)), 1)
        self.assertEqual(len(tree.xpath('//main//*[contains(concat(" ",normalize-space(@class)," ")," chart ")]')), len(specs))

    def test_standard_and_detailed_print_keep_frozen_measurement_data(self):
        from tests.test_report_measurement_accessibility_offline import chart_data
        before = copy.deepcopy(self.content)
        standard = chart_data(self.paper())
        detailed = chart_data(render(self.content, ROOT, report_profile='detailed'))
        self.assertEqual(len(standard), len(detailed))
        for one, other in zip(standard, detailed):
            self.assertEqual(one['rows'], other['rows'])
            self.assertEqual(one['display'], other['display'])
            self.assertEqual(one.get('unit_type'), other.get('unit_type'))
        self.assertEqual(self.content, before)

    def test_print_css_excludes_keyboard_focus_outline(self):
        css = (ROOT / 'products/app/frontend/report.css').read_text()
        printing = css.split('@media print{', 1)[1]
        self.assertTrue(':focus-visible{outline:none}' in printing, 'Keyboard focus must not be printed')
        self.assertIn(':focus-visible{outline:2px solid var(--FOCUS)', css)

    def test_single_answer_does_not_repeat_full_prompt_as_main_heading(self):
        content = copy.deepcopy(self.content)
        question = '核对当前接口所载字段并解释适用范围。' * 12
        content.update(title=question, question=question, theses=[], intent='综合研究')
        content['questions'] = content['questions'][:1]
        content['questions'][0]['question'] = question
        content['content_hash'] = fingerprint({key: value for key, value in content.items() if key != 'content_hash'})
        before = copy.deepcopy(content)
        tree = dom.document_fromstring(self.paper(content))
        main = tree.find('.//main')
        self.assertNotIn(question, main.text_content())
        self.assertIn('原始问题：' + question, tree.get_element_by_id('scope').text_content())
        self.assertIn(content['questions'][0]['answer'], main.text_content())
        self.assertEqual(content, before)

    def test_multiple_answers_keep_distinct_records_and_client_theses(self):
        tree = dom.document_fromstring(self.paper())
        main = tree.find('.//main')
        for question in self.content['questions']:
            record = tree.get_element_by_id('answer-' + question['id'])
            self.assertEqual(record.find('.//h3').text_content(), question['question'])
            self.assertIn(question['answer'], record.text_content())
            self.assertTrue(tree.xpath('//a[@href=$target and @aria-haspopup="dialog"]',
                                       target='#answer-' + question['id']))
        for thesis in self.content['theses']:
            for key in ('title', 'investment_meaning', 'limitation'):
                self.assertIn(thesis[key], main.text_content())

    def test_document_index_still_identifies_original_question(self):
        task = self.fixture.material_task()
        done = self.fixture.run_spec(task, self.fixture.spec(task))
        content = compose(done)
        main = dom.document_fromstring(self.paper(content)).find('.//main')
        self.assertIn(done['questions'][0]['question'], main.text_content())
        self.assertTrue(main.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," document-answer-index ")]'))
        for item in content['evidence']:
            self.assertEqual(main.text_content().count(item['text']), 1)

    def test_frozen_delivery_and_content_are_not_rewritten(self):
        before = copy.deepcopy(self.content)
        task_id = self.content['task_id']
        frozen = self.fixture.agent.export(task_id)
        self.paper()
        self.assertEqual(self.content, before)
        self.assertEqual(self.fixture.agent.export(task_id), frozen)


if __name__ == '__main__':
    unittest.main()
