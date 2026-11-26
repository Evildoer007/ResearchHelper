"""Provider failures through an isolated SDK and local HTTP, never real suppliers."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

from researchhelper.contracts import ContractError, budget
from researchhelper.evidence import EvidenceCollector
from researchhelper.source_transport import IsolatedEvidenceCollector, _source_worker

FAKE_TOKEN = "private-fixture-secret"


class TinySharePermissionError(Exception):
    pass


class FailureSDK:
    def fund_daily(self, **parameters):
        import requests
        response = requests.post(os.environ["SDK_FAILURE_ENDPOINT"],
            json={"operation": "fund_daily", "parameters": parameters}, timeout=2).json()
        mode = response["failure"]
        if mode == "expired":
            raise TinySharePermissionError("积分授权码已过期：" + FAKE_TOKEN)
        if mode == "permission":
            raise TinySharePermissionError("未开通权限：" + FAKE_TOKEN)
        raise TimeoutError(FAKE_TOKEN + " connection timed out")


def failure_worker(connection, deadline):
    connect = socket.socket.connect
    def local(sock, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError("Only the local synthetic SDK endpoint is allowed")
        return connect(sock, address)
    socket.socket.connect = local
    def pro_api(token, timeout):
        assert token == FAKE_TOKEN and timeout > 0
        return FailureSDK()
    sys.modules["tinyshare"] = types.SimpleNamespace(pro_api=pro_api)
    _source_worker(connection, deadline)


class FailureCollector(IsolatedEvidenceCollector):
    worker_target = staticmethod(failure_worker)


class ProviderFailureClassificationTests(unittest.TestCase):
    def setUp(self):
        self.task = {"id": "rh_provider_fixture", "version": 1, "batch": 1, "ledger": budget(),
            "cutoff": "2026-10-02T18:00:00+08:00", "objects": ["510300.SH"], "source_records": []}
        self.parameters = {"ts_code": "510300.SH", "start_date": "20260929", "end_date": "20260930",
            "fields": "ts_code,trade_date,close"}

    def dispatch_failure(self, mode):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                body = json.dumps({"failure": mode}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            collector = FailureCollector(offline=False)
            endpoint = f"http://127.0.0.1:{server.server_port}/query"
            with patch.dict(os.environ, SDK_FAILURE_ENDPOINT=endpoint, NO_PROXY="*"), self.assertRaises(ContractError) as caught:
                collector.external(self.task, "tinyshare", "fund_daily", self.parameters, {"token": FAKE_TOKEN})
            self.assertEqual(calls, [{"operation": "fund_daily", "parameters": self.parameters}])
            self.assertEqual(self.task["ledger"]["used"]["data_requests"], 1)
            self.assertEqual(self.task["ledger"]["used"]["model_calls"], 0)
            self.assertEqual(len(self.task["ledger"]["dispatches"]), 1)
            event = self.task["ledger"]["dispatches"][0]
            self.assertEqual(event["sdk_wire_requests_started"], 1)
            self.assertTrue(event["network_worker_reaped"])
            self.assertTrue(event["transport_authorizer_stopped"])
            self.assertTrue(event["transport_sender_stopped"])
            self.assertTrue(event["transport_receiver_stopped"])
            self.assertNotIn(FAKE_TOKEN, str(caught.exception) + json.dumps(self.task))
            self.assertEqual(self.task["source_records"], [])
            return event, str(caught.exception)
        finally:
            server.shutdown();server.server_close();thread.join(3)
            self.assertFalse(thread.is_alive())

    def test_expired_credential_is_classified_without_echo_retry_or_fallback(self):
        event, message = self.dispatch_failure("expired")
        self.assertEqual(event["failure_code"], "credential_expired")
        self.assertEqual(event["status"], "授权已过期")
        self.assertIn("更新ResearchHelper独立配置", message)

    def test_permission_failure_is_not_claimed_to_be_expiration(self):
        event, message = self.dispatch_failure("permission")
        self.assertEqual(event["failure_code"], "permission_denied")
        self.assertNotIn("过期", message)

    def test_unknown_transport_error_does_not_guess_permission(self):
        event, message = self.dispatch_failure("timeout")
        self.assertEqual(event["failure_code"], "transport_timeout")
        self.assertEqual(event["status"], "数据调用超时")
        self.assertNotIn("过期", message)
        self.assertNotIn("permission_denied", json.dumps(event))

    def test_offline_blocks_provider_import_and_dispatch(self):
        provider = types.SimpleNamespace(pro_api=Mock())
        with patch.dict(sys.modules, {"tinyshare": provider}), self.assertRaisesRegex(ContractError, "离线"):
            EvidenceCollector().external(self.task, "tinyshare", "fund_daily", self.parameters, {"token": FAKE_TOKEN})
        provider.pro_api.assert_not_called()
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 0)
