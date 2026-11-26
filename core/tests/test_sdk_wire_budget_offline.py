"""Local SDK query/CDN/redirect fixtures and durable pre-send reservations."""

import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import multiprocessing
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, budget
from researchhelper.engine import ResearchAgent
from researchhelper.source_transport import IsolatedEvidenceCollector, _source_worker

ROOT = Path(__file__).resolve().parents[2]


class Frame:
    def __init__(self, rows):
        self.rows = rows
    def to_dict(self, orient):
        assert orient == "records"
        return self.rows


class LocalSDK:
    def fund_daily(self, **parameters):
        return self.query('fund_daily')

    def daily_basic(self, **parameters):
        return self.query('daily_basic')

    def query(self, operation):
        import requests
        root, mode = os.environ["SDK_LOCAL_ENDPOINT"], os.environ["SDK_LOCAL_MODE"]
        tries = 4 if mode == "retry" else 2 if mode in ("unknown", "auth", "duplicate_query") else 1
        for _ in range(tries):
            try:
                reply = requests.post(root + "/query", json={"api": operation}, timeout=2).json()
            except Exception:
                if mode in ("unknown", "duplicate_query"):
                    continue
                raise
        if mode == "duplicate_query":
            return Frame([])
        if mode == "query":
            return Frame(reply["data"])
        if mode in ("swallowed_denial", "swallowed_cdn", "duplicate_cdn"):
            try:
                return Frame(requests.get(root + "/cdn", timeout=2).json()["data"])
            except Exception:
                return Frame([])
        return Frame(requests.get(root + "/cdn", timeout=2).json()["data"])


def fixture_worker(connection, deadline):
    original = socket.socket.connect
    def local(sock, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError("External forbidden")
        return original(sock, address)
    socket.socket.connect = local
    sys.modules["tinyshare"] = types.SimpleNamespace(pro_api=lambda *args, **kwargs: LocalSDK())
    _source_worker(connection, deadline)


class FixtureCollector(IsolatedEvidenceCollector):
    worker_target = staticmethod(fixture_worker)


class SDKWireBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sdk-wire-", dir=ROOT / "result/acceptance")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.calls, self.journals = [], []
        self.mode, self.release = "cdn", threading.Event()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.reply()
            def do_GET(self):
                self.reply()
            def reply(self):
                owner.calls.append(self.path)
                if owner.mode == "unknown":
                    self.connection.close()
                    return
                if owner.mode == "swallowed_cdn" and self.path == "/cdn":
                    self.connection.close()
                    return
                if owner.mode == "redirect" and self.path == "/cdn":
                    self.send_response(302)
                    self.send_header("Location", "/cdn-final")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if owner.mode == "silent" and self.path == "/cdn":
                    owner.release.wait(5)
                    return
                body = json.dumps({"data": getattr(owner, "response_rows", [{"ts_code": "510300.SH", "trade_date": "20260930", "close": 4.5}])}).encode()
                if (owner.mode, self.path) in (("duplicate_query", "/query"), ("duplicate_cdn", "/cdn")):
                    body = b'{"code":-1,"code":0,"data":[]}'
                self.send_response(401 if owner.mode == "auth" else 503 if owner.mode == "retry" else 200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.collector = FixtureCollector(offline=False, journal=self.journal)
        self.params = {"ts_code": "510300.SH", "start_date": "20260930", "end_date": "20260930",
            "fields": "ts_code,trade_date,close"}

    def stop(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive())

    def journal(self, task):
        self.journals.append((len(self.calls), copy.deepcopy(task["ledger"])))

    def task(self, requests=100):
        return {"id": "rh-wire-fixture", "version": 1, "batch": 1, "cutoff": "2026-10-02T18:00:00+08:00",
            "ledger": budget({"data_requests": requests}), "source_records": [], "objects": ["510300.SH"]}

    def run_sdk(self, task):
        with patch.dict(os.environ, {"SDK_LOCAL_ENDPOINT": f"http://127.0.0.1:{self.server.server_port}",
                "SDK_LOCAL_MODE": self.mode, "NO_PROXY": "*"}):
            return self.collector.external(task, "tinyshare", "fund_daily", self.params, {"token": "WIRE_PRIVATE_FIXTURE"})

    def check(self, task, used, sent):
        self.assertEqual(task["ledger"]["used"]["data_requests"], used)
        self.assertEqual(task["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(len(task["ledger"]["dispatches"]), used)
        event = task["ledger"]["dispatches"][0]
        self.assertEqual(event["sdk_wire_requests_started"], sent)
        self.assertTrue(event["transport_authorizer_stopped"])
        self.assertTrue(event["network_worker_reaped"])
        self.assertTrue(event["transport_sender_stopped"])
        self.assertTrue(event["transport_receiver_stopped"])
        self.assertNotIn("WIRE_PRIVATE_FIXTURE", json.dumps(task))
        self.assertNotIn("SDK_LOCAL_ENDPOINT", json.dumps(task))
        return event

    def test_query_and_cdn_each_use_one_request_reserved_before_sending(self):
        task = self.task()
        record = self.run_sdk(task)
        self.assertEqual(record["snapshot"]["data"][0]["close"], 4.5)
        self.assertEqual(self.calls, ["/query", "/cdn"])
        self.check(task, 2, 2)
        self.assertEqual(self.journals[0][0], 0)
        self.assertTrue(any(sent == 1 and ledger["used"]["data_requests"] == 2 for sent, ledger in self.journals))
        self.assertEqual(task["ledger"]["dispatches"][1]["sdk_request_state"], "响应已返回")

    def test_budget_one_cannot_dispatch_cdn_or_accept_an_empty_snapshot(self):
        task = self.task(1)
        with self.assertRaisesRegex(ContractError, "追加请求预算不足"):
            self.run_sdk(task)
        self.assertEqual(self.calls, ["/query"])
        self.assertEqual(self.check(task, 1, 1)["failure_code"], "source_budget_exceeded")

    def test_direct_query_does_not_charge_a_nonexistent_download(self):
        self.mode = "query"
        task = self.task()
        self.run_sdk(task)
        self.check(task, 1, 1)
        self.assertEqual(self.calls, ["/query"])

    def test_download_redirect_must_obtain_another_parent_reservation(self):
        self.mode = "redirect"
        task = self.task()
        self.run_sdk(task)
        self.assertEqual(self.calls, ["/query", "/cdn", "/cdn-final"])
        self.check(task, 3, 3)
        self.assertTrue(any(sent == 2 and ledger["used"]["data_requests"] == 3 for sent, ledger in self.journals))

    def test_sdk_cannot_hide_denied_download_as_successful_empty_frame(self):
        self.mode = "swallowed_denial"
        task = self.task(1)
        with self.assertRaisesRegex(ContractError, "追加请求预算不足"):
            self.run_sdk(task)
        self.assertEqual(self.calls, ["/query"])
        self.assertEqual(self.check(task, 1, 1)["failure_code"], "source_budget_exceeded")
        self.assertFalse(task["source_records"])

    def test_sdk_cannot_hide_broken_download_as_successful_empty_frame(self):
        self.mode = "swallowed_cdn"
        task = self.task()
        with self.assertRaises(ContractError):
            self.run_sdk(task)
        self.assertEqual(self.calls, ["/query", "/cdn"])
        self.assertEqual(self.check(task, 2, 2)["failure_code"], "connection_interrupted")
        self.assertFalse(task["source_records"])

    def test_duplicate_json_cannot_be_hidden_by_a_retry_or_empty_frame(self):
        for mode, paths in (("duplicate_query", ["/query"]), ("duplicate_cdn", ["/query", "/cdn"])):
            with self.subTest(mode=mode):
                self.mode = mode
                self.calls.clear()
                task = self.task()
                with self.assertRaisesRegex(ContractError, "重复字段"):
                    self.run_sdk(task)
                self.assertEqual(self.calls, paths)
                count = len(paths)
                self.assertEqual(self.check(task, count, count)["failure_code"], "duplicate_response_field")
                self.assertEqual(task["ledger"]["dispatches"][-1]["sdk_request_state"], "返回结构异常")
                self.assertFalse(task["source_records"])

    def test_known_responses_may_not_exceed_three_same_query_sends(self):
        self.mode = "retry"
        task = self.task()
        with self.assertRaisesRegex(ContractError, "三次"):
            self.run_sdk(task)
        self.assertEqual(self.calls, ["/query"] * 3)
        self.assertEqual(self.check(task, 3, 3)["failure_code"], "sdk_retry_limit")

    def test_unknown_request_result_is_not_automatically_retried(self):
        self.mode = "unknown"
        task = self.task()
        with self.assertRaisesRegex(ContractError, "禁止自动重发"):
            self.run_sdk(task)
        self.assertEqual(self.calls, ["/query"])
        self.assertEqual(self.check(task, 1, 1)["failure_code"], "sdk_unknown_retry_blocked")

    def test_auth_failure_is_not_retried_inside_sdk(self):
        self.mode = "auth"
        task = self.task()
        with self.assertRaisesRegex(ContractError, "禁止自动重发"):
            self.run_sdk(task)
        self.assertEqual(self.calls, ["/query"])
        self.assertEqual(self.check(task, 1, 1)["failure_code"], "sdk_auth_retry_blocked")

    def test_failed_persistence_never_grants_second_wire_request(self):
        failed = []
        def journal(task):
            if task["ledger"]["used"]["data_requests"] == 2 and not failed:
                failed.append(True)
                raise RuntimeError("PRIVATE_STORAGE_FAILURE")
        self.collector.journal = journal
        task = self.task()
        with self.assertRaisesRegex(ContractError, "登记未完成"):
            self.run_sdk(task)
        self.assertEqual(self.calls, ["/query"])
        self.assertEqual(self.check(task, 2, 1)["failure_code"], "wire_authorization_failed")
        self.assertEqual(task["ledger"]["dispatches"][1]["sdk_request_state"], "已登记但未发送")

    def test_persistent_checkpoint_failure_is_fixed_error_not_private_storage_text(self):
        def journal(task):
            if task["ledger"]["used"]["data_requests"] == 2:
                raise RuntimeError("PRIVATE_STORAGE_FAILURE")
        self.collector.journal = journal
        task = self.task()
        with self.assertRaisesRegex(ContractError, "检查点保存失败") as error:
            self.run_sdk(task)
        self.assertNotIn("PRIVATE_STORAGE_FAILURE", str(error.exception))
        self.assertEqual(self.calls, ["/query"])
        self.assertEqual(self.check(task, 2, 1)["failure_code"], "source_checkpoint_failed")

    def test_silent_cdn_shares_original_wall_clock_deadline(self):
        self.mode = "silent"
        self.collector.MAX_WAIT_SECONDS = 1.2
        task = self.task()
        began = time.monotonic()
        with self.assertRaises(ContractError):
            self.run_sdk(task)
        self.assertLess(time.monotonic() - began, 2.2)
        self.check(task, 2, 2)

    def test_pending_reservation_does_not_block_network_deadline_or_get_late_grant(self):
        entered, unlock = threading.Event(), threading.Event()
        def journal(task):
            if task["ledger"]["used"]["data_requests"] == 2:
                entered.set()
                unlock.wait(4)
        self.collector.journal = journal
        self.collector.MAX_WAIT_SECONDS = .9
        task = self.task()
        failure = []
        def run():
            try:
                self.run_sdk(task)
            except ContractError:
                failure.append(True)
        runner = threading.Thread(target=run)
        runner.start()
        try:
            self.assertTrue(entered.wait(3))
            pids = [process.pid for process in multiprocessing.active_children()]
            self.assertTrue(pids)
            time.sleep(1.1)
            for pid in pids:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            self.assertEqual(self.calls, ["/query"])
            self.assertTrue(runner.is_alive(), "Archive reservation is still awaiting settlement")
        finally:
            unlock.set()
            runner.join(5)
        self.assertFalse(runner.is_alive())
        self.assertTrue(failure)
        self.check(task, 2, 1)
        self.assertEqual(task["ledger"]["dispatches"][1]["sdk_request_state"], "已登记但未发送")


if __name__ == "__main__":
    unittest.main()
