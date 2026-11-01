"""Exact-copy cleanup tests, fully contained in a disposable fake repository."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'maintenance'))
import retire_duplicate_skill_archive as retirement


class RecordedSkillRetirementTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='rh-retirement-fixture-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve() / 'repository'
        self.target = self.root / '.skills/researchhelper'
        self.target.mkdir(parents=True)
        self.trash = Path(folder.name).resolve() / 'fake-trash'
        self.trash.mkdir()
        hashes = {}
        for name in ('README.md', 'requirements.lock', 'SKILL.md'):
            content = ('explicit offline generated fixture ' + name).encode()
            (self.target / name).write_bytes(content)
            stem = Path(name).stem
            (self.target / (stem + ' 2' + Path(name).suffix)).write_bytes(content)
            hashes[name] = hashlib.sha256(content).hexdigest()
        raw = json.dumps({'files': hashes}).encode()
        (self.target / 'manifest.json').write_bytes(raw)
        duplicate = json.dumps({'files': hashes, 'previous': True}).encode()
        (self.target / 'manifest 2.json').write_bytes(duplicate)
        self.installed = self.root / 'result/build-candidates/installed/candidate.json'
        self.previous = self.root / 'result/build-candidates/previous/candidate.json'
        for file, value in ((self.installed, raw), (self.previous, duplicate)):
            file.parent.mkdir(parents=True)
            file.write_text(json.dumps({'project_skill_installation': {
                'manifest_sha256': hashlib.sha256(value).hexdigest()}}))
        patcher = patch.object(retirement, 'ROOT', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Map only the hard-coded Trash path to a disposable sibling. No real
        # repository, user Trash or production path can be written by this test.
        def local_path(value):
            return self.trash if str(value) == '/Users/haoranxu/.Trash' else Path(value)
        patcher = patch.object(retirement, 'Path', side_effect=local_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_retirement(self, **options):
        return retirement.retire_recorded_installation_duplicates(
            installed_candidate=options.get('installed', self.installed),
            duplicate_manifest_candidate=options.get('previous', self.previous))

    def test_only_recorded_four_copies_move_and_managed_files_remain(self):
        before = {name: (self.target / name).read_bytes() for name in
                  ('README.md', 'SKILL.md', 'requirements.lock', 'manifest.json')}
        result = self.run_retirement()
        self.assertEqual(result['status'], 'retired_recoverably')
        self.assertEqual(result['files'], 4)
        record = json.loads(Path(result['receipt']).read_text())
        self.assertFalse(record['permanent_deletion'])
        for item in record['files']:
            self.assertFalse(Path(item['source']).exists())
            recovery = Path(item['recovery'])
            self.assertTrue(recovery.is_relative_to(self.trash))
            self.assertEqual(hashlib.sha256(recovery.read_bytes()).hexdigest(), item['sha256'])
        for name, value in before.items():
            self.assertEqual((self.target / name).read_bytes(), value)

    def test_modified_copy_is_retained_without_partial_move(self):
        (self.target / 'README 2.md').write_text('user changed content')
        with self.assertRaises(RuntimeError):
            self.run_retirement()
        self.assertTrue((self.target / 'SKILL 2.md').is_file())
        self.assertEqual(list(self.trash.iterdir()), [])

    def test_unknown_extra_file_is_not_deleted(self):
        (self.target / 'user-note.md').write_text('user content')
        with self.assertRaises(RuntimeError):
            self.run_retirement()
        self.assertTrue((self.target / 'user-note.md').is_file())
        self.assertEqual(list(self.trash.iterdir()), [])

    def test_unrecorded_authority_is_rejected_before_move(self):
        outside = self.root.parent / 'candidate.json'
        outside.write_bytes(self.previous.read_bytes())
        with self.assertRaises(RuntimeError):
            self.run_retirement(previous=outside)
        self.assertTrue((self.target / 'manifest 2.json').is_file())
        self.assertEqual(list(self.trash.iterdir()), [])

    def test_symlink_authority_is_rejected(self):
        link = self.root / 'result/build-candidates/linked/candidate.json'
        link.parent.mkdir()
        link.symlink_to(self.previous)
        with self.assertRaises(RuntimeError):
            self.run_retirement(previous=link)
        self.assertEqual(list(self.trash.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
