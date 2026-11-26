"""Independent extraction gate diagnostics; no external calls or approval repair."""

import copy
import unittest

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.reasoning import record_extraction_validation
from tests import test_extraction_specification_rejection_offline as retained
from tests.test_extraction_reread_workflow_offline import ScriptedModel


class JournaledExtractionModel(ScriptedModel):
    def __init__(self, response, agent):
        super().__init__([("extract", response)])
        self.agent = agent

    def reason_phase(self, task, messages, phase):
        answer = super().reason_phase(task, messages, phase)
        task["ledger"]["dispatches"].append({"kind": "model_calls", "batch": task["batch"], "status": "成功",
                                             "elapsed_seconds": 1.0})
        return answer

    def journal(self, task):
        self.agent._journal(task)


class ExtractionDispatchDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        fixture = retained.ExtractionSpecificationRejectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture, self.agent, self.task = fixture, fixture.agent, fixture.task

    def run_extract(self, specs):
        self.agent.model = JournaledExtractionModel({"evidence_specs": specs}, self.agent)
        return self.agent._extract_model_sources(self.task)

    def diagnostic(self):
        return self.agent.archive.get(self.task["id"])["ledger"]["dispatches"][-1]

    def test_shape_rejection_is_persisted_on_successful_transport_without_refund(self):
        self.run_extract([self.fixture.invalid_spec()])
        saved = self.agent.archive.get(self.task["id"])
        event = self.diagnostic()
        self.assertEqual(event["status"], "成功")
        self.assertTrue(event["output_contract"]["shape_accepted"])
        validation = event["output_contract"]["extraction_validation"]
        self.assertFalse(validation["accepted"])
        self.assertEqual(validation["failure_code"], "calculation_context_shape")
        self.assertEqual(validation["task_id"], saved["id"])
        self.assertEqual(validation["research_version"], saved["version"])
        self.assertEqual(saved["ledger"]["used"]["model_calls"], 1)
        self.assertFalse(saved["evidence"])
        self.assertFalse(saved["reasoning_state"]["rejected_extraction"]["automatic_repair"])

    def test_self_signed_proposal_stays_hard_failure_with_persisted_diagnostic(self):
        spec = self.fixture.fixture.spec()
        spec["source_check"] = "通过"
        with self.assertRaisesRegex(ContractError, "自签"):
            self.run_extract([spec])
        validation = self.diagnostic()["output_contract"]["extraction_validation"]
        self.assertFalse(validation["accepted"])
        self.assertEqual(validation["failure_code"], "proposal_constraints_rejected")
        rejection = self.task["reasoning_state"]["rejected_extraction"]
        self.assertFalse(rejection["specification_accepted"])
        self.assertFalse(rejection["automatic_repair"])
        self.assertFalse(rejection["issue"]["evidence_approved"])
        self.assertEqual(rejection["proposed_specs"], [spec])
        self.assertNotIn("extraction", self.task["reasoning_state"])
        self.assertFalse(self.task["evidence"])

    def test_successful_proposal_is_not_approved_evidence(self):
        spec = self.fixture.fixture.spec()
        result = self.run_extract([spec])
        self.assertEqual(result["evidence_specs"], [spec])
        self.assertTrue(self.diagnostic()["output_contract"]["extraction_validation"]["accepted"])
        self.assertFalse(self.task["evidence"])

    def test_empty_proposal_is_structure_valid_not_fact_found(self):
        self.run_extract([])
        self.assertTrue(self.diagnostic()["output_contract"]["extraction_validation"]["accepted"])
        self.assertEqual(self.task["host_result"]["evidence_specs"], [])
        self.assertFalse(self.task["evidence"])

    def test_host_without_dispatch_does_not_gain_a_fabricated_call(self):
        self.agent.model = ScriptedModel([("extract", {"evidence_specs": []})])
        before = len(self.task["ledger"]["dispatches"])
        self.agent._extract_model_sources(self.task)
        self.assertEqual(len(self.task["ledger"]["dispatches"]), before)

    def test_receipt_cannot_stamp_another_task_version_or_dispatch(self):
        self.run_extract([])
        original = copy.deepcopy(self.task)
        receipt = self.task["reasoning_state"]["extraction"]["receipt"]
        before = len(self.task["ledger"]["dispatches"]) - 1
        for key in ("task_id", "research_version", "batch", "input_hash", "phase"):
            with self.subTest(key=key):
                altered = dict(receipt, **{key: "foreign"})
                record_extraction_validation(self.agent.model, self.task, before, altered, False, "rejected")
                self.assertEqual(fingerprint(self.task), fingerprint(original))
        record_extraction_validation(self.agent.model, self.task, before - 1, receipt, False, "rejected")
        self.assertEqual(self.task, original)

    def test_diagnostic_does_not_store_proposal_or_exception_text(self):
        self.run_extract([self.fixture.invalid_spec()])
        validation = self.diagnostic()["output_contract"]["extraction_validation"]
        self.assertEqual(set(validation), {"phase", "task_id", "research_version", "batch", "input_hash",
                                          "accepted", "scope", "failure_code"})

    def test_another_batch_or_invalid_diagnostic_cannot_rewrite_the_dispatch(self):
        self.run_extract([])
        receipt = self.task["reasoning_state"]["extraction"]["receipt"]
        before = len(self.task["ledger"]["dispatches"]) - 1
        for accepted, code in (("false", None), (False, "PRIVATE_REJECTION_TEXT")):
            original = copy.deepcopy(self.task)
            record_extraction_validation(self.agent.model, self.task, before, receipt, accepted, code)
            self.assertEqual(self.task, original)
        self.task["ledger"]["dispatches"][-1]["batch"] = "another-batch"
        original = copy.deepcopy(self.task)
        record_extraction_validation(self.agent.model, self.task, before, receipt, False, "calculation_context_shape")
        self.assertEqual(self.task, original)

    def test_real_model_service_journals_rejection_without_new_network_or_retry(self):
        from unittest.mock import patch
        from researchhelper.model import ModelService
        from tests.test_autonomous_reasoning_offline import AutonomousReasoningTests, FabricatedModel
        model_fixture = AutonomousReasoningTests()
        model_fixture.setUp()
        self.addCleanup(model_fixture.doCleanups)
        self.agent.model = ModelService(model_fixture.settings, offline=False, journal=self.agent._journal)
        fabricated = FabricatedModel([{"evidence_specs": [self.fixture.invalid_spec()]}])
        with patch("researchhelper.model.urllib.request.urlopen", side_effect=fabricated.respond):
            self.agent._extract_model_sources(self.task)
        event = self.diagnostic()
        self.assertEqual(event["status"], "成功")
        self.assertTrue(event["output_contract"]["shape_accepted"])
        self.assertFalse(event["output_contract"]["extraction_validation"]["accepted"])
        self.assertEqual(len(fabricated.requests), 1)
        self.assertEqual(self.task["ledger"]["used"]["model_calls"], 1)


if __name__ == "__main__":
    unittest.main()
