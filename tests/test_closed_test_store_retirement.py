"""Separate cleanup controls; only synthetic temporary directories are moved."""
import json
from pathlib import Path
import tempfile
import unittest

from maintenance.retire_closed_test_stores import FLAGS, inventory, apply_inventory


class ClosedTestStoreRetirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='rh-cleanup-control-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.recovery = self.root/'trash'; self.recovery.mkdir()
        self.name = 'one-sentence-research-synthetic'
        self.case = self.root/'result/acceptance'/self.name
        self.store = self.case/'store'; self.store.mkdir(parents=True)
        (self.store/'researchhelper.sqlite3').write_bytes(b'synthetic closed store, not a real database')
        record = dict(status='collected_for_independent_review', research_status='失败', task_id='synthetic',
            browser=dict(checks=['temporary Store is empty']), **dict.fromkeys(FLAGS, True))
        (self.case/'receipt.json').write_text(json.dumps(record))
        (self.case/'task.json').write_text(json.dumps(dict(id='synthetic', status='失败')))
        (self.case/'report.html').write_text('<p>Original report control</p>')

    def test_only_explicit_closed_store_moves_and_exports_remain(self):
        before = {p.name:p.read_bytes() for p in self.case.iterdir() if p.is_file()}
        plan = inventory([self.name], root=self.root)
        path = apply_inventory(plan, root=self.root, recovery=self.recovery)
        result = json.loads(path.read_text())
        self.assertEqual(result['status'], 'retired_recoverably')
        self.assertFalse(self.store.exists())
        self.assertEqual({p.name:p.read_bytes() for p in self.case.iterdir() if p.is_file()}, before)
        self.assertEqual((Path(result['moved'][0]['recovery'])/'researchhelper.sqlite3').read_bytes(),
            b'synthetic closed store, not a real database')

    def test_source_reference_blocks_retirement(self):
        tests = self.root/'tests'; tests.mkdir()
        (tests/'fixture.py').write_text('case = '+repr(self.name))
        with self.assertRaises(ValueError): inventory([self.name], root=self.root)
        self.assertTrue(self.store.is_dir())

    def test_unclosed_runtime_or_missing_export_blocks_retirement(self):
        path = self.case/'receipt.json'; original = json.loads(path.read_text())
        for flag in FLAGS:
            path.write_text(json.dumps(dict(original, **{flag:False})))
            with self.subTest(flag=flag), self.assertRaises(ValueError): inventory([self.name], root=self.root)
        path.write_text(json.dumps(original)); (self.case/'task.json').unlink()
        with self.assertRaises(ValueError): inventory([self.name], root=self.root)

    def test_credentials_links_and_stale_inventory_are_not_moved(self):
        secret = self.store/'temporary.secret'; secret.write_bytes(b'synthetic')
        with self.assertRaises(RuntimeError): inventory([self.name], root=self.root)
        secret.unlink()
        plan = inventory([self.name], root=self.root)
        (self.store/'researchhelper.sqlite3').write_bytes(b'changed')
        with self.assertRaises(ValueError): apply_inventory(plan, root=self.root, recovery=self.recovery)
        self.assertTrue(self.store.is_dir())

    def test_live_file_handle_blocks_inventory(self):
        with (self.store/'researchhelper.sqlite3').open('rb'):
            with self.assertRaises(RuntimeError): inventory([self.name], root=self.root)

    def test_unknown_or_escaping_paths_are_not_targets(self):
        for names in (['../store'], ['one-sentence-research-a/store'], [self.name, self.name], [], ['production']):
            with self.subTest(names=names), self.assertRaises(ValueError): inventory(names, root=self.root)
