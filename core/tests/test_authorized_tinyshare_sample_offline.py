"""Independent preflight tests. No real credentials or external dispatch."""
import tempfile
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from researchhelper.evidence import validate_source_request
from tests.review_authorized_tinyshare_sample import (
    PARAMETERS, file_hashes, run, validate_authorization)
from tests.review_authorized_tinyshare_sample import complete_unsent_preflight


class AuthorizedTinyshareSampleTests(unittest.TestCase):
    def test_requires_literal_authorization_before_files(self):
        for confirmed in (False, None, 1, 'yes'):
            with self.subTest(confirmed=confirmed), patch.object(Path, 'mkdir') as create:
                with self.assertRaises(ValueError):
                    run('PRIVATE_FIXTURE', confirmed=confirmed)
                create.assert_not_called()

    def test_empty_or_nonstring_credentials_rejected_before_files(self):
        for token in ('', ' ', None, 7, ' key '):
            with self.subTest(token=token), patch.object(Path, 'mkdir') as create:
                with self.assertRaises(ValueError):
                    run(token, confirmed=True)
                create.assert_not_called()

    def test_authorization_keeps_token_in_memory(self):
        self.assertIsNone(validate_authorization('PRIVATE_FIXTURE', True))

    def test_sample_is_one_explicit_registered_etf_request(self):
        task = {'objects': ['510300.SH'], 'cutoff': '2026-10-07T18:00:00+08:00'}
        self.assertEqual(validate_source_request(task, 'tinyshare', 'fund_daily', PARAMETERS), PARAMETERS)

    def test_production_guard_detects_new_and_changed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = file_hashes(root)
            (root / 'fixture').touch()
            self.assertNotEqual(before, file_hashes(root))

    def test_used_authorization_is_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'authorization-dispatch.json').touch()
            with patch('tests.review_authorized_tinyshare_sample.OUTPUT', root), \
                    patch('tests.review_authorized_tinyshare_sample.project_store') as store:
                with self.assertRaises(ValueError):
                    run('PRIVATE_FIXTURE', confirmed=True)
                store.assert_not_called()

    def test_continuation_requires_authorization_before_files(self):
        with patch.object(Path, 'read_text') as read:
            with self.assertRaises(ValueError):
                complete_unsent_preflight('PRIVATE_FIXTURE')
            read.assert_not_called()

    def test_original_deadline_cannot_be_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            started = datetime.now(timezone.utc) - timedelta(seconds=61)
            (root / 'authorization-dispatch.json').write_text(json.dumps({
                'project': str(root), 'started_at': started.isoformat()}))
            (root / 'receipt.json').write_text(json.dumps({'ledger': {'dispatches': []}}))
            with patch('tests.review_authorized_tinyshare_sample.OUTPUT', root), \
                    patch('researchhelper.archive.ResearchArchive') as archive:
                with self.assertRaises(ValueError):
                    complete_unsent_preflight('PRIVATE_FIXTURE', confirmed=True)
                archive.assert_not_called()

    def test_used_continuation_cannot_mutate_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'authorization-dispatch.json').write_text(json.dumps({'project': str(root)}))
            (root / 'unsent-preflight-dispatch.json').touch()
            with patch('tests.review_authorized_tinyshare_sample.OUTPUT', root), \
                    patch('researchhelper.archive.ResearchArchive') as archive:
                with self.assertRaises(ValueError):
                    complete_unsent_preflight('PRIVATE_FIXTURE', confirmed=True)
                archive.assert_not_called()
