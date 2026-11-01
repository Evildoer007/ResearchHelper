"""Independent, offline tests of the task-bound semantic financial adapter."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
import researchhelper.engine as engine_module
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (EvidenceCollector, _source_extract, acquisition_channels,
    data_request_contract, snapshot_record, validate_source_request)
from researchhelper.financial_sources import (FINANCIAL_QUERY_FIELDS, FINANCIAL_QUERY_PERIODS,
    financial_query_response, validate_financial_query)
from researchhelper.output_schema import planning_output_schema
from researchhelper.planning import question_specs
from researchhelper.reasoning import evidence_contract, source_views, validate_plan


class FinancialSemanticQueryTests(unittest.TestCase):
    def setUp(self):
        output = Path.cwd() / "result/acceptance"
        output.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="semantic-financial-unit-", dir=output)
        self.addCleanup(self.temp.cleanup)
        origin = getattr(engine_module, "__frozen_origin__", None)
        resources = Path(origin.split("!/PYZ.pyz/")[0]).parents[2] if origin else None
        self.agent = ResearchAgent(ResearchArchive(Path(self.temp.name) / "store"), resources, offline=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create(
            "核对600000.SH2026年半年度营业收入与报表净利润。", objects=["600000.SH"],
            entry="个股", cutoff="2026-10-15T18:00:00+08:00",
            questions=[{"id": "q1", "question": "核对600000.SH2026年半年度营业收入与报表净利润。",
                        "kind": "fact", "required": True, "evidence_needs": ["营业收入", "净利润"]}])
        self.params = {"searchstring": "600000.SH2026年半年报营业收入、净利润", "searchtype": "stock"}
        self.payload = {"errorcode": 0, "errmsg": "", "tables": [{"table": {
            "股票代码": ["600000.SH"], "股票简称": ["合成证券"],
            "营业收入[20260630]": [1000], "归属于母公司所有者的净利润[20260630]": [200]}}]}
        guard = patch("socket.socket.connect", side_effect=AssertionError("Network forbidden"))
        guard.start()
        self.addCleanup(guard.stop)

    def plan(self, params=None):
        return {"questions": question_specs(self.task), "calculations": [],
            "data_requests": [{"id": "financial_query", "source": "ifind",
                "operation": "smart_stock_picking", "parameters": params or self.params,
                "question_ids": ["q1"], "selection_reason": "先核对结构化返回的实际财务字段"}]}

    def retain(self, payload=None):
        return snapshot_record(self.task, "ifind", "smart_stock_picking", self.params,
                               self.payload if payload is None else payload)

    def test_single_declared_security_and_explicit_period(self):
        result = validate_financial_query(self.task, self.params)
        self.assertEqual(result, {"code": "600000.SH", "period": "20260630", "fields": ["营业收入", "净利润"]})
        for period, suffix in FINANCIAL_QUERY_PERIODS.items():
            params = dict(self.params, searchstring="600000.SH2025年" + period + "营业收入")
            self.assertEqual(validate_financial_query(self.task, params)["period"], "2025" + suffix)

    def test_exact_scope_does_not_require_narrow_entry(self):
        for entry in ("个股", "行业", "事件", "混合"):
            with self.subTest(entry=entry):
                task = dict(self.task, entry=entry)
                validate_financial_query(task, self.params)

    def test_free_form_screens_and_implicit_latest_are_rejected(self):
        for query in ("低市盈率", "600000.SH最新营业收入", "600000.SH2026年半年报营业收入大于100",
                      "600000.SH2026年半年报营业收入、600001.SH净利润",
                      "600000.SH2026年半年报营业收入、营业收入", "600000.SH2026年半年报营业收入 排名",
                      "600000.SH2026年半年报总市值", "600000.SH2026年半年报", "2026年半年报营业收入"):
            with self.subTest(query=query), self.assertRaises(ContractError):
                validate_financial_query(self.task, dict(self.params, searchstring=query))

    def test_no_other_securities_funds_hk_or_index_scope(self):
        for code in ("600001.SH", "510300.SH", "00700.HK", "000300.SH", "600000", "000001.SZ"):
            with self.subTest(code=code), self.assertRaises(ContractError):
                validate_financial_query(self.task, dict(self.params, searchstring=code + "2026年半年报营业收入"))

    def test_preflight_may_check_syntax_but_send_requires_identity(self):
        task = dict(self.task, objects=[], question="核对一家企业的营业收入")
        validate_source_request(task, "ifind", "smart_stock_picking", self.params, require_identity=False)
        with self.assertRaises(ContractError):
            validate_source_request(task, "ifind", "smart_stock_picking", self.params)

    def test_bad_parameters_and_future_period_block_before_network(self):
        values = [dict(self.params, searchtype="fund"), dict(self.params, searchtype=True),
                  {"searchstring": self.params["searchstring"]}, dict(self.params, url="https://example.invalid"),
                  dict(self.params, searchstring=1), dict(self.params, searchstring="600000.SH2026年年报营业收入")]
        collector = EvidenceCollector(offline=False)
        with patch.object(collector, "_resolve_ifind_access", side_effect=AssertionError("No auth")):
            for params in values:
                with self.subTest(params=params), self.assertRaises(ContractError):
                    collector.external(self.task, "ifind", "smart_stock_picking", params, {})
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 0)

    def test_literal_profit_mismatch_preserved_and_not_approved(self):
        original = copy.deepcopy(self.payload)
        record = self.retain()
        result = record["financial_query_contract"]
        self.assertEqual(record["snapshot"], original)
        self.assertEqual(result["unmatched_fields"], ["净利润"])
        self.assertEqual(result["matched_columns"]["营业收入"], ["营业收入[20260630]"])
        self.assertEqual(result["matched_columns"]["净利润"], [])
        self.assertFalse(result["numeric_evidence_registered"])
        for key in ("unit_verified", "currency_verified", "accounting_scope_verified", "historical_vintage_verified"):
            self.assertFalse(result[key])
        self.assertEqual(record["source_check"], "待核验")

    def test_parent_profit_can_be_requested_without_renaming(self):
        params = dict(self.params, searchstring="600000.SH2026年半年报归属于母公司所有者的净利润")
        result = financial_query_response(self.task, params, self.payload)
        self.assertEqual(result["unmatched_fields"], [])
        self.assertEqual(set(result["matched_columns"]), {"归属于母公司所有者的净利润"})
        self.assertFalse(result["numeric_evidence_registered"])

    def test_different_period_or_deducted_profit_does_not_cover_requested_field(self):
        for field in ("营业收入[20250630]", "扣除非经常性损益后的净利润[20260630]", "营业总收入[20260630]"):
            payload = {"errorcode": 0, "tables": [{"table": {"股票代码": ["600000.SH"], field: [123]}}]}
            result = self.retain(payload)["financial_query_contract"]
            self.assertEqual(result["unmatched_fields"], ["营业收入", "净利润"])

    def test_ambiguous_subjects_and_shifted_rows_are_retained_but_unusable(self):
        for change in ({"股票代码": ["600001.SH"]}, {"股票代码": ["600000.SH", "600000.SH"]},
                       {"营业收入[20260630]": []}, {"股票代码": [None]}):
            payload = copy.deepcopy(self.payload)
            payload["tables"][0]["table"].update(change)
            record = self.retain(payload)
            self.assertEqual(record["status"], "返回结构异常")
            self.assertEqual(record["snapshot"], payload)
            self.assertIn("response_issue", record["financial_query_contract"])

    def test_empty_missing_and_nonfinite_response_never_becomes_valid_number(self):
        empty = self.retain({"errorcode": 0, "tables": []})
        self.assertEqual(empty["status"], "成功但为空")
        for value in (True, {}, [1]):
            payload = copy.deepcopy(self.payload)
            payload["tables"][0]["table"]["营业收入[20260630]"] = [value]
            self.assertEqual(self.retain(payload)["status"], "返回结构异常")
        for value in (float("inf"), float("nan")):
            payload = copy.deepcopy(self.payload)
            payload["tables"][0]["table"]["营业收入[20260630]"] = [value]
            with self.assertRaises(ContractError):
                financial_query_response(self.task, self.params, payload)

    def test_error_status_cannot_be_approved_by_flipping_label(self):
        payload = dict(self.payload, errorcode=-4001)
        record = self.retain(payload)
        self.assertEqual(record["status"], "业务失败待按官方错误定义核查")
        self.assertFalse(record["financial_query_contract"]["numeric_evidence_registered"])

    def test_repeated_financial_column_is_not_selected_from_first_table(self):
        payload = copy.deepcopy(self.payload)
        payload["tables"].append(copy.deepcopy(payload["tables"][0]))
        payload["tables"][1]["table"]["营业收入[20260630]"] = [999]
        record = self.retain(payload)
        self.assertEqual(record["status"], "返回结构异常")
        self.assertEqual(len(record["snapshot"]["tables"]), 2)

    def test_null_is_retained_and_marked_missing_not_changed_to_zero(self):
        payload = copy.deepcopy(self.payload)
        payload["tables"][0]["table"]["营业收入[20260630]"] = [None]
        record = self.retain(payload)
        self.assertIsNone(record["snapshot"]["tables"][0]["table"]["营业收入[20260630]"][0])
        self.assertEqual(record["financial_query_contract"]["missing_values"], ["营业收入"])

    def test_extract_rejects_even_fabricated_unit_and_approval_labels(self):
        record = self.retain()
        record.update(source_check="通过", applicability_check="通过")
        item = {"artifact": record, "snapshot": record["snapshot"], "snapshot_hash": record["hash"],
                "record_locator": {"table": 0, "row": 0, "field": "营业收入[20260630]"},
                "unit": "元", "raw_unit": "元", "value": 1000}
        with self.assertRaisesRegex(ContractError, "财务语义查询只保留原始返回字段"):
            _source_extract(item, self.task)

    def test_model_plan_and_host_request_use_same_validator(self):
        validate_plan(self.task, self.plan())
        validate_source_request(self.task, "ifind", "smart_stock_picking", self.params)
        bad = dict(self.params, searchstring="600000.SH最新营业收入")
        with self.assertRaises(ContractError):
            validate_plan(self.task, self.plan(bad))
        with self.assertRaises(ContractError):
            validate_source_request(self.task, "ifind", "smart_stock_picking", bad)

    def test_strict_plan_carries_same_business_request(self):
        # The strict question container is a representation, not a scope edit.
        schema = planning_output_schema(self.task)
        from researchhelper.output_schema import _planned_request_schemas, _matches
        request = self.plan()["data_requests"][0]
        self.assertTrue(any(_matches(request, choice) for choice in _planned_request_schemas(self.task)))
        matching = [choice for choice in _planned_request_schemas(self.task)
                    if _matches(request, choice)]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["properties"]["operation"]["enum"], ["smart_stock_picking"])
        self.assertIn("data_requests", schema["properties"])

    def test_readonly_tools_and_sources_expose_unmatched_fields_not_approved_cells(self):
        self.task["source_records"] = [self.retain()]
        before = copy.deepcopy(self.task)
        tools = data_request_contract(self.task)
        tool = next(item for item in tools["tools"] if item["operation"] == "smart_stock_picking")
        self.assertEqual(set(tool["query_field_names"]), set(FINANCIAL_QUERY_FIELDS))
        self.assertEqual(tool["numeric_fields"], [])
        self.assertFalse(tool["numeric_evidence_registered"])
        views = source_views(self.task)
        self.assertEqual(views[0]["financial_query_contract"]["unmatched_fields"], ["净利润"])
        source = evidence_contract(self.task, views)["sources"][0]
        self.assertTrue(source["financial_raw_only"])
        self.assertFalse(source["numeric_evidence_registered"])
        self.assertEqual(source.get("api_cells", {}).get("cells", []), [])
        self.assertEqual(self.task, before)

    def test_offline_mode_blocks_actual_send(self):
        with self.assertRaisesRegex(ContractError, "离线验收已阻断"):
            EvidenceCollector(offline=True).external(self.task, "ifind", "smart_stock_picking", self.params, {})
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 0)

    def test_registered_operation_remains_on_ifind_without_automatic_fallback(self):
        channels = acquisition_channels()
        self.assertEqual(channels["default_source"], "ifind")
        self.assertFalse(channels["automatic_fallback"])
        primary = next(item for item in channels["channels"] if item["id"] == "ifind")
        self.assertIn("smart_stock_picking", primary["operations"])


if __name__ == "__main__":
    unittest.main()
