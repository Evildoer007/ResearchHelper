"""Independent evaluation-tool tests; synthetic originals, no production writes."""

import copy
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from tests.research_quality_evaluation import aggregate, evaluate, input_scope


ROOT = Path(__file__).resolve().parents[2]


class ResearchQualityEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="evaluation-", dir=ROOT / "result/acceptance")
        cls.blocker = patch.object(socket.socket, "connect", side_effect=AssertionError("External network forbidden"))
        cls.blocker.start()
        agent = ResearchAgent(ResearchArchive(cls.folder.name), ROOT, offline=True)
        task = agent.create_case("etf")
        cls.good = agent.start(task["id"])
        assert cls.good["status"] == "已完成"
        cls.saved = agent.archive.report(task["id"], cls.good["version"])

    @classmethod
    def tearDownClass(cls):
        cls.blocker.stop()
        cls.folder.cleanup()

    def setUp(self):
        self.task = copy.deepcopy(self.good)
        self.delivery = copy.deepcopy(self.saved)
        self.spec = {"id": "synthetic-fee-comparison", "split": "development",
                     "input_digest": fingerprint(input_scope(self.task)),
                     "expected_status": "已完成", "question_states": {
                         question["id"]: "已回答" for question in self.task["questions"] if question["required"]}}

    def evaluate(self):
        return evaluate(self.task, self.spec, saved_delivery=self.delivery)

    def test_saved_delivery_metadata_alone_is_not_save_proof(self):
        result = evaluate(self.task, self.spec)
        self.assertIn("saved_delivery_not_verified", result["critical_violations"])

    def test_altered_frozen_content_cannot_pass(self):
        self.delivery["content"]["question"] += "篡改"
        self.assertIn("saved_delivery_integrity_failed", self.evaluate()["critical_violations"])

    def test_rehashed_foreign_report_still_fails_identity_binding(self):
        self.delivery["content"]["task_id"] = "foreign-task"
        self.delivery["digest"] = fingerprint(self.delivery["content"])
        self.task["delivery"]["digest"] = self.delivery["digest"]
        self.assertIn("saved_delivery_integrity_failed", self.evaluate()["critical_violations"])

    def test_empty_saved_html_is_not_complete_delivery(self):
        self.delivery["html"] = ""
        self.assertIn("saved_delivery_integrity_failed", self.evaluate()["critical_violations"])

    def test_complete_fixture_functionally_passes_without_quality_claim(self):
        result = self.evaluate()
        self.assertEqual(result["functional_status"], "passed")
        self.assertFalse(result["independent_research_quality_verified"])
        self.assertFalse(result["live_end_to_end_research_verified"])

    def test_evaluation_preserves_task_and_expected_results(self):
        before, expected = fingerprint(self.task), fingerprint(self.spec)
        self.evaluate()
        self.assertEqual(fingerprint(self.task), before)
        self.assertEqual(fingerprint(self.spec), expected)

    def test_expected_results_are_not_part_of_input_scope(self):
        scope = input_scope(self.task)
        self.assertEqual(set(scope), {"question", "entry", "intent", "objects", "cutoff"})
        self.assertNotIn("expected_status", scope)
        self.assertNotIn("question_states", scope)

    def test_changed_input_rejects_old_case_binding(self):
        self.task["question"] += "改变研究目标"
        with self.assertRaises(ContractError):
            self.evaluate()

    def test_missing_question_expectation_is_rejected(self):
        self.spec["question_states"].clear()
        with self.assertRaises(ContractError):
            self.evaluate()

    def test_unregistered_expectation_field_is_rejected(self):
        self.spec["approved"] = True
        with self.assertRaises(ContractError):
            self.evaluate()

    def test_synthetic_fixture_cannot_claim_independent_split(self):
        self.spec["split"] = "holdout"
        with self.assertRaises(ContractError):
            self.evaluate()

    def test_empty_question_denominator_is_not_perfect_score(self):
        self.task["questions"] = []
        self.spec["question_states"] = {}
        result = self.evaluate()
        self.assertIsNone(result["question_completion_ratio"])

    def test_wrong_completion_is_critical_even_when_expected_as_complete(self):
        self.task["questions"][0]["status"] = "未回答"
        result = self.evaluate()
        self.assertEqual(result["functional_status"], "failed")
        self.assertIn("wrong_completion_status", result["critical_violations"])

    def test_no_saved_delivery_cannot_pass_as_completed(self):
        self.task["delivery"] = None
        self.assertIn("missing_saved_delivery", self.evaluate()["critical_violations"])

    def test_delivery_from_other_version_is_critical(self):
        self.task["delivery"]["version"] += 1
        self.assertIn("delivery_version_mismatch", self.evaluate()["critical_violations"])

    def test_forged_answer_reference_is_critical(self):
        self.task["questions"][0]["refs"] = ["foreign_evidence"]
        self.assertIn("content_contract_failed", self.evaluate()["critical_violations"])

    def test_budget_overrun_cannot_be_averaged_away(self):
        good = self.evaluate()
        self.task["ledger"]["used"]["model_calls"] = self.task["ledger"]["limits"]["model_calls"] + 1
        self.spec["id"] = "over-budget"
        bad = self.evaluate()
        summary = aggregate([good, bad])
        self.assertEqual(summary["functional_status"], "failed")
        self.assertEqual(summary["critical_failure_cases"], ["over-budget"])

    def test_missing_cost_is_unknown_not_zero(self):
        del self.task["ledger"]["used"]["seconds"]
        result = self.evaluate()
        self.assertIsNone(result["costs"]["seconds"])
        self.assertIsNone(aggregate([result])["recorded_costs"]["seconds"])

    def test_boolean_budget_is_not_a_request_count(self):
        self.task["ledger"]["used"]["data_requests"] = True
        self.assertIn("invalid_budget_data_requests", self.evaluate()["critical_violations"])

    def test_fractional_calls_are_not_valid_dispatch_counts(self):
        self.task["ledger"]["used"]["model_calls"] = 1.5
        self.assertIn("invalid_budget_model_calls", self.evaluate()["critical_violations"])

    def test_non_finite_record_has_no_valid_evaluation_hash(self):
        self.task["ledger"]["used"]["seconds"] = float("inf")
        with self.assertRaises(ContractError):
            self.evaluate()

    def test_structured_statement_count_is_not_all_important_claims(self):
        result = self.evaluate()
        self.assertGreater(result["structured_answer_and_thesis_count"], 0)
        self.assertIsNone(result["important_statement_count"])

    def test_report_semantics_are_not_inferred_from_valid_content(self):
        result = self.evaluate()
        self.assertTrue(result["validated_content_hash"])
        for field in ("semantic_citation_accuracy", "financial_reasoning_quality", "counterevidence_recall"):
            self.assertIsNone(result[field])

    def test_uncovered_entry_has_no_pass_rate(self):
        summary = aggregate([self.evaluate()])
        untested = [row for row in summary["coverage"] if row["case_count"] == 0]
        self.assertTrue(untested)
        self.assertTrue(all(row["functional_pass_rate"] is None for row in untested))
        self.assertIsNone(summary["research_quality_pass_rate"])

    def test_duplicate_case_cannot_inflate_sample_count(self):
        result = self.evaluate()
        with self.assertRaises(ContractError):
            aggregate([result, result])

    def test_repeated_run_is_not_an_extra_distinct_research_input(self):
        result = self.evaluate()
        second = dict(result, case_id="same-input-repeat")
        summary = aggregate([result, second])
        self.assertEqual(summary["case_count"], 2)
        self.assertEqual(summary["unique_input_count"], 1)

    def test_development_input_cannot_also_enter_holdout(self):
        result = self.evaluate()
        contaminated = dict(result, case_id="contaminated-holdout", split="holdout")
        with self.assertRaises(ContractError):
            aggregate([result, contaminated])


if __name__ == "__main__":
    unittest.main()
