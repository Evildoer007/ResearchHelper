"""Real ad-hoc signing of an isolated minimal App; no executable is launched."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class ReleaseBundleCopyContractTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='rh-seal-contract-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        spec = importlib.util.spec_from_file_location('isolated_seal_contract', ROOT / 'packaging/app/build_app.py')
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        self.builder.OUTPUT = self.root
        self.bundle = self.root / 'ResearchHelper.app'
        contents = self.bundle / 'Contents'
        (contents / 'MacOS').mkdir(parents=True)
        (contents / 'Frameworks').mkdir()
        (contents / 'Resources').mkdir()
        shutil.copy('/usr/bin/true', contents / 'MacOS/ResearchHelper')
        shutil.copy('/usr/bin/true', contents / 'Frameworks/libcanonical.dylib')
        (contents / 'Frameworks/libalias.dylib').symlink_to('libcanonical.dylib')
        (contents / 'Resources/canonical.txt').write_text('retained resource')
        (contents / 'Resources/alias.txt').symlink_to('canonical.txt')
        with (contents / 'Info.plist').open('wb') as stream:
            plistlib.dump({'CFBundleName': 'ResearchHelper', 'CFBundleIdentifier': 'io.researchhelper.sealtest',
                'CFBundleExecutable': 'ResearchHelper', 'CFBundlePackageType': 'APPL',
                'CFBundleVersion': '1', 'CFBundleShortVersionString': '1'}, stream)

    def tree_identity(self):
        return {str(path.relative_to(self.bundle)):
            ('link', os.readlink(path)) if path.is_symlink() else
            ('file', hashlib.sha256(path.read_bytes()).hexdigest())
            for path in self.bundle.rglob('*') if path.is_file() or path.is_symlink()}

    def test_signed_tree_preserves_aliases_and_actual_signature(self):
        original_copytree = shutil.copytree
        injected = []

        def copy_with_destination_metadata(source, target, *args, **kwargs):
            result = original_copytree(source, target, *args, **kwargs)
            if Path(target) == self.bundle:
                # Mirror actual Desktop file-provider FinderInfo, introduced
                # after the already-clean temporary bundle has been signed.
                subprocess.run(['/usr/bin/xattr', '-wx', 'com.apple.FinderInfo',
                    '0000000000000000200000000000000000000000000000000000000000000000', str(target)], check=True)
                subprocess.run(['/usr/bin/xattr', '-wx', 'com.apple.ResourceFork', 'deadbeef',
                    str(Path(target) / 'Contents/Resources/canonical.txt')], check=True)
                invalid = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(target)],
                                         capture_output=True)
                self.assertNotEqual(invalid.returncode, 0)
                injected.append(True)
            return result

        with patch.object(self.builder.shutil, 'copytree', side_effect=copy_with_destination_metadata):
            disk = self.builder.seal(self.bundle)
        self.assertEqual(injected, [True])
        for relative, target in [('Frameworks/libalias.dylib', 'libcanonical.dylib'),
                                  ('Resources/alias.txt', 'canonical.txt')]:
            alias = self.bundle / 'Contents' / relative
            self.assertTrue(alias.is_symlink(), relative)
            self.assertEqual(os.readlink(alias), target)
        manifest = json.loads((self.root / 'app-manifest.json').read_text())
        self.assertTrue(manifest['distribution_signature_verified'])
        self.assertFalse(manifest['desktop_tree_signed'])
        self.assertEqual(manifest['signature_verified_from'], 'read-only mounted DMG')
        self.assertIn('FileProvider', manifest['desktop_tree_limitation'])
        # Destination metadata remains observable instead of claiming a race-
        # dependent Desktop signature. All distribution checks use the image.
        invalid_desktop = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(self.bundle)],
                                        capture_output=True)
        self.assertNotEqual(invalid_desktop.returncode, 0)
        with (self.bundle / 'Contents/_CodeSignature/CodeResources').open('rb') as stream:
            signed = plistlib.load(stream)
        self.assertEqual(signed['files2']['Frameworks/libalias.dylib']['symlink'], 'libcanonical.dylib')
        self.assertEqual(signed['files2']['Resources/alias.txt']['symlink'], 'canonical.txt')
        self.assertTrue(disk.is_file())
        subprocess.run(['/usr/bin/hdiutil', 'verify', str(disk)], check=True, capture_output=True)
        mount = self.root / 'mounted-image'
        mount.mkdir()
        mounted = False
        try:
            subprocess.run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse',
                '-mountpoint', str(mount), str(disk)], check=True, capture_output=True)
            mounted = True
            original = mount / 'ResearchHelper.app'
            self.assertTrue((original / 'Contents/Frameworks/libalias.dylib').is_symlink())
            subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(original)],
                           check=True, capture_output=True)
            preserved = self.root / 'preserved-copy.app'
            shutil.copytree(original, preserved, symlinks=True, copy_function=shutil.copy)
            subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(preserved)],
                           check=True, capture_output=True)
            expanded = self.root / 'expanded-copy.app'
            shutil.copytree(original, expanded, copy_function=shutil.copy)
            invalid = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(expanded)],
                                     capture_output=True)
            self.assertNotEqual(invalid.returncode, 0, 'Expanded aliases must not validate against a symlink seal')
        finally:
            if mounted:
                subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)], check=True, capture_output=True)
        self.assertFalse((self.root / 'ResearchHelper.app.pre-seal-recovery').exists())

    def test_copy_failure_restores_original_tree_without_alias_changes(self):
        before = self.tree_identity()
        original_copytree = shutil.copytree
        saw_signed_source = []

        def fail_copy_back(source, target, *args, **kwargs):
            if Path(target) == self.bundle:
                subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(source)],
                               check=True, capture_output=True)
                saw_signed_source.append(True)
                Path(target).mkdir(exist_ok=True)
                (Path(target) / 'partial-copy').write_text('incomplete')
                raise OSError('Injected copy-back failure after real signing')
            return original_copytree(source, target, *args, **kwargs)

        with patch.object(self.builder.shutil, 'copytree', side_effect=fail_copy_back):
            with self.assertRaisesRegex(OSError, 'Injected copy-back failure'):
                self.builder.seal(self.bundle)
        self.assertEqual(saw_signed_source, [True])
        self.assertEqual(self.tree_identity(), before)
        self.assertFalse((self.bundle / 'partial-copy').exists())
        self.assertFalse((self.root / 'ResearchHelper.app.pre-seal-recovery').exists())

    def test_existing_recovery_is_preserved_and_other_targets_rejected(self):
        recovery = self.root / 'ResearchHelper.app.pre-seal-recovery'
        recovery.mkdir()
        marker = recovery / 'retained.txt'
        marker.write_text('do not overwrite an interrupted recovery')
        before = self.tree_identity()
        with self.assertRaises(FileExistsError):
            self.builder.seal(self.bundle)
        self.assertEqual(marker.read_text(), 'do not overwrite an interrupted recovery')
        self.assertEqual(self.tree_identity(), before)
        with self.assertRaises(ValueError):
            self.builder.seal(self.root / 'Other.app')
