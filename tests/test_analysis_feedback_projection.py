"""A failed draft is archived, not repeated as if it were analysis evidence."""
import copy
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.legacy_replay_fixtures import financial_task
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.output_schema import analysis_protocol, provider_request_schema
from researchhelper.reasoning import model_object, analysis_fields, MAX_CONTEXT_CHARACTERS
from researchhelper.reasoning import analysis_numeric_review


class CapturedAnalysis(RuntimeError):
    pass


def payload_for(task):
    agent = ResearchAgent.__new__(ResearchAgent)
    agent.model = object()  # Never called: capture precedes any model transport.
    captured = {}
    def capture(model, current, phase, payload, expected, **options):
        captured.update(payload=copy.deepcopy(payload))
        raise CapturedAnalysis()
    with patch('researchhelper.engine.model_object', side_effect=capture):
        try:
            agent._reason_on_verified_material(task)
        except CapturedAnalysis:
            return captured['payload']
    raise AssertionError('Analysis payload was not reached')


def checked_prompt(task, payload):
    captured = {}
    def capture(current, messages, phase, schema):
        captured.update(messages=messages, schema=schema)
        raise CapturedAnalysis()
    model = SimpleNamespace(supports_structured_analysis=lambda: True, reason_structured_phase=capture)
    try:
        model_object(model, task, 'analyze', payload, analysis_fields(task))
    except CapturedAnalysis:
        return captured
    raise AssertionError('Finite prompt was not reached')


class AnalysisFeedbackProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        task = financial_task(both_ratios=True)
        task.update(analysis_question_scope_version=1, analysis_feedback_projection_version=1)
        invalid_number = {'text': '合成提案把净利润率写为91.23%。'}
        proposal = dict(answers={key: dict(refs=[], calculation_refs=[],
                         numeric_segments={'answer': [invalid_number]}) for key in ('meaning', 'financial')},
            theses=[dict(question_ids=['meaning'], title='合成论点', investment_meaning='须继续核对。',
                         limitation='仅作语法回归样本。', support=[], counter=[], calculation_refs=[],
                         numeric_segments={'title': [invalid_number]})])
        review = analysis_numeric_review(task, proposal)
        task['reasoning_state'] = dict(rejected_analysis_submissions=[dict(
            task_id=task['id'], research_version=task['version'], batch=task['batch'], input_hash=task['input_hash'],
            proposal=proposal, numeric_review=review, evidence_approved=False, automatic_repair=False)],
            numeric_resubmission=dict(status='已派发', reason='numeric_submission_rejected',
                invalid_fields=review['invalid_fields'], question_failures=review['question_failures'],
                thesis_failures=review['thesis_failures']))
        cls.saved = task

    def setUp(self):
        self.task = copy.deepcopy(self.saved)
        self.task['analysis_feedback_projection_version'] = 1
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline projection test'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_complete_evidence_questions_and_calculations_are_unchanged(self):
        legacy = copy.deepcopy(self.task)
        legacy.pop('analysis_feedback_projection_version')
        old, new = payload_for(legacy), payload_for(self.task)
        old_feedback, new_feedback = old.pop('submission_feedback'), new.pop('submission_feedback')
        self.assertEqual(old, new)
        self.assertEqual(old_feedback['invalid_fields'], new_feedback['invalid_fields'])
        self.assertEqual(old_feedback['question_failures'], new_feedback['question_failures'])
        self.assertIn('rejected_proposal', old_feedback)
        self.assertNotIn('rejected_proposal', new_feedback)

    def test_full_failed_proposal_stays_in_archive_with_exact_locator_and_hash(self):
        before = copy.deepcopy(self.task['reasoning_state']['rejected_analysis_submissions'])
        feedback = payload_for(self.task)['submission_feedback']
        reference = feedback['rejected_proposal_archive_ref']
        self.assertEqual(reference['task_id'], self.task['id'])
        self.assertEqual(reference['research_version'], self.task['version'])
        self.assertEqual(reference['proposal_hash'], fingerprint(before[reference['record_index']]['proposal']))
        self.assertEqual(self.task['reasoning_state']['rejected_analysis_submissions'], before)
        self.assertFalse(feedback['automatic_repair'])
        self.assertFalse(feedback['rejected_proposal_available'])

    def test_oversize_legacy_feedback_fits_after_projection_without_removing_sources(self):
        legacy = copy.deepcopy(self.task)
        legacy.pop('analysis_feedback_projection_version')
        captured = checked_prompt(legacy, payload_for(legacy))
        protocol = analysis_protocol(legacy)[0]
        schema_size = len(canonical(provider_request_schema(legacy, captured['schema'], protocol)))
        body_size = len(captured['messages'][-1]['content'])
        extra = MAX_CONTEXT_CHARACTERS - body_size - schema_size + 1
        self.assertGreater(extra, 0)
        # Grow the rejected draft to overflow only the body and schema together.
        for task in (legacy, self.task):
            rejected = task['reasoning_state']['rejected_analysis_submissions'][-1]['proposal']
            rejected['theses'][0]['limitation'] += '字' * extra
        with self.assertRaisesRegex(ContractError, '必要材料与实际提交契约超过上下文上限'):
            checked_prompt(legacy, payload_for(legacy))
        payload = payload_for(self.task)
        current = checked_prompt(self.task, payload)
        actual_size = (len(current['messages'][-1]['content']) +
                       len(canonical(provider_request_schema(self.task, current['schema'], protocol))))
        self.assertLessEqual(actual_size, MAX_CONTEXT_CHARACTERS)
        self.assertEqual(payload['calculations'], self.saved['calculations'])
        self.assertEqual(self.task['ledger'], self.saved['ledger'])

    def test_old_tasks_and_unregistered_flags_keep_the_original_feedback(self):
        for version in (None, True, 0, 2):
            self.task['analysis_feedback_projection_version'] = version
            with self.subTest(version=version):
                self.assertIn('rejected_proposal', payload_for(self.task)['submission_feedback'])

    def test_projection_does_not_drop_failed_questions_or_consume_budget(self):
        before = copy.deepcopy((self.task['ledger'], self.task['questions'], self.task['evidence']))
        feedback = payload_for(self.task)['submission_feedback']
        self.assertEqual(feedback['remaining_resubmissions'], 0)
        self.assertEqual([item['question_id'] for item in feedback['question_failures']], ['meaning', 'financial'])
        self.assertEqual((self.task['ledger'], self.task['questions'], self.task['evidence']), before)

    def test_retained_numeric_failure_has_specific_registered_codes_not_rejected_values(self):
        proposal = self.task['reasoning_state']['rejected_analysis_submissions'][-1]['proposal']
        review = analysis_numeric_review(self.task, proposal)
        self.assertFalse(review['accepted'])
        self.assertTrue(review['question_failures'])
        self.assertEqual({item['code'] for item in review['question_failures']}, {'unbound_numeric_prose'})
        self.assertEqual({item['code'] for item in review['thesis_failures']}, {'unbound_numeric_prose'})
        self.assertFalse(review['approval'])
        self.assertNotIn('text', canonical(review))

    def test_old_numeric_review_keeps_its_original_diagnostic_contract(self):
        self.task.pop('analysis_feedback_projection_version')
        proposal = self.task['reasoning_state']['rejected_analysis_submissions'][-1]['proposal']
        review = analysis_numeric_review(self.task, proposal)
        self.assertFalse(review['accepted'])
        self.assertNotIn('thesis_failures', review)
        self.assertEqual({item['code'] for item in review['question_failures']},
                         {'numeric_or_reference_binding_rejected'})
