"""Independent negative and positive checks for historical staging cleanup."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from tidy_development_workspace import inventory, legacy_staging_artifact, tree_stamp, validate


def digest(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


class LegacyStagingCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.stage = self.root / 'result/build-staging/20261011-234225-428015'
        self.source = self.stage / 'source'
        self.source.mkdir(parents=True)
        self.frozen = self.source / 'core.py'
        self.frozen.write_text('retained research source')
        self.cache = self.source / '.cache/pyinstaller'
        self.cache.mkdir(parents=True)
        (self.cache / 'libpython.dylib').write_bytes(b'generated compiler cache')
        self.demo = self.source / 'result/build-staging/current/demo'
        self.demo.mkdir(parents=True)
        (self.demo / 'index.html').write_text('generated offline demo')
        manifest = {'source': 'ResearchHelper', 'runtime': {
            'protocol_id': 'researchhelper.module-request'},
            'files': {'index.html': digest(self.demo / 'index.html')}}
        (self.demo / 'manifest.json').write_text(json.dumps(manifest))
        self.record = {'status': 'built_not_published', 'snapshot': str(self.source),
                       'payload': str(self.demo.parent),
                       'source_hashes': {'core.py': digest(self.frozen)},
                       'directory_artifacts': {'demo': {'path': str(self.demo), 'files': {
                           f.name: digest(f) for f in self.demo.iterdir()}}}}
        self.receipt = self.stage / 'candidate.json'
        self.save()
        (self.stage / 'build.log').write_text('retained build log')

    def save(self):
        self.receipt.write_text(json.dumps(self.record))

    def test_exact_generated_outputs_only(self):
        plan = inventory(self.root)
        self.assertEqual({item['source'] for item in plan['targets']}, {str(self.cache), str(self.demo)})
        self.assertEqual(set(validate(plan, self.root)), {self.cache, self.demo})
        self.assertTrue(self.frozen.is_file())
        self.assertTrue((self.stage / 'build.log').is_file())

    def test_incomplete_or_other_status_retained(self):
        for status in ('building', 'failed', 'built_local_candidate', None):
            with self.subTest(status=status):
                self.record['status'] = status
                self.save()
                self.assertFalse(inventory(self.root)['targets'])

    def test_source_change_blocks_all_old_artifacts(self):
        self.frozen.write_text('unrecorded user modification')
        self.assertFalse(inventory(self.root)['targets'])

    def test_bad_source_inventory_retained(self):
        for name in ('../private.py', '/tmp/private.py', '.cache/pyinstaller/libpython.dylib'):
            with self.subTest(name=name):
                self.record['source_hashes'] = {name: digest(self.cache / 'libpython.dylib')}
                self.save()
                self.assertFalse(inventory(self.root)['targets'])

    def test_wrong_payload_or_snapshot_retained(self):
        for key in ('payload', 'snapshot'):
            original = self.record[key]
            self.record[key] = str(self.root / 'private')
            self.save()
            self.assertFalse(inventory(self.root)['targets'])
            self.record[key] = original

    def test_unrecognized_cache_content_retained(self):
        (self.cache / 'research.sqlite').write_bytes(b'original user database')
        selected = {item['source'] for item in inventory(self.root)['targets']}
        self.assertNotIn(str(self.cache), selected)
        self.assertIn(str(self.demo), selected)

    def test_receipt_changed_after_inventory_blocks_apply(self):
        plan = inventory(self.root)
        self.record['note'] = 'record changed'
        self.save()
        with self.assertRaises(RuntimeError):
            validate(plan, self.root)

    def test_unrecorded_or_changed_demo_retained(self):
        (self.demo / 'original.pdf').write_bytes(b'original research')
        self.assertIsNone(legacy_staging_artifact(self.demo, self.root))
        (self.demo / 'original.pdf').unlink()
        (self.demo / 'index.html').write_text('changed offline demo')
        self.assertIsNone(legacy_staging_artifact(self.demo, self.root))

    def test_stage_process_or_mount_blocks_all_moves(self):
        plan = inventory(self.root)
        with self.assertRaises(RuntimeError):
            validate(plan, self.root, processes='python ' + str(self.frozen))
        with self.assertRaises(RuntimeError):
            validate(plan, self.root, mounted=[self.stage / 'mount'])

    def test_tracked_output_retained(self):
        subprocess.run(['git', 'add', str(self.cache.relative_to(self.root))], cwd=self.root, check=True)
        self.assertNotIn(str(self.cache), {item['source'] for item in inventory(self.root)['targets']})

    def test_receipt_source_or_output_symlink_retained(self):
        self.receipt.rename(self.stage / 'original-receipt.json')
        self.receipt.symlink_to(self.stage / 'original-receipt.json')
        self.assertFalse(inventory(self.root)['targets'])

    def test_forged_category_does_not_allow_arbitrary_folder(self):
        target = self.root / 'unrelated'
        target.mkdir()
        plan = {'root': str(self.root), 'targets': [{'source': str(target),
                'category': 'legacy_staging_compiler_cache', 'stamp': tree_stamp(target)}]}
        with self.assertRaises(RuntimeError):
            validate(plan, self.root)
