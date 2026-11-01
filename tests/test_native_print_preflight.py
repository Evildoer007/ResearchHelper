"""Build acceptance rejects stale, mismatched or interactive native printing."""
from pathlib import Path
import json,sys,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch
from tests import ROOT
sys.path.insert(0,str(ROOT/'packaging'))
from accept_current import accept_native_print_geometry


class NativePrintPreflightTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='rh-print-preflight-');self.addCleanup(folder.cleanup)
        self.root=Path(folder.name).resolve()
        self.worker=self.root/'readonly-worker';self.worker.write_bytes(b'fixture native worker');self.worker.chmod(0o444)
        self.source=self.root/'report.html';self.source.write_text('<main><div class="chart" id="one"></div></main>')
        self.state=dict(status='saved',engine='webkit',charts=1,print_rules=2,
            print_layout=[dict(id='one',width=354,rendered=354)],foreground_opened=False,
            system_panels_shown=False,physical_job_sent=False)

    def reply(self,arguments,**options):
        self.private=Path(arguments[0]);self.assertNotEqual(self.private,self.worker)
        Path(arguments[2]).write_bytes(b'%PDF-'+b'x'*1200)
        return SimpleNamespace(returncode=0,stdout=json.dumps(self.state),stderr='')

    def run_check(self,name='output'):
        return accept_native_print_geometry(self.worker,self.source,self.root/name)

    def test_complete_geometry_is_checked_with_a_private_executable(self):
        original=self.source.read_bytes();mode=self.worker.stat().st_mode
        with patch('accept_current.subprocess.run',side_effect=self.reply):result=self.run_check()
        self.assertEqual(result['status'],'passed');self.assertEqual(result['charts'],1)
        self.assertEqual(self.worker.stat().st_mode,mode);self.assertEqual(self.source.read_bytes(),original)

    def test_missing_preflight_or_inconsistent_geometry_is_rejected(self):
        for index,mutate in enumerate((lambda s:s.pop('print_rules'),
            lambda s:s['print_layout'][0].update(rendered=317),
            lambda s:s['print_layout'][0].update(width=True),lambda s:s.update(charts=0))):
            state=json.loads(json.dumps(self.state));mutate(self.state)
            with self.subTest(index=index),patch('accept_current.subprocess.run',side_effect=self.reply),self.assertRaises(RuntimeError):
                self.run_check('bad-'+str(index))
            self.state=state

    def test_unrequested_window_or_system_panel_is_rejected(self):
        for name in ('foreground_opened','system_panels_shown','physical_job_sent'):
            self.state[name]=True
            with self.subTest(name=name),patch('accept_current.subprocess.run',side_effect=self.reply),self.assertRaises(RuntimeError):
                self.run_check(name)
            self.state[name]=False

    def test_changed_source_is_not_recorded_as_a_successful_same_html_print(self):
        def mutate(arguments,**options):
            result=self.reply(arguments,**options);self.source.write_text('<main>changed</main>');return result
        with patch('accept_current.subprocess.run',side_effect=mutate),self.assertRaises(RuntimeError):self.run_check()


if __name__=='__main__':unittest.main()
