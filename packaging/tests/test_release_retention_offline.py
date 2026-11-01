"""New isolated release-retirement safety tests; no production artifacts used."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_retention as retention
from artifact_integrity import directory_record


class ReleaseRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'repo'
        self.root.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.trash = Path(self.temp.name) / 'recovery'
        self.trash.mkdir()
        self.current = self.archive(b'current', 'app')
        self.old = self.archive(b'old', 'app')
        self.skill = self.archive(b'current skill', 'skill')
        self.old_skill = self.archive(b'old skill', 'skill')
        self.record = self.root / 'result/build-candidates/current/candidate.json'
        self.record.parent.mkdir(parents=True)
        self.record.write_text('{}')
        (self.root / 'dist').mkdir()
        for file in (self.current, self.skill):
            (self.root / 'dist' / file.name).write_bytes(file.read_bytes())
        self.candidate = {'artifacts': {'dmg': {'path': str(self.current)},
                                        'skill': {'path': str(self.skill)}}}
        self.verifier = patch.object(retention, 'verify_candidate', return_value=self.candidate)
        self.verifier.start()
        self.addCleanup(self.verifier.stop)

    def archive(self, content, kind):
        name = ('ResearchHelper-0.10.0-arm64.dmg' if kind == 'app'
                else 'ResearchHelper-Skill-0.10.0.zip')
        import hashlib
        folder = self.root / 'versions' / kind / 'v0.10.0' / hashlib.sha256(content).hexdigest()
        folder.mkdir(parents=True)
        file = folder / name
        file.write_bytes(content)
        (folder / 'artifact.json').write_text('{}')
        return file

    def inventory(self, **kwargs):
        return retention.inventory(self.root, self.record, **kwargs)

    def test_keeps_current_archives_and_downloads(self):
        plan = self.inventory()
        self.assertEqual({t['path'] for t in plan['targets']}, {str(self.old), str(self.old_skill)})
        result = retention.apply_inventory(plan, recovery=self.trash)
        self.assertEqual(result['moved'], 2)
        self.assertTrue(self.current.is_file())
        self.assertTrue(self.skill.is_file())
        self.assertTrue((self.root / 'dist' / self.current.name).is_file())
        receipt = json.loads(Path(result['receipt']).read_text())
        for move in receipt['moves']:
            self.assertFalse(Path(move['source']).exists())
            self.assertTrue(Path(move['destination']).is_file())
        self.assertFalse(receipt['permanent_deletion'])

    def test_changed_content_aborts_before_any_move(self):
        plan = self.inventory()
        self.old.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'inventory changed'):
            retention.apply_inventory(plan, recovery=self.trash)
        self.assertTrue(self.old_skill.is_file())
        self.assertEqual(list(self.trash.iterdir()), [])

    def test_new_archive_aborts_old_plan(self):
        plan = self.inventory()
        self.archive(b'another old image', 'app')
        with self.assertRaisesRegex(RuntimeError, 'inventory changed'):
            retention.apply_inventory(plan, recovery=self.trash)

    def test_hash_mismatch_is_skipped(self):
        self.old.write_bytes(b'not the recorded hash')
        plan = self.inventory()
        self.assertNotIn(str(self.old), {t['path'] for t in plan['targets']})
        self.assertEqual(len(plan['skipped']), 1)

    def test_tracked_archive_is_not_retired(self):
        subprocess.run(['git', 'add', str(self.old.relative_to(self.root))], cwd=self.root, check=True)
        self.assertNotIn(str(self.old), {t['path'] for t in self.inventory()['targets']})

    def test_linked_archive_root_rejected(self):
        base = self.root / 'versions/app'
        base.rename(self.root / 'saved-app')
        base.symlink_to(self.root / 'saved-app', target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'Archive directory'):
            self.inventory()

    def test_recovery_inside_repository_rejected(self):
        recovery = self.root / 'backup'
        recovery.mkdir()
        with self.assertRaisesRegex(RuntimeError, 'outside'):
            retention.apply_inventory(self.inventory(), recovery=recovery)
        self.assertTrue(self.old.is_file())

    def test_invalid_current_candidate_never_cleans(self):
        with patch.object(retention, 'verify_candidate', side_effect=RuntimeError('bad current candidate')):
            with self.assertRaisesRegex(RuntimeError, 'bad current'):
                self.inventory()
        self.assertTrue(self.old.is_file())

    def test_independent_other_platform_download_keeps_its_skill(self):
        folder = self.root / 'result/windows-candidate'
        folder.mkdir()
        (folder / self.old_skill.name).write_bytes(self.old_skill.read_bytes())
        self.assertNotIn(str(self.old_skill), {t['path'] for t in self.inventory()['targets']})

    def test_old_demo_requires_exact_manifest_and_retains_receipt(self):
        record = self.root / 'result/build-candidates/old/candidate.json'
        demo = record.parent / 'demo'
        demo.mkdir(parents=True)
        (demo / 'manifest.json').write_text('{}')
        (demo / 'index.html').write_text('<h1>old generated demo</h1>')
        record.write_text(json.dumps({'directory_artifacts': {'demo': directory_record(demo)}}))
        plan = self.inventory(include_old_demos=True)
        self.assertIn(str(demo), {t['path'] for t in plan['targets']})
        retention.apply_inventory(plan, recovery=self.trash)
        self.assertTrue(record.is_file())
        self.assertFalse(demo.exists())

    def test_extra_demo_file_preserves_entire_demo(self):
        record = self.root / 'result/build-candidates/old/candidate.json'
        demo = record.parent / 'demo'
        demo.mkdir(parents=True)
        (demo / 'manifest.json').write_text('{}')
        record.write_text(json.dumps({'directory_artifacts': {'demo': directory_record(demo)}}))
        (demo / 'research.sqlite').write_bytes(b'original research')
        plan = self.inventory(include_old_demos=True)
        self.assertNotIn(str(demo), {t['path'] for t in plan['targets']})
        self.assertTrue(plan['skipped'])

    def test_diagnostic_images_only_by_explicit_option(self):
        file = self.root / 'result/acceptance/old/failed-default-filesystem.dmg'
        file.parent.mkdir(parents=True)
        file.write_bytes(b'failed test image')
        self.assertNotIn(str(file), {t['path'] for t in self.inventory()['targets']})
        self.assertIn(str(file), {t['path'] for t in self.inventory(include_test_images=True)['targets']})

    def test_missing_policy_does_not_retire(self):
        self.assertEqual(retention.retire_after_build(self.root, self.record)['status'], 'policy_not_configured')
        self.assertTrue(self.old.is_file())

    def test_invalid_policy_does_not_retire(self):
        folder = self.root / 'packaging'
        folder.mkdir()
        (folder / 'release-retention.json').write_text('{"recovery":"delete everything"}')
        with self.assertRaisesRegex(RuntimeError, 'Unknown release retention'):
            retention.retire_after_build(self.root, self.record)
        self.assertTrue(self.old.is_file())

    def test_macos_policy_does_not_touch_windows(self):
        folder = self.root / 'packaging'
        folder.mkdir()
        (folder / 'release-retention.json').write_text(json.dumps({
            'schema': 'researchhelper.release-retention.v1',
            'macos': 'current_candidate_only', 'recovery': 'trash'}))
        with patch.object(retention.sys, 'platform', 'win32'):
            self.assertEqual(retention.retire_after_build(self.root, self.record)['status'], 'macos_policy_not_applicable')
        self.assertTrue(self.old.is_file())

    def test_old_bundle_is_owned_and_internal_links_move_together(self):
        import plistlib
        app = self.root / 'versions/app/v0.9.0/ResearchHelper.app'
        (app / 'Contents/MacOS').mkdir(parents=True)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleName': 'ResearchHelper', 'CFBundleExecutable': 'ResearchHelper'}))
        (app / 'Contents/MacOS/ResearchHelper').write_bytes(b'compiled fixture')
        (app / 'Contents/current').symlink_to('MacOS/ResearchHelper')
        plan = self.inventory(include_old_apps=True)
        self.assertIn(str(app), {t['path'] for t in plan['targets']})
        retention.apply_inventory(plan, recovery=self.trash)
        self.assertFalse(app.exists())

    def test_bundle_linking_outside_is_not_retired(self):
        import plistlib
        app = self.root / 'versions/app/v0.9.0/ResearchHelper.app'
        (app / 'Contents/MacOS').mkdir(parents=True)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleName': 'ResearchHelper', 'CFBundleExecutable': 'ResearchHelper'}))
        (app / 'Contents/MacOS/ResearchHelper').write_bytes(b'compiled fixture')
        (app / 'Contents/external').symlink_to(self.current)
        plan = self.inventory(include_old_apps=True)
        self.assertNotIn(str(app), {t['path'] for t in plan['targets']})
        self.assertTrue(plan['skipped'])

    def test_opt_in_build_retires_only_after_current_commit(self):
        self.verifier.stop()
        import build_current as delivery
        from tests.test_current_delivery_offline import CurrentDeliveryTests
        fixture = CurrentDeliveryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        subprocess.run(['git', 'init', '-q', str(fixture.root)], check=True)
        recovery = fixture.root.parent / (fixture.root.name + '-recovery')
        recovery.mkdir()
        self.addCleanup(lambda: __import__('shutil').rmtree(recovery))
        def builder(**kwargs):
            built = fixture.builder(**kwargs)
            (fixture.root / 'packaging/release-retention.json').write_text(json.dumps({
                'schema': 'researchhelper.release-retention.v1',
                'macos': 'current_candidate_only', 'recovery': 'trash'}))
            return built
        actual_apply = retention.apply_inventory
        with patch.object(delivery, 'verify_release_binding'), patch.object(retention, 'apply_inventory',
                side_effect=lambda plan: actual_apply(plan, recovery=recovery)), patch.object(
                retention, '_bounded_retirement', side_effect=lambda root, record, **kw: retention._retire_now(root, record)):
            result = delivery.build_current(fixture.root, builder=builder)
            record = Path(result['payload']) / 'candidate.json'
            self.assertEqual(delivery.verify_current_build(record, fixture.root), result)
        self.assertEqual(result['release_retirement']['status'], 'retired_recoverably')
        self.assertEqual(result['release_retirement']['moved'], 1)
        self.assertFalse(Path(result['previous_artifacts'][0]).exists())
        self.assertTrue(all(Path(item['path']).is_file() for item in result['artifacts'].values()))


if __name__ == '__main__':
    unittest.main()
