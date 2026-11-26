"""Direct cleanup uses disposable, release-bound fixtures and never system Trash."""
import contextlib,hashlib,io,json,plistlib,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from tests.test_release_retention_offline import ReleaseRetentionTests as RecoveryFixture
import release_retention as retention
import reconcile_delivery as reconcile
import build_current as delivery
from artifact_integrity import directory_record
from app.macos.install_app import bundle_files

POLICY=dict(schema='researchhelper.release-retention.v2',macos='current_candidate_only',recovery='permanent')


def write_policy(root):
    path=root/'packaging/release-retention.json';path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(POLICY))


def recorded_release(root,name,*,artifacts=None,demo=None,app=None):
    folder=root/'result/build-candidates'/name;folder.mkdir(parents=True,exist_ok=True)
    release=dict(artifacts=artifacts or {})
    if demo is not None:release['directory_artifacts']=dict(demo=demo)
    if app is not None:
        path=folder/'app-manifest.json';path.write_text(json.dumps(app))
        release['component_manifests']=dict(app=dict(path=str(path),sha256=retention.digest(path)))
    source=folder/'release-manifest.json';source.write_text(json.dumps(release))
    candidate=dict(schema='researchhelper.current-build.v1',status='built_local_candidate',platform='macos',
        manifest_sha256=retention.digest(source),artifacts=artifacts or {})
    if demo is not None:candidate['directory_artifacts']=dict(demo=demo)
    path=folder/'candidate.json';path.write_text(json.dumps(candidate));return path


class PermanentReleaseCleanupTests(unittest.TestCase):
    _recovery_setup=RecoveryFixture.setUp
    archive=RecoveryFixture.archive
    inventory=RecoveryFixture.inventory
    def setUp(self):
        self._recovery_setup();write_policy(self.root)
        for name,path in (('old-app',self.old),('old-skill',self.old_skill)):
            role='skill' if path.suffix=='.zip' else 'dmg'
            recorded_release(self.root,name,artifacts={role:dict(path=str(path),sha256=retention.digest(path),bytes=path.stat().st_size)})

    def test_default_direct_policy_deletes_only_bound_superseded_archives(self):
        with patch.object(Path,'home',side_effect=AssertionError('No Trash lookup')):
            result=retention.apply_inventory(self.inventory())
        self.assertEqual(result['status'],'retired_permanently');self.assertEqual(result['deleted'],2)
        self.assertFalse(self.old.exists());self.assertFalse(self.old_skill.exists())
        self.assertTrue(self.current.is_file());self.assertTrue(self.skill.is_file())
        receipt=json.loads(Path(result['receipt']).read_text())
        self.assertTrue(receipt['permanent_deletion']);self.assertFalse(receipt['trash_used'])
        self.assertNotIn('recovery_directory',receipt);self.assertEqual(list(self.trash.iterdir()),[])

    def test_hash_named_but_unregistered_archive_is_preserved(self):
        unknown=self.archive(b'unregistered user artifact','app')
        result=retention.apply_inventory(self.inventory())
        self.assertTrue(unknown.is_file())
        self.assertTrue(any(row['path']==str(unknown) for row in result['skipped']))

    def test_unrelated_non_object_receipt_does_not_block_known_cleanup(self):
        for index,value in enumerate((None,[],{'directory_artifacts':[]},{'directory_artifacts':{'demo':None}})):
            folder=self.root/'result/build-candidates'/('000-invalid-'+str(index));folder.mkdir()
            (folder/'candidate.json').write_text(json.dumps(value));(folder/'release-manifest.json').write_text('{}')
        result=retention.apply_inventory(self.inventory(include_old_demos=True,include_old_apps=True))
        self.assertEqual(result['status'],'retired_permanently')
        self.assertFalse(self.old.exists());self.assertTrue(self.current.is_file())

    def test_automatic_cleanup_waits_for_bound_acceptance(self):
        with patch.object(retention,'_bounded_retirement') as worker:
            result=retention.retire_after_build(self.root,self.record)
        self.assertEqual(result['status'],'deferred_until_acceptance');worker.assert_not_called()
        self.assertTrue(self.old.is_file());self.assertTrue(self.old_skill.is_file())

    def test_changed_plan_is_rejected_before_any_deletion(self):
        plan=self.inventory();self.old.write_bytes(b'user changed it')
        with self.assertRaisesRegex(RuntimeError,'inventory changed'):retention.apply_inventory(plan)
        self.assertTrue(self.old_skill.is_file())

    def test_busy_artifact_prevents_the_entire_selected_delete(self):
        plan=self.inventory()
        with patch.object(retention,'_require_inactive',side_effect=RuntimeError('mounted')):
            with self.assertRaisesRegex(RuntimeError,'mounted'):retention.apply_inventory(plan)
        self.assertTrue(self.old.is_file());self.assertTrue(self.old_skill.is_file())

    def demo(self):
        path=self.root/'result/build-candidates/demo-old/demo';path.mkdir(parents=True)
        (path/'index.html').write_text('old generated demo')
        (path/'manifest.json').write_text('{}')
        recorded_release(self.root,'demo-old',demo=directory_record(path));return path

    def test_registered_demo_removed_research_and_receipts_preserved(self):
        demo=self.demo();raw=self.root/'data/research.sqlite';raw.parent.mkdir();raw.write_bytes(b'original research')
        report=self.root/'result/reports/client.html';report.parent.mkdir();report.write_text('original report')
        result=retention.apply_inventory(self.inventory(include_old_demos=True))
        self.assertEqual(result['status'],'retired_permanently');self.assertFalse(demo.exists())
        self.assertTrue(demo.parent.joinpath('candidate.json').is_file())
        self.assertEqual(raw.read_bytes(),b'original research');self.assertEqual(report.read_text(),'original report')

    def test_unknown_demo_file_prevents_selection(self):
        demo=self.demo();(demo/'original.sqlite').write_bytes(b'original')
        result=retention.apply_inventory(self.inventory(include_old_demos=True))
        self.assertEqual(result['status'],'retired_permanently')
        self.assertEqual((demo/'original.sqlite').read_bytes(),b'original')

    def test_new_file_during_tree_deletion_is_preserved_and_not_called_success(self):
        demo=self.demo();original=Path.unlink
        def changed(path,*args,**kwargs):
            result=original(path,*args,**kwargs)
            if path==demo/'index.html':(demo/'new-user-file.txt').write_text('keep me')
            return result
        with patch.object(Path,'unlink',changed):
            with self.assertRaises(OSError):retention.apply_inventory(self.inventory(include_old_demos=True))
        self.assertEqual((demo/'new-user-file.txt').read_text(),'keep me')
        receipts=list((self.root/'result/maintenance/release-retirement').glob('*/receipt.json'))
        record=json.loads(receipts[-1].read_text())
        self.assertEqual(record['status'],'partial_deletion_requires_review')
        self.assertTrue(any(not row['deleted'] for row in record['deletions']))

    def app(self):
        app=self.root/'versions/app/v0.9.0/ResearchHelper.app';(app/'Contents/MacOS').mkdir(parents=True)
        (app/'Contents/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleName='ResearchHelper',
            CFBundleIdentifier='io.researchhelper.app',CFBundleExecutable='ResearchHelper',CFBundleShortVersionString='0.9.0')))
        (app/'Contents/MacOS/ResearchHelper').write_bytes(b'compiled fixture')
        (app/'Contents/alias').symlink_to('MacOS',target_is_directory=True)
        (app/'Contents/current').symlink_to('alias/ResearchHelper')
        return app

    def test_bound_app_and_internal_link_chain_are_deleted(self):
        app=self.app();recorded_release(self.root,'old-native',app=dict(version='0.9.0',files=bundle_files(app)))
        result=retention.apply_inventory(self.inventory(include_old_apps=True))
        self.assertEqual(result['status'],'retired_permanently');self.assertFalse(app.exists())

    def test_unknown_app_content_is_not_authorized_by_its_current_listing(self):
        app=self.app();recorded_release(self.root,'old-native',app=dict(version='0.9.0',files=bundle_files(app)))
        (app/'client-note.txt').write_text('unique user content')
        result=retention.apply_inventory(self.inventory(include_old_apps=True))
        self.assertEqual((app/'client-note.txt').read_text(),'unique user content')
        self.assertTrue(any(row['path']==str(app) for row in result['skipped']))


class PermanentCopyCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='rh-direct-cleanup-');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();write_policy(self.root)

    def downloads(self):
        target=self.root/'dist';target.mkdir()
        canonical=target/'ResearchHelper-Skill-0.10.0.zip';canonical.write_bytes(b'current generated archive')
        copied=target/'ResearchHelper-Skill-0.10.0 2.zip';copied.write_bytes(b'old generated archive')
        recorded_release(self.root,'old',artifacts=dict(skill=dict(path=str(canonical),sha256=retention.digest(copied),bytes=copied.stat().st_size)))
        return target,canonical,copied

    def test_numbered_download_deleted_without_recovery_or_canonical_changes(self):
        target,canonical,copied=self.downloads()
        with patch.object(reconcile,'_recovery_location',side_effect=AssertionError('No recovery lookup')):
            result=reconcile.reconcile_downloads(self.root,target)
        self.assertEqual(result['status'],'retired_permanently');self.assertFalse(copied.exists())
        self.assertEqual(canonical.read_bytes(),b'current generated archive')
        receipt=json.loads(Path(result['receipt']).read_text());self.assertFalse(receipt['trash_used'])

    def test_unregistered_download_bytes_preserved(self):
        target,canonical,copied=self.downloads();copied.write_bytes(b'user bytes')
        with self.assertRaises(RuntimeError):reconcile.reconcile_downloads(self.root,target)
        self.assertEqual(copied.read_bytes(),b'user bytes');self.assertTrue(canonical.is_file())

    def test_partial_download_deletion_keeps_changed_remainder_and_receipt(self):
        target,canonical,first=self.downloads();second=target/'ResearchHelper-Skill-0.10.0 3.zip';second.write_bytes(first.read_bytes())
        original=Path.unlink
        def changed(path,*args,**kwargs):
            result=original(path,*args,**kwargs)
            if path==first:second.write_bytes(b'user changed the remainder')
            return result
        with patch.object(Path,'unlink',changed):
            with self.assertRaises(RuntimeError):reconcile.reconcile_downloads(self.root,target)
        self.assertFalse(first.exists());self.assertEqual(second.read_bytes(),b'user changed the remainder')
        record=json.loads(next((self.root/'result/maintenance/generated-download-copies').glob('*/receipt.json')).read_text())
        self.assertEqual(record['status'],'partial_deletion_requires_review')
        self.assertEqual(record['deleted'],[first.name]);self.assertTrue(canonical.is_file())

    def skill(self):
        target=self.root/'.skills/researchhelper';target.mkdir(parents=True)
        (target/'SKILL.md').write_text('current instructions')
        mapping={'SKILL.md':retention.digest(target/'SKILL.md')}
        (target/'manifest.json').write_text(json.dumps(dict(version='0.10.0',project_level=True,offline_default=True,starts_second_model=False,files=mapping)))
        (target/'SKILL 2.md').write_bytes((target/'SKILL.md').read_bytes())
        (target/'manifest 2.json').write_bytes((target/'manifest.json').read_bytes())
        (target/'core 2').mkdir();(target/'keep-empty').mkdir()
        return target

    def test_default_skill_reconciliation_deletes_copies_and_keeps_unknown_empty(self):
        target=self.skill()
        with patch.object(reconcile,'_recovery_location',side_effect=AssertionError('No recovery lookup')):
            result=reconcile.reconcile_project_skill(self.root)
        self.assertEqual(result['status'],'retired_permanently')
        self.assertFalse((target/'SKILL 2.md').exists());self.assertFalse((target/'core 2').exists())
        self.assertTrue((target/'keep-empty').is_dir());self.assertEqual((target/'SKILL.md').read_text(),'current instructions')

    def test_unknown_skill_file_preserves_every_copy(self):
        target=self.skill();(target/'user.txt').write_text('keep')
        with self.assertRaises(RuntimeError):reconcile.reconcile_project_skill(self.root)
        self.assertTrue((target/'SKILL 2.md').is_file());self.assertEqual((target/'user.txt').read_text(),'keep')


class PermanentWorkerResultTests(unittest.TestCase):
    def test_bounded_worker_returns_permanent_completion(self):
        with tempfile.TemporaryDirectory(prefix='rh-direct-worker-') as folder:
            root=Path(folder).resolve();candidate=root/'candidate.json';candidate.write_text('{}')
            worker="from pathlib import Path;import json,sys;Path(sys.argv[4]).write_text(json.dumps(dict(status='retired_permanently',deleted=2,bytes_deleted=8)))"
            with patch.object(retention,'RETIREMENT_WORKER',worker):
                result=retention._bounded_retirement(root,candidate,timeout=2)
            self.assertEqual(result['status'],'retired_permanently');self.assertEqual(result['deleted'],2)
            attempt=json.loads(Path(result['worker_receipt']).read_text())
            self.assertTrue(attempt['worker_stopped']);self.assertEqual(attempt['exit_code'],0)


class PermanentBuildPolicyTests(unittest.TestCase):
    def fixture(self):
        from tests.test_current_delivery_offline import CurrentDeliveryTests
        fixture=CurrentDeliveryTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        subprocess.run(['git','init','-q',str(fixture.root)],check=True)
        archived=delivery.archive_file(fixture.old,fixture.root)
        recorded_release(fixture.root,'old',artifacts=dict(dmg=dict(path=str(archived),sha256=retention.digest(archived),bytes=archived.stat().st_size)))
        return fixture,archived

    def test_one_click_build_uses_direct_cleanup_after_acceptance_and_skill(self):
        import accept_current as acceptance
        fixture,archived=self.fixture();events=[]
        def builder(**options):
            result=fixture.builder(**options);write_policy(fixture.root);return result
        def accept(selected,root):
            self.assertTrue(archived.is_file());events.append('accept')
            folder=root/'result/acceptance/package';folder.mkdir(parents=True)
            path=folder/'receipt.json';candidate=json.loads(selected.read_text())
            record=dict(status='scoped_passed',candidate=str(selected),manifest_sha256=candidate['manifest_sha256'],
                dataset_parity=True,service_stopped=True,mount_detached=True,temporary_research_removed=True,receipt=str(path))
            path.write_text(json.dumps(record));return record
        def skill(*args):
            self.assertTrue(archived.is_file());events.append('skill');return dict(status='already_current')
        previous_bytes=archived.read_bytes()
        def cleanup(root,record,**options):
            events.append('cleanup');result=retention._retire_now(root,record)
            # A recorded numbered copy can arrive after the retirement worker.
            (root/'dist/ResearchHelper-0.10.0-arm64 2.dmg').write_bytes(previous_bytes)
            return result
        output=io.StringIO()
        with patch.object(delivery,'verify_release_binding'),patch.object(acceptance,'accept_current',side_effect=accept), \
             patch.object(delivery,'update_project_skill',side_effect=skill), \
             patch.object(retention,'_bounded_retirement',side_effect=cleanup),contextlib.redirect_stdout(output):
            result=delivery.build_and_accept(fixture.root,builder=builder)
        self.assertEqual(events,['accept','skill','cleanup'])
        self.assertEqual(result['release_retirement']['status'],'retired_permanently')
        self.assertFalse(archived.exists());self.assertTrue(all(Path(row['path']).is_file() for row in result['artifacts'].values()))
        self.assertFalse((fixture.root/'dist/ResearchHelper-0.10.0-arm64 2.dmg').exists())
        self.assertNotIn('废纸篓',output.getvalue());self.assertIn('直接清理',output.getvalue())

    def test_failed_acceptance_never_starts_cleanup_or_updates_skill(self):
        import accept_current as acceptance
        fixture,archived=self.fixture()
        def builder(**options):
            result=fixture.builder(**options);write_policy(fixture.root);return result
        with patch.object(delivery,'verify_release_binding'), \
             patch.object(acceptance,'accept_current',side_effect=RuntimeError('package failed')), \
             patch.object(delivery,'update_project_skill') as skill, \
             patch.object(retention,'_bounded_retirement') as cleanup,contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'package failed'):delivery.build_and_accept(fixture.root,builder=builder)
        cleanup.assert_not_called();skill.assert_not_called();self.assertTrue(archived.is_file())


del RecoveryFixture
if __name__=='__main__':unittest.main()
