"""Use real loopback requests with a temporary offline archive."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection, HTTPResponse
import json
from pathlib import Path
import socket
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch

from researchhelper.contracts import context
from researchhelper.service import make_server


ROOT = Path(__file__).resolve().parents[1]
ROUTE = '/api/tasks'


def raw_post(port, token, route, body, *, lengths=None, transfer_encoding=None,
             cookie=None, half_close=True):
    if lengths is None:
        lengths = (len(body),)
    headers = [f'POST {route} HTTP/1.1', f'Host: 127.0.0.1:{port}',
               f'X-ResearchHelper-Token: {token}', 'Content-Type: application/json',
               'Connection: close']
    headers.extend(f'Content-Length: {length}' for length in lengths)
    if transfer_encoding:
        headers.append(f'Transfer-Encoding: {transfer_encoding}')
    if cookie:
        headers.append(f'Cookie: {cookie}')
    with socket.create_connection(('127.0.0.1', port), timeout=5) as stream:
        stream.sendall(('\r\n'.join(headers) + '\r\n\r\n').encode() + body)
        if half_close:
            stream.shutdown(socket.SHUT_WR)
        with HTTPResponse(stream) as response:
            response.begin()
            return response.status, json.loads(response.read())


class LocalPOSTIntegrityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='rh-local-post-')
        self.addCleanup(directory.cleanup)
        self.server = make_server(Path(directory.name) / 'store', ROOT, offline=True)
        self.worker = Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.worker.start()
        self.addCleanup(self.close)
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('GET', '/api/auth/status')
            self.token = json.loads(connection.getresponse().read())['token']
        finally:
            connection.close()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(3)
        self.assertFalse(self.worker.is_alive())

    def request(self, body, *, route=ROUTE, **options):
        return raw_post(self.server.server_port, self.token, route, body, **options)

    def assert_rejected_create(self, body, reason, **options):
        status, response = self.request(body, **options)
        self.assertEqual(status, 400)
        self.assertIn(reason, response['error'])
        self.assertNotIn('PRIVATE_FIXTURE_TEXT', json.dumps(response))
        self.assertEqual(self.server.agent.archive.list(), [])

    def test_duplicate_task_question_and_nested_escaped_field_never_save(self):
        for body in (
            b'{"question":"PRIVATE_FIXTURE_TEXT","question":"510300.SH"}',
            br'{"question":"510300.SH","limits":{"seconds":90,"secon\u0064s":300}}',
        ):
            with self.subTest(nested=b'limits' in body):
                self.assert_rejected_create(body, '重复字段')

    def test_duplicate_nested_start_version_leaves_the_existing_task_unchanged(self):
        task = self.server.agent.create('核对510300.SH')
        before = self.server.agent.archive.get(task['id'])
        status, result = self.request(b'{"expected_task":{"version":1,"version":2}}',
            route=ROUTE + '/' + task['id'] + '/start')
        self.assertEqual(status, 400)
        self.assertIn('重复字段', result['error'])
        self.assertEqual(self.server.agent.archive.get(task['id']), before)

    def test_report_retry_releases_http_status_reads_for_other_tasks(self):
        from researchhelper.reports import render

        agent = self.server.agent
        task = agent.create_case('value')
        with patch('researchhelper.reports.render', side_effect=OSError('Controlled rendering error')):
            failed = agent.start(task['id'])
        self.assertEqual(failed['status'], '失败')
        self.assertTrue(failed.get('pending_content'))
        other_id = agent.create_case('etf')['id']
        rendering, release = Event(), Event()

        def held_render(*args, **kwargs):
            rendering.set()
            if not release.wait(8):
                raise TimeoutError('Held renderer was not released')
            return render(*args, **kwargs)

        def read_status(ident):
            connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
            try:
                connection.request('GET', f'{ROUTE}/{ident}', headers={'X-ResearchHelper-View': 'workspace-v2'})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                return json.loads(response.read())
            finally:
                connection.close()

        body = json.dumps({'expected_task': context(failed)}).encode()
        route = f'{ROUTE}/{task["id"]}/retry-save'
        with patch('researchhelper.reports.render', side_effect=held_render), ThreadPoolExecutor(1) as pool:
            retry = pool.submit(self.request, body, route=route)
            try:
                self.assertTrue(rendering.wait(3))
                other = read_status(other_id)
                self.assertEqual(other['status'], '待开始')
                self.assertFalse(other.get('batch_settling'))
                self.assertTrue(read_status(task['id'])['batch_settling'])
            finally:
                release.set()
            status, saved = retry.result(timeout=5)
        self.assertEqual(status, 200)
        self.assertEqual(saved['status'], '已完成')
        self.assertEqual(saved['version'], failed['version'])
        self.assertEqual(saved['ledger']['used']['model_calls'], 0)
        self.assertEqual(saved['ledger']['used']['data_requests'], 0)
        self.assertFalse(read_status(task['id']).get('batch_settling'))

    def test_truncated_body_and_ambiguous_length_never_save(self):
        body = b'{"question":"510300.SH"}'
        for lengths, transfer, reason in (
            ((len(body) + 8,), None, '正文不完整'),
            ((len(body), len(body) + 8), None, '长度声明无效'),
            ((len(body), len(body)), None, '长度声明无效'),
            ((len(body),), 'chunked', '长度声明无效'),
            ((), None, '长度声明无效'),
        ):
            with self.subTest(lengths=lengths, transfer=transfer):
                self.assert_rejected_create(body, reason, lengths=lengths, transfer_encoding=transfer)

    def test_incomplete_body_deadline_releases_the_request(self):
        body = b'{"question":"510300.SH"}'
        handler = self.server.RequestHandlerClass
        read_body = handler.read_post_body

        def with_short_deadline(request, *args, **options):
            options.setdefault('timeout', .15)
            return read_body(request, *args, **options)

        with patch.object(handler, 'read_post_body', new=with_short_deadline):
            self.assert_rejected_create(body, '正文超时', lengths=(len(body) + 8,), half_close=False)
        status, result = self.request(body)
        self.assertEqual(status, 200)
        self.assertEqual(self.server.agent.archive.get(result['id'])['question'], '510300.SH')

    def test_nonfinite_invalid_encoding_and_excessive_depth_are_safe_errors(self):
        for literal in (b'NaN', b'Infinity', b'-Infinity', b'1e999'):
            with self.subTest(literal=literal):
                self.assert_rejected_create(b'{"question":"510300.SH","limits":{"seconds":' + literal + b'}}', '无效数值')
        for body in (b'{"question":"\xff"}', b'{"question":}',
                     b'{"question":' + b'[' * 10000 + b']' * 10000 + b'}'):
            self.assert_rejected_create(body, '不是有效JSON')

    def test_valid_multi_chunk_post_preserves_finite_values_and_task_identity(self):
        body = b'{"question":"510300.SH","limits":{"seconds":120.5}}' + b' ' * (256 * 1024)
        status, result = self.request(body, half_close=False)
        self.assertEqual(status, 200)
        task = self.server.agent.archive.get(result['id'])
        self.assertEqual(task['question'], '510300.SH')
        self.assertEqual(task['ledger']['limits']['seconds'], 120.5)
        self.assertEqual(task['ledger']['dispatches'], [])
