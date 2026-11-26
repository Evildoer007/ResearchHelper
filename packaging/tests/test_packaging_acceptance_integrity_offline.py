"""Independent regressions for the deep packaging review. No builds or network."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'packaging'))
sys.path.insert(0, str(ROOT / 'maintenance'))
from artifact_integrity import directory_record, verify_directory
from candidate_acceptance import selected_candidate
from candidate_acceptance import verify_mounted_bundle
from types import SimpleNamespace
from source_snapshot import source_files


class AcceptanceIntegrityTests(unittest.TestCase):
    def fixture(self, directory):
        demo = Path(directory) / 'demo'
        demo.mkdir()
        (demo / 'manifest.json').write_text('{}')
        (demo / 'index.html').write_text('offline fixture')
        return demo, directory_record(demo)

    def test_complete_demo_inventory_matches(self):
        with tempfile.TemporaryDirectory() as folder:
            _, record = self.fixture(folder)
            self.assertEqual(verify_directory(record, folder), record)

    def test_changed_missing_and_extra_demo_files_are_rejected(self):
        for mutation in ('changed', 'missing', 'extra'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                demo, record = self.fixture(folder)
                if mutation == 'changed':
                    (demo / 'index.html').write_text('changed')
                elif mutation == 'missing':
                    (demo / 'index.html').unlink()
                else:
                    (demo / 'extra.json').write_text('{}')
                with self.assertRaises(RuntimeError):
                    verify_directory(record, folder)

    def test_demo_symlink_or_outside_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as other:
            demo, record = self.fixture(folder)
            with self.assertRaises(RuntimeError):
                verify_directory(record, other)
            (demo / 'alias').symlink_to(demo / 'index.html')
            with self.assertRaises(RuntimeError):
                directory_record(demo)

    def test_selection_requires_explicit_identity(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                selected_candidate(ROOT)
        chosen = ROOT / 'result/build-staging/explicit-test/candidate.json'
        with patch.dict(os.environ, RESEARCHHELPER_CANDIDATE_RECEIPT=str(chosen)):
            self.assertEqual(selected_candidate(ROOT), chosen)

    def test_selection_rejects_another_repository(self):
        with patch.dict(os.environ, RESEARCHHELPER_CANDIDATE_RECEIPT='/synthetic-other/candidate.json'):
            with self.assertRaises(RuntimeError):
                selected_candidate(ROOT)

    def test_mounted_backend_identity_rejects_another_dmg_or_writable_mount(self):
        candidate = {'artifacts': {'dmg': {'path': '/synthetic/candidate.dmg'}}}
        info = {'images': [{'image-path': '/synthetic/candidate.dmg',
                           'system-entities': [{'mount-point': '/synthetic/mounted'}]}]}
        bundle = Path('/synthetic/mounted/ResearchHelper.app')
        with patch('candidate_acceptance.os.statvfs', return_value=SimpleNamespace(f_flag=os.ST_RDONLY)):
            verify_mounted_bundle(bundle, candidate, info)
            with self.assertRaises(RuntimeError):
                verify_mounted_bundle(Path('/synthetic/other/ResearchHelper.app'), candidate, info)
        with patch('candidate_acceptance.os.statvfs', return_value=SimpleNamespace(f_flag=0)):
            with self.assertRaises(RuntimeError):
                verify_mounted_bundle(bundle, candidate, info)

    def test_windows_separators_cannot_bypass_source_area_checks(self):
        for name in ('data\\credentials.json', 'C:\\secret.json', 'core//entry.py', './core/entry.py'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / 'packaging').mkdir()
                (root / 'packaging/package-source-map.json').write_text(json.dumps({'files': [name]}))
                with self.assertRaises(RuntimeError):
                    source_files(root)

    def test_current_native_icons_are_verified_without_browser(self):
        from icon_assets import verify_icons
        icons = verify_icons(ROOT)
        self.assertEqual(set(icons), {'png', 'ico', 'icns', 'dark_png', 'dark_ico', 'dark_icns'})
        source = (ROOT / 'packaging/app/build_app.py').read_text()
        self.assertNotIn('node_binary()', source)
        self.assertNotIn('build_icon.mjs', source)

    def test_restore_resolves_same_self_contained_mode_as_publish(self):
        source = (ROOT / 'packaging/app/windows/build_windows.py').read_text()
        restore = source[source.index("run([tools['dotnet'], 'restore'"):source.index("run([tools['dotnet'], 'publish'")]
        self.assertIn("'-p:SelfContained=true'", restore)
        self.assertIn("'--self-contained', 'true'", source)

    def test_nested_acceptance_uses_mounted_bundle_and_no_mtime_selection(self):
        for name in ('check_migrated_candidate.py', 'check_dual_platform_delivery.py'):
            source = (ROOT / 'maintenance' / name).read_text()
            self.assertNotIn('st_mtime', source)
            self.assertIn('selected_candidate(ROOT)', source)
        source = (ROOT / 'maintenance/check_dual_platform_delivery.py').read_text()
        self.assertIn("RESEARCHHELPER_ACCEPTANCE_BUNDLE=str(mount / 'ResearchHelper.app')", source)
        self.assertNotIn("'run': 51", source)
        self.assertNotIn("'double_click_entry_executed': True", source)


if __name__ == '__main__':
    unittest.main()
