"""One bounded author resubmission after a known completed plan format error."""
import copy
import json
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from researchhelper.contracts import ContractError,charge,fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelCompletionError
from researchhelper.reasoning import planning_digest

ROOT=Path(__file__).resolve().parents[1]
RETAINED=ROOT/'result/acceptance/one-sentence-research-vm0gp5kr/task.json'


class CompletedPlanningFormatRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.original=RETAINED.read_bytes();self.task=json.loads(self.original)
        self.task.update(status='运行中',planning_submission_recovery_version=1)
        self.task.setdefault('reasoning_state',{})
        self.agent=ResearchAgent.__new__(ResearchAgent)
        self.agent.model=SimpleNamespace()
        self.agent._active=Mock(return_value=self.task)
        self.agent._save_model_checkpoint=Mock()
        self.payload=dict(question=self.task['question'],questions=copy.deepcopy(self.task['questions']),
            untrusted_sources=[])
        self.digest=planning_digest(self.task)
        self.index=len(self.task['ledger']['dispatches'])-1
        self.limits=copy.deepcopy(self.task['ledger']['limits'])
        self.proposal=dict(questions=[],data_requests=[],calculations=[])
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(),self.original)
        self.assertEqual(self.task['ledger']['limits'],self.limits)

    def submit(self,model,task,phase,payload,fields):
        self.assertEqual(phase,'plan');self.assertEqual(payload,self.payload)
        self.assertEqual(fields,{'questions','data_requests','calculations'})
        self.assertEqual(task['reasoning_state']['planning_submission_recovery']['status'],'已派发')
        self.agent._save_model_checkpoint.assert_called()
        charge(task['ledger'],'model_calls')
        return copy.deepcopy(self.proposal),dict(phase='plan')

    def recover(self):
        return self.agent._recover_completed_planning_format(self.task,self.payload,self.digest,
            failed_dispatch_index=self.index)

    def test_completed_actual_schema_rejection_can_submit_once_without_new_data(self):
        original=copy.deepcopy(self.task['ledger']['dispatches'])
        with patch('researchhelper.engine.model_object',side_effect=self.submit) as call:
            self.assertEqual(self.recover()[0],self.proposal)
        self.assertEqual(call.call_count,1)
        self.assertEqual(self.task['ledger']['used']['model_calls'],2)
        self.assertEqual(self.task['ledger']['used']['data_requests'],0)
        self.assertEqual(self.task['ledger']['dispatches'],original)
        state=self.task['reasoning_state']['planning_submission_recovery']
        self.assertEqual(state['status'],'已提交');self.assertFalse(state['automatic_repair'])
        self.assertEqual(state['payload_digest'],fingerprint(self.payload))

    def test_legacy_and_closed_tasks_are_not_reopened(self):
        for change in ('legacy','failed','cancelled','completed'):
            task=copy.deepcopy(self.task)
            if change=='legacy':task.pop('planning_submission_recovery_version')
            else:task['status']={'failed':'失败','cancelled':'已取消','completed':'已完成'}[change]
            with self.subTest(change=change),patch('researchhelper.engine.model_object') as call:
                self.assertIsNone(self.agent._recover_completed_planning_format(task,self.payload,
                    planning_digest(task),failed_dispatch_index=self.index))
                call.assert_not_called()

    def test_unknown_truncated_timeout_http_or_wrong_schema_errors_are_not_retried(self):
        event=self.task['ledger']['dispatches'][-1];original=copy.deepcopy(event)
        changes=[dict(status='已派发'),dict(response_body_complete=False),
            dict(provider_completion_status='incomplete'),dict(finish_reason='length'),
            dict(failure_code='http_failure'),dict(failure_code='response_deadline_exceeded'),
            dict(research_phase='extract'),dict(batch=0),dict(output_schema_hash='stale'),
            dict(structured_failure_category='stale_identity_profile'),
            dict(structured_output_diagnostic={}),dict(output_protocol='strict_plan_v1')]
        for change in changes:
            event.clear();event.update(original);event.update(change)
            with self.subTest(change=change),patch('researchhelper.engine.model_object') as call:
                self.assertIsNone(self.recover());call.assert_not_called()

    def test_semantic_enum_pattern_and_resource_errors_do_not_gain_retry(self):
        event=self.task['ledger']['dispatches'][-1]
        for code in ('enum_mismatch','pattern_mismatch','array_limit','bound_mismatch','shape_limit'):
            event['structured_output_diagnostic']['schema_issues']=[dict(code=code,path='$.questions[*]')]
            with self.subTest(code=code),patch('researchhelper.engine.model_object') as call:
                self.assertIsNone(self.recover());call.assert_not_called()

    def test_delivery_call_and_time_reserves_are_kept(self):
        for remaining,seconds in ((2,0),(7,550)):
            self.task['ledger']['used'].update(model_calls=8-remaining,seconds=seconds)
            with self.subTest(remaining=remaining,seconds=seconds),patch('researchhelper.engine.model_object') as call:
                self.assertIsNone(self.recover());call.assert_not_called()

    def test_failure_must_be_the_just_attempted_dispatch_and_current_input(self):
        self.index-=1
        with patch('researchhelper.engine.model_object') as call:
            self.assertIsNone(self.recover());call.assert_not_called()
        self.index+=1;self.task['input_hash']='different-input'
        with patch('researchhelper.engine.model_object') as call:
            self.assertIsNone(self.recover());call.assert_not_called()

    def test_second_failure_is_saved_and_never_loops(self):
        def rejected(*args):
            charge(self.task['ledger'],'model_calls')
            raise ModelCompletionError('Second completed-format proposal failed')
        with patch('researchhelper.engine.model_object',side_effect=rejected) as call:
            with self.assertRaises(ModelCompletionError):self.recover()
            self.assertIsNone(self.recover())
        self.assertEqual(call.call_count,1)
        self.assertEqual(self.task['reasoning_state']['planning_submission_recovery']['status'],'未采用')

    def test_second_failure_is_reported_instead_of_the_old_format_error(self):
        with patch('researchhelper.engine.model_object',side_effect=ContractError('New transport failure')) as call:
            with self.assertRaisesRegex(ContractError,'New transport failure'):self.recover()
        self.assertEqual(call.call_count,1)
        self.assertEqual(self.task['reasoning_state']['planning_submission_recovery']['status'],'未采用')

    def test_dispatched_submitted_or_failed_markers_block_resume_duplication(self):
        for status in ('已派发','已提交','未采用'):
            self.task['reasoning_state']['planning_submission_recovery']=dict(version=1,status=status,input_digest=self.digest)
            with self.subTest(status=status),patch('researchhelper.engine.model_object') as call:
                self.assertIsNone(self.recover());call.assert_not_called()
                with self.assertRaises(ContractError):self.agent._prepare_model_plan(self.task['id'],self.task['batch'])
                call.assert_not_called()

    def test_cancel_is_checked_before_the_reserved_submission_is_dispatched(self):
        self.agent._active.side_effect=ContractError('Cancelled isolated run')
        with patch('researchhelper.engine.model_object') as call:
            with self.assertRaises(ContractError):self.recover()
        call.assert_not_called()

    def test_retry_does_not_approve_evidence_or_a_research_plan(self):
        with patch('researchhelper.engine.model_object',side_effect=self.submit):self.recover()
        self.assertEqual(self.task['evidence'],[]);self.assertEqual(self.task['calculations'],[])
        self.assertNotIn('plan',self.task['reasoning_state'])
        self.assertFalse(self.task.get('argument_support_reviews'))

    def test_diagnostic_accepts_no_private_unknown_field_names_or_values(self):
        event=self.task['ledger']['dispatches'][-1]
        event['structured_output_diagnostic']['schema_issues']=[dict(code='unknown_fields',path='$.questions[*]',value='PRIVATE')]
        with patch('researchhelper.engine.model_object') as call:
            self.assertIsNone(self.recover());call.assert_not_called()

    def prepare_call(self,*,second_invalid=False,first_valid=False):
        """Use the actual ordinary planner and validator, not a fake adoption."""
        retained=json.loads((ROOT/'result/acceptance/one-sentence-research-3s5f7b02/task.json').read_text())
        proposed=copy.deepcopy(retained['reasoning_state']['plan']['proposed'])
        event=copy.deepcopy(self.task['ledger']['dispatches'][-1])
        self.task['ledger']['used']['model_calls']=0
        self.task['ledger']['dispatches']=[]
        self.task['error']=None
        self.agent._adopt_method_knowledge=Mock()
        calls=[]
        def author(model,task,phase,payload,fields):
            calls.append(phase);charge(task['ledger'],'model_calls')
            if len(calls)==1 and not first_valid:
                task['ledger']['dispatches'].append(copy.deepcopy(event))
                raise ModelCompletionError('Known completed plan structure rejection')
            result=copy.deepcopy(proposed)
            if second_invalid or first_valid:result['questions'][0]['question']='A different research question'
            task['ledger']['dispatches'].append(dict(kind='model_calls',research_phase='plan',status='成功',batch=task['batch']))
            return result,dict(phase='plan')
        return author,calls,proposed

    def test_normal_planner_recovers_then_runs_the_original_business_validator(self):
        author,calls,proposed=self.prepare_call()
        with patch('researchhelper.engine.model_object',side_effect=author):
            self.agent._prepare_model_plan(self.task['id'],self.task['batch'])
        self.assertEqual(calls,['plan','plan'])
        self.assertEqual(self.task['question'],self.payload['question'])
        self.assertEqual(self.task['data_need_specs'],proposed['data_requests'])
        self.assertEqual(self.task['ledger']['used']['data_requests'],0)
        self.assertEqual(self.task['ledger']['used']['model_calls'],2)
        self.assertIsNone(self.task['error']);self.assertEqual(self.task['gaps'],[])
        self.assertEqual(self.task['reasoning_state']['plan']['adopted_input_hash'],self.task['input_hash'])
        with patch('researchhelper.engine.model_object') as call:
            self.agent._prepare_model_plan(self.task['id'],self.task['batch'])
        call.assert_not_called()

    def test_corrected_format_cannot_change_the_original_question(self):
        author,calls,_=self.prepare_call(second_invalid=True)
        with patch('researchhelper.engine.model_object',side_effect=author):
            with self.assertRaises(ContractError):self.agent._prepare_model_plan(self.task['id'],self.task['batch'])
        self.assertEqual(calls,['plan','plan'])
        self.assertNotIn('plan',self.task['reasoning_state'])
        self.assertFalse(self.task['reasoning_state']['rejected_plan']['adopted'])

    def test_valid_format_with_bad_business_plan_does_not_request_a_second_author(self):
        author,calls,_=self.prepare_call(first_valid=True)
        with patch('researchhelper.engine.model_object',side_effect=author):
            with self.assertRaises(ContractError):self.agent._prepare_model_plan(self.task['id'],self.task['batch'])
        self.assertEqual(calls,['plan'])
        self.assertNotIn('planning_submission_recovery',self.task['reasoning_state'])

    def test_real_plan_prompt_includes_only_safe_structural_feedback(self):
        from researchhelper.contracts import context
        from researchhelper.reasoning import model_object
        self.task['reasoning_state']['planning_submission_recovery']=dict(version=1,status='已派发',
            context=context(self.task),input_digest=planning_digest(self.task),payload_digest=fingerprint({}),
            format_issue=ResearchAgent._completed_planning_format_issue(self.task))
        class Author:
            supports_structured_analysis=lambda self:True
            def reason_structured_plan(inner,task,messages):
                inner.messages=messages
                return json.dumps(dict(questions=[],data_requests=[],calculations=[]))
        author=Author()
        model_object(author,self.task,'plan',{}, {'questions','data_requests','calculations'})
        body=json.loads(author.messages[1]['content'])
        self.assertEqual(body['planning_format_feedback']['schema_issues'],[
            dict(code='unknown_fields',path='$.questions[*]')])
        self.assertIn('上次规划响应已完整返回',author.messages[0]['content'])
        self.assertNotIn('proposed_specs',body)

    def test_changed_recovery_payload_is_rejected_before_the_model_call(self):
        from researchhelper.contracts import context
        from researchhelper.reasoning import model_object
        self.task['reasoning_state']['planning_submission_recovery']=dict(version=1,status='已派发',
            context=context(self.task),input_digest=planning_digest(self.task),payload_digest=fingerprint({}),
            format_issue=ResearchAgent._completed_planning_format_issue(self.task))
        model=SimpleNamespace(reason_structured_plan=Mock())
        with self.assertRaises(ContractError):
            model_object(model,self.task,'plan',dict(changed=True),{'questions','data_requests','calculations'})
        model.reason_structured_plan.assert_not_called()

    def test_new_formal_task_has_one_slot_but_document_and_fixture_do_not(self):
        import tempfile
        from researchhelper.archive import ResearchArchive
        with tempfile.TemporaryDirectory(prefix='planning-recovery-create-',dir=ROOT/'result/acceptance') as folder:
            agent=ResearchAgent(ResearchArchive(Path(folder)/'store'),ROOT,offline=True,require_argument_support=True)
            try:
                self.assertEqual(agent.create('研究基金')['planning_submission_recovery_version'],1)
                self.assertNotIn('planning_submission_recovery_version',agent.create('原件核对',intent='原件内容核对'))
                self.assertNotIn('planning_submission_recovery_version',agent.create('固定例',fixture='etf'))
            finally:
                for worker in agent.close_runtime():worker.join(timeout=5)


if __name__=='__main__':unittest.main()
