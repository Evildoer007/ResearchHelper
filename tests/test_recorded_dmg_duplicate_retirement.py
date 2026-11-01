"""Use only newly generated fake artifacts; never touch the actual Trash."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from maintenance import retire_duplicate_skill_archive as retirement


class RecordedDmgDuplicateRetirementTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='rh-dmg-retirement-fixture-')
        self.addCleanup(folder.cleanup)
        self.root=Path(folder.name).resolve()
        self.trash=self.root/'test-trash';self.trash.mkdir()
        (self.root/'dist').mkdir()
        self.old_bytes=b'old recorded fake DMG';self.new_bytes=b'current recorded fake DMG'
        self.duplicate=self.root/'dist/ResearchHelper-0.10.0-arm64 2.dmg'
        self.live=self.root/'dist/ResearchHelper-0.10.0-arm64.dmg'
        self.duplicate.write_bytes(self.old_bytes);self.live.write_bytes(self.new_bytes)
        self.old=self.candidate('old',self.old_bytes)
        self.current=self.candidate('current',self.new_bytes)
        guard=patch.object(retirement,'ROOT',self.root);guard.start();self.addCleanup(guard.stop)

    def candidate(self,name,content):
        folder=self.root/'result/build-candidates'/name;folder.mkdir(parents=True)
        release=folder/'release-manifest.json';release.write_text('{}')
        record={'schema':'researchhelper.current-build.v1','platform':'macos','status':'built_local_candidate',
            'manifest_sha256':hashlib.sha256(release.read_bytes()).hexdigest(),
            'artifacts':{'dmg':{'sha256':hashlib.sha256(content).hexdigest(),'bytes':len(content)}}}
        path=folder/'candidate.json';path.write_text(json.dumps(record));return path

    def run_retirement(self):
        return retirement.retire_recorded_dmg_duplicate(previous_candidate=self.old,
            current_candidate=self.current,trash=self.trash)

    def test_exact_previous_duplicate_moves_recoverably_not_current(self):
        receipt=self.run_retirement()
        self.assertEqual(receipt['status'],'retired_recoverably')
        self.assertFalse(self.duplicate.exists())
        self.assertEqual(Path(receipt['recovery']).read_bytes(),self.old_bytes)
        self.assertEqual(self.live.read_bytes(),self.new_bytes)
        self.assertFalse(json.loads(Path(receipt['receipt']).read_text())['permanent_deletion'])

    def test_unknown_bytes_preserved(self):
        self.duplicate.write_bytes(b'unknown user file')
        with self.assertRaises(RuntimeError):self.run_retirement()
        self.assertEqual(self.duplicate.read_bytes(),b'unknown user file')

    def test_changed_current_artifact_preserves_duplicate(self):
        self.live.write_bytes(b'changed current')
        with self.assertRaises(RuntimeError):self.run_retirement()
        self.assertEqual(self.duplicate.read_bytes(),self.old_bytes)

    def test_source_symlink_never_followed_or_moved(self):
        self.duplicate.unlink();self.duplicate.symlink_to(self.live)
        with self.assertRaises(RuntimeError):self.run_retirement()
        self.assertTrue(self.duplicate.is_symlink())
        self.assertEqual(self.live.read_bytes(),self.new_bytes)

    def test_unbound_release_preserved(self):
        self.old.with_name('release-manifest.json').write_text('{"changed":true}')
        with self.assertRaises(RuntimeError):self.run_retirement()
        self.assertTrue(self.duplicate.exists())

    def test_current_bytes_not_called_superseded(self):
        self.old.write_text(self.current.read_text())
        self.duplicate.write_bytes(self.new_bytes)
        with self.assertRaises(RuntimeError):self.run_retirement()
        self.assertTrue(self.duplicate.exists())


if __name__=='__main__':
    unittest.main()
