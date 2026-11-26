"""Offline batch yielding; deferred work is neither lost nor called complete."""
import copy
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError, budget, fingerprint


class ProgressiveFinancialBatchExecutionTests(unittest.TestCase):
    def setUp(self):
        self.task = dict(id='batch-yield-fixture', version=1, batch=1,
            question='核对两家公司已披露的营业收入',
            progressive_financial_acquisition_version=1,
            source_records=[], source_attempts={},
            ledger=budget(dict(data_requests=100, model_calls=8, seconds=600)))
        self.needs = [dict(id=f'original-{index}', source='ifind', operation='filing_original',
            parameters=dict(fixture=index), question_ids=['q1']) for index in range(2)]
        self.prepared = [(copy.deepcopy(need), 'ifind', 'filing_original', need['parameters'])
            for need in self.needs]
        self.agent = ResearchAgent.__new__(ResearchAgent)
        self.agent._validated_data_needs = Mock(return_value=self.prepared)
        self.agent._save_model_checkpoint = Mock()
        self.agent._journal = Mock()
        self.agent._active = Mock()
        self.agent.source_credentials = {}
        self.agent.collector = SimpleNamespace(external=Mock(side_effect=self.collect))
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.register = self.stack.enter_context(patch('researchhelper.source_requirements.register', return_value=True))
        self.stack.enter_context(patch('researchhelper.evidence.validate_source_request', side_effect=lambda task, source, operation, params:params))
        self.stack.enter_context(patch('researchhelper.evidence.filing_selection_identity', side_effect=lambda task, params:str(params['fixture'])))
        self.stack.enter_context(patch('researchhelper.engine.supplement_request_blocked', return_value=False))
        self.calendars = self.stack.enter_context(patch('researchhelper.market_calendar.dependency_requests', return_value=[]))
        self.coverage = self.stack.enter_context(patch('researchhelper.acquisition.initial_acquisition_coverage',
            side_effect=lambda task:dict(readable_financial_batch_question_ids=['q1'] if task['source_records'] else [])))
        self.stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('No network permitted')))

    @staticmethod
    def collect(task, source, operation, params, credentials):
        snapshot = dict(fixture=params['fixture'])
        return dict(id=f"source-{params['fixture']}", kind='filing', operation=operation,
            task_id=task['id'], research_version=task['version'], status='成功待核验',
            parameters=params, snapshot=snapshot, hash=fingerprint(snapshot))

    def test_yield_before_second_original_preserves_every_requirement(self):
        before = copy.deepcopy(self.task['ledger'])
        result = self.agent._acquire_sources(self.task, self.needs, yield_on_readable_financial=True)
        self.assertEqual(self.agent.collector.external.call_count, 1)
        self.assertEqual(result['deferred_request_ids'], ['original-1'])
        self.assertEqual(self.register.call_args.args[1], self.needs)
        self.assertEqual(self.calendars.call_args.args[1], self.prepared[:1])
        self.assertEqual(self.task['ledger'], before)
        self.assertNotIn('evidence', self.task)

    def test_default_batch_keeps_existing_execution_order(self):
        result = self.agent._acquire_sources(self.task, self.needs)
        self.assertEqual(self.agent.collector.external.call_count, 2)
        self.assertEqual(result['deferred_request_ids'], [])
        self.assertEqual(self.calendars.call_args.args[1], self.prepared)
        self.coverage.assert_not_called()

    def test_unreadable_source_does_not_trigger_yield(self):
        self.coverage.side_effect = None
        self.coverage.return_value = dict(readable_financial_batch_question_ids=[])
        result = self.agent._acquire_sources(self.task, self.needs, yield_on_readable_financial=True)
        self.assertEqual(self.agent.collector.external.call_count, 2)
        self.assertEqual(result['deferred_request_ids'], [])

    def test_unknown_policy_rejects_before_dispatch(self):
        self.task['progressive_financial_acquisition_version'] = 2
        with self.assertRaises(ContractError):
            self.agent._acquire_sources(self.task, self.needs, yield_on_readable_financial=True)
        self.agent.collector.external.assert_not_called()


if __name__ == '__main__':
    unittest.main()
