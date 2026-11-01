"""Candidate-specific release privacy checks; never infer installed-App parity."""
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'maintenance'))
from candidate_acceptance import selected_candidate, verify_candidate
PRIVATE_FILES = {'config.local.json', 'model-settings.json', 'source-settings.json',
                 'execution-policy.json', 'local-account.json', 'local-sessions.json',
                 'passwords.json', '.env'}


def allowed(relative):
    path = Path(relative)
    return (not path.is_absolute() and '..' not in path.parts
            and path.name not in PRIVATE_FILES and not path.name.startswith('.env.')
            and not path.name.endswith(('.sqlite', '.sqlite3', '.db', '.db-wal', '.db-shm'))
            and not {'__pycache__', '.local', 'installation-backups', 'demo-backups', 'live-connectivity'}.intersection(path.parts))


@unittest.skipUnless(os.environ.get('RESEARCHHELPER_CANDIDATE_RECEIPT'), 'Requires an explicitly built candidate')
class CurrentCandidatePrivacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidate = verify_candidate(selected_candidate(ROOT), ROOT)
        cls.payload = Path(cls.candidate['payload'])

    def test_skill_zip_is_complete_and_private_runtime_files_are_absent(self):
        with zipfile.ZipFile(self.candidate['artifacts']['skill']['path']) as bundle:
            self.assertIsNone(bundle.testzip())
            manifest = json.loads(bundle.read('researchhelper/manifest.json'))
            expected = {'researchhelper/' + name for name in manifest['files']} | {'researchhelper/manifest.json'}
            self.assertEqual(set(bundle.namelist()), expected)
            for name in bundle.namelist():
                self.assertTrue(allowed(name), name)
            for name, digest in manifest['files'].items():
                self.assertEqual(hashlib.sha256(bundle.read('researchhelper/' + name)).hexdigest(), digest)

    def test_demo_complete_inventory_excludes_private_store(self):
        record = self.candidate['directory_artifacts']['demo']
        for name in record['files']:
            self.assertTrue(allowed(name), name)

    def test_app_manifest_and_vendor_licenses_match(self):
        bundle = self.payload / 'ResearchHelper.app'
        manifest = json.loads((self.payload / 'app-manifest.json').read_text())
        actual = {file.relative_to(bundle).as_posix() for file in bundle.rglob('*') if file.is_file()}
        self.assertEqual(actual, set(manifest['files']))
        for name in actual:
            self.assertTrue(allowed(name), name)
        for name, expected in manifest['files'].items():
            self.assertTrue(allowed(name), name)
            file = bundle / name
            self.assertTrue(file.resolve().is_relative_to(bundle))
            self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), expected)
        resources = bundle / 'Contents/Resources'
        vendor = resources / 'backend/researchhelper-server/_internal/tinyshare'
        self.assertEqual({p.name for p in vendor.glob('*.pyc')},
                         {'__init__.pyc', 'config.pyc', 'token_utils.pyc', 'formula.pyc', 'cache_utils.pyc', 'device_id.pyc'})
        inventory = json.loads((resources / 'licenses/inventory.json').read_text())
        required = {'tinyshare', 'pandas', 'numpy', 'requests', 'urllib3', 'certifi', 'idna', 'python-dateutil', 'six', 'pytz', 'tzdata'}
        self.assertTrue(required <= {item['package'] for item in inventory})
        self.assertEqual(len(inventory), len({item['file'] for item in inventory}))
        for item in inventory:
            self.assertTrue(allowed(item['file']))
            self.assertGreater((resources / item['file']).stat().st_size, 0)

    def test_policy_and_platform_sources_are_bound(self):
        manifest = json.loads((self.payload / 'release-manifest.json').read_text())
        self.assertEqual(manifest['mode'], 'offline-candidate')
        self.assertFalse(manifest['real_model_verified'])
        self.assertFalse(manifest['real_provider_verified'])
        required = {'products/app/backend/entry.py', 'packaging/assemble_release.py',
                    'packaging/app/macos/install_app.py', 'packaging/demo/sync_demo.py',
                    'packaging/icon_assets.py', 'packaging/artifact_integrity.py',
                    'products/app/desktop/windows/Program.cs', 'packaging/app/windows/build_windows.py'}
        self.assertTrue(required <= manifest['source_hashes'].keys())
        self.assertEqual(manifest['source_hashes'], self.candidate['source_hashes'])
        with zipfile.ZipFile(self.candidate['artifacts']['skill']['path']) as bundle:
            skill = json.loads(bundle.read('researchhelper/manifest.json'))
        self.assertTrue(skill['offline_default'])
        self.assertTrue(skill['project_level'])
        self.assertFalse(skill['starts_second_model'])

    def test_packaged_icons_are_exact_approved_bytes(self):
        from PIL import Image
        resources = self.payload / 'ResearchHelper.app/Contents/Resources'
        for extension, filename in (('icns', 'ResearchHelper.icns'), ('png', 'ResearchHelperDock.png')):
            expected = ROOT / ('assets/icons/researchhelper-app-icon-tile-light.' + extension)
            self.assertEqual((resources / filename).read_bytes(), expected.read_bytes())
        with Image.open(resources / 'ResearchHelperDock.png') as image:
            image = image.convert('RGBA')
            self.assertEqual(image.size, (1024, 1024))
            self.assertLess(image.getpixel((0, 0))[3], 30)
            for x, y in ((.2, .2), (.5, .15), (.15, .5), (.85, .5), (.5, .85)):
                pixel = image.getpixel((int(x * image.width), int(y * image.height)))
                self.assertGreaterEqual(pixel[3], 245)
                self.assertGreaterEqual(min(pixel[:3]), 225)
                self.assertLessEqual(max(pixel[:3]) - min(pixel[:3]), 15)


if __name__ == '__main__':
    unittest.main()
