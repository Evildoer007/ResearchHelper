"""Export compression never needs permission to remove an occupied database."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from maintenance.compact_acceptance import compact_closed_exports


class ClosedExportCompactionTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.folder=self.root/'result/acceptance/autonomous-data-phase-fixture'
        self.folder.mkdir(parents=True)
        self.task=json.dumps(dict(id='closed_fixture',status='已取消',text='原始材料。'*20000)).encode()
        (self.folder/'task.json').write_bytes(self.task)
        self.receipt=dict(task_id='closed_fixture',status='data_phase_incomplete',owned_runtime_closed=True,
            production_unchanged=True,source_unchanged=False)
        (self.folder/'receipt.json').write_text(json.dumps(self.receipt))
        (self.folder/'store').mkdir();(self.folder/'store/researchhelper.sqlite3').write_bytes(b'untouched database')

    def check(self,returncode=1,stdout=b''):
        return patch('maintenance.compact_acceptance.subprocess.run',return_value=type('Probe',(),
            dict(returncode=returncode,stdout=stdout))())

    def test_failed_freeze_stays_failed_and_original_export_is_exactly_recoverable(self):
        receipt=(self.folder/'receipt.json').read_bytes()
        with self.check() as probe:result=compact_closed_exports(self.folder,root=self.root)
        self.assertEqual(gzip.decompress((self.folder/'task.json.gz').read_bytes()),self.task)
        self.assertFalse((self.folder/'task.json').exists())
        self.assertEqual((self.folder/'receipt.json').read_bytes(),receipt)
        self.assertEqual((self.folder/'store/researchhelper.sqlite3').read_bytes(),b'untouched database')
        self.assertFalse(result['store_touched']);self.assertGreater(result['saved_bytes'],0)
        self.assertTrue(all('store' not in call.args[0][-1] for call in probe.call_args_list))

    def test_open_export_is_not_removed_or_compressed(self):
        with self.check(0,b'open file'):
            with self.assertRaises(ValueError):compact_closed_exports(self.folder,root=self.root)
        self.assertEqual((self.folder/'task.json').read_bytes(),self.task)
        self.assertFalse((self.folder/'task.json.gz').exists())

    def test_running_task_is_not_accepted_even_with_an_old_closed_receipt(self):
        (self.folder/'task.json').write_text(json.dumps(dict(id='closed_fixture',status='运行中')))
        with self.check(),self.assertRaises(ValueError):compact_closed_exports(self.folder,root=self.root)

    def test_javascript_fixture_keeps_export_at_its_original_path(self):
        tests = self.root / 'tests'
        tests.mkdir()
        (tests / 'replay.mjs').write_text(f"const fixture = 'result/acceptance/{self.folder.name}/task.json';")
        with self.assertRaisesRegex(ValueError, '仍被回归引用'):
            compact_closed_exports(self.folder, root=self.root)
        self.assertEqual((self.folder / 'task.json').read_bytes(), self.task)
        self.assertFalse((self.folder / 'task.json.gz').exists())

    def test_unknown_directory_and_existing_archive_are_not_overwritten(self):
        (self.folder/'task.json.gz').write_bytes(b'existing')
        with self.check(),self.assertRaises(ValueError):compact_closed_exports(self.folder,root=self.root)
        self.assertEqual((self.folder/'task.json.gz').read_bytes(),b'existing')
        with self.assertRaises(ValueError):compact_closed_exports(self.root,root=self.root)
