"""Independent safety checks for the current recoverable cleanup utility."""
import hashlib
import json
import plistlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from tidy_development_workspace import generated_demo, inventory, tree_stamp, validate


class CurrentCleanupSafetyTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name).resolve()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)

    def plan(self, path, category='regenerable_compiler_cache'):
        path.mkdir(parents=True, exist_ok=True)
        return {'root': str(self.root), 'targets': [{'source': str(path), 'category': category, 'stamp': tree_stamp(path)}]}

    def test_preserved_assets_never_selected(self):
        for name in ('data', 'demo', 'dist', 'versions', 'result/build-candidates', 'result/host-research'):
            (self.root / name / '__pycache__').mkdir(parents=True)
        target = self.root / 'packaging/__pycache__';target.mkdir(parents=True)
        self.assertEqual([item['source'] for item in inventory(self.root)['targets']], [str(target)])

    def test_tracked_cache_not_selected_or_allowed(self):
        path = self.root / 'core/__pycache__';plan=self.plan(path)
        (path / 'source.py').write_text('retained source')
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        self.assertFalse(inventory(self.root)['targets'])
        with self.assertRaises(RuntimeError):validate(plan,self.root)

    def test_changes_block_apply(self):
        path=self.root / 'core/__pycache__';plan=self.plan(path)
        (path / 'new.pyc').write_bytes(b'new')
        with self.assertRaises(RuntimeError):validate(plan,self.root)

    def test_protected_target_blocked(self):
        for name in ('demo', 'data', 'dist', 'versions', 'result/build-candidates'):
            with self.subTest(name=name):
                with self.assertRaises(RuntimeError):validate(self.plan(self.root/name/'__pycache__'),self.root)

    def test_root_outside_symlink_blocked(self):
        path=self.root/'alias';path.symlink_to(self.root/'outside',target_is_directory=True)
        for target in (self.root,self.root.parent,path):
            with self.subTest(target=target),self.assertRaises(RuntimeError):
                validate({'root':str(self.root),'targets':[{'source':str(target),'category':'regenerable_compiler_cache','stamp':{}}]},self.root)

    def test_live_and_mount_blocked(self):
        path=self.root/'core/__pycache__';plan=self.plan(path)
        with self.assertRaises(RuntimeError):validate(plan,self.root,mounted=[path/'mount'])
        with self.assertRaises(RuntimeError):validate(plan,self.root,processes='python '+str(path/'probe.py'))

    def test_nested_symlink_blocked(self):
        path=self.root/'core/__pycache__';self.plan(path);(path/'link').symlink_to(self.root/'private')
        with self.assertRaises(RuntimeError):validate(self.plan(path),self.root)

    def test_unknown_backup_and_research_store_retained(self):
        for name in ('result/backups/old', 'result/acceptance/check/store', 'result/acceptance/check/source'):
            (self.root/name).mkdir(parents=True)
            (self.root/name/'record.json').write_text('{}')
        self.assertFalse(inventory(self.root)['targets'])

    def test_demo_requires_manifest_and_complete_identity(self):
        path=self.root/'result/acceptance/check/build/demo';path.mkdir(parents=True)
        source=path/'index.html';source.write_text('synthetic demo')
        record={'source':'ResearchHelper','runtime':{'protocol_id':'researchhelper.module-request'},'files':{'index.html':hashlib.sha256(source.read_bytes()).hexdigest()}}
        (path/'manifest.json').write_text(json.dumps(record))
        self.assertTrue(generated_demo(path));self.assertEqual(len(inventory(self.root)['targets']),1)
        source.write_text('changed');self.assertFalse(generated_demo(path))
        record['files']['index.html']=hashlib.sha256(source.read_bytes()).hexdigest();(path/'manifest.json').write_text(json.dumps(record))
        (path/'original.pdf').write_bytes(b'original');self.assertFalse(generated_demo(path))

    def test_overlapping_targets_blocked(self):
        path=self.root/'core/__pycache__';nested=path/'swift-cache';nested.mkdir(parents=True)
        plan=self.plan(path);plan['targets']+=self.plan(nested)['targets']
        with self.assertRaises(RuntimeError):validate(plan,self.root)

    def test_internal_app_framework_links_move_with_bundle(self):
        path=self.root/'result/acceptance/check/ResearchHelper.app'
        (path/'Contents/MacOS').mkdir(parents=True)
        (path/'Contents/MacOS/ResearchHelper').write_bytes(b'synthetic executable')
        (path/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleName':'ResearchHelper','CFBundleExecutable':'ResearchHelper'}))
        library=path/'Contents/Frameworks';library.mkdir()
        (library/'libactual').write_bytes(b'test framework')
        (library/'libalias').symlink_to('libactual')
        plan=inventory(self.root)
        self.assertEqual(len(plan['targets']),1)
        self.assertEqual(validate(plan,self.root),[path])
        (library/'external').symlink_to(self.root/'private')
        self.assertFalse(inventory(self.root)['targets'])
        with self.assertRaises(RuntimeError):validate(self.plan(path,'compiled_test_app_copy'),self.root)


if __name__ == '__main__':
    unittest.main()
