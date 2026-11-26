"""Known generated copies never require moving unknown user files."""
import json
from pathlib import Path
import subprocess
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

    def test_a_recorded_numbered_file_cannot_replace_the_missing_download_or_be_cleaned(self):
        self.canonical.unlink()
        plan = download_inventory(self.root,self.dist)
        self.assertEqual(plan['files'],[])
        self.assertEqual(len(plan['copies']),1)
        with self.assertRaisesRegex(RuntimeError,'缺少对应正式文件'):
            self.reconcile()
        self.assertEqual(self.copy.read_bytes(),b'previous generated fixture')
        self.assertFalse(self.recovery.exists())

    def test_renamed_prior_version_is_retired_once_the_exact_successor_is_accepted(self):
        from tests.test_permanent_release_cleanup import recorded_release

        self.canonical.unlink()
        successor = self.dist/'ResearchHelper-Skill-0.10.1.zip'
        successor.write_bytes(b'new generated archive')
        artifact = dict(path=str(successor), sha256=digest(successor), bytes=successor.stat().st_size)
        candidate = recorded_release(self.root,'successor',artifacts=dict(skill=artifact))
        with self.assertRaisesRegex(RuntimeError, '保留可恢复副本'):
            self.reconcile()
        proof = self.root/'result/acceptance/successor/receipt.json'
        proof.parent.mkdir(parents=True)
        record = json.loads(candidate.read_text())
        proof.write_text(json.dumps(dict(status='scoped_passed', candidate=str(candidate),
            manifest_sha256=record['manifest_sha256'], dataset_parity=True, service_stopped=True,
            mount_detached=True, temporary_research_removed=True)))
        record['postbuild_acceptance'] = dict(status='scoped_passed', path=str(proof), sha256=digest(proof))
        candidate.write_text(json.dumps(record))
        self.assertEqual(self.reconcile()['copies'], 1)
        self.assertEqual(successor.read_bytes(), b'new generated archive')
        self.assertFalse(self.copy.exists())

    def test_missing_same_name_is_safe_when_an_exact_archived_copy_exists(self):
        self.canonical.unlink()
        previous = json.loads(self.candidate.read_text())['artifacts']['skill']
        archive = Path(previous['path'])
        archive.parent.mkdir(parents=True)
        archive.write_bytes(self.copy.read_bytes())
        self.assertEqual(self.reconcile()['copies'], 1)
        self.assertEqual(archive.read_bytes(), b'previous generated fixture')

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS process lookup')
    def test_an_active_numbered_file_is_preserved(self):
        with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)', str(self.copy)]) as process:
            try:
                with self.assertRaisesRegex(RuntimeError, 'live process'):
                    self.reconcile()
                self.assertTrue(self.copy.is_file())
            finally:
                process.terminate()

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

    def test_known_copies_arriving_after_installation_are_reconciled(self):
        from skill import install_project_skill as installer

        install = installer.install_project_skill
        def with_late_copy(*args, **kwargs):
            result = install(*args, **kwargs)
            (self.target/'agents 2').mkdir()
            (self.target/'SKILL 2.md').write_bytes((self.target/'SKILL.md').read_bytes())
            return result

        with patch.object(installer,'install_project_skill',side_effect=with_late_copy):
            result = self.update()
        after = result['post_install_generated_copy_reconciliation']
        self.assertEqual((after['copies'],after['directories']),(1,1))
        self.assertFalse((self.target/'agents 2').exists())
        self.assertFalse((self.target/'SKILL 2.md').exists())
        self.assertTrue((self.target/'user-empty-notes').is_dir())


if __name__=='__main__':unittest.main()
