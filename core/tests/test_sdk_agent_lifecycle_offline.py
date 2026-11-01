"""Actual ResearchAgent, SDK wire reservations, SQLite and cancellation.

Only the provider implementation is replaced. The production engine owns
checkpoints, cancellation, result adoption and persistence throughout.
"""

import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from tests import test_sdk_wire_budget_offline as fixtures

ROOT = Path(__file__).resolve().parents[2]


def fixture_environment(fixture):
    return patch.dict(os.environ, {"SDK_LOCAL_ENDPOINT": f"http://127.0.0.1:{fixture.server.server_port}",
        "SDK_LOCAL_MODE": fixture.mode, "NO_PROXY": "*"})


def make_agent(archive):
    agent = ResearchAgent(archive, ROOT, offline=False, isolate_sources=True,
        source_credentials={"tinyshare": {"token": "WIRE_PRIVATE_FIXTURE"}})
    agent.collector = fixtures.FixtureCollector(offline=False, journal=agent._journal, active=agent._check_run_control)
    return agent


def make_task(agent, fixture, requests=100):
    task = agent.create("合成取数呈现核对", entry="ETF", objects=["510300.SH"],
        cutoff="2026-10-02T18:00:00+08:00", limits={"data_requests": requests},
        questions=[{"id": "q1", "question": "核对日线原始响应", "kind": "fact"}])
    host = {"data_requests": [{"id": "close", "source": "tinyshare", "operation": "fund_daily",
        "parameters": fixture.params, "question_ids": ["q1"], "selection_reason": "本机传输替身"}]}
    return task, host


def seed_browser_tasks(archive):
    """Produce persistent UI cases through the same actual collector and engine."""
    fixture = fixtures.SDKWireBudgetTests()
    fixture.setUp()
    try:
        agent = make_agent(archive)
        ids = {}
        for name, mode, requests in (("complete", "cdn", 100), ("budget", "swallowed_denial", 1),
                ("unknown", "unknown", 100)):
            fixture.mode = mode
            task, host = make_task(agent, fixture, requests)
            with fixture_environment(fixture):
                result = agent.start(task["id"], host)
            assert result["status"] != "已完成" and not result["evidence"]
            assert result["ledger"]["used"]["model_calls"] == 0
            ids[name] = task["id"]
        assert agent.run_controls == {}
        return ids
    finally:
        fixture.doCleanups()


class SDKAgentLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SDKWireBudgetTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.archive = ResearchArchive(self.fixture.root / "agent-store")
        self.agent = make_agent(self.archive)

    def wait_for(self, condition, timeout=3):
        began = time.monotonic()
        while not condition() and time.monotonic() - began < timeout:
            time.sleep(.02)
        self.assertTrue(condition(), "Expected state did not arrive")

    def assert_clean(self, task, used):
        self.assertEqual(task["ledger"]["used"]["data_requests"], used)
        self.assertEqual(task["ledger"]["used"]["model_calls"], 0)
        self.assertFalse(task["evidence"])
        self.assertNotIn("WIRE_PRIVATE_FIXTURE", json.dumps(task))
        self.assertEqual(self.agent.run_controls, {})
        event = task["ledger"]["dispatches"][0]
        for key in ("network_worker_reaped", "transport_sender_stopped", "transport_receiver_stopped", "transport_authorizer_stopped"):
            self.assertTrue(event[key], key)
        with self.assertRaises(ProcessLookupError):
            os.kill(event["network_worker_pid"], 0)
        return event

    def test_cdn_cancel_and_restart_do_not_refund_or_redispatch_unknown_request(self):
        self.fixture.mode = "silent"
        task, host = make_task(self.agent, self.fixture)
        with fixture_environment(self.fixture):
            self.agent.start(task["id"], host, asynchronous=True)
            try:
                self.wait_for(lambda: self.fixture.calls == ["/query", "/cdn"])
                self.agent.cancel(task["id"])
            finally:
                self.agent.workers[task["id"]].join(3)
                self.assertFalse(self.agent.workers[task["id"]].is_alive())
        stopped = self.archive.get(task["id"])
        self.assertEqual(stopped["status"], "已取消")
        self.assertFalse(stopped["source_records"])
        self.assertFalse(stopped["delivery"])
        self.assertEqual(self.assert_clean(stopped, 2)["failure_code"], "transport_cancelled")
        restarted = make_agent(ResearchArchive(self.archive.root))
        self.fixture.mode = "query"
        with fixture_environment(self.fixture):
            restored = restarted.start(task["id"], host)
        self.assertEqual(restored["ledger"]["used"]["data_requests"], 2)
        self.assertEqual(restored["batch"], 2)
        self.assertEqual(self.fixture.calls, ["/query", "/cdn"])
        self.assertNotEqual(restored["status"], "已完成")
        self.assertFalse(restored["evidence"])
        self.assertFalse(restarted.run_controls)

    def test_sqlite_reservation_wait_cannot_keep_network_alive_or_grant_late_cdn(self):
        entered, proceed = threading.Event(), threading.Event()
        original = self.agent._journal
        blocked = []
        def journal(task):
            if task["ledger"]["used"]["data_requests"] == 2 and not blocked:
                blocked.append(True)
                entered.set()
                proceed.wait(4)
            original(task)
        self.agent.collector.journal = journal
        self.agent.collector.MAX_WAIT_SECONDS = .9
        task, host = make_task(self.agent, self.fixture)
        database = None
        with fixture_environment(self.fixture):
            self.agent.start(task["id"], host, asynchronous=True)
            try:
                self.assertTrue(entered.wait(3))
                pids = [process.pid for process in multiprocessing.active_children()]
                self.assertTrue(pids)
                database = sqlite3.connect(self.archive.path, timeout=1)
                database.execute("BEGIN EXCLUSIVE")
                proceed.set()
                self.wait_for(lambda: not multiprocessing.active_children(), timeout=1.8)
                for pid in pids:
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
                self.assertTrue(self.agent.workers[task["id"]].is_alive())
                self.assertEqual(self.fixture.calls, ["/query"])
            finally:
                proceed.set()
                if database is not None:
                    database.rollback()
                    database.close()
                self.agent.workers[task["id"]].join(5)
                self.assertFalse(self.agent.workers[task["id"]].is_alive())
        stored = self.archive.get(task["id"])
        self.assertNotEqual(stored["status"], "已完成")
        self.assertFalse(stored["source_records"])
        event = self.assert_clean(stored, 2)
        self.assertEqual(event["failure_code"], "response_deadline_exceeded")
        self.assertEqual(stored["ledger"]["dispatches"][1]["sdk_request_state"], "已登记但未发送")

    def test_identical_parameters_in_two_tasks_keep_independent_ledgers_and_snapshots(self):
        results = []
        with fixture_environment(self.fixture):
            for _ in range(2):
                task, host = make_task(self.agent, self.fixture)
                results.append(self.agent.start(task["id"], host))
        self.assertEqual(self.fixture.calls, ["/query", "/cdn"] * 2)
        self.assertNotEqual(results[0]["id"], results[1]["id"])
        for task in results:
            self.assert_clean(task, 2)
            for event in task["ledger"]["dispatches"]:
                self.assertGreaterEqual(event["sdk_request_elapsed_seconds"], 0)
                self.assertLessEqual(event["sdk_request_elapsed_seconds"], task["ledger"]["used"]["seconds"])
            self.assertNotEqual(task["status"], "已完成")
            self.assertEqual(len(task["source_records"]), 1)
            source = task["source_records"][0]
            self.assertEqual(source["task_id"], task["id"])
            self.assertEqual(source["research_version"], task["version"])
            self.assertEqual(source["status"], "成功待核验")


if __name__ == "__main__":
    unittest.main()
