"""Single-page paper contract: no directory, complete retained research evidence."""
import copy
import html
import unittest
from pathlib import Path

from researchhelper.reports import compose, render, research_date_label
from tests import test_document_content_offline as documents

ROOT = Path(__file__).resolve().parents[2]


class SinglePageReportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = documents.DocumentContentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def study(self, key='mechanism'):
        task = self.fixture.agent.create_case(key)
        done = self.fixture.agent.start(task['id'])
        return done, compose(done)

    def test_paper_has_no_directory_sidebar_or_workbench_buttons(self):
        _, content = self.study()
        paper = render(content, ROOT)
        self.assertIn("class='single-page-paper'", paper)
        for markup in ('<nav', '<aside', '<button', 'aria-label=\'报告目录\'', 'markSection'):
            self.assertNotIn(markup, paper)
        self.assertIn("class='research-appendix'", paper)
        self.assertIn('查看研究依据', paper)

    def test_complete_content_and_archived_export_are_not_rewritten(self):
        done, content = self.study('value')
        before = copy.deepcopy(content)
        frozen = self.fixture.agent.export(done['id'])
        paper = render(content, ROOT)
        for item in content['evidence']:
            self.assertIn(html.escape(item['text'], quote=True), paper)
            self.assertIn("id='ev-" + item['id'] + "'", paper)
        for item in content['theses']:
            for key in ('title', 'investment_meaning', 'limitation'):
                self.assertIn(html.escape(item[key], quote=True), paper)
        for item in content['calculations']:
            self.assertIn("id='" + item['id'] + "'", paper)
        self.assertEqual(content, before)
        self.assertEqual(self.fixture.agent.export(done['id']), frozen)

    def test_evidence_navigation_unfolds_only_target_ancestors(self):
        _, content = self.study()
        paper = render(content, ROOT)
        self.assertIn('function revealReference(id)', paper)
        self.assertIn("if(parent.tagName==='DETAILS')parent.open=true", paper)
        self.assertNotIn("opened.forEach(([d])=>d.open=true)", paper)

    def test_document_answers_remain_visible_outside_the_appendix(self):
        task = self.fixture.material_task()
        done = self.fixture.run_spec(task, self.fixture.spec(task))
        content = compose(done)
        paper = render(content, ROOT)
        principal = paper.split("<details class='research-appendix'>", 1)[0]
        for item in content['evidence']:
            self.assertIn(html.escape(item['text'], quote=True), principal)

    def test_date_label_preserves_offset_and_precise_time(self):
        self.assertEqual(research_date_label('2026-07-20T18:00:00+08:00'), '2026年07月20日18:00UTC+08:00')
        self.assertEqual(research_date_label('2026-07-20T10:00:12.250000Z'), '2026年07月20日10:00:12.25UTC+00:00')
        self.assertEqual(research_date_label('2026-07-20'), '2026年07月20日')
        self.assertEqual(research_date_label('未核验'), '未核验')


if __name__ == '__main__':
    unittest.main()
