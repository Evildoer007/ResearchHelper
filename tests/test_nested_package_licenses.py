"""Preserve named notices under license directories, not just LICENSE files."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class NestedLicenseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='nested-license-', dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.original = self.folder/'source-license.txt'
        self.original.write_text('Offline synthetic license fixture')
        spec = importlib.util.spec_from_file_location('nested_license_app_builder', ROOT/'packaging/app/build_app.py')
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        self.builder.LICENSE_PACKAGES = ('offline_fixture',)

    def inventory(self, names):
        source = self.original
        class Distribution:
            version = '1.0'
            files = [Path(name) for name in names]
            def locate_file(self, relative): return source
        with patch.object(self.builder.importlib.metadata, 'distribution', return_value=Distribution()), \
                patch.object(self.builder, 'copy_browser_notices', return_value=[]):
            self.builder.licenses(self.folder/'resources')
        return json.loads((self.folder/'resources/licenses/inventory.json').read_text())

    def test_named_third_party_notices_under_licenses_are_kept(self):
        names = ['fixture.dist-info/licenses/LICENSES/Apache-2.0.txt',
                 'fixture.dist-info/licenses/data/BUILD_LICENSES/abseil.txt']
        records = [row for row in self.inventory(names) if row['package']=='offline_fixture']
        self.assertEqual(len(records), 2)
        for row in records:
            self.assertEqual((self.folder/'resources'/row['file']).read_bytes(), self.original.read_bytes())

    def test_ordinary_package_data_is_not_copied_as_a_notice(self):
        self.assertFalse(any(row['package']=='offline_fixture' for row in self.inventory(['fixture/data/settings.json'])))

    def test_parent_escape_is_rejected_even_for_a_license(self):
        with self.assertRaises(RuntimeError): self.inventory(['../LICENSE'])


if __name__ == '__main__':
    unittest.main()
