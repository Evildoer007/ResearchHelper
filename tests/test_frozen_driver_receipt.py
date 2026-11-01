"""Receipt normalization cannot turn a missing or wrong run into acceptance."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import accept_argument_support_runtime as driver


class FrozenDriverReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='frozen-return-', dir=driver.ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.candidate = self.root/'candidate.json'
        self.path = self.root/'result/acceptance/packaged-filing-filters-fixture/receipt.json'
        self.path.parent.mkdir(parents=True)
        self.record = dict(status='scoped_passed', candidate=str(self.candidate),
            app=dict(status='passed', tests_run=driver.TEST_COUNT), service_stopped=True, mount_detached=True)
        scope = patch.object(driver, 'ROOT', self.root)
        scope.start(); self.addCleanup(scope.stop)

    def save(self): self.path.write_text(json.dumps(self.record))

    def test_only_same_candidate_complete_and_stopped_receipt_is_accepted(self):
        self.save()
        result, path = driver.completed_frozen_receipt(set(), self.candidate)
        self.assertEqual(result, self.record); self.assertEqual(path, self.path)

    def test_old_or_missing_receipt_does_not_count_as_new_run(self):
        with self.assertRaises(RuntimeError): driver.completed_frozen_receipt(set(), self.candidate)
        self.save()
        with self.assertRaises(RuntimeError): driver.completed_frozen_receipt({self.path}, self.candidate)

    def test_wrong_candidate_count_failed_stage_or_open_mount_is_rejected(self):
        for field,value in (('candidate','foreign'), ('status','failed'), ('service_stopped',False),
                            ('mount_detached',False), ('app',dict(status='passed', tests_run=1))):
            saved = dict(self.record); self.record[field] = value; self.save()
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                driver.completed_frozen_receipt(set(), self.candidate)
            self.record = saved

    def test_two_new_receipts_are_ambiguous_and_cannot_be_selected_by_recency(self):
        self.save()
        second = self.path.parent.with_name('packaged-filing-filters-other')/'receipt.json'
        second.parent.mkdir(); second.write_text(json.dumps(self.record))
        with self.assertRaises(RuntimeError): driver.completed_frozen_receipt(set(), self.candidate)


if __name__=='__main__': unittest.main()
