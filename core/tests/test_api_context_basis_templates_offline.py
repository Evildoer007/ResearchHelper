"""Independent regression of exact anchors and method input requirements.

Synthetic fixtures only. No credential reads, external calls or automatic
repairs of the preserved real research are performed.
"""

import copy
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, METHOD_REGISTRY
from researchhelper.evidence import validate_calculation_context_basis
from researchhelper.reasoning import evidence_contract
from tests import test_api_calculation_context_contract_offline as fixtures


class APIContextBasisTemplateTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.APICalculationContextContractTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.task = fixture.task
        self.agent = fixture.agent
        self.record, self.constraints, self.item = fixture.record, fixture.constraints, fixture.item

    def test_templates_use_normalized_context_keys_not_provider_parameters(self):
        record = self.record("ifind")
        contract = self.constraints()
        expected = set(contract["row_fields"]) | set(contract["fixed_fields"]) | set(contract["column_fields"])
        self.assertEqual(set(contract["basis_templates"]), expected)
        for field, template in contract["basis_templates"].items():
            self.assertEqual(template, {"kind": "api", "artifact_id": record["id"],
                                       "snapshot_hash": record["hash"], "field": field})
        self.assertFalse({"Currency", "CPS", "startdate", "enddate", "close"} & expected)

    def test_exact_templates_pass_unchanged_context_validator(self):
        record = self.record("ifind")
        contract = self.constraints()
        values = {**contract["fixed_fields"], "security_id": "510300.SH", "price_kind": "close",
                  "observation_date": "2026-09-30", "corporate_action_status": "unknown"}
        item = self.item(record, values)
        item["calculation_context_basis"] = {
            field: copy.deepcopy(contract["basis_templates"][field])
            if field != "corporate_action_status" else {"kind": "unverified"}
            for field in values
        }
        validate_calculation_context_basis(item, [record])

    def test_provider_field_aliases_remain_rejected(self):
        record = self.record("ifind")
        for field, value, alias in (("currency", "CNY", "Currency"),
                                    ("price_basis", "unadjusted", "close"),
                                    ("window_start", "2026-09-29", "startdate")):
            with self.subTest(field=field):
                item = self.item(record, {field: value})
                item["calculation_context_basis"][field]["field"] = alias
                with self.assertRaisesRegex(ContractError, "field须为" + field):
                    validate_calculation_context_basis(item, [record])

    def test_templates_do_not_grant_missing_currency_or_cross_snapshot_authority(self):
        record = self.record()
        contract = self.constraints()
        self.assertNotIn("currency", contract["basis_templates"])
        self.assertNotIn("price_basis", contract["basis_templates"])
        item = self.item(record, {"price_kind": "close"})
        item["calculation_context_basis"]["price_kind"] = dict(contract["basis_templates"]["price_kind"], snapshot_hash="foreign")
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(item, [record])

    def test_templates_are_detached_and_never_approve_or_charge(self):
        self.record("ifind")
        before = copy.deepcopy(self.task)
        contract = self.constraints()
        contract["basis_templates"]["currency"]["field"] = "changed"
        self.assertEqual(self.task, before)
        self.assertEqual(self.constraints()["basis_templates"]["currency"]["field"], "currency")

    def test_extraction_contract_lists_all_required_price_input_context(self):
        self.record("ifind")
        self.task["calculation_specs"] = [{"id": "c1", "method": "observed_price_return_v1",
                                          "inputs": {"start_price": "start", "end_price": "end"}}]
        before = copy.deepcopy(self.task)
        requirements = evidence_contract(self.task)["calculation_input_requirements"]
        item = requirements["items"][0]
        self.assertEqual(item["required_context"], METHOD_REGISTRY[item["method"]]["binding_context"])
        self.assertTrue({"security_id", "price_kind", "observation_date"} <= set(item["required_context"]))
        self.assertEqual(item["inputs"], {"start_price": "start", "end_price": "end"})
        self.assertFalse(requirements["automatic_adoption"])
        item["required_context"].clear()
        self.assertEqual(self.task, before)
        self.assertTrue(METHOD_REGISTRY["observed_price_return_v1"]["binding_context"])

    def test_no_unplanned_methods_or_material_only_context_added(self):
        self.record("ifind")
        self.assertEqual(evidence_contract(self.task)["calculation_input_requirements"]["items"], [])
        self.task["intent"] = "原件内容核对"
        self.task["calculation_specs"] = []
        contract = evidence_contract(self.task)
        self.assertEqual(contract["calculation_input_requirements"]["items"], [])

    def test_actual_extraction_payload_receives_required_context_and_exact_anchors(self):
        self.record("ifind")
        self.task["host_result"] = {}
        self.task["calculation_specs"] = [{"id": "c1", "method": "observed_price_return_v1",
                                          "inputs": {"start_price": "start", "end_price": "end"}}]
        with patch.object(self.agent, "_reasoning_available", return_value=True), \
                patch.object(self.agent, "_save_model_checkpoint"), \
                patch("researchhelper.engine.model_object", return_value=({"evidence_specs": []}, {})) as model:
            self.agent._extract_model_sources(self.task)
        grammar = model.call_args.args[3]["evidence_contract"]
        requirements = grammar["calculation_input_requirements"]["items"][0]
        self.assertEqual(len(requirements["required_context"]), 8)
        anchor = grammar["sources"][0]["calculation_context_contract"]["basis_templates"]["currency"]
        self.assertEqual(anchor["field"], "currency")
        self.assertFalse(self.task["evidence"])
        self.assertEqual(self.task["ledger"]["used"]["model_calls"], 0)
