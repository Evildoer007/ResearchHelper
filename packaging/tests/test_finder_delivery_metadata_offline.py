"""Owned Finder-header fixtures; preserve metadata without relaxing package checks."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'packaging'))
import build_current as delivery


class FinderDeliveryMetadataTests(unittest.TestCase):
    def setUp(self):
        owner = tempfile.TemporaryDirectory(prefix='finder-metadata-', dir=ROOT / 'result/acceptance')
        self.addCleanup(owner.cleanup)
        self.root = Path(owner.name).resolve()
        self.dist = self.root / 'dist'
        self.dist.mkdir()
        self.old = self.dist / 'ResearchHelper-0.10.0-arm64.dmg'
        self.old.write_bytes(b'protected synthetic image')
        self.metadata = self.dist / '.DS_Store'
        self.content = b'\x00\x00\x00\x01Bud1' + b'owned metadata fixture, not a complete Finder database'
        self.metadata.write_bytes(self.content)

    def test_only_valid_regular_metadata_excluded_from_download_members(self):
        with patch.object(delivery.sys, 'platform', 'darwin'):
            self.assertEqual(delivery.delivery_entries(self.dist, self.root), [self.old])
        self.assertEqual(self.metadata.read_bytes(), self.content)
        self.assertFalse((self.root / 'versions').exists())

    def test_metadata_does_not_hide_unknown_download_copies(self):
        unknown = self.dist / 'ResearchHelper-0.10.0-arm64 2.dmg'
        unknown.write_bytes(b'protected user copy')
        with patch.object(delivery.sys, 'platform', 'darwin'):
            with self.assertRaisesRegex(RuntimeError, 'arm64 2.dmg'):
                delivery.delivery_entries(self.dist, self.root)
        self.assertEqual(unknown.read_bytes(), b'protected user copy')
        self.assertEqual(self.metadata.read_bytes(), self.content)

    def test_invalid_header_keeps_file_and_rejects_build(self):
        self.metadata.write_bytes(b'user note must not be called metadata')
        with patch.object(delivery.sys, 'platform', 'darwin'):
            with self.assertRaisesRegex(RuntimeError, 'DS_Store'):
                delivery.delivery_entries(self.dist, self.root)
        self.assertEqual(self.metadata.read_bytes(), b'user note must not be called metadata')

    def test_link_named_like_metadata_not_followed(self):
        self.metadata.unlink()
        target = self.root / 'private-file'
        target.write_bytes(self.content)
        self.metadata.symlink_to(target)
        with patch.object(delivery.sys, 'platform', 'darwin'):
            with self.assertRaisesRegex(RuntimeError, '符号链接'):
                delivery.delivery_entries(self.dist, self.root)
        self.assertEqual(target.read_bytes(), self.content)
        self.assertTrue(self.metadata.is_symlink())

    def test_windows_does_not_gain_unrelated_metadata_exception(self):
        with patch.object(delivery.sys, 'platform', 'win32'):
            with self.assertRaisesRegex(RuntimeError, 'DS_Store'):
                delivery.delivery_entries(self.dist, self.root)

    def test_archive_does_not_turn_finder_preferences_into_release_artifact(self):
        with patch.object(delivery.sys, 'platform', 'darwin'):
            files = delivery.archive_deliveries(self.dist, self.root)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), self.old.read_bytes())
        self.assertFalse(list((self.root / 'versions').rglob('.DS_Store')))
        self.assertEqual(self.metadata.read_bytes(), self.content)

    def test_successful_swap_preserves_metadata_bytes(self):
        staged = self.root / 'staged'
        staged.mkdir()
        (staged / self.old.name).write_bytes(b'new synthetic image')
        with patch.object(delivery.sys, 'platform', 'darwin'):
            delivery.replace_delivery(staged, self.dist)
        self.assertEqual(self.old.read_bytes(), b'new synthetic image')
        self.assertEqual(self.metadata.read_bytes(), self.content)

    def test_swap_failure_rolls_back_package_and_metadata(self):
        staged = self.root / 'staged'
        staged.mkdir()
        (staged / self.old.name).write_bytes(b'new synthetic image')
        rename = Path.rename
        def fail_install(path, target):
            if path.name == 'delivery':
                raise OSError('owned swap failure')
            return rename(path, target)
        with patch.object(delivery.sys, 'platform', 'darwin'), patch.object(Path, 'rename', fail_install):
            with self.assertRaises(OSError):
                delivery.replace_delivery(staged, self.dist)
        self.assertEqual(self.old.read_bytes(), b'protected synthetic image')
        self.assertEqual(self.metadata.read_bytes(), self.content)

    def test_final_candidate_gate_uses_same_metadata_contract(self):
        # Reuse the existing synthetic builder, not a real compiler or package.
        from tests.test_current_delivery_offline import CurrentDeliveryTests
        fixture = CurrentDeliveryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        metadata = fixture.dist / '.DS_Store'
        metadata.write_bytes(self.content)
        with patch.object(delivery.sys, 'platform', 'darwin'), \
             patch.object(delivery, 'verify_release_binding'):
            result = delivery.build_current(fixture.root, builder=fixture.builder)
            record = Path(result['payload']) / 'candidate.json'
            self.assertEqual(delivery.verify_current_build(record, fixture.root), result)
            self.assertEqual(metadata.read_bytes(), self.content)
            unknown = fixture.dist / 'user-note.txt'
            unknown.write_bytes(b'keep user note')
            with self.assertRaises(RuntimeError):
                delivery.verify_current_build(record, fixture.root)
            self.assertEqual(unknown.read_bytes(), b'keep user note')


if __name__ == '__main__':
    unittest.main()
