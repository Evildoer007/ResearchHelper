"""Independent cache-link boundaries in disposable fake repositories."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'maintenance'))
from tidy_development_workspace import inventory, permitted_compiler_cache_links, validate


class CompilerLockCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / 'result/acceptance', prefix='cache-lock-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.cache = self.root / 'result/acceptance/owned-probe/swift-cache'
        self.cache.mkdir(parents=True)
        self.lock = self.cache / 'AppKit.pcm.lock'
        self.target = self.cache / 'AppKit.pcm.lock-a123bc45'
        (self.cache / 'AppKit.pcm').write_bytes(b'independent compiled fixture')

    def test_stale_absolute_owned_lock_and_apply_validation(self):
        self.lock.symlink_to(self.target)
        self.assertTrue(permitted_compiler_cache_links(self.cache))
        plan = inventory(self.root)
        self.assertEqual([item['source'] for item in plan['targets']], [str(self.cache)])
        self.assertEqual(validate(plan, self.root), [self.cache])

    def test_relative_lock_with_regular_target(self):
        self.target.write_bytes(b'compiler-owned lock')
        self.lock.symlink_to(self.target.name)
        self.assertTrue(permitted_compiler_cache_links(self.cache))

    def test_external_target_is_skipped_and_visible(self):
        outside = self.root / 'retained-original.pcm.lock-a123bc45'
        outside.write_bytes(b'retained original')
        self.lock.symlink_to(outside)
        plan = inventory(self.root)
        self.assertFalse(plan['targets'])
        self.assertEqual(len(plan['skipped']), 1)
        self.assertEqual(outside.read_bytes(), b'retained original')

    def test_other_alias_and_directory_target_are_not_cache_locks(self):
        self.lock.symlink_to(self.target)
        self.target.mkdir()
        self.assertFalse(permitted_compiler_cache_links(self.cache))
        self.lock.unlink()
        (self.cache / 'material-alias').symlink_to(self.root / 'private-data')
        self.assertFalse(permitted_compiler_cache_links(self.cache))

    def test_changed_lock_after_inventory_blocks_apply(self):
        self.lock.symlink_to(self.target)
        plan = inventory(self.root)
        self.lock.unlink()
        self.lock.symlink_to(self.root / 'outside.pcm.lock-a123bc45')
        with self.assertRaises(RuntimeError):
            validate(plan, self.root)

    def test_active_or_mounted_cache_is_not_moved(self):
        self.lock.symlink_to(self.target)
        plan = inventory(self.root)
        for arguments in [{'processes': str(self.cache)}, {'mounted': [self.cache]}]:
            with self.assertRaises(RuntimeError):
                validate(plan, self.root, **arguments)

    def test_chained_lock_and_wrong_lock_name_are_rejected(self):
        self.lock.symlink_to(self.target)
        self.target.symlink_to(self.root / 'external')
        self.assertFalse(permitted_compiler_cache_links(self.cache))
        self.target.unlink()
        self.lock.unlink()
        self.lock.symlink_to(self.cache / 'arbitrary-file')
        self.assertFalse(permitted_compiler_cache_links(self.cache))
