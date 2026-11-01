"""Task mutations must reject stale browser contexts before effects or dispatch.

Authenticated loopback, isolated stores, no external services. Existing legacy
callers remain compatible; malformed explicitly supplied contexts never do.
"""

import copy
from unittest.mock import patch
from urllib.error import HTTPError
import unittest

from researchhelper.contracts import context
from tests import test_autonomous_supplement_interfaces_offline as fixtures


class TaskActionContextTests(unittest.TestCase):
    setUp = fixtures.SupplementInterfacesTests.setUp
    stop = fixtures.SupplementInterfacesTests.stop
    request = fixtures.SupplementInterfacesTests.request
    login = fixtures.SupplementInterfacesTests.login

    def task(self):
        return self.request('/api/tasks', {'question': '核对离线原件'})[0]

    def execution(self):
        policy = self.request('/api/bootstrap')[0]['execution']
        return {key: policy[key] for key in ('mode', 'policy_version')}

    def reject(self, task, action, expectation):
        before = self.server.agent.archive.get(task['id'])
        with self.assertRaises(HTTPError) as error:
            self.request('/api/tasks/' + task['id'] + '/' + action,
                         {'expected_task': expectation, 'expected_execution': self.execution()})
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        self.assertEqual(self.server.agent.archive.get(task['id']), before)

    def test_stale_and_invalid_contexts_rejected_before_every_mutator(self):
        task = self.task()
        expected = context(task)
        malformed = [None, [], {}, dict(expected, version=True), dict(expected, batch=False),
                     dict(expected, id='other-task'), dict(expected, version=task['version'] + 1),
                     dict(expected, batch=task['batch'] + 1), dict(expected, cutoff='2000-01-01'),
                     dict(expected, input_hash='wrong'), dict(expected, secret='DO_NOT_ECHO')]
        for action, method in (('start', 'start'), ('resume', 'start'), ('cancel', 'cancel'),
                               ('update', 'update'), ('retry-save', 'retry_save')):
            for value in malformed:
                with self.subTest(action=action, value=value), patch.object(self.server.agent, method) as operation:
                    self.reject(task, action, value)
                    operation.assert_not_called()

    def test_matching_context_reaches_existing_operation_once(self):
        task = self.task()
        with patch.object(self.server.agent, 'start', return_value=task) as operation:
            saved = self.request('/api/tasks/' + task['id'] + '/resume', {
                'expected_task': context(task), 'expected_execution': self.execution()})[0]
        operation.assert_called_once()
        self.assertEqual(saved['id'], task['id'])

    def test_old_cancel_cannot_cancel_a_new_batch(self):
        task = self.task()
        original = context(task)
        live = copy.deepcopy(task)
        live.update(status='运行中', batch=task['batch'] + 1)
        self.server.agent.archive.save(live)
        self.reject(live, 'cancel', original)
        self.server.agent.cancel(task['id'])

    def test_journal_revision_does_not_block_current_batch_cancellation(self):
        task = self.task()
        task.update(status='运行中', batch=1)
        self.server.agent.archive.save(task)
        expected = context(task)
        progressed = self.server.agent.archive.get(task['id'])
        progressed['ledger']['used']['seconds'] += .25
        self.server.agent.archive.save(progressed)
        result = self.request('/api/tasks/' + task['id'] + '/cancel', {'expected_task': expected})[0]
        self.assertEqual(result['status'], '已取消')
        self.assertEqual(result['batch'], 1)
        self.assertEqual(result['ledger'], progressed['ledger'])

    def test_ownership_checked_before_context_details(self):
        task = self.task()
        self.login('sales')
        with self.assertRaises(HTTPError) as error:
            self.request('/api/tasks/' + task['id'] + '/resume', {'expected_task': {}})
        self.assertIn(error.exception.code, (403, 404))
        error.exception.close()

    def test_legacy_missing_context_remains_compatible(self):
        task = self.task()
        with patch.object(self.server.agent, 'start', return_value=task) as operation:
            self.request('/api/tasks/' + task['id'] + '/resume', {'expected_execution': self.execution()})
        operation.assert_called_once()
