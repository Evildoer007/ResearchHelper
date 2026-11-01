"""A compact opt-in UI view must not alter the canonical task or originals."""
import copy
import http.client
import importlib
import json
import unittest
from unittest.mock import patch

fixtures = importlib.import_module((__package__ + '.' if __package__ else '') + 'test_task_read_snapshot')


class WorkspaceTaskViewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TaskReadSnapshotTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = self.fixture.task
        self.snapshot = {'name': '隔离原件', 'text': '原件仅用于界面体积检查。' * 32768}
        self.task.update(source_records=[dict(id='source_fixture', kind='api', source='isolated',
            operation='read_view_fixture', snapshot=copy.deepcopy(self.snapshot))],
            evidence=[dict(id='evidence_fixture', artifact_id='source_fixture',
                artifact=dict(id='source_fixture', kind='api', snapshot=copy.deepcopy(self.snapshot)),
                snapshot=copy.deepcopy(self.snapshot), text='保留实际摘录', value=12, unit='元',
                record_locator={'field': 'fixture'}, source_check='待核验', applicability_check='待核验')],
            pending_content={'text': '冻结正文不得被删改。' * 32768})
        self.fixture.server.agent.archive.save(self.task)
        self.route = self.fixture.route

    def request(self, *, view='workspace-v1', cookie=True, path=None, payload=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.fixture.server.server_port, timeout=5)
        headers = {'Content-Type': 'application/json'}
        if view is not None:
            headers['X-ResearchHelper-View'] = view
        if cookie:
            headers['Cookie'] = self.fixture.cookie
        if payload is not None:
            _, status, _ = self.fixture.request('/api/auth/status')
            headers['X-ResearchHelper-Token'] = status['token']
        try:
            connection.request('POST' if payload is not None else 'GET', path or self.route,
                body=json.dumps(payload) if payload is not None else None, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_legacy_call_still_returns_complete_canonical_data(self):
        status, task = self.request(view=None)
        self.assertEqual(status, 200)
        self.assertEqual(task, self.task)

    def test_workspace_opt_in_removes_only_duplicate_snapshots_and_frozen_content(self):
        status, task = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(task.get('workspace_view_version'), 1)
        self.assertTrue(task.get('has_pending_content'))
        self.assertNotIn('pending_content', task)
        self.assertNotIn('snapshot', task['evidence'][0])
        self.assertNotIn('snapshot', task['evidence'][0]['artifact'])
        self.assertEqual(task['evidence'][0]['text'], '保留实际摘录')
        self.assertEqual(task['evidence'][0]['value'], 12)
        self.assertEqual(task['evidence'][0]['source_check'], '待核验')

    def test_original_record_is_complete_for_source_viewer(self):
        _, task = self.request()
        self.assertEqual(task['source_records'], self.task['source_records'])
        self.assertEqual(task['evidence'][0]['record_locator'], {'field': 'fixture'})

    def test_view_does_not_mutate_the_authorized_snapshot_or_store(self):
        archive = self.fixture.server.agent.archive
        snapshot = copy.deepcopy(self.task)
        with patch.object(archive, 'get', return_value=snapshot) as read, \
                patch.object(archive, 'save', side_effect=AssertionError('View wrote task')):
            status, _ = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(snapshot, self.task)
        self.assertEqual(archive.get(self.task['id']), self.task)

    def test_view_preserves_task_context_revision_and_research_ledger(self):
        _, task = self.request()
        for field in ('id', 'version', 'batch', 'cutoff', 'input_hash', 'revision', 'ledger'):
            self.assertEqual(task[field], self.task[field])

    def test_missing_original_keeps_the_only_retained_snapshot(self):
        self.task['source_records'] = []
        self.fixture.server.agent.archive.save(self.task)
        _, task = self.request()
        self.assertEqual(task['evidence'][0]['snapshot'], self.snapshot)
        self.assertEqual(task['evidence'][0]['artifact']['snapshot'], self.snapshot)

    def test_different_snapshot_is_not_silently_replaced_by_original(self):
        self.task['evidence'][0]['snapshot'] = {'text': '不同内容仍保留供诊断'}
        self.fixture.server.agent.archive.save(self.task)
        _, task = self.request()
        self.assertEqual(task['evidence'][0]['snapshot'], {'text': '不同内容仍保留供诊断'})

    def test_unauthenticated_or_foreign_tasks_remain_inaccessible(self):
        self.assertEqual(self.request(cookie=False)[0], 401)
        self.task['owner_account'] = 'sales'
        self.fixture.server.agent.archive.save(self.task)
        status, result = self.request()
        self.assertEqual(status, 403)
        self.assertNotIn('evidence', result)

    def test_unknown_workspace_view_is_not_silently_accepted(self):
        self.assertEqual(self.request(view='workspace-v999')[0], 400)

    def test_bootstrap_uses_the_same_opt_in_view(self):
        status, boot = self.request(path='/api/bootstrap')
        self.assertEqual(status, 200)
        task = next(item for item in boot['tasks'] if item['id'] == self.task['id'])
        self.assertEqual(task.get('workspace_view_version'), 1)
        self.assertNotIn('pending_content', task)

    def test_created_task_response_uses_view_without_starting_research(self):
        status, task = self.request(path='/api/tasks', payload={'question': '另一个隔离视图任务'})
        self.assertEqual(status, 200)
        self.assertEqual(task.get('workspace_view_version'), 1)
        self.assertEqual(task['status'], '待开始')
        self.assertFalse(task.get('has_pending_content'))
        self.assertEqual(task['ledger']['used']['model_calls'], 0)
        self.assertEqual(task['ledger']['used']['data_requests'], 0)

    def test_synthetic_response_shrinks_without_deleting_original(self):
        _, task = self.request()
        raw = len(json.dumps(self.task, ensure_ascii=False).encode())
        view = len(json.dumps(task, ensure_ascii=False).encode())
        self.assertLess(view, raw * .5)
        self.assertEqual(task['source_records'][0]['snapshot'], self.snapshot)
