"""Report versions are durable deliveries, never mutable recovery checkpoints."""
import copy
from datetime import timedelta
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import timepoint
from researchhelper.engine import ResearchAgent
from researchhelper.service import make_server

ROOT = Path(__file__).resolve().parents[2]


class ReportDeliveryVersionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='delivery-version-', dir=ROOT / 'result/acceptance')
        self.addCleanup(self.folder.cleanup)
        original_connect = socket.socket.connect

        def local_only(connection, address):
            if not isinstance(address, tuple) or address[0] != '127.0.0.1':
                raise AssertionError('Offline test attempted an external connection')
            return original_connect(connection, address)

        blocker = patch.object(socket.socket, 'connect', new=local_only)
        blocker.start()
        self.addCleanup(blocker.stop)
        self.archive = ResearchArchive(self.folder.name)
        self.agent = ResearchAgent(self.archive, ROOT, offline=True)

    def test_initial_checkpoint_is_not_a_report(self):
        task = self.agent.create_case('etf')
        self.assertEqual(self.archive.versions(task['id']), [1])
        self.assertEqual(self.archive.delivery_versions(task['id']), [])

    def test_failed_save_does_not_publish_a_report_link(self):
        task = self.agent.create_case('etf')
        with patch.object(self.archive, 'deliver', side_effect=OSError('Synthetic save failure')):
            failed = self.agent.start(task['id'])
        self.assertEqual(failed['status'], '失败')
        self.assertTrue(failed['pending_html'])
        self.assertEqual(self.archive.delivery_versions(task['id']), [])
        self.assertEqual(self.archive.versions(task['id']), [1])

    def test_explicit_successful_save_publishes_one_report(self):
        task = self.agent.create_case('etf')
        with patch.object(self.archive, 'deliver', side_effect=OSError('Synthetic save failure')):
            self.agent.start(task['id'])
        saved = self.agent.retry_save(task['id'])
        self.assertEqual(self.archive.delivery_versions(task['id']), [1])
        self.assertTrue(self.archive.report(task['id'], 1)['html_integrity_verified'])
        self.assertEqual(saved['ledger']['used']['model_calls'], 0)
        self.assertEqual(saved['ledger']['used']['data_requests'], 0)

    def test_pending_later_checkpoint_does_not_hide_or_impersonate_old_delivery(self):
        saved = self.agent.start(self.agent.create_case('etf')['id'])
        old_report = self.archive.report(saved['id'], 1)
        checkpoint = copy.deepcopy(saved)
        checkpoint['version'] = 2
        checkpoint.pop('delivery', None)
        checkpoint['status'] = '失败'
        self.archive.save(checkpoint)
        before = copy.deepcopy(self.archive.get(saved['id']))
        self.assertEqual(self.archive.versions(saved['id']), [1, 2])
        self.assertEqual(self.archive.delivery_versions(saved['id']), [1])
        self.assertEqual(self.archive.report(saved['id'], 1), old_report)
        self.assertEqual(self.archive.get(saved['id']), before)

    def test_tasks_do_not_share_delivery_lists(self):
        first = self.agent.start(self.agent.create_case('etf')['id'])
        second = self.agent.create_case('etf')
        self.assertEqual(self.archive.delivery_versions(first['id']), [1])
        self.assertEqual(self.archive.delivery_versions(second['id']), [])

    def http_service(self):
        server = make_server(Path(self.folder.name) / 'http', ROOT, offline=True,
                             authentication=True, preset_accounts=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()

        def close():
            server.shutdown()
            server.server_close()
            worker.join(3)
            self.assertFalse(worker.is_alive())

        self.addCleanup(close)
        cookies = {}
        token = ''

        def request(route, body=None, account='admin', *, view=None):
            nonlocal token
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            headers = {'Origin': 'http://127.0.0.1:' + str(server.server_port)}
            if view:
                headers['X-ResearchHelper-View'] = view
            if cookies.get(account):
                headers['Cookie'] = cookies[account]
            if body is not None:
                headers.update({'Content-Type': 'application/json', 'X-ResearchHelper-Token': token})
            try:
                connection.request('GET' if body is None else 'POST', route,
                                   None if body is None else json.dumps(body), headers)
                response = connection.getresponse()
                payload = json.loads(response.read())
                cookie = response.getheader('Set-Cookie')
                if cookie:
                    cookies[account] = cookie.split(';', 1)[0]
                if route == '/api/auth/status':
                    token = payload['token']
                return response.status, payload
            finally:
                connection.close()

        request('/api/auth/status')
        for account in ('admin', 'sales'):
            self.assertEqual(request('/api/auth/login',
                                     {'account': account, 'password': '66666666' if account == 'admin' else '88888888'}, account)[0], 200)
        return server, request

    def test_actual_endpoint_lists_only_saved_deliveries(self):
        server, request = self.http_service()
        status, task = request('/api/cases', {'id': 'etf'})
        self.assertEqual(status, 200)
        self.assertEqual(request('/api/tasks/' + task['id'] + '/versions'), (200, []))
        saved = server.agent.start(task['id'])
        before = copy.deepcopy(server.agent.archive.get(task['id']))
        self.assertEqual(request('/api/tasks/' + task['id'] + '/versions'), (200, [1]))
        self.assertEqual(server.agent.archive.get(task['id']), before)
        self.assertEqual(saved['ledger']['used']['data_requests'], 0)
        self.assertEqual(saved['ledger']['used']['model_calls'], 0)

    def test_workspace_previous_delivery_is_read_only_and_requires_the_update_digest(self):
        server, request = self.http_service()
        _, task = request('/api/cases', {'id':'etf'})
        saved = server.agent.start(task['id'])
        archive = server.agent.archive
        archive.correction(task['id'], 1, '版本1原有更正。')
        cutoff = (timepoint(saved['cutoff']) + timedelta(seconds=1)).isoformat()
        pending = server.agent.prepare_update(task['id'], cutoff)
        route = '/api/tasks/'+task['id']
        expected, = archive.delivery_history(task['id'], version=1)
        status, view = request(route, view='workspace-v1')
        self.assertEqual(status, 200)
        self.assertIsNone(view['delivery'])
        self.assertEqual(view['previous_delivery'], expected)
        self.assertNotIn('previous_delivery', request(route)[1])
        self.assertEqual(request('/api/bootstrap', view='workspace-v2')[1]['saved_report_versions'], {task['id']:1})
        self.assertEqual(request('/api/bootstrap', account='sales', view='workspace-v2')[1]['saved_report_versions'], {})
        self.assertEqual(archive.get(task['id']), pending)
        pending['update_from']['digest'] = '0'*64
        archive.save(pending)
        self.assertNotIn('previous_delivery', request(route, view='workspace-v1')[1])
        self.assertEqual(archive.delivery_history(task['id'], version=2), [])

    def test_actual_endpoint_still_requires_same_owner(self):
        server, request = self.http_service()
        status, task = request('/api/cases', {'id': 'etf'}, 'admin')
        self.assertEqual(status, 200)
        server.agent.start(task['id'])
        self.assertEqual(request('/api/tasks/' + task['id'] + '/versions', account='sales')[0], 403)
        self.assertEqual(request('/api/tasks/nonexistent/versions')[0], 400)


if __name__ == '__main__':
    unittest.main()
