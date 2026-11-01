"""A failed draft is archived, not repeated as if it were analysis evidence."""
import copy
import json
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.reasoning import model_object, analysis_fields, MAX_CONTEXT_CHARACTERS
from researchhelper.reasoning import analysis_numeric_review

RETAINED = ROOT / 'result/acceptance/one-sentence-research-jw2plz9b/task.json'


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
        cls.original = RETAINED.read_bytes()
        cls.saved = json.loads(cls.original)

    @classmethod
    def tearDownClass(cls):
        if RETAINED.read_bytes() != cls.original:
            raise AssertionError('Closed failed research changed')

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

    def test_actual_failed_capacity_is_reproduced_then_fits_without_truncating_sources(self):
        legacy = copy.deepcopy(self.task)
        legacy.pop('analysis_feedback_projection_version')
        with self.assertRaisesRegex(ContractError, '必要分析与结构化契约超过上下文上限'):
            checked_prompt(legacy, payload_for(legacy))
        captured = checked_prompt(self.task, payload_for(self.task))
        self.assertLessEqual(sum(len(item['content']) for item in captured['messages'])
                             + len(canonical(captured['schema'])), MAX_CONTEXT_CHARACTERS)
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
        self.assertEqual([item['question_id'] for item in feedback['question_failures']], ['q3', 'q1'])
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
