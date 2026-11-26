"""Invalid extraction proposals remain unapproved and allow gap delivery."""

import copy
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.reasoning import evidence_contract
from tests import test_extraction_binding_guidance_offline as fixtures
from tests.test_extraction_reread_workflow_offline import ScriptedModel


class ExtractionSpecificationRejectionTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.ExtractionBindingGuidanceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.agent, self.task = fixture.agent, fixture.task
        self.task.update(status="运行中", run_mode="model", host_result={"data_requests": [], "calculations": []})
        self.agent.archive.save(self.task)
        self.agent.offline = False

    def invalid_spec(self):
        value = self.fixture.spec()
        value["calculation_context"] = {"currency": "CNY"}
        value["calculation_context_basis"] = "人民币口径的自由说明，没有定位"
        return value

    def test_context_grammar_is_explicit_and_not_source_approval(self):
        before = fingerprint(self.task)
        grammar = evidence_contract(self.task)["calculation_context_shape"]
        self.assertTrue(grammar["same_key_set_required"])
        self.assertFalse(grammar["example_is_source_fact"])
        self.assertEqual(grammar["basis_fields_by_kind"]["api"],
                         ["kind", "artifact_id", "snapshot_hash", "field"])
        self.assertEqual(fingerprint(self.task), before)

    def test_api_indices_and_review_coverage_have_explicit_types(self):
        contract = evidence_contract(self.task)
        self.assertIn("非负整数", contract["locators"]["api"]["row_type"])
        self.assertIn("必填", contract["locators"]["api"]["ifind_table"])
        self.assertIn("不接受单独列表", contract["review_types"]["evidence_need_ids"])

    def test_invalid_batch_is_recorded_without_adoption_or_refund(self):
        self.agent.model = ScriptedModel([("extract", {"evidence_specs": [self.invalid_spec()]})])
        before = copy.deepcopy(self.task["host_result"])
        result = self.agent._extract_model_sources(self.task)
        self.assertEqual(result["evidence_specs"], [])
        self.assertEqual(self.task["host_result"], before)
        saved = self.agent.archive.get(self.task["id"])
        self.assertEqual(saved["ledger"]["used"]["model_calls"], 1)
        self.assertEqual(saved["status"], "运行中")
        self.assertFalse(saved["evidence"])
        self.assertIn("模型计算口径", saved["gaps"][0])
        rejection = saved["reasoning_state"]["rejected_extraction"]
        self.assertFalse(rejection["specification_accepted"])
        self.assertFalse(rejection["automatic_repair"])
        self.assertNotIn("extraction", saved["reasoning_state"])
        self.assertEqual(len(self.agent.model.calls), 1)

    def test_one_invalid_item_does_not_partially_adopt_batch(self):
        valid = self.fixture.spec()
        self.agent.model = ScriptedModel([("extract", {"evidence_specs": [valid, self.invalid_spec()]})])
        self.assertEqual(self.agent._extract_model_sources(self.task)["evidence_specs"], [])
        self.assertNotIn("evidence_specs", self.task["host_result"])
        self.assertFalse(self.task["evidence"])

    def test_valid_proposal_keeps_existing_success_path(self):
        spec = self.fixture.spec()
        self.agent.model = ScriptedModel([("extract", {"evidence_specs": [spec]})])
        self.assertEqual(self.agent._extract_model_sources(self.task)["evidence_specs"], [spec])
        self.assertIn("extraction", self.task["reasoning_state"])
        self.assertFalse(self.task["evidence"])

    def test_self_signed_approval_is_a_hard_failure_not_a_format_gap(self):
        spec = self.fixture.spec()
        spec["source_check"] = "通过"
        self.agent.model = ScriptedModel([("extract", {"evidence_specs": [spec]})])
        with self.assertRaisesRegex(ContractError, "自签"):
            self.agent._extract_model_sources(self.task)
        rejection = self.task["reasoning_state"]["rejected_extraction"]
        self.assertFalse(rejection["specification_accepted"])
        self.assertFalse(rejection["automatic_repair"])
        self.assertFalse(rejection["issue"]["evidence_approved"])
        self.assertEqual(rejection["proposed_specs"], [spec])
        self.assertNotIn("extraction", self.task["reasoning_state"])
        self.assertEqual(self.task["gaps"], [])

    def test_cancellation_cannot_be_converted_to_gap_delivery(self):
        def late_model(*args):
            cancelled = self.agent.archive.get(self.task["id"])
            cancelled["status"] = "已取消"
            self.agent.archive.save(cancelled)
            return {"evidence_specs": [self.invalid_spec()]}, {"synthetic": True}
        with patch("researchhelper.engine.model_object", side_effect=late_model):
            with self.assertRaisesRegex(ContractError, "已取消"):
                self.agent._extract_model_sources(self.task)
        saved = self.agent.archive.get(self.task["id"])
        self.assertEqual(saved["status"], "已取消")
        self.assertNotIn("rejected_extraction", saved.get("reasoning_state", {}))
