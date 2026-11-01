"""Known generated copies never require moving unknown user files."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from tests import ROOT
import sys
sys.path.insert(0,str(ROOT/'packaging'))
import build_current
from reconcile_delivery import reconcile_downloads,download_inventory,digest
from tests import test_project_skill_update_offline as skill_fixture


class DownloadReconciliationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.directory=Path(temporary.name).resolve();self.root=self.directory/'project';self.root.mkdir()
        self.dist=self.root/'dist';self.dist.mkdir();self.recovery=self.directory/'recovery'
        self.canonical=self.dist/'ResearchHelper-Skill-0.10.0.zip';self.canonical.write_bytes(b'current generated fixture')
        self.copy=self.dist/'ResearchHelper-Skill-0.10.0 3.zip';self.copy.write_bytes(b'previous generated fixture')
        folder=self.root/'result/build-candidates/old';folder.mkdir(parents=True)
        artifact=dict(path=str(self.root/'versions/skill/v0.10.0'/digest(self.copy)/self.canonical.name),
            sha256=digest(self.copy),bytes=self.copy.stat().st_size)
        release=folder/'release-manifest.json';release.write_text(json.dumps(dict(artifacts=dict(skill=artifact))))
        self.candidate=folder/'candidate.json';self.candidate.write_text(json.dumps(dict(
            schema='researchhelper.current-build.v1',manifest_sha256=digest(release),artifacts=dict(skill=artifact))))

    def reconcile(self):return reconcile_downloads(self.root,self.dist,recovery=self.recovery)

    def test_exact_recorded_old_archive_moves_to_recovery_without_requiring_its_retired_original(self):
        before=self.canonical.read_bytes();result=self.reconcile()
        self.assertEqual(result['copies'],1);self.assertFalse(self.copy.exists())
        self.assertEqual(self.canonical.read_bytes(),before)
        record=json.loads(Path(result['receipt']).read_text())
        self.assertEqual((Path(record['recovery'])/self.copy.name).read_bytes(),b'previous generated fixture')

    def test_an_unknown_file_or_changed_copy_prevents_all_moves(self):
        for mutation in ('unknown','changed','receipt'):
            unknown=self.dist/'my-notes.txt';old=self.copy.read_bytes();release=self.candidate.parent/'release-manifest.json';raw=release.read_bytes()
            if mutation=='unknown':unknown.write_text('user data')
            elif mutation=='changed':self.copy.write_bytes(b'user edit')
            else:release.write_text('{}')
            with self.subTest(mutation=mutation),self.assertRaises(RuntimeError):self.reconcile()
            self.assertTrue(self.copy.exists());self.assertFalse(self.recovery.exists())
            unknown.unlink(missing_ok=True);self.copy.write_bytes(old);release.write_bytes(raw)

    def test_linked_or_other_platform_copies_are_preserved(self):
        linked=self.directory/'linked';linked.symlink_to(self.dist,target_is_directory=True)
        with self.assertRaises(RuntimeError):reconcile_downloads(self.root,linked,recovery=self.recovery)
        windows=self.dist/'ResearchHelper-0.10.0-windows-x64-setup 2.exe';windows.write_text('other platform')
        with self.assertRaises(RuntimeError):self.reconcile()
        self.assertTrue(self.copy.exists());self.assertTrue(windows.exists())

    def test_one_click_preflight_reconciles_copies_before_the_builder(self):
        builder=Mock(side_effect=RuntimeError('builder reached'))
        with patch('reconcile_delivery._recovery_location',return_value=self.recovery):
            with self.assertRaisesRegex(RuntimeError,'builder reached'):
                build_current.build_current(self.root,builder=builder,install_skill=False)
        builder.assert_called_once();self.assertFalse(self.copy.exists())

    def test_unrecognized_copy_does_not_start_a_build(self):
        self.copy.write_text('unknown bytes');builder=Mock()
        with self.assertRaises(RuntimeError):build_current.build_current(self.root,builder=builder,install_skill=False)
        builder.assert_not_called();self.assertTrue(self.copy.exists())


class ProjectSkillReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=skill_fixture.ProjectSkillUpdateTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.target=self.fixture.previous()
        (self.target/'SKILL 2.md').write_bytes((self.target/'SKILL.md').read_bytes())
        (self.target/'manifest 2.json').write_bytes((self.target/'manifest.json').read_bytes())
        (self.target/'agents 2').mkdir();(self.target/'user-empty-notes').mkdir()
        self.home=self.fixture.directory/'home';(self.home/'.Trash').mkdir(parents=True)

    def update(self):
        candidate=dict(artifacts=dict(skill=dict(path=str(self.fixture.archive),sha256=digest(self.fixture.archive))))
        with patch.object(build_current,'verify_current_build',return_value=candidate),patch.object(Path,'home',return_value=self.home):
            return build_current.update_project_skill(self.fixture.directory/'candidate.json',self.fixture.project)

    def test_update_uses_the_real_installer_after_reconciling_known_old_files(self):
        result=self.update()
        self.assertEqual(result['status'],'installed')
        self.assertNotEqual((self.target/'SKILL.md').read_text(),'old instructions')
        self.assertFalse((self.target/'SKILL 2.md').exists());self.assertFalse((self.target/'agents 2').exists())
        self.assertTrue((self.target/'user-empty-notes').is_dir())
        self.assertEqual(result['generated_copy_reconciliation']['copies'],2)
        self.assertFalse(result['research_data_changed'])

    def test_edited_copy_stops_installation_and_is_never_retired(self):
        (self.target/'SKILL 2.md').write_text('user edit')
        with self.assertRaises(RuntimeError):self.update()
        self.assertEqual((self.target/'SKILL 2.md').read_text(),'user edit')
        self.assertEqual((self.target/'SKILL.md').read_text(),'old instructions')
        self.assertEqual(list((self.home/'.Trash').iterdir()),[])


if __name__=='__main__':unittest.main()
