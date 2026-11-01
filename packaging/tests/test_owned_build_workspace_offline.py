"""Owned system scratch must not become another persistent developer checkout."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import io

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / 'packaging'))
import build_all as builder


class OwnedBuildWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.owner = tempfile.TemporaryDirectory(prefix='scratch-owner-fixture-', dir=ROOT / 'result/acceptance')
        self.addCleanup(self.owner.cleanup)
        self.root = Path(self.owner.name)

    def test_owned_scratch_is_not_inside_project(self):
        with builder.temporary_build_workspace(self.root) as folder:
            self.assertTrue(folder.is_dir())
            self.assertFalse(folder.is_relative_to(self.root))
            self.assertTrue(builder.is_owned_build_workspace(folder, self.root))
        self.assertFalse(folder.exists())
        self.assertFalse(builder.is_owned_build_workspace(folder, self.root))

    def test_failure_removes_owned_files_not_project_files(self):
        keep = self.root / 'protected.txt'; keep.write_text('user source')
        with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
            with builder.temporary_build_workspace(self.root) as folder:
                (folder / 'owned-output').write_text('compiler output')
                raise RuntimeError('synthetic failure')
        self.assertFalse(folder.exists())
        self.assertEqual(keep.read_text(), 'user source')

    def test_cancel_removes_scratch_and_registration(self):
        with self.assertRaises(KeyboardInterrupt):
            with builder.temporary_build_workspace(self.root) as folder:
                raise KeyboardInterrupt()
        self.assertFalse(folder.exists())
        self.assertFalse(builder.is_owned_build_workspace(folder, self.root))

    def test_unknown_external_directory_is_not_authorized_by_name(self):
        with tempfile.TemporaryDirectory(prefix='researchhelper-current-') as folder:
            self.assertFalse(builder.is_owned_build_workspace(Path(folder), self.root))
            with self.assertRaises(ValueError):
                builder.build_candidate(self.root, workspace=Path(folder))
            self.assertFalse((Path(folder) / 'source').exists())

    def test_owner_is_bound_to_project_and_active_lifetime(self):
        other = self.root / 'unrelated'; other.mkdir()
        with builder.temporary_build_workspace(self.root) as folder:
            self.assertFalse(builder.is_owned_build_workspace(folder, other))
        with self.assertRaises(ValueError):
            builder.build_candidate(self.root, workspace=folder)

    def test_owned_preflight_failure_keeps_project_journal_only(self):
        with builder.temporary_build_workspace(self.root) as folder:
            with patch.object(builder, 'verify_environment', side_effect=RuntimeError('synthetic preflight')), redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'synthetic preflight'):
                    builder.build_candidate(self.root, workspace=folder)
            self.assertFalse((folder / 'source').exists())
        self.assertFalse(folder.exists())
        self.assertEqual(len(list(self.root.glob('result/build-records/*/candidate.json'))), 1)


if __name__ == '__main__':
    unittest.main()
