"""New delivery contract tests; original frozen-release tests are not overwritten."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'packaging'))
import build_current as delivery
from source_snapshot import digest


class CurrentDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.dist = self.root / 'dist'
        self.dist.mkdir()
        self.old = self.dist / 'ResearchHelper-0.10.0-arm64.dmg'
        self.old.write_bytes(b'previous image')

    def make_source_map(self):
        directory = self.root / 'packaging'
        directory.mkdir()
        (self.root / 'VERSION').write_text('0.10.0')
        (directory / 'package-source-map.json').write_text(json.dumps({'files':['VERSION']}))
        return {'VERSION': digest(self.root / 'VERSION')}

    def builder(self, *, root, workspace):
        inventory = self.make_source_map()
        payload = workspace / 'source/result/build-staging/current'
        payload.mkdir(parents=True)
        artifacts = {}
        for key, name in [('dmg', 'ResearchHelper-0.10.0-arm64.dmg'), ('skill', 'ResearchHelper-Skill-0.10.0.zip')]:
            file = payload / name; file.write_bytes(('new ' + key).encode())
            artifacts[key] = {'path': str(file), 'sha256': digest(file), 'bytes': file.stat().st_size}
        demo = payload / 'demo'; demo.mkdir()
        (demo / 'manifest.json').write_text('{}'); (demo / 'index.html').write_text('offline')
        components = {}
        for key in ['app','skill','demo']:
            file = payload / (key + '-manifest.json'); file.write_text('{}')
            components[key] = {'path': str(file), 'sha256': digest(file)}
        release = {'artifacts': artifacts, 'component_manifests': components, 'platform': 'macos', 'source_hashes': inventory}
        (payload / 'release-manifest.json').write_text(json.dumps(release))
        logs = self.root / 'result/build-records/fixture'; logs.mkdir(parents=True)
        (logs / 'build.log').write_text('fixture')
        (logs / 'candidate.json').write_text('{}')
        return {'payload':str(payload), 'snapshot':str(workspace/'source'), 'platform':'macos', 'source_hashes': inventory,
                'log':str(logs/'build.log'), 'published':False,'installed':False,'demos_updated':False}

    def test_archive_is_content_addressed_and_repeated_copy_is_deduplicated(self):
        first = delivery.archive_file(self.old, self.root)
        second = delivery.archive_file(self.old, self.root)
        self.assertEqual(first, second)
        self.assertEqual(first.parent.name, digest(self.old))
        self.assertEqual(first.read_bytes(), self.old.read_bytes())

    def test_corrupt_existing_archive_is_never_overwritten(self):
        archived = delivery.archive_file(self.old, self.root)
        archived.write_bytes(b'corrupt')
        with self.assertRaisesRegex(RuntimeError, '不一致'):
            delivery.archive_file(self.old, self.root)
        self.assertEqual(archived.read_bytes(), b'corrupt')

    def test_formal_index_is_retained_by_hash_not_carried_into_new_dist(self):
        index = self.dist / 'current-release.json'; index.write_text('{"formal":true}')
        archived = delivery.archive_deliveries(self.dist, self.root)
        copy = next(file for file in archived if file.name == index.name)
        self.assertEqual(copy.read_bytes(), index.read_bytes())

    def test_unknown_dist_item_refuses_replacement(self):
        (self.dist / 'my-notes.md').write_text('protected')
        with self.assertRaisesRegex(RuntimeError, '未知项目'):
            delivery.archive_deliveries(self.dist, self.root)
        self.assertFalse((self.root / 'versions').exists())
        self.assertEqual(self.old.read_bytes(), b'previous image')

    def test_symlink_delivery_and_archive_cannot_escape_the_project(self):
        linked = self.root / 'linked'; linked.symlink_to(self.dist, target_is_directory=True)
        with self.assertRaises(RuntimeError): delivery.archive_deliveries(linked, self.root)
        (self.root / 'versions').symlink_to(self.dist, target_is_directory=True)
        with self.assertRaises(RuntimeError): delivery.archive_file(self.old, self.root)

    def test_directory_swap_failure_restores_complete_old_delivery(self):
        staged = self.root / 'new'; staged.mkdir(); (staged / self.old.name).write_bytes(b'new')
        rename = Path.rename
        def failing(path, target):
            if path.name == 'delivery': raise OSError('synthetic rename failure')
            return rename(path, target)
        with patch.object(Path, 'rename', failing):
            with self.assertRaises(OSError): delivery.replace_delivery(staged, self.dist)
        self.assertEqual(self.old.read_bytes(), b'previous image')
        self.assertFalse(list(self.root.glob('.dist-*')))

    def test_copy_failure_never_exposes_partial_dist(self):
        staged = self.root / 'new'; staged.mkdir(); (staged / self.old.name).write_bytes(b'new')
        with patch.object(delivery.shutil, 'copytree', side_effect=OSError('copy failed')):
            with self.assertRaises(OSError): delivery.replace_delivery(staged, self.dist)
        self.assertEqual(self.old.read_bytes(), b'previous image')

    def test_backup_cleanup_failure_keeps_verified_new_delivery(self):
        staged = self.root / 'new'; staged.mkdir(); (staged / self.old.name).write_bytes(b'new')
        remove = Path.unlink
        def failing(path, *args, **kwargs):
            if '-backup-' in str(path): raise OSError('busy backup')
            return remove(path, *args, **kwargs)
        with patch.object(Path, 'unlink', failing): delivery.replace_delivery(staged, self.dist)
        self.assertEqual(self.old.read_bytes(), b'new')
        self.assertEqual(len(list(self.root.glob('.dist-backup-*'))), 1)

    def test_late_unknown_file_in_previous_directory_survives_the_swap(self):
        staged = self.root / 'new'
        staged.mkdir()
        (staged / self.old.name).write_bytes(b'new')
        rename = Path.rename

        def external_write(path, target):
            moved = rename(path, target)
            if path == self.dist:
                (moved / 'user-note.txt').write_bytes(b'unique notes')
            return moved

        with patch.object(Path, 'rename', external_write), self.assertRaisesRegex(RuntimeError, '未知项目'):
            delivery.replace_delivery(staged, self.dist)
        self.assertEqual(self.old.read_bytes(), b'previous image')
        self.assertEqual((self.dist / 'user-note.txt').read_bytes(), b'unique notes')
        self.assertFalse(list(self.root.glob('.dist-backup-*')))

    def test_late_unknown_file_in_retired_directory_stays_recoverable(self):
        staged = self.root / 'new'
        staged.mkdir()
        (staged / self.old.name).write_bytes(b'new')
        rename = Path.rename

        def external_write(path, target):
            moved = rename(path, target)
            if path.name == 'delivery':
                (next(self.root.glob('.dist-backup-*')) / 'user-note.txt').write_bytes(b'unique notes')
            return moved

        with patch.object(Path, 'rename', external_write):
            delivery.replace_delivery(staged, self.dist)
        self.assertEqual(self.old.read_bytes(), b'new')
        backup, = self.root.glob('.dist-backup-*')
        self.assertEqual((backup / 'user-note.txt').read_bytes(), b'unique notes')
        self.assertEqual((backup / self.old.name).read_bytes(), b'previous image')

    def test_read_only_gate_accepts_a_recorded_late_copy_without_counting_it_as_current(self):
        from reconcile_delivery import download_inventory

        with patch.object(delivery,'verify_release_binding'):
            result = delivery.build_current(self.root,builder=self.builder,install_skill=False)
            record = Path(result['payload'])/'candidate.json'
            alias = self.dist/'ResearchHelper-0.10.0-arm64 2.dmg'
            alias.write_bytes(self.old.read_bytes())
            self.assertEqual(delivery.verify_current_build(record,self.root),result)
            self.assertEqual(download_inventory(self.root,self.dist)['copies'][0]['authority'],str(record))
            self.assertTrue(alias.is_file())
            self.old.unlink()
            with self.assertRaisesRegex(RuntimeError,'下载目录不属于本候选'):
                delivery.verify_current_build(record,self.root)
            self.assertTrue(alias.is_file())

    def test_real_kernel_lock_refuses_another_process_and_releases(self):
        program = "import sys;sys.path.insert(0,"+repr(str(ROOT/'packaging'))+");from build_current import build_lock;from pathlib import Path\nwith build_lock(Path("+repr(str(self.root))+")): pass"
        with delivery.build_lock(self.root):
            result = subprocess.run([sys.executable,'-B','-c',program],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0); self.assertIn('正在运行',result.stderr)
        result = subprocess.run([sys.executable,'-B','-c',program],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_build_failure_and_cancel_clean_only_owned_scratch(self):
        for error in [RuntimeError('build failed'), KeyboardInterrupt()]:
            with self.subTest(error=type(error).__name__):
                def failing(**kwargs):
                    (kwargs['workspace']/'partial').write_text('compiler cache')
                    raise error
                with self.assertRaises(type(error)): delivery.build_current(self.root,builder=failing)
                self.assertEqual(list((self.root/'result/build-work').iterdir()),[])
                self.assertEqual(self.old.read_bytes(),b'previous image')

    def test_changed_source_rejects_old_snapshot_before_archiving(self):
        expected=self.make_source_map();(self.root/'VERSION').write_text('0.10.1')
        with self.assertRaises(RuntimeError): delivery.current_sources_match(self.root,expected)
        self.assertFalse((self.root/'versions').exists())

    def test_journal_save_failure_preserves_previous_complete_json(self):
        record=self.root/'receipt.json';delivery.write_record(record,{'status':'old'})
        with patch.object(delivery.os,'replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):delivery.write_record(record,{'status':'new'})
        self.assertEqual(json.loads(record.read_text()),{'status':'old'})
        self.assertFalse(list(self.root.glob('.receipt.json-*')))

    def test_success_leaves_two_downloads_and_small_retained_candidate(self):
        with patch.object(delivery,'verify_release_binding'):
            result=delivery.build_current(self.root,builder=self.builder)
        self.assertEqual(set(file.name for file in self.dist.iterdir()),{
            'ResearchHelper-0.10.0-arm64.dmg','ResearchHelper-Skill-0.10.0.zip'})
        self.assertTrue(result['temporary_workspace_removed']);self.assertTrue(result['dist_updated'])
        self.assertFalse(result['published']);self.assertFalse(result['installed']);self.assertFalse(result['demos_updated'])
        self.assertEqual(Path(result['previous_artifacts'][0]).read_bytes(),b'previous image')
        output=Path(result['payload'])
        self.assertTrue((output/'demo/index.html').is_file())
        self.assertFalse((output/'ResearchHelper.app').exists());self.assertFalse((output/'source').exists())
        self.assertTrue(all(Path(item['path']).is_file() for item in result['artifacts'].values()))
        self.assertEqual(list((self.root/'result/build-work').iterdir()),[])

    def test_windows_delivery_never_replaces_mac_dist(self):
        # Exercise platform routing without pretending to compile Windows on macOS.
        def windows_builder(**kwargs):
            built=self.builder(**kwargs);built['platform']='windows'
            payload=Path(built['payload']);release=json.loads((payload/'release-manifest.json').read_text())
            old=Path(release['artifacts'].pop('dmg')['path']);new=old.with_name('ResearchHelper-0.10.0-windows-x64-setup.exe');old.rename(new)
            release['artifacts']['setup']={'path':str(new),'sha256':digest(new),'bytes':new.stat().st_size}
            (payload/'release-manifest.json').write_text(json.dumps(release));return built
        with patch.object(delivery,'verify_release_binding'):
            result=delivery.build_current(self.root,builder=windows_builder)
        self.assertEqual(self.old.read_bytes(),b'previous image')
        self.assertEqual(Path(result['delivery']),self.root/'result/windows-candidate')
        self.assertTrue((Path(result['delivery'])/'ResearchHelper-0.10.0-windows-x64-setup.exe').is_file())

    def test_retained_verifier_requires_archive_download_and_source_to_match(self):
        with patch.object(delivery,'verify_release_binding'):
            result=delivery.build_current(self.root,builder=self.builder)
            record=Path(result['payload'])/'candidate.json'
            self.assertEqual(delivery.verify_current_build(record,self.root)['status'],'built_local_candidate')
            self.old.write_bytes(b'changed download')
            with self.assertRaisesRegex(RuntimeError,'下载与保留候选不一致'):
                delivery.verify_current_build(record,self.root)
            # Ignoring current downloads can inspect immutable archives, but never
            # bypasses source/manifest binding or approves a new installation.
            self.assertEqual(delivery.verify_current_build(record,self.root,check_downloads=False)['status'],'built_local_candidate')
            (self.root/'VERSION').write_text('0.10.1')
            with self.assertRaisesRegex(RuntimeError,'源码'):
                delivery.verify_current_build(record,self.root,check_downloads=False)

    def test_retained_verifier_rejects_mutated_archive(self):
        with patch.object(delivery,'verify_release_binding'):
            result=delivery.build_current(self.root,builder=self.builder)
            Path(result['artifacts']['skill']['path']).write_bytes(b'changed archive')
            with self.assertRaisesRegex(RuntimeError,'归档已变化'):
                delivery.verify_current_build(Path(result['payload'])/'candidate.json',self.root)


if __name__ == '__main__':
    unittest.main()
