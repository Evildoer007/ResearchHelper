"""Independent sync safety checks; mocked package receipts are not App acceptance."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('current_demo_sync', ROOT / 'packaging/demo/sync_demo.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
from artifact_integrity import directory_record


class CurrentDemoSyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'ResearchHelper'
        self.root.mkdir()
        (self.root / 'VERSION').write_text('0.1.0\n')
        self.desktop = self.base / 'desktop-demo'
        self.desktop.mkdir()
        self.trash = self.base / 'Trash'
        self.trash.mkdir()
        self.candidate_path = self.root / 'result/build-candidates/fixture/candidate.json'
        self.candidate_path.parent.mkdir(parents=True)
        self.candidate_path.write_text('{}')
        self.source = self.candidate_path.parent / 'demo'
        self.source.mkdir()
        (self.source / 'public').mkdir()
        (self.source / 'index.html').write_text('current demo entry')
        (self.source / 'public/workspace.js').write_text('current workspace')
        self.bind_source()
        self.package_path = self.root / 'result/package.json'
        self.browser_path = self.root / 'result/browser.json'
        shared = {'status': 'scoped_passed', 'candidate': str(self.candidate_path),
                  'native_window_opened': False, 'production_store_touched': False, 'installed': False,
                  'model_calls': 0, 'real_data_requests': 0, 'mount_detached': True}
        self.package = {**shared, 'public_reports': 6}
        for flag in ('headless', 'backend_stopped', 'skill_zip_inventory_verified', 'demo_private_files_absent',
                     'app_complete_inventory_verified', 'vendor_licenses_verified', 'app_private_files_absent',
                     'signature_verified', 'readonly_image_verified', 'development_and_shared_products_denied', 'anaconda_denied'):
            self.package[flag] = True
        self.browser = {**shared, 'backend_mode': 'readonly_dmg', 'system_dialog_presented': False,
                        'bundled_backend_isolated': True, 'signature_verified': True, 'service_stopped': True,
                        'browser': {'status': 'passed', 'errors': [], 'externalRequests': 0, 'headless': True,
                                    'browserClosed': True, 'systemClipboardTouched': False, 'systemPrintOpened': False,
                                    'checks': ['synthetic fixture'], 'screens': ['synthetic.png'],
                                    'syntheticCases': [{'fixture': name, 'id': name, 'version': 1,
                                                       'ledger': {'used': {'model_calls': 0, 'data_requests': 0}}}
                                                      for name in ('mechanism', 'etf')]}}
        self.save_receipts()
        self.targets = (self.root / 'demo', self.desktop)
        for target in self.targets:
            target.mkdir(exist_ok=True)
            (target / 'index.html').write_text('previous retained entry')
            (target / 'public').mkdir()
            (target / 'public/workspace.js').write_text('previous workspace')
            (target / '.local').mkdir()
            (target / '.local/private.sqlite').write_bytes(b'private original')
            (target / 'wrangler.jsonc').write_text('preserved deployment config')
        self.verifier = patch.object(sync, 'verify_current_build', return_value=self.candidate).start()
        self.addCleanup(patch.stopall)
        patch.object(sync, 'DESTINATION', self.desktop).start()

    def bind_source(self, version='0.1.0'):
        manifest = {'source': 'ResearchHelper', 'version': version,
                    'runtime': {'version': version, 'protocol_id': 'researchhelper.module-request'}, 'files': {
                        file.relative_to(self.source).as_posix(): sync.digest(file)
                        for file in self.source.rglob('*') if file.is_file() and file.name != 'manifest.json'}}
        (self.source / 'manifest.json').write_text(json.dumps(manifest))
        self.candidate = {'payload': str(self.candidate_path.parent),
                          'directory_artifacts': {'demo': directory_record(self.source)}}
        if hasattr(self, 'verifier'):
            self.verifier.return_value = self.candidate

    def save_receipts(self):
        self.package_path.write_text(json.dumps(self.package))
        self.browser_path.write_text(json.dumps(self.browser))

    def run_sync(self):
        return sync.sync_current(self.candidate_path, self.package_path, self.browser_path,
                                 root=self.root, recovery=self.trash)

    def contents(self):
        return [{file.relative_to(target).as_posix(): file.read_bytes() for file in target.rglob('*') if file.is_file()}
                for target in self.targets]

    def assert_rejected_unchanged(self):
        before = self.contents()
        with self.assertRaises((ValueError, RuntimeError)):
            self.run_sync()
        self.assertEqual(self.contents(), before)

    def test_both_targets_updated_and_private_files_preserved(self):
        before = [sync.untouched_state(target, {'index.html', 'public/workspace.js'}) for target in self.targets]
        result = self.run_sync()
        self.assertEqual(result['status'], 'synced_offline_candidate')
        self.assertEqual(result['version'], '0.1.0')
        self.assertFalse(result['native_ui_verified'])
        self.assertFalse(result['published'])
        self.assertEqual(result['files_verified'], 8)
        for index, target in enumerate(self.targets):
            self.assertEqual((target / 'index.html').read_text(), 'current demo entry')
            self.assertEqual((target / 'manifest.json').read_bytes(), (target / 'researchhelper-release.json').read_bytes())
            self.assertEqual(sync.untouched_state(target, {'index.html', 'public/workspace.js', 'manifest.json', 'researchhelper-release.json'}), before[index])
        recovery = Path(result['recovery_directory'])
        self.assertFalse(list(recovery.rglob('*.sqlite')))
        self.assertEqual((recovery / '0/index.html').read_text(), 'previous retained entry')

    def test_new_source_version_controls_demo_validation_and_sync_receipt(self):
        (self.root / 'VERSION').write_text('0.2.0\n')
        self.assert_rejected_unchanged()
        self.bind_source('0.2.0')
        self.assertEqual(self.run_sync()['version'], '0.2.0')

    def test_repeat_is_idempotent_without_new_backup(self):
        self.run_sync()
        backups = list(self.trash.iterdir())
        second = self.run_sync()
        self.assertEqual(second['changed_files'], 0)
        self.assertIsNone(second['recovery_directory'])
        self.assertEqual(list(self.trash.iterdir()), backups)

    def test_actual_candidate_verifier_is_not_bypassed(self):
        self.verifier.side_effect = RuntimeError('stale source')
        self.assert_rejected_unchanged()

    def test_stale_failed_or_wrong_scope_receipts_rejected(self):
        for key, value in (('candidate', '/previous/candidate.json'), ('status', 'failed'),
                           ('model_calls', 1), ('real_data_requests', 1), ('installed', True),
                           ('mount_detached', False), ('production_store_touched', True)):
            original = self.package[key]
            with self.subTest(key=key):
                self.package[key] = value
                self.save_receipts()
                self.assert_rejected_unchanged()
            self.package[key] = original

    def test_missing_package_checks_rejected(self):
        self.package['app_complete_inventory_verified'] = False
        self.save_receipts()
        self.assert_rejected_unchanged()

    def test_unclosed_or_failed_browser_rejected(self):
        for key, value in (('errors', ['failed']), ('externalRequests', 1), ('externalRequests', []),
                           ('externalRequests', False),
                           ('browserClosed', False), ('systemPrintOpened', True)):
            original = self.browser['browser'][key]
            with self.subTest(key=key):
                self.browser['browser'][key] = value
                self.save_receipts()
                self.assert_rejected_unchanged()
            self.browser['browser'][key] = original

    def test_missing_fixed_cases_rejected(self):
        self.browser['browser']['syntheticCases'].pop()
        self.save_receipts()
        self.assert_rejected_unchanged()

    def test_private_source_cannot_be_published(self):
        (self.source / 'public/source-settings.json').write_text('must not be published')
        self.bind_source()
        self.assert_rejected_unchanged()

    def test_unknown_source_top_level_rejected(self):
        (self.source / 'extra.py').write_text('not in the release contract')
        self.bind_source()
        self.assert_rejected_unchanged()

    def test_bare_path_validation_blocks_traversal_and_private_fields(self):
        for name in ('../index.html', '/index.html', 'public//index.html', 'public\\index.html',
                     'public/.env.production', 'app/data/task.json', 'public/data/task.json', 'public/tasks.db'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                sync.release_name(name)
        self.assertEqual(sync.release_name('app/data/reports/semi.html'), 'app/data/reports/semi.html')

    def test_changed_source_rejected_before_target_mutation(self):
        (self.source / 'index.html').write_text('changed after acceptance')
        self.assert_rejected_unchanged()

    def test_internal_destination_symlink_rejected(self):
        target = self.desktop / 'public/workspace.js'
        target.unlink()
        target.symlink_to(self.desktop / 'index.html')
        self.assert_rejected_unchanged()

    def test_file_directory_conflict_rejected(self):
        target = self.desktop / 'public/workspace.js'
        target.unlink()
        target.mkdir()
        self.assert_rejected_unchanged()

    def test_error_rolls_back_both_targets(self):
        before = self.contents()
        real_commit = sync.commit_file
        calls = []
        def fail(staged, target):
            calls.append(target)
            if len(calls) == 3:
                raise OSError('simulated save failure')
            real_commit(staged, target)
        with patch.object(sync, 'commit_file', side_effect=fail), self.assertRaises(OSError):
            self.run_sync()
        self.assertEqual(self.contents(), before)
        receipt = next((self.root / 'result/maintenance/demo-sync').glob('*/receipt.json'))
        record = json.loads(receipt.read_text())
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['rollback_conflicts'], [])

    def test_concurrent_edit_to_next_target_not_overwritten(self):
        real_commit = sync.commit_file
        changed = self.desktop / 'public/workspace.js'
        calls = []
        def concurrent(staged, target):
            real_commit(staged, target)
            calls.append(target)
            if len(calls) == 1:
                changed.write_text('concurrent user edit')
        with patch.object(sync, 'commit_file', side_effect=concurrent), self.assertRaises(RuntimeError):
            self.run_sync()
        self.assertEqual(changed.read_text(), 'concurrent user edit')
        self.assertEqual((self.targets[0] / 'public/workspace.js').read_text(), 'previous workspace')

    def test_concurrent_edit_to_committed_file_preserved_during_rollback(self):
        real_commit = sync.commit_file
        changed = self.targets[0] / 'public/workspace.js'
        calls = []
        def concurrent(staged, target):
            calls.append(target)
            if len(calls) == 2:
                changed.write_text('concurrent user edit')
                raise OSError('simulated failure after user edit')
            real_commit(staged, target)
        with patch.object(sync, 'commit_file', side_effect=concurrent), self.assertRaises(OSError):
            self.run_sync()
        self.assertEqual(changed.read_text(), 'concurrent user edit')
        receipt = next((self.root / 'result/maintenance/demo-sync').glob('*/receipt.json'))
        self.assertEqual(json.loads(receipt.read_text())['rollback_conflicts'], [str(changed)])

    def test_changed_receipt_during_staging_blocks_commit(self):
        original = sync.shutil.copyfile
        def change_receipt(source, target, **kwargs):
            result = original(source, target, **kwargs)
            self.package['backend_stopped'] = False
            self.save_receipts()
            return result
        with patch.object(sync.shutil, 'copyfile', side_effect=change_receipt):
            self.assert_rejected_unchanged()

    def permanent_policy(self):
        folder=self.root/'packaging'
        folder.mkdir(exist_ok=True)
        (folder/'release-retention.json').write_text(json.dumps(dict(
            schema='researchhelper.release-retention.v2',macos='current_candidate_only',recovery='permanent')))

    def test_permanent_policy_defaults_to_the_owned_recovery_namespace(self):
        self.permanent_policy()
        original=self.contents()
        result=sync.sync_current(self.candidate_path,self.package_path,self.browser_path,root=self.root)
        backup=Path(result['recovery_directory'])
        self.assertEqual(backup.parent,self.root/'result/demo-sync-recovery')
        self.assertEqual((backup/'0/index.html').read_bytes(),original[0]['index.html'])
        self.assertEqual(list(self.trash.iterdir()),[])
        for target in self.targets:
            self.assertEqual((target/'.local/private.sqlite').read_bytes(),b'private original')
        self.assertEqual(result['status'],'synced_offline_candidate')

    def test_explicit_owned_namespace_is_created_and_remains_idempotent(self):
        recovery=self.root/'result/demo-sync-recovery'
        first=sync.sync_current(self.candidate_path,self.package_path,self.browser_path,root=self.root,recovery=recovery)
        backups=list(recovery.iterdir())
        second=sync.sync_current(self.candidate_path,self.package_path,self.browser_path,root=self.root,recovery=recovery)
        self.assertEqual(Path(first['recovery_directory']).parent,recovery)
        self.assertEqual(second['changed_files'],0)
        self.assertIsNone(second['recovery_directory'])
        self.assertEqual(list(recovery.iterdir()),backups)

    def test_other_in_repository_recovery_paths_cannot_mutate_targets(self):
        original=self.contents()
        for recovery in (self.root,self.root/'demo',self.root/'result/reports',
                         self.root/'result/demo-sync-recovery/nested'):
            with self.subTest(recovery=recovery),self.assertRaises(ValueError):
                sync.sync_current(self.candidate_path,self.package_path,self.browser_path,
                                  root=self.root,recovery=recovery)
            self.assertEqual(self.contents(),original)

    def test_linked_owned_namespace_is_rejected_before_target_mutation(self):
        self.permanent_policy()
        recovery=self.root/'result/demo-sync-recovery'
        recovery.symlink_to(self.trash,target_is_directory=True)
        original=self.contents()
        with self.assertRaises(ValueError):
            sync.sync_current(self.candidate_path,self.package_path,self.browser_path,root=self.root)
        self.assertEqual(self.contents(),original)
        self.assertEqual(list(self.trash.iterdir()),[])

    def test_project_recovery_retains_complete_rollback_after_a_copy_failure(self):
        self.permanent_policy()
        original=self.contents();commit=sync.commit_file;calls=[]
        def fail_second(staged,target):
            calls.append(target)
            if len(calls)==2:raise OSError('Controlled project recovery copy failure')
            commit(staged,target)
        with patch.object(sync,'commit_file',side_effect=fail_second),self.assertRaises(OSError):
            sync.sync_current(self.candidate_path,self.package_path,self.browser_path,root=self.root)
        self.assertEqual(self.contents(),original)
        backup=next((self.root/'result/demo-sync-recovery').iterdir())
        receipt=json.loads((backup/'ResearchHelper-recovery.json').read_text())
        self.assertEqual(receipt['status'],'failed')
        self.assertEqual(receipt['rollback_conflicts'],[])

    def test_legacy_default_uses_only_the_selected_temporary_home_trash(self):
        legacy=self.base/'.Trash';legacy.mkdir()
        with patch.object(sync.Path,'home',return_value=self.base):
            result=sync.sync_current(self.candidate_path,self.package_path,self.browser_path,root=self.root)
        self.assertEqual(Path(result['recovery_directory']).parent,legacy)
        self.assertFalse((self.root/'result/demo-sync-recovery').exists())
