"""Independent closed-data-test cleanup, synthetic bytes only."""
import json
import tempfile
from pathlib import Path
import unittest
import zipfile

from maintenance.compact_acceptance import compact_data_phase


class DataPhaseCompactionTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.folder=self.root/'result/acceptance/autonomous-data-phase-synthetic'
        (self.folder/'store').mkdir(parents=True)
        (self.folder/'store/researchhelper.sqlite3').write_bytes(b'SYNTHETIC_DB'*1000)
        (self.folder/'task.json').write_text(json.dumps(dict(id='synthetic',status='已取消',gaps=['合成失败不能改成功'])))
        self.receipt=dict(scope='autonomous_source_kernel_data_phase',status='data_phase_incomplete',
            owned_runtime_closed=True,production_unchanged=True,source_unchanged=True,
            supplied_plan=False,supplied_evidence=False,task_id='synthetic',task_status='已取消')
        self.save()

    def save(self):(self.folder/'receipt.json').write_text(json.dumps(self.receipt))

    def test_lossless_archive_preserves_original_failure(self):
        before=(self.folder/'task.json').read_bytes();receipt=(self.folder/'receipt.json').read_bytes()
        result=compact_data_phase(self.folder,root=self.root)
        with zipfile.ZipFile(self.folder/'raw-qa.zip') as package:self.assertEqual(package.read('task.json'),before)
        self.assertEqual((self.folder/'receipt.json').read_bytes(),receipt)
        self.assertFalse((self.folder/'store').exists());self.assertFalse((self.folder/'task.json').exists())
        self.assertFalse(result['financial_approval']);self.assertEqual(result['task_status'],'已取消')

    def test_live_unrecovered_or_foreign_scope_is_not_removed(self):
        self.receipt['owned_runtime_closed']=False;self.save()
        with self.assertRaises(ValueError):compact_data_phase(self.folder,root=self.root)
        self.assertTrue((self.folder/'store').is_dir())
        with self.assertRaises(ValueError):compact_data_phase(self.root,root=self.root)

    def test_secrets_and_existing_archives_are_preserved(self):
        (self.folder/'store/private.secret').write_bytes(b'SYNTHETIC_SECRET')
        with self.assertRaises(ValueError):compact_data_phase(self.folder,root=self.root)
        self.assertTrue((self.folder/'task.json').is_file())
