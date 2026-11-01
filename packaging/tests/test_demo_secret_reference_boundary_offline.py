"""Independent demo release boundary checks for the new local credential store."""
from pathlib import Path
import importlib.util
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("secret_boundary_demo_sync", ROOT / "packaging/demo/sync_demo.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class DemoSecretBoundary(unittest.TestCase):
    def test_private_credential_records_cannot_enter_release_inventory(self):
        for name in ("researchhelper/credentials/key.secret", "assets/test.secret", "app/web/credentials/test.json", "public/credentials/test.json"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                sync.release_name(name)

    def test_runtime_implementation_and_public_reports_remain_allowed(self):
        for name in ("researchhelper/credentials.py", "app/data/report_catalog.json", "app/data/reports/ai.html"):
            self.assertEqual(sync.release_name(name), name)
