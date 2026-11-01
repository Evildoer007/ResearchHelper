"""Local TLS and child SDK fixtures. No real supplier, credential or model.

Production adapters and ledger run in the parent. Fixture workers only replace
the supplier endpoint/SDK inside the child; error classification remains real.
"""

import copy
import json
import multiprocessing
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.request
from urllib.parse import urlsplit
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from researchhelper.contracts import ContractError, budget
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector, snapshot_record
from researchhelper.source_transport import IsolatedEvidenceCollector, _source_worker
from researchhelper.service import make_server
from tests import test_isolated_model_transport_offline as model_fixture
from tests import test_skill_source_execution_offline as skill_fixture

ROOT = Path(__file__).resolve().parents[2]
SECRET = "SOURCE_FIXTURE_PRIVATE_KEY"
RETURNED = "SOURCE_FIXTURE_RETURNED_ACCESS"


class FixtureFrame:
    def to_dict(self, orient):
        assert orient == "records"
        return [{"ts_code": "510300.SH", "trade_date": "20260930", "close": 4.5, "vol": float("nan")}]


class FixtureTinyClient:
    def fund_daily(self, **parameters):
        return FixtureFrame()


def fixture_source_worker(connection, deadline):
    connect = socket.socket.connect
    def localhost_only(sock, address):
        if isinstance(address, tuple) and address[0] not in ("localhost", "127.0.0.1", "::1"):
            raise AssertionError("Child external network forbidden")
        return connect(sock, address)
    socket.socket.connect = localhost_only
    def pro_api(token, timeout):
        assert os.environ.get("TINYSHARE_CACHE_MAX_ENTRIES") == "0"
        print("SDK diagnostic must not escape " + SECRET)
        if token.endswith("stall"):
            time.sleep(30)
        if token.endswith("expired"):
            raise type("TinySharePermissionError", (Exception,), {})("expired " + SECRET)
        if token.endswith("permission"):
            raise type("TinySharePermissionError", (Exception,), {})("denied " + SECRET)
        if token.endswith("unknown"):
            raise ValueError(SECRET)
        return FixtureTinyClient()
    sys.modules["tinyshare"] = types.SimpleNamespace(pro_api=pro_api)
    import researchhelper.source_transport as source
    def download(url, timeout, vendor_source_id=None):
        if "stall" in url:
            time.sleep(30)
        return b"%PDF-synthetic-unparseable"
    source._download_filing_pdf = download
    _source_worker(connection, deadline)


def fixture_dns_stall(connection, deadline):
    socket.getaddrinfo = lambda *args, **kwargs: time.sleep(30)
    _source_worker(connection, deadline)


class LocalCollector(IsolatedEvidenceCollector):
    worker_target = staticmethod(fixture_source_worker)

    def _http_json(self, task, request, timeout, event):
        target = urllib.request.Request(self.fixture_endpoint + urlsplit(request.full_url).path,
            data=request.data, headers=dict(request.headers), method="POST")
        for name, value in request.unredirected_hdrs.items():
            target.add_unredirected_header(name, value)
        return super()._http_json(task, target, timeout, event)


class SourceTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="source-transport-", dir=ROOT / "result/acceptance")
        cls.folder = Path(cls.directory.name)
        cls.key, cls.cert = cls.folder / "fixture.key", cls.folder / "fixture.crt"
        subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(cls.key), "-out", str(cls.cert), "-days", "1", "-subj", "/CN=localhost"],
            check=True, capture_output=True, timeout=20)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.journal = []
        self.collector = LocalCollector(offline=False, journal=lambda task: self.journal.append(copy.deepcopy(task)))
        self.collector.MAX_WAIT_SECONDS = 2
        self.params = {"ts_code": "510300.SH", "start_date": "20260929", "end_date": "20260930",
            "fields": "ts_code,trade_date,close,vol"}
        environment = patch.dict(os.environ, {"SSL_CERT_FILE": str(self.cert), "NO_PROXY": "*", "no_proxy": "*"})
        environment.start()
        self.addCleanup(environment.stop)

    def task(self):
        return {"id": "rh-source-fixture", "version": 1, "batch": 1, "objects": ["600000.SH"],
            "question": "600000.SH测试取数", "cutoff": "2026-10-02T18:00:00+08:00", "ledger": budget(), "source_records": []}

    def server(self, mode="normal"):
        stop, entered = threading.Event(), threading.Event()
        self.paths = []
        paths = self.paths
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                paths.append(self.path)
                entered.set()
                try:
                    if mode == "silent":
                        stop.wait(8)
                        return
                    if mode == "headers":
                        for byte in b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n":
                            if stop.wait(.07):
                                return
                            self.connection.sendall(bytes([byte]))
                        return
                    payload = {"errorcode": 0, "data": {"access_token": RETURNED}} if self.path.endswith("get_access_token") else {"errorcode": 0, "tables": [{"fixture": 1}]}
                    if mode == "business":
                        payload = {"errorcode": 999, "tables": []}
                    body = json.dumps(payload).encode()
                    self.send_response(401 if mode == "auth" else 200)
                    self.send_header("Content-Length", str(len(body) + (10 if mode == "truncated" else 0)))
                    self.end_headers()
                    if mode == "body":
                        for byte in body:
                            if stop.wait(.04):
                                return
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                    else:
                        self.wfile.write(body)
                except (OSError, ssl.SSLError):
                    pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(str(self.cert), str(self.key))
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.collector.fixture_endpoint = f"https://localhost:{server.server_port}"
        def cleanup():
            stop.set()
            server.shutdown()
            server.server_close()
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.addCleanup(cleanup)
        return entered

    def check(self, task, count=1):
        self.assertEqual(task["ledger"]["used"]["data_requests"], count)
        self.assertEqual(task["ledger"]["used"]["model_calls"], 0)
        events = task["ledger"]["dispatches"]
        self.assertEqual(len(events), count)
        for event in events:
            self.assertTrue(all(event.get(key) for key in ("network_worker_reaped", "transport_sender_stopped",
                "transport_receiver_stopped", "wall_clock_deadline")), event)
        for secret in (SECRET, RETURNED):
            self.assertNotIn(secret, json.dumps(task) + json.dumps(self.journal))
        return events[-1]

    def ifind(self, task, credentials=None):
        return self.collector.external(task, "ifind", "cmd_history_quotation", {"codes": "600000.SH"}, credentials or {"access_token": SECRET})

    def test_ifind_response_remains_unverified_snapshot(self):
        self.server()
        task = self.task()
        record = self.ifind(task)
        self.assertEqual(record["snapshot"]["tables"], [{"fixture": 1}])
        self.assertEqual(record["source_check"], "待核验")
        self.check(task)

    def test_auth_and_business_charge_separately_with_private_memory_cache(self):
        self.server()
        task = self.task()
        self.ifind(task, {"refresh_token": SECRET})
        self.check(task, 2)
        self.assertEqual(self.paths, ["/api/v1/get_access_token", "/api/v1/cmd_history_quotation"])
        self.assertEqual(self.collector._ifind_access_cache[1], RETURNED)
        self.ifind(task, {"refresh_token": SECRET})
        self.check(task, 3)
        self.assertEqual(len([path for path in self.paths if path.endswith("get_access_token")]), 1)

    def test_failed_auth_never_dispatches_business_query(self):
        self.server("auth")
        task = self.task()
        with self.assertRaises(ContractError):
            self.ifind(task, {"refresh_token": SECRET})
        event = self.check(task)
        self.assertEqual(event["failure_code"], "authentication_failed")
        self.assertIsNone(self.collector._ifind_access_cache)
        self.assertEqual(self.paths, ["/api/v1/get_access_token"])

    def stall(self, mode=None, dns=False):
        self.server(mode or "normal")
        if dns:
            self.collector.worker_target = fixture_dns_stall
        self.collector.MAX_WAIT_SECONDS = .65
        task, began = self.task(), time.monotonic()
        with self.assertRaises(ContractError):
            self.ifind(task)
        self.assertLess(time.monotonic() - began, 1.8)
        event = self.check(task)
        self.assertIn(event["failure_code"], ("response_deadline_exceeded", "transport_timeout"))

    def test_dns_has_one_bounded_attempt(self):
        self.stall(dns=True)

    def test_silent_headers_have_one_bounded_attempt(self):
        self.stall("silent")

    def test_trickled_headers_have_one_bounded_attempt(self):
        self.stall("headers")

    def test_trickled_body_has_one_bounded_attempt(self):
        self.stall("body")

    def test_truncated_json_is_not_accepted_as_full_response(self):
        self.server("truncated")
        task = self.task()
        with self.assertRaises(ContractError):
            self.ifind(task)
        self.assertEqual(self.check(task)["failure_code"], "response_incomplete")

    def test_business_failure_does_not_retry_or_guess_permission(self):
        self.server("business")
        task = self.task()
        with self.assertRaises(ContractError):
            self.ifind(task)
        event = self.check(task)
        self.assertEqual(event["status"], "业务失败")
        self.assertNotIn("failure_code", event)

    def tiny(self, task, suffix=""):
        return self.collector.external(task, "tinyshare", "fund_daily", self.params, {"token": SECRET + suffix})

    def test_tinyshare_preserves_numbers_and_missing_cells(self):
        task = self.task()
        record = self.tiny(task)
        self.assertEqual(record["snapshot"]["data"][0]["close"], 4.5)
        self.assertIsNone(record["snapshot"]["data"][0]["vol"])
        self.check(task)

    def test_sdk_history_cache_policy_is_child_only_and_does_not_reuse_shared_results(self):
        task = self.task()
        with patch.dict(os.environ, {"TINYSHARE_CACHE_MAX_ENTRIES": "999"}):
            self.tiny(task)
            self.assertEqual(os.environ["TINYSHARE_CACHE_MAX_ENTRIES"], "999")
        self.assertTrue(self.check(task)["sdk_history_cache_disabled"])

    def test_tinyshare_expiry_has_one_attempt_and_no_private_error(self):
        task = self.task()
        with self.assertRaisesRegex(ContractError, "已过期"):
            self.tiny(task, "expired")
        event = self.check(task)
        self.assertEqual(event["failure_code"], "credential_expired")
        self.assertTrue(event["sdk_history_cache_disabled"])

    def test_tinyshare_permission_is_not_inferred_as_expiration(self):
        task = self.task()
        with self.assertRaisesRegex(ContractError, "权限"):
            self.tiny(task, "permission")
        self.assertEqual(self.check(task)["failure_code"], "permission_denied")

    def test_tinyshare_unknown_error_does_not_guess_permission(self):
        task = self.task()
        with self.assertRaises(ContractError):
            self.tiny(task, "unknown")
        self.assertEqual(self.check(task)["failure_code"], "provider_failure")

    def test_sdk_stall_cannot_hold_parent_controller(self):
        self.collector.MAX_WAIT_SECONDS = .65
        task, began = self.task(), time.monotonic()
        with self.assertRaises(ContractError):
            self.tiny(task, "stall")
        self.assertLess(time.monotonic() - began, 1.8)
        event = self.check(task)
        self.assertEqual(event["failure_code"], "response_deadline_exceeded")
        self.assertTrue(event["sdk_history_cache_disabled"])

    def test_offline_and_invalid_tiny_parameters_spawn_no_worker(self):
        task = self.task()
        self.collector.offline = True
        with patch("researchhelper.transport.multiprocessing.get_context") as context:
            with self.assertRaises(ContractError):
                self.tiny(task)
            self.collector.offline = False
            self.params["ts_code"] = "all"
            with self.assertRaises(ContractError):
                self.tiny(task)
            context.assert_not_called()
        self.assertEqual(task["ledger"]["used"]["data_requests"], 0)

    def test_worker_start_failure_is_explicit_and_never_retried(self):
        self.collector.fixture_endpoint = "https://localhost:1"
        context = Mock(wraps=multiprocessing.get_context("spawn"))
        worker = Mock(pid=None)
        worker.start.side_effect = RuntimeError(SECRET)
        context.Process.return_value = worker
        task = self.task()
        with patch("researchhelper.transport.multiprocessing.get_context", return_value=context):
            with self.assertRaisesRegex(ContractError, "无法启动"):
                self.ifind(task)
        worker.start.assert_called_once()
        event = self.check(task)
        self.assertEqual(event["failure_code"], "worker_start_failed")
        self.assertFalse(event["network_request_started"])

    def test_production_service_selects_isolated_collector(self):
        server = make_server(self.folder / "server", ROOT, authentication=True, runtime_configurable=True)
        self.addCleanup(server.server_close)
        self.assertIsInstance(server.agent.collector, IsolatedEvidenceCollector)
        self.assertEqual(server.agent.collector.active, server.agent._check_run_control)

    def filing(self, task, filename="fixture.pdf"):
        task.update(input_hash="fixture", materials=[], questions=[{"id": "q1", "required": True}])
        params = {"codes": "600000.SH", "functionpara": {"reportType": "901"}, "beginrDate": "2026-09-01",
            "endrDate": "2026-10-02", "outputpara": "reportDate:Y,thscode:Y,secName:Y,ctime:Y,reportTitle:Y,pdfURL:Y,seq:Y"}
        row = {"reportDate": "2026-09-30", "thscode": "600000.SH", "secName": "合成主体",
            "ctime": "2026-09-29T17:00:00+08:00", "reportTitle": "合成公告",
            "pdfURL": "https://static.cninfo.com.cn/finalpage/" + filename, "seq": "fixture_1"}
        catalog = snapshot_record(task, "ifind", "report_query", params, {"errorcode": 0, "tables": [row]})
        task["source_records"].append(catalog)
        return {"catalog_id": catalog["id"], "source_id": "fixture_1"}

    def test_downloaded_unparseable_pdf_is_kept_but_not_fabricated_text(self):
        task = self.task()
        selection = self.filing(task)
        record = self.collector.external(task, "ifind", "filing_original", selection, {})
        self.assertEqual(record["status"], "解析受限")
        self.assertNotEqual(record["stages"]["parse"], "已解析")
        self.check(task)

    def test_original_download_stall_obeys_same_request_deadline(self):
        self.collector.MAX_WAIT_SECONDS = .65
        task = self.task()
        selection = self.filing(task, "stall.pdf")
        began = time.monotonic()
        with self.assertRaises(ContractError):
            self.collector.external(task, "ifind", "filing_original", selection, {})
        self.assertLess(time.monotonic() - began, 1.8)
        self.assertEqual(self.check(task)["failure_code"], "response_deadline_exceeded")

    def test_cross_task_original_selection_dispatches_nothing(self):
        task = self.task()
        selection = self.filing(task)
        task["source_records"][0]["task_id"] = "different-task"
        with self.assertRaises(ContractError):
            self.collector.external(task, "ifind", "filing_original", selection, {})
        self.assertEqual(task["ledger"]["used"]["data_requests"], 0)

    def test_missing_sdk_dependency_has_no_charge_or_worker(self):
        task = self.task()
        with patch("researchhelper.source_transport.importlib.util.find_spec", return_value=None), \
                patch("researchhelper.transport.multiprocessing.get_context") as context:
            with self.assertRaisesRegex(ContractError, "未安装"):
                self.tiny(task)
            context.assert_not_called()
        self.assertEqual(task["ledger"]["used"]["data_requests"], 0)

    def test_running_source_cancel_reaps_transport_and_adopts_no_snapshot(self):
        entered = self.server("silent")
        agent = ResearchAgent(ResearchArchive(self.folder / "cancel-store"), ROOT, offline=False,
            source_credentials={"ifind": {"access_token": SECRET}})
        self.collector.active, self.collector.journal = agent._check_run_control, agent._journal
        self.collector.MAX_WAIT_SECONDS = 6
        agent.collector = self.collector
        task = agent.create("600000.SH收盘价取数", objects=["600000.SH"], cutoff="2026-10-02T18:00:00+08:00")
        need = {"id": "close", "source": "ifind", "operation": "cmd_history_quotation", "question_ids": ["q1"],
            "parameters": {"codes": "600000.SH", "indicators": "close", "startdate": "2026-09-30", "enddate": "2026-09-30",
                "functionpara": {"Currency": "RMB", "CPS": "1", "Fill": "Omit"}}}
        agent.start(task["id"], {"data_requests": [need]}, asynchronous=True)
        try:
            self.assertTrue(entered.wait(3))
            began = time.monotonic()
            agent.cancel(task["id"])
            agent.workers[task["id"]].join(2)
            self.assertFalse(agent.workers[task["id"]].is_alive())
            self.assertLess(time.monotonic() - began, 1.5)
        finally:
            agent.workers[task["id"]].join(8)
        stored = agent.archive.get(task["id"])
        self.assertEqual(stored["status"], "已取消")
        self.assertEqual(stored["source_records"], [])
        self.assertEqual(stored["evidence"], [])
        self.assertFalse(stored["delivery"])
        self.assertEqual(stored["ledger"]["dispatches"][0]["failure_code"], "transport_cancelled")
        self.assertTrue(stored["ledger"]["dispatches"][0]["network_worker_reaped"])
        self.assertEqual(stored["ledger"]["used"]["data_requests"], 1)
        self.assertEqual(agent.run_controls, {})


class PublicationFixtureSourceTransportTests(unittest.TestCase):
    stop = model_fixture.PublicationFixtureTransportTests.stop
    request = model_fixture.PublicationFixtureTransportTests.request
    login = model_fixture.PublicationFixtureTransportTests.login

    def setUp(self):
        model_fixture.PublicationFixtureTransportTests.setUp(self)
        collector = self.server.agent.collector
        self.assertIsInstance(collector, IsolatedEvidenceCollector)
        self.server.agent.collector = EvidenceCollector(offline=collector.offline, journal=collector.journal)

    def test_original_publication_assertions_with_explicit_model_and_source_fixtures(self):
        model_fixture.PublicationFixtureTransportTests.test_original_auth_publication_assertions_with_explicit_mock_transport(self)


class SkillParentFixtureTransportTests(unittest.TestCase):
    """Retain old parent HTTP assertions without dispatching a child request.

    Real isolated Skill execution is verified separately from the installed ZIP.
    This adapter exists only for fixtures whose urllib mock lives in the parent.
    """
    files = staticmethod(skill_fixture.SkillSourceExecutionTests.files)
    run_source = skill_fixture.SkillSourceExecutionTests.run_source
    replies = staticmethod(skill_fixture.SkillSourceExecutionTests.replies)
    tearDown = skill_fixture.SkillSourceExecutionTests.tearDown

    def setUp(self):
        skill_fixture.SkillSourceExecutionTests.setUp(self)
        original = self.module.Session

        def fixture_session(*args, **kwargs):
            session = original(*args, **kwargs)
            if kwargs.get("allow_network"):
                collector = session.agent.collector
                self.assertIsInstance(collector, IsolatedEvidenceCollector)
                session.agent.collector = EvidenceCollector(offline=collector.offline, journal=collector.journal)
            return session

        self.module.Session = fixture_session

    def test_refresh_token_original_assertions(self):
        skill_fixture.SkillSourceExecutionTests.test_explicit_refresh_token_two_dispatches_and_no_secret_in_task_or_report(self)

    def test_auth_failure_original_assertions(self):
        skill_fixture.SkillSourceExecutionTests.test_auth_failure_keeps_spent_budget_and_never_switches_source(self)

    def test_auth_budget_original_assertions(self):
        skill_fixture.SkillSourceExecutionTests.test_authentication_uses_request_budget_and_does_not_refund(self)

    def test_project_settings_original_assertions(self):
        skill_fixture.SkillSourceExecutionTests.test_only_project_source_settings_are_read_not_app_installation_or_environment(self)

    def test_environment_ignoring_host_rejects_network_before_creating_project(self):
        with patch("sys.flags", types.SimpleNamespace(ignore_environment=1, dont_write_bytecode=0)):
            with self.assertRaisesRegex(ContractError, "不写字节码"):
                self.module.Session(self.project, allow_network=True)
        self.assertFalse(self.project.exists())


if __name__ == "__main__":
    unittest.main()
