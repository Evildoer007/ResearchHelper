"""Old archive receipts authorize only their matching metadata-only folder."""
import json
from pathlib import Path
import tempfile
import unittest
from maintenance.retire_release_metadata import archive_owners

ROOT = Path(__file__).resolve().parents[1]


class RetiredMetadataAuthorityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='retired-authority-', dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = self.root/'versions/app/v0.10.0'/('a'*64)
        self.folder.mkdir(parents=True)
        self.archive = self.folder/'ResearchHelper-0.10.0-arm64.dmg'
        self.meta = dict(filename=self.archive.name, sha256='a'*64, version='0.10.0',
                         status='retained_bytes', formal_release_verified=False)
        (self.folder/'artifact.json').write_text(json.dumps(self.meta))
        self.receipt = self.root/'result/maintenance/release-retirement/fixture/receipt.json'
        self.receipt.parent.mkdir(parents=True)
        self.record = dict(status='retired_recoverably',
            targets=[dict(path=str(self.archive), sha256='a'*64, category='superseded_archive')],
            moves=[dict(source=str(self.archive), moved=True)])

    def owners(self):
        self.receipt.write_text(json.dumps(self.record))
        return archive_owners(self.root)

    def test_matching_completed_retirement_is_an_authority(self):
        self.assertIn(str(self.folder), self.owners())

    def test_failed_or_unmoved_receipts_are_not_authority(self):
        self.record['moves'][0]['moved'] = False
        self.assertFalse(self.owners())

    def test_changed_metadata_is_not_authorized(self):
        self.meta['sha256'] = 'b'*64
        (self.folder/'artifact.json').write_text(json.dumps(self.meta))
        self.assertFalse(self.owners())

    def test_existing_archive_is_not_a_metadata_only_target(self):
        self.archive.write_bytes(b'offline fixture')
        self.assertFalse(self.owners())


if __name__ == '__main__':
    unittest.main()
