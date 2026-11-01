"""Independent installation guards, contained in disposable fake App bundles."""
import json
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/app/macos'))
import install_app as installer


class CurrentAppInstallationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='app-installation-fixture-',
            dir=ROOT / 'result/acceptance')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.old = self.root / 'Applications/ResearchHelper.app'
        self.stage = self.root / 'staged/ResearchHelper.app'
        self.backup = self.root / 'recovery/ResearchHelper.app'
        self.backup.parent.mkdir()
        self.before = self.make_bundle(self.old, b'old frozen candidate')
        self.after = self.make_bundle(self.stage, b'new frozen candidate')

    def make_bundle(self, directory, content):
        executable = directory / 'Contents/MacOS/ResearchHelper'
        executable.parent.mkdir(parents=True)
        executable.write_bytes(content)
        info = dict(CFBundleIdentifier='io.researchhelper.app', CFBundleName='ResearchHelper',
            CFBundleExecutable='ResearchHelper', CFBundleShortVersionString='0.10.0')
        (directory / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
        return dict(version='0.10.0', files=installer.bundle_files(directory))

    def promote(self, before=True, preflight=lambda: None):
        installer.promote(self.stage, self.old, self.after,
            self.before if before else None, self.backup, preflight)

    def authority(self, manifest=None):
        output = self.root / 'result/build-candidates/exact'
        output.mkdir(parents=True)
        app = output / 'app-manifest.json'
        app.write_text(json.dumps(manifest or self.before))
        release = output / 'release-manifest.json'
        release.write_text(json.dumps({'component_manifests': {'app': {
            'path': str(app), 'sha256': installer.digest(app)}}}))
        candidate = output / 'candidate.json'
        candidate.write_text(json.dumps(dict(schema='researchhelper.current-build.v1',
            status='built_local_candidate', platform='macos', manifest_sha256=installer.digest(release))))
        return candidate

    def test_exact_promotion_preserves_old_app(self):
        self.promote()
        self.assertEqual(installer.bundle_files(self.old), self.after['files'])
        self.assertEqual(installer.bundle_files(self.backup), self.before['files'])
        self.assertFalse(self.stage.exists())

    def test_new_installation_requires_unoccupied_destination(self):
        os.replace(self.old, self.root / 'uninstalled-old.app')
        self.promote(before=False)
        self.assertFalse(self.backup.exists())
        self.assertEqual(installer.bundle_files(self.old), self.after['files'])

    def test_unknown_added_file_is_preserved(self):
        note = self.old / 'user-note.txt'
        note.write_text('user content')
        with self.assertRaises(RuntimeError):
            self.promote()
        self.assertEqual(note.read_text(), 'user content')
        self.assertFalse(self.backup.exists())

    def test_edited_existing_app_is_preserved(self):
        executable = self.old / 'Contents/MacOS/ResearchHelper'
        executable.write_bytes(b'user changed binary')
        with self.assertRaises(RuntimeError):
            self.promote()
        self.assertEqual(executable.read_bytes(), b'user changed binary')
        self.assertFalse(self.backup.exists())

    def test_invalid_stage_never_moves_old_app(self):
        (self.stage / 'Contents/Info.plist').unlink()
        with self.assertRaises(RuntimeError):
            self.promote()
        self.assertEqual(installer.bundle_files(self.old), self.before['files'])
        self.assertFalse(self.backup.exists())

    def test_running_process_preflight_prevents_promotion(self):
        def running():
            raise RuntimeError('app still running')
        with self.assertRaises(RuntimeError):
            self.promote(preflight=running)
        self.assertEqual(installer.bundle_files(self.old), self.before['files'])

    def test_destination_appearing_during_first_install_is_retained(self):
        with self.assertRaises(RuntimeError):
            self.promote(before=False)
        self.assertEqual(installer.bundle_files(self.old), self.before['files'])

    def test_preexisting_recovery_is_never_overwritten(self):
        self.backup.mkdir()
        (self.backup / 'keep.txt').write_text('independent recovery')
        with self.assertRaises(RuntimeError):
            self.promote()
        self.assertEqual((self.backup / 'keep.txt').read_text(), 'independent recovery')

    def test_promotion_failure_restores_exact_old_app(self):
        replace = os.replace
        def fail_new(source, destination):
            if source == self.stage:
                raise OSError('simulated full destination')
            return replace(source, destination)
        with patch.object(installer.os, 'replace', side_effect=fail_new):
            with self.assertRaises(OSError):
                self.promote()
        self.assertEqual(installer.bundle_files(self.old), self.before['files'])
        self.assertEqual(installer.bundle_files(self.stage), self.after['files'])

    def test_postpromotion_unknown_changes_are_not_overwritten(self):
        original = installer.verify_bundle
        def change_after_promote(bundle, manifest):
            if bundle == self.old and manifest is self.after:
                (bundle / 'concurrent-note.txt').write_text('concurrent user change')
            return original(bundle, manifest)
        with patch.object(installer, 'verify_bundle', side_effect=change_after_promote):
            with self.assertRaises(RuntimeError):
                self.promote()
        self.assertEqual((self.old / 'concurrent-note.txt').read_text(), 'concurrent user change')
        self.assertEqual(installer.bundle_files(self.backup), self.before['files'])

    def test_links_leaving_app_are_rejected(self):
        (self.stage / 'Contents/escape').symlink_to(self.root / 'Applications')
        with self.assertRaises(RuntimeError):
            self.promote()
        self.assertEqual(installer.bundle_files(self.old), self.before['files'])

    def test_owned_relative_library_links_are_preserved(self):
        link = self.stage / 'Contents/MacOS/library-link'
        link.symlink_to('ResearchHelper')
        self.after['files'] = installer.bundle_files(self.stage)
        self.promote()
        self.assertTrue((self.old / 'Contents/MacOS/library-link').is_symlink())

    def test_recorded_manifest_remains_usable_without_old_dmg(self):
        selected = self.authority()
        self.assertEqual(installer.recorded_manifest(selected, self.root), self.before)

    def test_tampered_manifest_is_rejected(self):
        selected = self.authority()
        (selected.parent / 'app-manifest.json').write_text('{}')
        with self.assertRaises(RuntimeError):
            installer.recorded_manifest(selected, self.root)

    def test_manifest_cannot_name_parent_files(self):
        value = dict(self.before, files={'Contents/../outside': '0' * 64})
        selected = self.authority(value)
        with self.assertRaises(RuntimeError):
            installer.recorded_manifest(selected, self.root)

    def test_native_and_backend_processes_each_prevent_updates(self):
        for executable in ('Contents/MacOS/ResearchHelper',
                'Contents/Resources/backend/researchhelper-server/researchhelper-server'):
            with self.subTest(executable=executable), patch.object(installer.subprocess,
                    'check_output', return_value='123 ' + str(self.old / executable)):
                with self.assertRaises(RuntimeError):
                    installer.require_closed(self.old)

    def test_signature_error_retains_concrete_cause(self):
        result = type('SignatureResult', (), dict(returncode=1,
            stderr='resource fork, Finder information, or similar detritus not allowed'))()
        with patch.object(installer.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(RuntimeError, 'Finder information'):
                installer.signature(self.stage)


if __name__ == '__main__':
    unittest.main()
