"""Independent response-deadline checks with fake transports and local TLS.

The local server is not a model: it returns fixture bytes and delayed chunks.
No vendor credentials, real model invocation or external network is allowed.
"""

import copy
import io
import json
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import urllib.request
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from researchhelper.contracts import ContractError, budget
from researchhelper.model import ModelService, ModelSettings


class FixtureResponse(io.BytesIO):
    def __init__(self, payload, clock, delays, connection=None):
        super().__init__(payload)
        self.clock, self.delays = clock, iter(delays)
        self.reads = 0
        if connection is not None:
            self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=connection))

    def read1(self, size=-1):
        self.reads += 1
        self.clock[0] += next(self.delays, 0)
        return super().read1(min(size, 16))


class ModelResponseDeadlineTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="rh-response-deadline-")
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.secret = "OFFLINE_DEADLINE_KEY_NEVER_REAL"
        self.settings = ModelSettings(self.folder)
        self.settings.save({"endpoint": "https://deadline.example.invalid/chat",
                            "model": "offline-fixture", "api_key": self.secret})
        self.journal = []
        self.service = ModelService(self.settings, offline=False,
                                   journal=lambda task: self.journal.append(copy.deepcopy(task)))
        original_connect = socket.socket.connect
        def local_only(connection, address):
            if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
                raise AssertionError("External network forbidden")
            return original_connect(connection, address)
        network = patch("socket.socket.connect", local_only)
        network.start()
        self.addCleanup(network.stop)

    @staticmethod
    def task():
        return {"id": "deadline-fixture", "batch": 1, "ledger": budget()}

    @staticmethod
    def payload():
        return json.dumps({"model": "offline-fixture", "choices": [{"finish_reason": "stop",
            "message": {"content": "fixture answer", "reasoning_content": "PRIVATE_CHAIN_NEVER_SAVE"}}],
            "usage": {"total_tokens": 7}}).encode()

    def run_clock(self, delays, *, opened_at=0, connection=None):
        task, clock = self.task(), [0]
        response = FixtureResponse(self.payload(), clock, delays, connection)
        def opened(*_, **__):
            clock[0] = opened_at
            return response
        self.service.MAX_WAIT_SECONDS = 1
        with patch("researchhelper.model.time.monotonic", side_effect=lambda: clock[0]), \
                patch("researchhelper.model.urllib.request.urlopen", side_effect=opened) as transport:
            try:
                answer = self.service.reason(task, [])
                error = None
            except ContractError as failure:
                answer, error = None, str(failure)
        transport.assert_called_once()
        self.assertTrue(response.closed)
        self.assertEqual(task["ledger"]["used"]["model_calls"], 1)
        self.assertEqual(task["ledger"]["used"]["seconds"], clock[0])
        self.assertNotIn(self.secret, json.dumps(self.journal) + str(error))
        self.assertNotIn("PRIVATE_CHAIN_NEVER_SAVE", json.dumps(self.journal))
        return task, response, answer, error

    def test_slow_trickle_cannot_reset_each_chunk_timeout(self):
        task, response, answer, error = self.run_clock([.3, .3, .3, .3, .3])
        self.assertIsNone(answer)
        self.assertIn("时限", error)
        self.assertEqual(response.reads, 4)
        self.assertEqual(task["ledger"]["dispatches"][0]["failure_code"], "response_deadline_exceeded")
        self.assertEqual(task["ledger"]["used"]["seconds"], 1.2)

    def test_header_time_is_not_restarted_for_the_body(self):
        task, response, answer, error = self.run_clock([.3], opened_at=.8)
        self.assertIsNone(answer)
        self.assertEqual(response.reads, 1)
        self.assertIn("时限", error)
        self.assertEqual(task["ledger"]["used"]["seconds"], 1.1)

    def test_already_late_headers_do_not_read_the_body(self):
        task, response, answer, error = self.run_clock([], opened_at=1.1)
        self.assertIsNone(answer)
        self.assertEqual(response.reads, 0)
        self.assertEqual(task["ledger"]["dispatches"][0]["status"], "失败")

    def test_socket_wait_shrinks_but_global_default_is_unchanged(self):
        waits = []
        connection = SimpleNamespace(settimeout=waits.append)
        default = socket.getdefaulttimeout()
        task, response, answer, error = self.run_clock([.01] * 30, connection=connection)
        self.assertEqual(answer, "fixture answer")
        self.assertIsNone(error)
        self.assertGreater(response.reads, 2)
        self.assertTrue(all(a > b > 0 for a, b in zip(waits, waits[1:])))
        self.assertEqual(socket.getdefaulttimeout(), default)
        self.assertTrue(task["ledger"]["dispatches"][0]["response_read_deadline"])

    def test_exact_deadline_is_not_a_successful_response(self):
        task, _, answer, error = self.run_clock([1])
        self.assertIsNone(answer)
        self.assertIn("时限", error)
        self.assertEqual(task["ledger"]["used"]["seconds"], 1)

    def test_unknown_network_wrapper_fails_without_unbounded_fallback(self):
        response = io.BytesIO(self.payload())
        response.fp = SimpleNamespace(raw=object())
        task = self.task()
        with patch("researchhelper.model.urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ContractError, "剩余等待时限"):
                self.service.reason(task, [])
        self.assertEqual(task["ledger"]["dispatches"][0]["failure_code"], "unsupported_response_reader")
        self.assertTrue(response.closed)

    def test_read_only_wrapper_is_not_used_as_read1(self):
        response = io.BytesIO(self.payload())
        response.read1 = None
        with patch("researchhelper.model.urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ContractError, "有界读取"):
                self.service.reason(self.task(), [])
        self.assertTrue(response.closed)

    def test_size_limit_rejects_the_first_excess_byte(self):
        response = io.BytesIO(b"x" * 131073)
        task = self.task()
        self.service.MAX_RESPONSE_BYTES = 131072
        with patch("researchhelper.model.urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ContractError, "大小限制"):
                self.service.reason(task, [])
        self.assertEqual(task["ledger"]["dispatches"][0]["failure_code"], "response_too_large")
        self.assertTrue(response.closed)

    def local_tls_case(self, *, trickle):
        key, cert = self.folder / "fixture.key", self.folder / "fixture.crt"
        subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost"],
                       check=True, capture_output=True, timeout=20)
        finished, stop, peer_closed = threading.Event(), threading.Event(), threading.Event()
        body = self.payload()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    if trickle:
                        for byte in body:
                            if stop.wait(.035):
                                break
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                    else:
                        self.wfile.write(body)
                        self.wfile.flush()
                except (OSError, ssl.SSLError):
                    peer_closed.set()
                finally:
                    finished.set()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(str(cert), str(key))
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        client_tls = ssl.create_default_context(cafile=str(cert))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=client_tls))
        self.settings.save({"endpoint": f"https://localhost:{server.server_port}/chat", "model": "offline-fixture", "api_key": self.secret})
        self.service.MAX_WAIT_SECONDS = .28 if trickle else 2
        task, start = self.task(), time.monotonic()
        try:
            with patch("researchhelper.model.urllib.request.urlopen", side_effect=opener.open) as transport:
                if trickle:
                    with self.assertRaisesRegex(ContractError, "超时|时限"):
                        self.service.reason(task, [])
                else:
                    self.assertEqual(self.service.reason(task, []), "fixture answer")
            elapsed = time.monotonic() - start
            if trickle:
                self.assertLess(elapsed, .8, "Tiny body must not extend into seconds of trickled response")
                self.assertGreaterEqual(elapsed, .2)
                self.assertTrue(peer_closed.wait(1), "Timed-out response must release its live TLS connection")
            transport.assert_called_once()
            record = task["ledger"]["dispatches"][0]
            self.assertTrue(record["response_read_deadline"])
            self.assertEqual(record["status"], "失败" if trickle else "成功")
            self.assertEqual(task["ledger"]["used"]["model_calls"], 1)
            self.assertAlmostEqual(record["elapsed_seconds"], task["ledger"]["used"]["seconds"])
            self.assertNotIn(self.secret, json.dumps(self.journal))
        finally:
            stop.set()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertTrue(finished.wait(2))

    def test_local_tls_slow_body_stops_and_closes_the_connection(self):
        self.local_tls_case(trickle=True)

    def test_local_tls_complete_body_is_accepted_after_socket_closes(self):
        self.local_tls_case(trickle=False)


if __name__ == "__main__":
    unittest.main()
