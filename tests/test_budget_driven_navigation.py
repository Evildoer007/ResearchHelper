"""Initial navigation follows progress and total budget, not three decisions."""
import copy
import unittest
from unittest.mock import patch

from tests import test_initial_acquisition_navigation as fixture
from researchhelper import acquisition
from researchhelper.contracts import ContractError


class BudgetDrivenNavigationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.InitialAcquisitionNavigationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = self.fixture.task
        self.task['acquisition_budget_navigation_version'] = 1
        self.task['ledger']['used']['model_calls'] = 1  # Existing planning cost.
        self.fixture.decisions = [self.fixture.proposal(operation) for operation in
            ('get_thscode', 'report_query', 'report_query', 'filing_original')]
        patch.object(acquisition, 'extractable_source_views', side_effect=lambda task:
            [dict(id='original')] if self.fixture.stage >= 4 else []).start()
        patch.object(acquisition, 'navigation_progress', side_effect=lambda task: (
            {'mapped'} if self.fixture.stage >= 1 else set(),
            {str(self.fixture.stage)} if self.fixture.stage >= 2 else set(),
            {'original'} if self.fixture.stage >= 4 else set())).start()

    def test_fourth_progressing_decision_keeps_extraction_analysis_and_review_calls(self):
        before = copy.deepcopy(self.task['ledger']['limits'])
        self.fixture.run_navigation()
        self.assertEqual(self.fixture.model.call_count, 4)
        self.assertEqual(self.task['reasoning_state']['acquisition']['status'], 'complete')
        self.assertEqual(self.task['ledger']['used']['model_calls'], 5)
        self.assertEqual(self.task['ledger']['limits'], before)
        self.assertEqual(self.task['ledger']['supplements'], 0)
        self.assertEqual(self.fixture.model.call_args.args[3]['navigation']['maximum'], 4)

    def test_remaining_delivery_work_is_not_spent_on_a_fifth_navigation(self):
        self.task['ledger']['used']['model_calls'] = 5
        self.fixture.run_navigation()
        self.fixture.model.assert_not_called()
        self.assertEqual(self.task['ledger']['used']['model_calls'], 5)
        self.assertEqual(self.task['reasoning_state']['acquisition']['status'], 'stopped')

    def test_no_progress_still_stops_without_replay_or_budget_reset(self):
        self.fixture.engine._acquire_sources.side_effect = None
        self.fixture.run_navigation()
        self.fixture.run_navigation()
        self.assertEqual(self.fixture.model.call_count, 1)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 2)

    def test_explicit_stop_still_wins_over_unused_budget(self):
        self.fixture.decisions = [dict(data_requests=[], stop_reason='已有检索不足，不继续无依据查询')]
        self.fixture.run_navigation()
        self.fixture.engine._acquire_sources.assert_not_called()
        self.assertEqual(self.fixture.model.call_count, 1)

    def test_retained_legacy_tasks_keep_their_three_decision_checkpoint_rules(self):
        self.task.pop('acquisition_budget_navigation_version')
        self.fixture.run_navigation()
        self.assertEqual(self.fixture.model.call_count, 3)
        self.assertEqual(self.task['reasoning_state']['acquisition']['status'], 'stopped')

    def test_unknown_or_boolean_policy_cannot_open_more_navigation(self):
        for version in (True, 2, '1'):
            self.task['acquisition_budget_navigation_version'] = version
            with self.subTest(version=version), self.assertRaises(ContractError):
                self.fixture.run_navigation()
        self.fixture.model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
