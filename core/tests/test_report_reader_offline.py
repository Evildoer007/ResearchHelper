"""Report headings and the frozen original question are separate, lossless roles."""

import copy
import html
import unittest

from researchhelper.contracts import fingerprint
from researchhelper.reports import compose, render, report_heading, READER_VERSION
from tests import test_document_content_offline as documents


class ReportReaderTests(unittest.TestCase):
    def setUp(self):
        self.fixture = documents.DocumentContentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def complete(self, case='mechanism'):
        task = self.fixture.agent.create_case(case)
        return self.fixture.agent.start(task['id'])

    def test_long_question_uses_subject_label_not_a_claim(self):
        content = compose(self.complete())
        content.update(title='核对财政部税务总局2023年第39号公告的征税变化和生效日期。仅依据该公告，能否识别券商盈利与股价方向？',
                       question='完整提问不能因标题精简而删除。', objects=['证券交易印花税'], intent='综合研究', synthetic=False)
        content['content_hash'] = fingerprint({k: v for k, v in content.items() if k != 'content_hash'})
        before = copy.deepcopy(content)
        paper = render(content, documents.ROOT)
        self.assertIn('<h1>证券交易印花税研究报告</h1>', paper)
        for key in ('title', 'question'):
            self.assertIn(html.escape(content[key], quote=True), paper)
        for item in content['evidence']:
            self.assertIn(html.escape(item['text'], quote=True), paper)
        self.assertEqual(content, before)

    def test_short_fixture_titles_remain_for_all_eight_entries(self):
        for key in ('mechanism', 'etf', 'value', 'index', 'sector', 'event', 'macro', 'mixed'):
            with self.subTest(case=key):
                content = compose(self.complete(key))
                self.assertEqual(report_heading(content), content['title'])
                self.assertEqual(content['question'], self.fixture.agent.archive.get(content['task_id'])['question'])

    def test_long_multiple_subjects_never_silently_drop_a_subject(self):
        content = {'title': '长篇研究问题。' * 20, 'objects': ['很长的研究对象名称甲' * 4, '对象乙'], 'entry': '混合', 'intent': '比较对象'}
        self.assertEqual(report_heading(content), '综合研究报告')
        self.assertEqual(content['objects'][1], '对象乙')

    def test_short_comparison_label_preserves_all_declared_subjects(self):
        content = {'title': '比较两个ETF的同期间管理费与托管费，不推断费用就是收益。' * 3,
                   'objects': ['ETF-A', 'ETF-B'], 'entry': 'ETF', 'intent': '比较对象'}
        self.assertEqual(report_heading(content), 'ETF-A、ETF-B比较研究')

    def test_entry_fallbacks_do_not_infer_a_company_or_event(self):
        for entry in ('个股', 'ETF', '指数', '行业', '产业链', '事件', '宏观', '混合'):
            with self.subTest(entry=entry):
                heading = report_heading({'title': '尚未消歧的长篇提问。' * 20, 'objects': [], 'entry': entry})
                self.assertLessEqual(len(heading), 36)
                self.assertNotIn('尚未消歧', heading)
        self.assertEqual(report_heading({'title': '<script>unsafe</script>', 'objects': ['<script>unsafe</script>']}), '研究报告')

    def test_document_only_heading_remains_restricted(self):
        self.assertEqual(report_heading({'title': '某项投资机会。' * 40, 'objects': ['标的甲'],
                                        'intent': '原件内容核对'}), '原件内容核对')

    def test_old_frozen_export_is_unchanged_and_reader_has_separate_version(self):
        done = self.complete()
        frozen = self.fixture.agent.export(done['id'])
        content = copy.deepcopy(frozen['content'])
        content.pop('question')
        content['template_version'] = 'rh-paper-2'
        content['content_hash'] = fingerprint({k: v for k, v in content.items() if k != 'content_hash'})
        before = copy.deepcopy(content)
        paper = render(content, documents.ROOT)
        self.assertIn("data-reader-version='" + READER_VERSION + "'", paper)
        self.assertIn('模板rh-paper-2', paper)
        self.assertIn('留存报告题目', paper)
        self.assertEqual(content, before)
        self.assertEqual(self.fixture.agent.export(done['id']), frozen)

    def test_original_question_is_saved_even_when_fixture_has_a_short_title(self):
        done = self.complete('etf')
        content = compose(done)
        self.assertNotEqual(content['title'], content['question'])
        paper = render(content, documents.ROOT)
        self.assertIn('原始问题：' + html.escape(done['question'], quote=True), paper)
        self.assertIn("aria-label='报告目录'", paper)
        self.assertIn('navigateSection', paper)


if __name__ == '__main__':
    unittest.main()
