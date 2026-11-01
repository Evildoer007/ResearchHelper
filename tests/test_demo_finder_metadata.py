"""Finder metadata is volatile; all other nonrelease content stays guarded."""
import importlib.util
from pathlib import Path
import sys,tempfile,unittest
from tests import ROOT
sys.path.insert(0,str(ROOT/'packaging'))
spec=importlib.util.spec_from_file_location('finder_sync',ROOT/'packaging/demo/sync_demo.py')
sync=importlib.util.module_from_spec(spec);spec.loader.exec_module(sync)


class FinderMetadataSyncTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory(dir='/private/tmp');self.addCleanup(directory.cleanup)
        self.root=Path(directory.name)
        (self.root/'index.html').write_text('published')
        self.files={'index.html':'irrelevant'}

    def test_finder_regular_metadata_change_does_not_abort_sync(self):
        path=self.root/'.DS_Store';path.write_bytes(b'Finder-view-before')
        before=sync.untouched_state(self.root,self.files)
        path.write_bytes(b'Finder-view-after-with-a-new-size')
        self.assertEqual(sync.untouched_state(self.root,self.files),before)
        self.assertEqual(path.read_bytes(),b'Finder-view-after-with-a-new-size')

    def test_private_and_unknown_file_changes_still_abort(self):
        for name in ('config.local.json','notes.txt','not.DS_Store'):
            path=self.root/name;path.write_text('before')
            before=sync.untouched_state(self.root,self.files)
            path.write_text('modified-with-different-size')
            self.assertNotEqual(sync.untouched_state(self.root,self.files),before)

    def test_finder_named_symlink_is_not_exempt(self):
        path=self.root/'.DS_Store';path.symlink_to('first-target')
        before=sync.untouched_state(self.root,self.files)
        self.assertIn('.DS_Store',before)
        path.unlink();path.symlink_to('second-target')
        self.assertNotEqual(sync.untouched_state(self.root,self.files),before)
