"""One fresh submission for a known complete JSON failure, not network retry."""
import copy
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from tests.legacy_replay_fixtures import financial_task
from researchhelper.argument_support import VERSION
from researchhelper.contracts import ContractError,charge
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelCompletionError

class CompletedAnalysisFormatRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.task = financial_task()
        self.task['argument_support_contract'] = VERSION
        self.task['ledger']['used'].update(model_calls=6,seconds=200)
        self.task['ledger']['dispatches'].append(dict(kind='model_calls', batch=self.task['batch'],
            research_phase='analyze', status='失败', failure_code='structured_output_rejected',
            provider_completion_status='completed', response_body_complete=True, finish_reason='stop',
            structured_output_diagnostic=dict(json_error='expected_comma')))
        self.agent=ResearchAgent.__new__(ResearchAgent)
        self.agent.model=SimpleNamespace(supports_final_delivery=lambda:True,reason_final_analysis=lambda:None)
        self.agent._save_model_checkpoint=Mock()
        self.result=dict(answers={'q1':dict(answer='隔离流程夹具，不是研究结论。',refs=[],calculation_refs=[])},theses=[])
        def corrected(task,selected,*,final_delivery):
            self.assertFalse(final_delivery)
            charge(task['ledger'],'model_calls')
            return copy.deepcopy(self.result)
        self.agent._reason_on_verified_material=Mock(side_effect=corrected)
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))
        guard.start();self.addCleanup(guard.stop)

    def recover(self):return self.agent._recover_completed_analysis_format(self.task)

    def test_complete_json_failure_is_eligible(self):
        issue=self.agent._completed_analysis_format_issue(self.task)
        self.assertEqual(issue['json_error'],'expected_comma')
        self.assertFalse(issue['original_text_retained']);self.assertFalse(issue['automatic_repair'])

    def test_resubmission_uses_seventh_call_and_leaves_eighth_for_review(self):
        limits=copy.deepcopy(self.task['ledger']['limits'])
        self.assertEqual(self.recover(),self.result)
        self.assertEqual(self.task['ledger']['used']['model_calls'],7)
        self.assertEqual(self.task['ledger']['limits'],limits)
        self.assertEqual(self.task['reasoning_state']['numeric_resubmission']['status'],'已提交')
        charge(self.task['ledger'],'model_calls',delivery=True)
        with self.assertRaises(ContractError):charge(self.task['ledger'],'model_calls',delivery=True)

    def test_last_remaining_call_is_not_spent_on_unreviewable_prose(self):
        self.task['ledger']['used']['model_calls']=7
        self.assertIsNone(self.recover());self.agent._reason_on_verified_material.assert_not_called()

    def test_delivery_time_reserve_is_not_spent(self):
        self.task['ledger']['used']['seconds']=550
        self.assertIsNone(self.recover());self.agent._reason_on_verified_material.assert_not_called()

    def test_timeout_unknown_incomplete_truncation_refusal_and_access_failure_do_not_retry(self):
        event=self.task['ledger']['dispatches'][-1]
        original=copy.deepcopy(event)
        for values in (dict(status='已派发'),dict(response_body_complete=False),
                       dict(provider_completion_status='incomplete'),dict(finish_reason='length'),
                       dict(failure_code='response_deadline_exceeded'),dict(failure_code='provider_refusal'),
                       dict(failure_code='http_failure'),dict(research_phase='extract')):
            event.clear();event.update(original);event.update(values)
            with self.subTest(values=values):self.assertIsNone(self.recover())
        self.agent._reason_on_verified_material.assert_not_called()

    def test_a_second_failed_submission_does_not_loop(self):
        def failure(task,*args,**kwargs):
            charge(task['ledger'],'model_calls')
            raise ModelCompletionError('Isolated rejected resubmission')
        self.agent._reason_on_verified_material.side_effect=failure
        self.assertIsNone(self.recover());self.assertIsNone(self.recover())
        self.assertEqual(self.agent._reason_on_verified_material.call_count,1)
        self.assertEqual(self.task['reasoning_state']['numeric_resubmission']['status'],'未采用')

    def test_unknown_saved_resubmission_is_not_repeated_after_resume(self):
        self.agent._reason_on_verified_material.side_effect=ContractError('Isolated cancellation')
        with self.assertRaises(ContractError):self.recover()
        self.assertEqual(self.task['reasoning_state']['numeric_resubmission']['status'],'已派发')
        self.task['batch']+=1
        self.assertIsNone(self.recover())
        self.assertEqual(self.agent._reason_on_verified_material.call_count,1)

    def test_numeric_and_json_corrections_share_one_slot(self):
        self.task['reasoning_state']['numeric_resubmission']=dict(status='已提交',reason='numeric_submission_rejected')
        self.assertIsNone(self.recover());self.agent._reason_on_verified_material.assert_not_called()

    def test_resubmission_does_not_approve_facts_or_answers(self):
        fields=('evidence','calculations','questions','theses')
        before={key:copy.deepcopy(self.task[key]) for key in fields}
        self.recover()
        self.assertEqual(before,{key:self.task[key] for key in fields})
        self.assertFalse(self.task['reasoning_state']['rejected_analysis_submissions'][-1]['evidence_approved'])

    def test_feedback_contains_finite_issue_not_rejected_text_or_a_new_plan(self):
        payloads=[]
        def capture(model,task,phase,payload,fields,**kwargs):
            payloads.append(payload)
            raise ModelCompletionError('Isolated capture')
        self.agent._reason_on_verified_material=ResearchAgent._reason_on_verified_material.__get__(self.agent)
        with patch('researchhelper.engine.model_object',side_effect=capture):self.recover()
        feedback=payloads[0]['submission_feedback']
        self.assertEqual(feedback['issue']['json_error'],'expected_comma')
        self.assertFalse(feedback['rejected_proposal_available'])
        self.assertNotIn('rejected_proposal',feedback)
        self.assertEqual(feedback['remaining_resubmissions'],0)

    def test_legacy_tasks_do_not_gain_automatic_resubmission(self):
        self.task.pop('argument_support_contract')
        self.assertIsNone(self.recover());self.agent._reason_on_verified_material.assert_not_called()

    def test_missing_or_unrecognized_diagnostic_is_not_eligible(self):
        event=self.task['ledger']['dispatches'][-1]
        for diagnostic in (None,{},dict(json_error='not-a-known-category'),dict(schema_issues=[])):
            event['structured_output_diagnostic']=diagnostic
            self.assertIsNone(self.recover())
        self.agent._reason_on_verified_material.assert_not_called()


if __name__=='__main__':unittest.main()
