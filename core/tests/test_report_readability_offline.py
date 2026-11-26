"""Presentation changes preserve frozen facts, task scope and archived exports."""

import copy
import html
import unittest
from lxml import html as dom

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.reports import compose, render, READER_VERSION
from tests import test_document_content_offline as originals


class ReportReadabilityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = originals.DocumentContentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def document(self):
        task = self.fixture.material_task()
        final = self.fixture.run_spec(task, self.fixture.spec(task))
        self.assertEqual(final["status"], "已完成", final.get("error"))
        return final, compose(final)

    def render(self, content):
        return render(content, originals.ROOT)

    def test_document_title_is_short_and_full_question_remains(self):
        _, content = self.document()
        content["title"] = "这是用户声明的完整研究问题，核对文件内容，不确认历史可得性。" * 5
        content["content_hash"] = fingerprint({key: value for key, value in content.items() if key != "content_hash"})
        paper = self.render(content)
        self.assertIn("<h1>原件内容核对</h1>", paper)
        self.assertIn(html.escape(content["title"], quote=True), paper)

    def test_exact_document_quotation_appears_once_and_numbered_ref_targets_it(self):
        _, content = self.document()
        tree = dom.document_fromstring(self.render(content))
        main = tree.find('.//main')
        item = content['evidence'][0]
        quotes = main.xpath('.//p[contains(concat(" ",normalize-space(@class)," ")," document-content-quote ")]')
        self.assertEqual(sum(node.text_content() == item['text'] for node in quotes), 1)
        links = main.xpath('.//a[@href="#ev-clause"]')
        self.assertEqual([node.text_content() for node in links], ['证据1'])
        self.assertEqual(len(tree.xpath('//*[@id="ev-clause"]')), 1)
        self.assertTrue(main.xpath('.//*[@id="ev-clause"]'))
        self.assertFalse(main.xpath('.//a[text()="证据clause"]'))

    def test_renderer_never_mutates_frozen_content(self):
        _, content = self.document()
        before = copy.deepcopy(content)
        self.render(content)
        self.assertEqual(content, before)

    def test_historical_frozen_template_rerenders_without_changing_version_or_hash(self):
        _, content = self.document()
        content['template_version'] = 'rh-paper-2'
        content['content_hash'] = fingerprint({key: value for key, value in content.items() if key != 'content_hash'})
        prior = fingerprint(content)
        tree = dom.document_fromstring(self.render(content))
        self.assertEqual(tree.get('data-renderer-version'), 'rh-paper-2')
        self.assertEqual(tree.get('data-reader-version'), READER_VERSION)
        self.assertIn('模板rh-paper-2', tree.get_element_by_id('scope').text_content())
        self.assertEqual(fingerprint(content), prior)

    def test_legacy_distinct_answer_is_retained_not_silently_elided(self):
        _, content = self.document()
        content['questions'][0]['answer'] = '历史保存的独立回答，不能按原文重复处理。'
        content['content_hash'] = fingerprint({key: value for key, value in content.items() if key != 'content_hash'})
        before = copy.deepcopy(content)
        main = dom.document_fromstring(self.render(content)).find('.//main')
        self.assertEqual(main.text_content().count(content['questions'][0]['answer']), 1)
        self.assertEqual(main.text_content().count(content['evidence'][0]['text']), 1)
        self.assertEqual(content, before)

    def test_document_report_has_continuous_chapters_and_no_empty_calculation_section(self):
        _, content = self.document()
        tree = dom.document_fromstring(self.render(content))
        main = tree.find('.//main')
        self.assertFalse(tree.xpath('//*[@id="methods"]'))
        self.assertFalse(main.xpath('.//*[@id="risk"]'))
        links = tree.xpath('//*[@aria-label="报告目录"]//a')
        self.assertTrue(links)
        self.assertEqual([link.xpath('./span/text()')[0] for link in links],
                         [str(index) + '.' for index in range(1, len(links) + 1)])
        for link in links:
            self.assertEqual(len(main.xpath('.//*[@id=$ident]', ident=link.get('href')[1:])), 1)
        self.assertIn('完整问题与知识依据', tree.get_element_by_id('scope').text_content())

    def test_normal_research_retains_title_methods_judgment_counter_and_tracking(self):
        task = self.fixture.agent.create_case('mechanism')
        done = self.fixture.agent.start(task['id'])
        content = compose(done)
        tree = dom.document_fromstring(self.render(content))
        main = tree.find('.//main')
        self.assertEqual(main.xpath('.//h1/text()'), [content['title']])
        self.assertEqual(bool(tree.xpath('//*[@id="methods"]')), bool(content['calculations']))
        for thesis in content['theses']:
            for key in ('judgment', 'investment_meaning', 'limitation'):
                self.assertIn(thesis[key], main.text_content())
        for track in content['tracking']:
            self.assertIn(track['condition'], main.text_content())
        for evidence in content['evidence']:
            record = tree.xpath('//*[@id=$ident]', ident='ev-' + evidence['id'])
            self.assertEqual(len(record), 1)
            self.assertIn(evidence['text'], record[0].text_content())
        self.assertIn('完整问题与知识依据', tree.get_element_by_id('scope').text_content())

    def test_methods_math_and_scenario_chart_remain_in_investment_report(self):
        task = self.fixture.agent.create_case("value")
        done = self.fixture.agent.start(task["id"])
        paper = self.render(compose(done))
        self.assertIn("<math ", paper)
        self.assertIn("echarts.init", paper)
        for item in done["calculations"]:
            self.assertIn(f"{item['output']['value']:.4f}", paper)

    def test_missing_answers_and_gaps_remain_visible(self):
        task = self.fixture.material_task()
        done = self.fixture.agent.start(task["id"], {"evidence_specs": []})
        self.assertEqual(done["status"], "部分完成")
        content = compose(done)
        paper = self.render(content)
        self.assertIn("未回答", paper)
        for gap in content["limitations"]:
            self.assertIn(html.escape(gap, quote=True), paper)

    def test_old_saved_export_is_not_replaced_by_new_presentation(self):
        final, content = self.document()
        old = self.fixture.agent.export(final["id"])["html"]
        self.render(content)
        self.assertEqual(self.fixture.agent.export(final["id"])["html"], old)

    def test_content_tampering_is_still_rejected(self):
        _, content = self.document()
        content["evidence"][0]["text"] += "篡改"
        with self.assertRaises(ContractError):
            self.render(content)

    def test_full_question_and_original_html_are_escaped(self):
        _, content = self.document()
        content['title'] = "<script>alert('unsafe')</script>"
        content['content_hash'] = fingerprint({key: value for key, value in content.items() if key != 'content_hash'})
        before = copy.deepcopy(content)
        tree = dom.document_fromstring(self.render(content))
        scope = tree.get_element_by_id('scope')
        self.assertTrue(scope.xpath('.//p[text()=$title]', title='留存报告题目：' + content['title']))
        self.assertFalse(tree.xpath('//script[contains(text(),"alert(")]'))
        self.assertFalse(scope.xpath('.//script'))
        self.assertEqual(content, before)


if __name__ == "__main__":
    unittest.main()
