"""Real engine journals and SQLite around a completed official response.

Transport is replaced with explicit complete or interrupted fixture replies;
no provider, credentials or model service is used.
"""
import base64
import copy
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from tests.test_independent_official_sources import CN_ISSUER, cn_raw, params
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.announcement_sources import retained_response, catalogue_recovery
from researchhelper.contracts import fingerprint
from researchhelper.official_sources import parse_catalogue


class OfficialResponseRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='response-recovery-',dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup)
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)
        self.archive=ResearchArchive(Path(temporary.name)/'store')
        self.agent=ResearchAgent(self.archive,ROOT,offline=False,isolate_sources=True,
            source_credentials={},draft_first=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task=self.agent.create('核对贵州茅台600519.SH的公告',research_mode='current')
        self.need=dict(id='company_catalogue',source='official',operation='release_catalog',parameters=params(),
            question_ids=['q1'],selection_reason='核对该公司已发布公告的内容。')

    def run_catalogue(self, transport):
        with patch('researchhelper.source_transport.official_announcement',side_effect=transport):
            return self.agent.start(self.task['id'],dict(data_requests=[self.need],draft_report=None),defer_delivery=True)

    def cancelled_after(self, phase):
        def respond(task,parameters,stage,started,timeout,event,**options):
            event.update(transport_isolated=True,network_request_started=True,network_worker_reaped=True)
            if stage==phase:self.agent.cancel(task['id'])
            return CN_ISSUER if stage=='lookup' else cn_raw()
        result=self.run_catalogue(respond)
        self.assertEqual(result['status'],'已取消')
        return self.archive.get(self.task['id'])

    def test_completed_lookup_survives_cancellation_and_resume_queries_only_directory(self):
        cancelled=self.cancelled_after('lookup')
        rows=cancelled.get('official_response_checkpoints',[])
        self.assertEqual(len(rows),1)
        self.assertEqual(base64.b64decode(rows[0]['original_base64']),CN_ISSUER)
        self.assertEqual(cancelled['source_records'],[])
        self.assertEqual(cancelled['ledger']['used']['data_requests'],1)
        phases=[]
        def resume(task,parameters,phase,started,timeout,event,**options):
            phases.append(phase)
            self.assertEqual(phase,'catalogue','Completed issuer lookup must not be downloaded twice')
            event.update(transport_isolated=True,network_request_started=True,network_worker_reaped=True)
            return cn_raw()
        restored=self.run_catalogue(resume)
        self.assertEqual(phases,['catalogue'])
        self.assertEqual(len(restored['source_records']),1)
        self.assertEqual(restored['ledger']['used']['data_requests'],2)
        self.assertEqual(restored['ledger']['used']['model_calls'],0)

    def test_completed_directory_survives_cancellation_and_resume_uses_no_network(self):
        cancelled=self.cancelled_after('catalogue')
        self.assertEqual(len(cancelled.get('official_response_checkpoints',[])),2)
        self.assertEqual(cancelled['source_records'],[])
        before=copy.deepcopy(cancelled['ledger']['used'])
        restored=self.run_catalogue(lambda *args,**kwargs:self.fail('Retained directory must be parsed without another send'))
        self.assertEqual(len(restored['source_records']),1)
        self.assertEqual(restored['ledger']['used']['data_requests'],before['data_requests'])
        self.assertEqual(restored['ledger']['used']['model_calls'],0)
        self.assertEqual(restored['source_records'][0]['snapshot']['query_scope'],params())

    def test_complete_directory_survives_restart_and_offline_recovery(self):
        cancelled = self.cancelled_after('catalogue')
        original = next(row for row in cancelled['official_response_checkpoints'] if row['role'] == 'catalogue')
        dispatches = copy.deepcopy(cancelled['ledger']['dispatches'])
        self.agent.close_runtime()
        # Reopen the actual SQLite archive with networking disabled, as on a
        # later App or Skill session. No source or model object is carried over.
        self.archive = ResearchArchive(self.archive.root)
        self.agent = ResearchAgent(self.archive, ROOT, offline=True,
            draft_first=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        restored = self.run_catalogue(lambda *a, **k: self.fail('Offline recovery must not send'))
        record = restored['source_records'][0]
        self.assertEqual(record['obtained_at'], original['obtained_at'])
        self.assertEqual(record['response_recovery']['checkpoint_id'], original['id'])
        self.assertEqual(record['response_recovery']['new_network_requests'], 0)
        self.assertFalse(record['response_recovery']['financial_approval'])
        self.assertFalse(record.get('current_original_receipt'))
        self.assertEqual(restored['ledger']['dispatches'], dispatches)
        self.assertEqual(restored['ledger']['used']['data_requests'], 2)
        self.assertEqual(restored['ledger']['used']['model_calls'], 0)
        self.assertEqual(len(restored['official_response_checkpoints']), 2)

    def test_interrupted_parser_restores_from_durable_bytes_without_network(self):
        def respond(task, parameters, phase, started, timeout, event, **options):
            event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
            return CN_ISSUER if phase == 'lookup' else cn_raw()
        with patch('researchhelper.official_sources.parse_catalogue', side_effect=SystemExit('Simulated process interruption')):
            with self.assertRaises(SystemExit):
                self.run_catalogue(respond)
        interrupted = self.archive.get(self.task['id'])
        self.assertEqual(interrupted['status'], '运行中')
        self.assertEqual(len(interrupted['official_response_checkpoints']), 2)
        self.agent.close_runtime()
        self.archive = ResearchArchive(self.archive.root)
        self.agent = ResearchAgent(self.archive, ROOT, offline=True,
            draft_first=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.agent.recover_interrupted()
        self.assertEqual(self.archive.get(self.task['id'])['status'], '失败')
        restored = self.run_catalogue(lambda *a, **k: self.fail('Interrupted parser must reuse bytes'))
        self.assertEqual(len(restored['source_records']), 1)
        self.assertEqual(restored['ledger']['used']['data_requests'], 2)
        self.assertEqual(restored['ledger']['used']['model_calls'], 0)
        self.assertIsNone(restored.get('error'))

    def test_response_is_durable_before_directory_parsing(self):
        observed = []
        def parse(*args, **kwargs):
            if kwargs.get('channel') == 'cninfo':
                stored = ResearchArchive(self.archive.root).get(self.task['id'])
                checkpoint = retained_response(stored, self.need['parameters'], 'catalogue')
                self.assertIsNotNone(checkpoint)
                self.assertEqual(checkpoint['payload'], cn_raw())
                self.assertEqual(stored['ledger']['used']['data_requests'], 2)
                observed.append(stored['status'])
            return parse_catalogue(*args, **kwargs)
        def respond(task, parameters, phase, started, timeout, event, **options):
            event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
            return CN_ISSUER if phase == 'lookup' else cn_raw()
        with patch('researchhelper.official_sources.parse_catalogue', side_effect=parse):
            result = self.run_catalogue(respond)
        self.assertTrue(observed)
        self.assertEqual(observed[0], '运行中')
        self.assertEqual(len(result['official_response_checkpoints']), 2)
        self.assertEqual(len(result['source_records']), 1)

    def test_tampered_foreign_or_incomplete_response_is_not_reusable(self):
        cancelled = self.cancelled_after('catalogue')
        for change in ('task', 'version', 'bytes', 'hash', 'identity', 'request', 'timestamp',
                       'no_dispatch', 'no_byte_receipt', 'incomplete_transport'):
            task = copy.deepcopy(cancelled)
            checkpoint = task['official_response_checkpoints'][-1]
            event = task['ledger']['dispatches'][-1]
            if change == 'task': checkpoint['task_id'] = 'other-task'
            elif change == 'version': checkpoint['research_version'] += 1
            elif change == 'bytes': checkpoint['original_base64'] = base64.b64encode(b'{}').decode()
            elif change == 'hash': checkpoint['sha256'] = '0' * 64
            elif change == 'identity': checkpoint['id'] = 'other-response'
            elif change == 'request': checkpoint['dispatch_fingerprint'] = 'other-query'
            elif change == 'timestamp': checkpoint.pop('obtained_at')
            elif change == 'no_dispatch': task['ledger']['dispatches'].pop()
            elif change == 'no_byte_receipt': event.pop('response_sha256')
            else: event['network_worker_reaped'] = False
            before = fingerprint(task)
            with self.subTest(change=change):
                self.assertIsNone(retained_response(task, self.need['parameters'], 'catalogue'))
                self.assertIsNone(catalogue_recovery(task, self.need['parameters'], main_dispatched=True))
                self.assertEqual(fingerprint(task), before)

    def test_missing_issuer_does_not_repeat_a_completed_directory_request(self):
        cancelled = self.cancelled_after('catalogue')
        cancelled['official_response_checkpoints'] = [cancelled['official_response_checkpoints'][-1]]
        self.archive.save(cancelled)
        with patch('researchhelper.source_transport.official_announcement') as transport:
            restored = self.agent.start(self.task['id'],
                dict(data_requests=[self.need], draft_report=None), defer_delivery=True)
        transport.assert_not_called()
        self.assertFalse(restored['source_records'])
        self.assertEqual(restored['ledger']['used']['data_requests'], 2)
        self.assertEqual(restored['ledger']['used']['model_calls'], 0)

    def test_changed_query_keeps_issuer_but_does_not_reuse_another_directory(self):
        cancelled = self.cancelled_after('catalogue')
        original = cancelled['official_response_checkpoints'][-1]
        self.need = copy.deepcopy(self.need)
        self.need['parameters']['end_date'] = '2026-10-16'
        phases = []
        def respond(task, parameters, phase, started, timeout, event, **options):
            phases.append(phase)
            event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
            return cn_raw()
        restored = self.run_catalogue(respond)
        self.assertEqual(phases, ['catalogue'])
        self.assertEqual(restored['ledger']['used']['data_requests'], 3)
        record = restored['source_records'][0]
        self.assertEqual(record['snapshot']['query_scope'], self.need['parameters'])
        self.assertNotEqual(record['request_hash'], original['dispatch_fingerprint'])
        self.assertNotIn('response_recovery', record)

    def test_repeated_invalid_directory_is_retained_but_not_redownloaded(self):
        def respond(task, parameters, phase, started, timeout, event, **options):
            event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
            return CN_ISSUER if phase == 'lookup' else b'{}'
        failed = self.run_catalogue(respond)
        self.assertFalse(failed['source_records'])
        self.assertEqual(failed['ledger']['used']['data_requests'], 2)
        self.assertEqual(base64.b64decode(failed['official_response_checkpoints'][-1]['original_base64']), b'{}')
        restored = self.run_catalogue(lambda *a, **k: self.fail('Invalid retained reply is not a network retry'))
        self.assertFalse(restored['source_records'])
        self.assertEqual(restored['ledger']['used']['data_requests'], 2)
        self.assertEqual(len(restored['official_response_checkpoints']), 2)


if __name__ == '__main__':
    unittest.main()
