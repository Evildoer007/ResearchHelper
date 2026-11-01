"""Selected API cells are decoded, not model-transcribed or self-approved.

Synthetic source and local SDK fixtures only. No real provider/model calls,
credential reads, legacy test edits or production-store writes.
"""

import copy
import json
from decimal import Decimal
import unittest
from unittest.mock import patch

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_INTENT, fingerprint, validate_evidence
from researchhelper.evidence import (
    API_VALUE_FIELDS, api_value_reference, candidate_evidence, expand_api_value_reference,
)
from researchhelper.reasoning import evidence_contract, validate_model_specs
from tests import test_api_calculation_context_contract_offline as cells
from tests import test_current_snapshot_availability_offline as acquired


class APIValueReferenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = cells.APICalculationContextContractTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent, self.task = self.fixture.agent, self.fixture.task
        self.record = self.fixture.record("ifind")

    def proposal(self, record=None):
        record = record or self.record
        return {"id": "selected_close", "artifact_id": record["id"], "object": "510300.SH",
                "record_locator": {"row": 0, "field": "close", **({"table": 0} if record["source"] == "ifind" else {})},
                "api_value_reference": api_value_reference(record)}

    def rehash(self, record):
        record["hash"] = fingerprint(record["snapshot"])
        if "snapshot_hash" in record:
            record["snapshot_hash"] = record["hash"]
        return record

    def test_ifind_reads_exact_original_cell_and_registered_unit(self):
        result = expand_api_value_reference(self.task, self.record, self.proposal())
        self.assertEqual(result["raw_value"], 4.432)
        self.assertEqual(result["value"], 4.432)
        self.assertEqual(result["period"], "2026-09-30")
        self.assertEqual((result["raw_unit"], result["unit"], result["conversion_factor"]), ("元", "元", 1))
        self.assertNotIn("api_value_reference", result)

    def test_tinyshare_preserves_raw_decimal_string_and_normalizes_only_date(self):
        record = self.fixture.record()
        record["snapshot"]["data"][0]["close"] = "4.4320"
        self.rehash(record)
        result = expand_api_value_reference(self.task, record, self.proposal(record))
        self.assertEqual(result["raw_value"], "4.4320")
        self.assertEqual(result["value"], 4.432)
        self.assertEqual(result["period"], "2026-09-30")

    def test_unit_conversion_preserves_more_than_decimal_default_precision(self):
        record = self.fixture.record(field="amount")
        raw = "123456789012345678901234567890.123"
        record["snapshot"]["data"][0]["amount"] = raw
        self.rehash(record)
        spec = self.proposal(record)
        spec["record_locator"]["field"] = "amount"
        result = expand_api_value_reference(self.task, record, spec)
        self.assertEqual(result["raw_value"], raw)
        self.assertEqual(result["value"], 123456789012345678901234567890123)
        self.assertEqual((result["raw_unit"], result["unit"], result["conversion_factor"]), ("千元", "元", 1000))

    def test_unrepresentable_decimal_not_silently_rounded(self):
        record = self.fixture.record()
        record["snapshot"]["data"][0]["close"] = "4.43201234567890123456789"
        self.rehash(record)
        with self.assertRaisesRegex(ContractError, "无损表示精度"):
            expand_api_value_reference(self.task, record, self.proposal(record))

    def test_plain_legacy_proposal_is_returned_without_repair(self):
        spec = {"id": "legacy", "raw_value": 999}
        self.assertIs(expand_api_value_reference(self.task, self.record, spec), spec)

    def test_mixed_full_and_compact_fields_rejected_even_if_null_or_correct(self):
        for key in API_VALUE_FIELDS | {"raw_token"}:
            with self.subTest(field=key), self.assertRaises(ContractError):
                expand_api_value_reference(self.task, self.record, dict(self.proposal(), **{key: None}))

    def test_reference_keys_versions_and_bindings_cannot_be_changed(self):
        mutations = [
            lambda r: r.pop("snapshot_hash"), lambda r: r.update(extra=True),
            lambda r: r.update(version=True), lambda r: r.update(version=2),
            lambda r: r.update(field_registry_version=True), lambda r: r.update(field_registry_version=99),
            lambda r: r.update(artifact_id="foreign"), lambda r: r.update(snapshot_hash="forged"),
            lambda r: r.update(field_registry="tinyshare.fund_daily"),
        ]
        for index, mutate in enumerate(mutations):
            spec = self.proposal()
            mutate(spec["api_value_reference"])
            with self.subTest(mutation=index), self.assertRaises(ContractError):
                expand_api_value_reference(self.task, self.record, spec)

    def test_locator_requires_actual_array_coordinates(self):
        for locator in ({"row": 0, "field": "close"}, {"row": "0", "field": "close", "table": 0},
                        {"row": False, "field": "close", "table": 0},
                        {"row": 0, "field": "close", "table": True},
                        {"row": -1, "field": "close", "table": 0},
                        {"row": 99, "field": "close", "table": 0},
                        {"row": 0, "field": "unknown", "table": 0},
                        {"row": 0, "field": "close", "table": 0, "condition": "latest"}):
            with self.subTest(locator=locator), self.assertRaises(ContractError):
                expand_api_value_reference(self.task, self.record, dict(self.proposal(), record_locator=locator))

    def test_cross_task_and_version_rejected(self):
        for key, value in (("id", "another_task"), ("version", self.task["version"] + 1)):
            task = copy.deepcopy(self.task)
            task[key] = value
            with self.subTest(field=key), self.assertRaises(ContractError):
                expand_api_value_reference(task, self.record, self.proposal())

    def test_snapshot_and_request_changes_cannot_reuse_reference(self):
        original = self.proposal()
        for field in ("snapshot", "parameters"):
            record = copy.deepcopy(self.record)
            if field == "snapshot":
                record[field]["tables"][0]["table"]["close"][0] = 999
            else:
                record[field]["codes"] = "000001.SZ"
            with self.subTest(field=field), self.assertRaises(ContractError):
                expand_api_value_reference(self.task, record, original)

    def test_null_boolean_nonfinite_and_invalid_numeric_values_rejected(self):
        for value in (None, False, "NaN", "Infinity", "not a number", "1e1000000"):
            record = copy.deepcopy(self.record)
            record["snapshot"]["tables"][0]["table"]["close"][0] = value
            self.rehash(record)
            with self.subTest(value=value), self.assertRaises(ContractError):
                expand_api_value_reference(self.task, record, self.proposal(record))

    def test_raw_only_and_nonapi_sources_have_no_value_reference(self):
        for kind, source, operation in (("api", "tinyshare", "income"), ("api", "ifind", "edb_service"),
                                        ("material", "material", "parse")):
            record = copy.deepcopy(self.record)
            record.update(kind=kind, source=source, operation=operation)
            with self.subTest(operation=operation):
                self.assertIsNone(api_value_reference(record))
                with self.assertRaises(ContractError):
                    expand_api_value_reference(self.task, record, self.proposal())

    def test_invalid_date_and_misaligned_series_not_decoded(self):
        for dates in ([], ["2026-02-30"], ["2026-09-29", "2026-09-30"]):
            record = copy.deepcopy(self.record)
            record["snapshot"]["tables"][0]["time"] = dates
            self.rehash(record)
            with self.subTest(dates=dates), self.assertRaises(ContractError):
                expand_api_value_reference(self.task, record, self.proposal(record))

    def test_decoder_is_readonly_and_does_not_approve_or_call_network(self):
        before = copy.deepcopy((self.task, self.record))
        proposal = self.proposal()
        with patch("urllib.request.urlopen") as request:
            result = expand_api_value_reference(self.task, self.record, proposal)
        request.assert_not_called()
        self.assertEqual((self.task, self.record), before)
        for key in ("identity", "availability", "publication", "review", "question_ids", "calculation_context", "source_check"):
            self.assertNotIn(key, result)
        self.assertFalse(self.task["evidence"])

    def test_schema_validator_checks_value_reference_before_saving(self):
        proposal = self.proposal()
        proposal["api_value_reference"]["snapshot_hash"] = "forged"
        with self.assertRaises(ContractError):
            validate_model_specs(self.task, [proposal], schema_only=True)

    def test_contract_exposes_explicit_alternative_without_approving_import(self):
        before = copy.deepcopy(self.task)
        contract = evidence_contract(self.task)
        self.assertEqual(contract["sources"][0]["value_reference"], api_value_reference(self.record))
        self.assertEqual(set(contract["compact_api_value"]["excluded_fields"]), API_VALUE_FIELDS | {"raw_token"})
        self.assertIn("api_value_reference", contract["api_candidate_contract"]["alternative_shapes"])
        self.assertFalse(contract["compact_api_value"]["automatic_adoption"])
        self.assertEqual(self.task, before)
        with self.assertRaises(ContractError):
            candidate_evidence(self.task, self.record, self.proposal())


class AcquiredAPIValueWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = acquired.CurrentSnapshotAvailabilityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent = self.fixture.agent
        self.task, self.record = self.fixture.acquired()

    def proposal(self):
        spec = self.fixture.spec(self.task, self.record)
        for key in API_VALUE_FIELDS:
            spec.pop(key)
        spec["api_value_reference"] = api_value_reference(self.record)
        return spec

    def approved(self, spec):
        validate_model_specs(self.task, [spec])
        item = candidate_evidence(self.task, self.record, spec)
        item = ResearchAnalyzer().assess(self.task, item, spec.get("review"), self.task["source_records"])
        validate_evidence(item, self.task)
        return item

    def test_local_acquired_cell_passes_original_two_receipts(self):
        before = copy.deepcopy(self.task["ledger"])
        item = self.approved(self.proposal())
        self.assertEqual(Decimal(item["value"]), Decimal("4.5"))
        self.assertEqual(item["raw_value"], 4.5)
        self.assertEqual((item["source_check"], item["applicability_check"]), ("通过", "通过"))
        self.assertIsNone(item["published_at"])
        self.assertEqual(self.task["ledger"], before)
        self.assertNotIn("api_value_reference", item)

    def test_identity_time_and_semantic_reviews_are_not_generated(self):
        for key in ("identity", "availability", "review"):
            spec = self.proposal()
            spec.pop(key)
            with self.subTest(field=key), self.assertRaises(ContractError):
                self.approved(spec)

    def test_wrong_identity_and_unknown_currency_still_fail(self):
        spec = self.proposal()
        spec["identity"]["provider_code"] = "510500.SH"
        with self.assertRaises(ContractError):
            self.approved(spec)
        spec = self.proposal()
        contract = evidence_contract(self.task)["sources"][0]
        spec.update(calculation_context={"currency": "CNY"}, api_context_reference=contract["compact_context_reference"])
        with self.assertRaises(ContractError):
            self.approved(spec)

    def test_historical_and_document_content_scopes_do_not_gain_permission(self):
        spec = self.proposal()
        for mode in ("historical", "document_content"):
            task = copy.deepcopy(self.task)
            if mode == "historical":
                task["research_mode"] = mode
            else:
                task["intent"] = DOCUMENT_CONTENT_INTENT
                spec["usage_scope"] = "document_content"
            with self.subTest(mode=mode), self.assertRaises(ContractError):
                validate_model_specs(task, [spec])

    def test_host_workflow_saves_expanded_evidence_and_restores_exact_report(self):
        spec = self.proposal()
        done = self.agent.start(self.task["id"], {"evidence_specs": [spec], "answers": {"q1": {
            "answer": "本次取得的版本记录收盘价为4.5元。", "refs": ["close"]}}})
        self.assertEqual(done["status"], "已完成", done.get("error"))
        report = self.agent.export(self.task["id"])
        self.assertEqual(report["content"]["evidence"][0]["value"], 4.5)
        self.assertNotIn("api_value_reference", report["content"]["evidence"][0])
        self.assertIn("不证明历史当时可得", report["html"])
        restarted = type(self.agent)(self.agent.archive, acquired.ROOT, offline=True)
        self.assertEqual(restarted.export(self.task["id"])["digest"], report["digest"])

    def test_app_extraction_and_host_validator_accept_same_selected_cell(self):
        proposal = self.proposal()

        class LocalModel:
            messages = []

            def reason_phase(model, task, messages, phase):
                model.messages.append(copy.deepcopy(messages))
                return json.dumps({"evidence_specs": [proposal]})

        model = LocalModel()
        self.agent.model = model
        self.task["host_result"] = {}
        before = copy.deepcopy(self.task["ledger"])
        with patch.object(self.agent, "_reasoning_available", return_value=True), patch.object(self.agent, "_save_model_checkpoint"):
            result = self.agent._extract_model_sources(self.task)
        self.assertEqual(result["evidence_specs"], [proposal])
        self.assertEqual(self.task["ledger"], before)
        payload = json.loads(model.messages[0][1]["content"])
        self.assertEqual(payload["evidence_contract"]["sources"][0]["value_reference"], proposal["api_value_reference"])
        self.assertFalse(self.task["evidence"])
        self.assertEqual(self.approved(proposal)["value"], 4.5)


if __name__ == "__main__":
    unittest.main()
