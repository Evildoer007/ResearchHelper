"""Independent fabricated acceptance for task-bound OH-compatible series."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (CAPABILITIES, FIELD_REGISTRY, EvidenceCollector,
    candidate_evidence, data_request_contract, snapshot_record, validate_source_request)
from researchhelper.market_series import (SERIES_FAMILIES, SERIES_REQUEST_REGISTRY,
    series_request_shapes, series_settings, validate_series_request)
from researchhelper.planning import question_specs
from researchhelper.reasoning import validate_plan, evidence_contract


class OHMarketSeriesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.agent = ResearchAgent(ResearchArchive(Path(temporary.name)), offline=True)
        self.task = self.create("个股", "300033.SZ")
        self.collector = EvidenceCollector(offline=False)
        self.params = self.parameters(self.task)
        blocked = patch("socket.socket.connect", side_effect=AssertionError("Real network forbidden"))
        blocked.start()
        self.addCleanup(blocked.stop)

    def create(self, entry, code):
        return self.agent.create("核对" + code + "的市场背景原始记录，不推断收益", entry=entry,
            intent="解释变化", objects=[code], cutoff="2026-10-02T18:00:00+08:00")

    @staticmethod
    def parameters(task):
        family = SERIES_FAMILIES[task["entry"]]
        indicator = next(iter(family))
        return {"codes": task["objects"][0], "startdate": "2026-09-29", "enddate": "2026-09-30",
            "functionpara": series_settings(task),
            "indipara": [{"indicator": indicator, "indiparams": family[indicator][:]}]}

    def payload(self, params=None):
        params = params or self.params
        return {"errorcode": 0, "tables": [{"thscode": params["codes"], "time": ["2026-09-30"],
            "table": {params["indipara"][0]["indicator"]: [12.34]}}]}

    def rejects_before_auth(self, params, task=None):
        task = task or self.task
        before = copy.deepcopy(task["ledger"])
        with patch.object(self.collector, "_resolve_ifind_access") as auth, \
                patch.object(self.collector, "_http_json") as transport, self.assertRaises(ContractError):
            self.collector.external(task, "ifind", "date_sequence", params, {"refresh_token": "fixture-only"})
        auth.assert_not_called()
        transport.assert_not_called()
        self.assertEqual(task["ledger"], before)

    def test_registration_is_raw_only_not_numeric_evidence_or_permission(self):
        definition = SERIES_REQUEST_REGISTRY["ifind.date_sequence"]
        self.assertTrue(definition["raw_only"])
        self.assertEqual(definition["permission"], "未验收")
        self.assertNotIn("ifind.date_sequence", FIELD_REGISTRY)
        self.assertIn("date_sequence", CAPABILITIES["ifind"]["endpoints"])
        self.assertNotIn("date_sequence", [item["operation"] for item in CAPABILITIES["ifind"]["documented_only"]])

    def test_each_registered_family_exposes_copied_parameters_without_changing_task(self):
        for entry, code in (("个股", "300033.SZ"), ("指数", "000300.SH"), ("ETF", "510300.SH")):
            task = self.create(entry, code)
            before = fingerprint(task)
            shape = series_request_shapes(task)[0]
            self.assertEqual(shape["field_parameters"], SERIES_FAMILIES[entry])
            self.assertEqual(shape["available_codes"], [code])
            self.assertEqual(shape["numeric_fields"], [])
            self.assertFalse(shape["numeric_evidence_registered"])
            self.assertIsNone(shape["example"])
            shape["field_parameters"][next(iter(shape["field_parameters"]))].append("mutation")
            self.assertNotIn("mutation", next(iter(SERIES_FAMILIES[entry].values())))
            self.assertEqual(fingerprint(task), before)

    def test_request_contract_and_model_plan_use_the_same_operation(self):
        tool = next(item for item in data_request_contract(self.task)["tools"] if item["operation"] == "date_sequence")
        self.assertTrue(tool["raw_only"])
        proposal = {"questions": question_specs(self.task), "calculations": [], "data_requests": [{
            "id": "valuation_raw", "source": "ifind", "operation": "date_sequence", "parameters": self.params,
            "question_ids": [self.task["questions"][0]["id"]],
            "selection_reason": "iFinD优先，留存明确对象的已登记市场背景原值，数字口径另核验"}]}
        validate_plan(self.task, proposal)
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 0)

    def test_unknown_entry_does_not_guess_a_family(self):
        for entry in ("事件", "宏观", "混合", "行业", "产业链"):
            task = self.create(entry, "300033.SZ")
            self.assertEqual(series_request_shapes(task), [])
            self.rejects_before_auth(self.params, task)

    def test_invalid_codes_cannot_expand_scope_or_infer_asset_types(self):
        for code in ("600000.SH", "300033.SZ,600000.SH", "300033", "00700.HK", "300033.SZ "):
            self.rejects_before_auth(dict(self.params, codes=code))
        for entry, code in (("ETF", "300033.SZ"), ("个股", "510300.SH"), ("指数", "399001.SZ")):
            task = self.create(entry, code)
            self.assertEqual(series_request_shapes(task)[0]["available_codes"], [])
            self.rejects_before_auth(self.parameters(task), task)

    def test_unknown_cross_family_and_duplicate_indicators_fail_before_auth(self):
        for fields in ([{"indicator": "invented_revenue", "indiparams": [""]}],
                [{"indicator": "ths_pe_index", "indiparams": ["", "100", "100"]}],
                self.params["indipara"] * 2, [], "ths_pe_ttm_stock",
                [{"indicator": [], "indiparams": []}]):
            self.rejects_before_auth(dict(self.params, indipara=fields))

    def test_parameter_changes_and_private_keys_fail_before_auth(self):
        for item in ({"indicator": "ths_pe_ttm_stock", "indiparams": [""]},
                {"indicator": "ths_pe_ttm_stock", "indiparams": ["", 100]},
                {"indicator": "ths_pe_ttm_stock", "indiparams": ["", "100"], "otherparams": {"sys": {}}}):
            self.rejects_before_auth(dict(self.params, indipara=[item]))
        self.rejects_before_auth(dict(self.params, token="fixture-only"))

    def test_fill_and_index_constituent_settings_are_not_interchangeable(self):
        for settings in ({}, {"Fill": "Previous"}, {"Fill": "Blank", "block": "history"}):
            self.rejects_before_auth(dict(self.params, functionpara=settings))
        task = self.create("指数", "000300.SH")
        params = self.parameters(task)
        self.assertEqual(validate_series_request(task, "date_sequence", params), ["2026-09-29", "2026-09-30"])
        self.rejects_before_auth(dict(params, functionpara={"Fill": "Blank"}), task)

    def test_bad_and_future_dates_are_not_rewritten(self):
        for changes in ({"startdate": "20260929"}, {"startdate": "2026-02-30"}, {"enddate": "2026-10-03"},
                {"startdate": "2026-10-01"}, {"startdate": None}, {"enddate": "2026-09-30T15:00:00+08:00"}):
            self.rejects_before_auth(dict(self.params, **changes))

    def test_maximum_window_is_explicit_no_automatic_chunking(self):
        validate_source_request(self.task, "ifind", "date_sequence", dict(self.params, startdate="2025-10-01"))
        self.rejects_before_auth(dict(self.params, startdate="2025-09-30"))

    def test_missing_cutoff_missing_fields_and_extra_fields_do_not_dispatch(self):
        task = copy.deepcopy(self.task)
        task.pop("cutoff")
        self.rejects_before_auth(self.params, task)
        self.rejects_before_auth({key: value for key, value in self.params.items() if key != "indipara"})
        self.rejects_before_auth(dict(self.params, arbitrary=True))

    def test_valid_request_is_sent_unchanged_and_charged_once(self):
        payload = self.payload()
        with patch.object(self.collector, "_http_json", return_value=payload) as transport:
            record = self.collector.external(self.task, "ifind", "date_sequence", self.params,
                {"access_token": "fixture-only-access"})
        self.assertEqual(transport.call_count, 1)
        request = transport.call_args.args[1]
        self.assertTrue(request.full_url.endswith("/date_sequence"))
        self.assertEqual(json.loads(request.data), self.params)
        self.assertEqual(record["parameters"], self.params)
        self.assertEqual(record["snapshot"], payload)
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 1)
        self.assertFalse(record["series_contract"]["unit_verified"])
        self.assertFalse(record["series_contract"]["historical_vintage_verified"])
        self.assertEqual(record["source_check"], "待核验")
        self.assertNotIn("fixture-only-access", json.dumps(record))

    def test_all_families_and_all_fields_are_accepted_only_in_their_family(self):
        for entry, code in (("个股", "300033.SZ"), ("指数", "000300.SH"), ("ETF", "159915.SZ")):
            task = self.create(entry, code)
            params = self.parameters(task)
            params["indipara"] = [{"indicator": key, "indiparams": value[:]} for key, value in SERIES_FAMILIES[entry].items()]
            self.assertEqual(validate_source_request(task, "ifind", "date_sequence", params), params)

    def test_raw_record_cannot_be_promoted_by_fabricating_unit_and_publication(self):
        record = snapshot_record(self.task, "ifind", "date_sequence", self.params, self.payload())
        self.task["source_records"].append(record)
        spec = {"id": "raw_pe", "artifact_id": record["id"], "object": "300033.SZ",
            "record_locator": {"table": 0, "row": 0, "field": "ths_pe_ttm_stock"},
            "period": "2026-09-30", "published_at": "2026-09-30", "nature": "已实现事实",
            "raw_value": 12.34, "value": 12.34, "raw_unit": "倍", "unit": "倍", "conversion_factor": 1}
        with self.assertRaisesRegex(ContractError, "日期序列原始记录"):
            candidate_evidence(self.task, record, spec)

    def test_offline_mode_never_authenticates_even_a_valid_registered_request(self):
        collector = EvidenceCollector(offline=True)
        with patch.object(collector, "_resolve_ifind_access") as auth, self.assertRaises(ContractError):
            collector.external(self.task, "ifind", "date_sequence", self.params, {"refresh_token": "fixture-only"})
        auth.assert_not_called()
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 0)

    def test_business_no_data_spends_once_does_not_retry_or_switch_source(self):
        with patch.object(self.collector, "_http_json", return_value={"errorcode": -4001}) as transport, \
                patch.object(self.collector, "_tinyshare") as fallback, self.assertRaisesRegex(ContractError, "未返回数据"):
            self.collector.external(self.task, "ifind", "date_sequence", self.params, {"access_token": "fixture-only"})
        self.assertEqual(transport.call_count, 1)
        fallback.assert_not_called()
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 1)
        self.assertEqual(self.task["ledger"]["dispatches"][-1]["supplier_rejection_category"], "no_data")

    def test_empty_response_retains_gap_no_numeric_approval(self):
        record = snapshot_record(self.task, "ifind", "date_sequence", self.params, {"errorcode": 0, "tables": []})
        self.assertEqual(record["status"], "成功但为空")
        self.assertEqual(record["source_check"], "待核验")
        self.assertFalse(record["series_contract"]["numeric_evidence_registered"])

    def test_model_extraction_contract_cannot_mistake_raw_series_for_verified_fields(self):
        record = snapshot_record(self.task, "ifind", "date_sequence", self.params, self.payload())
        self.task["source_records"].append(record)
        contract = evidence_contract(self.task)
        source = next(item for item in contract["sources"] if item["artifact_id"] == record["id"])
        self.assertTrue(source["series_raw_only"])
        self.assertFalse(source["numeric_evidence_registered"])
        self.assertFalse(source["series_contract"]["unit_verified"])
        self.assertEqual(source["api_cells"]["cells"], [])
