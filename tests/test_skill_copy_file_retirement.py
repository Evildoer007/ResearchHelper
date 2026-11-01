"""Retire verified copies without touching temporarily locked empty folders."""
import unittest
from pathlib import Path
from tests import test_empty_skill_duplicate_cleanup_offline as fixture
from maintenance.retire_empty_skill_duplicates import inventory, apply_inventory


class SkillCopyFileRetirementTests(unittest.TestCase):
    def test_file_only_mode_preserves_empty_directories_and_manifest(self):
        sample = fixture.EmptySkillDuplicateTests();sample.setUp()
        self.addCleanup(sample.doCleanups)
        before = (sample.target/'manifest.json').read_bytes()
        result = apply_inventory(inventory(sample.root), root=sample.root,
            recovery=sample.directory/'recovery', include_directories=False)
        self.assertEqual(result['copies'],2)
        self.assertEqual(result['directories'],0)
        self.assertEqual(result['pending_empty_directories'],1)
        self.assertTrue((sample.target/'agents 2').is_dir())
        self.assertTrue((Path(result['recovery'])/'SKILL 2.md').is_file())
        self.assertEqual((sample.target/'manifest.json').read_bytes(),before)

    def test_file_only_mode_still_rejects_unreviewed_concurrent_changes(self):
        sample = fixture.EmptySkillDuplicateTests();sample.setUp()
        self.addCleanup(sample.doCleanups)
        plan = inventory(sample.root)
        (sample.target/'agents 2/private.txt').write_text('User data')
        with self.assertRaises(RuntimeError):
            apply_inventory(plan,root=sample.root,recovery=sample.directory/'recovery',include_directories=False)
        self.assertTrue((sample.target/'SKILL 2.md').is_file())
