"""Independent safety checks for the authorized connection-only acceptance."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import build_current
spec = importlib.util.spec_from_file_location("connection_acceptance",
    ROOT / "tests/accept_authorized_deepseek_connection.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class AuthorizedConnectionSafety(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        (self.root / "result/acceptance").mkdir(parents=True)
        self.production = self.root / "private-app"
        self.key = "independent-test-credential-not-real"
        self.started = 0
        for target, value in (("ROOT", self.root), ("PRODUCTION", self.production)):
            guard = patch.object(probe, target, value)
            guard.start()
            self.addCleanup(guard.stop)
        guard = patch.object(build_current, "verify_current_build", return_value={})
        self.verify = guard.start()
        self.addCleanup(guard.stop)

    def simulate(self, task, messages, delivery=False, phase=None):
        self.started += 1
        self.assertTrue(delivery)
        self.assertEqual(phase, "extract")
        self.assertEqual(task["ledger"]["limits"]["model_calls"], 1)
        task["ledger"]["used"]["model_calls"] += 1
        task["ledger"]["dispatches"].append({"kind": "model_calls", "status": "成功"})
        return '{"ok":true}'

    def execute(self, transport=None):
        with patch.object(probe.ConnectionProbe, "reason", side_effect=transport or self.simulate):
            with contextlib.redirect_stdout(io.StringIO()):
                return probe.run(self.key)

    def test_success_saves_only_private_configuration_not_execution(self):
        record = self.execute()
        self.assertEqual(record["status"], "scoped_passed")
        self.assertEqual(self.started, 1)
        saved = probe.ModelSettings(self.production).read()
        self.assertTrue(saved["has_key"])
        self.assertEqual(saved["model"], "deepseek-flash")
        self.assertEqual((self.production / "model-settings.json").stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.production / "execution-policy.json").exists())
        self.assertFalse(record["full_app_research_verified"])

    def test_credential_absent_from_every_receipt(self):
        self.execute()
        for file in (self.root / "result").rglob("*"):
            if file.is_file():
                self.assertNotIn(self.key, file.read_text())

    def test_failure_does_not_save_or_retry(self):
        def failed(task, *args, **kwargs):
            self.started += 1
            task["ledger"]["used"]["model_calls"] = 1
            task["ledger"]["dispatches"].append({"kind": "model_calls", "failure_code": "authentication_failed"})
            raise probe.ContractError("Sensitive provider text is not retained")
        record = self.execute(failed)
        self.assertEqual(record["failure_code"], "authentication_failed")
        self.assertEqual(self.started, 1)
        self.assertEqual(record["real_model_requests"], 1)
        self.assertFalse((self.production / "model-settings.json").exists())

    def test_wrong_reply_does_not_save(self):
        self.assertEqual(self.execute(lambda *a, **kw: '{"ok":false}')["status"], "failed")
        self.assertFalse((self.production / "model-settings.json").exists())

    def test_existing_configuration_not_overwritten_or_contacted(self):
        settings = probe.ModelSettings(self.production)
        settings.save({"endpoint": "https://example.org/chat/completions", "model": "existing", "api_key": "existing-secret"})
        original = settings.path.read_bytes()
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assertEqual(settings.path.read_bytes(), original)
        self.assertEqual(self.started, 0)

    def test_concurrent_configuration_preserved(self):
        def changed(task, *args, **kwargs):
            self.simulate(task, *args, **kwargs)
            probe.ModelSettings(self.production).save({"endpoint": "https://example.org/chat/completions", "model": "new-choice", "api_key": "user-choice"})
            return '{"ok":true}'
        self.assertEqual(self.execute(changed)["status"], "failed")
        self.assertEqual(probe.ModelSettings(self.production).read()["model"], "new-choice")

    def test_arbitrary_failure_text_not_logged(self):
        def failed(*args, **kwargs):
            raise RuntimeError(self.key)
        self.assertEqual(self.execute(failed)["failure_code"], "probe_local_failure")
        for receipt in (self.root / "result").rglob("receipt.json"):
            self.assertNotIn(self.key, receipt.read_text())

    def test_probe_output_cap_does_not_change_research_cap(self):
        config = probe.MemoryProbeSettings(self.key).read(private=True)
        self.assertEqual(probe.ConnectionProbe.output_limit(config), 128)
        self.assertEqual(probe.IsolatedModelService.output_limit(config), 16384)

    def test_candidate_integrity_required_before_request(self):
        self.verify.side_effect = RuntimeError("candidate changed")
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assertEqual(self.started, 0)
        self.assertFalse(self.production.exists())


if __name__ == "__main__":
    unittest.main()
