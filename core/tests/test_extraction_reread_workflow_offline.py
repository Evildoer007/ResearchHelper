"""Bounded same-original rereading; synthetic language responses, no network."""

import copy
import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, charge, fingerprint
from researchhelper.reasoning import question_specs, validate_supplement
from tests import test_extraction_binding_guidance_offline as fixtures


class ScriptedModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def reason_phase(self, task, messages, phase):
        if not self.responses:
            raise AssertionError("Unexpected extra model call: " + phase)
        expected, response = self.responses.pop(0)
        if phase != expected:
            raise AssertionError("Expected " + expected + ", got " + phase)
        charge(task["ledger"], "model_calls")
        self.calls.append((phase, json.loads(messages[1]["content"])))
        return json.dumps(response, ensure_ascii=False)


class ExtractionRereadWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExtractionBindingGuidanceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent, self.task = self.fixture.agent, self.fixture.task
        self.agent.offline = False
        self.agent.model = ScriptedModel([])
        self.good = self.fixture.spec()
        self.bad = self.fixture.spec(title=False, broad=True)
        self.task["host_result"] = {"evidence_specs": [copy.deepcopy(self.bad)]}
        self.task["rejected_evidence"] = [{"id": self.bad["id"], "artifact_id": self.bad["artifact_id"],
            "question_ids": self.bad["question_ids"], "source_check": "不通过", "applicability_check": "待核验",
            "rejection": "公开时间未对应所引发布语句；须准确定位该原件发布日期，不能借报告期或生效日"}]
        self.task["questions"][0]["status"] = "未回答"
        self.task["questions"][1]["status"] = "未回答"

    def proposal(self):
        return {"question_ids": ["rates"], "reason": "重读已有原件标题的发布依据并核对问题绑定",
            "expected_change": "已有正文的利率值可能取得合规的发布定位，不预设核验通过",
            "data_requests": [], "calculations": [], "artifact_ids": [self.bad["artifact_id"]], "stop_reason": ""}

    def repairs(self):
        return self.agent._supplement_extraction_repairs(self.task)

    def test_valid_alternative_anchor_opens_readonly_reread_option(self):
        before = fingerprint(self.task)
        repairs = self.repairs()
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0]["artifact_id"], self.bad["artifact_id"])
        self.assertIn(self.bad["artifact_id"], self.agent._supplement_options(self.task)["artifact_ids"])
        self.assertEqual(fingerprint(self.task), before)
        self.assertFalse(self.agent.model.calls)

    def test_reread_option_is_not_evidence_approval_or_reference_repair(self):
        before = copy.deepcopy(self.task["host_result"])
        repairs = self.repairs()
        self.assertFalse(repairs[0]["source_approved"])
        self.assertFalse(repairs[0]["automatic_reference_repair"])
        self.assertEqual(self.task["host_result"], before)
        self.assertFalse(self.task["evidence"])

    def test_matching_question_review_failure_can_open_narrow_reread(self):
        self.task["host_result"]["evidence_specs"] = [self.fixture.spec(broad=True)]
        self.task["rejected_evidence"][0].update(source_check="通过", applicability_check="不通过",
            rejection="材料适用性必须绑定本任务的实际研究问题")
        self.assertEqual(len(self.repairs()), 1)

    def test_unknown_publication_time_does_not_open_reread(self):
        self.task["host_result"]["evidence_specs"][0]["published_at"] = None
        self.assertEqual(self.repairs(), [])

    def test_future_release_does_not_open_reread(self):
        spec = self.task["host_result"]["evidence_specs"][0]
        spec["published_at"] = "2027-09-20"
        spec["publication"]["at"] = spec["published_at"]
        self.assertEqual(self.repairs(), [])

    def test_binding_error_does_not_hide_future_publication(self):
        spec = self.fixture.spec(broad=True)
        spec["published_at"] = "2027-09-20"
        self.task["host_result"]["evidence_specs"] = [spec]
        self.task["rejected_evidence"][0].update(source_check="通过",
            rejection="材料适用性必须绑定本任务的实际研究问题")
        self.assertEqual(self.repairs(), [])

    def test_stale_rejection_does_not_reread_already_corrected_anchor(self):
        self.task["host_result"]["evidence_specs"] = [self.good]
        self.assertEqual(self.repairs(), [])

    def test_already_matching_question_sets_do_not_reread(self):
        self.task["host_result"]["evidence_specs"] = [self.good]
        self.task["rejected_evidence"][0].update(source_check="通过",
            rejection="材料适用性必须绑定本任务的实际研究问题")
        self.assertEqual(self.repairs(), [])

    def test_missing_alternative_anchor_does_not_open_reread(self):
        self.task["materials"] = []
        self.assertEqual(self.repairs(), [])

    def test_source_integrity_failure_does_not_open_reread(self):
        self.task["materials"][0]["snapshot"]["parts"][0]["text"] += "tampered"
        self.assertEqual(self.repairs(), [])

    def test_wrong_raw_number_does_not_trigger_metadata_reread(self):
        self.task["rejected_evidence"][0]["rejection"] = "原始数值不能从原件摘录核对"
        self.assertEqual(self.repairs(), [])

    def test_transport_truncation_is_not_an_extraction_reread_action(self):
        self.task["rejected_evidence"] = []
        self.task["ledger"]["dispatches"].append({"kind": "model_calls", "status": "失败", "failure_code": "content_truncated"})
        self.task.update(status="运行中", run_mode="model")
        self.agent.archive.save(self.task)
        before = copy.deepcopy(self.task["ledger"])
        self.assertEqual(self.repairs(), [])
        self.assertFalse(self.agent._maybe_supplement(self.task["id"], self.task["batch"]))
        self.assertFalse(self.agent.model.calls)
        self.assertEqual(self.agent.archive.get(self.task["id"])["ledger"], before)

    def test_foreign_original_is_not_a_reread_candidate(self):
        self.task["materials"][0]["task_id"] = "rh_foreign_task"
        self.assertEqual(self.repairs(), [])

    def test_unanswered_optional_question_does_not_open_reread(self):
        self.task["questions"][0]["required"] = False
        self.assertEqual(self.repairs(), [])

    def test_attempted_same_problem_reread_not_reoffered(self):
        self.task["supplement_history"] = [{"proposal": self.proposal(), "status": "已完成"}]
        self.assertEqual(self.repairs(), [])
        with self.assertRaisesRegex(ContractError, "没有新的重读增量"):
            validate_supplement(self.task, self.proposal())

    def test_no_model_or_supplier_dispatch_when_decision_budget_insufficient(self):
        self.task.update(status="运行中", run_mode="model")
        self.task["ledger"]["used"]["model_calls"] = self.task["ledger"]["limits"]["model_calls"] - 2
        self.agent.archive.save(self.task)
        before = copy.deepcopy(self.task["ledger"])
        with patch.object(self.agent.collector, "external") as supplier:
            self.assertFalse(self.agent._maybe_supplement(self.task["id"], self.task["batch"]))
        supplier.assert_not_called()
        self.assertFalse(self.agent.model.calls)
        self.assertEqual(self.agent.archive.get(self.task["id"])["ledger"], before)

    def test_two_round_limit_blocks_new_decision_without_spending(self):
        self.task.update(status="运行中", run_mode="model")
        self.task["ledger"]["supplements"] = 2
        self.agent.archive.save(self.task)
        before = copy.deepcopy(self.task["ledger"])
        self.assertFalse(self.agent._maybe_supplement(self.task["id"], self.task["batch"]))
        self.assertFalse(self.agent.model.calls)
        self.assertEqual(self.agent.archive.get(self.task["id"])["ledger"], before)

    def test_cancelled_task_cannot_dispatch_repair(self):
        self.task.update(status="已取消", run_mode="model")
        self.agent.archive.save(self.task)
        with self.assertRaises(ContractError):
            self.agent._maybe_supplement(self.task["id"], self.task["batch"])
        self.assertFalse(self.agent.model.calls)

    def test_reread_uses_existing_round_count_and_never_resets_ledger(self):
        self.task.update(status="运行中", run_mode="model")
        self.task["ledger"]["used"]["model_calls"] = 2
        self.task["ledger"]["used"]["data_requests"] = 1
        self.agent.archive.save(self.task)
        self.agent.model = ScriptedModel([("supplement", self.proposal())])
        with patch.object(self.agent.collector, "external") as supplier:
            self.assertTrue(self.agent._maybe_supplement(self.task["id"], self.task["batch"]))
        supplier.assert_not_called()
        saved = self.agent.archive.get(self.task["id"])
        self.assertEqual(saved["ledger"]["used"]["model_calls"], 3)
        self.assertEqual(saved["ledger"]["used"]["data_requests"], 1)
        self.assertEqual(saved["ledger"]["supplements"], 1)
        self.assertEqual(saved["ledger"]["limits"], self.task["ledger"]["limits"])
        self.assertEqual(saved["ledger"]["reserve"], self.task["ledger"]["reserve"])

    def test_same_original_checkpoint_is_not_reused_across_supplement_round(self):
        self.task.update(status="运行中", run_mode="model")
        self.agent.archive.save(self.task)
        self.agent.model = ScriptedModel([("extract", {"evidence_specs": [self.bad]}),
                                          ("extract", {"evidence_specs": [self.good]})])
        first = self.agent._extract_model_sources(self.task)
        self.assertEqual(first["evidence_specs"], [self.bad])
        before = self.task["reasoning_state"]["extraction"]["input_digest"]
        self.assertTrue(self.agent._register_supplement(self.task, self.proposal()))
        self.agent.archive.save(self.task)
        self.agent._extract_model_sources(self.task)
        self.assertEqual([phase for phase, _ in self.agent.model.calls], ["extract", "extract"])
        self.assertNotEqual(self.task["reasoning_state"]["extraction"]["input_digest"], before)
        active = self.agent._active_supplement(self.task)
        self.assertEqual(active["evidence_specs"], [self.good])
        self.assertIn("review_feedback", self.agent.model.calls[-1][1])
        self.assertEqual(self.agent.model.calls[-1][1]["review_feedback"][0]["id"], self.bad["id"])

    def test_completed_extract_checkpoint_resumes_same_round_without_new_call(self):
        self.task.update(status="运行中", run_mode="model")
        self.agent.archive.save(self.task)
        self.agent._register_supplement(self.task, self.proposal())
        self.agent.archive.save(self.task)
        self.agent.model = ScriptedModel([("extract", {"evidence_specs": [self.good]})])
        self.agent._extract_model_sources(self.task)
        before = copy.deepcopy(self.task["ledger"])
        self.agent._extract_model_sources(self.task)
        self.assertEqual(len(self.agent.model.calls), 1)
        self.assertEqual(self.task["ledger"], before)

    def test_full_model_workflow_can_recover_fact_without_new_source_or_forced_mechanism(self):
        initial = self.agent.archive.get(self.task["id"])
        self.agent.model = ScriptedModel([
            ("plan", {"questions": question_specs(initial), "data_requests": [], "calculations": []}),
            ("extract", {"evidence_specs": [self.bad]}),
            ("supplement", self.proposal()),
            ("extract", {"evidence_specs": [self.good]}),
            ("analyze", {"answers": {"rates": {"answer": "样例利率为2.25%。", "refs": ["ev_rate"], "calculation_refs": []}}, "theses": []})])
        with patch.object(self.agent.collector, "external", side_effect=AssertionError("No supplier allowed")):
            final = self.agent.start(initial["id"])
        self.assertIsNone(final.get("error"))
        self.assertIsNotNone(final.get("delivery"))
        self.assertEqual(final["ledger"]["used"]["model_calls"], 5)
        self.assertEqual(final["ledger"]["used"]["data_requests"], 0)
        self.assertEqual(final["ledger"]["supplements"], 1)
        questions = {question["id"]: question for question in final["questions"]}
        self.assertEqual(questions["rates"]["status"], "已回答")
        self.assertEqual(questions["margin"]["status"], "未回答")
        self.assertEqual(final["status"], "部分完成")
        self.assertEqual(len(final["evidence"]), 1)
        self.assertTrue(final["rejected_evidence"])
        self.assertFalse(final["theses"])


if __name__ == "__main__":
    unittest.main()
