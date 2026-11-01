"""Independent asset-license assembly checks, no App build or SDK access."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('rh_orb_license_builder', ROOT / 'packaging/app/build_app.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ThinkingOrbsLicenseTests(unittest.TestCase):
    def test_app_shared_license_assembly_preserves_full_text(self):
        with tempfile.TemporaryDirectory(prefix='orb-notices-', dir=ROOT / 'result/acceptance') as folder:
            target = Path(folder)
            builder.licenses(target)
            inventory = json.loads((target / 'licenses/inventory.json').read_text())
            entries = [item for item in inventory if item['package'] == 'ThinkingOrbs']
            self.assertEqual({item['file'] for item in entries},
                             {'licenses/ThinkingOrbs-LICENSE.txt', 'licenses/ThinkingOrbs-SOURCE.md'})
            self.assertEqual({item['version'] for item in entries}, {'0.3.1'})
            for name in ('ThinkingOrbs-LICENSE.txt', 'ThinkingOrbs-SOURCE.md'):
                self.assertEqual((ROOT / 'LICENSES' / name).read_bytes(), (target / 'licenses' / name).read_bytes())

    def test_missing_notice_stops_assembly(self):
        previous = builder.ROOT
        with tempfile.TemporaryDirectory(prefix='orb-missing-', dir=ROOT / 'result/acceptance') as folder:
            try:
                builder.ROOT = Path(folder) / 'empty-source'
                with self.assertRaisesRegex(RuntimeError, 'Missing vendored browser license'):
                    builder.licenses(Path(folder) / 'resources')
            finally:
                builder.ROOT = previous

    def test_source_map_and_windows_common_helper(self):
        files = json.loads((ROOT / 'packaging/package-source-map.json').read_text())['files']
        for name in ('ThinkingOrbs-LICENSE.txt', 'ThinkingOrbs-SOURCE.md'):
            self.assertIn('LICENSES/' + name, files)
        license_source = ROOT / 'LICENSES/ThinkingOrbs-LICENSE.txt'
        upstream = Path('/Users/haoranxu/Desktop/OptionHelper/LICENSES/ThinkingOrbs-LICENSE.txt')
        self.assertEqual(hashlib.sha256(license_source.read_bytes()).hexdigest(),
                         hashlib.sha256(upstream.read_bytes()).hexdigest())
        # Structural wiring only, not a Windows build/installation claim.
        self.assertIn('common.licenses(resources)', (ROOT / 'packaging/app/windows/build_windows.py').read_text())


if __name__ == '__main__':
    unittest.main()
