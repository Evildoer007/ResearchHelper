"""Authenticated loopback acceptance, using only fabricated HTTPS inference."""

import base64
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from researchhelper.service import make_server
from tests import test_autonomous_reasoning_offline as fixtures

ROOT = Path(__file__).resolve().parents[2]


class AutonomousInterfaceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="autonomous-interface-", dir=ROOT / "result/acceptance")
        self.addCleanup(folder.cleanup)
        # A trusted test launcher chooses this policy. Browser requests cannot.
        self.server = make_server(folder.name, ROOT, offline=False)
        self.server.agent.model.settings.save({"endpoint": "https://example.invalid/model", "model": "fabricated-fixture-model",
                                                "api_key": "fabricated-only-credential"})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.address = f"http://127.0.0.1:{self.server.server_port}"
        self.bootstrap = json.load(urlopen(self.address + "/api/bootstrap", timeout=5))

    def stop(self):
        for worker in self.server.agent.workers.values():
            worker.join(8)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(8)

    def post(self, path, payload, headers=None):
        fields = {"Content-Type": "application/json", "X-ResearchHelper-Token": self.bootstrap["token"]}
        fields.update(headers or {})
        request = Request(self.address + path, data=json.dumps(payload).encode(), headers=fields)
        return json.load(urlopen(request, timeout=8))

    def task(self):
        task = self.post("/api/tasks", {"question": "这份政策什么时候生效？", "objects": ["材料示例政策"], "cutoff": fixtures.CUTOFF})
        text = "材料示例政策\n\n发布日期：2026年7月19日。\n\n政策自2026年7月21日起生效。\n\n适用对象为符合条件的企业。"
        return self.post(f"/api/tasks/{task['id']}/material", {"name": "policy.txt", "base64": base64.b64encode(text.encode()).decode()})

    def test_user_input_and_attachment_reach_three_phase_model_path_and_frozen_report(self):
        self.assertFalse(self.bootstrap["offline"])
        self.assertFalse(self.bootstrap["model_settings"]["offline"])
        task = self.task()
        outputs = [fixtures.AutonomousReasoningTests.plan(task), {"evidence_specs": [fixtures.AutonomousReasoningTests.policy_spec(task)]},
                   fixtures.AutonomousReasoningTests.answer()]
        model = fixtures.FabricatedModel(outputs)
        with patch("researchhelper.model.urllib.request.urlopen", side_effect=model.respond):
            self.post(f"/api/tasks/{task['id']}/start", {})
            self.server.agent.workers[task["id"]].join(8)
        final = json.load(urlopen(f"{self.address}/api/tasks/{task['id']}", timeout=5))
        self.assertEqual(final["status"], "已完成", final.get("error"))
        self.assertEqual(final["run_mode"], "model")
        self.assertEqual(len(model.requests), 3)
        self.assertIn("analysis", final["reasoning_state"])
        html = urlopen(f"{self.address}/api/tasks/{task['id']}/report/1", timeout=5).read().decode()
        self.assertIn("2026年7月21日", html)
        self.assertNotIn("fabricated-only-credential", html)

    def test_web_model_settings_cannot_promote_execution_policy(self):
        previous = self.server.agent.model.settings.read(private=True)
        with self.assertRaises(HTTPError) as error:
            self.post("/api/settings/model", {"endpoint": "https://example.invalid/model", "model": "other", "offline": False})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.server.agent.model.settings.read(private=True), previous)

    def test_wrong_origin_cannot_start_an_autonomous_research_or_spend_calls(self):
        task = self.task()
        with self.assertRaises(HTTPError) as error, patch("researchhelper.model.urllib.request.urlopen", side_effect=AssertionError("External dispatch")):
            self.post(f"/api/tasks/{task['id']}/start", {}, {"Origin": "https://example.invalid"})
        self.assertEqual(error.exception.code, 400)
        final = self.server.agent.archive.get(task["id"])
        self.assertEqual(final["status"], "待开始")
        self.assertEqual(final["ledger"]["used"]["model_calls"], 0)
