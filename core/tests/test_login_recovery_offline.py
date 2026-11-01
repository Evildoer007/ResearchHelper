"""Real loopback login guard; no vendor calls, production users or resets."""

import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from researchhelper.service import make_server

ROOT = Path(__file__).resolve().parents[2]


class LoginRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='login-recovery-', dir=ROOT / 'result/acceptance')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.servers = []
        self.addCleanup(self.close_servers)
        self.server = self.start_server()
        self.token = self.request('/api/auth/status')[1]['token']

    def start_server(self, port=0):
        server = make_server(self.directory, ROOT, port=port, authentication=True, preset_accounts=True)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        thread.start()
        self.servers.append((server, thread))
        return server

    def close_servers(self):
        for server, thread in self.servers:
            if thread.is_alive():
                server.shutdown()
                thread.join(timeout=2)
            server.server_close()
            self.assertFalse(thread.is_alive())

    def request(self, route, payload=None, token=None, origin=None):
        address = 'http://127.0.0.1:%d' % self.server.server_port
        headers = {'Content-Type': 'application/json'}
        if token is not None:
            headers['X-ResearchHelper-Token'] = token
        if origin is not None:
            headers['Origin'] = origin
        request = Request(address + route, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read()), dict(response.headers)

    def login(self, token=None, password='66666666'):
        return self.request('/api/auth/login', {'account': 'admin', 'password': password, 'remember': False}, token)

    def test_missing_guard_is_distinguished_from_credentials_without_leaking_token(self):
        status, body, headers = self.login()
        self.assertEqual(status, 400)
        self.assertEqual(body, {'error': '本地操作令牌无效', 'failure_code': 'local_token_invalid'})
        self.assertNotIn(self.token, json.dumps(body))
        self.assertNotIn('Set-Cookie', headers)

    def test_wrong_password_with_valid_guard_remains_a_credential_error(self):
        status, body, headers = self.login(self.token, password='fixture-wrong-password')
        self.assertEqual(status, 401)
        self.assertIn('账户或密码', body['error'])
        self.assertNotIn('failure_code', body)
        self.assertNotIn('Set-Cookie', headers)

    def test_wrong_guard_does_not_count_as_a_password_attempt(self):
        for index in range(6):
            self.assertEqual(self.login('obsolete-fixture-token')[0], 400)
        self.assertEqual(self.login(self.token)[0], 200)

    def test_no_request_is_replayed_and_readonly_status_does_not_log_in(self):
        self.assertEqual(self.login('obsolete-fixture-token')[0], 400)
        status, state, headers = self.request('/api/auth/status')
        self.assertEqual(status, 200)
        self.assertFalse(state['authenticated'])
        self.assertEqual(state['token'], self.token)
        self.assertNotIn('Set-Cookie', headers)
        self.assertEqual(self.login(self.token)[0], 200)

    def test_cross_origin_failure_is_not_misreported_as_a_stale_guard(self):
        status, body, _ = self.request('/api/auth/login', {'account': 'admin', 'password': '66666666'},
            token=self.token, origin='https://example.invalid')
        self.assertEqual(status, 400)
        self.assertIn('跨域', body['error'])
        self.assertNotIn('failure_code', body)

    def test_stale_guard_cannot_create_a_task_or_change_account_verifiers(self):
        profile = self.directory / 'local-account.json'
        before = hashlib.sha256(profile.read_bytes()).hexdigest()
        status, body, _ = self.request('/api/tasks', {'question': '不应创建的研究'}, token='obsolete-fixture-token')
        self.assertEqual(status, 400)
        self.assertEqual(body['failure_code'], 'local_token_invalid')
        self.assertEqual(self.server.agent.archive.list(), [])
        self.assertEqual(hashlib.sha256(profile.read_bytes()).hexdigest(), before)

    def test_same_port_restart_rotates_guard_but_preserves_account_data(self):
        profile = self.directory / 'local-account.json'
        before = profile.read_bytes()
        port = self.server.server_port
        old_server, thread = self.servers[-1]
        old_server.shutdown(); thread.join(timeout=2); old_server.server_close()
        self.server = self.start_server(port)
        new_token = self.request('/api/auth/status')[1]['token']
        self.assertNotEqual(new_token, self.token)
        self.assertEqual(profile.read_bytes(), before)
        self.assertEqual(self.login(self.token)[1]['failure_code'], 'local_token_invalid')
        self.assertEqual(self.login(new_token)[0], 200)


if __name__ == '__main__':
    unittest.main()
