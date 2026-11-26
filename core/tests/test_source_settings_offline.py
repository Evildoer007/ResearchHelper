"""Isolated source-configuration checks; no real credentials or provider traffic."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.contracts import ContractError
from researchhelper.settings import DataSettings


class SourceSettingsTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / "result" / "acceptance" / "source-settings"
        root.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.folder.cleanup)
        self.settings = DataSettings(self.folder.name)

    def test_defaults_do_not_read_environment_or_other_projects(self):
        with patch.dict(os.environ, {"IFIND_ACCESS_TOKEN": "foreign-access", "TINYSHARE_TOKEN": "foreign-tiny"}):
            self.assertEqual(self.settings.read(), {"ifind": {"has_access_token": False, "has_refresh_token": False}, "tinyshare": {"has_token": False}})
            self.assertEqual(self.settings.read(private=True), {"ifind": {"access_token": "", "refresh_token": ""}, "tinyshare": {"token": ""}})
        self.assertFalse(self.settings.path.exists())

    def test_private_atomic_save_and_public_flags(self):
        public = self.settings.save({"ifind": {"access_token": " access-fixture ", "refresh_token": "refresh-fixture"}, "tinyshare": {"token": "tiny-fixture"}})
        self.assertEqual(public, {"ifind": {"has_access_token": True, "has_refresh_token": True}, "tinyshare": {"has_token": True}})
        self.assertEqual(self.settings.read(private=True)["ifind"]["access_token"], "access-fixture")
        self.assertEqual(stat.S_IMODE(self.settings.path.stat().st_mode), 0o600)
        self.assertEqual(set(Path(self.folder.name).iterdir()), {self.settings.path, self.settings.secrets.root})
        profile = self.settings.path.read_text()
        for secret in ("access-fixture", "refresh-fixture", "tiny-fixture"):
            self.assertNotIn(secret, profile)
        for record in self.settings.secrets.root.iterdir():
            self.assertEqual(stat.S_IMODE(record.stat().st_mode), 0o600)
        self.assertNotIn("fixture", json.dumps(public))

    def test_blank_preserves_and_clear_wins(self):
        self.settings.save({"ifind": {"access_token": "access-fixture", "refresh_token": "refresh-fixture"}, "tinyshare": {"token": "tiny-fixture"}})
        self.settings.save({"ifind": {"access_token": " \t\n", "refresh_token": "replacement", "clear_refresh_token": True}, "tinyshare": {"token": "", "clear_token": False}})
        self.assertEqual(self.settings.read(private=True), {"ifind": {"access_token": "access-fixture", "refresh_token": ""}, "tinyshare": {"token": "tiny-fixture"}})
        self.settings.save({"ifind": {"clear_access_token": True}, "tinyshare": {"clear_token": True}})
        self.assertFalse(self.settings.read()["ifind"]["has_access_token"])
        self.assertFalse(self.settings.read()["tinyshare"]["has_token"])

    def test_bad_payloads_rejected_without_partial_writes(self):
        self.settings.save({"ifind": {"access_token": "original"}})
        original = self.settings.path.read_bytes()
        for payload in ([], {"wind": {}}, {"ifind": []}, {"ifind": {"password": "bad"}}, {"ifind": {"access_token": 123}}, {"ifind": {"clear_access_token": 1}}, {"tinyshare": {"clear_token": "true"}}, {"ifind": {"access_token": "header\r\ninjection"}}, {"ifind": {"access_token": "x" * 16385}}, {"ifind": {"access_token": "new"}, "tinyshare": {"token": None}}):
            with self.subTest(payload_type=type(payload).__name__), self.assertRaises(ContractError):
                self.settings.save(payload)
            self.assertEqual(self.settings.path.read_bytes(), original)

    def test_corruption_fails_closed_without_overwrite(self):
        for content in ('{"secret":"fixture",', '["fixture"]', '{"ifind":{},"tinyshare":{}}'):
            self.settings.path.write_text(content)
            with self.assertRaises(ContractError) as raised:
                self.settings.save({"ifind": {"access_token": "new"}})
            self.assertNotIn("fixture", str(raised.exception))
            self.assertEqual(self.settings.path.read_text(), content)

    def test_replace_failure_preserves_old_file_and_cleans_temporary(self):
        self.settings.save({"tinyshare": {"token": "old-fixture"}})
        old = self.settings.path.read_bytes()
        secrets = {p.name: p.read_bytes() for p in self.settings.secrets.root.iterdir()}
        with patch("researchhelper.credentials.os.replace", side_effect=OSError("fixture disk failure")), self.assertRaises(OSError):
            self.settings.save({"tinyshare": {"token": "new-fixture"}})
        self.assertEqual(self.settings.path.read_bytes(), old)
        self.assertEqual({p.name:p.read_bytes() for p in self.settings.secrets.root.iterdir()}, secrets)
        self.assertEqual(self.settings.read(private=True)["tinyshare"]["token"], "old-fixture")
        self.assertEqual(set(Path(self.folder.name).iterdir()), {self.settings.path, self.settings.secrets.root})
        profile = self.settings.path.read_text()
        for secret in ("access-fixture", "refresh-fixture", "tiny-fixture"):
            self.assertNotIn(secret, profile)
        for record in self.settings.secrets.root.iterdir():
            self.assertEqual(stat.S_IMODE(record.stat().st_mode), 0o600)
