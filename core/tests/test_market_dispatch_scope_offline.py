"""Request authority at App, host and provider boundaries; no real services."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, budget
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector, snapshot_record
from researchhelper.reasoning import validate_plan

ROOT = Path(__file__).resolve().parents[2]
IFIND = {"codes": "600000.SH", "indicators": "close", "startdate": "2026-09-29",
    "enddate": "2026-09-30", "functionpara": {"Currency": "RMB", "CPS": "1", "Fill": "Omit"}}
TINY = {"ts_code": "600000.SH", "fields": "ts_code,trade_date,close",
    "start_date": "20260929", "end_date": "20260930"}


class MarketDispatchScopeTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / "result/acceptance/market-dispatch-scope"
        base.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(prefix="isolated-", dir=base)
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        self.task = self.agent.create("核对600000.SH的收盘原值", entry="个股", objects=["600000.SH"],
            cutoff="2026-10-02T18:00:00+08:00", questions=[{"id": "q1", "question": "核对收盘原值", "kind": "fact"}])
        self.collector = EvidenceCollector(False)

    def rejected_before_dispatch(self, source, operation, parameters, task=None):
        task = task or self.task
        before = copy.deepcopy(task["ledger"])
        with patch.object(self.collector, "_resolve_ifind_access") as auth, \
                patch.object(self.collector, "_http_json") as http, \
                patch.object(self.collector, "_require_tinyshare") as sdk, \
                patch("socket.socket.connect", side_effect=AssertionError("External network forbidden")), \
                self.assertRaises(ContractError):
            self.collector.external(task, source, operation, parameters,
                {"refresh_token": "SYNTHETIC_REFRESH", "token": "SYNTHETIC_TINY"})
        auth.assert_not_called()
        http.assert_not_called()
        sdk.assert_not_called()
        self.assertEqual(task["ledger"], before)

    def test_ifind_rejects_out_of_scope_security_before_authentication(self):
        for code in ("600001.SH", "600000.SH,600001.SH", "600000.SH,600000.SH", "", "600000", "600000.SH "):
            with self.subTest(code=code):
                self.rejected_before_dispatch("ifind", "cmd_history_quotation", dict(IFIND, codes=code))

    def test_tinyshare_rejects_out_of_scope_security_before_sdk_import(self):
        for operation in ("daily", "fund_daily", "daily_basic"):
            parameters = dict(TINY, ts_code="600001.SH")
            if operation == "daily_basic":
                parameters["fields"] = "ts_code,trade_date,pe_ttm"
            with self.subTest(operation=operation):
                self.rejected_before_dispatch("tinyshare", operation, parameters)

    def test_ifind_rejects_unknown_or_duplicate_fields(self):
        for fields in ("close,vol", "close,close", "close,", "close,trade_date", "", ["close"]):
            with self.subTest(fields=fields):
                self.rejected_before_dispatch("ifind", "cmd_history_quotation", dict(IFIND, indicators=fields))

    def test_ifind_rejects_unregistered_currency_adjustment_and_fill(self):
        for settings in ({"Fill": "Blank"}, {"Currency": "USD", "CPS": "1", "Fill": "Omit"},
                {"Currency": "RMB", "CPS": "2", "Fill": "Omit"},
                {"Currency": "RMB", "CPS": "1", "Fill": "Blank"},
                {"Currency": "RMB", "CPS": 1, "Fill": "Omit"}):
            with self.subTest(settings=settings):
                self.rejected_before_dispatch("ifind", "cmd_history_quotation", dict(IFIND, functionpara=settings))

    def test_ifind_rejects_future_inverted_or_nonliteral_windows(self):
        for changes in ({"enddate": "2026-10-03"}, {"startdate": "2026-10-01"},
                {"startdate": "20260929"}, {"enddate": "2026-09-31"},
                {"enddate": "2026-09-30T15:00:00+08:00"}, {"startdate": None}):
            with self.subTest(changes=changes):
                self.rejected_before_dispatch("ifind", "cmd_history_quotation", dict(IFIND, **changes))

    def test_missing_keys_cutoff_and_extra_parameters_fail_before_dispatch(self):
        for parameters in ({"codes": "600000.SH"}, dict(IFIND, arbitrary=True)):
            self.rejected_before_dispatch("ifind", "cmd_history_quotation", parameters)
        task = copy.deepcopy(self.task)
        task.pop("cutoff")
        self.rejected_before_dispatch("ifind", "cmd_history_quotation", IFIND, task)

    def test_valid_ifind_request_is_sent_unchanged_and_recorded(self):
        payload = {"errorcode": 0, "tables": [{"thscode": "600000.SH", "time": ["2026-09-30"], "table": {"close": [10]}}]}
        with patch.object(self.collector, "_http_json", return_value=payload) as transport:
            record = self.collector.external(self.task, "ifind", "cmd_history_quotation", copy.deepcopy(IFIND),
                {"access_token": "SYNTHETIC_ACCESS"})
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(record["parameters"], IFIND)
        self.assertEqual(record["status"], "成功待核验")
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 1)
        self.assertEqual(record["snapshot"], payload)

    def test_model_plan_uses_the_same_duplicate_field_rule(self):
        proposal = {"questions": [{key: value for key, value in self.task["questions"][0].items() if key != "status"}],
            "data_requests": [{"id": "close", "source": "ifind", "operation": "cmd_history_quotation",
                "parameters": dict(IFIND, indicators="close,close"), "question_ids": ["q1"]}], "calculations": []}
        with self.assertRaises(ContractError):
            validate_plan(self.task, proposal)
        proposal["data_requests"][0]["parameters"] = copy.deepcopy(IFIND)
        self.assertEqual(len(validate_plan(self.task, proposal)), 1)

    def test_host_cannot_reuse_a_cached_out_of_scope_request(self):
        parameters = dict(IFIND, codes="600001.SH")
        record = snapshot_record(self.task, "ifind", "cmd_history_quotation", parameters,
            {"errorcode": 0, "tables": [{"thscode": "600001.SH", "time": ["2026-09-30"], "table": {"close": [11]}}]})
        record["data_need_ids"] = ["old"]
        self.task["source_records"].append(record)
        before = copy.deepcopy(self.task)
        with patch.object(self.agent.collector, "external") as dispatch, self.assertRaises(ContractError):
            self.agent._acquire_sources(self.task, [{"id": "new", "source": "ifind", "operation": "cmd_history_quotation",
                "parameters": parameters, "question_ids": ["q1"]}])
        dispatch.assert_not_called()
        self.assertEqual(self.task, before)

    def test_host_valid_request_keeps_question_binding(self):
        self.task.update(status="运行中", batch=1)
        self.task = self.agent.archive.save(self.task)
        record = snapshot_record(self.task, "ifind", "cmd_history_quotation", IFIND,
            {"errorcode": 0, "tables": []})
        with patch.object(self.agent.collector, "external", return_value=record) as dispatch:
            self.agent._acquire_sources(self.task, [{"id": "close", "source": "ifind", "operation": "cmd_history_quotation",
                "parameters": copy.deepcopy(IFIND), "question_ids": ["q1"]}])
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(self.task["source_records"][0]["question_ids"], ["q1"])
        self.assertEqual(self.task["source_records"][0]["data_need_ids"], ["close"])


if __name__ == "__main__":
    unittest.main()
