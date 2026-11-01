"""Assessment append invariants; no research, credentials or network."""

import copy
import unittest

from researchhelper.contracts import ContractError
from tests.review_latest_research_assessment import extend


class LatestResearchAssessmentTests(unittest.TestCase):
    def record(self, ident="old", digest="same-input", passed=False):
        return {"evaluation_version": 2, "case_id": ident, "input_digest": digest, "split": "development",
            "entry": "ETF", "intent": "综合研究", "functional_status": "passed" if passed else "failed",
            "critical_violations": [], "costs": {"data_requests": 0, "model_calls": 1, "seconds": 58},
            "saved_html_integrity": "not_delivered"}

    def test_failed_attempt_and_successful_recovery_are_both_retained(self):
        original = self.record()
        before = copy.deepcopy(original)
        result = extend([original], self.record("recovery", passed=True))
        self.assertEqual(original, before)
        self.assertEqual(result["case_count"], 2)
        self.assertEqual(result["unique_input_count"], 1)
        self.assertEqual(result["functional_status"], "failed")
        self.assertEqual([record["functional_status"] for record in result["records"]], ["failed", "passed"])

    def test_new_problem_is_a_distinct_input_not_an_independent_review(self):
        result = extend([self.record()], self.record("new", "different-input"))
        self.assertEqual(result["unique_input_count"], 2)
        self.assertEqual(result["independent_holdout_cases"], 0)
        self.assertFalse(result["independent_research_quality_verified"])
        self.assertEqual(result["quality_status"], "independent_review_missing")

    def test_all_forty_combinations_and_uncovered_groups_are_explicit(self):
        result = extend([self.record()], self.record("new"))
        self.assertEqual(len(result["entry_intent_coverage"]), 40)
        self.assertEqual(len(result["uncovered_entry_intent_combinations"]), 39)
        group = next(row for row in result["entry_intent_coverage"] if row["entry"] == "ETF" and row["intent"] == "综合研究")
        self.assertEqual(group["recorded_runs"], 2)
        self.assertEqual(group["distinct_inputs"], 1)
        self.assertEqual(group["functional_passed"], 0)

    def test_duplicate_case_does_not_silently_replace_prior_failure(self):
        with self.assertRaises(ContractError):
            extend([self.record()], self.record())

    def test_old_evaluator_version_cannot_be_relabelled(self):
        record = self.record("legacy")
        record["evaluation_version"] = 1
        with self.assertRaises(ContractError):
            extend([self.record()], record)


if __name__ == "__main__":
    unittest.main()
