"""Presentation changes preserve frozen facts, task scope and archived exports."""

import copy
import html
import unittest

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.reports import compose, render
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
        paper = self.render(content)
        item = content["evidence"][0]
        self.assertEqual(paper.count(html.escape(item["text"], quote=True)), 1)
        self.assertIn("href='#ev-clause'>证据1</a>", paper)
        self.assertIn("id='ev-clause'", paper)
        self.assertNotIn(">证据clause</a>", paper)

    def test_renderer_never_mutates_frozen_content(self):
        _, content = self.document()
        before = copy.deepcopy(content)
        self.render(content)
        self.assertEqual(content, before)

    def test_historical_frozen_template_rerenders_without_changing_version_or_hash(self):
        _, content = self.document()
        content["template_version"] = "rh-paper-2"
        content["content_hash"] = fingerprint({key: value for key, value in content.items() if key != "content_hash"})
        prior = fingerprint(content)
        paper = self.render(content)
        self.assertIn("data-renderer-version='rh-paper-3'", paper)
        self.assertIn("模板rh-paper-2", paper)
        self.assertEqual(fingerprint(content), prior)

    def test_legacy_distinct_answer_is_retained_not_silently_elided(self):
        _, content = self.document()
        content["questions"][0]["answer"] = "历史保存的独立回答，不能按原文重复处理。"
        content["content_hash"] = fingerprint({key: value for key, value in content.items() if key != "content_hash"})
        paper = self.render(content)
        self.assertEqual(paper.count(content["questions"][0]["answer"]), 1)
        self.assertIn(content["evidence"][0]["text"], paper)

    def test_document_report_has_continuous_chapters_and_no_empty_calculation_section(self):
        _, content = self.document()
        paper = self.render(content)
        self.assertNotIn("id='methods'", paper)
        self.assertIn("4.限制与验证条件", paper)
        self.assertIn("5.研究范围与知识依据", paper)
        self.assertNotIn("6.研究范围", paper)

    def test_normal_research_retains_title_methods_judgment_counter_and_tracking(self):
        task = self.fixture.agent.create_case("mechanism")
        done = self.fixture.agent.start(task["id"])
        content = compose(done)
        paper = self.render(content)
        self.assertIn("<h1>" + html.escape(content["title"], quote=True) + "</h1>", paper)
        self.assertIn("id='methods'", paper)
        self.assertIn("6.研究范围与知识依据", paper)
        for thesis in content["theses"]:
            for key in ("judgment", "investment_meaning", "limitation"):
                self.assertIn(html.escape(thesis[key], quote=True), paper)
        for track in content["tracking"]:
            self.assertIn(html.escape(track["condition"], quote=True), paper)

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
        content["title"] = "<script>alert('unsafe')</script>"
        content["content_hash"] = fingerprint({key: value for key, value in content.items() if key != "content_hash"})
        paper = self.render(content)
        self.assertNotIn("<script>alert", paper)
        self.assertIn(html.escape(content["title"], quote=True), paper)


if __name__ == "__main__":
    unittest.main()
