"""Real move semantics and process deadlines, using only disposable files."""
from pathlib import Path
import json,os,sys,tempfile,time,unittest
from unittest.mock import patch
from tests import ROOT
sys.path.insert(0,str(ROOT/'packaging'))
import release_retention as retention
from artifact_integrity import directory_record

class DirectoryRetirementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance')
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.source=self.base/'source';self.target=self.base/'recovery'
        (self.source/'nested/empty').mkdir(parents=True)
        (self.source/'manifest.json').write_text('{}')
        (self.source/'nested/value.txt').write_text('original')
        self.before=directory_record(self.source)
    def test_directory_rename_is_never_needed(self):
        original=Path.rename
        def rename(path,destination):
            if path.is_dir():raise OSError('Simulated synchronized-folder rename stall')
            return original(path,destination)
        with patch.object(Path,'rename',rename):
            retention._move_directory(self.source,self.target,self.before['files'])
        self.assertFalse(self.source.exists())
        self.assertEqual(directory_record(self.target)['sha256'],self.before['sha256'])
        self.assertTrue((self.target/'nested/empty').is_dir())
    def test_partial_failure_preserves_the_recoverable_union(self):
        original=Path.rename
        def rename(path,destination):
            if path.name=='value.txt':raise OSError('Simulated second-file failure')
            return original(path,destination)
        with patch.object(Path,'rename',rename),self.assertRaises(OSError):
            retention._move_directory(self.source,self.target,self.before['files'])
        self.assertEqual((self.target/'manifest.json').read_text(),'{}')
        self.assertEqual((self.source/'nested/value.txt').read_text(),'original')
    def test_unknown_file_stays_in_the_source(self):
        (self.source/'personal.txt').write_text('preserve')
        with self.assertRaises(OSError):retention._move_directory(self.source,self.target,self.before['files'])
        self.assertEqual((self.source/'personal.txt').read_text(),'preserve')
        self.assertFalse((self.target/'personal.txt').exists())
    def test_changed_file_is_not_retired(self):
        (self.source/'manifest.json').write_text('changed')
        with self.assertRaisesRegex(RuntimeError,'file changed'):
            retention._move_directory(self.source,self.target,self.before['files'])
        self.assertEqual((self.source/'manifest.json').read_text(),'changed')
    def test_owned_relative_link_is_preserved(self):
        (self.source/'current').symlink_to('nested',target_is_directory=True)
        entries=dict(self.before['files'],current={'link':'nested'})
        retention._move_directory(self.source,self.target,entries)
        self.assertEqual(str((self.target/'current').readlink()),'nested')
        self.assertEqual((self.target/'current/value.txt').read_text(),'original')

class RetirementDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance')
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def test_blocked_worker_is_stopped_and_current_candidate_stays(self):
        candidate=self.root/'candidate.json';candidate.write_text('current')
        worker='import time;time.sleep(60)'
        started=time.monotonic()
        with patch.object(retention,'RETIREMENT_WORKER',worker):
            result=retention._bounded_retirement(self.root,candidate,timeout=.15)
        self.assertLess(time.monotonic()-started,4)
        self.assertEqual(result['status'],'requires_review');self.assertTrue(result['worker_stopped'])
        self.assertEqual(candidate.read_text(),'current')
        attempt=json.loads(Path(result['worker_receipt']).read_text())
        self.assertEqual(attempt['status'],'cleanup_timed_out')
        with self.assertRaises(ProcessLookupError):os.kill(attempt['worker_pid'],0)
    def test_success_is_returned_with_a_durable_attempt(self):
        worker="from pathlib import Path;import json,sys;Path(sys.argv[4]).write_text(json.dumps(dict(status='nothing_superseded_to_remove',moved=0)))"
        with patch.object(retention,'RETIREMENT_WORKER',worker):
            result=retention._bounded_retirement(self.root,self.root/'candidate.json',timeout=2)
        self.assertEqual(result['status'],'nothing_superseded_to_remove')
        self.assertTrue(json.loads(Path(result['worker_receipt']).read_text())['worker_stopped'])
    def test_failed_worker_is_not_cleanup_success(self):
        with patch.object(retention,'RETIREMENT_WORKER','raise RuntimeError("controlled failure")'):
            result=retention._bounded_retirement(self.root,self.root/'candidate.json',timeout=2)
        self.assertEqual(result['status'],'requires_review')
        self.assertTrue(result['worker_stopped'])

if __name__=='__main__':unittest.main()
