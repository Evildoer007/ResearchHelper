from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.model_registry import (ModelCatalog, build_model_plan, fetch_model_catalog,
                                 suggest_model_configuration)
from llm.client import DeepSeekClient


class ModelRegistryTests(unittest.TestCase):
    def test_fetches_authenticated_catalog_and_writes_cache(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "object": "list",
            "data": [{"id": "deepseek-flash"}, {"id": "deepseek-next"}],
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "models.json"
            result = fetch_model_catalog(
                api_key="secret",
                base_url="https://api.deepseek.com",
                cache_path=path,
                request_get=Mock(return_value=response),
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.source, "live")
            self.assertEqual(result.models, ["deepseek-flash", "deepseek-next"])
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["models"], result.models)

    def test_uses_cache_when_live_catalog_temporarily_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "models.json"
            path.write_text(json.dumps({
                "ok": True,
                "models": ["deepseek-flash"],
                "fetched_at": "2026-09-16T10:00:00+01:00",
            }), encoding="utf-8")
            result = fetch_model_catalog(
                api_key="secret",
                base_url="https://api.deepseek.com",
                cache_path=path,
                request_get=Mock(side_effect=TimeoutError("offline")),
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.source, "cache")
            self.assertIn("TimeoutError", result.live_error)

    def test_fallback_requires_explicit_permission(self):
        catalog = ModelCatalog(True, models=["deepseek-flash"], source="live")
        requested = {"fast": "deepseek-flash", "quality": "retired-pro"}
        blocked = build_model_plan(catalog=catalog, requested=requested,
                                   fallback="deepseek-flash", allow_fallback=False)
        self.assertFalse(blocked.ok)
        self.assertEqual(blocked.unavailable, {"quality": "retired-pro"})
        allowed = build_model_plan(catalog=catalog, requested=requested,
                                   fallback="deepseek-flash", allow_fallback=True)
        self.assertTrue(allowed.ok)
        self.assertEqual(allowed.effective["quality"], "deepseek-flash")
        self.assertEqual(allowed.fallback_used, {"quality": "deepseek-flash"})

    def test_client_records_requested_and_effective_model(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "model": "deepseek-flash-v4.1",
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 3},
        }
        client = DeepSeekClient(model="deepseek-flash")
        client.api_key = "secret"
        with patch("llm.client.requests.post", return_value=response):
            result = client.chat([{"role": "user", "content": "hi"}], json_mode=False)
        self.assertTrue(result.ok)
        self.assertEqual(result.requested_model, "deepseek-flash")
        self.assertEqual(result.effective_model, "deepseek-flash-v4.1")

    def test_live_catalog_replaces_retired_slots_in_settings_form(self):
        choices, replaced = suggest_model_configuration(
            ["deepseek-flash", "deepseek-v4-pro"],
            fast="deepseek-v4-flash", quality="deepseek-v4-pro",
            fallback="deepseek-flash",
        )
        self.assertEqual(choices, {
            "fast": "deepseek-flash",
            "quality": "deepseek-v4-pro",
            "fallback": "deepseek-flash",
        })
        self.assertEqual(replaced, {"fast": "deepseek-v4-flash → deepseek-flash"})


if __name__ == "__main__":
    unittest.main()
