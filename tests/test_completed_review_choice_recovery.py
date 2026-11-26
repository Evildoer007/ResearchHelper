"""A fresh bounded reviewer may correct choices, never repair financial claims."""
import copy
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests import ROOT, test_argument_support_workflow as fixtures
from researchhelper import argument_support as support
from researchhelper.contracts import ContractError, charge, fingerprint
from researchhelper.model import ModelCompletionError, ModelService, ModelSettings
from researchhelper.output_schema import StructuredOutputError


class CompletedReviewChoiceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ArgumentSupportWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task, self.proposal, self.agent = self.fixture.task, self.fixture.proposal, self.fixture.agent
        self.task.update(status='运行中', run_mode='model', argument_submission_recovery_version=1,
            argument_choice_recovery_version=1, review_basis_presentation_version=1, argument_reference_format=2)
        # Explicit test-only counterevidence; not a new real research adoption.
        counter = copy.deepcopy(self.task['evidence'][0])
        counter['id'] += '_choice_counter_fixture'
        self.task['evidence'].append(counter)
        self.task['ledger']['used'].update(model_calls=5, seconds=100)
        self.before = copy.deepcopy((self.task['evidence'], self.task['calculations'], self.proposal,
                                     self.task['ledger']['limits']))
        self.calls = 0
        self.model = Mock(side_effect=self.submit)
        self.agent.model = SimpleNamespace(reason_argument_support=self.model)

    def tearDown(self):
        self.assertEqual((self.task['evidence'], self.task['calculations'], self.proposal,
                          self.task['ledger']['limits']), self.before)

    def wire(self, request, wrong=True):
        units = support.reference_units(request)
        reviews = [dict(target_id=target, segments=[dict(unit_ref=unit['unit_ref'],
            verdict='insufficient', basis_refs=['scope'], explanation='隔离选择流程夹具。') for unit in selected])
            for target, selected in units.items()]
        if wrong:
            segment = reviews[0]['segments'][0]
            segment.update(verdict='supported', basis_refs=['e' + str(len(request['evidence']))])
        related = sorted({ident for target in request['targets'] for ident in target['question_ids']})
        return dict(version=2, binding_ref=request['review_hash'][:16], reviews=reviews,
            questions=[dict(question_id=ident, status='requires_evidence', missing_evidence=['隔离必要依据夹具。'],
                            reason='隔离充分性夹具，不是金融质量验收。') for ident in related])

    def failure(self, **changes):
        request = support.review_request(self.task, self.proposal)
        schema = support.reference_review_schema(request)
        with self.assertRaises(StructuredOutputError) as caught:
            support.decode_reference_review(self.wire(request), schema, request)
        event = dict(kind='model_calls', research_phase='review_support', batch=self.task['batch'], status='失败',
            failure_code='structured_output_rejected', structured_failure_category='invalid_argument_reference',
            provider_completion_status='completed', response_body_complete=True, finish_reason='stop',
            output_protocol=support.REFERENCE_PROTOCOL, output_schema_hash=fingerprint(schema),
            structured_output_diagnostic=caught.exception.diagnostic)
        event.update(changes)
        self.task['ledger']['dispatches'].append(event)
        return event

    def submit(self, task, proposal, *, delivery, format_issue=None):
        self.calls += 1
        self.assertEqual(proposal, self.proposal)
        charge(task['ledger'], 'model_calls', delivery=delivery)
        if self.calls == 1:
            self.failure()
            raise ModelCompletionError('Known complete citation selection mismatch')
        self.assertEqual(format_issue['category'], 'completed_review_choice')
        self.assertFalse(format_issue['automatic_repair'])
        self.assertFalse(delivery)
        self.agent._save_model_checkpoint.assert_called()
        request = support.review_request(task, proposal)
        result = support.decode_reference_review(self.wire(request, wrong=False),
            support.reference_review_schema(request), request)
        task['ledger']['dispatches'].append(dict(kind='model_calls', status='成功'))
        return json.dumps(result)

    def test_actual_wrong_positive_reference_yields_only_known_context_diagnostic(self):
        event = self.failure()
        issue = support.completed_review_choice_issue(self.task, self.proposal)
        self.assertEqual(issue['category'], 'completed_review_choice')
        self.assertTrue(event['structured_output_diagnostic']['choice_scope_check']['complete_coverage'])
        self.assertFalse(event['structured_output_diagnostic']['choice_scope_check']['financial_approval'])
        self.assertIsNone(support.completed_review_format_issue(self.task, self.proposal))
        self.assertNotIn('basis_refs', json.dumps(issue))

    def test_fresh_negative_review_is_preserved_with_no_new_data_or_approval(self):
        data_before = self.task['ledger']['used']['data_requests']
        self.agent._prepare_argument_review(self.task, self.proposal, ['q1'])
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 7)
        self.assertEqual(self.task['ledger']['used']['data_requests'], data_before)
        self.assertFalse(self.task['argument_support_reviews'][-1]['result']['supported'])
        self.fixture.consume()
        self.assertNotEqual(self.task['questions'][0]['status'], '已回答')

    def test_unknown_completion_changed_schema_and_changed_proposal_stop(self):
        for changes in (dict(status='已派发'), dict(response_body_complete=False), dict(finish_reason='length'),
                        dict(batch=0), dict(output_schema_hash='changed'), dict(failure_code='http_failure')):
            event = self.failure(**changes)
            with self.subTest(changes=changes):
                self.assertIsNone(support.completed_review_choice_issue(self.task, self.proposal))
            self.task['ledger']['dispatches'].remove(event)
        self.failure()
        changed = copy.deepcopy(self.proposal)
        changed['answers']['q1']['refs'] = []
        try:
            issue = support.completed_review_choice_issue(self.task, changed)
        except ContractError:
            issue = None
        self.assertIsNone(issue)

    def test_wrong_bindings_unknown_refs_and_unverified_coverage_do_not_gain_retry(self):
        event = self.failure()
        original = copy.deepcopy(event['structured_output_diagnostic'])
        cases = [dict(schema_issues=[dict(path='$.binding_ref', code='enum_mismatch')]),
                 dict(schema_issues=[dict(path='$.reviews', code='too_few_items')]),
                 dict(choice_scope_check=dict(version=1, other_fields_valid=True, complete_coverage=False,
                                              financial_approval=False)),
                 dict(reference_issues=[dict(code='unknown_basis_reference', target_index=0, segment_index=0)])]
        for changes in cases:
            event['structured_output_diagnostic'] = dict(copy.deepcopy(original), **changes)
            with self.subTest(changes=changes):
                self.assertIsNone(support.completed_review_choice_issue(self.task, self.proposal))

    def test_diagnostic_check_does_not_accept_or_repair_wrong_wire(self):
        request = support.review_request(self.task, self.proposal)
        schema = support.reference_review_schema(request)
        wire = self.wire(request)
        wire['reviews'][0]['segments'].pop()
        before = copy.deepcopy(wire)
        with self.assertRaises(StructuredOutputError) as caught:
            support.decode_reference_review(wire, schema, request)
        self.assertFalse(caught.exception.diagnostic.get('choice_scope_check', {}).get('complete_coverage', False))
        self.assertEqual(wire, before)

    def test_legacy_and_boolean_flags_keep_the_previous_stop_rule(self):
        self.failure()
        for flag in (None, True, 0, 2):
            self.task['argument_choice_recovery_version'] = flag
            with self.subTest(flag=flag):
                self.assertIsNone(support.completed_review_choice_issue(self.task, self.proposal))

    def test_budget_and_time_reserves_are_not_spent_on_choice_resubmission(self):
        for used in (dict(model_calls=7, seconds=100), dict(model_calls=5, seconds=545)):
            self.task['ledger']['used'].update(used)
            self.calls = 0
            self.agent._prepare_argument_review(self.task, self.proposal, ['q1'])
            self.assertEqual(self.model.call_count, 1)
            self.assertNotIn('argument_submission_recovery', self.task['reasoning_state'])
            self.model.reset_mock()
            self.task['reasoning_state'].pop('argument_support_attempts', None)
            self.task['reasoning_state'].pop('final_delivery', None)

    def test_second_failure_and_resume_do_not_grant_another_shared_slot(self):
        def reject(task, proposal, *, delivery, format_issue=None):
            charge(task['ledger'], 'model_calls', delivery=delivery)
            self.failure()
            raise ModelCompletionError('Second bounded selection rejection')
        self.model.side_effect = reject
        self.agent._prepare_argument_review(self.task, self.proposal, ['q1'])
        self.agent._prepare_argument_review(self.task, self.proposal, ['q1'])
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual(self.task['reasoning_state']['argument_submission_recovery']['status'], '未采用')
        self.assertFalse(self.task.get('argument_support_reviews'))

    def test_real_model_adapter_uses_same_schema_and_negative_response_once(self):
        self.failure()
        issue = support.completed_review_choice_issue(self.task, self.proposal)
        request = support.review_request(self.task, self.proposal)
        wire = self.wire(request, wrong=False)
        response = dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                         content=[dict(type='output_text', text=json.dumps(wire))])], usage={})
        with tempfile.TemporaryDirectory(prefix='review-choice-', dir=ROOT / 'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                               api_key='FAKE_REVIEW_CHOICE_KEY'))
            model = ModelService(settings, offline=False)
            with patch.object(model, '_request_bytes', return_value=json.dumps(response).encode()) as transport:
                result = json.loads(model.reason_argument_support(self.task, self.proposal, format_issue=issue))
        self.assertEqual(transport.call_count, 1)
        self.assertFalse(result['supported'])
