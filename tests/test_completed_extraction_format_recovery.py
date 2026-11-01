"""One new submission after a known complete syntax failure, never JSON repair."""
import copy
import json
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from researchhelper.contracts import ContractError, charge
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelCompletionError

ROOT = Path(__file__).resolve().parents[1]
RETAINED = ROOT / 'result/acceptance/one-sentence-research-5wixw_pf/task.json'


class CompletedExtractionFormatRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.original = RETAINED.read_bytes()
        self.task = json.loads(self.original)
        self.task['extraction_submission_recovery_version'] = 1
        self.agent = ResearchAgent.__new__(ResearchAgent)
        self.agent.model = SimpleNamespace()
        self.agent._save_model_checkpoint = Mock()
        self.agent._active = Mock(return_value=self.task)
        self.payload = dict(question=self.task['question'], questions=[{'id': 'q1'}],
                            untrusted_sources=[{'id': 'bounded-original-fixture'}])
        self.proposal = {'evidence_specs': []}
        self.receipt = {'phase': 'extract', 'output_hash': 'isolated-format-fixture'}
        self.index = len(self.task['ledger']['dispatches']) - 1
        self.limits = copy.deepcopy(self.task['ledger']['limits'])
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No network'))
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(), self.original)
        self.assertEqual(self.task['ledger']['limits'], self.limits)

    def submit(self, model, task, phase, payload, expected):
        self.assertEqual(phase, 'extract')
        self.assertEqual(expected, {'evidence_specs'})
        self.assertEqual(payload, self.payload)
        self.assertEqual(task['reasoning_state']['extraction_submission_recovery']['status'], '已派发')
        self.agent._save_model_checkpoint.assert_called()
        charge(task['ledger'], 'model_calls')
        return copy.deepcopy(self.proposal), copy.deepcopy(self.receipt)

    def recover(self):
        return self.agent._recover_completed_extraction_format(self.task, self.payload,
            'same-bounded-input', failed_dispatch_index=self.index)

    def test_known_complete_failure_uses_one_call_and_keeps_input_and_budget(self):
        before = copy.deepcopy(self.payload)
        with patch('researchhelper.engine.model_object', side_effect=self.submit) as call:
            self.assertEqual(self.recover(), (self.proposal, self.receipt))
        self.assertEqual(call.call_count, 1)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 6)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 4)
        self.assertEqual(self.payload, before)
        self.assertEqual(self.task['reasoning_state']['extraction_submission_recovery']['status'], '已提交')
        self.assertFalse(self.task['reasoning_state']['extraction_submission_recovery']['automatic_repair'])

    def test_legacy_tasks_do_not_gain_automatic_resubmission(self):
        self.task.pop('extraction_submission_recovery_version')
        with patch('researchhelper.engine.model_object', side_effect=self.submit) as call:
            self.assertIsNone(self.recover())
        call.assert_not_called()

    def test_two_remaining_calls_are_reserved_for_analysis_and_review(self):
        self.task['ledger']['used']['model_calls'] = 6
        with patch('researchhelper.engine.model_object') as call:
            self.assertIsNone(self.recover())
        call.assert_not_called()

    def test_delivery_time_reserve_is_not_spent(self):
        self.task['ledger']['used']['seconds'] = 550
        with patch('researchhelper.engine.model_object') as call:
            self.assertIsNone(self.recover())
        call.assert_not_called()

    def test_unknown_incomplete_timeout_truncation_or_non_syntax_failures_do_not_retry(self):
        event = self.task['ledger']['dispatches'][-1]
        original = copy.deepcopy(event)
        for values in (dict(status='已派发'), dict(response_body_complete=False),
                       dict(provider_completion_status='incomplete'), dict(finish_reason='length'),
                       dict(failure_code='response_deadline_exceeded'), dict(failure_code='http_failure'),
                       dict(structured_failure_category='schema_mismatch'), dict(research_phase='analyze'),
                       dict(structured_output_diagnostic={})):
            event.clear()
            event.update(original)
            event.update(values)
            with self.subTest(values=values), patch('researchhelper.engine.model_object') as call:
                self.assertIsNone(self.recover())
                call.assert_not_called()

    def test_prior_batch_failure_does_not_authorize_a_new_submission(self):
        self.task['ledger']['dispatches'][-1]['batch'] = self.task['batch'] - 1
        with patch('researchhelper.engine.model_object') as call:
            self.assertIsNone(self.recover())
        call.assert_not_called()

    def test_failure_must_be_from_the_call_just_attempted(self):
        self.index = len(self.task['ledger']['dispatches'])
        with patch('researchhelper.engine.model_object') as call:
            self.assertIsNone(self.recover())
        call.assert_not_called()

    def test_second_failure_is_durable_and_never_loops(self):
        def rejected(*args):
            self.submit(*args)
            raise ModelCompletionError('Isolated second format failure')
        with patch('researchhelper.engine.model_object', side_effect=rejected) as call:
            self.assertIsNone(self.recover())
            self.assertIsNone(self.recover())
        self.assertEqual(call.call_count, 1)
        self.assertEqual(self.task['reasoning_state']['extraction_submission_recovery']['status'], '未采用')

    def test_resume_with_dispatched_or_failed_marker_does_not_duplicate_submission(self):
        for status in ('已派发', '已提交', '未采用'):
            self.task['reasoning_state']['extraction_submission_recovery'] = {
                'version': 1, 'status': status, 'input_digest': 'same-bounded-input'}
            with self.subTest(status=status), patch('researchhelper.engine.model_object') as call:
                self.assertIsNone(self.recover())
                call.assert_not_called()

    def test_cancellation_is_checked_before_dispatch(self):
        self.agent._active.side_effect = ContractError('Isolated cancellation')
        with patch('researchhelper.engine.model_object') as call:
            with self.assertRaises(ContractError):
                self.recover()
        call.assert_not_called()

    def test_resubmission_creates_no_evidence_calculation_or_review_approval(self):
        with patch('researchhelper.engine.model_object', side_effect=self.submit):
            self.recover()
        self.assertEqual(self.task['evidence'], [])
        self.assertEqual(self.task['calculations'], [])
        self.assertFalse(self.task.get('argument_support_reviews'))
        self.assertEqual(self.task['status'], '部分完成')

    def test_unknown_recovery_marker_blocks_normal_extraction_after_resume(self):
        self.task['reasoning_state']['extraction_submission_recovery'] = dict(version=1,
            status='已派发', input_digest=self.task['reasoning_state']['reading']['input_digest'])
        with patch('researchhelper.engine.model_object') as call:
            result = self.agent._extract_model_sources(self.task)
        call.assert_not_called()
        self.assertEqual(result['evidence_specs'], [])
        self.assertEqual(self.task['ledger']['used']['model_calls'], 5)

    def test_successful_resubmission_enters_normal_proposal_validation(self):
        previous_gaps = list(self.task['gaps'])
        with patch('researchhelper.engine.model_object', side_effect=ContractError('Isolated first failure')), \
                patch.object(self.agent, '_recover_completed_extraction_format',
                             return_value=(self.proposal, self.receipt)) as recover:
            result = self.agent._extract_model_sources(self.task)
        recover.assert_called_once()
        self.assertEqual(result['evidence_specs'], [])
        self.assertIn('extraction', self.task['reasoning_state'])
        self.assertEqual(self.task['gaps'], previous_gaps)

    def test_resubmission_does_not_bypass_registered_proposal_fields(self):
        with patch('researchhelper.engine.model_object', side_effect=ContractError('Isolated first failure')), \
                patch.object(self.agent, '_recover_completed_extraction_format', return_value=(
                    {'evidence_specs': [{'unregistered_approval': True}]}, self.receipt)):
            with self.assertRaises(ContractError):
                self.agent._extract_model_sources(self.task)
        self.assertEqual(self.task['evidence'], [])

    def test_actual_prompt_and_receipt_include_bounded_format_feedback(self):
        import tempfile
        from researchhelper.model import ModelService, ModelSettings
        from researchhelper.reasoning import model_object, evidence_contract, question_specs
        bundle = self.task['reasoning_state']['reading']['bundle']
        contract = evidence_contract(self.task, bundle['views'])
        payload = dict(question=self.task['question'], intent=self.task['intent'], objects=self.task['objects'],
            cutoff=self.task['cutoff'], questions=question_specs(self.task), calculations=self.task['calculation_specs'],
            evidence_contract=contract, field_registry={}, untrusted_sources=bundle['views'],
            source_reading={key: bundle[key] for key in ('reading_hash', 'coverage', 'limitations')},
            calculation_input_targets=contract['calculation_input_requirements']['proposal_requirements'])
        self.task['reasoning_state']['extraction_submission_recovery'] = dict(version=1, status='已派发')
        with tempfile.TemporaryDirectory(prefix='format-feedback-', dir=ROOT / 'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                               api_key='FAKE_EXTRACTION_FEEDBACK_KEY'))
            service = ModelService(settings, offline=False)
            with patch.object(service, 'reason', return_value='{"evidence_specs":[]}') as call:
                _, receipt = model_object(service, self.task, 'extract', payload, {'evidence_specs'})
            self.assertIn('上次提取响应已完整返回', call.call_args.args[1][0]['content'])
            self.assertIn('不加Markdown代码围栏', call.call_args.args[1][0]['content'])
            self.assertEqual(receipt['extraction_submission_recovery_version'], 1)
