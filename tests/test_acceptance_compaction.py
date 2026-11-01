"""Lossless cleanup of synthetic closed QA data, never finance acceptance."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from maintenance.compact_acceptance import compact_case


class AcceptanceCompactionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='rh-compaction-tests-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.case = self.root/'result/acceptance/one-sentence-research-synthetic'
        (self.case/'store/credentials').mkdir(parents=True)
        self.task = dict(id='synthetic', status='部分完成', text='原件合成夹具'*10000)
        self.raw = json.dumps(self.task, ensure_ascii=False).encode()
        (self.case/'task.json').write_bytes(self.raw)
        (self.case/'store/researchhelper.sqlite3').write_bytes(b'SYNTHETIC_STORE'*10000)
        self.receipt = dict(status='collected_for_independent_review', task_id='synthetic',
            research_status='部分完成', service_stopped=True, browser_stopped=True,
            mount_detached=True, temporary_credentials_cleared=True,
            temporary_execution_policy_disabled=True, production_unchanged=True,
            browser=dict(checks=['temporary Store is empty']))
        self.save_receipt()
        (self.case/'report.html').write_text('<h1>明确标记的合成报告</h1>')
        (self.case/'report.json').write_bytes(b'{"synthetic":true,"original":"' + b'FIXTURE'*10000 + b'"}')
        (self.case/'reschat.png').write_bytes(b'SYNTHETIC_SCREEN')

    def save_receipt(self):
        (self.case/'receipt.json').write_text(json.dumps(self.receipt))

    def test_closed_workspace_compacts_exact_bytes_and_preserves_partial_status(self):
        report = (self.case/'report.html').read_bytes()
        receipt = (self.case/'receipt.json').read_bytes()
        report_package = (self.case/'report.json').read_bytes()
        result = compact_case(self.case, root=self.root)
        with gzip.open(self.case/'task.json.gz', 'rb') as stream:
            self.assertEqual(stream.read(), self.raw)
        with zipfile.ZipFile(self.case/'test-store.zip') as package:
            self.assertEqual(package.read('researchhelper.sqlite3'), b'SYNTHETIC_STORE'*10000)
        self.assertFalse((self.case/'store').exists())
        self.assertFalse((self.case/'task.json').exists())
        self.assertFalse((self.case/'reschat.png').exists())
        self.assertFalse((self.case/'report.json').exists())
        with gzip.open(self.case/'report.json.gz', 'rb') as stream:
            self.assertEqual(stream.read(), report_package)
        self.assertEqual((self.case/'report.html').read_bytes(), report)
        self.assertEqual((self.case/'receipt.json').read_bytes(), receipt)
        self.assertEqual(result['research_status'], '部分完成')
        self.assertFalse(result['financial_quality_accepted'])
        self.assertGreater(result['saved_bytes'], 0)

    def test_running_or_unrecovered_workspace_is_never_removed(self):
        for key in ('service_stopped','browser_stopped','mount_detached','temporary_credentials_cleared',
                    'temporary_execution_policy_disabled','production_unchanged'):
            with self.subTest(key=key):
                self.receipt[key] = False
                self.save_receipt()
                with self.assertRaises(ValueError):
                    compact_case(self.case, root=self.root)
                self.assertTrue((self.case/'task.json').exists())
                self.receipt[key] = True

    def test_active_status_wrong_task_and_secret_refuse_cleanup(self):
        self.receipt['research_status'] = '运行中'
        self.save_receipt()
        with self.assertRaises(ValueError): compact_case(self.case, root=self.root)
        self.receipt['research_status'] = '部分完成'
        self.receipt['task_id'] = 'foreign'
        self.save_receipt()
        with self.assertRaises(ValueError): compact_case(self.case, root=self.root)
        self.receipt['task_id'] = 'synthetic'
        self.save_receipt()
        (self.case/'store/credentials/credential.secret').write_bytes(b'FAKE_TEST_ONLY')
        with self.assertRaises(ValueError): compact_case(self.case, root=self.root)

    def test_foreign_scope_symlink_and_existing_archive_are_not_overwritten(self):
        with self.assertRaises(ValueError): compact_case(self.root, root=self.root)
        (self.case/'outside-link').symlink_to(self.root)
        with self.assertRaises(ValueError): compact_case(self.case, root=self.root)
        (self.case/'outside-link').unlink()
        (self.case/'task.json.gz').write_bytes(b'EXISTING')
        with self.assertRaises(ValueError): compact_case(self.case, root=self.root)
        self.assertEqual((self.case/'task.json.gz').read_bytes(), b'EXISTING')

    def test_open_store_handle_blocks_cleanup(self):
        from types import SimpleNamespace
        with patch('maintenance.compact_acceptance.subprocess.run',
                return_value=SimpleNamespace(returncode=0, stdout=b'live database handle')):
            with self.assertRaisesRegex(ValueError, '仍被进程使用'):
                compact_case(self.case, root=self.root)
        self.assertTrue((self.case/'task.json').is_file())
        self.assertFalse((self.case/'task.json.gz').exists())


class MonitorFixtureLifecycleTests(unittest.TestCase):
    def test_generated_monitor_is_removed_when_context_finishes(self):
        from tests import ROOT
        from tests.workspace_monitor_fixture import workspace_monitor
        with tempfile.TemporaryDirectory(prefix='monitor-cleanup-', dir=ROOT/'result/acceptance') as private:
            with workspace_monitor(private) as record:
                self.assertTrue(Path(record['fixture']).is_file())
            self.assertTrue(record['fixture_removed'])
            self.assertFalse(Path(record['fixture']).exists())

    def test_error_still_removes_owned_monitor_but_modified_files_are_preserved(self):
        from tests import ROOT
        from tests.workspace_monitor_fixture import workspace_monitor
        with tempfile.TemporaryDirectory(prefix='monitor-cleanup-', dir=ROOT/'result/acceptance') as private:
            with self.assertRaises(ValueError):
                with workspace_monitor(private) as record:
                    raise ValueError('synthetic observation failure')
            self.assertTrue(record['fixture_removed'])
            with self.assertRaisesRegex(RuntimeError, '保留文件'):
                with workspace_monitor(private) as changed:
                    Path(changed['fixture']).write_text('deliberate synthetic modification')
            self.assertTrue(Path(changed['fixture']).is_file())
