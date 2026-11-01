"""Independent transport diagnostics acceptance; fabricated keys, no network."""

import io
import json
import ssl
import tempfile
import unittest
from urllib.error import URLError
from unittest.mock import patch

from researchhelper.contracts import ContractError, budget
from researchhelper.model import ModelService, ModelSettings


class ModelDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rh-model-diagnostics-")
        self.addCleanup(temporary.cleanup)
        self.settings = ModelSettings(temporary.name)
        self.secret = "OFFLINE_FAKE_KEY_NEVER_REAL"
        self.settings.save({"endpoint": "https://example.invalid/chat", "model": "offline-fixture", "api_key": self.secret})
        self.service = ModelService(self.settings, offline=False)
        network = patch("socket.socket.connect", side_effect=AssertionError("No external network in acceptance"))
        network.start()
        self.addCleanup(network.stop)

    def task(self, seconds=600, used=0):
        ledger = budget()
        ledger["limits"]["seconds"] = seconds
        ledger["used"]["seconds"] = used
        return {"id": "diagnostic-fixture", "batch": 1, "ledger": ledger}

    def payload(self, content="{}", reason="stop"):
        return json.dumps({"model": "offline-fixture", "choices": [{"finish_reason": reason,
            "message": {"content": content}}], "usage": {"total_tokens": 1}}).encode()

    def failure(self, code, *, payload=None, error=None):
        task = self.task()
        options = {"side_effect": error} if error is not None else {"return_value": io.BytesIO(payload)}
        with patch("researchhelper.model.urllib.request.urlopen", **options) as transport:
            with self.assertRaises(ContractError) as caught:
                self.service.reason(task, [])
        transport.assert_called_once()
        dispatch = task["ledger"]["dispatches"][0]
        self.assertEqual(dispatch["failure_code"], code)
        self.assertEqual(dispatch["status"], "失败")
        self.assertEqual(task["ledger"]["used"]["model_calls"], 1)
        self.assertIn("elapsed_seconds", dispatch)
        self.assertNotIn(self.secret, str(caught.exception) + json.dumps(dispatch))

    def test_normal_request_uses_bounded_wait_and_records_duration(self):
        task = self.task()
        with patch("researchhelper.model.urllib.request.urlopen", return_value=io.BytesIO(self.payload())) as transport:
            self.assertEqual(self.service.reason(task, []), "{}")
        self.assertEqual(transport.call_args.kwargs["timeout"], 90)
        dispatch = task["ledger"]["dispatches"][0]
        self.assertEqual(dispatch["timeout_seconds"], 90)
        self.assertEqual(dispatch["stage"], "已完成")
        self.assertEqual(dispatch["elapsed_seconds"], task["ledger"]["used"]["seconds"])

    def test_remaining_budget_preserves_delivery_reserve(self):
        task = self.task(seconds=100, used=15)
        with patch("researchhelper.model.urllib.request.urlopen", return_value=io.BytesIO(self.payload())) as transport:
            self.service.reason(task, [])
        self.assertEqual(transport.call_args.kwargs["timeout"], 25)

    def test_delivery_can_use_reserved_seconds(self):
        task = self.task(seconds=100, used=65)
        with patch("researchhelper.model.urllib.request.urlopen", return_value=io.BytesIO(self.payload())) as transport:
            self.service.reason(task, [], delivery=True)
        self.assertEqual(transport.call_args.kwargs["timeout"], 35)

    def test_exhausted_research_budget_never_dispatches(self):
        task = self.task(seconds=100, used=40)
        with patch("researchhelper.model.urllib.request.urlopen") as transport, self.assertRaises(ContractError):
            self.service.reason(task, [])
        transport.assert_not_called()
        self.assertEqual(task["ledger"]["used"]["model_calls"], 0)

    def test_direct_and_wrapped_timeouts_are_classified_without_retry(self):
        for error in (TimeoutError(self.secret), URLError(TimeoutError(self.secret))):
            with self.subTest(kind=type(error).__name__):
                self.failure("transport_timeout", error=error)

    def test_tls_and_connection_failures_have_distinct_codes(self):
        self.failure("tls_failure", error=URLError(ssl.SSLError(self.secret)))
        self.failure("connection_failed", error=URLError(self.secret))

    def test_invalid_json_and_encoding_are_safe_failures(self):
        for payload in (b"{broken", b"\xff\xff"):
            with self.subTest(payload=repr(payload)):
                self.failure("invalid_response_json", payload=payload)

    def test_incomplete_response_structure_is_not_success(self):
        for value in ([], {}, {"choices": []}, {"choices": [None]}, {"choices": [{"message": None}]}):
            with self.subTest(shape=value):
                self.failure("invalid_response_shape", payload=json.dumps(value).encode())

    def test_truncated_empty_and_unfinished_content_are_distinct(self):
        self.failure("content_truncated", payload=self.payload(reason="length"))
        self.failure("empty_content", payload=self.payload(content=None))
        for reason in ("content_filter", "tool_calls", "function_call"):
            self.failure("content_not_available", payload=self.payload(reason=reason))

    def test_rejected_output_keeps_provider_usage_but_no_private_reasoning(self):
        value = json.loads(self.payload(reason="length"))
        value["choices"][0]["message"]["reasoning_content"] = "PRIVATE_REASONING_DO_NOT_ARCHIVE"
        task = self.task()
        with patch("researchhelper.model.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(value).encode())):
            with self.assertRaises(ContractError):
                self.service.reason(task, [])
        dispatch = task["ledger"]["dispatches"][0]
        self.assertEqual(dispatch["usage"]["total_tokens"], 1)
        self.assertEqual(dispatch["finish_reason"], "length")
        self.assertNotIn("PRIVATE_REASONING_DO_NOT_ARCHIVE", json.dumps(dispatch))

    def test_known_reasoning_provider_has_space_without_switching_effort(self):
        for endpoint, model, expected in (
                ("https://api.deepseek.com/chat/completions", "deepseek-flash", 16384),
                ("https://api.deepseek.com/chat/completions", "deepseek-v4-pro", 16384),
                ("https://proxy.example.invalid/chat", "deepseek-flash", 8192),
                ("https://api.deepseek.com.evil.invalid/chat", "deepseek-flash", 8192),
                ("https://api.deepseek.com/chat/completions", "unknown", 8192)):
            with self.subTest(endpoint=endpoint, model=model):
                self.settings.save({"endpoint": endpoint, "model": model, "api_key": self.secret})
                task = self.task()
                with patch("researchhelper.model.urllib.request.urlopen", return_value=io.BytesIO(self.payload())) as transport:
                    self.service.reason(task, [])
                request = json.loads(transport.call_args.args[0].data)
                self.assertEqual(request["max_tokens"], expected)
                self.assertNotIn("thinking", request)
                self.assertNotIn("reasoning_effort", request)
                self.assertEqual(request["model"], model)
                self.assertEqual(task["ledger"]["dispatches"][0]["max_output_tokens"], expected)

    def test_large_response_is_rejected_before_decoding(self):
        self.failure("response_too_large", payload=b" " * (self.service.MAX_RESPONSE_BYTES + 2))

    def test_late_result_is_rejected_and_actual_elapsed_time_kept(self):
        task = self.task(seconds=100, used=15)
        with patch("researchhelper.model.time.monotonic", side_effect=[0, 26, 26]), \
             patch("researchhelper.model.urllib.request.urlopen", return_value=io.BytesIO(self.payload())):
            with self.assertRaises(ContractError):
                self.service.reason(task, [])
        self.assertEqual(task["ledger"]["used"]["seconds"], 41)
        self.assertEqual(task["ledger"]["dispatches"][0]["failure_code"], "response_deadline_exceeded")


if __name__ == "__main__":
    unittest.main()
