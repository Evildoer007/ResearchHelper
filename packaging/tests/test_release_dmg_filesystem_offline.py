"""Actual minimal signed image verifies the explicit release filesystem policy."""

import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from tests.test_release_bundle_copy_contract_offline import ReleaseBundleCopyContractTests


class ReleaseFilesystemTests(unittest.TestCase):
    def test_hfs_image_preserves_read_only_signature_and_declares_actual_policy(self):
        fixture = ReleaseBundleCopyContractTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        calls = []
        original = subprocess.run

        def capture(command, *args, **options):
            if command[:2] == ["hdiutil", "create"]:
                calls.append(command)
            return original(command, *args, **options)

        with patch.object(fixture.builder.subprocess, "run", side_effect=capture):
            image = fixture.builder.seal(fixture.bundle)
        self.assertTrue(image.is_file())
        self.assertEqual(len(calls), 1)
        command = calls[0]
        self.assertEqual(command[command.index("-fs") + 1], "HFS+")
        self.assertEqual(command[command.index("-format") + 1], "UDZO")
        manifest = json.loads((fixture.root / "app-manifest.json").read_text())
        self.assertEqual(manifest["dmg_filesystem"], "HFS+")
        self.assertTrue(manifest["distribution_signature_verified"])
        self.assertEqual(manifest["signature_verified_from"], "read-only mounted DMG")
        self.assertTrue((fixture.bundle / "Contents/Frameworks/libalias.dylib").is_symlink())
        self.assertFalse((fixture.root / "ResearchHelper.app.pre-seal-recovery").exists())
