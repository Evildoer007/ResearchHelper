"""Presentation follows adopted questions and preserves frozen research data."""
import copy
import html
import unittest
from lxml import html as dom

from researchhelper.contracts import fingerprint
from researchhelper.reports import compose, render, report_heading, READER_VERSION
from tests import test_document_content_offline as documents
from tests import test_current_strategy_chart_regression_offline as strategy
from tests import test_report_reader_offline as reader


class ReportScopePresentationTests(unittest.TestCase):
    def setUp(self):
        fixture = documents.DocumentContentTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.agent = fixture.agent

    def content(self):
        task = self.agent.create_case('mechanism')
        content = compose(self.agent.start(task['id']))
        content.update(title='完整的价格变化研究问题。' * 8, question='完整问题保留。',
                       objects=['510300.SH'], intent='解释变化', theses=[], tracking=[],
                       limitations=[], optional_findings=[], reading_coverage=[],
                       tracking_reason='静态事实与比较仅在依据变化时主动复核，不制造收益目标。',
                       questions=[{'id': 'q1', 'kind': 'fact', 'required': True,
                                   'question': '区间未复权价格变化是多少？', 'status': '已回答',
                                   'answer': '只记录已观测事实，不解释原因。', 'refs': []}])
        return self.freeze(content)

    @staticmethod
    def freeze(content):
        content['content_hash'] = fingerprint({key: value for key, value in content.items()
                                               if key != 'content_hash'})
        return content

    def test_fact_scope_is_not_mislabelled_as_a_causal_analysis(self):
        content = self.content()
        self.assertEqual(report_heading(content), '510300.SH事实研究')
        self.assertIn('<h1>510300.SH事实研究</h1>', render(content, documents.ROOT))

    def test_comparison_scope_overrides_broad_intent(self):
        content = self.content()
        content['questions'][0]['kind'] = 'comparison'
        content['objects'] = ['ETF-A', 'ETF-B']
        self.assertEqual(report_heading(content), 'ETF-A、ETF-B比较研究')

    def test_mixed_and_investment_scope_never_inherit_mechanism_label(self):
        for kinds, expected in ((['fact', 'mechanism'], '研究报告'),
                                (['investment'], '投资研究')):
            with self.subTest(kinds=kinds):
                content = self.content()
                content['questions'] = [{'kind': kind, 'required': True} for kind in kinds]
                self.assertEqual(report_heading(content), '510300.SH' + expected)

    def test_optional_questions_do_not_relabel_required_fact_scope(self):
        content = self.content()
        content['questions'].append({'kind': 'mechanism', 'required': False})
        self.assertEqual(report_heading(content), '510300.SH事实研究')

    def test_short_explicit_titles_and_document_scope_remain_unchanged(self):
        content = self.content()
        content['title'] = '沪深300价格观察'
        self.assertEqual(report_heading(content), content['title'])
        content['intent'] = '原件内容核对'
        self.assertEqual(report_heading(content), '原件内容核对')

    def test_empty_risk_section_and_success_filler_are_not_generated(self):
        content = self.content()
        before = copy.deepcopy(content)
        tree = dom.document_fromstring(render(content, documents.ROOT))
        main = tree.find('.//main')
        scope = tree.get_element_by_id('scope')
        self.assertFalse(main.xpath('.//*[@id="risk"]'))
        self.assertNotIn('核心问题已按当前核验材料回答', main.text_content())
        self.assertNotIn(content['tracking_reason'], main.text_content())
        self.assertEqual(scope.text_content().count(content['tracking_reason']), 1)
        self.assertTrue(scope.xpath('ancestor::*[@id="report-records" and @hidden]'))
        self.assertTrue(main.xpath('.//a[@href="#scope" and @aria-haspopup="dialog"]'))
        self.assertFalse(main.xpath('.//details'))
        self.assertEqual(content, before)

    def test_real_limits_and_tracking_remain_visible_without_generic_filler(self):
        content = self.content()
        content['limitations'] = ['缺少公司行为原件，不能换算总回报。']
        content['optional_findings'] = ['尚未覆盖交易费用。']
        content['tracking'] = [{'indicator': '公开费率', 'source': '正式公告',
                                'window': '新公告发布后', 'condition': '费率变更则重新比较',
                                'benchmark': '原正式费率'}]
        content = self.freeze(content)
        before = copy.deepcopy(content)
        tree = dom.document_fromstring(render(content, documents.ROOT))
        risk = tree.find('.//main').xpath('.//*[@id="risk"]')
        self.assertEqual(len(risk), 1)
        for text in content['limitations'] + content['optional_findings']:
            self.assertIn(text, risk[0].text_content())
        for value in content['tracking'][0].values():
            self.assertIn(value, risk[0].text_content())
        self.assertNotIn('核心问题已按当前核验材料回答', risk[0].text_content())
        self.assertEqual(content, before)

    def test_reading_coverage_stays_accessible_without_an_empty_risk_section(self):
        content = self.content()
        content['reading_coverage'] = [{'artifact_id': 'original', 'name': '原公告',
                                        'read_characters': 200, 'total_characters': 400}]
        content = self.freeze(content)
        before = copy.deepcopy(content)
        tree = dom.document_fromstring(render(content, documents.ROOT))
        main = tree.find('.//main')
        scope = tree.get_element_by_id('scope')
        rows = scope.xpath('.//table/tbody/tr')
        self.assertTrue(any(row.xpath('./td/text()') == ['原公告', '200/400字'] for row in rows))
        self.assertIn('原件读取范围', scope.text_content())
        self.assertTrue(main.xpath('.//a[@href="#scope" and @aria-haspopup="dialog"]'))
        self.assertFalse(main.xpath('.//*[@id="risk"]'))
        self.assertEqual(content, before)

    def test_presentation_does_not_mutate_frozen_content_or_old_delivery(self):
        task = self.agent.create_case('mechanism')
        done = self.agent.start(task['id'])
        saved = self.agent.export(done['id'])
        before = copy.deepcopy(saved)
        content = self.content()
        frozen = copy.deepcopy(content)
        paper = render(content, documents.ROOT)
        self.assertIn(html.escape(content['question']), paper)
        self.assertIn(html.escape(content['title']), paper)
        self.assertEqual(content, frozen)
        self.assertEqual(self.agent.export(done['id']), before)


class CurrentScopeStrategyCharts(strategy.CurrentStrategyCharts):
    """Compatibility name; the canonical chart suite owns these assertions."""
    __test__ = False


class CurrentScopeReaderTests(reader.ReportReaderTests):
    def test_original_question_is_saved_even_when_fixture_has_a_short_title(self):
        super().test_original_question_is_saved_even_when_fixture_has_a_short_title()
