"""Independent API-context regressions; synthetic imports, no providers."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import snapshot_record, validate_calculation_context_basis
from researchhelper.reasoning import evidence_contract

ROOT = Path(__file__).resolve().parents[2]


class APICalculationContextContractTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / "result/acceptance/api-calculation-context"
        directory.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(prefix="isolated-", dir=directory)
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        guard = patch("socket.socket.connect", side_effect=AssertionError("External network forbidden"))
        guard.start()
        self.addCleanup(guard.stop)
        self.task = self.agent.create("核对行情计算口径", objects=["510300.SH"])

    def record(self, source="tinyshare", field="close", **parameters):
        if source == "ifind":
            params = {"codes": "510300.SH", "indicators": field, "startdate": "2026-09-29",
                      "enddate": "2026-09-30", "functionpara": {"Currency": "RMB", "CPS": "1", "Fill": "Omit"}}
            raw = {"errorcode": 0, "tables": [{"thscode": "510300.SH", "time": ["2026-09-30"],
                   "table": {field: [4.432]}}]}
            operation = "cmd_history_quotation"
        else:
            params = {"ts_code": "510300.SH", "start_date": "20260929", "end_date": "20260930", "fields": "ts_code,trade_date," + field}
            raw = {"code": 0, "data": [{"ts_code": "510300.SH", "trade_date": "20260930", field: 4.432}]}
            operation = "fund_daily"
        params.update(parameters)
        record = snapshot_record(self.task, source, operation, params, raw)
        self.task["source_records"] = [record]
        return record

    def constraints(self):
        source = evidence_contract(self.task)["sources"][0]
        self.assertIn("calculation_context_contract", source)
        return source["calculation_context_contract"]

    def item(self, record, values):
        return {"task_id": self.task["id"], "research_version": self.task["version"],
                "object": "510300.SH", "artifact": record, "snapshot_hash": record["hash"],
                "record_locator": {"row": 0, "field": "close", **({"table": 0} if record["source"] == "ifind" else {})},
                "identity": {"provider_code": "510300.SH", "object": "510300.SH"},
                "calculation_context": values,
                "calculation_context_basis": {key: {"kind": "api", "artifact_id": record["id"],
                    "snapshot_hash": record["hash"], "field": key} for key in values}}

    def test_tinyshare_missing_currency_and_basis_are_explicit(self):
        self.record()
        contract = self.constraints()
        self.assertEqual(contract["registered_numeric_fields"], ["close"])
        self.assertEqual(contract["column_fields"], {"price_kind": {"close": "close"}})
        self.assertEqual(contract["fixed_fields"]["window_start"], "2026-09-29")
        self.assertIn("currency", contract["unavailable_fields"])
        self.assertIn("price_basis", contract["unavailable_fields"])
        self.assertFalse(contract["context_is_approval"])

    def test_ifind_explicit_parameters_match_actual_validator(self):
        record = self.record("ifind")
        contract = self.constraints()
        self.assertEqual(contract["fixed_fields"]["currency"], "CNY")
        self.assertEqual(contract["fixed_fields"]["price_basis"], "unadjusted")
        values = {**contract["fixed_fields"], "security_id": "510300.SH", "price_kind": "close", "observation_date": "2026-09-30"}
        validate_calculation_context_basis(self.item(record, values), [record])

    def test_tinyshare_does_not_accept_free_declared_currency_or_basis(self):
        record = self.record()
        for key, value in (("currency", "CNY"), ("price_basis", "unadjusted")):
            with self.subTest(field=key), self.assertRaises(ContractError):
                validate_calculation_context_basis(self.item(record, {key: value}), [record])

    def test_nonclose_column_does_not_offer_close_context(self):
        record = self.record(field="vol")
        self.assertEqual(self.constraints()["column_fields"], {})
        item = self.item(record, {"price_kind": "close"})
        item["record_locator"]["field"] = "vol"
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(item, [record])

    def test_corporate_actions_never_assumed_absent(self):
        self.record()
        contract = self.constraints()
        self.assertIn("corporate_action_status", contract["unavailable_fields"])
        self.assertEqual(contract["unverified_context"]["corporate_action_status"],
                         {"value": "unknown", "basis": {"kind": "unverified"}})

    def test_diagnostic_does_not_approve_import_or_charge_budget(self):
        self.record()
        before = copy.deepcopy(self.task)
        self.constraints()
        self.assertEqual(self.task, before)
        self.assertNotIn("current_availability_reference", evidence_contract(self.task)["sources"][0])
        self.assertFalse(self.task["evidence"])

    def test_request_tampering_cannot_change_derived_context(self):
        record = self.record("ifind")
        record["parameters"]["functionpara"]["Currency"] = "CNY"
        self.assertEqual(self.constraints()["status"], "unavailable")
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(self.item(record, {"currency": "CNY"}), [record])

    def test_cross_task_or_changed_snapshot_contract_rejected(self):
        self.record()
        for field, value in (("task_id", "foreign"), ("research_version", 99)):
            altered = copy.deepcopy(self.task)
            altered["source_records"][0][field] = value
            with self.assertRaises(ContractError):
                evidence_contract(altered)
        altered = copy.deepcopy(self.task)
        altered["source_records"][0]["snapshot"]["data"][0]["close"] = 999
        with self.assertRaises(ContractError):
            evidence_contract(altered)

    def test_extraction_model_receives_actual_context_constraints(self):
        self.record()
        self.task["host_result"] = {}
        before = copy.deepcopy(self.task["ledger"])
        with patch.object(self.agent, "_reasoning_available", return_value=True), \
                patch.object(self.agent, "_save_model_checkpoint"), \
                patch("researchhelper.engine.model_object", return_value=({"evidence_specs": []}, {"synthetic": True})) as model:
            self.agent._extract_model_sources(self.task)
        self.assertEqual(model.call_args.args[2], "extract")
        contract = model.call_args.args[3]["evidence_contract"]["sources"][0]["calculation_context_contract"]
        self.assertIn("price_basis", contract["unavailable_fields"])
        self.assertEqual(self.task["ledger"], before)
        self.assertFalse(self.task["evidence"])

    def test_supplement_constraints_follow_unanswered_question_scope(self):
        record = self.record()
        record["question_ids"] = ["q1"]
        before = copy.deepcopy(self.task)
        constraints = self.agent._supplement_context_constraints(self.task)
        self.assertEqual(len(constraints), 1)
        self.assertEqual(constraints[0]["question_ids"], ["q1"])
        self.assertIn("currency", constraints[0]["unavailable_fields"])
        self.assertEqual(self.task, before)
        self.task["questions"][0]["status"] = "已回答"
        self.assertEqual(self.agent._supplement_context_constraints(self.task), [])

    def test_constraints_do_not_create_executable_supplement_action(self):
        record = self.record()
        record["question_ids"] = ["q1"]
        self.assertTrue(self.agent._supplement_context_constraints(self.task))
        self.assertNotIn("api_context_constraints", self.agent._supplement_options(self.task))
        self.assertFalse(any(self.agent._supplement_options(self.task).values()))

    def test_unregistered_financial_response_stays_raw_only(self):
        from researchhelper.contracts import fingerprint
        self.record()
        record = self.task["source_records"][0]
        record["operation"] = "income"
        record["request_hash"] = fingerprint({"source": record["source"], "operation": "income", "parameters": record["parameters"]})
        contract = self.constraints()
        self.assertEqual(contract["status"], "unavailable")
        self.assertEqual(contract["fixed_fields"], {})
        source = evidence_contract(self.task)["sources"][0]
        self.assertTrue(source["financial_raw_only"])
        self.assertFalse(source["numeric_evidence_registered"])

    def test_invalid_observation_day_rejected_even_with_registered_field(self):
        from researchhelper.contracts import fingerprint
        record = self.record()
        record["snapshot"]["data"][0]["trade_date"] = "20260230"
        record["hash"] = fingerprint(record["snapshot"])
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(self.item(record, {"observation_date": "2026-02-30"}), [record])
