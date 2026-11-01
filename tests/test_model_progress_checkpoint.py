"""Local progress checkpoints, never real provider calls or generated research."""
from __future__ import annotations

import copy
import json
import multiprocessing
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, budget
from researchhelper.engine import ResearchAgent
import researchhelper.engine as engine_module
from researchhelper.model import IsolatedModelService, ModelSettings
from researchhelper.transport import TransportFailure, run_isolated


RESPONSE = json.dumps({'choices': [{'finish_reason': 'stop',
    'message': {'content': '{"fixture":true}'}}]}).encode()


class ModelProgressCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='model-progress-fixture-')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.settings = ModelSettings(self.folder / 'settings')
        self.settings.save({'endpoint': 'https://example.invalid/v1', 'model': 'fixture',
                            'api_key': 'SYNTHETIC_PROGRESS_KEY'})
        self.saved = []
        self.service = IsolatedModelService(self.settings, offline=False,
            journal=lambda task: self.saved.append(copy.deepcopy(task)))
        self.task = {'id': 'progress-fixture', 'version': 1, 'batch': 0, 'ledger': budget()}

    def transport(self, task, spec, started, timeout, record, worker, **options):
        self.assertEqual(timeout, 180)
        self.assertEqual(options['allowed_stages'], frozenset({'连接服务', '读取响应'}))
        callback = options['on_stage']
        record.update(stage='连接服务', network_request_started=True)
        callback()
        if hasattr(self, 'inspect_stage'):
            self.inspect_stage(task, '连接服务')
        callback()  # Repeated polling must not create archive revisions.
        record['stage'] = '读取响应'
        callback()
        if hasattr(self, 'inspect_stage'):
            self.inspect_stage(task, '读取响应')
        callback()
        return RESPONSE

    def reason(self):
        with patch('researchhelper.model.run_isolated', side_effect=self.transport):
            return self.service.reason_research_plan(self.task, [{'role': 'user', 'content': 'synthetic fixture'}])

    def test_stage_changes_are_saved_before_completion(self):
        self.reason()
        self.assertEqual(len(self.saved), 4)
        events = [item['ledger']['dispatches'][-1] for item in self.saved]
        self.assertEqual([item['status'] for item in events], ['已派发', '已派发', '已派发', '成功'])
        self.assertEqual([item['stage'] for item in events[:3]], ['连接服务', '连接服务', '读取响应'])
        self.assertTrue(events[1]['network_request_started'])
        self.assertEqual(events[1]['progress_version'], 1)
        self.assertIn('stage_updated_at', events[2])

    def test_progress_does_not_charge_again_or_change_configuration(self):
        before = self.settings.read()
        self.reason()
        self.assertEqual(self.task['ledger']['used']['model_calls'], 1)
        self.assertEqual(len(self.task['ledger']['dispatches']), 1)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)
        self.assertEqual(self.task['ledger']['reserve'], {'model_calls': 1, 'seconds': 60})
        self.assertEqual(self.settings.read(), before)
        self.assertNotIn('SYNTHETIC_PROGRESS_KEY', json.dumps(self.saved))
        self.assertNotIn('synthetic fixture', json.dumps(self.saved))

    def agent_task(self):
        origin = getattr(engine_module, '__frozen_origin__', None)
        resources = Path(origin.split('!/PYZ.pyz/')[0]).parents[2] if origin else None
        agent = ResearchAgent(ResearchArchive(self.folder / 'archive'), resources)
        self.addCleanup(agent.close_runtime)
        task = agent.create('合成进度测试，不开展真实研究')
        task['status'] = '运行中'
        task = agent.archive.save(task)
        self.service.journal = agent._journal
        self.task = task
        return agent

    def test_real_archive_observes_pending_stages(self):
        agent = self.agent_task()
        observed = []
        def inspect(task, stage):
            fresh = agent.archive.get(task['id'])
            self.assertEqual(fresh['status'], '运行中')
            self.assertEqual(fresh['ledger']['dispatches'][-1]['stage'], stage)
            self.assertEqual(fresh['ledger']['used']['model_calls'], 1)
            self.assertIsNone(fresh['delivery'])
            observed.append(stage)
        self.inspect_stage = inspect
        self.reason()
        self.assertEqual(observed, ['连接服务', '读取响应'])

    def test_concurrent_cancellation_is_not_overwritten(self):
        agent = self.agent_task()
        def inspect(task, stage):
            if stage == '连接服务':
                fresh = agent.archive.get(task['id'])
                fresh['status'] = '已取消'
                agent.archive.save(fresh)
        self.inspect_stage = inspect
        with self.assertRaises(ContractError):
            self.reason()
        fresh = agent.archive.get(self.task['id'])
        self.assertEqual(fresh['status'], '已取消')
        self.assertEqual(fresh['ledger']['used']['model_calls'], 1)
        self.assertIsNone(fresh['delivery'])

    def test_checkpoint_failure_keeps_the_charge_without_retry(self):
        calls = []
        def fail_once(task):
            calls.append(copy.deepcopy(task))
            if len(calls) == 2:
                raise OSError('SYNTHETIC_SAVE_FAILURE')
        self.service.journal = fail_once
        with self.assertRaises(ContractError):
            self.reason()
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 1)
        self.assertEqual(len(self.task['ledger']['dispatches']), 1)
        self.assertNotIn('SYNTHETIC_SAVE_FAILURE', json.dumps(self.task))


class ThreadProcess:
    """In-process IPC fixture, not a replacement production worker."""
    def __init__(self, target, args, daemon):
        self.thread = threading.Thread(target=target, args=args, daemon=daemon)
        self.pid = None

    def start(self):
        self.pid = 1
        self.thread.start()

    def is_alive(self):
        return self.thread.is_alive()

    def join(self, timeout):
        self.thread.join(timeout)

    def terminate(self):
        pass  # Every fixture sends bounded messages and returns immediately.

    def kill(self):
        pass

    def close(self):
        pass


class TransportStagePolicyTests(unittest.TestCase):
    def exchange(self, messages, **options):
        context = multiprocessing.get_context('spawn')
        # Threads share the descriptor; do not duplicate the production child
        # close operation when exercising the controller in this local fixture.
        class ChildHandle:
            def __init__(self, connection): self.connection = connection
            def close(self): pass
            def __getattr__(self, name): return getattr(self.connection, name)
        handles = []
        class LocalContext:
            def Pipe(self):
                parent, child = context.Pipe()
                handles.extend([parent, child])
                return parent, ChildHandle(child)
            Process = ThreadProcess
        def worker(connection, deadline):
            connection.recv()
            for message in messages:
                connection.send(message)
        record = {}
        try:
            with patch('researchhelper.transport.multiprocessing.get_context', return_value=LocalContext()):
                result = run_isolated({'id': 'ipc-fixture'}, {}, time.monotonic(), 2,
                    record, worker, poll_seconds=.01, **options)
            return result, record
        finally:
            self.last_record = record
            for connection in handles:
                connection.close()

    def test_valid_stages_reach_callback_without_response_content(self):
        events = []
        body, record = self.exchange([
            {'kind': 'stage', 'stage': '连接服务', 'network_request_started': True},
            {'kind': 'stage', 'stage': '读取响应'}, {'kind': 'response', 'body': b'{}'}],
            allowed_stages=frozenset({'连接服务', '读取响应'}), on_stage=lambda: events.append(True))
        self.assertEqual(events, [True, True])
        self.assertEqual(body, b'{}')
        self.assertTrue(record['network_worker_reaped'])

    def test_invalid_stages_never_reach_journal_or_record(self):
        for stage in [None, {}, 'PRIVATE_UNTRUSTED_STAGE']:
            with self.subTest(stage=stage):
                events = []
                with self.assertRaises(TransportFailure) as caught:
                    self.exchange([{'kind': 'stage', 'stage': stage}],
                        allowed_stages=frozenset({'读取响应'}), on_stage=lambda: events.append(True))
                self.assertEqual(caught.exception.code, 'invalid_transport_message')
                self.assertEqual(events, [])
                self.assertNotIn('stage', self.last_record)

    def test_model_stage_does_not_accept_arbitrary_worker_metadata(self):
        for extra in [{'record': {'private': 'PRIVATE_STAGE_VALUE'}}, {'network_request_started': 'yes'}]:
            with self.subTest(extra=extra):
                with self.assertRaises(TransportFailure):
                    self.exchange([{'kind': 'stage', 'stage': '读取响应', **extra}],
                        allowed_stages=frozenset({'读取响应'}), on_stage=lambda: self.fail('untrusted stage reached callback'))
                self.assertNotIn('PRIVATE_STAGE_VALUE', json.dumps(self.last_record))

    def test_data_worker_contract_without_stage_policy_is_unchanged(self):
        body, record = self.exchange([
            {'kind': 'stage', 'stage': '查询数据', 'record': {'sdk_request_role': 'query'}},
            {'kind': 'response', 'body': b'original'}])
        self.assertEqual(body, b'original')
        self.assertEqual(record['sdk_request_role'], 'query')


if __name__ == '__main__':
    unittest.main()
