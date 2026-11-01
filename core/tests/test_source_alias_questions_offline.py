"""One retained response may satisfy multiple explicit needs, not multiple sources."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from tests import test_filing_workflow_offline as filing_fixture

ROOT = Path(__file__).resolve().parents[2]


class SourceAliasQuestionsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='source-alias-', dir=ROOT / 'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=False,
            source_credentials={'ifind': {'access_token': 'synthetic-only-token'}})
        self.task = self.agent.create('核对同一主体两个独立公告事项', entry='个股', objects=['600000.SH'],
            cutoff='2026-10-02T18:00:00+08:00', questions=[
                {'id': 'q1', 'question': '核对公告标题', 'kind': 'fact'},
                {'id': 'q2', 'question': '核对原件条款', 'kind': 'fact', 'group': 'independent'},
                {'id': 'q3', 'question': '未登记的其他取数事项', 'kind': 'fact', 'group': 'other'}])
        self.task.update(status='运行中', batch=1)
        self.needs = [dict(id='catalog_'+q, source='ifind', operation='report_query',
            parameters=copy.deepcopy(filing_fixture.PARAMETERS), question_ids=[q]) for q in ('q1', 'q2')]
        self.task['data_need_specs'] = copy.deepcopy(self.needs)
        self.task['host_result'] = {'data_requests': copy.deepcopy(self.needs)}
        self.agent.archive.save(self.task)
        self.fixture = filing_fixture.FilingWorkflowTests()
        self.fixture.agent = self.agent
        self.fixture.dispatches = []
        network = patch('socket.socket.connect', side_effect=AssertionError('External network forbidden'))
        network.start()
        self.addCleanup(network.stop)

    def acquire(self, needs=None):
        with patch.object(self.agent.collector, 'external', side_effect=self.fixture.mocked_transport) as transport:
            self.agent._acquire_sources(self.task, self.needs if needs is None else needs)
        return transport

    def test_cached_response_retains_both_explicit_question_bindings(self):
        transport = self.acquire()
        transport.assert_called_once()
        record = self.task['source_records'][0]
        self.assertEqual(record['data_need_ids'], ['catalog_q1', 'catalog_q2'])
        self.assertEqual(record['question_ids'], ['q1', 'q2'])
        self.assertEqual(self.task['ledger']['used']['data_requests'], 1)
        self.assertEqual(len(self.task['source_records']), 1)
        self.assertEqual(fingerprint(record['snapshot']), record['hash'])

    def test_unanswered_alias_can_select_original_without_requerying_catalog(self):
        self.acquire()
        self.task['questions'][0]['status'] = '已回答'
        self.task['questions'][1]['status'] = '未回答'
        self.task['questions'][2]['status'] = '已回答'
        candidates = self.agent._supplement_options(self.task)['filing_candidates']
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['question_ids'], ['q2'])
        self.assertEqual(candidates[0]['catalog_id'], self.task['source_records'][0]['id'])
        self.assertEqual(candidates[0]['source_id'], '10001')
        self.assertEqual(self.task['ledger']['used']['data_requests'], 1)

    def test_repeated_alias_is_idempotent_and_does_not_extend_unrequested_questions(self):
        self.acquire()
        before = copy.deepcopy(self.task['ledger'])
        self.acquire(list(reversed(self.needs))).assert_not_called()
        record = self.task['source_records'][0]
        self.assertEqual(record['question_ids'], ['q1', 'q2'])
        self.assertNotIn('q3', record['question_ids'])
        self.assertEqual(record['data_need_ids'], ['catalog_q1', 'catalog_q2'])
        self.assertEqual(self.task['ledger'], before)

    def test_restart_preserves_aliases_and_snapshot_without_new_dispatch(self):
        self.acquire()
        self.agent.archive.save(self.task)
        prior = copy.deepcopy(self.task['source_records'][0])
        self.agent = ResearchAgent(ResearchArchive(self.agent.archive.root), ROOT, offline=False)
        self.task = self.agent.archive.get(self.task['id'])
        with patch.object(self.agent.collector, 'external', side_effect=AssertionError('Unexpected replay')):
            self.agent._acquire_sources(self.task, self.needs)
        self.assertEqual(self.task['source_records'][0], prior)
        self.assertEqual(self.task['source_records'][0]['question_ids'], ['q1', 'q2'])

    def test_failed_response_is_not_promoted_by_alias(self):
        self.acquire([self.needs[0]])
        self.task['source_records'][0]['status'] = '权限不足'
        self.task['ledger']['dispatches'][0].update(status='权限不足', failure_code='permission_denied')
        with patch.object(self.agent.collector, 'external', side_effect=AssertionError('Permission retry')):
            self.agent._acquire_sources(self.task, [self.needs[1]])
        self.assertEqual(self.task['source_records'][0]['question_ids'], ['q1'])
        self.assertEqual(self.task['source_records'][0]['data_need_ids'], ['catalog_q1'])
        self.assertEqual(self.task['ledger']['used']['data_requests'], 1)

    def test_unknown_question_is_rejected_before_cache_alias(self):
        self.acquire([self.needs[0]])
        need = copy.deepcopy(self.needs[1]); need['question_ids'] = ['other_task_question']
        before = copy.deepcopy(self.task['source_records'])
        with self.assertRaises(ContractError):
            self.acquire([need])
        self.assertEqual(self.task['source_records'], before)


if __name__ == '__main__':
    unittest.main()
