"""Release-copy gates cannot relabel background checks as native acceptance."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("rh_demo_sync_gate", ROOT / "packaging/sync_demo.py")
sync_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync_module)


class DemoAcceptanceModeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="demo-mode-", dir=ROOT / "result/acceptance")
        self.root = Path(self.folder.name)
        self.directory = self.root / "result/acceptance"
        self.source = self.root / "app/web/workspace.js"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("/* isolated fixture */")
        hashes = {"app/web/workspace.js": hashlib.sha256(self.source.read_bytes()).hexdigest()}
        self.write("package-background.json", {"version": "0.10.0", "status": "passed", "dmg_sha256": "current",
                                               "backend_stopped": True, "mount_detached": True, "native_ui_verified": False})
        self.write("package.json", {"version": "0.10.0", "image_sha256": "current"})
        browser = {"version": "0.10.0", "errors": [], "source_hashes": hashes}
        self.write("autonomous-composer/full-chat/acceptance.json", browser)
        self.write("chat-port/acceptance.json", browser)
        self.write("roles-workspace/acceptance.json", {"errors": [], "headless": True,
                                                       "production_store_touched": False, "source_hashes": hashes})
        self.write("integrity.json", {"version": "0.10.0"})
        self.write("native-intensive/port-acceptance.json", {"version": "0.10.0", "errors": [], "image_sha256": "previous"})
        release = self.root / "dist/0.10.0/release-manifest.json"
        release.parent.mkdir(parents=True)
        release.write_text(json.dumps({"artifacts": {"dmg": {"sha256": "current"}}, "source_hashes": hashes}))
        self.scope = patch.object(sync_module, "ROOT", self.root)
        self.scope.start()

    def tearDown(self):
        self.scope.stop()
        self.folder.cleanup()

    def write(self, name, value):
        path = self.directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def test_background_candidate_keeps_native_unverified(self):
        value = sync_module.acceptance(background_candidate=True)
        self.assertEqual(value["acceptance_mode"], "background-candidate")
        self.assertFalse(value["native_ui_verified"])
        self.assertEqual(value["app_image_sha256"], "current")

    def test_default_native_gate_still_rejects_old_native_image(self):
        with self.assertRaisesRegex(ValueError, "native UI acceptance"):
            sync_module.acceptance()

    def test_native_gate_requires_matching_actual_native_evidence(self):
        self.write("native-intensive/port-acceptance.json", {"version": "0.10.0", "errors": [], "image_sha256": "current"})
        value = sync_module.acceptance()
        self.assertTrue(value["native_ui_verified"])
        self.assertEqual(value["acceptance_mode"], "installed-native")

    def test_background_cannot_use_stale_or_unfinished_package(self):
        for field, value in (("status", "failed"), ("dmg_sha256", "previous"), ("backend_stopped", False), ("mount_detached", False)):
            package = {"version": "0.10.0", "status": "passed", "dmg_sha256": "current", "backend_stopped": True, "mount_detached": True}
            package[field] = value
            self.write("package-background.json", package)
            with self.subTest(field=field), self.assertRaises(ValueError):
                sync_module.acceptance(True)

    def test_current_source_must_match_browser_and_role_evidence(self):
        self.source.write_text("/* changed after testing */")
        with self.assertRaisesRegex(ValueError, "different source"):
            sync_module.acceptance(True)

    def test_role_errors_cannot_be_ignored_by_background_gate(self):
        self.write("roles-workspace/acceptance.json", {"errors": ["fixture failure"], "headless": True})
        with self.assertRaisesRegex(ValueError, "Role workspace"):
            sync_module.acceptance(True)

    def test_acceptance_mode_must_be_explicit_boolean(self):
        with self.assertRaisesRegex(ValueError, "explicit"):
            sync_module.acceptance("background")
