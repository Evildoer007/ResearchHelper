"""Bounded readiness checks with owned child processes and no real service."""
from pathlib import Path
import json,os,select,subprocess,sys,tempfile,time,unittest
from tests import ROOT
from tests.accept_native_workbench_render import wait_for_bundled_service

@unittest.skipUnless(os.name=='posix','DMG harness uses POSIX process pipes')
class BundledServiceReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance')
        self.addCleanup(self.temporary.cleanup)
        self.log=Path(self.temporary.name)/'backend.log'

    def child(self,script):
        process=subprocess.Popen([sys.executable,'-I','-B','-c',script],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        def close():
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
            process.stdout.close()
            self.assertIsNotNone(process.poll())
        self.addCleanup(close)
        return process

    def wire(self,payload):
        return self.child('import os;os.write(1,'+repr(payload)+')')

    def test_complete_local_origin_is_returned_without_waiting_for_exit(self):
        process=self.child('import os,time;os.write(1,'+repr(b'{"url":"http://127.0.0.1:19876/"}\n')+');time.sleep(10)')
        self.assertEqual(wait_for_bundled_service(process,self.log,timeout=2),'http://127.0.0.1:19876')
        self.assertIsNone(process.poll())

    def test_fragmented_line_is_joined_within_the_same_deadline(self):
        process=self.child('import os,time;os.write(1,'+repr(b'{"url":"http://127.0.0.1:')+');time.sleep(.03);os.write(1,'+repr(b'19876"}\n')+')')
        self.assertEqual(wait_for_bundled_service(process,self.log,timeout=2),'http://127.0.0.1:19876')

    def test_closed_output_reports_the_startup_log_instead_of_json_decode(self):
        process=self.wire(b'')
        with self.assertRaisesRegex(RuntimeError,'closed readiness output') as caught:
            wait_for_bundled_service(process,self.log,timeout=2)
        self.assertIn(str(self.log),str(caught.exception))

    def test_partial_line_cannot_block_past_the_deadline(self):
        process=self.child('import os,time;os.write(1,'+repr(b'{"url":"http://127.0.0.1:')+');time.sleep(10)')
        self.assertTrue(select.select([process.stdout],[],[],2)[0])
        start=time.monotonic()
        with self.assertRaisesRegex(RuntimeError,'deadline'):
            wait_for_bundled_service(process,self.log,timeout=.12)
        self.assertLess(time.monotonic()-start,1.5)

    def test_silent_live_child_cannot_block_past_the_deadline(self):
        process=self.child('import time;time.sleep(10)')
        start=time.monotonic()
        with self.assertRaisesRegex(RuntimeError,'deadline'):
            wait_for_bundled_service(process,self.log,timeout=.12)
        self.assertLess(time.monotonic()-start,1.5)

    def test_oversized_readiness_line_is_rejected_before_json_parsing(self):
        process=self.wire(b'x'*5000+b'\n')
        with self.assertRaisesRegex(RuntimeError,'exceeds the limit'):
            wait_for_bundled_service(process,self.log,timeout=2)

    def test_malformed_protocol_does_not_echo_the_raw_payload(self):
        for wire in (b'private-placeholder\n',b'{}\n',b'null\n',b'[]\n',b'{"url":123}\n'):
            with self.subTest(wire=wire):
                with self.assertRaisesRegex(RuntimeError,'invalid loopback readiness') as caught:
                    wait_for_bundled_service(self.wire(wire),self.log,timeout=2)
                self.assertNotIn('private-placeholder',str(caught.exception))

    def test_url_prefix_cannot_admit_an_external_host_credentials_or_query(self):
        invalid=['https://127.0.0.1:19876','http://127.0.0.1:19876@outside.invalid',
            'http://localhost:19876','http://127.0.0.1:19876.outside.invalid',
            'http://user:private-placeholder@127.0.0.1:19876','http://127.0.0.1:0',
            'http://127.0.0.1:65536','http://127.0.0.1:19876/path',
            'http://127.0.0.1:19876?token=private-placeholder','http://127.0.0.1:19876#private-placeholder']
        for origin in invalid:
            with self.subTest(origin=origin):
                process=self.wire((json.dumps(dict(url=origin))+'\n').encode())
                with self.assertRaisesRegex(RuntimeError,'invalid loopback readiness') as caught:
                    wait_for_bundled_service(process,self.log,timeout=2)
                self.assertNotIn('private-placeholder',str(caught.exception))
