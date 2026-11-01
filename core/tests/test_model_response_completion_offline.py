"""HTTP response framing and disconnects; fabricated transport, no network."""

import copy
import http.client
import io
import json
import ssl
import tempfile
import socket
import subprocess
import threading
import unittest
import urllib.request
from urllib.error import URLError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from researchhelper.contracts import ContractError, budget
from researchhelper.model import ModelService, ModelSettings

ROOT = Path(__file__).resolve().parents[2]


class FramedResponse(io.BytesIO):
    def __init__(self, body, declared=None, failure=None):
        super().__init__(body)
        self.length = len(body) if declared is None else declared
        self.failure = failure
        self.reads = 0

    def read1(self, size=-1):
        self.reads += 1
        if self.failure is not None:
            raise self.failure
        return super().read1(size)


class ModelResponseCompletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rh-response-completion-", dir=ROOT / "result/acceptance")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.secret = "FABRICATED_HTTP_PRIVATE_KEY"
        self.settings = ModelSettings(temporary.name)
        self.settings.save({"endpoint": "https://example.invalid/model", "model": "offline-fixture", "api_key": self.secret})
        self.snapshots = []
        self.service = ModelService(self.settings, offline=False, journal=lambda task: self.snapshots.append(copy.deepcopy(task)))
        connect = socket.socket.connect
        def local_only(connection, address):
            if isinstance(address, tuple) and address[0] not in ("localhost", "127.0.0.1", "::1"):
                raise AssertionError("External network forbidden")
            return connect(connection, address)
        network = patch("socket.socket.connect", local_only)
        network.start()
        self.addCleanup(network.stop)

    def body(self):
        return json.dumps({"model": "offline-fixture", "choices": [{"finish_reason": "stop",
            "message": {"content": "{}", "reasoning_content": "PRIVATE_BODY_NEVER_RECORD"}}],
            "usage": {"total_tokens": 1}}).encode()

    def run_response(self, response, code=None):
        task = {"id": "completion-fixture", "batch": 1, "ledger": budget()}
        with patch("researchhelper.model.urllib.request.urlopen", return_value=response) as transport:
            if code:
                with self.assertRaises(ContractError) as error:
                    self.service.reason(task, [])
                self.assertNotIn(self.secret, str(error.exception))
            else:
                self.assertEqual(self.service.reason(task, []), "{}")
        transport.assert_called_once()
        self.assertTrue(response.closed)
        self.assertEqual(task["ledger"]["used"]["model_calls"], 1)
        self.assertEqual(len(self.snapshots), 2)
        event = self.snapshots[-1]["ledger"]["dispatches"][0]
        self.assertEqual(event["status"], "失败" if code else "成功")
        if code:
            self.assertEqual(event["failure_code"], code)
            self.assertNotIn("response_body_complete", event)
        self.assertNotIn(self.secret, json.dumps(event))
        self.assertNotIn("PRIVATE_BODY_NEVER_RECORD", json.dumps(event))
        return event

    def test_complete_content_length_keeps_only_safe_byte_counters(self):
        body = self.body()
        event = self.run_response(FramedResponse(body))
        self.assertTrue(event["response_body_complete"])
        self.assertEqual(event["received_response_bytes"], len(body))
        self.assertEqual(event["declared_response_bytes"], len(body))

    def test_parseable_json_does_not_override_incomplete_content_length(self):
        body = self.body()
        event = self.run_response(FramedResponse(body, declared=len(body) + 5), "response_incomplete")
        self.assertEqual(event["received_response_bytes"], len(body))
        self.assertNotIn("usage", event)

    def test_invalid_json_prefix_is_not_decoded_before_framing_check(self):
        self.run_response(FramedResponse(b'{"choices":', declared=200), "response_incomplete")

    def test_empty_body_with_positive_length_is_incomplete(self):
        self.run_response(FramedResponse(b'', declared=20), "response_incomplete")

    def test_claimed_body_over_limit_is_rejected_before_reading(self):
        response = FramedResponse(self.body(), declared=self.service.MAX_RESPONSE_BYTES + 1)
        self.run_response(response, "response_too_large")
        self.assertEqual(response.reads, 0)

    def test_chunked_incomplete_read_does_not_archive_partial_content(self):
        self.run_response(FramedResponse(b'', declared=200,
            failure=http.client.IncompleteRead(self.secret.encode(), 100)), "response_incomplete")

    def test_remote_closed_reset_abort_and_tls_eof_are_interrupted(self):
        for error in (http.client.RemoteDisconnected(self.secret), ConnectionResetError(self.secret),
                      ConnectionAbortedError(self.secret), BrokenPipeError(self.secret), ssl.SSLEOFError(self.secret)):
            with self.subTest(kind=type(error).__name__):
                self.snapshots.clear()
                self.run_response(FramedResponse(b'', failure=error), "connection_interrupted")

    def test_invalid_http_status_line_is_protocol_failure(self):
        self.run_response(FramedResponse(b'', failure=http.client.BadStatusLine(self.secret)), "http_protocol_failure")

    def test_unknown_length_accepts_valid_close_delimited_body(self):
        response = FramedResponse(self.body())
        response.length = None
        event = self.run_response(response)
        self.assertNotIn("declared_response_bytes", event)
        self.assertTrue(event["response_body_complete"])

    def test_wrapped_transport_errors_keep_the_same_safe_classification(self):
        for error, code in ((http.client.IncompleteRead(self.secret.encode()), "response_incomplete"),
                            (ConnectionResetError(self.secret), "connection_interrupted"),
                            (ssl.SSLEOFError(self.secret), "connection_interrupted"),
                            (http.client.BadStatusLine(self.secret), "http_protocol_failure")):
            with self.subTest(code=code):
                task = {"id": "wrapped-fixture", "batch": 1, "ledger": budget()}
                with patch("researchhelper.model.urllib.request.urlopen", side_effect=URLError(error)) as transport:
                    with self.assertRaises(ContractError):
                        self.service.reason(task, [])
                transport.assert_called_once()
                self.assertEqual(task["ledger"]["dispatches"][0]["failure_code"], code)
                self.assertNotIn(self.secret, json.dumps(task))

    def test_unexpected_error_records_only_fixed_category_and_safe_errno(self):
        for error, category in ((ValueError(self.secret), "value_error"),
                                (OSError(9, self.secret), "os_error")):
            with self.subTest(category=category):
                task = {"id": "unexpected-fixture", "batch": 1, "ledger": budget()}
                with patch("researchhelper.model.urllib.request.urlopen", side_effect=error):
                    with self.assertRaises(ContractError):
                        self.service.reason(task, [])
                event = task["ledger"]["dispatches"][0]
                self.assertEqual(event["failure_code"], "unexpected_transport_failure")
                self.assertEqual(event["exception_category"], category)
                self.assertNotIn(self.secret, json.dumps(event))
                if category == "os_error":
                    self.assertEqual(event["os_error_number"], 9)

    def test_actual_tls_parseable_but_short_body_is_rejected(self):
        body = self.body()
        key, cert = self.directory / "tls.key", self.directory / "tls.crt"
        subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost"],
            check=True, capture_output=True, timeout=20)
        finished = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Length", str(len(body) + 5))
                self.end_headers()
                try:
                    self.wfile.write(body)
                    self.wfile.flush()
                finally:
                    self.close_connection = True
                    finished.set()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(str(cert), str(key))
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(cert))))
        self.settings.save({"endpoint": f"https://localhost:{server.server_port}/model", "model": "offline-fixture", "api_key": self.secret})
        task = {"id": "tls-fixture", "batch": 1, "ledger": budget()}
        try:
            with patch("researchhelper.model.urllib.request.urlopen", side_effect=opener.open):
                with self.assertRaises(ContractError):
                    self.service.reason(task, [])
            event = task["ledger"]["dispatches"][0]
            self.assertEqual(event["failure_code"], "response_incomplete")
            self.assertEqual(event["received_response_bytes"], len(body))
            self.assertEqual(task["ledger"]["used"]["model_calls"], 1)
            self.assertTrue(finished.wait(2))
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
