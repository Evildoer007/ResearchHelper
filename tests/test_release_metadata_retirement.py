"""Metadata-only classification cannot retire edited or live deliverables."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from maintenance.retire_release_metadata import metadata_only, retire, digest

ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='metadata-policy-', dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)/'project'
        self.folder = self.project/'versions/app/v0.10.0'/('a'*64)
        self.folder.mkdir(parents=True)
        self.recovery = Path(temporary.name)/'recovery'
        self.recovery.mkdir()
        self.owner = dict(filename='ResearchHelper-0.10.0-arm64.dmg', sha256='a'*64, version='0.10.0')
        self.value = dict(self.owner, status='retained_bytes', formal_release_verified=False)
        (self.folder/'artifact.json').write_text(json.dumps(self.value))

    def test_only_known_metadata_is_eligible(self):
        self.assertTrue(metadata_only(self.folder, self.owner))

    def test_archive_or_unknown_file_is_never_a_metadata_only_folder(self):
        (self.folder/'ResearchHelper-0.10.0-arm64.dmg').write_bytes(b'fixture')
        self.assertFalse(metadata_only(self.folder, self.owner))

    def test_changed_metadata_is_preserved(self):
        self.value['formal_release_verified'] = True
        (self.folder/'artifact.json').write_text(json.dumps(self.value))
        self.assertFalse(metadata_only(self.folder, self.owner))

    def test_linked_metadata_is_preserved(self):
        file = self.folder/'artifact.json'
        file.rename(self.folder.parent/'retained.json')
        file.symlink_to(self.folder.parent/'retained.json')
        self.assertFalse(metadata_only(self.folder, self.owner))

    def test_recovery_move_has_a_saved_receipt_before_and_after_transfer(self):
        stamp = self.folder.stat()
        plan = dict(root=str(self.project), candidate=str(self.project/'candidate.json'), skipped=[],
            targets=[dict(source=str(self.folder), inode=stamp.st_ino, modified_ns=stamp.st_mtime_ns,
                          metadata_sha256=digest(self.folder/'artifact.json'), authority='offline_fixture')])
        with patch('maintenance.retire_release_metadata.inventory', return_value=plan), \
                patch('maintenance.retire_release_metadata.verify_current_build'):
            result = retire(plan['candidate'], self.project, self.recovery)
        record = json.loads(Path(result['receipt']).read_text())
        self.assertEqual(record['status'], 'retired_recoverably')
        self.assertFalse(record['permanent_deletion'])
        self.assertEqual(len(record['moves']), 1)
        self.assertFalse(self.folder.exists())
        self.assertTrue((Path(record['moves'][0]['destination'])/'artifact.json').is_file())


if __name__ == '__main__':
    unittest.main()
