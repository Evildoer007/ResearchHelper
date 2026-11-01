"""Independent frozen-delivery checks; isolated archive, zero external calls."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent

ROOT = Path(__file__).resolve().parents[2]


class DeliveryIntegrityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='delivery-integrity-', dir=ROOT / 'result/acceptance')
        self.addCleanup(folder.cleanup)
        blocker = patch.object(socket.socket, 'connect', side_effect=AssertionError('禁止联网'))
        blocker.start()
        self.addCleanup(blocker.stop)
        self.archive = ResearchArchive(folder.name)
        self.agent = ResearchAgent(self.archive, ROOT, offline=True)
        task = self.agent.create_case('etf')
        with patch.object(self.archive, 'deliver', side_effect=OSError('模拟保存故障')):
            self.task = self.agent.start(task['id'])
        self.assertEqual(self.task['status'], '失败')
        self.content = copy.deepcopy(self.task['pending_content'])
        self.paper = self.task['pending_html']

    def deliver(self, content=None, paper=None):
        return self.archive.deliver(self.task, self.content if content is None else content,
                                    self.paper if paper is None else paper)

    def report(self):
        return self.archive.report(self.task['id'], 1)

    def corrupt(self, field, value):
        with self.archive.connect() as database:
            database.execute(f'UPDATE deliveries SET {field}=? WHERE task_id=? AND version=1',
                             (value, self.task['id']))

    def test_foreign_content_cannot_be_frozen(self):
        other = copy.deepcopy(self.content)
        other['task_id'] = 'rh_foreign'
        other['content_hash'] = fingerprint({k: v for k, v in other.items() if k != 'content_hash'})
        with self.assertRaises(ContractError):
            self.deliver(content=other)

    def test_rehashed_changed_answer_cannot_be_frozen(self):
        other = copy.deepcopy(self.content)
        other['questions'][0]['answer'] += '未经核验的新结论'
        other['content_hash'] = fingerprint({k: v for k, v in other.items() if k != 'content_hash'})
        with self.assertRaises(ContractError):
            self.deliver(content=other)

    def test_unrendered_html_cannot_replace_checkpoint(self):
        with self.assertRaises(ContractError):
            self.deliver(paper='<html><body>另一份报告</body></html>')

    def test_empty_html_cannot_be_saved(self):
        with self.assertRaises(ContractError):
            self.deliver(paper='')

    def test_new_report_records_and_checks_html_hash(self):
        done = self.deliver()
        self.assertEqual(done['delivery']['html_sha256'], hashlib.sha256(self.paper.encode()).hexdigest())
        self.assertTrue(self.report()['html_integrity_verified'])

    def test_changed_saved_html_is_rejected(self):
        self.deliver()
        self.corrupt('html', self.paper + '<!--unexpected modification-->')
        with self.assertRaises(ContractError):
            self.report()

    def test_changed_saved_content_is_rejected(self):
        self.deliver()
        changed = copy.deepcopy(self.content)
        changed['title'] += '变更'
        self.corrupt('content', json.dumps(changed))
        with self.assertRaises(ContractError):
            self.report()

    def test_same_version_rehashed_content_cannot_borrow_old_metadata(self):
        self.deliver()
        changed = copy.deepcopy(self.content)
        changed['title'] += '变更'
        changed['content_hash'] = fingerprint({k: v for k, v in changed.items() if k != 'content_hash'})
        self.corrupt('content', json.dumps(changed))
        self.corrupt('digest', fingerprint(changed))
        with self.assertRaises(ContractError):
            self.report()

    def test_legacy_report_is_readable_without_claiming_html_verification(self):
        self.deliver()
        with self.archive.connect() as database:
            row = database.execute('SELECT body FROM versions WHERE task_id=? AND version=1',
                                   (self.task['id'],)).fetchone()
            saved = json.loads(row[0])
            saved['delivery'].pop('html_sha256')
            database.execute('UPDATE versions SET body=? WHERE task_id=? AND version=1',
                             (json.dumps(saved), self.task['id']))
        report = self.report()
        self.assertEqual(report['html'], self.paper)
        self.assertFalse(report['html_integrity_verified'])

    def test_idempotent_save_preserves_original_freeze(self):
        done = self.deliver()
        self.assertEqual(self.archive.deliver(done, self.content, self.paper), done)

    def test_retry_save_never_repeats_research(self):
        before = copy.deepcopy(self.task['ledger']['used'])
        done = self.agent.retry_save(self.task['id'])
        self.assertEqual(done['status'], '已完成')
        for kind in ('data_requests', 'model_calls'):
            self.assertEqual(done['ledger']['used'][kind], before[kind])
        self.assertTrue(self.report()['html_integrity_verified'])

    def test_failed_save_retry_keeps_accounting_durable(self):
        before = self.task['ledger']['used']['seconds']
        with patch.object(self.archive, 'deliver', side_effect=OSError('再次保存失败')):
            with self.assertRaises(OSError):
                self.agent.retry_save(self.task['id'])
        current = self.archive.get(self.task['id'])
        self.assertAlmostEqual(current['ledger']['used']['seconds'], before + .001)

    def test_unsaved_task_changes_cannot_freeze(self):
        self.task['question'] += '未保存变更'
        with self.assertRaises(ContractError):
            self.deliver()

    def test_idempotent_retry_rejects_damaged_existing_html(self):
        done = self.deliver()
        self.corrupt('html', self.paper + '<!--changed-->')
        with self.assertRaises(ContractError):
            self.archive.deliver(done, self.content, self.paper)
