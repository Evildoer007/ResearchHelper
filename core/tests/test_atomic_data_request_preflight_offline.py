"""A bad request cannot consume an earlier request or mutate a host batch."""
import copy
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import snapshot_record


class AtomicDataRequestPreflightTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='atomic-data-preflight-')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), offline=False)
        self.task = self.agent.create('核对510500.SH指定两日收盘价', objects=['510500.SH'],
            research_mode='current', questions=[
                {'id': 'prices', 'question': '两日收盘价是多少', 'kind': 'fact'}])
        self.parameters = {'codes': '510500.SH', 'indicators': 'close',
            'startdate': '2026-09-28', 'enddate': '2026-09-29',
            'functionpara': {'Currency': 'RMB', 'CPS': '1', 'Fill': 'Omit'}}
        self.need = {'id': 'prices', 'source': 'ifind', 'operation': 'cmd_history_quotation',
                     'parameters': self.parameters, 'question_ids': ['prices']}
        guard = patch('socket.socket.connect', side_effect=AssertionError('No live calls in this test'))
        guard.start()
        self.addCleanup(guard.stop)

    def raw_record(self):
        return snapshot_record(self.task, 'ifind', 'cmd_history_quotation', self.parameters,
            {'errorcode': 0, 'tables': [{'thscode': '510500.SH',
             'time': ['2026-09-28', '2026-09-29'], 'table': {'close': [1.0, 1.1]}}]})

    def test_invalid_later_request_prevents_the_first_dispatch(self):
        invalid = dict(copy.deepcopy(self.need), id='other', reason='not registered')
        self.task.update(status='运行中', batch=1)
        before = copy.deepcopy(self.task)
        with patch.object(self.agent.collector, 'external', return_value=self.raw_record()) as transport, \
                patch.object(self.agent, '_save_model_checkpoint') as checkpoint, \
                patch.object(self.agent, '_journal') as journal:
            with self.assertRaises(ContractError):
                self.agent._acquire_sources(self.task, [self.need, invalid])
        transport.assert_not_called()
        checkpoint.assert_not_called()
        journal.assert_not_called()
        self.assertEqual(self.task, before)

    def test_duplicate_ids_are_rejected_before_dispatch_or_alias_mutation(self):
        self.task.update(status='运行中', batch=1)
        record = self.raw_record()
        self.task['source_records'].append(record)
        before = copy.deepcopy(self.task)
        with patch.object(self.agent.collector, 'external') as transport, \
                patch.object(self.agent, '_save_model_checkpoint') as checkpoint, \
                patch.object(self.agent, '_journal') as journal:
            with self.assertRaises(ContractError):
                self.agent._acquire_sources(self.task, [self.need, copy.deepcopy(self.need)])
        transport.assert_not_called()
        checkpoint.assert_not_called()
        journal.assert_not_called()
        self.assertEqual(self.task, before)

    def reject_host_without_mutation(self, requests):
        before = copy.deepcopy(self.agent.archive.get(self.task['id']))
        with patch.object(self.agent.collector, 'external') as transport:
            with self.assertRaises(ContractError):
                self.agent.start(self.task['id'], {'data_requests': requests})
        transport.assert_not_called()
        self.assertEqual(self.agent.archive.get(self.task['id']), before)
        self.assertEqual(self.agent._get(self.task['id']), before)

    def test_missing_id_is_rejected_before_host_batch_starts(self):
        invalid = copy.deepcopy(self.need)
        invalid.pop('id')
        self.reject_host_without_mutation([invalid])

    def test_unknown_field_is_rejected_before_host_batch_starts(self):
        self.reject_host_without_mutation([dict(self.need, reason='not registered')])

    def test_wrong_security_is_rejected_before_host_batch_starts(self):
        invalid = copy.deepcopy(self.need)
        invalid['parameters']['codes'] = '510300.SH'
        self.reject_host_without_mutation([invalid])

    def test_invalid_resume_preserves_prior_budget_and_supplement_count(self):
        self.task.update(status='失败', batch=1)
        self.task['ledger']['used']['data_requests'] = 2
        self.task['ledger']['used']['model_calls'] = 1
        self.agent.archive.save(self.task)
        self.reject_host_without_mutation([dict(self.need, reason='not registered')])


if __name__ == '__main__':
    unittest.main()
