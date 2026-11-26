"""Authenticated App workflow over real localhost TLS and spawned workers.

Model answers and vendor rows are explicitly fabricated. iFinD routes are
redirected only by this test collector; Tinyshare is a test SDK, not vendor
permission verification. Production planning, transport IPC, metering,
publication checks, supplementation, ownership and delivery remain enabled.
Legacy three-phase cases use an explicitly fabricated host review because their
generic fixture endpoint does not declare vendor-specific structured support.
The shared kernel still validates every review and its exact input binding.
"""
import base64
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading
import types
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request

from researchhelper import argument_support
from researchhelper.contracts import ContractError, context
from tests.test_argument_support_workflow import assessed
from researchhelper.evidence import current_snapshot_reference, direct_code_reference
from researchhelper.model import _model_http_worker
from researchhelper.source_transport import IsolatedEvidenceCollector, _source_worker
from tests import test_autonomous_supplement_interfaces_offline as fixtures

MODEL_KEY = 'LOCAL_MODEL_FIXTURE_NOT_A_REAL_KEY'
ACCESS = 'LOCAL_IFIND_ACCESS_NOT_A_REAL_TOKEN'
REFRESH = 'LOCAL_IFIND_REFRESH_NOT_A_REAL_TOKEN'
SDK_TOKEN = 'LOCAL_TINYSHARE_NOT_A_REAL_TOKEN'


def restrict_network():
    original = socket.socket.connect

    def connect(sock, address):
        if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1'):
            raise AssertionError('External network forbidden in test worker')
        return original(sock, address)
    socket.socket.connect = connect


def trust_fixture_certificate():
    # Trust this test's generated CA only in its spawned child. Certificate
    # and localhost hostname verification remain enabled; production trust
    # keeps using the bundled public CA file.
    from researchhelper import https_transport
    certificate = str(Path(os.environ['SSL_CERT_FILE']).resolve())
    https_transport.bundled_ca_file = lambda: certificate
    context = https_transport.verified_ssl_context()
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


def local_model_worker(connection, deadline):
    restrict_network()
    trust_fixture_certificate()
    _model_http_worker(connection, deadline)


def local_untrusted_model_worker(connection, deadline):
    restrict_network()
    _model_http_worker(connection, deadline)


class FixtureFrame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        assert orient == 'records'
        return self.rows


class LocalTinyshare:
    def daily(self, **parameters):
        import requests
        response = requests.post(os.environ['RH_WORKFLOW_FIXTURE_ENDPOINT'] + '/tinyshare/daily',
                                 json=parameters, timeout=5)
        response.raise_for_status()
        return FixtureFrame(response.json()['data'])


def local_source_worker(connection, deadline):
    restrict_network()
    trust_fixture_certificate()
    sys.modules['tinyshare'] = types.SimpleNamespace(pro_api=lambda *args, **kwargs: LocalTinyshare())
    _source_worker(connection, deadline)


class LocalCollector(IsolatedEvidenceCollector):
    worker_target = staticmethod(local_source_worker)

    def _require_tinyshare(self):
        return None  # The child explicitly installs the fabricated SDK above.

    def _http_json(self, task, request, timeout, event):
        original = urlparse(request.full_url)
        assert original.hostname == 'quantapi.51ifind.com'
        assert original.path in ('/api/v1/get_access_token', '/api/v1/cmd_history_quotation', '/api/v1/get_trade_dates')
        routed = Request(os.environ['RH_WORKFLOW_FIXTURE_ENDPOINT'] + original.path,
                         data=request.data, headers=dict(request.headers), method='POST')
        for name, value in request.unredirected_hdrs.items():
            routed.add_unredirected_header(name, value)
        return super()._http_json(task, routed, timeout, event)


class AuthenticatedSpawnWorkflowTests(unittest.TestCase):
    stop = fixtures.SupplementInterfacesTests.stop
    request = fixtures.SupplementInterfacesTests.request
    login = fixtures.SupplementInterfacesTests.login

    def setUp(self):
        fixtures.SupplementInterfacesTests.setUp(self)
        self.calls, self.fixture_errors = [], []
        self.source, self.invalid_publication, self.unauthorized = 'ifind', False, False
        self.model_index = 0
        self.review_calls = 0
        self.host_review_calls = 0
        self.ident, self.initial, self.preserved = None, None, None
        key, cert = self.root / 'fixture.key', self.root / 'fixture.crt'
        subprocess.run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost',
                        '-addext', 'subjectAltName=DNS:localhost'],
                       check=True, capture_output=True, timeout=20)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                payload = json.loads(raw) if raw else {}
                owner.calls.append({'path': self.path, 'parameters': payload})
                try:
                    status, value = owner.reply(self.path, payload, self.headers)
                except BaseException as error:
                    owner.fixture_errors.append(type(error).__name__ + ': ' + str(error))
                    status, value = 500, {'fixture_error': True}
                body = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.vendor = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.vendor.daemon_threads = True
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(str(cert), str(key))
        self.vendor.socket = tls.wrap_socket(self.vendor.socket, server_side=True)
        self.vendor_thread = threading.Thread(target=self.vendor.serve_forever, kwargs={'poll_interval': .01})
        self.vendor_thread.start()
        self.addCleanup(self.stop_vendor)
        endpoint = 'https://localhost:' + str(self.vendor.server_port)
        environment = patch.dict(os.environ, {'SSL_CERT_FILE': str(cert), 'REQUESTS_CA_BUNDLE': str(cert), 'NO_PROXY': '*', 'no_proxy': '*',
                                              'RH_WORKFLOW_FIXTURE_ENDPOINT': endpoint})
        environment.start()
        self.addCleanup(environment.stop)
        agent = self.server.agent
        agent.model.worker_target = local_model_worker
        self.prepare_argument_review = agent._prepare_argument_review
        host = patch.object(agent, '_prepare_argument_review', side_effect=types.MethodType(AuthenticatedSpawnWorkflowTests.host_review, self))
        host.start()
        self.addCleanup(host.stop)
        agent.collector = LocalCollector(offline=agent.offline, journal=agent._journal, active=agent._check_run_control)
        agent.collector.original_keeper = agent.archive.retain_original
        self.request('/api/settings/model', {'endpoint': endpoint + '/model', 'model': 'fabricated-model', 'api_key': MODEL_KEY})
        self.request('/api/settings/data', {'ifind': {'refresh_token': REFRESH}, 'tinyshare': {'token': SDK_TOKEN}})
        self.request('/api/settings/execution', {'enabled': True, 'confirm': True})

    def host_review(self, task, proposal, selected):
        # Synthetic host assessment isolates the legacy generic-transport
        # contract. Never declare this a model/financial-quality review.
        if argument_support.enabled(task) and proposal:
            try:
                if argument_support.find_record(task, proposal) is None:
                    _, result = assessed(task, proposal)
                    record = argument_support.make_record(task, proposal, result, actor='host')
                    task.setdefault('argument_support_reviews', []).append(record)
                    self.host_review_calls += 1
            except ContractError:
                # Invalid quantities must reach the real engine's partial-
                # delivery path, not fail inside this fabricated reviewer.
                return self.prepare_argument_review(task, proposal, selected)
        return self.prepare_argument_review(task, proposal, selected)

    def stop_vendor(self):
        self.vendor.shutdown()
        self.vendor.server_close()
        self.vendor_thread.join(3)
        self.assertFalse(self.vendor_thread.is_alive())

    def need(self, day, ident):
        if self.source == 'ifind':
            return fixtures.need(day, ident)
        return {'id': ident, 'source': 'tinyshare', 'operation': 'daily', 'question_ids': ['q2'],
                'selection_reason': '本独立验收案例明确核对Tinyshare响应，不是在iFinD失败后换源',
                'parameters': {'ts_code': '600000.SH', 'start_date': day.replace('-', ''),
                               'end_date': day.replace('-', ''), 'fields': 'ts_code,trade_date,close'}}

    def reply(self, route, payload, headers):
        if route == '/api/v1/get_access_token':
            self.assertEqual(headers.get('refresh_token'), REFRESH)
            return 200, {'errorcode': 0, 'data': {'access_token': ACCESS}}
        if route == '/api/v1/cmd_history_quotation':
            self.assertEqual(headers.get('access_token'), ACCESS)
            if self.unauthorized:
                return 401, {'errorcode': -1, 'errmsg': ACCESS}
            return 200, {'errorcode': 0, 'tables': [{'thscode': '600000.SH', 'time': [payload['startdate']], 'table': {'close': [12]}}]}
        if route == '/tinyshare/daily':
            return 200, {'data': [{'ts_code': '600000.SH', 'trade_date': payload['start_date'], 'close': 12}]}
        self.assertEqual(route, '/model')
        self.assertEqual(headers.get('Authorization'), 'Bearer ' + MODEL_KEY)
        # The model fixture follows the shipped phase contract, including the
        # independent review request. Labels below are fabricated protocol
        # results, never a test of financial-semantic quality.
        presented = json.loads(payload['messages'][-1]['content'])
        if isinstance(presented, dict) and 'binding_ref' in presented and 'targets' in presented:
            self.review_calls += 1
            targets = presented['targets']
            related = sorted({ident for target in targets for ident in target['question_ids']})
            self.assertTrue(set(related).issubset({'q1', 'q2'}))
            def references(target):
                choices = target.get('allowed_support_basis_refs')
                if choices is not None:
                    facts = [ref for ref in choices if ref != 'scope']
                    return facts[:1] or ['scope']
                return ['evidence:' + target['evidence_refs'][0]] if target['evidence_refs'] else ['scope:task']
            result = dict(version=presented['version'], binding_ref=presented['binding_ref'],
                questions=[dict(question_id=ident,status='answered',missing_evidence=[],
                    reason='合成传输流程复核，不代表真实金融质量。') for ident in related],
                reviews=[dict(target_id=target['id'],segments=[dict(unit_ref=unit['unit_ref'],
                    verdict='supported',basis_refs=references(target),
                    explanation='仅验证同版引用、完整覆盖与研究采用流程。') for unit in target['units']]) for target in targets])
            return 200, {'model': 'fabricated-model', 'choices': [{'finish_reason': 'stop',
                'message': {'content': json.dumps(result, ensure_ascii=False)}}]}
        index = self.model_index
        self.model_index += 1
        if index == 0:
            result = {'questions': fixtures.QUESTIONS, 'data_requests': [self.need('2026-09-29', 'first_close')], 'calculations': []}
        elif index == 1:
            result = {'evidence_specs': [self.initial]}
        elif index == 2:
            result = {'answers': {'q1': fixtures.answer('publication', '合成原件披露日期为2026-10-01。')}, 'theses': []}
        elif index == 3:
            task = self.server.agent.archive.get(self.ident)
            self.preserved = copy.deepcopy(next(node for node in task['nodes'] if node['questions'] == ['q1'] and node['kind'] == 'answer'))
            result = fixtures.proposal(data_requests=[self.need('2026-09-30', 'second_close')])
        elif index == 4:
            task = self.server.agent.archive.get(self.ident)
            record = next(item for item in task['source_records'] if 'second_close' in item.get('data_need_ids', []))
            spec = {'id': 'close_value', 'artifact_id': record['id'], 'object': '600000.SH',
                'record_locator': {'row': 0, 'field': 'close'}, 'period': '2026-09-30', 'nature': '已实现事实',
                'published_at': None, 'publication': {'at': None, 'basis_ref': None, 'certainty': '未知',
                    'precision': '未知', 'timezone': '未知', 'basis': '首次公开未知，当前响应有本任务取数记录'},
                'availability': current_snapshot_reference(task, record), 'raw_value': 12, 'value': 12,
                'raw_unit': '元', 'unit': '元', 'conversion_factor': 1, 'question_ids': ['q2'],
                'direct_code_reference': direct_code_reference(task, record),
                'review': {'question_ids': ['q2'], 'allowed_natures': ['已实现事实'],
                    'conditions': ['只核对当前响应原值，不证明历史当时可得'], 'reason': '同一代码、观测日与字段原值'}}
            if self.source == 'ifind':
                spec['record_locator']['table'] = 0
            if self.invalid_publication:
                spec.pop('availability')
                spec['published_at'] = self.initial['published_at']
                spec['publication'] = self.initial['publication']
            result = {'evidence_specs': [spec]}
        elif index == 5:
            result = {'answers': {'q2': {'refs': ['close_value'], 'calculation_refs': [], 'numeric_segments': {'answer': [
                {'text': '本次响应所载指定日收盘价为'}, {'source': 'evidence', 'ref': 'close_value', 'path': 'raw_value', 'format': 'signed', 'digits': 2},
                {'text': '，首次公开时间未知，不证明历史当时可得。'}]}}}, 'theses': []}
        else:
            raise AssertionError('Unexpected fabricated model request')
        return 200, {'model': 'fabricated-model', 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(result, ensure_ascii=False)}}]}

    def run_research(self):
        task = self.request('/api/tasks', {'question': '核对本次响应的指定证券收盘原值与材料日期',
                             'objects': ['600000.SH'], 'questions': fixtures.QUESTIONS, 'research_mode': 'current'})[0]
        self.assertEqual(task['research_mode'], 'current')
        task = self.request('/api/tasks/' + task['id'] + '/material', {'name': 'publication.txt',
            'base64': base64.b64encode(fixtures.TEXT.encode()).decode(), 'expected_version': task['version']})[0]
        self.ident, self.initial = task['id'], fixtures.material_spec(task['materials'][0])
        execution = self.request('/api/bootstrap')[0]['execution']
        self.request('/api/tasks/' + self.ident + '/start', {'expected_task': context(task),
            'expected_execution': {key: execution[key] for key in ('mode', 'policy_version')}})
        self.server.agent.workers[self.ident].join(40)
        self.assertFalse(self.server.agent.workers[self.ident].is_alive())
        final = self.request('/api/tasks/' + self.ident)[0]
        self.observed_final = copy.deepcopy(final)
        self.assertFalse(self.fixture_errors, self.fixture_errors)
        for dispatch in final['ledger']['dispatches']:
            if dispatch.get('network_request_started'):
                self.assertTrue(dispatch['network_worker_reaped'])
                self.assertTrue(dispatch['transport_receiver_stopped'])
        self.assertEqual(final['questions'][0]['status'], '已回答', final.get('error') or final['gaps'])
        self.assertEqual(final['questions'][0]['answer'], '合成原件披露日期为2026-10-01。')
        self.assertIsNotNone(final['delivery'])
        self.assertNotIn(ACCESS, json.dumps(final))
        self.assertNotIn(REFRESH, json.dumps(final))
        self.assertNotIn(MODEL_KEY, json.dumps(final))
        self.assertNotIn(SDK_TOKEN, json.dumps(final))
        return final

    def assert_positive(self, source, requests):
        self.source = source
        final = self.run_research()
        self.assertEqual(final['status'], '已完成', final.get('error') or final['gaps'])
        self.assertEqual(final['ledger']['used']['model_calls'], 6)
        self.assertEqual(self.host_review_calls, 2)
        self.assertEqual(self.review_calls, 0)
        self.assertEqual(final['ledger']['used']['data_requests'], requests)
        self.assertEqual(final['ledger']['supplements'], 1)
        self.assertEqual(final['questions'][1]['status'], '已回答')
        evidence = next(item for item in final['evidence'] if item['id'] == 'close_value')
        self.assertIsNone(evidence['published_at'])
        self.assertEqual(evidence['publication']['certainty'], '未知')
        self.assertEqual(evidence['source_receipt']['component'], 'EvidenceCollector')
        self.assertEqual(evidence['applicability_receipt']['component'], 'ResearchAnalyzer')
        node = next(item for item in final['nodes'] if item['id'] == self.preserved['id'])
        self.assertEqual(node['output_hash'], self.preserved['output_hash'])
        report = self.server.agent.archive.report(self.ident, final['version'])
        self.assertIn('12.00元', report['html'])
        self.login('sales')
        with self.assertRaises(HTTPError) as denied:
            self.request('/api/tasks/' + self.ident)
        self.assertIn(denied.exception.code, (403, 404))
        denied.exception.close()

    def test_ifind_authenticated_supplement_and_delivery(self):
        self.assert_positive('ifind', 3)
        self.assertEqual(sum(call['path'].endswith('get_access_token') for call in self.calls), 1)

    def test_tinyshare_authenticated_supplement_and_delivery(self):
        self.assert_positive('tinyshare', 2)
        self.assertTrue(all(call['path'] in ('/model', '/tinyshare/daily') for call in self.calls))

    def test_borrowed_publication_is_rejected_without_losing_answer(self):
        self.invalid_publication = True
        final = self.run_research()
        self.assertEqual(final['status'], '部分完成')
        self.assertEqual(final['ledger']['used']['model_calls'], 5)
        self.assertEqual(self.host_review_calls, 2)
        self.assertEqual(self.review_calls, 0)
        self.assertTrue(all('q2' not in row['proposal']['answers'] for row in final['argument_support_reviews']))
        self.assertEqual(final['questions'][1]['status'], '未回答')
        self.assertEqual([item['id'] for item in final['evidence']], ['publication'])
        self.assertTrue(any('不对应本条证据原件' in item['rejection'] for item in final['rejected_evidence']))

    def test_permission_denial_never_retries_refreshes_or_switches_source(self):
        self.unauthorized = True
        final = self.run_research()
        self.assertEqual(final['status'], '部分完成')
        self.assertEqual(final['ledger']['used']['data_requests'], 2)
        self.assertEqual(sum(call['path'].endswith('cmd_history_quotation') for call in self.calls), 1)
        self.assertEqual(sum(call['path'].endswith('get_access_token') for call in self.calls), 1)
        self.assertFalse(any(call['path'].startswith('/tinyshare') for call in self.calls))
        self.assertEqual(final['questions'][1]['status'], '未回答')


class CurrentDraftAuthenticatedWorkflowTests(unittest.TestCase):
    """Current default Chat over real local TLS, with fabricated data only."""
    def setUp(self):
        fixture = AuthenticatedSpawnWorkflowTests()
        fixture.draft_first = True
        fixture.reply = self.reply
        self.fixture = fixture
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()

    def reply(self, route, payload, headers):
        fixture = self.fixture
        if route != '/model':
            return AuthenticatedSpawnWorkflowTests.reply(fixture, route, payload, headers)
        self.assertEqual(headers.get('Authorization'), 'Bearer ' + MODEL_KEY)
        index = fixture.model_index
        fixture.model_index += 1
        if index == 0:
            need = fixture.need('2026-09-30', 'current_close')
            need['question_ids'] = ['q1']
            result = dict(data_requests=[need])
        elif index == 1:
            analysis = ('当前接口未返回可用原值，保留研究范围并列出资料缺口。' if fixture.unauthorized else
                        '600000.SH在2026-09-30的收盘价为12元。仅核对本次响应原值，不证明历史当时可得。')
            result = dict(title='指定日收盘原值核对',conclusion=analysis,
                logics=[dict(title='原值与日期',analysis=analysis,risk='当前响应不能证明历史可得性。')])
        else:
            raise AssertionError('Default Chat unexpectedly requested another fabricated completion')
        return 200, {'model':'fabricated-model','choices':[{'finish_reason':'stop',
            'message':{'content':json.dumps(result,ensure_ascii=False)}}]}

    def study(self):
        fixture = self.fixture
        question = ('使用Tinyshare核对' if fixture.source == 'tinyshare' else '核对') + '600000.SH在2026-09-30的收盘原值并生成报告。'
        task = fixture.request('/api/tasks',dict(question=question,objects=['600000.SH'],research_mode='current'))[0]
        self.assertEqual(task['draft_first_version'],1)
        state = fixture.request('/api/bootstrap')[0]['execution']
        fixture.request('/api/tasks/'+task['id']+'/start',dict(expected_task=context(task),
            expected_execution={key:state[key] for key in ('mode','policy_version')}))
        worker=fixture.server.agent.workers[task['id']];worker.join(40)
        self.assertFalse(worker.is_alive())
        final=fixture.request('/api/tasks/'+task['id'])[0]
        self.assertFalse(fixture.fixture_errors,fixture.fixture_errors)
        self.assertTrue(final['delivery'],final.get('error') or final.get('gaps'))
        self.assertEqual(final['ledger']['used']['model_calls'],2)
        self.assertEqual(fixture.model_index,2)
        self.assertEqual(fixture.review_calls,0)
        self.assertEqual(final['ledger']['used']['data_requests'],1 if fixture.source=='tinyshare' else 2)
        self.assertTrue(all(call['path'] in ('/model','/api/v1/get_access_token','/api/v1/cmd_history_quotation','/tinyshare/daily') for call in fixture.calls))
        report=fixture.server.agent.archive.report(task['id'],final['version'])
        self.assertTrue(report['html_integrity_verified'])
        self.assertEqual(report['digest'],final['delivery']['digest'])
        self.assertEqual(final['draft_report']['origin'],'model')
        for secret in (MODEL_KEY,ACCESS,REFRESH,SDK_TOKEN):
            self.assertNotIn(secret,json.dumps(final));self.assertNotIn(secret,report['html'])
        return final,report

    def test_default_chat_fetches_ifind_once_and_saves_the_report(self):
        final,report=self.study()
        facts=final['draft_report']['facts']
        self.assertEqual(len(facts),1)
        self.assertEqual((facts[0]['code'],facts[0]['period'],facts[0]['raw_value'],facts[0]['unit']),
                         ('600000.SH','2026-09-30',12,'元'))
        self.assertIn('12元',report['html'])
        self.assertEqual(sum(call['path'].endswith('get_access_token') for call in self.fixture.calls),1)
        self.assertEqual(sum(call['path'].endswith('cmd_history_quotation') for call in self.fixture.calls),1)
        self.assertFalse(any(call['path'].startswith('/tinyshare') for call in self.fixture.calls))

    def test_explicit_tinyshare_task_uses_its_own_transport_and_report(self):
        self.fixture.source='tinyshare'
        final,report=self.study()
        self.assertEqual(len(final['draft_report']['facts']),1)
        self.assertEqual(final['draft_report']['facts'][0]['raw_value'],12)
        self.assertIn('12元',report['html'])
        self.assertTrue(all(call['path'] in ('/model','/tinyshare/daily') for call in self.fixture.calls))

    def test_untrusted_tls_stops_after_one_attempt_and_saves_the_data_outline(self):
        fixture=self.fixture
        fixture.server.agent.model.worker_target=local_untrusted_model_worker
        task=fixture.request('/api/tasks',dict(question='核对600000.SH的原值并生成报告',
            objects=['600000.SH'],research_mode='current'))[0]
        state=fixture.request('/api/bootstrap')[0]['execution']
        fixture.request('/api/tasks/'+task['id']+'/start',dict(expected_task=context(task),
            expected_execution={key:state[key] for key in ('mode','policy_version')}))
        worker=fixture.server.agent.workers[task['id']];worker.join(40)
        self.assertFalse(worker.is_alive())
        final=fixture.request('/api/tasks/'+task['id'])[0]
        self.assertTrue(final['delivery'])
        self.assertEqual(final['status'],'部分完成')
        self.assertEqual(final['draft_report']['origin'],'data_outline')
        self.assertEqual(final['draft_report']['facts'],[])
        self.assertEqual(final['ledger']['used']['model_calls'],1)
        self.assertEqual(final['ledger']['used']['data_requests'],0)
        self.assertEqual(fixture.model_index,0)
        self.assertEqual(fixture.calls,[])
        event=final['ledger']['dispatches'][0]
        self.assertEqual(event['failure_code'],'tls_failure')
        self.assertTrue(event['network_worker_reaped'])
        self.assertTrue(fixture.server.agent.archive.report(task['id'],final['version'])['html_integrity_verified'])

    def test_permission_failure_still_delivers_without_refresh_or_source_switch(self):
        self.fixture.unauthorized=True
        final,report=self.study()
        self.assertEqual(final['draft_report']['facts'],[])
        self.assertIn('未返回可用原值',report['html'])
        self.assertNotIn('12元',report['html'])
        self.assertEqual(sum(call['path'].endswith('get_access_token') for call in self.fixture.calls),1)
        self.assertEqual(sum(call['path'].endswith('cmd_history_quotation') for call in self.fixture.calls),1)
        self.assertFalse(any(call['path'].startswith('/tinyshare') for call in self.fixture.calls))
