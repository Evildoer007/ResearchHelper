"""A4 paper with an external directory and complete, separately inspectable sources."""
import copy
import html
import unittest
from lxml import html as dom
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
        tree = dom.document_fromstring(render(content, ROOT))
        main = tree.find('.//main')
        self.assertFalse(main.xpath('.//nav|.//aside|.//button|.//details'))
        directory = tree.xpath('//*[@aria-label="报告目录"]')
        self.assertEqual(len(directory), 1)
        self.assertNotIn(directory[0], list(main.iter()))
        self.assertTrue(directory[0].xpath('.//a[starts-with(@href,"#")]'))
        self.assertEqual(len(tree.xpath('//dialog[@id="report-inspector"]')), 1)
        self.assertTrue(main.xpath('.//a[@href="#scope" and @aria-haspopup="dialog"]'))

    def test_complete_content_and_archived_export_are_not_rewritten(self):
        done, content = self.study('value')
        before = copy.deepcopy(content)
        frozen = copy.deepcopy(self.fixture.agent.export(done['id']))
        tree = dom.document_fromstring(render(content, ROOT))
        main = tree.find('.//main')
        for item in content['evidence']:
            record = tree.xpath('//*[@id=$ident]', ident='ev-' + item['id'])
            self.assertEqual(len(record), 1)
            self.assertIn(item['text'], record[0].text_content())
        for item in content['theses']:
            for key in ('title', 'investment_meaning', 'limitation'):
                self.assertIn(item[key], main.text_content())
        for item in content['calculations']:
            self.assertEqual(len(tree.xpath('//*[@id=$ident]', ident=item['id'])), 1)
        self.assertEqual(content, before)
        self.assertEqual(self.fixture.agent.export(done['id']), frozen)

    def test_evidence_navigation_unfolds_only_target_ancestors(self):
        _, content = self.study()
        tree = dom.document_fromstring(render(content, ROOT))
        main = tree.find('.//main')
        records = tree.get_element_by_id('report-records')
        self.assertIn('hidden', records.attrib)
        self.assertFalse(main.xpath('.//details'))
        for item in content['evidence']:
            target = tree.xpath('//*[@id=$ident]', ident='ev-' + item['id'])
            self.assertEqual(len(target), 1)
            self.assertTrue(target[0].xpath('ancestor::*[@id="report-records"]'))
        for link in main.xpath('.//a[starts-with(@href,"#ev-")]'):
            self.assertEqual(link.get('aria-haspopup'), 'dialog')
            self.assertEqual(len(records.xpath('.//*[@id=$ident]', ident=link.get('href')[1:])), 1)

    def test_document_answers_remain_visible_outside_the_appendix(self):
        task = self.fixture.material_task()
        done = self.fixture.run_spec(task, self.fixture.spec(task))
        content = compose(done)
        main = dom.document_fromstring(render(content, ROOT)).find('.//main')
        for item in content['evidence']:
            self.assertEqual(main.text_content().count(item['text']), 1)
        self.assertFalse(main.xpath('.//details'))

    def test_date_label_preserves_offset_and_precise_time(self):
        self.assertEqual(research_date_label('2026-07-20T18:00:00+08:00'), '2026年07月20日18:00UTC+08:00')
        self.assertEqual(research_date_label('2026-07-20T10:00:12.250000Z'), '2026年07月20日10:00:12.25UTC+00:00')
        self.assertEqual(research_date_label('2026-07-20'), '2026年07月20日')
        self.assertEqual(research_date_label('未核验'), '未核验')


if __name__ == '__main__':
    unittest.main()
