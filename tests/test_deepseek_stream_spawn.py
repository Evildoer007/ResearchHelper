"""Actual spawned HTTP reader against a local TLS/SSE fixture, not DeepSeek.

Only the four new cases are collected. Original fixture tests are preserved
and exercised separately; this does not run a financial study or open UI.
"""
import copy
import json
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import unittest

from researchhelper.contracts import ContractError
from researchhelper.model import IsolatedModelService
from tests import test_portable_transport_fixtures_offline as portable
from tests import test_deepseek_stream as codec


class LocalStreamService(IsolatedModelService):
    """Test-only localhost route; production origin selection is not patched."""
    def _request_bytes(self,task,request,started,timeout,record):
        body=json.loads(request.data)
        body.update(stream=True,stream_options={'include_usage':True})
        request.data=json.dumps(body).encode()
        record.update(response_protocol='deepseek_sse_v1',stream_progress_is_lower_bound=True)
        return super()._request_bytes(task,request,started,timeout,record)


class StreamTLSFixture(portable.PortableModelTransport):
    def setUp(self):
        super().setUp()
        self.service=LocalStreamService(self.settings,offline=False,
            journal=lambda task:self.journal.append(copy.deepcopy(task)))
        self.service.worker_target=portable.model_fixture_worker
        self.service.MAX_WAIT_SECONDS=2

    def stream_server(self,mode):
        stop=threading.Event()
        self.request_headers=[];self.request_bodies=[]
        headers,bodies=self.request_headers,self.request_bodies
        prefix=codec.frame({'reasoning_content':'PRIVATE_NEVER_ARCHIVE'},model='fixture')
        suffix=codec.wire(codec.frame({'content':'{"ok":true}'},'stop',model='fixture'),
            codec.frame(usage={'prompt_tokens':3,'completion_tokens':4,'total_tokens':7},model='fixture'),
            done=mode!='incomplete')

        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                bodies.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                headers.append(dict(self.headers))
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                if mode!='stall':self.send_header('Content-Length',str(len(prefix)+len(suffix)))
                self.end_headers()
                try:
                    self.wfile.write(prefix);self.wfile.flush()
                    if mode=='stall':
                        stop.wait(8)
                    else:
                        self.wfile.write(suffix);self.wfile.flush()
                except (OSError,ssl.SSLError):pass

        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.cert),str(self.key))
        server.socket=context.wrap_socket(server.socket,server_side=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.settings.save(dict(endpoint=f'https://localhost:{server.server_port}/chat',model='fixture',
            api_key='ISOLATED_FIXTURE_NOT_A_REAL_KEY'))
        def cleanup():
            stop.set();server.shutdown();server.server_close();thread.join(2)
            self.assertFalse(thread.is_alive())
        self.addCleanup(cleanup)

    def test_stream_complete_uses_one_request_and_preserves_usage(self):
        self.stream_server('normal');task=self.task()
        self.assertEqual(self.service.reason_research_plan(task,[]),'{"ok":true}')
        record=self.checked(task)
        self.assertTrue(record['response_stream_complete'])
        self.assertEqual(record['usage']['total_tokens'],7)
        self.assertFalse(record['stream_progress_is_lower_bound'])
        self.assertTrue(self.request_bodies[0]['stream'])
        self.assertEqual(len(self.request_headers),1)

    def test_stream_timeout_keeps_numeric_progress_and_reaps_worker(self):
        self.stream_server('stall');task=self.task();self.service.MAX_WAIT_SECONDS=.8
        with self.assertRaises(ContractError):self.service.reason_research_plan(task,[])
        record=self.checked(task)
        self.assertGreater(record['stream_reasoning_characters'],0)
        self.assertEqual(record['stream_content_characters'],0)
        self.assertTrue(record['stream_progress_is_lower_bound'])
        self.assertEqual(len(self.request_headers),1)
        self.assertNotIn('reasoning_content',json.dumps(self.journal))

    def test_stream_cancel_does_not_reset_budget_or_save_thought(self):
        self.stream_server('stall');task=self.task()
        def active(current):
            if current['ledger']['dispatches'][-1].get('stream_events',0):
                raise ContractError('合成取消')
        self.service.active=active
        with self.assertRaises(ContractError):self.service.reason_research_plan(task,[])
        record=self.checked(task,'transport_cancelled')
        self.assertGreater(record['stream_events'],0)
        self.assertEqual(task['ledger']['reserve'],dict(model_calls=1,seconds=60))
        self.assertEqual(len(self.request_headers),1)

    def test_stream_missing_done_is_not_a_completed_response(self):
        self.stream_server('incomplete');task=self.task()
        with self.assertRaises(ContractError):self.service.reason_research_plan(task,[])
        self.checked(task,'stream_incomplete')
        self.assertEqual(len(self.request_headers),1)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(StreamTLSFixture(name) for name in (
        'test_stream_complete_uses_one_request_and_preserves_usage',
        'test_stream_timeout_keeps_numeric_progress_and_reaps_worker',
        'test_stream_cancel_does_not_reset_budget_or_save_thought',
        'test_stream_missing_done_is_not_a_completed_response'))


if __name__=='__main__':unittest.main()
