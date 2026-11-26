"""Independent frozen-delivery checks; isolated archive, zero external calls."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
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

    def test_archive_refuses_to_freeze_a_page_changed_after_rendering(self):
        self.task['pending_html'] += '<!-- storage fault -->'
        self.archive.save(self.task)
        with self.assertRaisesRegex(ContractError, '同版呈现检查点'):
            self.deliver(paper=self.task['pending_html'])
        self.assertEqual(self.archive.delivery_versions(self.task['id']), [])

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
        clock = [100.0]

        def failed_save(*args, **kwargs):
            clock[0] += 5
            raise OSError('再次保存失败')

        with patch('researchhelper.engine.time', SimpleNamespace(monotonic=lambda: clock[0])), \
                patch.object(self.archive, 'deliver', side_effect=failed_save):
            with self.assertRaises(OSError):
                self.agent.retry_save(self.task['id'])
        current = self.archive.get(self.task['id'])
        self.assertAlmostEqual(current['ledger']['used']['seconds'], before + 5)
        self.assertEqual(current['pending_html'], self.paper)

    def test_failed_first_save_counts_render_and_transaction_wait_once(self):
        from researchhelper import reports

        task = self.agent.create_case('etf')
        clock = [100.0]
        render = reports.render

        def render_after_wait(*args, **kwargs):
            clock[0] += 2
            return render(*args, **kwargs)

        def failed_save(*args, **kwargs):
            clock[0] += 5
            raise OSError('归档提交失败')

        with patch('researchhelper.engine.time', SimpleNamespace(monotonic=lambda: clock[0])), \
                patch.object(reports, 'render', side_effect=render_after_wait), \
                patch.object(self.archive, 'deliver', side_effect=failed_save):
            failed = self.agent.start(task['id'])
        self.assertEqual(failed['status'], '失败')
        self.assertTrue(failed['pending_html'])
        self.assertAlmostEqual(failed['ledger']['used']['seconds'], 7)

    def test_successful_save_retry_freezes_elapsed_seconds_with_the_same_report(self):
        clock = [100.0]
        deliver = self.archive.deliver
        before = self.task['ledger']['used']['seconds']

        def save_after_wait(*args, **kwargs):
            clock[0] += 5
            return deliver(*args, **kwargs)

        with patch('researchhelper.engine.time', SimpleNamespace(monotonic=lambda: clock[0])), \
                patch.object(self.archive, 'deliver', side_effect=save_after_wait):
            saved = self.agent.retry_save(self.task['id'])
        frozen = self.archive.get(self.task['id'], self.task['version'])
        self.assertAlmostEqual(frozen['ledger']['used']['seconds'], before + 5)
        self.assertEqual(saved['ledger'], frozen['ledger'])
        self.assertEqual(self.report()['html'], self.paper)

    def test_save_timeout_keeps_the_checkpoint_without_a_partial_frozen_report(self):
        task = self.archive.get(self.task['id'])
        used = task['ledger']['used']['seconds']
        task['ledger']['limits']['seconds'] = used + 2
        self.archive.save(task)
        clock = [100.0]
        deliver = self.archive.deliver

        def save_after_timeout(*args, **kwargs):
            clock[0] += 5
            return deliver(*args, **kwargs)

        with patch('researchhelper.engine.time', SimpleNamespace(monotonic=lambda: clock[0])), \
                patch.object(self.archive, 'deliver', side_effect=save_after_timeout):
            with self.assertRaisesRegex(ContractError, '执行时间.*正文已保留') as error:
                self.agent.retry_save(task['id'])
        self.assertEqual(getattr(error.exception, 'code', None), 'execution_time_exhausted')
        current = self.archive.get(task['id'])
        self.assertAlmostEqual(current['ledger']['used']['seconds'], used + 5)
        self.assertEqual(current['pending_html'], self.paper)
        self.assertFalse(current['delivery'])
        self.assertEqual(self.archive.delivery_versions(task['id']), [])
        self.assertEqual(current['error'], str(error.exception))

    def test_exhausted_save_recovery_leaves_the_original_checkpoint_untouched(self):
        task = self.archive.get(self.task['id'])
        task['ledger']['used']['seconds'] = task['ledger']['limits']['seconds']
        self.archive.save(task)
        before = self.archive.get(task['id'])
        with patch('researchhelper.reports.compose', side_effect=AssertionError('No work can be started')):
            for action in (self.agent.retry_save, self.agent.start):
                with self.subTest(action=action.__name__):
                    with self.assertRaisesRegex(ContractError, '执行时间.*正文已保留'):
                        action(task['id'])
        self.assertEqual(self.archive.get(task['id']), before)

    def test_unspent_delivery_reserve_still_allows_local_save(self):
        task = self.archive.get(self.task['id'])
        for kind in ('model_calls', 'data_requests'):
            task['ledger']['used'][kind] = task['ledger']['limits'][kind]
        task['ledger']['used']['seconds'] = task['ledger']['limits']['seconds'] - 10
        self.archive.save(task)
        done = self.agent.retry_save(task['id'])
        self.assertEqual(self.report()['html'], self.paper)
        self.assertEqual(done['ledger']['dispatches'], task['ledger']['dispatches'])
        for kind in ('model_calls', 'data_requests'):
            self.assertEqual(done['ledger']['used'][kind], task['ledger']['used'][kind])

    def test_unsaved_task_changes_cannot_freeze(self):
        self.task['question'] += '未保存变更'
        with self.assertRaises(ContractError):
            self.deliver()

    def test_idempotent_retry_rejects_damaged_existing_html(self):
        done = self.deliver()
        self.corrupt('html', self.paper + '<!--changed-->')
        with self.assertRaises(ContractError):
            self.archive.deliver(done, self.content, self.paper)
