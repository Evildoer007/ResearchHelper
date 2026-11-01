"""Independent destination diagnostics, using only owned synthetic fixtures.

No package build, provider request, real download edit or foreground UI.
"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'packaging'))
import build_current as delivery


class DeliveryDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='delivery-diagnostics-',
                                                     dir=ROOT / 'result/acceptance')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.dist = self.root / 'dist'
        self.dist.mkdir()
        self.good = self.dist / 'ResearchHelper-0.10.0-arm64.dmg'
        self.good.write_bytes(b'protected synthetic macOS download')

    def reject_before_build(self, platform='darwin'):
        with patch.object(delivery.sys, 'platform', platform), \
             patch.object(delivery, 'build_lock') as lock:
            builder = unittest.mock.Mock(side_effect=AssertionError('must not reach builder'))
            with self.assertRaises(RuntimeError) as caught:
                delivery.build_current(self.root, builder=builder)
            builder.assert_not_called()
            lock.assert_not_called()
        self.assertFalse((self.root / 'versions').exists())
        self.assertFalse((self.root / 'result/build-work').exists())
        self.assertFalse((self.root / 'result/build-records').exists())
        self.assertEqual(self.good.read_bytes(), b'protected synthetic macOS download')
        return str(caught.exception)

    def test_all_unknown_items_reported_in_one_read_only_pass(self):
        names = ('ResearchHelper-0.10.0-arm64 2.dmg', 'ResearchHelper-Skill-0.10.0 2.zip')
        for name in names:
            (self.dist / name).write_bytes(b'protected renamed download')
        message = self.reject_before_build()
        self.assertIn(str(self.dist), message)
        for name in names:
            self.assertIn(name, message)
            self.assertEqual((self.dist / name).read_bytes(), b'protected renamed download')
        self.assertIn('未开始构建', message)
        self.assertIn('确认', message)

    def test_directories_and_links_identified_without_following_targets(self):
        folder = self.dist / 'private-notes'
        folder.mkdir()
        (folder / 'keep.txt').write_bytes(b'protected nested file')
        target = self.root / 'private-source.zip'
        target.write_bytes(b'protected link target')
        link = self.dist / 'ResearchHelper-Skill-0.10.0.zip'
        link.symlink_to(target)
        message = self.reject_before_build()
        self.assertIn('private-notes', message)
        self.assertIn('目录', message)
        self.assertIn(link.name, message)
        self.assertIn('符号链接', message)
        self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_bytes(), b'protected link target')
        self.assertEqual((folder / 'keep.txt').read_bytes(), b'protected nested file')

    def test_control_characters_in_names_cannot_forge_diagnostic_lines(self):
        name = 'note\nBUILD SUCCESS\t.txt'
        (self.dist / name).write_text('keep')
        message = self.reject_before_build()
        self.assertNotIn('\nBUILD SUCCESS', message)
        self.assertIn('note\\nBUILD SUCCESS\\t.txt', message)

    def test_macos_downloads_reject_windows_installer(self):
        file = self.dist / 'ResearchHelper-0.10.0-windows-x64-setup.exe'
        file.write_bytes(b'protected synthetic Windows installer')
        message = self.reject_before_build()
        self.assertIn(file.name, message)
        self.assertIn('其他平台', message)
        self.assertEqual(file.read_bytes(), b'protected synthetic Windows installer')

    def test_windows_downloads_reject_macos_image_without_inspecting_dist(self):
        target = self.root / 'result/windows-candidate'
        target.mkdir(parents=True)
        file = target / self.good.name
        file.write_bytes(b'protected misplaced image')
        (self.dist / 'keep-notes.txt').write_text('macOS directory not inspected')
        message = self.reject_before_build('win32')
        self.assertIn(str(target), message)
        self.assertIn('其他平台', message)
        self.assertNotIn('keep-notes.txt', message)
        self.assertEqual(file.read_bytes(), b'protected misplaced image')

    def test_dot_dot_escape_rejected_before_directory_listing(self):
        # The lexical prefix lies inside root; the normalized path is outside it.
        target = self.root / 'dist/../../outside-downloads'
        with patch.object(Path, 'iterdir', side_effect=AssertionError('must not inspect outside')):
            with self.assertRaisesRegex(RuntimeError, '超出本项目'):
                delivery.delivery_entries(target, self.root)

    def test_parent_symlink_not_hidden_by_dot_dot_normalization(self):
        outside = self.root / 'owned-external'
        outside.mkdir()
        (self.root / 'linked').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, '符号链接'):
            delivery.delivery_entries(self.root / 'linked/../dist', self.root)
        self.assertTrue((self.root / 'linked').is_symlink())

    def test_destination_file_has_clear_error_without_creating_output(self):
        target = self.root / 'result/windows-candidate'
        target.parent.mkdir()
        target.write_bytes(b'protected ordinary file')
        message = self.reject_before_build('win32')
        self.assertIn('不是目录', message)
        self.assertIn(str(target), message)
        self.assertEqual(target.read_bytes(), b'protected ordinary file')


if __name__ == '__main__':
    unittest.main()
