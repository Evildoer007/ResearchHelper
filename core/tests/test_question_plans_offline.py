"""Independent question-led workflow tests, without models or data providers."""

import copy
import tempfile
import unittest
from pathlib import Path

from researchhelper.archive import ResearchArchive
from researchhelper.cases import CUTOFF
from tests.host_fixture_support import policy_proposal, fee_calculation, fee_originals
from researchhelper.contracts import ContractError, charge, module_result, request, validate_request, validate_result
from researchhelper.engine import ResearchAgent
from researchhelper.reports import compose

ROOT = Path(__file__).resolve().parents[2]


class QuestionPlanTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="question-plan-", dir=ROOT / "result" / "acceptance")
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        self.addCleanup(self.agent.close_runtime)

    def create(self, questions=None, **options):
        self.current_task = self.agent.create("核查原文日期", cutoff=CUTOFF, questions=questions or [
            {"id": "q1", "question": "原文何时生效", "kind": "fact"}], **options)
        return self.current_task

    def facts(self, answers=None, **extra):
        spec = policy_proposal(self.agent, self.current_task["id"], "original", "政策原文",
                               "生效时间为2026年7月19日。", ["q1"])
        return dict(evidence_specs=[spec], answers=answers or {
            "q1": {"answer": "原文于2026年7月19日生效。", "refs": ["original"]}}, **extra)

    def test_plain_fact_delivers_without_invented_thesis(self):
        task = self.create(entry="事件")
        final = self.agent.start(task["id"], self.facts())
        self.assertEqual(final["status"], "已完成", final.get("error"))
        self.assertEqual(final["theses"], [])
        self.assertNotIn("rh_thesis.build_thesis", [n["operation"] for n in final["nodes"]])
        report = self.agent.export(task["id"])
        self.assertIn("2026年7月19日生效", report["html"])
        self.assertNotIn("未生成投资论点", report["html"])
        self.assertEqual(report["content"]["questions"][0]["refs"], ["original"])

    def test_comparison_uses_registered_calculation_without_thesis(self):
        task = self.agent.create("比较年度费用", entry="ETF", intent="比较对象", cutoff=CUTOFF, questions=[
            {"question": "同口径费用差是多少", "kind": "comparison", "method_needs": ["fee_difference_v2"]}])
        spec = fee_calculation()
        host = {"evidence_specs": fee_originals(self.agent, task["id"]), "calculations": [spec], "answers": {
            "q1": {"answer": "A比B低0.40个百分点，不等于实际收益差。", "refs": ["e1", "e2"], "calculation_refs": ["fee_delta"]}}}
        final = self.agent.start(task["id"], host)
        self.assertEqual(final["status"], "已完成", final.get("error"))
        self.assertEqual(final["calculations"][0]["id"], "fee_delta")
        self.assertEqual(final["theses"], [])
        self.assertIn("#fee_delta", self.agent.export(task["id"])["html"])

    def test_optional_question_missing_does_not_change_completion(self):
        task = self.create([
            {"id": "q1", "question": "原文日期", "kind": "fact"},
            {"id": "extra", "question": "可选观察", "kind": "fact", "required": False}])
        final = self.agent.start(task["id"], self.facts())
        self.assertEqual(final["status"], "已完成", final.get("error"))
        self.assertEqual(final["gaps"], [])
        self.assertTrue(final["optional_findings"])
        self.assertIn("可选观察", self.agent.export(task["id"])["html"])

    def test_optional_missing_calculation_does_not_block_fact(self):
        task = self.create([
            {"id": "q1", "question": "原文日期", "kind": "fact", "group": "date"},
            {"id": "extra", "question": "可选费用比较", "kind": "comparison", "required": False, "group": "fees"}])
        spec = fee_calculation(question_ids=["extra"])
        final = self.agent.start(task["id"], self.facts(calculations=[spec]))
        self.assertEqual(final["status"], "已完成", final.get("error"))
        date_node = next(n for n in final["nodes"] if n["id"] == "answer")
        self.assertNotIn("calculation", date_node["dependencies"])
        self.assertEqual(final["gaps"], [])

    def test_independent_core_branch_preserves_valid_answer(self):
        task = self.create([
            {"id": "q1", "question": "政策日期", "kind": "fact", "group": "date"},
            {"id": "second", "question": "盈利是否改善", "kind": "mechanism", "group": "profit"}])
        final = self.agent.start(task["id"], self.facts())
        self.assertEqual(final["status"], "部分完成")
        self.assertEqual(final["questions"][0]["status"], "已回答")
        self.assertEqual(final["questions"][1]["status"], "未回答")
        self.assertIn("2026年7月19日生效", self.agent.export(task["id"])["html"])

    def test_mechanism_cannot_complete_with_only_fact_answer(self):
        task = self.create([{"question": "为何盈利变化", "kind": "mechanism"}])
        final = self.agent.start(task["id"], self.facts())
        self.assertEqual(final["status"], "部分完成")
        self.assertTrue(any("论点" in gap for gap in final["gaps"]))

    def test_thesis_does_not_inherit_unrelated_calculations(self):
        task = self.create()
        host = self.facts(calculations=[fee_calculation(), fee_calculation("other_delta")],
                          theses=[{"title": "原文日期已确认", "judgment": "支持", "support": ["original"],
                                   "counter": [], "investment_meaning": "仅核查日期", "limitation": "限定原文"}])
        host["evidence_specs"] += fee_originals(self.agent, task["id"])
        final = self.agent.start(task["id"], host)
        self.assertEqual(final["status"], "已完成", final.get("error"))
        self.assertEqual(len(final["calculations"]), 2)
        self.assertEqual(final["theses"][0]["calculation_refs"], [])

    def test_required_method_must_have_answer_calculation_reference(self):
        task = self.create([{"question": "年度费差", "kind": "comparison", "method_needs": ["fee_difference_v2"]}])
        host = self.facts(calculations=[fee_calculation()])
        host["evidence_specs"] += fee_originals(self.agent, task["id"])
        final = self.agent.start(task["id"], host)
        self.assertEqual(final["status"], "部分完成")
        self.assertEqual(final["questions"][0]["status"], "未回答")

    def test_question_dependencies_are_respected_not_inferred(self):
        task = self.create([
            {"id": "later", "question": "适用状态", "kind": "fact", "group": "state", "dependencies": ["q1"]},
            {"id": "q1", "question": "政策日期", "kind": "fact", "group": "date"}])
        host = self.facts(answers={"later": {"answer": "已生效", "refs": ["original"]}})
        final = self.agent.start(task["id"], host)
        self.assertEqual(final["status"], "部分完成")
        self.assertTrue(all(q["status"] == "未回答" for q in final["questions"]))
        node = next(n for n in final["nodes"] if "later" in n["questions"] and n["kind"] == "answer")
        self.assertTrue(any(dep.startswith("answer") for dep in node["dependencies"]))

    def test_plan_revision_preserves_ledger_scope_and_original_input(self):
        task = self.create()
        charge(task["ledger"], "data_requests", 3)
        self.agent.archive.save(task)
        prior = self.agent.archive.get(task["id"])
        old_request = request(prior, "rh_analyzer.analyze")
        specs = [{"id": "q1", "question": "原文何时生效", "kind": "fact", "evidence_needs": ["原文生效条款"]}]
        revised = self.agent.revise_plan(task["id"], specs, "补充原文条款定位", prior["input_hash"])
        self.assertEqual(revised["ledger"], prior["ledger"])
        self.assertEqual(revised["original_input"], prior["original_input"])
        self.assertEqual(revised["plan_version"], 2)
        self.assertNotEqual(revised["input_hash"], prior["input_hash"])
        with self.assertRaises(ContractError):
            validate_request(old_request, revised)

    def test_plan_cannot_weaken_or_erase_core_questions(self):
        task = self.create()
        bad = [
            [{"id": "q1", "question": "原文何时生效", "kind": "fact", "required": False}],
            [{"id": "other", "question": "替换问题", "kind": "fact"}],
            [{"id": "q1", "question": "改写问题", "kind": "fact"}],
            [{"id": "q1", "question": "原文何时生效", "kind": "comparison"}],
        ]
        for proposed in bad:
            with self.subTest(proposed=proposed), self.assertRaises(ContractError):
                self.agent.revise_plan(task["id"], proposed, "材料不足")

    def test_unknown_cycle_duplicate_and_unregistered_method_rejected(self):
        for questions in (
            [{"id": "q1", "question": "问题", "kind": "fact", "dependencies": ["missing"]}],
            [{"id": "q1", "question": "问题", "dependencies": ["q1"]}],
            [{"id": "q1", "question": "问题"}, {"id": "q1", "question": "另一问题"}],
            [{"question": "问题", "method_needs": ["generated_code"]}],
            [{"question": "问题", "required": 1}],
        ):
            with self.subTest(questions=questions), self.assertRaises(ContractError):
                self.create(questions)

    def test_frozen_plan_cannot_be_changed(self):
        task = self.create()
        final = self.agent.start(task["id"], self.facts())
        with self.assertRaises(ContractError):
            self.agent.revise_plan(task["id"], [{"question": "新问题", "kind": "fact"}], "新增范围")
        self.assertEqual(self.agent.export(task["id"])["digest"], final["delivery"]["digest"])

    def test_node_contract_excludes_unrelated_branches_and_rejects_omitted_dependency(self):
        task = self.create([
            {"id": "q1", "question": "日期", "kind": "fact", "group": "date"},
            {"id": "other", "question": "事件", "kind": "fact", "group": "event"}])
        for index, node in enumerate(task["nodes"]):
            if node["id"] in ("identity", "evidence", "answer"):
                node.update(status="成功", output_hash=f"hash_{index}")
        second = next(n for n in task["nodes"] if n["id"].startswith("answer_") and n["id"] != "answer")
        message = request(task, second["operation"], node_id=second["id"])
        self.assertEqual(set(message["dependencies"]), {"evidence"})
        validate_request(message, task)
        result = module_result(task, second["operation"], {}, node_id=second["id"])
        result["dependencies"] = {}
        with self.assertRaises(ContractError):
            validate_result(result, task)

    def test_report_revalidates_answer_references(self):
        task = self.agent.start(self.create()["id"], self.facts())
        changed = copy.deepcopy(task)
        changed["questions"][0]["refs"] = ["other_task_evidence"]
        with self.assertRaises(ContractError):
            compose(changed)

    def test_answer_cannot_borrow_another_questions_calculation(self):
        task = self.create([
            {"id": "q1", "question": "日期核查", "kind": "fact", "group": "date"},
            {"id": "fees", "question": "费用核查", "kind": "comparison", "group": "fees"}])
        host = self.facts(calculations=[fee_calculation("fee_only", ["fees"])])
        host["evidence_specs"] += fee_originals(self.agent, task["id"], ["fees"])
        host["answers"]["q1"].update(refs=["original", "e1", "e2"], calculation_refs=["fee_only"])
        final = self.agent.start(task["id"], host)
        self.assertEqual(final["questions"][0]["status"], "未回答")
        self.assertEqual(final["status"], "部分完成")

    def test_report_rejects_a_calculation_without_its_original_evidence(self):
        task = self.agent.start(self.agent.create_case("etf")["id"])
        changed = copy.deepcopy(task)
        changed["questions"][0].update(refs=["e1"], calculation_refs=[changed["calculations"][0]["id"]])
        with self.assertRaises(ContractError):
            compose(changed)

    def test_independent_mechanism_groups_do_not_duplicate_unbound_thesis(self):
        task = self.create([
            {"id": "q1", "question": "渠道一", "kind": "mechanism", "group": "channel_a"},
            {"id": "q2", "question": "渠道二", "kind": "mechanism", "group": "channel_b"}])
        host = self.facts(theses=[{"title": "仅一个渠道", "judgment": "支持", "support": ["original"],
                                  "investment_meaning": "限定渠道", "limitation": "不能证明另一渠道"}])
        final = self.agent.start(task["id"], host)
        self.assertEqual(final["status"], "失败")
        self.assertIn("对应问题", final["error"])
        self.assertEqual(final["theses"], [])

    def test_foreign_answer_binding_is_rejected(self):
        task = self.create()
        host = self.facts()
        host["answers"]["q1"]["task_id"] = "different_task"
        final = self.agent.start(task["id"], host)
        self.assertEqual(final["status"], "失败")
        self.assertIsNone(final["delivery"])

    def test_follow_up_context_retains_fact_answer_without_adopting_it(self):
        task = self.agent.start(self.create()["id"], self.facts())
        next_task = self.agent.follow_up(task["id"], "此后是否有修订")
        self.assertIn("2026年7月19日生效", next_task["messages"][1]["content"])
        self.assertEqual(next_task["evidence"], [])
        self.assertEqual(next_task["status"], "部分完成")

    def test_new_version_does_not_reuse_old_host_answers_as_new_time_facts(self):
        task = self.agent.start(self.create()["id"], self.facts())
        updated = self.agent.update(task["id"], cutoff="2026-07-21T18:00:00+08:00")
        self.assertEqual(updated["status"], "部分完成", updated.get("error"))
        self.assertEqual(updated["evidence"], [])
        self.assertNotIn("host_result", updated)
        self.assertEqual(self.agent.export(task["id"], 1)["content"]["questions"][0]["status"], "已回答")


if __name__ == "__main__":
    unittest.main()
