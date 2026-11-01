"""Process readable financial originals before exhausting unrelated searches."""
import copy
import unittest
from unittest.mock import patch

from tests import test_initial_acquisition_navigation as fixtures
from researchhelper import acquisition
from researchhelper.financial_sources import progressive_financial_source_ready
from researchhelper.contracts import ContractError


class ProgressiveFinancialAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InitialAcquisitionNavigationTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = self.fixture.task
        self.task['acquisition_budget_navigation_version'] = 1
        self.task['progressive_financial_acquisition_version'] = 1
        self.fixture.engine._acquire_sources.side_effect = lambda task, needs, **kwargs: self.fixture.acquire(task, needs)
        self.task['questions'].append(dict(id='q2', required=True, evidence_needs=['完整行业分类']))
        self.fixture.decisions.append(dict(data_requests=[], stop_reason='合成夹具：分类无适用渠道'))
        self.coverage_patch = patch.object(acquisition, 'initial_acquisition_coverage', side_effect=lambda task:dict(
            ready=False, pending_original_question_ids=['q1', 'q2'], pending_discovery_question_ids=['q2'],
            readable_financial_batch_question_ids=['q1'] if self.fixture.stage >= 3 else []))
        self.coverage_patch.start(); self.addCleanup(self.coverage_patch.stop)

    def test_readable_financial_branch_is_processed_without_a_fourth_navigation_call(self):
        limits = copy.deepcopy(self.task['ledger']['limits'])
        self.fixture.run_navigation()
        self.assertEqual(self.fixture.model.call_count, 3)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 3)
        self.assertEqual(self.task['ledger']['limits'], limits)
        state = self.task['reasoning_state']['acquisition']
        self.assertFalse(state['progressive_processing']['source_coverage_complete'])
        self.assertFalse(state['progressive_processing']['financial_approval'])
        self.assertIn('其他公司、口径及行业完整性', state['stop_reason'])

    def test_legacy_navigation_keeps_the_old_continuation(self):
        self.task.pop('progressive_financial_acquisition_version')
        self.fixture.run_navigation()
        self.assertEqual(self.fixture.model.call_count, 4)
        self.assertNotIn('progressive_processing', self.task['reasoning_state']['acquisition'])

    def test_a_raw_batch_alone_never_counts_as_a_readable_original(self):
        self.assertFalse(progressive_financial_source_ready(self.task, dict(readable_financial_batch_question_ids=[])))

    def test_unknown_policy_cannot_enable_progressive_processing(self):
        for value in (True, '1', 2):
            with self.subTest(value=value), self.assertRaises(ContractError):
                progressive_financial_source_ready(dict(self.task, progressive_financial_acquisition_version=value),
                    dict(readable_financial_batch_question_ids=['q1']))
