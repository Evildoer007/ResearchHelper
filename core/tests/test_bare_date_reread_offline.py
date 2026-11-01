"""Publication-cue rereading is eligibility, never automatic evidence approval."""

import copy
import unittest

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import candidate_evidence
from tests import test_extraction_reread_workflow_offline as fixtures


class BareDateRereadTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExtractionRereadWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent, self.task = self.fixture.agent, self.fixture.task
        self.spec = self.fixture.fixture.spec()
        self.spec["publication"]["basis_ref"]["quote"] = "2026年9月20日"
        self.task["host_result"]["evidence_specs"] = [self.spec]
        self.task["rejected_evidence"][0].update(
            rejection="原文未明确定位发布或披露日期，所属期与生效日不能替代",
            question_ids=["rates"])

    def repairs(self):
        return self.agent._supplement_extraction_repairs(self.task)

    def test_bare_date_with_independent_readable_publication_anchor_can_reread(self):
        self.assertEqual(len(self.repairs()), 1)
        self.assertEqual(self.repairs()[0]["question_ids"], ["rates"])

    def test_bare_date_remains_rejected_and_task_is_not_rewritten(self):
        before = fingerprint(self.task)
        self.assertFalse(self.repairs()[0]["source_approved"])
        self.assertFalse(self.repairs()[0]["automatic_reference_repair"])
        with self.assertRaisesRegex(ContractError, "原文未明确定位"):
            candidate_evidence(self.task, self.task["materials"][0], self.spec)
        self.assertEqual(fingerprint(self.task), before)
        self.assertEqual(self.agent.model.calls, [])

    def test_current_future_timestamp_cannot_open_repair(self):
        self.spec["published_at"] = "2026-09-22"
        self.assertEqual(self.repairs(), [])

    def test_missing_timestamp_cannot_open_repair(self):
        self.spec.pop("published_at")
        self.assertEqual(self.repairs(), [])

    def test_false_locator_diagnostic_cannot_hide_foreign_artifact(self):
        self.spec["publication"]["basis_ref"]["artifact_id"] = "foreign-original"
        self.assertEqual(self.repairs(), [])

    def test_false_locator_diagnostic_cannot_hide_hash_mismatch(self):
        self.spec["publication"]["basis_ref"]["snapshot_hash"] = "wrong-hash"
        self.assertEqual(self.repairs(), [])

    def test_stale_bare_date_rejection_does_not_reread_corrected_proposal(self):
        self.spec["publication"] = copy.deepcopy(self.fixture.good["publication"])
        self.assertEqual(self.repairs(), [])

    def test_other_declared_date_has_no_matching_alternative(self):
        self.spec["published_at"] = "2026-09-19"
        self.assertEqual(self.repairs(), [])

    def test_existing_same_pair_reread_is_not_reoffered(self):
        self.task["supplement_history"] = [{"proposal": self.fixture.proposal()}]
        self.assertEqual(self.repairs(), [])

    def test_no_valid_publication_candidate_does_not_open_repair(self):
        from unittest.mock import patch
        from researchhelper.reasoning import evidence_contract
        contract = evidence_contract(self.task)
        for source in contract["sources"]:
            source["publication_candidates"] = []
        with patch("researchhelper.engine.evidence_contract", return_value=contract):
            self.assertEqual(self.repairs(), [])


if __name__ == "__main__":
    unittest.main()
