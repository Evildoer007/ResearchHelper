"""Single authorized task snapshot per HTTP read; isolated offline Store only."""
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from researchhelper.service import make_server

ROOT = Path(__file__).resolve().parents[1]


class TaskReadSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='task-read-snapshot-',
            dir=ROOT / 'result/acceptance')
        self.addCleanup(self.directory.cleanup)
        self.server = make_server(self.directory.name, ROOT, offline=True,
                                  authentication=True, preset_accounts=True)
        self.thread = threading.Thread(target=self.server.serve_forever,
            kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        status, boot, _ = self.request('/api/auth/status')
        self.assertEqual(status, 200)
        status, _, headers = self.request('/api/auth/login',
            payload={'account': 'admin', 'password': '66666666'}, token=boot['token'])
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';')[0]
        self.task = self.server.agent.create('离线核对单次授权快照')
        self.task['owner_account'] = 'admin'
        self.server.agent.archive.save(self.task)
        self.route = '/api/tasks/' + self.task['id']

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, *, payload=None, cookie=None, token=None):
        connection = http.client.HTTPConnection('127.0.0.1',
            self.server.server_port, timeout=5)
        headers = {'Content-Type': 'application/json'}
        if cookie:
            headers['Cookie'] = cookie
        if token:
            headers['X-ResearchHelper-Token'] = token
        try:
            connection.request('POST' if payload is not None else 'GET', path,
                body=json.dumps(payload) if payload is not None else None, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read()), dict(response.getheaders())
        finally:
            connection.close()

    def test_one_verified_archive_read_and_no_task_write(self):
        archive = self.server.agent.archive
        with patch.object(archive, 'get', wraps=archive.get) as read, \
                patch.object(archive, 'save', side_effect=AssertionError('Read wrote task')):
            status, task, _ = self.request(self.route, cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(task, self.task)

    def test_response_is_the_snapshot_ownership_was_checked_against(self):
        foreign = dict(copy.deepcopy(self.task), owner_account='sales', question='不可泄露的后续快照')
        with patch.object(self.server.agent.archive, 'get',
                          side_effect=[copy.deepcopy(self.task), foreign]) as read:
            status, task, _ = self.request(self.route, cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertEqual(task['owner_account'], 'admin')
        self.assertEqual(task['question'], self.task['question'])
        self.assertEqual(read.call_count, 1)

    def test_foreign_task_still_denied_after_one_read(self):
        foreign = dict(copy.deepcopy(self.task), owner_account='sales')
        with patch.object(self.server.agent.archive, 'get', return_value=foreign) as read:
            status, task, _ = self.request(self.route, cookie=self.cookie)
        self.assertEqual(status, 403)
        self.assertNotIn('question', task)
        self.assertEqual(read.call_count, 1)

    def test_unauthenticated_request_never_reads_task(self):
        with patch.object(self.server.agent.archive, 'get',
                          side_effect=AssertionError('Unauthenticated read')) as read:
            status, task, _ = self.request(self.route)
        self.assertEqual(status, 401)
        self.assertNotIn('question', task)
        self.assertEqual(read.call_count, 0)

    def test_pending_original_and_mapping_views_are_preserved(self):
        originals = [{'id': 'isolated-pending-original', 'name': '原件.txt'}]
        mappings = [{'query': '独立离线映射视图'}]
        archive = self.server.agent.archive
        with patch.object(archive, 'get', wraps=archive.get) as read, \
                patch.object(archive, 'retained_originals', return_value=originals) as retained, \
                patch('researchhelper.service.identity_resolutions', return_value=mappings) as identity:
            status, task, _ = self.request(self.route, cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertEqual(task, dict(self.task, retained_originals=originals, identity_resolutions=mappings))
        retained.assert_called_once_with(self.task, pending_only=True)
        identity.assert_called_once_with(self.task)
        self.assertEqual(read.call_count, 1)

    def test_each_request_reads_the_newly_committed_revision(self):
        status, first, _ = self.request(self.route, cookie=self.cookie)
        self.assertEqual(status, 200)
        self.task['question'] = '已经提交的新版问题'
        self.server.agent.archive.save(self.task)
        archive = self.server.agent.archive
        with patch.object(archive, 'get', wraps=archive.get) as read:
            status, second, _ = self.request(self.route, cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertGreater(second['revision'], first['revision'])
        self.assertEqual(second['question'], self.task['question'])
        self.assertEqual(read.call_count, 1)
