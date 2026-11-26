"""Storage boundaries only; real report contracts run in delivery integrity tests.

The large payload is synthetic. No financial approval, external call or old
production-store migration is inferred from these checks.
"""
import copy
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper import archive_codec as codec
from researchhelper.archive_codec import STORAGE_KEY
from researchhelper.reports import stage_pending_html


class DeliveryStorageCompactionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='delivery-storage-')
        self.addCleanup(folder.cleanup)
        self.archive = ResearchArchive(folder.name)
        self.content = dict(task_id='storage-only', research_version=1,
            input_hash='isolated-storage-fixture',
            evidence=[dict(snapshot={'text': '独立存储夹具，不是金融证据。' * 20000})] * 4)
        document = dict(schema='researchhelper.presentation-document', version=1, output_type='report',
                        profile='standard', content_hash=fingerprint(self.content), title='独立报告存储夹具')
        self.html = ('<!doctype html><html><head><title>独立报告存储夹具</title></head>'
                     '<body data-report-variant="standard"><main><h1>独立报告存储夹具</h1></main>'
                     '<script type="application/json" id="rh-publication-document">'
                     + json.dumps(document) + '</script></body></html>')
        self.task = dict(id='storage-only', version=1, revision=0, questions=[], gaps=[], status='失败',
            input_hash=self.content['input_hash'], pending_content=copy.deepcopy(self.content),
            evidence=copy.deepcopy(self.content['evidence']),
            ledger=dict(used=dict(model_calls=0, data_requests=0)))
        stage_pending_html(self.task, self.html)
        self.archive.save(self.task)
        for name, kwargs in [('check_content', dict(return_value=None)),
                             ('compose', dict(return_value=self.content))]:
            mock = patch('researchhelper.reports.' + name, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)

    def deliver(self):
        return self.archive.deliver(self.task, self.content, self.html)

    def test_success_releases_only_checkpoints_and_retains_complete_report(self):
        original = copy.deepcopy(self.task)
        done = self.deliver()
        self.assertNotIn('pending_content', done)
        self.assertNotIn('pending_html', done)
        self.assertEqual(done['evidence'], original['evidence'])
        self.assertEqual(done['ledger'], original['ledger'])
        self.assertEqual(self.task, original)
        restarted = ResearchArchive(self.archive.root)
        self.assertEqual(restarted.get(done['id']), done)
        report = restarted.report(done['id'], 1)
        self.assertEqual(report['content'], self.content)
        self.assertEqual(report['html'], self.html)
        self.assertEqual(report['digest'], fingerprint(self.content))

    def test_delivery_uses_existing_content_addressed_values(self):
        self.deliver()
        with self.archive.connect() as db:
            body = db.execute('SELECT content FROM deliveries').fetchone()[0]
            self.assertIn(STORAGE_KEY, json.loads(body))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM archive_values').fetchone()[0], 1)
        self.assertLess(len(body.encode()), len(canonical(self.content).encode()) / 100)

    def test_report_reuses_verified_values_within_one_read_and_rechecks_on_reopen(self):
        self.deliver()
        with patch.object(codec, '_get', wraps=codec._get) as read:
            first = self.archive.report(self.task['id'], 1)
            self.assertEqual(read.call_count, 1)
            first['content']['evidence'][0]['snapshot']['text'] = '改动只在当前读取结果'
            self.assertEqual(first['content']['evidence'][1]['snapshot'], self.content['evidence'][1]['snapshot'])
            self.assertEqual(self.archive.report(self.task['id'], 1)['content'], self.content)
            self.assertEqual(read.call_count, 2)
            with self.archive.connect() as db:
                db.execute('UPDATE archive_values SET compressed=?', (b'changed after preview',))
            with self.assertRaises(ContractError):
                self.archive.report(self.task['id'], 1)
            self.assertEqual(read.call_count, 3)

    def test_legacy_plain_delivery_remains_readable_without_rewrite(self):
        self.deliver()
        raw = canonical(self.content)
        with self.archive.connect() as db:
            db.execute('UPDATE deliveries SET content=?', (raw,))
        self.assertEqual(self.archive.report(self.task['id'], 1)['content'], self.content)
        with self.archive.connect() as db:
            self.assertEqual(db.execute('SELECT content FROM deliveries').fetchone()[0], raw)

    def test_idempotent_completed_task_checks_frozen_html(self):
        done = self.deliver()
        self.assertEqual(self.archive.deliver(done, self.content, self.html), done)
        with self.assertRaises(ContractError):
            self.archive.deliver(done, self.content, self.html + 'changed')
        with self.archive.connect() as db:
            db.execute('UPDATE deliveries SET html=?', (self.html + 'damaged',))
        with self.assertRaises(ContractError):
            self.archive.deliver(done, self.content, self.html)

    def test_transaction_failure_retains_recovery_checkpoint(self):
        before = self.archive.get(self.task['id'])
        with self.archive.connect() as db:
            db.execute("CREATE TRIGGER reject_freeze BEFORE INSERT ON versions BEGIN SELECT RAISE(ABORT,'storage failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.deliver()
        self.assertEqual(self.archive.get(self.task['id']), before)
        with self.archive.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM delivery_titles').fetchone()[0], 0)
        self.assertEqual(before['pending_content'], self.content)

    def test_corrupt_or_missing_compressed_value_never_returns_report(self):
        self.deliver()
        with self.archive.connect() as db:
            db.execute('UPDATE archive_values SET compressed=?', (b'invalid',))
        with self.assertRaises(ContractError):
            self.archive.report(self.task['id'], 1)
        with self.archive.connect() as db:
            db.execute('DELETE FROM archive_values')
        with self.assertRaises(ContractError):
            self.archive.report(self.task['id'], 1)

    def test_changed_delivery_metadata_fails_integrity(self):
        self.deliver()
        with self.archive.connect() as db:
            value = json.loads(db.execute('SELECT content FROM deliveries').fetchone()[0])
            value['input_hash'] = 'changed'
            db.execute('UPDATE deliveries SET content=?', (canonical(value),))
        with self.assertRaises(ContractError):
            self.archive.report(self.task['id'], 1)

    def test_malformed_json_in_delivery_or_version_is_a_controlled_error(self):
        self.deliver()
        with self.archive.connect() as db:
            original = db.execute('SELECT content FROM deliveries').fetchone()[0]
            db.execute('UPDATE deliveries SET content=?', ('{invalid',))
        with self.assertRaises(ContractError):
            self.archive.report(self.task['id'], 1)
        with self.archive.connect() as db:
            db.execute('UPDATE deliveries SET content=?', (original,))
            db.execute('UPDATE versions SET body=?', ('{invalid',))
        with self.assertRaises(ContractError):
            self.archive.report(self.task['id'], 1)


if __name__ == '__main__':
    unittest.main()
