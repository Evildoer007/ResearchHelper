"""Independent compact-wire tests using the unchanged provenance gates."""

import copy
import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from researchhelper.evidence import expand_api_context_reference, validate_calculation_context_basis
from researchhelper.reasoning import evidence_contract, validate_model_specs
from tests import test_api_calculation_context_contract_offline as contexts
from tests import test_current_snapshot_availability_offline as acquired


class CompactAPIContextTests(unittest.TestCase):
    def setUp(self):
        fixture = contexts.APICalculationContextContractTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture, self.task = fixture, fixture.task
        self.record = fixture.record("ifind")

    def spec(self, record=None):
        record = record or self.record
        contract = evidence_contract(self.task)["sources"][0]
        values = {**contract["calculation_context_contract"]["fixed_fields"],
                  "security_id": "510300.SH", "price_kind": "close",
                  "observation_date": "2026-09-30", "corporate_action_status": "unknown"}
        return {"id": "close", "artifact_id": record["id"], "calculation_context": values,
                "api_context_reference": copy.deepcopy(contract["compact_context_reference"])}

    def test_explicit_compact_reference_expands_to_exact_existing_anchors(self):
        proposal = self.spec()
        before = copy.deepcopy((self.task, self.record, proposal))
        expanded = expand_api_context_reference(self.task, self.record, proposal)
        item = self.fixture.item(self.record, proposal["calculation_context"])
        item["calculation_context_basis"] = expanded["calculation_context_basis"]
        validate_calculation_context_basis(item, [self.record])
        self.assertNotIn("api_context_reference", expanded)
        self.assertEqual((self.task, self.record, proposal), before)
        self.assertFalse(self.task["evidence"])
        self.assertEqual(self.task["ledger"]["used"]["model_calls"], 0)

    def test_compact_encoding_removes_most_repeated_anchor_bytes(self):
        compact = self.spec()
        full = expand_api_context_reference(self.task, self.record, compact)
        encode = lambda value: json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
        self.assertLess(len(encode(compact)), len(encode(full)) * .5)

    def test_legacy_invalid_anchor_is_not_repaired(self):
        legacy = self.fixture.item(self.record, {"currency": "CNY"})
        legacy["calculation_context_basis"]["currency"]["field"] = "Currency"
        self.assertIs(expand_api_context_reference(self.task, self.record, legacy), legacy)
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(legacy, [self.record])

    def test_mixed_encodings_and_unknown_reference_fields_are_rejected(self):
        for extra in ({"calculation_context_basis": {}},):
            with self.assertRaises(ContractError):
                expand_api_context_reference(self.task, self.record, dict(self.spec(), **extra))
        for version in (True, "1", 2, None):
            spec = self.spec()
            spec["api_context_reference"]["version"] = version
            with self.assertRaises(ContractError):
                expand_api_context_reference(self.task, self.record, spec)
        spec = self.spec()
        spec["api_context_reference"]["approved"] = True
        with self.assertRaises(ContractError):
            expand_api_context_reference(self.task, self.record, spec)

    def test_cross_artifact_and_wrong_hash_are_rejected(self):
        for field in ("artifact_id", "snapshot_hash"):
            spec = self.spec()
            spec["api_context_reference"][field] = "foreign"
            with self.assertRaises(ContractError):
                expand_api_context_reference(self.task, self.record, spec)

    def test_cross_task_cross_version_and_tampered_snapshot_are_rejected(self):
        for field, value in (("task_id", "foreign"), ("research_version", 99)):
            record = copy.deepcopy(self.record)
            record[field] = value
            with self.assertRaises(ContractError):
                expand_api_context_reference(self.task, record, self.spec())
        record = copy.deepcopy(self.record)
        record["snapshot"]["tables"][0]["table"]["close"][0] = 123
        with self.assertRaises(ContractError):
            expand_api_context_reference(self.task, record, self.spec())

    def test_wrong_context_value_still_fails_same_validator(self):
        for field, value in (("currency", "USD"), ("observation_date", "2026-10-01"),
                             ("price_kind", "open"), ("price_basis", "adjusted")):
            spec = self.spec()
            spec["calculation_context"][field] = value
            expanded = expand_api_context_reference(self.task, self.record, spec)
            item = self.fixture.item(self.record, spec["calculation_context"])
            item["calculation_context_basis"] = expanded["calculation_context_basis"]
            with self.assertRaises(ContractError):
                validate_calculation_context_basis(item, [self.record])

    def test_unknown_values_are_not_filled_and_company_actions_are_not_inferred(self):
        for context in ({}, {"currency": True}, {"corporate_action_status": "verified_none"},
                        {"corporate_action_status": "present"}, {"accounting_standard": "CAS"}):
            spec = self.spec()
            spec["calculation_context"] = context
            with self.assertRaises(ContractError):
                expand_api_context_reference(self.task, self.record, spec)

    def test_missing_tinyshare_currency_cannot_be_granted_by_reference(self):
        self.record = self.fixture.record("tinyshare")
        contract = evidence_contract(self.task)["sources"][0]
        spec = {"artifact_id": self.record["id"], "calculation_context": {"currency": "CNY"},
                "api_context_reference": contract["compact_context_reference"]}
        with self.assertRaises(ContractError):
            expand_api_context_reference(self.task, self.record, spec)

    def test_compact_reference_is_not_advertised_for_document_content(self):
        self.task["intent"] = "原件内容核对"
        self.assertNotIn("compact_context_reference", evidence_contract(self.task)["sources"][0])


class AcquiredCompactAPITests(unittest.TestCase):
    def setUp(self):
        fixture = acquired.CurrentSnapshotAvailabilityTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.task, self.record = fixture.acquired()

    def spec(self):
        proposal = self.fixture.spec(self.task, self.record)
        proposal["calculation_context"] = {"security_id": "510300.SH", "price_kind": "close",
                                           "observation_date": "2026-09-30"}
        proposal["api_context_reference"] = evidence_contract(self.task)["sources"][0]["compact_context_reference"]
        return proposal

    def test_compact_and_full_proposals_produce_identical_verified_evidence(self):
        compact = self.spec()
        before = copy.deepcopy(compact)
        full = expand_api_context_reference(self.task, self.record, compact)
        validate_model_specs(self.task, [compact], schema_only=True)
        self.assertEqual(self.fixture.approved(self.task, self.record, compact),
                         self.fixture.approved(self.task, self.record, full))
        self.assertEqual(compact, before)

    def test_compact_reference_does_not_grant_identity_or_availability(self):
        for field in ("direct_code_reference", "availability"):
            proposal = self.spec()
            proposal.pop(field)
            with self.assertRaises(ContractError):
                self.fixture.approved(self.task, self.record, proposal)

    def test_compact_reference_does_not_grant_history_or_financial_approval(self):
        changed = copy.deepcopy(self.task)
        changed["research_mode"] = "historical"
        with self.assertRaises(ContractError):
            self.fixture.approved(changed, self.record, self.spec())
        proposal = self.spec()
        proposal["source_check"] = "通过"
        with self.assertRaises(ContractError):
            validate_model_specs(self.task, [proposal])

    def test_host_run_saves_expanded_evidence_and_restores_it_without_new_calls(self):
        proposal = self.spec()
        result = self.fixture.agent.start(self.task["id"], {"evidence_specs": [proposal], "answers": {"q1": {
            "answer": "本次取得的版本记录收盘价为4.5元。", "refs": ["close"]}}})
        self.assertEqual(result["status"], "已完成", result.get("error"))
        self.assertNotIn("api_context_reference", result["evidence"][0])
        self.assertEqual(result["evidence"][0]["calculation_context_basis"]["price_kind"]["field"], "price_kind")
        self.assertEqual(result["ledger"]["used"]["data_requests"], 2)
        self.assertEqual(result["ledger"]["used"]["model_calls"], 0)
        report = self.fixture.agent.export(self.task["id"])
        self.assertNotIn("api_context_reference", report["content"]["evidence"][0])
        from researchhelper.archive import ResearchArchive
        from researchhelper.engine import ResearchAgent
        from researchhelper.contracts import validate_evidence
        restarted = ResearchAgent(ResearchArchive(self.fixture.fixture.root / "store"), acquired.ROOT, offline=True)
        restored = restarted.archive.get(self.task["id"])
        validate_evidence(restored["evidence"][0], restored)
        self.assertEqual(restarted.export(self.task["id"])["digest"], report["digest"])

    def test_model_extraction_adopts_compact_proposal_without_mutating_or_approving_it(self):
        proposal = self.spec()
        self.task["host_result"] = {}
        with patch.object(self.fixture.agent, "_reasoning_available", return_value=True), \
                patch.object(self.fixture.agent, "_save_model_checkpoint"), \
                patch("researchhelper.engine.model_object", return_value=({"evidence_specs": [proposal]}, {})) as model:
            result = self.fixture.agent._extract_model_sources(self.task)
        self.assertEqual(model.call_args.args[2], "extract")
        self.assertEqual(result["evidence_specs"], [proposal])
        self.assertFalse(self.task["evidence"])
        self.assertEqual(self.task["ledger"]["used"]["model_calls"], 0)
        self.fixture.approved(self.task, self.record, result["evidence_specs"][0])
