"""Supplement admission is budget-aware; all transports stay fabricated."""
import copy
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, METHOD_REGISTRY, budget
from researchhelper.reasoning import validate_supplement
from tests import test_source_supplement_eligibility_offline as source_fixture
from tests import test_source_catalog_bridge_offline as catalog_fixture
from tests import test_autonomous_supplement_contracts_offline as contract_fixture
from tests import test_method_dependency_workflow_offline as method_fixture


class SupplementBudgetAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = source_fixture.SourceSupplementEligibilityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent, self.task = self.fixture.agent, self.fixture.task

    def exhaust_data(self):
        self.task["ledger"]["used"]["data_requests"] = self.task["ledger"]["limits"]["data_requests"]

    def test_exhausted_data_hides_external_actions_without_mutating_ledger(self):
        self.exhaust_data()
        before = copy.deepcopy(self.task)
        options = self.agent._supplement_options(self.task)
        self.assertEqual(options["sources"], [])
        self.assertEqual(options["source_question_ids"], {})
        self.assertEqual(options["filing_candidates"], [])
        self.assertEqual(self.task, before)

    def test_source_only_exhaustion_stops_before_model_decision(self):
        self.exhaust_data()
        self.fixture.activate()
        before = copy.deepcopy(self.task["ledger"])
        with patch("researchhelper.engine.model_object") as model, patch.object(self.agent.collector, "external") as vendor:
            self.assertFalse(self.agent._maybe_supplement(self.task["id"], self.task["batch"]))
        model.assert_not_called()
        vendor.assert_not_called()
        self.assertEqual(self.agent.archive.get(self.task["id"])["ledger"], before)

    def test_local_registered_calculation_remains_available(self):
        self.exhaust_data()
        self.task["questions"][0]["method_needs"] = ["gross_margin_v1"]
        self.task["evidence"] = [{"unit": unit, "value": 100, "question_ids": ["q1"]}
                                 for unit in METHOD_REGISTRY["gross_margin_v1"]["input_units"].values()]
        self.assertIn("gross_margin_v1", self.agent._supplement_options(self.task)["methods"])

    def test_local_material_remains_readable_with_exhausted_data(self):
        current = self.agent.add_material(self.task["id"], "local.txt", b"Retained original body.")
        self.task.update(current)
        self.exhaust_data()
        self.task["evidence"] = [{"unit": "元", "value": 100, "question_ids": ["q1"]}]
        ident = self.task["materials"][-1]["id"]
        self.assertEqual(self.agent._supplement_options(self.task)["artifact_ids"], [ident])
        proposal = dict(self.fixture.stop(), artifact_ids=[ident], reason="核对未读条件",
                        expected_change="原文可能补充条件", stop_reason="")
        self.assertEqual(validate_supplement(self.task, proposal), proposal)

    def test_local_calculation_can_save_report_at_exhausted_data_budget(self):
        fixture = method_fixture.MethodDependencyWorkflowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        task, host, expected = fixture.prepare("gross_margin_v1")
        retained = fixture.archive.get(task["id"])
        retained["ledger"]["used"]["data_requests"] = retained["ledger"]["limits"]["data_requests"]
        fixture.archive.save(retained)
        result = fixture.agent.start(task["id"], host)
        self.assertEqual(result["status"], "已完成", result.get("error"))
        self.assertAlmostEqual(result["calculations"][0]["output"]["value"], expected)
        self.assertEqual(result["ledger"]["used"]["data_requests"], retained["ledger"]["used"]["data_requests"])
        self.assertEqual(result["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(fixture.archive.report(result["id"], result["version"])["content"]["calculations"], result["calculations"])

    def test_diagnostic_scope_alone_does_not_trigger_a_model_call(self):
        self.fixture.activate()
        options = {"artifact_ids": [], "methods": [], "sources": [], "filing_candidates": [],
                   "source_question_ids": {"ifind": ["q1"]}}
        with patch.object(self.agent, "_supplement_options", return_value=options), patch("researchhelper.engine.model_object") as model:
            self.assertFalse(self.agent._maybe_supplement(self.task["id"], self.task["batch"]))
        model.assert_not_called()


class SupplementRequestAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = contract_fixture.SupplementContractsTests()
        self.fixture.setUp()
        self.task = self.fixture.task
        self.task["ledger"] = budget({"data_requests": 1})
        self.proposal = copy.deepcopy(self.fixture.proposal)

    def test_new_request_count_over_remaining_rejected_without_partial_mutation(self):
        second = copy.deepcopy(self.proposal["data_requests"][0])
        second["id"] = "other_day"
        second["parameters"]["startdate"] = "2026-09-28"
        self.proposal["data_requests"].append(second)
        before = copy.deepcopy(self.task)
        with self.assertRaisesRegex(ContractError, "取数.*额度"):
            validate_supplement(self.task, self.proposal)
        self.assertEqual(self.task, before)

    def test_exact_remaining_request_count_is_accepted(self):
        self.assertEqual(validate_supplement(self.task, self.proposal), self.proposal)

    def test_zero_remaining_rejects_host_source_proposal(self):
        self.task["ledger"]["used"]["data_requests"] = 1
        with self.assertRaisesRegex(ContractError, "取数.*额度"):
            validate_supplement(self.task, self.proposal)

    def test_explicit_stop_remains_valid_at_zero_remaining(self):
        self.task["ledger"]["used"]["data_requests"] = 1
        stop = dict(self.proposal, data_requests=[], stop_reason="预算用尽")
        self.assertEqual(validate_supplement(self.task, stop), stop)


class SupplementMetadataAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = catalog_fixture.SourceCatalogBridgeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent, self.task = self.fixture.agent, self.fixture.task
        self.record = self.fixture.catalog()
        self.task["materials"] = []
        self.task["evidence"] = [{"unit": "元", "value": 1, "question_ids": ["q1"]}]

    def test_directory_not_exposed_as_body_reread(self):
        self.assertEqual(self.agent._supplement_options(self.task)["artifact_ids"], [])

    def test_host_cannot_register_directory_body_reread(self):
        proposal = dict(self.fixture.directory_proposal(), data_requests=[], artifact_ids=[self.record["id"]])
        with self.assertRaisesRegex(ContractError, "正文"):
            validate_supplement(self.task, proposal)

    def test_identity_metadata_is_not_a_body_reread(self):
        self.record["operation"] = "get_thscode"
        self.assertEqual(self.agent._supplement_options(self.task)["artifact_ids"], [])
        proposal = dict(self.fixture.directory_proposal(), data_requests=[], artifact_ids=[self.record["id"]])
        with self.assertRaisesRegex(ContractError, "正文"):
            validate_supplement(self.task, proposal)

    def test_budget_suppresses_download_actions_but_keeps_candidate_progress(self):
        before = self.agent._supplement_filing_candidates(self.task, {"q1"})
        self.assertTrue(before)
        self.task["ledger"]["used"]["data_requests"] = self.task["ledger"]["limits"]["data_requests"]
        self.assertEqual(self.agent._supplement_options(self.task)["filing_candidates"], [])
        self.assertEqual(self.agent._supplement_filing_candidates(self.task, {"q1"}), before)


if __name__ == "__main__":
    unittest.main()
