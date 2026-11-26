"""An unreadable task must leave unrelated studies and verified reports usable."""
import hashlib
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from tests import ROOT
from researchhelper.archive import ArchiveRecordError, ResearchArchive
from researchhelper.archive_codec import encode_body
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.service import make_server


def recovery_fixture(store, resources=ROOT):
    archive = ResearchArchive(store)
    agent = ResearchAgent(archive, resources, offline=True)
    try:
        def completed(owner):
            task = agent.create_case('mechanism')
            task['owner_account'] = owner
            archive.save(task)
            return agent.start(task['id'])

        healthy = completed('admin')
        original = completed('admin')
        saved = archive.report(original['id'], 1)
        broken = agent.prepare_update(original['id'])
        value = 'fixture-only unreadable source checkpoint '*450
        broken.update(status='运行中', batch=1, source_records=[dict(id='test-damaged-source', snapshot=value)])
        broken['ledger']['used'].update(model_calls=2, data_requests=4, seconds=15)
        archive.save(broken)

        interrupted = agent.create('可恢复的中断研究')
        interrupted.update(status='运行中', owner_account='sales', batch=1)
        interrupted['nodes'][0]['status'] = '运行中'
        interrupted['ledger']['used'].update(model_calls=1, data_requests=5, seconds=19)
        archive.save(interrupted)

        unknown = agent.create('不可读取的任务标题')
        unknown['owner_account'] = 'sales'
        archive.save(unknown)
        with archive.connect() as db:
            assert db.execute('UPDATE archive_values SET compressed=? WHERE id=?',
                              (b'invalid synthetic value', fingerprint(value))).rowcount == 1
            db.execute('UPDATE tasks SET body=? WHERE id=?', ('invalid synthetic body', unknown['id']))
            db.execute('INSERT INTO task_presentations VALUES(?,?,?,?)',
                       ('admin', healthy.get('thread_id') or healthy['id'], 1, 'invalid presentation'))
        return archive, dict(healthy=healthy, broken=broken, interrupted=interrupted, unknown=unknown, saved=saved)
    finally:
        agent.close_runtime()


class ArchiveRecoveryIsolationTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='rh-archive-isolation-')
        self.addCleanup(directory.cleanup)
        self.project = Path(directory.name)
        connect = socket.socket.connect
        def loopback_only(sock, address):
            if not isinstance(address, tuple) or address[0] != '127.0.0.1':
                raise AssertionError('Non-loopback traffic is forbidden')
            return connect(sock, address)
        guard = patch.object(socket.socket, 'connect', loopback_only)
        guard.start()
        self.addCleanup(guard.stop)
        self.archive, self.fixture = recovery_fixture(self.project/'data/researchhelper')
        self.broken = self.fixture['broken']['id']
        self.unknown = self.fixture['unknown']['id']
        with self.archive.connect() as db:
            self.broken_body = db.execute('SELECT body FROM tasks WHERE id=?', (self.broken,)).fetchone()[0]
        self.opener = build_opener(ProxyHandler({}))

    def serve(self):
        self.server = make_server(self.archive.root, ROOT, offline=True, authentication=True, preset_accounts=True)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        thread.start()
        def stop():
            self.server.shutdown()
            thread.join(3)
            self.server.server_close()
            self.assertFalse(thread.is_alive())
        self.addCleanup(stop)
        self.token = self.request('/api/auth/status')[1]['token']

    def request(self, route, *, cookie=None, data=None, view=None):
        headers = {}
        if data is not None:
            headers.update({'Content-Type':'application/json','X-ResearchHelper-Token':self.token})
        if cookie:
            headers['Cookie'] = cookie
        if view:
            headers['X-ResearchHelper-View'] = view
        request = Request('http://127.0.0.1:%d'%self.server.server_port + route, headers=headers,
                          data=json.dumps(data).encode() if data is not None else None)
        try:
            response = self.opener.open(request, timeout=4)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            payload = json.loads(raw) if response.headers.get_content_type() == 'application/json' else raw
            return response.status, payload, response.headers

    def login(self, account):
        status, _, headers = self.request('/api/auth/login',
            data=dict(account=account, password={'admin':'66666666','sales':'88888888'}[account]))
        self.assertEqual(status, 200)
        return headers['Set-Cookie'].split(';',1)[0]

    def assert_bad_row_retained(self):
        with self.archive.connect() as db:
            self.assertEqual(db.execute('SELECT body FROM tasks WHERE id=?',(self.broken,)).fetchone()[0], self.broken_body)
            self.assertEqual(db.execute('SELECT body FROM tasks WHERE id=?',(self.unknown,)).fetchone()[0], 'invalid synthetic body')

    def test_startup_and_both_workspace_views_isolate_unreadable_rows_and_recover_healthy_work(self):
        self.serve()
        admin, sales = self.login('admin'), self.login('sales')
        healthy = self.fixture['healthy']['id']
        for view in ('workspace-v2', None):
            with self.subTest(view=view):
                status, body, _ = self.request('/api/bootstrap',cookie=admin,view=view)
                self.assertEqual(status,200)
                tasks = {row['id']:row for row in body['tasks']}
                self.assertEqual(set(tasks), {healthy,self.broken})
                self.assertTrue(tasks[self.broken]['archive_unreadable'])
                self.assertNotIn('source_records',tasks[self.broken])
                self.assertNotIn('archive_unreadable',tasks[healthy])
                self.assertEqual(body['archive_issue_count'],2)
                self.assertEqual(body['task_presentations'],{})
        status, body, _ = self.request('/api/bootstrap',cookie=sales,view='workspace-v2')
        self.assertEqual(status,200)
        self.assertEqual(body['archive_issue_count'],0)
        self.assertNotIn(self.broken,json.dumps(body))
        self.assertNotIn(self.unknown,json.dumps(body))
        old = self.fixture['interrupted']
        restored, = body['tasks']
        self.assertEqual(restored['id'],old['id'])
        self.assertEqual(restored['status'],'失败')
        self.assertEqual(restored['nodes'][0]['status'],'失败')
        loaded = self.archive.get(old['id'])
        self.assertEqual(loaded['ledger'],old['ledger'])
        self.assertEqual(loaded['cutoff'],old['cutoff'])
        self.assert_bad_row_retained()

    def test_frozen_report_read_and_export_do_not_resume_or_rewrite_damaged_task(self):
        self.serve()
        admin, sales = self.login('admin'), self.login('sales')
        prefix = '/api/tasks/'+self.broken
        status, body, _ = self.request(prefix,cookie=admin)
        self.assertEqual((status,body['failure_code']),(400,'archive_record_unavailable'))
        self.assertEqual(self.request(prefix+'/start',cookie=admin,data={})[0],400)
        status, history, _ = self.request(prefix+'/report-history',cookie=admin)
        self.assertEqual(status,200)
        self.assertEqual([row['version'] for row in history['reports']],[1])
        status, paper, _ = self.request(prefix+'/report/1',cookie=admin)
        self.assertEqual(status,200)
        self.assertEqual(paper,self.fixture['saved']['html'].encode())
        status, export, _ = self.request('/api/export',cookie=admin,
            data=dict(task_id=self.broken,version=1,format='html'))
        self.assertEqual(status,200)
        self.assertEqual(export['version'],1)
        for route in (prefix,prefix+'/report-history',prefix+'/report/1'):
            self.assertEqual(self.request(route,cookie=sales)[0],403)
        self.assertEqual(self.request('/api/export',cookie=sales,data=dict(task_id=self.broken,version=1))[0],403)
        self.assertEqual(self.archive.report(self.broken,1),self.fixture['saved'])
        self.assert_bad_row_retained()

    def test_recovery_never_grants_a_frozen_version_without_independent_owner(self):
        frozen = self.archive.get(self.broken,1)
        frozen['owner_account'] = 'sales'
        with self.archive.connect() as db:
            db.execute('UPDATE versions SET body=? WHERE task_id=? AND version=1',
                       (encode_body(db,frozen),self.broken))
        self.serve()
        admin = self.login('admin')
        prefix = '/api/tasks/'+self.broken
        status, body, _ = self.request(prefix+'/report-history',cookie=admin)
        self.assertEqual((status,body['reports']),(200,[]))
        self.assertEqual(self.request(prefix+'/report/1',cookie=admin)[0],403)
        self.assert_bad_row_retained()

    def test_recovery_omits_a_saved_report_with_a_damaged_page(self):
        with self.archive.connect() as db:
            db.execute('UPDATE deliveries SET html=? WHERE task_id=? AND version=1',
                       ('<html>damaged fixture</html>', self.broken))
        self.serve()
        admin = self.login('admin')
        prefix = '/api/tasks/'+self.broken
        status, body, _ = self.request(prefix+'/report-history',cookie=admin)
        self.assertEqual((status,body['reports']),(200,[]))
        self.assertEqual(self.request(prefix+'/report/1',cookie=admin)[0],400)
        self.assert_bad_row_retained()

    def test_healthy_task_can_be_archived_with_an_unreadable_unrelated_task(self):
        healthy = self.fixture['healthy']
        with self.archive.connect() as db:
            db.execute('DELETE FROM task_presentations')
        value = self.archive.update_presentation(healthy['id'],dict(expected_revision=0,changes=dict(archived=True)))
        self.assertTrue(value['archived'])
        self.assertEqual(self.archive.get(healthy['id']),healthy)
        self.assert_bad_row_retained()

    def test_skill_can_export_an_explicit_frozen_version_and_keeps_damaged_latest_strict(self):
        from core.session import Session

        skill = Session(self.project)
        with self.assertRaises(ArchiveRecordError):
            skill.status(self.broken)
        with self.assertRaises(ArchiveRecordError):
            skill.export(self.broken)
        path = skill.export(self.broken,1)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         hashlib.sha256(self.fixture['saved']['html'].encode()).hexdigest())
        self.assertEqual(skill.report_history(self.broken)[0]['version'],1)
        with self.assertRaises(ContractError):
            skill.export(self.broken,0)
        self.assert_bad_row_retained()


if __name__ == '__main__':
    unittest.main()
