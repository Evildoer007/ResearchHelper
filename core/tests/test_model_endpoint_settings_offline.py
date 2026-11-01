"""Base URL usability with OH-style origin-safe credential retention."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, budget
from researchhelper.model import ModelService, ModelSettings


class ModelEndpointSettings(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.settings = ModelSettings(self.folder.name)
        self.input = {"endpoint": "https://example.invalid/v1", "model": "fixture", "api_key": "INDEPENDENT_FAKE_SECRET"}

    def test_root_and_versioned_base_urls_resolve_idempotently(self):
        for base in ("https://example.invalid", "https://example.invalid/", "https://example.invalid/v1", "https://example.invalid/v1/", "https://example.invalid/proxy/v1/"):
            actual = ModelSettings.completion_endpoint(base)
            self.assertTrue(actual.endswith("/chat/completions"))
            self.assertEqual(ModelSettings.completion_endpoint(actual), actual)

    def test_explicit_complete_and_legacy_proxy_routes_are_preserved(self):
        for endpoint in ("https://example.invalid/v1/chat/completions", "https://example.invalid/private/custom-chat", "https://example.invalid/chat"):
            self.assertEqual(ModelSettings.completion_endpoint(endpoint), endpoint)
        self.assertEqual(ModelSettings.completion_endpoint("https://example.invalid/v1/chat/completions/"), "https://example.invalid/v1/chat/completions")

    def test_base_url_used_by_actual_shared_transport_without_rewriting_settings(self):
        self.settings.save(self.input)
        response = io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}).encode())
        with patch("researchhelper.model.urllib.request.urlopen", return_value=response) as sender:
            ModelService(self.settings, offline=False).reason({"batch": 1, "ledger": budget()}, [])
        self.assertEqual(sender.call_args.args[0].full_url, "https://example.invalid/v1/chat/completions")
        self.assertEqual(self.settings.read()["endpoint"], self.input["endpoint"])

    def test_new_origin_cannot_inherit_existing_secret(self):
        self.settings.save(self.input)
        raw = self.settings.path.read_bytes()
        with self.assertRaises(ContractError):
            self.settings.save(dict(self.input, endpoint="https://foreign.invalid/v1", api_key=""))
        self.assertEqual(raw, self.settings.path.read_bytes())
        self.assertEqual(self.settings.read(private=True)["api_key"], self.input["api_key"])

    def test_explicit_new_key_permits_origin_change(self):
        self.settings.save(self.input)
        self.settings.save(dict(self.input, endpoint="https://foreign.invalid/v1", api_key="NEW_FAKE_KEY"))
        self.assertEqual(self.settings.read(private=True)["api_key"], "NEW_FAKE_KEY")

    def test_explicit_clear_permits_origin_change_without_leaking_key(self):
        self.settings.save(self.input)
        self.settings.save(dict(self.input, endpoint="https://foreign.invalid/v1", api_key="", clear_key=True))
        self.assertFalse(self.settings.read()["has_key"])
        self.assertEqual(self.settings.read(private=True)["api_key"], "")

    def test_same_origin_path_or_default_port_changes_keep_key(self):
        self.settings.save(self.input)
        self.settings.save(dict(self.input, endpoint="https://EXAMPLE.invalid:443/v1/chat/completions", api_key=""))
        self.assertEqual(self.settings.read(private=True)["api_key"], self.input["api_key"])

    def test_different_port_is_another_origin(self):
        self.settings.save(self.input)
        with self.assertRaises(ContractError):
            self.settings.save(dict(self.input, endpoint="https://example.invalid:8443/v1", api_key=""))

    def test_invalid_port_rejected_before_any_write(self):
        with self.assertRaises(ContractError):
            self.settings.save(dict(self.input, endpoint="https://example.invalid:bad/v1"))
        self.assertFalse(self.settings.path.exists())

    def test_base_url_still_accepts_registered_direct_extraction(self):
        self.settings.save({"endpoint": "https://api.deepseek.com", "model": "deepseek-flash", "api_key": "FAKE", "extraction_mode": "direct"})
        self.assertEqual(self.settings.read()["extraction_mode"], "direct")
