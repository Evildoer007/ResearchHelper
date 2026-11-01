"""Cleanup fault checks use only temporary fixtures and a private recovery dir."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from maintenance.retire_empty_skill_duplicates import inventory, apply_inventory


class EmptySkillDuplicateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='rh-skill-cleanup-fixture-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / 'project'
        self.target = self.root / '.skills/researchhelper'
        self.target.mkdir(parents=True)
        (self.target / 'core').mkdir()
        for name in ['SKILL.md', 'README.md', 'core/session.py', 'requirements.lock']:
            (self.target / name).write_text('Controlled test content: ' + name)
        files = {name: hashlib.sha256((self.target / name).read_bytes()).hexdigest()
                 for name in ['SKILL.md', 'README.md', 'core/session.py', 'requirements.lock']}
        record = {'version': '0.10.0', 'project_level': True, 'offline_default': True,
            'starts_second_model': False, 'files': files,
            'runtime': {'protocol_id': 'researchhelper.module-request'}}
        (self.target / 'manifest.json').write_text(json.dumps(record))
        (self.target / 'agents 2').mkdir()
        (self.target / 'SKILL 2.md').write_bytes((self.target / 'SKILL.md').read_bytes())
        (self.target / 'manifest 2.json').write_bytes((self.target / 'manifest.json').read_bytes())

    def test_inventory_is_readonly_and_apply_preserves_payload(self):
        (self.target / 'assets 10').mkdir()
        before = (self.target / 'manifest.json').read_bytes()
        plan = inventory(self.root)
        self.assertTrue((self.target / 'agents 2').is_dir())
        result = apply_inventory(plan, root=self.root, recovery=self.directory / 'recovery')
        self.assertEqual(result['directories'], 2)
        self.assertEqual(result['copies'], 2)
        self.assertEqual((self.target / 'manifest.json').read_bytes(), before)
        self.assertTrue((Path(result['recovery']) / 'SKILL 2.md').is_file())
        self.assertFalse((self.target / 'agents 2').exists())

    def test_nonempty_or_unknown_directory_is_not_retired(self):
        (self.target / 'agents 2/private.txt').write_text('Must preserve')
        with self.assertRaises(RuntimeError):
            inventory(self.root)
        self.assertTrue((self.target / 'agents 2/private.txt').is_file())

    def test_changed_copy_or_controlled_file_is_preserved(self):
        (self.target / 'SKILL 2.md').write_text('User changed copy')
        with self.assertRaises(RuntimeError):
            inventory(self.root)
        self.assertEqual((self.target / 'SKILL 2.md').read_text(), 'User changed copy')

    def test_concurrent_change_rejects_all_moves(self):
        plan = inventory(self.root)
        (self.target / 'agents 2/new.txt').write_text('Concurrent change')
        with self.assertRaises(RuntimeError):
            apply_inventory(plan, root=self.root, recovery=self.directory / 'recovery')
        self.assertTrue((self.target / 'SKILL 2.md').is_file())

    def test_unknown_file_and_link_are_preserved(self):
        (self.target / 'unknown.txt').write_text('Preserve unknown data')
        with self.assertRaises(RuntimeError):
            inventory(self.root)
        self.assertTrue((self.target / 'unknown.txt').is_file())


if __name__ == '__main__':
    unittest.main()
