"""Independent offline checks of the browser notices shipped with each product."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'packaging'))
import shared_payload

ECHARTS = {
    'Apache-ECharts-LICENSE.txt': '634293835b43a6dd2094fa39182a3d9a6b9ca43b7fdb9ac354e8037af2a3093a',
    'Apache-ECharts-NOTICE.txt': 'dc0d9176413c3d96d0ac40b39b1dbb11e3043b7b010be560f5dfe66bd3774ae1',
    'Apache-ECharts-d3-LICENSE.txt': 'e1211892da0b0e0585b7aebe8f98c1274fba15bafe47fa1f4ee8a7a502c06304',
}
SOURCE = 'Apache-ECharts-SOURCE.md'


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BrowserNoticesTests(unittest.TestCase):
    def temporary(self):
        owner = tempfile.TemporaryDirectory(prefix='browser-notices-', dir=ROOT / 'result/acceptance')
        self.addCleanup(owner.cleanup)
        return Path(owner.name)

    def test_upstream_text_and_existing_chart_bytes_remain_exact(self):
        for name, value in ECHARTS.items():
            self.assertEqual(hashlib.sha256((ROOT / 'LICENSES' / name).read_bytes()).hexdigest(), value)
        self.assertEqual(hashlib.sha256((ROOT / 'assets/vendor/echarts.min.js').read_bytes()).hexdigest(),
                         'baa8dfe7e1d9336b98e8986ba7e20ea15e7cdbea1ef42a59d59478632fa45a1d')

    def test_shared_browser_inventory_copies_only_registered_packages(self):
        target = self.temporary() / 'resources'
        inventory = shared_payload.copy_browser_notices(target)
        self.assertEqual({item['package'] for item in inventory}, {'Apache ECharts', 'ThinkingOrbs', 'Bloub'})
        files = set(ECHARTS) | {SOURCE, 'ThinkingOrbs-LICENSE.txt', 'ThinkingOrbs-SOURCE.md', 'Bloub-LICENSE.txt', 'Bloub-SOURCE.md'}
        self.assertEqual({path.name for path in (target / 'licenses').iterdir()}, files)
        for name in files:
            self.assertEqual((target / 'licenses' / name).read_bytes(), (ROOT / 'LICENSES' / name).read_bytes())

    def test_missing_notice_rejected_before_any_payload_copy(self):
        source = self.temporary() / 'source'
        (source / 'LICENSES').mkdir(parents=True)
        target = source.parent / 'output'
        with self.assertRaisesRegex(RuntimeError, 'Missing vendored browser license'):
            shared_payload.copy_browser_notices(target, packages=('Apache ECharts',), root=source)
        self.assertFalse(target.exists())

    def test_linked_source_notices_rejected_without_following_target(self):
        source = self.temporary() / 'source'
        (source / 'LICENSES').mkdir(parents=True)
        for name in set(ECHARTS) | {SOURCE}:
            (source / 'LICENSES' / name).symlink_to(ROOT / 'LICENSES' / name)
        target = source.parent / 'output'
        with self.assertRaisesRegex(RuntimeError, 'browser license'):
            shared_payload.copy_browser_notices(target, packages=('Apache ECharts',), root=source)
        self.assertFalse(target.exists())

    def test_unregistered_package_cannot_add_arbitrary_files(self):
        target = self.temporary() / 'resources'
        with self.assertRaises(ValueError):
            shared_payload.copy_browser_notices(target, packages=('../private',))
        self.assertFalse(target.exists())

    def test_app_shared_inventory_contains_echarts_and_orbs(self):
        builder = load('packaging/app/build_app.py', 'browser_notices_app')
        target = self.temporary() / 'resources'
        builder.licenses(target)
        inventory = json.loads((target / 'licenses/inventory.json').read_text())
        entries = [item for item in inventory if item['package'] == 'Apache ECharts']
        self.assertEqual({item['file'] for item in entries}, {'licenses/' + name for name in set(ECHARTS) | {SOURCE}})
        self.assertEqual({item['version'] for item in entries}, {'6.0.0'})

    def test_skill_zip_carries_report_library_not_unused_chat_runtime(self):
        builder = load('packaging/skill/build_skill.py', 'browser_notices_skill')
        archive = builder.skill(output=self.temporary(), install=False)
        with zipfile.ZipFile(archive) as bundle:
            for name in set(ECHARTS) | {SOURCE}:
                self.assertEqual(bundle.read('researchhelper/licenses/' + name), (ROOT / 'LICENSES' / name).read_bytes())
            self.assertFalse(any('ThinkingOrbs' in name for name in bundle.namelist()))
            manifest = json.loads(bundle.read('researchhelper/manifest.json'))
            for name in ECHARTS:
                self.assertEqual(manifest['files']['licenses/' + name], ECHARTS[name])

    def test_demo_carries_complete_notices_and_unchanged_frontend(self):
        builder = load('packaging/demo/build_demo.py', 'browser_notices_demo')
        demo = builder.demo(self.temporary())
        for name in set(ECHARTS) | {SOURCE, 'ThinkingOrbs-LICENSE.txt', 'ThinkingOrbs-SOURCE.md', 'Bloub-LICENSE.txt', 'Bloub-SOURCE.md'}:
            self.assertEqual((demo / 'licenses' / name).read_bytes(), (ROOT / 'LICENSES' / name).read_bytes())
        self.assertEqual((demo / 'public/report.css').read_bytes(),
                         (ROOT / 'products/app/frontend/report.css').read_bytes())
        self.assertEqual((demo / 'assets/vendor/echarts.min.js').read_bytes(),
                         (ROOT / 'assets/vendor/echarts.min.js').read_bytes())

    def test_source_whitelist_and_windows_reuse_cover_actual_notices(self):
        from source_snapshot import source_files
        files = {path.relative_to(ROOT).as_posix() for path in source_files(ROOT)}
        for name in set(ECHARTS) | {SOURCE}:
            self.assertIn('LICENSES/' + name, files)
        self.assertIn('common.licenses(resources)', (ROOT / 'packaging/app/windows/build_windows.py').read_text())
        notice = (ROOT / 'assets/vendor/NOTICE.md').read_text()
        self.assertNotIn('Research Helper', notice)
        self.assertNotIn('render/interactive.py', notice)
        self.assertIn('modules/visualrenderer/src', notice)


if __name__ == '__main__':
    unittest.main()
