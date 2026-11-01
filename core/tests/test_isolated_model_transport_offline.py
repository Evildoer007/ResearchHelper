"""Production spawn transport checks. Only fixture TLS on localhost is used.

Credentials are synthetic. No supplier, live model, foreground App or user
archive is accessed. Test workers reproduce DNS and incomplete IPC stalls.
"""

import copy
import json
import multiprocessing
import os
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, budget
from researchhelper.engine import ResearchAgent
from researchhelper.model import IsolatedModelService, ModelService, ModelSettings, _model_http_worker
from researchhelper.service import make_server
from tests import test_publication_relationship_offline as publication

ROOT = Path(__file__).resolve().parents[2]
SECRET = "ISOLATED_FIXTURE_NOT_A_REAL_KEY"


def dns_stall(connection, deadline):
    def stalled(*args, **kwargs):
        time.sleep(30)
    socket.getaddrinfo = stalled
    _model_http_worker(connection, deadline)


def incomplete_pipe_frame(connection, deadline):
    connection.recv()
    os.write(connection.fileno(), struct.pack("!i", 1000000) + b"x")
    time.sleep(30)


def crashed_worker(connection, deadline):
    connection.close()


class IsolatedModelTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="isolated-http-", dir=ROOT / "result/acceptance")
        cls.folder = Path(cls.directory.name)
        cls.key, cls.cert = cls.folder / "fixture.key", cls.folder / "fixture.crt"
        subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(cls.key), "-out", str(cls.cert), "-days", "1", "-subj", "/CN=localhost"],
            check=True, capture_output=True, timeout=20)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.settings = ModelSettings(self.folder / self._testMethodName)
        self.settings.save({"endpoint": "https://localhost:1/chat", "model": "fixture", "api_key": SECRET})
        self.journal = []
        self.service = IsolatedModelService(self.settings, offline=False,
            journal=lambda task: self.journal.append(copy.deepcopy(task)))
        self.service.MAX_WAIT_SECONDS = 2
        self.environment = patch.dict(os.environ, {"SSL_CERT_FILE": str(self.cert), "NO_PROXY": "*", "no_proxy": "*"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def task(self):
        return {"id": "isolated-fixture", "batch": 1, "ledger": budget()}

    def server(self, mode):
        entered, stop = threading.Event(), threading.Event()
        self.request_headers = []
        request_headers = self.request_headers
        body = json.dumps({"model": "fixture", "choices": [{"finish_reason": "stop",
            "message": {"content": "fixture answer", "reasoning_content": "PRIVATE_NEVER_ARCHIVE"}}],
            "usage": {"total_tokens": 7}}).encode()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                request_headers.append(dict(self.headers))
                entered.set()
                try:
                    if mode == "redirect":
                        self.send_response(302)
                        self.send_header("Location", "/redirected")
                        self.end_headers()
                        return
                    if mode == "silent":
                        stop.wait(8)
                        return
                    if mode == "headers":
                        for byte in b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n":
                            if stop.wait(.07):
                                return
                            self.connection.sendall(bytes([byte]))
                        return
                    status = 401 if mode == "unauthorized" else 200
                    self.send_response(status)
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
            def do_GET(self):
                request_headers.append(dict(self.headers))
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.cert), str(self.key))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.settings.save({"endpoint": f"https://localhost:{server.server_port}/chat", "model": "fixture", "api_key": SECRET})
        def cleanup():
            stop.set()
            server.shutdown()
            server.server_close()
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.addCleanup(cleanup)
        return entered

    def checked(self, task, code=None):
        record = task["ledger"]["dispatches"][0]
        self.assertEqual(task["ledger"]["used"]["model_calls"], 1)
        self.assertEqual(record["elapsed_seconds"], task["ledger"]["used"]["seconds"])
        for field in ("network_worker_reaped", "transport_sender_stopped", "transport_receiver_stopped", "wall_clock_deadline"):
            self.assertTrue(record[field], (field, record))
        self.assertNotIn(record["network_worker_pid"], [child.pid for child in multiprocessing.active_children()])
        self.assertNotIn(SECRET, json.dumps(self.journal))
        self.assertNotIn("PRIVATE_NEVER_ARCHIVE", json.dumps(self.journal))
        if code is not None:
            self.assertEqual(record["failure_code"], code)
            self.assertEqual(record["status"], "失败")
        return record

    def test_production_server_uses_isolated_transport(self):
        server = make_server(self.folder / "server", ROOT, authentication=True, runtime_configurable=True)
        self.addCleanup(server.server_close)
        self.assertIsInstance(server.agent.model, IsolatedModelService)
        self.assertTrue(server.agent.model.offline)

    def test_complete_local_tls_response_preserves_usage_and_privacy(self):
        self.server("normal")
        task = self.task()
        self.assertEqual(self.service.reason(task, []), "fixture answer")
        record = self.checked(task)
        self.assertEqual(record["usage"]["total_tokens"], 7)
        self.assertEqual(record["status"], "成功")
        self.assertTrue(record["network_request_started"])

    def stalled(self, mode=None, target=None):
        if mode:
            self.server(mode)
        if target:
            self.service.worker_target = target
        self.service.MAX_WAIT_SECONDS = .65
        task, started = self.task(), time.monotonic()
        with self.assertRaises(ContractError):
            self.service.reason(task, [])
        self.assertLess(time.monotonic() - started, 1.8)
        record = self.checked(task)
        self.assertIn(record["failure_code"], ("response_deadline_exceeded", "transport_timeout"))
        self.assertEqual(task["ledger"]["reserve"]["seconds"], 60)

    def test_dns_cannot_extend_global_deadline(self):
        self.stalled(target=dns_stall)

    def test_silent_headers_cannot_extend_global_deadline(self):
        self.stalled(mode="silent")

    def test_trickled_headers_cannot_reset_global_deadline(self):
        self.stalled(mode="headers")

    def test_trickled_body_cannot_reset_global_deadline(self):
        self.stalled(mode="body")

    def test_incomplete_ipc_frame_cannot_block_controller(self):
        self.stalled(target=incomplete_pipe_frame)

    def test_worker_crash_keeps_charge_without_retry(self):
        self.service.worker_target = crashed_worker
        task = self.task()
        with self.assertRaises(ContractError):
            self.service.reason(task, [])
        self.checked(task, "connection_interrupted")

    def test_http_authentication_keeps_existing_category(self):
        self.server("unauthorized")
        task = self.task()
        with self.assertRaisesRegex(ContractError, "认证"):
            self.service.reason(task, [])
        self.checked(task, "authentication_failed")

    def test_redirect_does_not_forward_private_authorization(self):
        self.server("redirect")
        task = self.task()
        self.assertEqual(self.service.reason(task, []), "fixture answer")
        self.checked(task)
        self.assertEqual(len(self.request_headers), 2)
        self.assertEqual(self.request_headers[0]["Authorization"], "Bearer " + SECRET)
        self.assertNotIn("Authorization", self.request_headers[1])

    def test_truncated_transport_rejects_parseable_json(self):
        self.server("truncated")
        task = self.task()
        with self.assertRaises(ContractError):
            self.service.reason(task, [])
        self.checked(task, "response_incomplete")

    def test_cancel_stops_connection_and_persists_actual_charge(self):
        entered = self.server("silent")
        agent = ResearchAgent(ResearchArchive(self.folder / "cancel-store"), ROOT, offline=True)
        task = agent.create("取消连接测试")
        task.update(status="运行中", batch=1)
        agent.archive.save(task)
        self.service.journal = agent._journal
        self.service.active = lambda current: agent._active(current["id"], current["batch"])
        self.service.MAX_WAIT_SECONDS = 6
        errors = []
        def run():
            try:
                self.service.reason(task, [])
            except ContractError as error:
                errors.append(str(error))
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            started = time.monotonic()
            agent.cancel(task["id"])
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertTrue(errors)
            self.checked(task, "transport_cancelled")
            stored = agent.archive.get(task["id"])
            self.assertEqual(stored["status"], "已取消")
            self.assertEqual(stored["ledger"], task["ledger"])
            self.assertEqual(stored["evidence"], [])
        finally:
            # Clean up even when an assertion fails; no retained test threads.
            if thread.is_alive():
                self.service.active = lambda current: (_ for _ in ()).throw(ContractError("停止"))
                thread.join(3)

    def test_offline_and_missing_configuration_start_no_worker(self):
        for offline, empty in ((True, False), (False, True)):
            self.service.offline = offline
            if empty:
                self.settings.save({"endpoint": "", "model": "", "api_key": ""})
            task = self.task()
            with patch("researchhelper.model.multiprocessing.get_context") as context:
                with self.assertRaises(ContractError):
                    self.service.reason(task, [])
                context.assert_not_called()
            self.assertEqual(task["ledger"]["used"]["model_calls"], 0)


class PublicationFixtureTransportTests(unittest.TestCase):
    """Keep the old auth/publication assertions, explicitly inject its mock.

    A parent urllib patch cannot cross spawn. This fixture is not proof of the
    production transport; separate real TLS and frozen tests cover that path.
    """
    stop = publication.PublicationAppBoundaryTests.stop
    request = publication.PublicationAppBoundaryTests.request
    login = publication.PublicationAppBoundaryTests.login

    def setUp(self):
        publication.PublicationAppBoundaryTests.setUp(self)
        model = self.server.agent.model
        self.assertIsInstance(model, IsolatedModelService)
        self.server.agent.model = ModelService(model.settings, offline=model.offline, journal=model.journal)

    def test_original_auth_publication_assertions_with_explicit_mock_transport(self):
        publication.PublicationAppBoundaryTests.test_supplier_supplement_preserves_answer_but_cannot_borrow_its_publication(self)


if __name__ == "__main__":
    unittest.main()
