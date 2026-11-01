"""Independent print/readability regressions; no modification of old tests."""
import copy
import html
from pathlib import Path
import unittest

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

    def test_container_and_print_media_changes_resize_actual_charts(self):
        paper = self.paper()
        self.assertTrue('new ResizeObserver(resizeCharts)' in paper, 'Container observer is missing')
        self.assertTrue("matchMedia('print')" in paper, 'Print media observation is missing')
        self.assertTrue("printMedia.addEventListener('change',resizeCharts)" in paper, 'Print media change is not handled')

    def test_print_resize_flushes_svg_without_shrinking_labels(self):
        paper = self.paper()
        self.assertTrue('chart.getZr().flush()' in paper, 'Print SVG is not flushed')
        self.assertTrue('width:Math.round(bounds.width)' in paper, 'Chart does not use container width')
        self.assertIn('fontSize:14', paper)
        self.assertNotIn("svg{width:100%", paper)

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
        paper = self.paper(content)
        principal = paper.split("<details class='research-appendix'>", 1)[0]
        self.assertFalse(html.escape(question) in principal, 'A full prompt is repeated above the sole answer')
        self.assertIn(html.escape(question), paper)
        self.assertIn(html.escape(content['questions'][0]['answer']), principal)
        self.assertEqual(content, before)

    def test_multiple_answers_keep_distinct_question_headings(self):
        paper = self.paper()
        for question in self.content['questions']:
            self.assertIn('<h3>' + html.escape(question['question']) + '</h3>', paper)

    def test_document_index_still_identifies_original_question(self):
        task = self.fixture.material_task()
        done = self.fixture.run_spec(task, self.fixture.spec(task))
        paper = self.paper(compose(done))
        principal = paper.split("<details class='research-appendix'>", 1)[0]
        self.assertIn(html.escape(done['questions'][0]['question']), principal)
        self.assertIn('document-answer-index', principal)

    def test_frozen_delivery_and_content_are_not_rewritten(self):
        before = copy.deepcopy(self.content)
        task_id = self.content['task_id']
        frozen = self.fixture.agent.export(task_id)
        self.paper()
        self.assertEqual(self.content, before)
        self.assertEqual(self.fixture.agent.export(task_id), frozen)


if __name__ == '__main__':
    unittest.main()
