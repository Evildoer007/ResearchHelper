"""Preserve harmless empty user directories; never silently adopt private data."""
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_project_skill_update_offline as original


class EmptyDirectoryPreservation(unittest.TestCase):
    def setUp(self):
        self.fixture = original.ProjectSkillUpdateTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def install(self):
        fixture = self.fixture
        return fixture.installer.install_project_skill(fixture.archive, project_root=fixture.project,
            expected_sha256=hashlib.sha256(fixture.archive.read_bytes()).hexdigest(),
            recovery_root=fixture.recovery, preserve_empty_directories=True)

    def test_controlled_files_update_and_unknown_empty_directory_survives(self):
        target = self.fixture.previous(); (target / 'agents 2').mkdir()
        record = self.install()
        self.assertEqual(record['status'], 'installed')
        self.assertEqual(record['preserved_empty_directories'], ['agents 2'])
        self.assertEqual(list((target / 'agents 2').iterdir()), [])
        self.assertEqual(list((Path(record['recovery']) / 'agents 2').iterdir()), [])
        self.assertTrue((target / 'core/session.py').is_file())
        self.assertEqual(self.install()['status'], 'already_current')

    def test_unknown_files_nonempty_private_nested_or_symlinked_directories_are_refused(self):
        target = self.fixture.previous(); directory = target / 'agents 2'; directory.mkdir()
        (directory / 'notes.txt').write_text('用户材料')
        with self.assertRaises(RuntimeError): self.install()
        self.assertEqual((directory / 'notes.txt').read_text(), '用户材料')
        (directory / 'notes.txt').unlink()
        (directory / 'child').mkdir()
        with self.assertRaises(RuntimeError): self.install()
        (directory / 'child').rmdir()
        (target / 'data').mkdir()
        with self.assertRaises(RuntimeError): self.install()
        (target / 'data').rmdir()
        (directory / 'link').symlink_to(self.fixture.directory)
        with self.assertRaises(RuntimeError): self.install()
        self.assertEqual((target / 'SKILL.md').read_text(), 'old instructions')

    def test_modified_managed_source_is_never_overwritten(self):
        target = self.fixture.previous(); (target / 'agents 2').mkdir()
        (target / 'SKILL.md').write_text('保留本地修改')
        with self.assertRaises(RuntimeError): self.install()
        self.assertEqual((target / 'SKILL.md').read_text(), '保留本地修改')

    def test_promotion_failure_restores_old_installation_and_empty_directory(self):
        target = self.fixture.previous(); (target / 'agents 2').mkdir()
        original = self.fixture.installer.os.replace
        def fail(source, destination):
            if Path(destination) == target and Path(source).name.startswith('.researchhelper-install-'):
                raise OSError('fixture promotion failure')
            return original(source, destination)
        with patch.object(self.fixture.installer.os, 'replace', side_effect=fail):
            with self.assertRaises(OSError): self.install()
        self.assertEqual((target / 'SKILL.md').read_text(), 'old instructions')
        self.assertEqual(list((target / 'agents 2').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
