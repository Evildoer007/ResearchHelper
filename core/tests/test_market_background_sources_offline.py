"""Market-background source contracts; every provider response is fabricated."""

import json
import sys
import types
import unittest
from unittest.mock import Mock, patch

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError, budget, fingerprint, validate_evidence
from researchhelper.evidence import (CAPABILITIES, FIELD_REGISTRY, TINYSHARE_DOCS, EvidenceCollector,
    candidate_evidence, extract_material, material_record, snapshot_record, validate_tinyshare_request)


class MarketBackgroundSourceTests(unittest.TestCase):
    def setUp(self):
        self.task = {"id": "rh_market_background_fixture", "version": 1, "batch": 1, "input_hash": "fixture-only",
                     "cutoff": "2026-10-02T18:00:00+08:00", "objects": ["600000.SH"], "ledger": budget(),
                     "questions": [{"id": "q1", "question": "核对市场背景原值", "required": True}], "source_records": [], "materials": []}
        self.collector = EvidenceCollector(offline=False)
        self.params = {"ts_code": "600000.SH", "start_date": "20260929", "end_date": "20260930",
                       "fields": "ts_code,trade_date,pe,pb,total_mv,total_share"}
        self.text = "合成验收材料，非真实市场数据。600000.SH市场背景指标于2026年10月1日公布。"
        self.material = material_record(self.task, extract_material("publication.txt", self.text.encode()), self.text.encode())
        self.task["materials"].append(self.material)
        network = patch("socket.socket.connect", side_effect=AssertionError("External networking forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def snapshot(self, field, value, **changes):
        row = {"ts_code": "600000.SH", "trade_date": "20260930", field: value}
        row.update(changes)
        params = dict(self.params, fields="ts_code,trade_date," + field)
        record = snapshot_record(self.task, "tinyshare", "daily_basic", params, {"code": 0, "data": [row]})
        self.task["source_records"].append(record)
        return record

    def spec(self, artifact, field, raw_value, **changes):
        definition = FIELD_REGISTRY["tinyshare.daily_basic"]["fields"][field]
        spec = {"id": "market_" + field, "artifact_id": artifact["id"], "object": "600000.SH", "record_locator": {"row": 0, "field": field},
                "period": "2026-09-30", "nature": "已实现事实", "raw_value": raw_value, "raw_unit": definition["raw_unit"],
                "value": raw_value * definition["factor"], "unit": definition["unit"], "conversion_factor": definition["factor"],
                "question_ids": ["q1"], "published_at": "2026-10-01", "identity": {"provider_code": "600000.SH", "object": "600000.SH",
                    "basis": "任务明确指定单只证券代码", "valid_from": "2026-01-01", "valid_to": "2026-12-31"},
                "publication": {"at": "2026-10-01", "precision": "日", "timezone": "Asia/Shanghai", "certainty": "已确认",
                    "basis": "留存合成原文披露时间，非交易日推定", "basis_ref": {"artifact_id": self.material["id"],
                    "snapshot_hash": self.material["snapshot_hash"], "part": "段落1", "quote": self.text}},
                "review": {"question_ids": ["q1"], "allowed_natures": ["已实现事实"], "conditions": ["仅核验指定证券与观测日原值，不构成买入信号"],
                           "reason": "记录API字段及明确单位，估值指标不代替财报分析"}}
        spec.update(changes)
        return spec

    def approve(self, artifact, spec):
        candidate = candidate_evidence(self.task, artifact, spec)
        item = ResearchAnalyzer().assess(self.task, candidate, spec["review"], self.task["source_records"])
        validate_evidence(item, self.task)
        return item

    def test_daily_basic_registration_is_independent_and_does_not_claim_permissions(self):
        definition = FIELD_REGISTRY["tinyshare.daily_basic"]
        fields = definition["fields"]
        self.assertEqual(set(fields), {"pe", "pe_ttm", "pb", "ps", "ps_ttm", "volume_ratio", "turnover_rate", "turnover_rate_f",
                                       "dv_ratio", "dv_ttm", "total_share", "float_share", "free_share", "total_mv", "circ_mv"})
        self.assertFalse({"open", "high", "low", "close", "vol", "amount", "pct_chg"} & set(fields))
        self.assertEqual(definition["documentation"], "https://tushare.pro/document/2?doc_id=32")
        self.assertEqual(definition["permission"], "未验收")
        self.assertEqual(definition["live_samples"], [])
        self.assertIn("不是买入信号", definition["limitations"])
        self.assertIn("其他权益工具", fields["pb"]["definition"])
        self.assertIn("亏损时为空", fields["pe_ttm"]["definition"])
        for operation in ("daily", "fund_daily"):
            original = FIELD_REGISTRY["tinyshare." + operation]["fields"]
            self.assertEqual(original["amount"]["raw_unit"], "千元")
            self.assertEqual(original["amount"]["factor"], 1000)
            self.assertNotIn("pe", original)

    def test_all_registered_fields_retain_units_and_two_verification_receipts(self):
        for field, definition in FIELD_REGISTRY["tinyshare.daily_basic"]["fields"].items():
            with self.subTest(field=field):
                raw = 3.125
                artifact = self.snapshot(field, raw)
                item = self.approve(artifact, self.spec(artifact, field, raw))
                self.assertEqual(item["raw_value"], raw)
                self.assertEqual(item["raw_unit"], definition["raw_unit"])
                self.assertEqual(item["value"], raw * definition["factor"])
                self.assertEqual(item["unit"], definition["unit"])
                self.assertEqual(item["field_definition"]["registry"], "tinyshare.daily_basic")
                self.assertEqual(item["artifact"]["parameters"], artifact["parameters"])
                self.assertEqual(item["source_receipt"]["component"], "EvidenceCollector")
                self.assertEqual(item["applicability_receipt"]["component"], "ResearchAnalyzer")
                self.assertEqual(item["period"], "2026-09-30")

    def test_nonzero_drift_missing_units_and_wrong_conversion_are_rejected(self):
        artifact = self.snapshot("total_mv", 2.5)
        original = self.spec(artifact, "total_mv", 2.5)
        for changes in ({"raw_value": 2.500000001}, {"value": 25000.000001}, {"value": 0}, {"raw_unit": "元"},
                        {"unit": "百万元"}, {"conversion_factor": 1}, {"conversion_factor": True}, {"raw_unit": None}):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                self.approve(artifact, dict(original, **changes))

    def test_missing_pe_is_never_promoted_to_zero_and_negative_pb_is_preserved(self):
        for empty in (None, "", "nan"):
            artifact = self.snapshot("pe", empty)
            with self.subTest(empty=empty), self.assertRaises(ContractError):
                self.approve(artifact, self.spec(artifact, "pe", 0))
        artifact = self.snapshot("pb", -1.5)
        item = self.approve(artifact, self.spec(artifact, "pb", -1.5))
        self.assertEqual(item["value"], -1.5)
        self.assertNotIn("recommendation", item)

    def test_wrong_identity_future_date_and_cross_task_are_rejected(self):
        for changes in ({"ts_code": "600001.SH"}, {"trade_date": "20260928"}, {"trade_date": "20261003"},
                        {"trade_date": "20260230"}, {"trade_date": "2026-09-30T15:00:00+08:00"}):
            artifact = self.snapshot("pe", 10, **changes)
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                self.approve(artifact, self.spec(artifact, "pe", 10))
        artifact = self.snapshot("pe", 10)
        artifact["task_id"] = "other-task"
        with self.assertRaises(ContractError):
            self.approve(artifact, self.spec(artifact, "pe", 10))

    def test_trade_date_and_obtained_at_do_not_prove_first_publication(self):
        artifact = self.snapshot("pe", 10)
        for field in ("trade_date", "obtained_at"):
            spec = self.spec(artifact, "pe", 10, published_at="2026-09-30")
            spec["publication"].update(at="2026-09-30", basis="接口记录日期不能自签为公开日",
                basis_ref={"kind": "api", "artifact_id": artifact["id"], "snapshot_hash": artifact["hash"], "field": field})
            with self.subTest(field=field), self.assertRaises(ContractError):
                self.approve(artifact, spec)

    def test_business_error_cannot_be_relabelled_as_valid_data(self):
        artifact = self.snapshot("pe", 10)
        artifact["snapshot"]["code"] = -200
        artifact["hash"] = fingerprint(artifact["snapshot"])
        artifact["status"] = "成功待核验"
        with self.assertRaisesRegex(ContractError, "业务错误"):
            self.approve(artifact, self.spec(artifact, "pe", 10))

    def sdk(self, operation, rows=None, exception=None):
        call = Mock(side_effect=exception) if exception else Mock(return_value=Mock(to_dict=Mock(return_value=rows)))
        factory = Mock(return_value=types.SimpleNamespace(**{operation: call}))
        return types.SimpleNamespace(pro_api=factory), call

    def test_sdk_dispatches_daily_and_daily_basic_without_global_login_or_tushare(self):
        for operation, fields in (("daily", "close"), ("fund_daily", "close"), ("daily_basic", "pe")):
            with self.subTest(operation=operation):
                params = dict(self.params, fields="ts_code,trade_date," + fields)
                provider, call = self.sdk(operation, [{"ts_code": "600000.SH", "trade_date": "20260930", fields: 10}])
                before = self.task["ledger"]["used"]["data_requests"]
                with patch.dict(sys.modules, {"tinyshare": provider, "tushare": None}):
                    record = self.collector.external(self.task, "tinyshare", operation, params, {"token": "fabricated-private-token"})
                provider.pro_api.assert_called_once_with("fabricated-private-token", timeout=30)
                call.assert_called_once_with(**params)
                self.assertEqual(record["operation"], operation)
                self.assertEqual(record["status"], "成功待核验")
                self.assertEqual(self.task["ledger"]["used"]["data_requests"], before + 1)
                self.assertNotIn("fabricated-private-token", json.dumps(record) + json.dumps(self.task))

    def test_sdk_nan_pe_stays_missing_empty_data_and_failures_are_distinct(self):
        provider, _ = self.sdk("daily_basic", [{"ts_code": "600000.SH", "trade_date": "20260930", "pe": float("nan")}])
        with patch.dict(sys.modules, {"tinyshare": provider}):
            record = self.collector.external(self.task, "tinyshare", "daily_basic", self.params, {"token": "fabricated-only"})
        self.assertIsNone(record["snapshot"]["data"][0]["pe"])
        provider, _ = self.sdk("daily_basic", [])
        with patch.dict(sys.modules, {"tinyshare": provider}):
            empty = self.collector.external(self.task, "tinyshare", "daily_basic", self.params, {"token": "fabricated-only"})
        self.assertEqual(empty["status"], "成功但为空")
        permission = type("TinySharePermissionError", (Exception,), {})
        for error in (permission("未开通接口 fabricated-only"), RuntimeError("业务失败 fabricated-only")):
            provider, call = self.sdk("daily_basic", exception=error)
            before = self.task["ledger"]["used"]["data_requests"]
            with patch.dict(sys.modules, {"tinyshare": provider}), self.assertRaises(ContractError) as caught:
                self.collector.external(self.task, "tinyshare", "daily_basic", self.params, {"token": "fabricated-only"})
            self.assertEqual(call.call_count, 1)
            self.assertEqual(self.task["ledger"]["used"]["data_requests"], before + 1)
            self.assertNotIn("fabricated-only", str(caught.exception))

    def test_invalid_requests_fail_before_sdk_or_budget_dispatch(self):
        provider = types.SimpleNamespace(pro_api=Mock())
        mutations = ({"ts_code": ""}, {"ts_code": "600000.SH,600001.SH"}, {"ts_code": "全市场"},
                     {"ts_code": "920001.BJ"},
                     {"start_date": "20260931"}, {"end_date": "20261003"}, {"start_date": "20260930", "end_date": "20260929"},
                     {"fields": "pe"}, {"fields": "ts_code,trade_date"}, {"fields": "ts_code,trade_date,close"},
                     {"fields": "ts_code,trade_date,pe,pe"}, {"fields": "ts_code,trade_date,unknown"}, {"trade_date": "20260930"})
        for changes in mutations:
            with self.subTest(changes=changes), patch.dict(sys.modules, {"tinyshare": provider}), self.assertRaises(ContractError):
                self.collector.external(self.task, "tinyshare", "daily_basic", dict(self.params, **changes), {"token": "fabricated-only"})
        provider.pro_api.assert_not_called()
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 0)
        self.assertEqual(validate_tinyshare_request(self.task, "daily_basic", self.params), ["2026-09-29", "2026-09-30"])

    def test_official_date_format_is_required_without_rewriting_request_or_dispatch(self):
        for operation, field in (("daily", "close"), ("fund_daily", "close"), ("daily_basic", "pe")):
            params = dict(self.params, fields="ts_code,trade_date," + field)
            before = fingerprint(params)
            self.assertEqual(validate_tinyshare_request(self.task, operation, params), ["2026-09-29", "2026-09-30"])
            self.assertEqual(fingerprint(params), before)
            provider = types.SimpleNamespace(pro_api=Mock())
            for key, invalid in (("start_date", "2026-09-29"), ("end_date", "2026-09-30"),
                                 ("start_date", "20260230"), ("start_date", "2026093"),
                                 ("end_date", "20260930T150000")):
                with self.subTest(operation=operation, key=key, invalid=invalid), \
                     patch.dict(sys.modules, {"tinyshare": provider}), self.assertRaises(ContractError):
                    self.collector.external(self.task, "tinyshare", operation, dict(params, **{key: invalid}), {"token": "fabricated-only"})
            provider.pro_api.assert_not_called()
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 0)
        self.assertEqual(self.task["ledger"]["dispatches"], [])

    def test_announcement_endpoint_name_is_not_registered_directory_evidence(self):
        self.assertIn("report_query", CAPABILITIES["ifind"]["endpoints"])
        self.assertNotIn("ifind.report_query", FIELD_REGISTRY)
        self.assertEqual(CAPABILITIES["ifind"]["state"], "真实权限未验收")
        self.assertEqual(TINYSHARE_DOCS["daily_basic"], FIELD_REGISTRY["tinyshare.daily_basic"]["documentation"])
