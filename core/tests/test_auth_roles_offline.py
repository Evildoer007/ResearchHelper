"""Independent loopback role and archive isolation acceptance checks."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from researchhelper.auth import LocalAccount, AuthenticationRequired
from researchhelper.contracts import ContractError
from researchhelper.service import make_server


class RoleIsolationTests(unittest.TestCase):
    def setUp(self):
        acceptance = Path(__file__).resolve().parents[1] / "result" / "acceptance"
        acceptance.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="roles-offline-", dir=acceptance)
        self.server = make_server(self.temp.name, authentication=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.token = self.call('/api/auth/status')[1]['token']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def call(self, path, payload=None, cookie=None, token=True):
        headers = {}
        if cookie:
            headers['Cookie'] = cookie
        if token and hasattr(self, 'token'):
            headers['X-ResearchHelper-Token'] = self.token
        request = Request(self.base + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            response = urlopen(request)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read()), response.headers.get('Set-Cookie')

    def setup_accounts(self):
        self.assertEqual(self.call('/api/auth/setup', {'admin_password':'admin-secret', 'sales_password':'sales-secret'})[0], 200)
        admin = self.call('/api/auth/login', {'account':'admin', 'password':'admin-secret', 'remember':True})
        sales = self.call('/api/auth/login', {'account':'sales', 'password':'sales-secret', 'remember':True})
        self.assertEqual(admin[1]['role'], 'admin')
        self.assertEqual(sales[1]['role'], 'sales')
        self.assertIn('HttpOnly; SameSite=Strict', sales[2])
        return admin[2].split(';')[0], sales[2].split(';')[0]

    def test_roles_task_isolation_and_legacy_claim(self):
        legacy = self.server.agent.create('历史研究')
        admin, sales = self.setup_accounts()
        self.assertEqual(self.call('/api/bootstrap')[0], 401)
        admin_task = self.call('/api/tasks', {'question':'admin研究'}, admin)[1]
        sales_task = self.call('/api/tasks', {'question':'sales研究'}, sales)[1]
        for cookie, other in ((admin, sales_task), (sales, admin_task)):
            for suffix in ('', '/report', '/versions'):
                self.assertEqual(self.call('/api/tasks/' + other['id'] + suffix, cookie=cookie)[0], 403)
            for action in ('material','update','snapshot','filing','messages','cancel','retry-save','plan','start'):
                self.assertEqual(self.call('/api/tasks/' + other['id'] + '/' + action, {}, cookie)[0], 403)
        bootstrap = self.call('/api/bootstrap', cookie=sales)[1]
        self.assertEqual([task['id'] for task in bootstrap['tasks']], [sales_task['id']])
        self.assertEqual(bootstrap['model_settings'], {})
        self.assertEqual(self.call('/api/settings/model', {}, sales)[0], 403)
        self.assertEqual(self.call('/api/archive/claim-legacy', {'confirm':True}, sales)[0], 403)
        self.assertEqual(self.call('/api/tasks/' + legacy['id'], cookie=admin)[0], 403)
        self.assertEqual(self.call('/api/archive/claim-legacy', {'confirm':True}, admin)[1]['claimed'], 1)
        self.assertEqual(self.call('/api/tasks/' + legacy['id'], cookie=admin)[0], 200)
        reply = self.call('/api/tasks/' + sales_task['id'] + '/messages', {'message':'继续研究', 'start':False}, sales)
        self.assertEqual(reply[0], 200)
        self.assertEqual(reply[1]['owner_account'], 'sales')

    def test_csrf_sessions_reset_and_logout(self):
        self.assertEqual(self.call('/api/auth/setup', {'admin_password':'admin-secret','sales_password':'sales-secret'}, token=False)[0],400)
        admin, sales = self.setup_accounts()
        self.assertEqual(self.call('/api/auth/setup', {'admin_password':'changed123','sales_password':'changed456'})[0],400)
        restarted = LocalAccount(self.temp.name)
        self.assertEqual(restarted.require(sales.split('=',1)[1])['role'], 'sales')
        raw = Path(self.temp.name, 'local-sessions.json').read_text()
        self.assertNotIn(sales.split('=',1)[1], raw)
        self.assertEqual(self.call('/api/auth/users/sales/password', {'password':'new-sales-secret'}, sales)[0],403)
        self.assertEqual(self.call('/api/auth/users/sales/password', {'password':'new-sales-secret'}, admin)[0],200)
        self.assertIsNone(restarted.current(sales.split('=',1)[1]))
        self.assertEqual(self.call('/api/bootstrap', cookie=sales)[0],401)
        self.assertEqual(self.call('/api/auth/logout', {}, admin)[0],200)
        self.assertEqual(self.call('/api/bootstrap', cookie=admin)[0],401)

    def test_corruption_fails_closed_and_attempts_limited(self):
        admin, sales = self.setup_accounts()
        local = LocalAccount(self.temp.name)
        for _ in range(5):
            with self.assertRaises(AuthenticationRequired):
                local.login('missing', 'invalid-password')
        with self.assertRaisesRegex(ContractError, '次数'):
            local.login('admin', 'admin-secret')
        Path(self.temp.name, 'local-account.json').write_text('{bad-json')
        self.assertEqual(self.call('/api/bootstrap', cookie=admin)[0],400)
        self.assertEqual(self.call('/api/auth/setup', {'admin_password':'changed123','sales_password':'changed456'})[0],400)
