"""Independent checks for mutually exclusive context encodings.

Capture the actual model prompt and run the real proposal validators using
synthetic API records. No provider, model service or credential is contacted.
"""

import copy
import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import expand_api_context_reference
from researchhelper.reasoning import (API_EXTRACTION_GUIDANCE_VERSION, PHASE_RULES,
                                     RULES_VERSION, evidence_contract, model_object,
                                     extractable_source_views, validate_model_specs)
from tests import test_api_compact_context_offline as compact


class CapturingModel:
    def __init__(self, proposal):
        self.output = json.dumps({"evidence_specs": [proposal]}, ensure_ascii=False)
        self.calls = []

    def reason_phase(self, task, messages, phase):
        self.calls.append((phase, copy.deepcopy(messages)))
        return self.output


class APIContextPromptModesTests(unittest.TestCase):
    def setUp(self):
        fixture = compact.AcquiredCompactAPITests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.task, self.record = fixture.task, fixture.record

    def call(self, proposal):
        model = CapturingModel(proposal)
        payload = {"evidence_contract": evidence_contract(self.task),
                   "untrusted_sources": extractable_source_views(self.task)}
        value, receipt = model_object(model, self.task, "extract", payload, {"evidence_specs"})
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(model.calls[0][0], "extract")
        self.assertEqual(receipt["context_hash"], fingerprint(payload))
        validate_model_specs(self.task, value["evidence_specs"], schema_only=True)
        return model.calls[0][1], value

    def test_actual_prompt_explicitly_scopes_equal_keys_to_full_basis_only(self):
        messages, value = self.call(self.fixture.spec())
        system = messages[0]["content"]
        self.assertIn("仅calculation_context与calculation_context_basis须键集合完全相同", system)
        self.assertIn("引用对象的键不与calculation_context比较", system)
        self.assertNotIn("两者必须都是非空JSON对象，键集合完全相同", system)
        self.assertIn("不混用、不提交另一格式的空对象", system)
        self.assertEqual(value["evidence_specs"], [self.fixture.spec()])

    def test_compact_shape_has_three_reference_keys_not_context_keys(self):
        proposal = self.fixture.spec()
        # Make the distinct cardinalities observable rather than coincidentally
        # both having three keys in the smaller acquisition fixture.
        proposal["calculation_context"]["corporate_action_status"] = "unknown"
        messages, value = self.call(proposal)
        contract = json.loads(messages[1]["content"])["evidence_contract"]
        compact = contract["compact_api_context"]
        self.assertFalse(compact["context_key_set_matches_reference"])
        self.assertEqual(compact["required_fields"], ["version", "artifact_id", "snapshot_hash"])
        self.assertEqual(compact["excluded_field"], "calculation_context_basis")
        self.assertNotEqual(set(proposal["calculation_context"]), set(proposal["api_context_reference"]))
        item = self.fixture.fixture.approved(self.task, self.record, value["evidence_specs"][0])
        self.assertEqual(item["source_check"], "通过")
        self.assertFalse(self.task["evidence"])

    def test_full_encoding_retains_equal_key_requirement_and_actual_gate(self):
        full = expand_api_context_reference(self.task, self.record, self.fixture.spec())
        messages, value = self.call(full)
        shape = json.loads(messages[1]["content"])["evidence_contract"]["calculation_context_shape"]
        self.assertTrue(shape["same_key_set_required"])
        self.assertIn("不适用于api_context_reference", shape["applies_to"])
        self.assertEqual(set(full["calculation_context"]), set(full["calculation_context_basis"]))
        self.fixture.fixture.approved(self.task, self.record, value["evidence_specs"][0])

    def test_full_encoding_missing_key_remains_rejected(self):
        proposal = expand_api_context_reference(self.task, self.record, self.fixture.spec())
        proposal["calculation_context_basis"].pop("price_kind")
        with self.assertRaises(ContractError):
            self.call(proposal)

    def test_mixed_encoding_remains_rejected_even_when_other_object_is_empty(self):
        for value in ({}, None):
            proposal = self.fixture.spec()
            proposal["calculation_context_basis"] = value
            with self.assertRaises(ContractError):
                self.call(proposal)

    def test_context_fields_cannot_be_added_to_compact_reference(self):
        proposal = self.fixture.spec()
        proposal["api_context_reference"]["price_kind"] = "close"
        with self.assertRaises(ContractError):
            self.call(proposal)

    def test_v4_extraction_cache_is_invalidated_without_resetting_budget(self):
        agent = self.fixture.fixture.agent
        self.task["host_result"] = {}
        ledger = copy.deepcopy(self.task["ledger"])
        old = {"input_hash": self.task["input_hash"], "sources": extractable_source_views(self.task),
               "reader_version": 1, "api_candidate_version": 4}
        self.task["reasoning_state"] = {"extraction": {"input_digest": fingerprint(old)}}
        with patch.object(agent, "_reasoning_available", return_value=True), \
                patch.object(agent, "_save_model_checkpoint"), \
                patch("researchhelper.engine.model_object", return_value=({"evidence_specs": []}, {})) as model:
            agent._extract_model_sources(self.task)
            agent._extract_model_sources(self.task)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(API_EXTRACTION_GUIDANCE_VERSION, 5)
        self.assertEqual(RULES_VERSION, 26)
        self.assertEqual(self.task["ledger"], ledger)
        self.assertEqual(self.task["reasoning_state"]["extraction"]["input_digest"],
                         fingerprint(dict(old, api_candidate_version=5)))

    def test_contract_is_detached_and_does_not_relax_provenance(self):
        before = copy.deepcopy(self.task)
        contract = evidence_contract(self.task)
        contract["compact_api_context"]["required_fields"].clear()
        self.assertEqual(self.task, before)
        self.assertEqual(len(evidence_contract(self.task)["compact_api_context"]["required_fields"]), 3)
        for field in ("identity", "availability"):
            proposal = self.fixture.spec()
            proposal.pop(field)
            with self.assertRaises(ContractError):
                self.fixture.fixture.approved(self.task, self.record, proposal)

    def test_no_false_fix_for_strict_json_parser(self):
        model = CapturingModel(self.fixture.spec())
        model.output = '{"evidence_specs":[],}'
        with self.assertRaises(ContractError):
            model_object(model, self.task, "extract", {}, {"evidence_specs"})
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(model.output, '{"evidence_specs":[],}')
        self.assertFalse(self.task["evidence"])
