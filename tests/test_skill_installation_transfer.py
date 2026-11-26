"""Skill replacement avoids cross-folder directory identity changes."""
from pathlib import Path
from unittest.mock import patch
import hashlib,unittest
from tests import test_project_skill_update_offline as original


class SkillInstallationTransferTests(unittest.TestCase):
    def setUp(self):
        self.fixture=original.ProjectSkillUpdateTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.installer=self.fixture.installer

    def test_old_installation_is_transferred_as_verified_files(self):
        target=self.fixture.previous();old=self.installer.verify_installation(target)
        replace=self.installer.os.replace;moves=[]
        def observed(source,destination):
            source,destination=Path(source),Path(destination)
            self.assertFalse(source.is_dir() and destination.parent==self.fixture.recovery)
            moves.append((str(source),str(destination)))
            return replace(source,destination)
        with patch.object(self.installer.os,'replace',side_effect=observed):record=self.fixture.install()
        self.assertEqual(self.installer.verify_installation(Path(record['recovery'])),old)
        self.assertTrue(any(source.endswith('/SKILL.md') for source,_ in moves))
        self.assertEqual(self.installer.verify_installation(target),record['manifest_sha256'])

    def test_partial_file_move_failure_restores_old_bytes(self):
        target=self.fixture.previous();old=self.installer.verify_installation(target)
        replace=self.installer.os.replace
        def fail(source,destination):
            if Path(source)==target/'manifest.json':raise OSError('Interrupted transfer')
            return replace(source,destination)
        with patch.object(self.installer.os,'replace',side_effect=fail),self.assertRaises(OSError):self.fixture.install()
        self.assertEqual(self.installer.verify_installation(target),old)
        self.assertEqual((target/'SKILL.md').read_text(),'old instructions')
        self.assertFalse(list(self.fixture.recovery.glob('*/manifest.json')))

    def test_new_concurrent_user_file_survives_failed_retirement(self):
        target=self.fixture.previous();replace=self.installer.os.replace
        def changed(source,destination):
            result=replace(source,destination)
            if Path(source)==target/'manifest.json':(target/'user.txt').write_text('用户资料')
            return result
        with patch.object(self.installer.os,'replace',side_effect=changed),self.assertRaises(OSError):self.fixture.install()
        self.assertEqual((target/'user.txt').read_text(),'用户资料')
        self.assertEqual((target/'SKILL.md').read_text(),'old instructions')

    def test_promotion_failure_uses_verified_file_transfer_for_rollback(self):
        target=self.fixture.previous();old=self.installer.verify_installation(target)
        replace=self.installer.os.replace
        def fail(source,destination):
            source,destination=Path(source),Path(destination)
            self.assertFalse(source.is_dir() and destination.parent==self.fixture.recovery)
            if destination==target and source.name.startswith('.researchhelper-install-'):raise OSError('Promotion interrupted')
            return replace(source,destination)
        with patch.object(self.installer.os,'replace',side_effect=fail),self.assertRaises(OSError):self.fixture.install()
        self.assertEqual(self.installer.verify_installation(target),old)

    def test_default_recovery_stays_in_project_and_does_not_use_trash(self):
        self.fixture.previous()
        with patch.object(self.installer.Path,'home',return_value=self.fixture.directory/'unused-home'):
            result=self.installer.install_project_skill(self.fixture.archive,project_root=self.fixture.project,
                expected_sha256=hashlib.sha256(self.fixture.archive.read_bytes()).hexdigest())
        self.assertTrue(Path(result['recovery']).is_relative_to(self.fixture.project/'result/skill-installation-recovery'))


if __name__=='__main__':unittest.main()
