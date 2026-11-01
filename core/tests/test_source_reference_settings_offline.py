"""Retain source-setting regressions with the new OH-style storage fixture.

The original test file is untouched. Only the one-file storage assumption and
the obsolete replacement injection location are adapted; business assertions
and corruption/blank/clear checks are inherited unchanged.
"""
from pathlib import Path
from unittest.mock import patch
import test_source_settings_offline as legacy


class SourceReferenceSettingsTests(legacy.SourceSettingsTests):
    def test_private_atomic_save_and_public_flags(self):
        public = self.settings.save({"ifind": {"access_token": " access-fixture ", "refresh_token": "refresh-fixture"}, "tinyshare": {"token": "tiny-fixture"}})
        self.assertEqual(public, {"ifind": {"has_access_token": True, "has_refresh_token": True}, "tinyshare": {"has_token": True}})
        self.assertEqual(self.settings.read(private=True)["ifind"]["access_token"], "access-fixture")
        self.assertEqual(self.settings.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual({p.name for p in Path(self.folder.name).iterdir()}, {"source-settings.json", "credentials"})
        self.assertNotIn("fixture", self.settings.path.read_text())
        self.assertNotIn("fixture", str(public))

    def test_replace_failure_preserves_old_file_and_cleans_temporary(self):
        self.settings.save({"tinyshare": {"token": "old-fixture"}})
        old = self.settings.path.read_bytes()
        files = {p.name: p.read_bytes() for p in self.settings.secrets.root.iterdir()}
        with patch("researchhelper.credentials.os.replace", side_effect=OSError("fixture disk failure")), self.assertRaises(OSError):
            self.settings.save({"tinyshare": {"token": "new-fixture"}})
        self.assertEqual(self.settings.path.read_bytes(), old)
        self.assertEqual({p.name: p.read_bytes() for p in self.settings.secrets.root.iterdir()}, files)
        self.assertFalse(list(Path(self.folder.name).glob(".source-settings-*")))
