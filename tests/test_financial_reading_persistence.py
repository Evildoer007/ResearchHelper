"""Verify the actual R124 read checkpoint across JSON storage, without resume."""
import copy
import json
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.source_reader import read, validate_reading

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('RESEARCHHELPER_READING_PERSISTENCE_INPUT', str(
    ROOT/'result/acceptance/one-sentence-research-grwwag19/task.json')))


class FinancialReadingPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = SOURCE.read_bytes(); cls.original = json.loads(cls.raw)

    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No real requests'))
        guard.start(); self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)
        self.task = copy.deepcopy(self.original)
        self.bundle = self.task['reasoning_state']['reading']['bundle']

    def tearDown(self): self.assertEqual(SOURCE.read_bytes(), self.raw)

    def test_actual_saved_reading_revalidates_without_changing_the_failed_study(self):
        before = fingerprint(self.task)
        validate_reading(self.task, self.bundle)
        self.assertEqual(fingerprint(self.task), before)
        self.assertEqual(self.task['status'], '失败')

    def test_fresh_read_is_json_native_before_and_after_storage(self):
        new = read(self.task, self.bundle['selection'], self.bundle['binding'], self.bundle['artifact_ids'], reader_version=7)
        stored = json.loads(json.dumps(new, ensure_ascii=False))
        self.assertEqual(new, stored)
        self.assertEqual(new, self.bundle)
        validate_reading(self.task, stored)

    def test_changed_span_or_hash_remains_rejected(self):
        for issue in ('span', 'hash'):
            bad = copy.deepcopy(self.bundle)
            if issue == 'hash': bad['reading_hash'] = 'changed'
            else:
                spans = next(iter(bad['coverage'][0]['identity_context_reading']['groups'][0]['spans'].values()))
                spans[0][1] += 1
            with self.subTest(issue=issue), self.assertRaises(ContractError):
                validate_reading(self.task, bad)


if __name__ == '__main__': unittest.main()
