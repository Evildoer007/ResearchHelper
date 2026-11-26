"""Check extraction prompt scoping without provider or model-service calls.

Exercise the real orchestrator and parser with retained synthetic API records.
Scoping describes originals only; it must not approve evidence, reset budgets,
weaken source bindings, or force previously valid checkpoints to rerun.
"""

import copy
import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.evidence import FIELD_REGISTRY
from researchhelper.reasoning import PHASE_RULES, extractable_source_views
from tests import test_api_compact_context_offline as compact
from tests import test_api_calculation_context_contract_offline as contexts


class CapturingModel:
    def __init__(self):
        self.messages = []

    def reason_phase(self, task, messages, phase):
        assert phase == "extract"
        self.messages.append(copy.deepcopy(messages))
        return '{"evidence_specs":[]}'


class ExtractionRegistryScopeTests(unittest.TestCase):
    def setUp(self):
        fixture = compact.AcquiredCompactAPITests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.agent = fixture.fixture.agent
        self.task, self.record = fixture.task, fixture.record
        self.task["host_result"] = {}
        self.model = CapturingModel()
        self.agent.model = self.model

    def extract(self):
        with patch.object(self.agent, "_reasoning_available", return_value=True), \
                patch.object(self.agent, "_save_model_checkpoint"):
            return self.agent._extract_model_sources(self.task)

    def test_actual_prompt_contains_only_acquired_api_fields(self):
        self.extract()
        payload = json.loads(self.model.messages[0][1]["content"])
        registry = payload["field_registry"]
        name = self.record["source"] + "." + self.record["operation"]
        self.assertEqual(set(registry), {name})
        self.assertEqual(registry[name], FIELD_REGISTRY[name])
        self.assertLess(len(canonical(registry)), len(canonical(FIELD_REGISTRY)) / 2)
        self.assertEqual(payload["untrusted_sources"], extractable_source_views(self.task))
        self.assertIn(PHASE_RULES["extract"], self.model.messages[0][0]["content"])
        self.assertFalse(self.task["evidence"])

    def test_ifind_prompt_does_not_include_tinyshare_rules(self):
        fixture = contexts.APICalculationContextContractTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.task = fixture.task
        self.record = fixture.record("ifind")
        self.task["host_result"] = {}
        self.extract()
        registry = json.loads(self.model.messages[0][1]["content"])["field_registry"]
        self.assertEqual(set(registry), {"ifind.cmd_history_quotation"})

    def test_prompt_scoping_does_not_reset_or_charge_ledger(self):
        ledger = copy.deepcopy(self.task["ledger"])
        self.extract()
        self.assertEqual(self.task["ledger"], ledger)

    def test_current_checkpoint_is_reused_without_new_inference(self):
        self.extract()
        checkpoint = copy.deepcopy(self.task["reasoning_state"]["extraction"])
        self.extract()
        self.assertEqual(len(self.model.messages), 1)
        self.assertEqual(self.task["reasoning_state"]["extraction"], checkpoint)

    def test_existing_valid_checkpoint_is_not_invalidated_by_prompt_only_change(self):
        self.extract()
        checkpoint = copy.deepcopy(self.task["reasoning_state"]["extraction"])
        ledger = copy.deepcopy(self.task["ledger"])
        with patch.dict(PHASE_RULES, {"extract": PHASE_RULES["extract"] + "\n仅调整说明文字。"}):
            self.extract()
        self.assertEqual(len(self.model.messages), 1)
        self.assertEqual(self.task["reasoning_state"]["extraction"], checkpoint)
        self.assertEqual(self.task["ledger"], ledger)

    def test_obsolete_semantic_checkpoint_is_recomputed_without_approving_evidence(self):
        views = extractable_source_views(self.task)
        digest = fingerprint({"input_hash": self.task["input_hash"], "sources": views,
                              "reader_version": 1, "api_candidate_version": 5})
        self.task["reasoning_state"] = {"extraction": {"input_digest": digest}}
        self.extract()
        self.assertEqual(len(self.model.messages), 1)
        self.assertNotEqual(self.task["reasoning_state"]["extraction"]["input_digest"], digest)
        self.assertFalse(self.task["evidence"])

    def test_payload_cannot_mutate_global_field_definitions(self):
        before = copy.deepcopy(FIELD_REGISTRY)

        def mutate_payload(model, task, phase, payload, expected_fields):
            name = self.record["source"] + "." + self.record["operation"]
            payload["field_registry"][name].clear()
            return {"evidence_specs": []}, {}

        with patch("researchhelper.engine.model_object", side_effect=mutate_payload):
            self.extract()
        self.assertEqual(FIELD_REGISTRY, before)

    def test_cross_task_source_remains_rejected_before_model_dispatch(self):
        self.record["task_id"] = "another_task"
        ledger = copy.deepcopy(self.task["ledger"])
        with self.assertRaises(ContractError):
            self.extract()
        self.assertFalse(self.model.messages)
        self.assertEqual(self.task["ledger"], ledger)
