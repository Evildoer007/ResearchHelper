"""Independent early-rejection checks. Synthetic directories, no real packaging."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'packaging'))
import build_current as delivery


class BuilderReached(Exception):
    pass


class DeliveryPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='delivery-preflight-', dir=ROOT / 'result/acceptance')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.dist = self.root / 'dist'
        self.dist.mkdir()
        self.original = self.dist / 'ResearchHelper-0.10.0-arm64.dmg'
        self.original.write_bytes(b'protected old image')
        self.calls = []

    def builder(self, **kwargs):
        self.calls.append(kwargs)
        raise BuilderReached('synthetic builder boundary')

    def assert_early_rejection(self):
        with patch.object(delivery.sys, 'platform', 'darwin'):
            with self.assertRaisesRegex(RuntimeError, '未知项目|符号链接'):
                delivery.build_current(self.root, builder=self.builder)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.original.read_bytes(), b'protected old image')
        self.assertFalse((self.root / 'versions').exists())
        self.assertFalse((self.root / 'result/build-work').exists())
        self.assertFalse((self.root / 'result/build-records').exists())

    def test_renamed_download_copy_rejected_before_builder(self):
        duplicate = self.dist / 'ResearchHelper-0.10.0-arm64 2.dmg'
        duplicate.write_bytes(b'protected duplicate')
        self.assert_early_rejection()
        self.assertEqual(duplicate.read_bytes(), b'protected duplicate')

    def test_notes_are_preserved_and_not_archived(self):
        note = self.dist / 'notes.md'
        note.write_text('protected note')
        self.assert_early_rejection()
        self.assertEqual(note.read_text(), 'protected note')

    def test_extra_directory_is_not_recursively_removed(self):
        folder = self.dist / 'user-files'
        folder.mkdir()
        (folder / 'keep.txt').write_text('keep')
        self.assert_early_rejection()
        self.assertEqual((folder / 'keep.txt').read_text(), 'keep')

    def test_symlink_named_like_delivery_is_rejected(self):
        target = self.root / 'external.zip'
        target.write_bytes(b'keep linked target')
        (self.dist / 'ResearchHelper-Skill-0.10.0.zip').symlink_to(target)
        self.assert_early_rejection()
        self.assertEqual(target.read_bytes(), b'keep linked target')

    def test_linked_download_directory_is_rejected(self):
        other = self.root / 'other'
        other.mkdir()
        self.original.unlink()
        self.dist.rmdir()
        self.dist.symlink_to(other, target_is_directory=True)
        with patch.object(delivery.sys, 'platform', 'darwin'):
            with self.assertRaisesRegex(RuntimeError, '符号链接'):
                delivery.build_current(self.root, builder=self.builder)
        self.assertEqual(self.calls, [])
        self.assertTrue(self.dist.is_symlink())

    def test_expected_downloads_reach_builder_without_archiving(self):
        (self.dist / 'ResearchHelper-Skill-0.10.0.zip').write_bytes(b'old skill')
        (self.dist / 'current-release.json').write_text('{"formal":true}')
        with patch.object(delivery.sys, 'platform', 'darwin'):
            with self.assertRaises(BuilderReached):
                delivery.build_current(self.root, builder=self.builder)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.root / 'versions').exists())

    def test_windows_preflight_uses_its_own_destination(self):
        (self.dist / 'private-macos-note.txt').write_text('keep macOS side')
        target = self.root / 'result/windows-candidate'
        target.mkdir(parents=True)
        (target / 'unknown.txt').write_text('keep Windows side')
        with patch.object(delivery.sys, 'platform', 'win32'):
            with self.assertRaisesRegex(RuntimeError, '未知项目'):
                delivery.build_current(self.root, builder=self.builder)
        self.assertEqual(self.calls, [])
        self.assertEqual((target / 'unknown.txt').read_text(), 'keep Windows side')

    def test_windows_expected_candidate_does_not_validate_macos_notes(self):
        (self.dist / 'private-macos-note.txt').write_text('keep')
        target = self.root / 'result/windows-candidate'
        target.mkdir(parents=True)
        (target / 'ResearchHelper-0.10.0-windows-x64-setup.exe').write_bytes(b'old setup')
        with patch.object(delivery.sys, 'platform', 'win32'):
            with self.assertRaises(BuilderReached):
                delivery.build_current(self.root, builder=self.builder)
        self.assertEqual(len(self.calls), 1)

    def test_linked_windows_parent_cannot_redirect_preflight(self):
        external = self.root / 'keep-outside-output'
        external.mkdir()
        (external / 'windows-candidate').mkdir()
        artifact = external / 'windows-candidate/ResearchHelper-Skill-0.10.0.zip'
        artifact.write_bytes(b'keep outside candidate')
        (self.root / 'result').symlink_to(external, target_is_directory=True)
        with patch.object(delivery.sys, 'platform', 'win32'):
            with self.assertRaisesRegex(RuntimeError, '符号链接'):
                delivery.build_current(self.root, builder=self.builder)
        self.assertEqual(self.calls, [])
        self.assertEqual(artifact.read_bytes(), b'keep outside candidate')


if __name__ == '__main__':
    unittest.main()
