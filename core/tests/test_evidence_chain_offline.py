"""Independent original-source, field and applicability regression.

All provider responses are explicitly fabricated fixtures. No real account,
SDK, production Store or foreground application is accessed.
"""

import base64
import copy
import io
import json
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint, validate_evidence
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (CAPABILITIES, FIELD_REGISTRY, EvidenceCollector,
    current_snapshot_reference, direct_code_reference)
from researchhelper.reports import compose

ROOT = Path(__file__).resolve().parents[2]
PARAMS = {"ts_code": "600000.SH", "start_date": "20260719", "end_date": "20260719", "fields": "ts_code,trade_date,close,amount"}
IFIND_PARAMS = {"codes": "600000.SH", "indicators": "close", "startdate": "2026-07-19", "enddate": "2026-07-19",
                "functionpara": {"Currency": "RMB", "CPS": "1", "Fill": "Omit"}}


QUOTE_RESPONSE = {"errorcode": 0, "tables": [
    {"thscode": "600000.SH", "time": ["2026-07-19"], "table": {"close": [10]}}]}
DAILY_ROWS = [{"ts_code": "600000.SH", "trade_date": "20260719", "close": 10, "amount": 2}]


def isolated_response(event):
    # The tests supply replies locally. The actual acquisition code creates
    # the ledger binding from these fixture transport outcomes.
    event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)


def publication(at="2026-07-19"):
    return {"at": at, "basis": "独立验收材料所载发布日，不以下载时间推定", "precision": "日" if len(at) == 10 else "秒",
            "timezone": "Asia/Shanghai", "certainty": "已确认"}


def review(nature="已实现事实"):
    return {"question_ids": ["q1"], "allowed_natures": [nature], "conditions": ["同一主体、人民币、未复权及指定日期"],
            "reason": "用于原问题的披露值核查，不延伸为收益预测"}


class EvidenceChainTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="evidence-chain-", dir=ROOT / "result/acceptance")
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create("核验原值", objects=["材料示例公司", "600000.SH"], cutoff="2026-07-20T18:00:00+08:00",
                                      questions=[{"id": "q1", "question": "原值是多少", "kind": "fact"}])

    def snapshot(self, source="tinyshare", payload=None, params=None):
        response = payload or {"code": 0, "data": [{"ts_code": "600000.SH", "trade_date": "20260719", "close": 10, "amount": 2}]}
        result = self.agent.add_snapshot(self.task["id"], source, "daily" if source == "tinyshare" else "cmd_history_quotation",
                                          params or (PARAMS if source == "tinyshare" else IFIND_PARAMS), response)
        return result["source_records"][-1]

    def create_current(self):
        self.task = self.agent.create("核验600000.SH本次取得的原值", objects=["600000.SH"], research_mode="current",
            questions=[{"id": "q1", "question": "原值是多少", "kind": "fact"}])

    def acquired(self, source="tinyshare", rows=None):
        self.create_current()
        collector = EvidenceCollector(False)
        if source == "tinyshare":
            def receive(task, operation, parameters, token, timeout, event, provider):
                isolated_response(event)
                event["sdk_wire_requests_started"] = 1
                return copy.deepcopy(DAILY_ROWS if rows is None else rows)
            with patch.object(collector, "_require_tinyshare", return_value=object()), \
                    patch.object(collector, "_tinyshare_rows", side_effect=receive):
                record = collector.external(self.task, source, "daily", PARAMS, {"token": "fixture"})
        else:
            def receive(task, request, timeout, event):
                isolated_response(event)
                return copy.deepcopy(QUOTE_RESPONSE)
            with patch.object(collector, "_http_json", side_effect=receive):
                record = collector.external(self.task, source, "cmd_history_quotation", IFIND_PARAMS,
                                            {"access_token": "fixture"})
        self.task["source_records"].append(record)
        self.task = self.agent.archive.save(self.task)
        return record

    def current_spec(self, artifact, **overrides):
        locator = {"row": 0, "field": "close"}
        if artifact["source"] == "ifind":
            locator["table"] = 0
        result = self.spec(artifact, object="600000.SH", record_locator=locator,
            published_at=None, publication=dict(at=None, basis_ref=None, precision="未知",
                timezone="未知", certainty="未知", basis="当前取数回执与首次公开时间分开"),
            availability=current_snapshot_reference(self.task, artifact),
            direct_code_reference=direct_code_reference(self.task, artifact))
        result.pop("identity")
        self.assertIsNotNone(result["availability"])
        self.assertIsNotNone(result["direct_code_reference"])
        return dict(result, **overrides)

    def spec(self, artifact, **overrides):
        result = {"id": "close_value", "artifact_id": artifact["id"], "object": "材料示例公司", "record_locator": {"row": 0, "field": "close"},
            "identity": {"provider_code": "600000.SH", "object": "材料示例公司", "basis": "独立身份映射材料",
                         "valid_from": "2026-01-01", "valid_to": "2026-12-31"},
            "published_at": "2026-07-19", "publication": publication(), "period": "2026-07-19", "nature": "已实现事实",
            "raw_value": 10, "raw_unit": "元", "value": 10, "unit": "元", "conversion_factor": 1,
            "question_ids": ["q1"], "review": review()}
        result.update(overrides)
        return result

    def run_spec(self, spec):
        return self.agent.start(self.task["id"], {"evidence_specs": [spec], "answers": {"q1": {"answer": "核查原文或原始字段。", "refs": [spec["id"]]}}})

    def material(self, text="政策自2026年7月19日起生效。", **overrides):
        release = "发布日期：2026年7月19日。"
        payload = (release + "\n\n" + text).encode()
        task = self.agent.add_material(self.task["id"], "policy.txt", payload)
        record = task["materials"][-1]
        basis = dict(kind="material", artifact_id=record["id"], snapshot_hash=record["snapshot_hash"],
                     part="段落1", quote=release)
        spec = {"id": "policy_fact", "artifact_id": record["id"], "object": "材料示例公司", "record_locator": {"part": "段落2"},
                "text": text, "published_at": "2026-07-19", "publication": dict(publication(), basis_ref=basis), "period": "2026年7月",
                "nature": "合同政策条款", "unit": "不适用", "question_ids": ["q1"], "review": review("合同政策条款")}
        spec.update(overrides)
        return record, spec

    def directory(self, **overrides):
        value = {"issuer": "材料示例公司", "source": "独立公告验收材料", "source_id": "fixture-001", "title": "经营数据公告",
                 "published_at": "2026-07-19", "original_url": "https://example.invalid/original.txt",
                 "query_coverage": {"code": "600000.SH", "start": "2026-07-01", "end": "2026-07-20"}}
        value.update(overrides)
        return value

    def filing(self, directory=None, text="已实现营业收入100百万元。"):
        task = self.agent.add_filing(self.task["id"], directory or self.directory(), "original.txt", text.encode() if text else None)
        record = task["source_records"][-1]
        released = record["directory"]["published_at"]
        basis = dict(kind="directory", artifact_id=record["id"], snapshot_hash=record["hash"], field="published_at")
        spec = {"id": "revenue", "artifact_id": record["id"], "object": "材料示例公司", "record_locator": {"part": "段落1"},
                "text": text, "published_at": released, "publication": dict(publication(released), basis_ref=basis),
                "period": "2026年上半年", "nature": "已实现事实", "unit": "百万元", "raw_unit": "百万元", "raw_token": "100",
                "raw_value": 100, "value": 100, "conversion_factor": 1, "question_ids": ["q1"], "review": review()}
        return record, spec

    def test_tinyshare_original_cell_and_two_distinct_receipts(self):
        task = self.run_spec(self.current_spec(self.acquired()))
        self.assertEqual(task["status"], "已完成", task.get("error"))
        evidence = task["evidence"][0]
        self.assertEqual(evidence["source_receipt"]["component"], "EvidenceCollector")
        self.assertEqual(evidence["applicability_receipt"]["component"], "ResearchAnalyzer")
        self.assertEqual(task["ledger"]["used"]["data_requests"], 1)
        self.assertEqual(evidence["identity"]["mode"], "direct_provider_observation")
        self.assertIsNone(evidence["published_at"])
        self.assertFalse(task["synthetic"])

    def test_ifind_original_table_and_currency_definition(self):
        record = self.acquired("ifind")
        final = self.run_spec(self.current_spec(record))
        self.assertEqual(final["status"], "已完成", final.get("error"))
        self.assertEqual(final["evidence"][0]["unit"], "元")
        self.assertEqual(final["evidence"][0]["raw_value"], 10)

    def test_thousand_yuan_conversion_preserves_raw_value(self):
        spec = self.current_spec(self.acquired(), record_locator={"row": 0, "field": "amount"}, raw_value=2, raw_unit="千元", value=2000, conversion_factor=1000)
        final = self.run_spec(spec)
        self.assertEqual(final["status"], "已完成", final.get("error"))
        self.assertEqual(final["evidence"][0]["raw_value"], 2)
        self.assertEqual(final["evidence"][0]["value"], 2000)

    def test_bad_numeric_value_is_rejected_not_repaired(self):
        final = self.run_spec(self.current_spec(self.acquired(), value=11))
        self.assertEqual(final["status"], "部分完成", final.get("error"))
        self.assertEqual(final["evidence"], [])
        self.assertIn("换算", final["rejected_evidence"][0]["rejection"])

    def test_date_only_not_available_intraday(self):
        self.task["cutoff"] = "2026-07-19T10:00:00+08:00"
        self.agent.archive.save(self.task)
        _, spec = self.material()
        final = self.run_spec(spec)
        self.assertEqual(final["status"], "部分完成")
        rejection = final["rejected_evidence"][0]
        self.assertEqual(rejection["source_check"], "通过")
        self.assertEqual(rejection["applicability_check"], "不通过")
        self.assertIn("晚于研究截止时点", rejection["rejection"])

    def test_fetch_timestamp_not_substituted_for_publication(self):
        record = self.acquired()
        observed = record["obtained_at"]
        final = self.run_spec(self.current_spec(record, published_at=observed, publication=publication(observed)))
        self.assertEqual(final["status"], "部分完成")
        self.assertIn("首次公开", final["rejected_evidence"][0]["rejection"])

    def test_identity_mapping_cannot_strip_hong_kong_digits(self):
        record = self.snapshot()
        mapping = self.spec(record)["identity"]
        mapping["provider_code"] = "0600000.SH"
        final = self.run_spec(self.spec(record, identity=mapping))
        self.assertEqual(final["status"], "部分完成")
        self.assertIn("证券代码", final["rejected_evidence"][0]["rejection"])

    def test_response_date_outside_request_is_rejected(self):
        record = self.acquired(rows=[dict(DAILY_ROWS[0], trade_date="20260718")])
        final = self.run_spec(self.current_spec(record, period="2026-07-18"))
        self.assertEqual(final["status"], "部分完成")
        self.assertIn("请求范围", final["rejected_evidence"][0]["rejection"])

    def test_forecast_cannot_pass_realized_only_review(self):
        _, spec = self.material(text="管理层预计下期收入可能增长。", nature="管理层展望", review=review())
        final = self.run_spec(spec)
        self.assertEqual(final["status"], "部分完成")
        rejection = final["rejected_evidence"][0]
        self.assertEqual(rejection["source_check"], "通过")
        self.assertEqual(rejection["applicability_check"], "不通过")
        self.assertIn("预测", rejection["rejection"])

    def test_material_keeps_original_bytes_and_exact_paragraph(self):
        _, spec = self.material()
        final = self.run_spec(spec)
        self.assertEqual(final["status"], "已完成", final.get("error"))
        stored = final["evidence"][0]["artifact"]["snapshot"]
        self.assertIn("政策", base64.b64decode(stored["original_base64"]).decode())

    def test_duplicate_material_import_has_stable_content_identity(self):
        record, spec = self.material()
        self.material()
        stored = self.agent.archive.get(self.task["id"])
        self.assertEqual(len(stored["materials"]), 1)
        self.assertEqual(stored["materials"][0]["id"], record["id"])
        final = self.run_spec(spec)
        self.assertEqual(final["status"], "已完成", final.get("error"))

    def test_invented_excerpt_rejected(self):
        _, spec = self.material(text="收入稳定。")
        spec["text"] = "收入翻倍。"
        final = self.run_spec(spec)
        self.assertEqual(final["status"], "部分完成")
        self.assertIn("摘录", final["rejected_evidence"][0]["rejection"])

    def test_filing_directory_does_not_prove_body(self):
        _, spec = self.filing(text=None)
        spec.update(text="已实现营业收入100百万元。")
        final = self.run_spec(spec)
        self.assertEqual(final["status"], "部分完成")
        self.assertIn("只有目录", final["rejected_evidence"][0]["rejection"])

    def test_filing_original_numeric_revenue_is_recomputable(self):
        _, spec = self.filing()
        final = self.run_spec(spec)
        self.assertEqual(final["status"], "已完成", final.get("error"))
        self.assertEqual(final["evidence"][0]["value"], 100)
        self.assertIn("source_records", self.agent.export(final["id"])["content"])

    def test_same_original_reprints_share_one_event_group(self):
        original, _ = self.filing()
        repost, _ = self.filing(self.directory(source="另一转载渠道", source_id="repost-002", original_url="https://example.invalid/repost.txt"))
        self.assertEqual(original["event_group"], repost["event_group"])
        self.assertNotEqual(original["id"], repost["id"])

    def test_correction_before_cutoff_blocks_old_record(self):
        original, spec = self.filing()
        self.filing(self.directory(source_id="correction-002", published_at="2026-07-20T09:00:00+08:00",
                                  relation={"kind": "更正", "target": original["id"]}), "更正营业收入90百万元。")
        final = self.run_spec(spec)
        self.assertEqual(final["status"], "部分完成")
        self.assertIn("重新评估", final["rejected_evidence"][0]["rejection"])

    def test_future_correction_never_changes_frozen_past_report(self):
        original, spec = self.filing()
        final = self.run_spec(spec)
        before = self.agent.export(final["id"])
        self.filing(self.directory(source_id="correction-002", published_at="2026-07-21",
                                  relation={"kind": "更正", "target": original["id"]}), "更正营业收入90百万元。")
        self.assertEqual(self.agent.export(final["id"]), before)
        self.assertEqual(len(self.agent.archive.get(final["id"])["pending_source_records"]), 1)

    def test_rejected_private_query_does_not_enter_store(self):
        before = self.agent.archive.get(self.task["id"])
        with self.assertRaises(ContractError):
            self.snapshot(params=dict(PARAMS, token="unit-test-not-a-real-token"))
        self.assertEqual(self.agent.archive.get(self.task["id"]), before)

    def test_response_private_fields_removed_before_hashing(self):
        raw = {"code": 0, "access_token": "not-real", "data": [{"ts_code": "600000.SH", "trade_date": "20260719", "close": 10, "email": "private@example.invalid"}]}
        record = self.snapshot(payload=raw)
        self.assertNotIn("not-real", json.dumps(record))
        self.assertNotIn("private@example.invalid", json.dumps(record))
        self.assertEqual(record["hash"], fingerprint(record["snapshot"]))

    def test_arbitrary_pass_flags_do_not_make_real_evidence(self):
        from researchhelper.cases import evidence
        fake = evidence("bare", "材料示例公司", "未经原件定位的断言")
        fake.update(source="网络来源", nature="已实现事实")
        final = self.agent.start(self.task["id"], {"evidence": [fake], "answers": {"q1": {"answer": "断言", "refs": ["bare"]}}})
        self.assertEqual(final["status"], "部分完成")
        self.assertIn("归档", final["rejected_evidence"][0]["rejection"])

    def test_tampered_application_conditions_fail_report_recheck(self):
        final = self.run_spec(self.current_spec(self.acquired()))
        final["evidence"][0]["applicability_review"]["conditions"] = ["改写条件"]
        with self.assertRaises(ContractError):
            compose(final)

    def test_cross_task_snapshot_is_rejected(self):
        record = self.snapshot()
        other = self.agent.create("另一个问题", cutoff=self.task["cutoff"], questions=[{"question": "问题", "kind": "fact"}])
        final = self.agent.start(other["id"], {"evidence_specs": [self.spec(record)], "answers": {}})
        self.assertEqual(final["status"], "部分完成")
        self.assertIn("不存在", final["rejected_evidence"][0]["rejection"])

    def test_boolean_conversion_factor_is_not_a_numeric_contract(self):
        final = self.run_spec(self.current_spec(self.acquired(), conversion_factor=True))
        self.assertEqual(final["status"], "部分完成", final.get("error"))
        self.assertIn("换算倍数", final["rejected_evidence"][0]["rejection"])

    def test_malformed_locator_is_local_rejection_not_whole_task_crash(self):
        final = self.run_spec(self.spec(self.snapshot(), record_locator="猜测字段"))
        self.assertEqual(final["status"], "部分完成", final.get("error"))
        self.assertIn("结构化定位", final["rejected_evidence"][0]["rejection"])

    def test_private_host_payload_is_rejected_before_persistence(self):
        before = self.agent.archive.get(self.task["id"])
        with self.assertRaises(ContractError):
            self.agent.start(self.task["id"], {"credentials": {"access_token": "not-real"}})
        self.assertEqual(self.agent.archive.get(self.task["id"]), before)

    def test_verified_material_cannot_answer_an_unreviewed_other_question(self):
        from researchhelper.planning import question_specs
        _, spec = self.material()
        self.agent.revise_plan(self.task["id"], question_specs(self.task) + [{"id": "q2", "question": "另一主体的盈利是否改善", "kind": "fact"}], "增加独立核查")
        final = self.agent.start(self.task["id"], {"evidence_specs": [spec], "answers": {
            "q1": {"answer": "原文条款", "refs": [spec["id"]]}, "q2": {"answer": "盈利改善", "refs": [spec["id"]]}}})
        self.assertEqual(final["status"], "部分完成", final.get("error"))
        self.assertEqual(final["questions"][0]["status"], "已回答")
        self.assertEqual(final["questions"][1]["status"], "未回答")

    def test_empty_and_business_error_never_masquerade_as_valid_response(self):
        empty = self.snapshot(payload={"code": 0, "data": []})
        error = self.snapshot(payload={"code": 2002, "msg": "fixture business error", "data": [{"close": 10}]})
        self.assertEqual(empty["status"], "成功但为空")
        self.assertNotEqual(error["status"], "成功待核验")

    def test_offline_guard_runs_before_import_or_dispatch(self):
        before = copy.deepcopy(self.task["ledger"])
        with patch.dict(sys.modules, {"tinyshare": None}), patch("urllib.request.urlopen", side_effect=AssertionError("external call")):
            with self.assertRaises(ContractError):
                EvidenceCollector(True).external(self.task, "tinyshare", "daily", PARAMS, {"token": "test"})
        self.assertEqual(before, self.task["ledger"])

    def test_doc_registry_never_promotes_offline_sample_to_live_permission(self):
        self.run_spec(self.spec(self.snapshot()))
        for source in ("ifind", "tinyshare"):
            self.assertEqual(CAPABILITIES[source]["state"], "真实权限未验收")
        for operation in ("ifind.cmd_history_quotation", "tinyshare.daily", "tinyshare.fund_daily"):
            self.assertEqual(FIELD_REGISTRY[operation]["permission"], "未验收")
            self.assertEqual(FIELD_REGISTRY[operation]["live_samples"], [])

    def test_mocked_tinyshare_uses_isolated_token_timeout_and_ledger(self):
        fake_result = Mock()
        fake_result.to_dict.return_value = [{"ts_code": "600000.SH", "trade_date": "20260719", "close": 10}]
        client = Mock()
        client.daily.return_value = fake_result
        factory = Mock(return_value=client)
        with patch.dict(sys.modules, {"tinyshare": types.SimpleNamespace(pro_api=factory)}):
            record = EvidenceCollector(False).external(self.task, "tinyshare", "daily", PARAMS, {"token": "independent-fake-token"})
        factory.assert_called_once_with("independent-fake-token", timeout=30)
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 1)
        self.assertNotIn("independent-fake-token", json.dumps(record))

    def test_mocked_ifind_dispatch_checkpoint_and_no_silent_fallback(self):
        self.create_current()
        self.agent.offline = self.agent.collector.offline = False
        self.agent.source_credentials = {"ifind": {"access_token": "independent-fake-token"}}
        need = dict(id="daily_close", source="ifind", operation="cmd_history_quotation",
                    parameters=IFIND_PARAMS, question_ids=["q1"], selection_reason="核验收盘原值")
        def receive(task, request, timeout, event):
            self.assertEqual(request.get_header("Access_token"), "independent-fake-token")
            persisted = self.agent.archive.get(task["id"])
            self.assertEqual(persisted["ledger"]["used"]["data_requests"], 1)
            self.assertEqual(persisted["ledger"]["dispatches"][-1]["status"], "已派发")
            isolated_response(event)
            return copy.deepcopy(QUOTE_RESPONSE)
        with patch.object(self.agent.collector, "_http_json", side_effect=receive) as transport:
            self.task = self.agent.start(self.task["id"], {"data_requests": [need]}, defer_delivery=True)
            record = self.task["source_records"][0]
            final = self.run_spec(self.current_spec(record))
        self.assertEqual(final["status"], "已完成", final.get("error"))
        transport.assert_called_once()
        self.assertEqual(final["ledger"]["used"]["data_requests"], 1)
        self.assertEqual(final["source_records"][0]["data_need_ids"], ["daily_close"])
        self.assertNotIn("independent-fake-token", json.dumps(final))

    def test_unavailable_optional_acquisition_does_not_block_independent_answer(self):
        _, spec = self.material()
        from researchhelper.planning import question_specs
        proposed = question_specs(self.task) + [{"id": "extra", "question": "可选行情", "kind": "fact", "required": False}]
        self.agent.revise_plan(self.task["id"], proposed, "增加可选验证")
        need = {"id": "optional_close", "operation": "cmd_history_quotation", "parameters": IFIND_PARAMS, "question_ids": ["extra"]}
        final = self.agent.start(self.task["id"], {"data_requests": [need], "evidence_specs": [spec], "answers": {"q1": {"answer": "原文事实", "refs": [spec["id"]]}}})
        self.assertEqual(final["status"], "已完成", final.get("error"))
        self.assertTrue(final["optional_findings"])
        self.assertEqual(final["ledger"]["used"]["data_requests"], 0)

    def test_cancelled_provider_response_retains_spending_and_resumes_fixed_cutoff(self):
        self.agent.offline = self.agent.collector.offline = False
        self.agent.source_credentials = {"ifind": {"access_token": "fake-token"}}
        need = {"id": "daily_close", "operation": "cmd_history_quotation", "parameters": IFIND_PARAMS, "question_ids": ["q1"]}
        raw = {"errorcode": 0, "tables": [{"thscode": "600000.SH", "time": ["2026-07-19"], "table": {"close": [10]}}]}
        started, release = threading.Event(), threading.Event()
        def delayed(*_, **__):
            started.set()
            if not release.wait(5):
                raise RuntimeError("bounded synthetic barrier")
            return io.BytesIO(json.dumps(raw).encode())
        with patch("urllib.request.urlopen", side_effect=delayed):
            self.agent.start(self.task["id"], {"data_requests": [need]}, asynchronous=True)
            try:
                self.assertTrue(started.wait(5))
                cancelled = self.agent.cancel(self.task["id"])
                self.assertEqual(cancelled["ledger"]["used"]["data_requests"], 1)
            finally:
                release.set()
                self.agent.workers[self.task["id"]].join(5)
        stopped = self.agent.archive.get(self.task["id"])
        self.assertEqual(stopped["status"], "已取消")
        self.assertEqual(stopped["source_records"], [])
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(raw).encode())):
            resumed = self.agent.start(self.task["id"])
        self.assertEqual(resumed["ledger"]["used"]["data_requests"], 2)
        self.assertEqual(resumed["cutoff"], self.task["cutoff"])
        self.assertEqual(resumed["status"], "部分完成")

    def test_mocked_business_error_is_not_retried_or_relabelled_empty(self):
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b'{"errorcode":-999,"tables":[]}')) as call:
            with self.assertRaises(ContractError):
                EvidenceCollector(False).external(self.task, "ifind", "cmd_history_quotation", IFIND_PARAMS, {"access_token": "test"})
        self.assertEqual(call.call_count, 1)
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 1)
        self.assertEqual(self.task["ledger"]["dispatches"][0]["status"], "业务失败")

    def test_multiple_needs_for_one_snapshot_do_not_duplicate_dispatch(self):
        self.agent.offline = self.agent.collector.offline = False
        self.agent.source_credentials = {"ifind": {"access_token": "fake-token"}}
        need = {"id": "close_a", "operation": "cmd_history_quotation", "parameters": IFIND_PARAMS, "question_ids": ["q1"]}
        raw = {"errorcode": 0, "tables": [{"thscode": "600000.SH", "time": ["2026-07-19"], "table": {"close": [10]}}]}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(raw).encode())) as call:
            result = self.agent.start(self.task["id"], {"data_requests": [need, dict(need, id="close_b")]})
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result["source_records"][0]["data_need_ids"], ["close_a", "close_b"])

    def test_new_version_requires_fresh_receipts_and_rechecks_correction(self):
        original, spec = self.filing()
        final = self.run_spec(spec)
        before = self.agent.export(final["id"], 1)
        self.filing(self.directory(source_id="corrected", published_at="2026-07-21",
                                  relation={"kind": "更正", "target": original["id"]}), "更正营业收入90百万元。")
        updated = self.agent.update(final["id"], "2026-07-22T18:00:00+08:00", host_result={"evidence_specs": [spec], "answers": {}})
        self.assertEqual(updated["status"], "部分完成", updated.get("error"))
        self.assertIn("重新评估", updated["rejected_evidence"][0]["rejection"])
        self.assertEqual(self.agent.export(final["id"], 1), before)


if __name__ == "__main__":
    unittest.main()
