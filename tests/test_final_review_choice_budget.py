"""Last-call review submission recovery; mocked verdicts are not finance QA."""
import copy
import json
import tempfile
import unittest

from tests import ROOT, test_completed_review_choice_recovery as fixture
from researchhelper import argument_support as support
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import charge, ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelCompletionError
from researchhelper.planning import input_fingerprint

POLICY = 'review_choice_delivery_version'


class FinalReviewChoiceBudgetTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.CompletedReviewChoiceRecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.task, self.proposal, self.agent = self.fixture.task, self.fixture.proposal, self.fixture.agent
        self.task[POLICY] = 1
        self.task['ledger']['used']['model_calls'] = 6
        self.model = self.fixture.model
        self.model.side_effect = self.submit
        self.calls = 0
        self.fail_again = False

    def submit(self, task, proposal, *, delivery, format_issue=None):
        self.calls += 1
        charge(task['ledger'], 'model_calls', delivery=delivery)
        if self.calls == 1 or self.fail_again:
            self.fixture.failure()
            raise ModelCompletionError('Known complete local citation mismatch')
        self.assertTrue(delivery)
        self.assertEqual(format_issue['category'], 'completed_review_choice')
        self.assertFalse(format_issue['automatic_repair'])
        self.assertEqual(proposal, self.proposal)
        self.assertEqual(task['reasoning_state']['argument_submission_recovery']['status'], '已派发')
        self.agent._save_model_checkpoint.assert_called()
        request = support.review_request(task, proposal)
        result = support.decode_reference_review(self.fixture.wire(request, wrong=False),
            support.reference_review_schema(request), request)
        task['ledger']['dispatches'].append(dict(kind='model_calls', status='成功'))
        return json.dumps(result)

    def prepare(self):
        self.agent._prepare_argument_review(self.task, self.proposal, ['q1'])

    def test_eighth_call_can_submit_same_full_review_without_new_data(self):
        data_before = self.task['ledger']['used']['data_requests']
        self.prepare()
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 8)
        self.assertEqual(self.task['ledger']['used']['data_requests'], data_before)
        self.assertEqual(self.task['reasoning_state']['final_delivery']['status'], '已提交')
        self.assertFalse(self.task['argument_support_reviews'][-1]['result']['supported'])
        self.fixture.fixture.consume()
        self.assertNotEqual(self.task['questions'][0]['status'], '已回答')

    def test_legacy_and_invalid_policy_do_not_spend_the_reserve(self):
        for flag in (None, 0, True, '1', 2):
            with self.subTest(flag=flag):
                task = copy.deepcopy(self.task)
                task[POLICY] = flag
                request = support.review_request(task, self.proposal)
                # Build the diagnostic against this exact scope, not another task.
                saved = self.fixture.task
                self.fixture.task = task
                try:
                    self.fixture.failure()
                finally:
                    self.fixture.task = saved
                task['ledger']['used']['model_calls'] = 7
                attempt = dict(review_hash=request['review_hash'])
                result = self.agent._recover_completed_argument_format(task, self.proposal, attempt,
                    failed_dispatch_index=len(task['ledger']['dispatches'])-1)
                self.assertIsNone(result)
        self.model.assert_not_called()

    def test_no_ninth_call_or_retry_after_second_failure(self):
        self.fail_again = True
        self.prepare()
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 8)
        self.assertEqual(self.task['reasoning_state']['argument_submission_recovery']['status'], '未采用')
        self.assertFalse(self.task.get('argument_support_reviews'))
        self.prepare()
        self.assertEqual(self.calls, 2)

    def test_only_one_remaining_call_does_not_create_a_ninth(self):
        self.task['ledger']['used']['model_calls'] = 7
        self.prepare()
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 8)

    def test_cancellation_blocks_recovery(self):
        self.agent._check_run_control.side_effect = ContractError('Cancelled')
        with self.assertRaises(ContractError):
            self.prepare()
        self.assertEqual(self.calls, 1)
        self.assertNotIn('argument_submission_recovery', self.task['reasoning_state'])

    def test_checkpoint_failure_blocks_new_dispatch(self):
        self.fixture.failure()
        self.task['ledger']['used']['model_calls'] = 7
        self.agent._save_model_checkpoint.side_effect = OSError('Cannot save')
        attempt = dict(review_hash=support.review_request(self.task, self.proposal)['review_hash'])
        with self.assertRaises(OSError):
            self.agent._recover_completed_argument_format(self.task, self.proposal, attempt,
                failed_dispatch_index=len(self.task['ledger']['dispatches'])-1)
        self.model.assert_not_called()

    def test_closed_task_and_insufficient_save_margin_cannot_recover(self):
        self.fixture.failure()
        self.task['ledger']['used']['model_calls'] = 7
        for change in ('closed', 'margin'):
            task = copy.deepcopy(self.task)
            if change == 'closed':
                task['status'] = '部分完成'
            else:
                task['ledger']['used']['seconds'] = 596
            attempt = dict(review_hash=support.review_request(task, self.proposal)['review_hash'])
            with self.subTest(change=change):
                self.assertIsNone(self.agent._recover_completed_argument_format(task, self.proposal, attempt,
                    failed_dispatch_index=len(task['ledger']['dispatches'])-1))
        self.model.assert_not_called()

    def test_policy_changes_both_input_and_review_binding(self):
        old = copy.deepcopy(self.task)
        old.pop(POLICY)
        self.assertNotEqual(input_fingerprint(old), input_fingerprint(self.task))
        self.assertNotEqual(support.review_request(old, self.proposal)['review_hash'],
            support.review_request(self.task, self.proposal)['review_hash'])

    def test_new_formal_tasks_only(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            self.assertEqual(agent.create('研究公开资料')[POLICY], 1)
            self.assertNotIn(POLICY, agent.create_case('mechanism'))
            self.assertNotIn(POLICY, agent.create('核对原件', intent='原件内容核对'))


class ReviewChoiceReportBindingTests(unittest.TestCase):
    def test_policy_is_frozen_with_a_real_task_bound_material(self):
        from tests import test_revision_readiness as material_fixture
        sample = material_fixture.RevisionReadinessProtocolTests()
        sample.setUp()
        self.addCleanup(sample.doCleanups)
        # Unlike the routing fixture's deliberately unbound counterevidence,
        # this Session acquired its own TXT with exact task/source bindings.
        sample.session.review_arguments(sample.task['id'], sample.wire)
        task = sample.session.deliver(sample.task['id'])
        report = sample.session.archive.report(task['id'], task['version'])['content']
        self.assertEqual(report[POLICY], 1)
        self.assertEqual(task['status'], '部分完成')
