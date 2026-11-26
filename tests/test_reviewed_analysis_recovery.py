"""Correction retains review budget; failed reasoning is not missing data."""
import copy
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.legacy_replay_fixtures import financial_task, numeric_proposal
from researchhelper.argument_support import VERSION
from researchhelper.contracts import ContractError, charge
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelCompletionError

class ReviewedAnalysisRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.task = financial_task()
        self.task['argument_support_contract'] = VERSION
        self.task['ledger']['used']['model_calls'] = 6
        self.task['reasoning_state'].pop('final_delivery', None)
        self.proposal = numeric_proposal(valid=False)
        self.agent = ResearchAgent.__new__(ResearchAgent)
        self.agent.model = SimpleNamespace(supports_final_delivery=lambda:True, reason_final_analysis=lambda:None)
        self.agent._save_model_checkpoint = Mock()
        self.result = {'answers':{}, 'theses':[]}
        def corrected(task, selected, *, final_delivery):
            self.assertFalse(final_delivery)
            self.assertEqual(task['reasoning_state']['numeric_resubmission']['status'], '已派发')
            charge(task['ledger'], 'model_calls')
            return copy.deepcopy(self.result)
        self.agent._reason_on_verified_material = Mock(side_effect=corrected)
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No network'))
        guard.start(); self.addCleanup(guard.stop)

    def test_correction_uses_ordinary_budget_and_retains_review_reserve(self):
        self.assertEqual(self.agent._finalize_numeric_submission(self.task, self.proposal), self.result)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 7)
        self.assertNotIn('final_delivery', self.task['reasoning_state'])
        self.assertEqual(self.task['reasoning_state']['numeric_resubmission']['status'], '已提交')
        charge(self.task['ledger'], 'model_calls', delivery=True)
        with self.assertRaises(ContractError): charge(self.task['ledger'], 'model_calls', delivery=True)

    def test_last_call_is_not_wasted_on_an_unreviewable_correction(self):
        self.task['ledger']['used']['model_calls'] = 7
        self.assertIs(self.agent._finalize_numeric_submission(self.task, self.proposal), self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()
        self.assertNotIn('numeric_resubmission', self.task['reasoning_state'])

    def test_already_reserved_review_leaves_no_new_correction_slot(self):
        for status in ('已派发', '已提交', '未采用'):
            task = copy.deepcopy(self.task)
            task['reasoning_state']['final_delivery'] = dict(status=status, reason='argument_support')
            with self.subTest(status=status):
                self.assertIs(self.agent._finalize_numeric_submission(task, self.proposal), self.proposal)
                self.assertNotIn('numeric_resubmission', task['reasoning_state'])
        self.agent._reason_on_verified_material.assert_not_called()

    def test_correction_does_not_touch_evidence_calculations_or_original_proposal(self):
        before = copy.deepcopy((self.task['evidence'], self.task['calculations'], self.proposal))
        self.agent._finalize_numeric_submission(self.task, self.proposal)
        self.assertEqual((self.task['evidence'], self.task['calculations'], self.proposal), before)
        record = self.task['reasoning_state']['rejected_analysis_submissions'][-1]
        self.assertFalse(record['automatic_repair']); self.assertFalse(record['evidence_approved'])

    def test_same_version_correction_is_not_repeated_after_resume(self):
        self.agent._finalize_numeric_submission(self.task, self.proposal)
        self.task['batch'] += 1
        self.agent._finalize_numeric_submission(self.task, self.proposal)
        self.agent._reason_on_verified_material.assert_called_once()

    def test_unknown_checkpoint_attempt_is_not_redispatched(self):
        self.task['reasoning_state']['numeric_resubmission'] = {'status':'已派发'}
        self.agent._finalize_numeric_submission(self.task, self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_known_model_failure_is_preserved_without_loop(self):
        self.agent._reason_on_verified_material.side_effect = ModelCompletionError('Synthetic completion failure')
        self.agent._finalize_numeric_submission(self.task, self.proposal)
        self.assertEqual(self.task['reasoning_state']['numeric_resubmission']['status'], '未采用')
        self.agent._finalize_numeric_submission(self.task, self.proposal)
        self.agent._reason_on_verified_material.assert_called_once()

    def test_time_reserve_cannot_be_spent_on_correction(self):
        self.task['ledger']['used']['seconds'] = 550
        self.agent._finalize_numeric_submission(self.task, self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_ordinary_correction_receives_explicit_unchanged_data_feedback(self):
        payloads = []
        def capture(model, task, phase, payload, fields, **kwargs):
            payloads.append(payload)
            raise ModelCompletionError('No network fixture')
        self.agent._reason_on_verified_material = ResearchAgent._reason_on_verified_material.__get__(self.agent)
        with patch('researchhelper.engine.model_object', side_effect=capture):
            self.agent._finalize_numeric_submission(self.task, self.proposal)
        self.assertEqual(len(payloads), 1)
        feedback = payloads[0]['submission_feedback']
        self.assertEqual(feedback['remaining_resubmissions'], 0)
        self.assertEqual(feedback['rejected_proposal'], self.proposal)
        self.assertFalse(feedback['automatic_repair'])

    def test_truncated_analysis_is_not_reported_as_unacquired_financial_values(self):
        task = financial_task()
        task['argument_support_contract'] = VERSION
        task['reasoning_state']['analysis'] = {'output_rejected': True}
        task['gaps'] = []; task['gap_records'] = []
        before = copy.deepcopy((task['evidence'], task['calculations']))
        self.agent._answer_questions(task, [q['id'] for q in task['questions']], task['host_result'], {})
        self.assertTrue(all(q['status']=='未回答' for q in task['questions']))
        self.assertTrue(any('分析未完成' in gap for gap in task['gaps']))
        self.assertFalse(any('未覆盖' in gap for gap in task['gaps']))
        self.assertEqual((task['evidence'], task['calculations']), before)


if __name__=='__main__': unittest.main()
