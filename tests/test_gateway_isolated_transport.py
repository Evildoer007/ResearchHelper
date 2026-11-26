"""Real local TLS and isolated workers, never a real supplier or model.

Reuse the explicit test-only CA worker; production still ignores environment
CA paths. Neither hostname nor certificate verification is disabled.
"""
import json
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ssl
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import socket

from tests import ROOT, test_portable_transport_fixtures_offline as portable
from tests import test_source_transport_offline as original
from researchhelper.contracts import ContractError


class GatewayIsolatedTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):portable.PortableSupplierTransport.setUpClass()

    @classmethod
    def tearDownClass(cls):portable.PortableSupplierTransport.tearDownClass()

    def setUp(self):
        self.fixture = portable.PortableSupplierTransport()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = self.fixture.task()
        self.task['ifind_gateway_recovery_version'] = 1
        self.requests = []

    def server(self, statuses, response=None):
        requests = self.requests
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                request = self.rfile.read(int(self.headers['Content-Length']))
                requests.append((self.path,request))
                status = statuses[min(len(requests)-1,len(statuses)-1)]
                body = response if response is not None else json.dumps(dict(errorcode=0,tables=[dict(fixture=1)])).encode()
                self.send_response(status)
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server.daemon_threads = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.fixture.cert),str(self.fixture.key))
        server.socket = context.wrap_socket(server.socket,server_side=True)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        self.fixture.collector.fixture_endpoint = 'https://localhost:'+str(server.server_port)
        def close():
            server.shutdown();server.server_close();thread.join(2)
            self.assertFalse(thread.is_alive())
        self.addCleanup(close)

    def verify_workers(self,count):
        self.assertEqual(len(self.requests),count)
        self.assertEqual(self.task['ledger']['used']['data_requests'],count)
        self.assertEqual(self.task['ledger']['used']['model_calls'],0)
        for event in self.task['ledger']['dispatches']:
            for key in ('network_request_started','network_worker_reaped','transport_sender_stopped','transport_receiver_stopped'):
                self.assertIs(event[key],True)
        self.assertNotIn(original.SECRET,json.dumps(self.task)+json.dumps(self.fixture.journal))

    def test_known_502_recovery_uses_second_isolated_worker_same_request(self):
        self.server([502,200])
        record = self.fixture.ifind(self.task)
        self.verify_workers(2)
        self.assertEqual(self.requests[0],self.requests[1])
        self.assertEqual(record['source_check'],'待核验')
        self.assertTrue(self.task['ledger']['dispatches'][0]['gateway_retry_scheduled'])
        self.assertEqual(self.task['ledger']['dispatches'][1]['gateway_retry_of_dispatch'],0)

    def test_repeated_503_is_two_sends_no_loop_and_all_workers_reaped(self):
        self.server([503,503])
        with self.assertRaisesRegex(ContractError,'服务暂不可用'):self.fixture.ifind(self.task)
        self.verify_workers(2)

    def test_permission_failure_is_one_send_without_credential_refresh(self):
        self.server([403])
        with self.assertRaises(ContractError):self.fixture.ifind(self.task)
        self.verify_workers(1)
        self.assertTrue(self.requests[0][0].endswith('/cmd_history_quotation'))

    def test_duplicate_fields_reap_worker_and_leave_no_success(self):
        self.server([200], b'{"errorcode":-4001,"errorcode":0,"tables":[{"fixture":1}]}')
        with self.assertRaisesRegex(ContractError, '重复字段'):
            self.fixture.ifind(self.task)
        self.verify_workers(1)
        event = self.task['ledger']['dispatches'][0]
        self.assertEqual(event['failure_code'], 'duplicate_response_field')
        self.assertEqual(event['status'], '返回结构异常')
        self.assertNotIn('business_code', event)
        self.assertNotIn('response_snapshot_hash', event)


def run():
    """Durable loopback-only transport evidence, not supplier acceptance."""
    from tests.accept_one_sentence_research import production_digest
    output = Path(tempfile.mkdtemp(prefix='gateway-transport-',dir=ROOT/'result/acceptance'))
    paths = list((ROOT/'core/src/researchhelper').glob('*.py')) + list((ROOT/'modules').glob('*/src/*.py'))
    before = {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    production_before = production_digest()
    original_connect = socket.socket.connect
    def local_only(connection,address):
        if isinstance(address,tuple) and address[0] in ('127.0.0.1','localhost','::1'):
            return original_connect(connection,address)
        raise AssertionError('Transport acceptance permits loopback only')
    names = ['tests.test_gateway_isolated_transport.GatewayIsolatedTransportTests',
             'tests.test_current_ifind_auth_contract',
             'tests.test_portable_transport_fixtures_offline.PortableSupplierTransport']
    with patch.object(socket.socket,'connect',local_only), (output/'tests.log').open('w') as log:
        result = unittest.TextTestRunner(stream=log,verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromNames(names))
    unchanged = all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value for name,value in before.items())
    production_unchanged = production_before==production_digest()
    record = dict(status='scoped_passed' if result.wasSuccessful() and unchanged and production_unchanged else 'failed',
        tests=result.testsRun,failures=[case.id() for case,_ in result.failures],
        errors=[case.id() for case,_ in result.errors],source_unchanged=unchanged,
        production_unchanged=production_unchanged,source_sha256=before,
        real_supplier_calls=0,real_model_calls=0,network_scope='loopback_only',
        installed_app_accepted=False,financial_approval=False,
        unchanged_inherited_assertions=True)
    target=output/'receipt.json'
    target.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print(json.dumps(dict(status=record['status'],tests=record['tests'],receipt=str(target))),flush=True)
    assert record['status']=='scoped_passed'
    return target
