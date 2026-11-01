"""Actual collector expiry must stop fresh dispatch without blocking unrelated sources."""

import copy
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.reasoning import supplement_request_blocked, validate_supplement

ROOT = Path(__file__).resolve().parents[2]


class ExpiredSourceWorkflowTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="rh-expiry-workflow-", dir=ROOT / "result/acceptance")
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=False,
            source_credentials={"tinyshare": {"token": "fake-expired-only"}, "ifind": {"access_token": "fake-only"}})
        self.task = self.agent.create("核对600000.SH价格及独立日期问题", objects=["600000.SH"],
            cutoff="2026-09-30T18:00:00+08:00", questions=[
                {"id": "q1", "question": "核对价格", "kind": "fact"},
                {"id": "q2", "question": "独立核对另一窗口", "kind": "fact", "group": "independent"}])
        self.task["batch"] = 1
        self.task["status"] = "运行中"
        self.agent.archive.save(self.task)

    def need(self, source="tinyshare", q="q1", ident="daily", start="20260929"):
        return {"id": ident, "source": source,
            "operation": "daily" if source == "tinyshare" else "cmd_history_quotation",
            "parameters": ({"ts_code": "600000.SH", "start_date": start, "end_date": "20260930", "fields": "ts_code,trade_date,close"}
                if source == "tinyshare" else {"codes": "600000.SH", "startdate": "2026-09-29", "enddate": "2026-09-30",
                    "indicators": "close", "functionpara": {"Currency": "RMB", "CPS": "1", "Fill": "Omit"}}),
            "question_ids": [q], "selection_reason": "显式登记核对原值"}

    def expire(self):
        need = self.need()
        self.task["host_result"] = {"data_requests": [need]}
        self.agent.archive.save(self.task)
        class TinySharePermissionError(Exception):
            pass
        client = Mock()
        client.daily.side_effect = TinySharePermissionError("token expired fake-expired-only")
        provider = Mock(return_value=client)
        with patch.dict(sys.modules, {"tinyshare": types.SimpleNamespace(pro_api=provider)}):
            self.agent._acquire_sources(self.task, [need])
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(client.daily.call_count, 1)
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 1)
        self.assertEqual(self.task["ledger"]["dispatches"][-1]["failure_code"], "credential_expired")
        return need

    def test_collector_expiry_blocks_same_source_and_same_question_switch(self):
        self.expire()
        self.assertTrue(supplement_request_blocked(self.task, "tinyshare", ["q1"]))
        self.assertTrue(supplement_request_blocked(self.task, "ifind", ["q1"]))
        self.assertTrue(supplement_request_blocked(self.task, "tinyshare", ["q2"]))
        self.assertFalse(supplement_request_blocked(self.task, "ifind", ["q2"]))

    def test_new_window_does_not_circumvent_expiry_or_spend(self):
        self.expire()
        with patch.object(self.agent.collector, "external", side_effect=AssertionError("Expired request dispatched")):
            self.agent._acquire_sources(self.task, [self.need(ident="different_window", start="20260928")])
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 1)
        self.assertEqual(len(self.task["ledger"]["dispatches"]), 1)
        self.assertIn("不继续派发", "".join(self.task["gaps"]))

    def test_preplanned_same_question_switch_is_not_dispatched(self):
        self.expire()
        with patch.object(self.agent.collector, "external", side_effect=AssertionError("Automatic switch dispatched")):
            self.agent._acquire_sources(self.task, [self.need(source="ifind", ident="switch")])
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 1)

    def test_expiry_blocks_supplement_proposal_even_with_new_id_and_parameters(self):
        self.expire()
        self.task["questions"][0]["status"] = "未回答"
        for source in ("tinyshare", "ifind"):
            proposal = {"question_ids": ["q1"], "reason": "还未取到原值", "expected_change": "核对原值",
                "data_requests": [self.need(source=source, ident="new_id", start="20260928")],
                "calculations": [], "artifact_ids": [], "stop_reason": ""}
            with self.subTest(source=source), self.assertRaisesRegex(ContractError, "鉴权权限失败"):
                validate_supplement(self.task, proposal)

    def test_expiry_status_without_failure_code_is_still_recognized(self):
        self.expire()
        self.task["ledger"]["dispatches"][-1].pop("failure_code")
        self.assertTrue(supplement_request_blocked(self.task, "tinyshare", ["q1"]))

    def test_restart_preserves_failure_and_unspent_new_request(self):
        self.expire()
        self.agent.archive.save(self.task)
        agent = ResearchAgent(ResearchArchive(self.agent.archive.root), ROOT, offline=False)
        task = agent.archive.get(self.task["id"])
        with patch.object(agent.collector, "external", side_effect=AssertionError("Restart reset failure")):
            agent._acquire_sources(task, [self.need(ident="after_restart", start="20260928")])
        self.assertEqual(task["ledger"]["used"]["data_requests"], 1)

    def test_independent_source_branch_remains_available(self):
        self.expire()
        need = self.need(source="ifind", q="q2", ident="independent")
        # No live call; observe that only the independent branch reaches the collector.
        with patch.object(self.agent.collector, "external", side_effect=ContractError("模拟独立接口缺口")) as external:
            self.agent._acquire_sources(self.task, [need])
        external.assert_called_once()
        self.assertEqual(external.call_args.args[1:3], ("ifind", "cmd_history_quotation"))

    def test_unbound_expiry_cannot_authorize_independent_dispatch(self):
        self.task["ledger"]["dispatches"].append({"failure_code": "credential_expired", "status": "授权已过期"})
        self.assertTrue(supplement_request_blocked(self.task, "ifind", ["q2"]))

    def test_expiry_does_not_delete_existing_evidence_or_refund_budget(self):
        self.expire()
        self.task["evidence"] = [{"id": "already_checked", "text": "既有核验内容"}]
        before = copy.deepcopy(self.task["ledger"])
        with patch.object(self.agent.collector, "external", side_effect=AssertionError("Expired request dispatched")):
            self.agent._acquire_sources(self.task, [self.need(ident="new", start="20260928")])
        self.assertEqual(self.task["ledger"], before)
        self.assertEqual(self.task["evidence"][0]["id"], "already_checked")
