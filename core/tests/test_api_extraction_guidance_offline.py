"""API guidance describes the real gates; it never completes a proposal."""

import copy
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.reasoning import (API_EXTRACTION_GUIDANCE_VERSION, MODEL_EVIDENCE_FIELDS,
                                     evidence_contract)
from tests import test_api_calculation_context_contract_offline as fixtures
from tests import test_current_snapshot_availability_offline as acquired


class APIExtractionGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.APICalculationContextContractTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent, self.task = self.fixture.agent, self.fixture.task
        self.fixture.record("ifind")

    def test_required_and_optional_fields_exactly_match_allowed_model_fields(self):
        grammar = evidence_contract(self.task)["api_candidate_contract"]
        self.assertEqual(set(grammar["required_spec_fields"]) | set(grammar["optional_fields"]), MODEL_EVIDENCE_FIELDS)
        self.assertFalse(set(grammar["required_spec_fields"]) & set(grammar["optional_fields"]))
        self.assertIn("identity", grammar["required_spec_fields"])
        self.assertNotIn("availability", grammar["required_spec_fields"])
        self.assertIn("availability", grammar["conditional_fields"])

    def test_identity_shapes_cover_all_validator_fields_without_default_values(self):
        contract = evidence_contract(self.task)
        shapes = contract["api_candidate_contract"]["field_shapes"]["identity"]
        self.assertEqual(set(shapes), set(contract["identity_fields_for_api"]))
        self.assertNotEqual(shapes["provider_code"], "510300.SH")
        self.assertNotEqual(shapes["valid_from"], "2026-09-29")
        cell = contract["sources"][0]["api_cells"]["cells"][0]
        self.assertNotIn("identity", cell["proposal_fields"])

    def test_partial_location_cannot_be_mistaken_for_a_complete_specification(self):
        grammar = evidence_contract(self.task)["api_candidate_contract"]
        cell = evidence_contract(self.task)["sources"][0]["api_cells"]["cells"][0]
        missing = set(grammar["required_spec_fields"]) - set(cell["proposal_fields"])
        self.assertTrue({"identity", "publication", "review", "raw_value", "unit"} <= missing)
        self.assertFalse(grammar["automatic_adoption"])
        self.assertFalse(grammar["automatic_reference_repair"])

    def test_api_guidance_version_is_bound_to_unfinished_extraction_cache(self):
        from researchhelper.reasoning import extractable_source_views
        self.task["host_result"] = {}
        before = fingerprint(self.task["ledger"])
        prior = {"input_hash": self.task["input_hash"], "sources": extractable_source_views(self.task),
                 "reader_version": 1, "api_candidate_version": 1}
        self.task["reasoning_state"] = {"extraction": {"input_digest": fingerprint(prior)}}
        with patch.object(self.agent, "_reasoning_available", return_value=True), \
                patch.object(self.agent, "_save_model_checkpoint"), \
                patch("researchhelper.engine.model_object", return_value=({"evidence_specs": []}, {})) as model:
            self.agent._extract_model_sources(self.task)
            self.agent._extract_model_sources(self.task)
        self.assertEqual(model.call_count, 1)
        current = dict(prior, api_candidate_version=API_EXTRACTION_GUIDANCE_VERSION)
        self.assertEqual(self.task["reasoning_state"]["extraction"]["input_digest"], fingerprint(current))
        self.assertEqual(fingerprint(self.task["ledger"]), before)

    def test_tool_reading_does_not_modify_task_or_field_registration(self):
        before = copy.deepcopy(self.task)
        contract = evidence_contract(self.task)
        contract["api_candidate_contract"]["field_shapes"]["review"]["conditions"] = "changed"
        self.assertEqual(self.task, before)
        self.assertNotEqual(evidence_contract(self.task)["api_candidate_contract"]["field_shapes"]["review"]["conditions"], "changed")


class AcquiredAPIGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = acquired.CurrentSnapshotAvailabilityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_complete_sdk_spec_covers_checklist_and_passes_actual_gates(self):
        task, record = self.fixture.acquired()
        proposal = self.fixture.spec(task, record)
        grammar = evidence_contract(task)["api_candidate_contract"]
        self.assertTrue(set(grammar["required_spec_fields"]) <= set(proposal))
        item = self.fixture.approved(task, record, proposal)
        self.assertEqual(item["source_check"], "通过")
        self.assertEqual(item["applicability_check"], "通过")
        self.assertEqual(task["ledger"]["used"]["model_calls"], 0)

    def test_reading_complete_grammar_cannot_approve_missing_identity(self):
        task, record = self.fixture.acquired()
        evidence_contract(task)
        proposal = self.fixture.spec(task, record)
        proposal.pop("identity")
        with self.assertRaises(ContractError):
            self.fixture.approved(task, record, proposal)
        self.assertFalse(task["evidence"])

    def test_reading_complete_grammar_cannot_mint_availability_for_import(self):
        from researchhelper.evidence import snapshot_record
        task, record = self.fixture.acquired()
        imported = snapshot_record(task, record["source"], record["operation"], record["parameters"], record["snapshot"])
        task["source_records"] = [imported]
        grammar = evidence_contract(task)
        self.assertIn("availability", grammar["api_candidate_contract"]["conditional_fields"])
        self.assertNotIn("current_availability_reference", grammar["sources"][0])
        with self.assertRaises(ContractError):
            self.fixture.approved(task, imported, self.fixture.spec(task, record))
