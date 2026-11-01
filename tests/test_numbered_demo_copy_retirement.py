"""Independent cleanup tests; only temporary manifests and recovery folders."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from maintenance.retire_numbered_demo_copies import inventory,apply_inventory


class NumberedDemoCopyTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.folder=Path(temporary.name).resolve();self.project=self.folder/'project'
        self.demo=self.project/'demo';self.demo.mkdir(parents=True)
        self.recovery=self.folder/'recovery';self.recovery.mkdir()
        (self.project/'result/maintenance').mkdir(parents=True)
        self.current=b'Current demo';self.old=b'Known previous release'
        self.hash=lambda data:hashlib.sha256(data).hexdigest()
        (self.demo/'index.html').write_bytes(self.current)
        (self.demo/'manifest.json').write_text(json.dumps({'files':{'index.html':self.hash(self.current)}}))
        candidate=self.project/'result/build-candidates/old';candidate.mkdir(parents=True)
        manifest=candidate/'demo-manifest.json'
        manifest.write_text(json.dumps({'files':{'index.html':self.hash(self.old)}}))
        release=candidate/'release-manifest.json'
        release.write_text(json.dumps({'component_manifests':{'demo':{
            'path':str(manifest),'sha256':self.hash(manifest.read_bytes())}}}))
        (candidate/'candidate.json').write_text(json.dumps({'schema':'researchhelper.current-build.v1',
            'status':'built_local_candidate','manifest_sha256':self.hash(release.read_bytes())}))
        (self.demo/'index 2.html').write_bytes(self.old)

    def plan(self):return inventory(self.project,(self.demo,))
    def apply(self,plan):return apply_inventory(plan,project=self.project,roots=(self.demo,),recovery=self.recovery)

    def test_only_known_previous_copy_moves_and_current_demo_stays_identical(self):
        plan=self.plan();self.assertEqual(len(plan['targets']),1)
        result=self.apply(plan)
        self.assertEqual(result['moved'],1)
        self.assertEqual((Path(result['recovery'])/'demo-0/index 2.html').read_bytes(),self.old)
        self.assertEqual((self.demo/'index.html').read_bytes(),self.current)

    def test_unknown_edits_and_report_copies_are_retained(self):
        (self.demo/'index 3.html').write_bytes(b'User changes')
        report=self.demo/'app/data/reports/index 2.html';report.parent.mkdir(parents=True);report.write_bytes(self.old)
        plan=self.plan();self.assertEqual(len(plan['targets']),1)
        self.apply(plan)
        self.assertEqual((self.demo/'index 3.html').read_bytes(),b'User changes')
        self.assertEqual(report.read_bytes(),self.old)

    def test_changed_copy_after_inventory_aborts_before_any_move(self):
        plan=self.plan();(self.demo/'index 2.html').write_bytes(b'Later edit')
        with self.assertRaises(RuntimeError):self.apply(plan)
        self.assertTrue((self.demo/'index 2.html').is_file())
        self.assertEqual(list(self.recovery.iterdir()),[])

    def test_changed_current_demo_or_authority_does_not_authorize_cleanup(self):
        (self.demo/'index.html').write_bytes(b'Current edit')
        with self.assertRaises(RuntimeError):self.plan()
        (self.demo/'index.html').write_bytes(self.current)
        (self.project/'result/build-candidates/old/demo-manifest.json').write_text('{}')
        self.assertEqual(self.plan()['targets'],[])

    def test_symlink_copy_is_not_followed_or_moved(self):
        (self.demo/'index 3.html').symlink_to(self.demo/'index.html')
        plan=self.plan();self.assertEqual(len(plan['targets']),1)
        self.apply(plan)
        self.assertTrue((self.demo/'index 3.html').is_symlink())
