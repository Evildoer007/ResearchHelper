"""Model transport hardening with fabricated credentials and no network access."""

import copy
import io
import json
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler
from unittest.mock import patch

from researchhelper.contracts import ContractError, budget
from researchhelper.model import ModelService, ModelSettings


class ModelTransportHardeningTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="rh-model-hardening-")
        self.addCleanup(self.directory.cleanup)
        network = patch("socket.socket.connect", side_effect=AssertionError("Network forbidden in offline acceptance"))
        network.start()
        self.addCleanup(network.stop)
        self.settings = ModelSettings(self.directory.name)
        self.config = {"endpoint": "https://model.example.invalid/chat", "model": "fabricated-model",
                       "api_key": "AUDIT_ONLY_FAKE_SECRET_531"}
        self.settings.save(self.config)
        self.journal = []
        self.service = ModelService(self.settings, offline=False,
                                    journal=lambda task: self.journal.append(copy.deepcopy(task)))

    @staticmethod
    def task():
        return {"id": "audit-only-task", "batch": 1, "ledger": budget()}

    @staticmethod
    def response(text="fabricated answer", finish_reason="stop"):
        return io.BytesIO(json.dumps({"model": "fabricated-model", "choices": [
            {"message": {"content": text}, "finish_reason": finish_reason}
        ], "usage": {"total_tokens": 3}}).encode())

    def assert_failure_accounted(self, task):
        self.assertEqual(task["ledger"]["used"]["model_calls"], 1)
        self.assertEqual(len(task["ledger"]["dispatches"]), 1)
        self.assertNotEqual(task["ledger"]["dispatches"][0]["status"], "成功")
        self.assertGreaterEqual(task["ledger"]["used"]["seconds"], 0)
        self.assertGreaterEqual(len(self.journal), 2)
        self.assertEqual(self.journal[0]["ledger"]["used"]["model_calls"], 1)
        self.assertEqual(self.journal[-1]["ledger"], task["ledger"])
        self.assertNotIn(self.config["api_key"], json.dumps(self.journal))

    def test_cross_origin_302_does_not_forward_authorization(self):
        observed = []

        def transport(request, timeout):
            self.assertEqual(request.get_header("Authorization"), "Bearer " + self.config["api_key"])
            redirected = HTTPRedirectHandler().redirect_request(
                request, None, 302, "Found", {}, "https://other.example.invalid/collect")
            observed.append(redirected)
            self.assertIsNone(redirected.get_header("Authorization"))
            return self.response()

        with patch("researchhelper.model.urllib.request.urlopen", side_effect=transport) as request:
            self.assertEqual(self.service.reason(self.task(), []), "fabricated answer")
        self.assertEqual(len(observed), 1)
        request.assert_called_once()

    def test_saving_configuration_never_activates_offline_service(self):
        service = ModelService(self.settings, offline=True)
        self.settings.save(self.config)
        task = self.task()
        with patch("researchhelper.model.urllib.request.urlopen") as request:
            with self.assertRaises(ContractError):
                service.reason(task, [])
        request.assert_not_called()
        self.assertTrue(self.settings.read()["offline"])
        self.assertTrue(service.offline)
        self.assertEqual(task["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(task["ledger"]["dispatches"], [])

    def test_clear_key_requires_boolean_and_invalid_updates_preserve_file(self):
        original = self.settings.path.read_bytes()
        for value in ("true", "false", 0, 1, None, [], {}):
            with self.subTest(clear_key=value), self.assertRaises(ContractError):
                self.settings.save({**self.config, "clear_key": value})
            self.assertEqual(self.settings.path.read_bytes(), original)

    def test_control_characters_rejected_without_disclosing_or_saving_key(self):
        original = self.settings.path.read_bytes()
        for field in ("endpoint", "model", "api_key"):
            for control in ("\n", "\r", "\t", "\x00", "\x7f"):
                with self.subTest(field=field, control=repr(control)):
                    value = {**self.config, field: self.config[field] + control + "invalid"}
                    with self.assertRaises(ContractError) as caught:
                        self.settings.save(value)
                    self.assertNotIn(self.config["api_key"], str(caught.exception))
                    self.assertEqual(self.settings.path.read_bytes(), original)

    def test_corrupt_configuration_is_not_overwritten(self):
        invalid_records = ["{bad-json", "[]", json.dumps({**self.config, "api_key": []}),
                           json.dumps({"endpoint": self.config["endpoint"]}),
                           json.dumps({**self.config, "endpoint": "http://insecure.example.invalid"}),
                           json.dumps({**self.config, "unexpected": True})]
        for raw in invalid_records:
            with self.subTest(raw=raw):
                self.settings.path.write_text(raw)
                with self.assertRaises(ContractError):
                    self.settings.read()
                with self.assertRaises(ContractError):
                    self.settings.save(self.config)
                self.assertEqual(self.settings.path.read_text(), raw)

    def test_http_failure_classes_preserve_budget_without_secrets_or_retry(self):
        messages = []
        for status in (401, 402, 429):
            with self.subTest(status=status):
                self.journal.clear()
                task = self.task()
                failure = HTTPError(self.config["endpoint"], status, self.config["api_key"], {},
                                    io.BytesIO(self.config["api_key"].encode()))
                with patch("researchhelper.model.urllib.request.urlopen", side_effect=failure) as request:
                    with self.assertRaises(ContractError) as caught:
                        self.service.reason(task, [])
                request.assert_called_once()
                message = str(caught.exception)
                self.assertNotIn(self.config["api_key"], message)
                messages.append(message)
                self.assert_failure_accounted(task)
                failure.close()
        self.assertEqual(len(set(messages)), 3, "Authentication, billing and rate limits need distinct errors")

    def test_truncated_or_empty_model_content_is_not_success(self):
        for text, reason in (("partial answer", "length"), ("", "stop"), (" \n\t ", "stop")):
            with self.subTest(content=text, finish_reason=reason):
                self.journal.clear()
                task = self.task()
                with patch("researchhelper.model.urllib.request.urlopen", return_value=self.response(text, reason)) as request:
                    with self.assertRaises(ContractError):
                        self.service.reason(task, [])
                request.assert_called_once()
                self.assert_failure_accounted(task)


if __name__ == "__main__":
    unittest.main()
