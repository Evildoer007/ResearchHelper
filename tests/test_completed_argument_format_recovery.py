"""Known review-format resubmission; synthetic verdicts are not financial QA."""
import copy
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests import ROOT
from tests import test_argument_support_workflow as fixture
from researchhelper import argument_support as support
from researchhelper.contracts import ContractError, charge, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelCompletionError, ModelService, ModelSettings


class CompletedArgumentFormatRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ArgumentSupportWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.task = self.fixture.task
        self.task.update(status='运行中', run_mode='model', argument_submission_recovery_version=1)
        self.task['ledger']['used'].update(model_calls=6, seconds=100)
        self.proposal = self.fixture.proposal
        self.agent = self.fixture.agent
        self.model = Mock(side_effect=self.submit)
        self.agent.model = SimpleNamespace(reason_argument_support=self.model)
        self.calls = 0
        self.limits = copy.deepcopy(self.task['ledger']['limits'])

    def tearDown(self):
        self.assertEqual(self.task['ledger']['limits'], self.limits)

    def failure(self, task, **changes):
        request = support.review_request(task, self.proposal)
        event = dict(kind='model_calls', research_phase='review_support', batch=task['batch'],
            status='失败', failure_code='structured_output_rejected',
            structured_failure_category='invalid_argument_reference',
            provider_completion_status='completed', response_body_complete=True, finish_reason='stop',
            output_protocol=support.REFERENCE_PROTOCOL,
            output_schema_hash=fingerprint(support.reference_review_schema(request)),
            structured_output_diagnostic=dict(version=1, automatic_repair=False,
                submitted_values_retained=False, schema_issues=[dict(code='unknown_fields', path='$')]))
        event.update(changes)
        task['ledger']['dispatches'].append(event)
        return event

    def submit(self, task, proposal, *, delivery, format_issue=None):
        self.calls += 1
        self.assertEqual(proposal, self.proposal)
        charge(task['ledger'], 'model_calls', delivery=delivery)
        if self.calls == 1:
            self.failure(task)
            raise ModelCompletionError('Known complete structural rejection')
        self.assertEqual(task['reasoning_state']['argument_submission_recovery']['status'], '已派发')
        self.agent._save_model_checkpoint.assert_called()
        self.assertIsNotNone(format_issue)
        self.assertTrue(delivery)
        self.assertEqual(task['ledger']['used']['data_requests'], self.data_before)
        task['ledger']['dispatches'].append(dict(kind='model_calls', status='成功'))
        return json.dumps(fixture.assessed(task, proposal)[1])

    def prepare(self):
        self.data_before = self.task['ledger']['used']['data_requests']
        self.agent._prepare_argument_review(self.task, self.proposal, ['q1'])

    def test_known_complete_structure_gets_one_fresh_review_with_last_call(self):
        before = copy.deepcopy((self.task['evidence'], self.task['calculations'], self.proposal))
        self.prepare()
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 8)
        self.assertEqual(self.task['reasoning_state']['final_delivery']['status'], '已提交')
        self.assertEqual(len(self.task['argument_support_reviews']), 1)
        self.assertEqual(self.task['reasoning_state']['argument_submission_recovery']['status'], '已复核')
        self.assertEqual((self.task['evidence'], self.task['calculations'], self.proposal), before)

    def test_legacy_task_keeps_one_attempt_without_silent_upgrade(self):
        self.task.pop('argument_submission_recovery_version')
        self.prepare()
        self.assertEqual(self.model.call_count, 1)
        self.assertFalse(self.task.get('argument_support_reviews'))

    def test_no_ninth_call(self):
        self.task['ledger']['used']['model_calls'] = 7
        self.prepare()
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 8)

    def test_second_failure_is_preserved_without_loop(self):
        def fail(task, proposal, *, delivery, format_issue=None):
            charge(task['ledger'], 'model_calls', delivery=delivery)
            self.failure(task)
            raise ModelCompletionError('Rejected again')
        self.model.side_effect = fail
        self.prepare()
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual(self.task['reasoning_state']['argument_submission_recovery']['status'], '未采用')
        self.assertFalse(self.task.get('argument_support_reviews'))
        self.agent._prepare_argument_review(self.task, self.proposal, ['q1'])
        self.assertEqual(self.model.call_count, 2)

    def test_unknown_truncated_transport_or_semantic_failures_are_ineligible(self):
        changes = [dict(status='已派发'), dict(finish_reason='length'),
            dict(response_body_complete=False), dict(provider_completion_status='incomplete'),
            dict(failure_code='http_failure'), dict(structured_failure_category='incomplete_argument_coverage'),
            dict(output_schema_hash='foreign'), dict(batch=0), dict(output_protocol='foreign')]
        for change in changes:
            event = self.failure(self.task, **change)
            with self.subTest(change=change):
                self.assertIsNone(support.completed_review_format_issue(self.task, self.proposal))
            self.task['ledger']['dispatches'].remove(event)

    def test_enum_binding_coverage_and_unknown_diagnostic_paths_are_not_format(self):
        event = self.failure(self.task)
        for code,path in [('enum_mismatch','$.binding_ref'), ('duplicate_items','$.reviews'),
                          ('unknown_fields','$.private_name'), ('too_few_items','$.reviews')]:
            event['structured_output_diagnostic']['schema_issues'] = [dict(code=code,path=path)]
            with self.subTest(code=code,path=path):
                self.assertIsNone(support.completed_review_format_issue(self.task, self.proposal))

    def test_rejected_response_values_are_never_accepted_as_diagnostics(self):
        event = self.failure(self.task)
        event['structured_output_diagnostic']['schema_issues'][0]['value'] = 'PRIVATE'
        self.assertIsNone(support.completed_review_format_issue(self.task, self.proposal))

    def test_changed_proposal_does_not_reuse_failure(self):
        self.failure(self.task)
        self.assertIsNotNone(support.completed_review_format_issue(self.task, self.proposal))
        altered = copy.deepcopy(self.proposal)
        altered['answers']['q1']['numeric_segments']['answer'][0]['text'] += '修改'
        self.assertIsNone(support.completed_review_format_issue(self.task, altered))

    def test_closed_task_save_margin_or_existing_marker_never_resubmits(self):
        for change in ('closed','margin','marker'):
            task = copy.deepcopy(self.task)
            if change=='closed': task['status']='部分完成'
            if change=='margin': task['ledger']['used']['seconds']=598
            if change=='marker': task['reasoning_state']['argument_submission_recovery']={'status':'已派发'}
            event = self.failure(task)
            attempt = dict(review_hash=support.review_request(task,self.proposal)['review_hash'])
            with self.subTest(change=change):
                self.assertIsNone(self.agent._recover_completed_argument_format(task,self.proposal,attempt,
                    failed_dispatch_index=len(task['ledger']['dispatches'])-1))
        self.model.assert_not_called()

    def test_cancel_prevents_resubmission(self):
        self.agent._check_run_control.side_effect = ContractError('Cancelled')
        with self.assertRaisesRegex(ContractError,'Cancelled'): self.prepare()
        self.assertEqual(self.model.call_count, 1)
        self.assertNotIn('argument_submission_recovery', self.task['reasoning_state'])

    def test_checkpoint_failure_blocks_dispatch(self):
        self.failure(self.task)
        self.agent._save_model_checkpoint.side_effect = OSError('Cannot save')
        attempt = dict(review_hash=support.review_request(self.task,self.proposal)['review_hash'])
        with self.assertRaisesRegex(OSError,'Cannot save'):
            self.agent._recover_completed_argument_format(self.task,self.proposal,attempt,
                failed_dispatch_index=len(self.task['ledger']['dispatches'])-1)
        self.model.assert_not_called()

    def test_negative_fresh_verdict_stays_negative(self):
        original_submit = self.submit
        def negative(task,proposal,**options):
            if self.calls == 0: return original_submit(task,proposal,**options)
            original_submit(task,proposal,**options)
            return json.dumps(fixture.assessed(task,proposal,rejected=('answer:q1.answer',))[1])
        self.model.side_effect = negative
        self.prepare()
        self.assertFalse(self.task['argument_support_reviews'][-1]['result']['supported'])
        self.fixture.consume()
        self.assertNotEqual(self.task['questions'][0]['status'], '已回答')

    def test_prompt_names_exact_fields_and_required_empty_list(self):
        request = support.review_request(self.task,self.proposal)
        messages = support.review_messages(request,use_reference_units=True)
        self.assertIn('精确为version、binding_ref、questions、reviews', messages[0]['content'])
        self.assertIn('missing_evidence必须为[]，字段不可省略', messages[0]['content'])
        self.assertNotIn('submission_format_issue', json.loads(messages[1]['content']))

    def test_actual_model_rejects_stale_format_issue_before_spending(self):
        self.failure(self.task)
        issue = support.completed_review_format_issue(self.task,self.proposal)
        issue['review_hash'] = 'foreign'
        with tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',api_key='FAKE_REVIEW_KEY'))
            model = ModelService(settings,offline=False)
            with patch.object(model,'reason') as call:
                with self.assertRaises(ContractError):
                    model.reason_argument_support(self.task,self.proposal,format_issue=issue)
                call.assert_not_called()

    def test_actual_model_builds_same_review_schema_with_bounded_diagnostic(self):
        self.failure(self.task)
        issue = support.completed_review_format_issue(self.task,self.proposal)
        request = support.review_request(self.task,self.proposal)
        with tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',api_key='FAKE_REVIEW_KEY'))
            model = ModelService(settings,offline=False)
            with patch.object(model,'reason',return_value='isolated') as call:
                self.assertEqual(model.reason_argument_support(self.task,self.proposal,
                    delivery=True,format_issue=issue),'isolated')
                args,options = call.call_args
                self.assertEqual(options['output_schema'],support.reference_review_schema(request))
                self.assertEqual(options['schema_context'],self.proposal)
                presented = json.loads(args[1][1]['content'])
                self.assertEqual(presented['submission_format_issue'],issue)
                self.assertFalse(issue['automatic_repair'])

    def test_policy_only_enabled_for_new_formal_research(self):
        from researchhelper.archive import ResearchArchive
        with tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder),ROOT,offline=True,require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            self.assertEqual(agent.create('研究公开资料')['argument_submission_recovery_version'],1)
            self.assertNotIn('argument_submission_recovery_version',agent.create_case('mechanism'))
            self.assertNotIn('argument_submission_recovery_version',agent.create('核对原件',intent='原件内容核对'))


if __name__=='__main__': unittest.main()
