"""One new submission after a known complete syntax failure, never JSON repair."""
import copy
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, charge
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelCompletionError

ROOT = Path(__file__).resolve().parents[1]


class CompletedExtractionFormatRecoveryTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No network'))
        guard.start()
        self.addCleanup(guard.stop)
        folder = TemporaryDirectory(prefix='rh-extraction-format-')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        created = self.agent.create('核对510500.SH基金资料的管理费率。', entry='ETF',
                                    objects=['510500.SH'], research_mode='current')
        self.agent.add_material(created['id'], '合成基金费用.txt', '基金管理费率为每年0.20%。'.encode())
        self.task = self.agent.archive.get(created['id'])
        self.task.update(status='部分完成', batch=1, reasoning_state={},
                         host_result={'calculations': [], 'data_requests': []})
        # The archived extractor rejected an unknown approval field as a whole.
        self.task.pop('extraction_read_isolation_version', None)
        charge(self.task['ledger'], 'data_requests', 4)
        charge(self.task['ledger'], 'model_calls', 5)
        self.task['ledger']['dispatches'].append(dict(kind='model_calls', research_phase='extract',
            batch=1, status='失败', failure_code='structured_output_rejected',
            structured_failure_category='invalid_argument_json', provider_completion_status='completed',
            response_body_complete=True, finish_reason='stop',
            structured_output_diagnostic=dict(json_error='expected_comma'), synthetic_test_double=True))
        self.agent.model = SimpleNamespace()
        self.agent._save_model_checkpoint = Mock()
        self.agent._active = Mock(return_value=self.task)
        self.payload = dict(question=self.task['question'], questions=[{'id': 'q1'}],
                            untrusted_sources=[{'id': 'bounded-original-fixture'}])
        self.proposal = {'evidence_specs': []}
        self.receipt = {'phase': 'extract', 'output_hash': 'isolated-format-fixture'}
        self.index = len(self.task['ledger']['dispatches']) - 1
        self.limits = copy.deepcopy(self.task['ledger']['limits'])

    def tearDown(self):
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

    def planned_extraction(self):
        task = copy.deepcopy(self.task)
        with patch('researchhelper.engine.model_object', return_value=(self.proposal, self.receipt)) as call:
            self.agent._extract_model_sources(task)
        call.assert_called_once()
        return task['reasoning_state']['extraction']['input_digest'], call.call_args.args[3]

    def test_spent_recovery_marker_blocks_normal_extraction_after_restart(self):
        digest, _ = self.planned_extraction()
        for status in ('已派发', '已提交', '未采用'):
            task = copy.deepcopy(self.task)
            task['batch'] += 1
            task['status'] = '运行中'
            task['revision'] = self.agent.archive.get(task['id'])['revision']
            if status == '已提交':
                charge(task['ledger'], 'model_calls')
            task['reasoning_state']['extraction_submission_recovery'] = dict(version=1, status=status,
                input_digest=digest)
            archived = self.agent.archive.save(task)
            resumed = self.agent.archive.get(archived['id'])
            with self.subTest(status=status), patch('researchhelper.engine.model_object',
                    side_effect=AssertionError('Same extraction input was dispatched again')) as call:
                result = self.agent._extract_model_sources(resumed)
                call.assert_not_called()
                self.assertEqual(result['evidence_specs'], [])
                self.assertEqual(resumed['ledger'], task['ledger'])
                self.assertEqual(resumed['materials'], self.task['materials'])

    def test_restart_reuses_the_validated_checkpoint_if_it_was_saved(self):
        with patch('researchhelper.engine.model_object', return_value=(self.proposal, self.receipt)):
            expected = self.agent._extract_model_sources(self.task)
        state = self.task['reasoning_state']
        state['extraction_submission_recovery'] = dict(version=1, status='已提交',
            input_digest=state['extraction']['input_digest'])
        self.task.update(status='运行中', batch=2)
        self.agent.archive.save(self.task)
        resumed = self.agent.archive.get(self.task['id'])
        with patch('researchhelper.engine.model_object') as call:
            self.assertEqual(self.agent._extract_model_sources(resumed), expected)
        call.assert_not_called()
        self.assertEqual(resumed['gaps'], self.task['gaps'])

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

    def test_interrupted_recovery_delivers_the_retained_material_without_more_dispatches(self):
        from researchhelper.planning import question_specs

        class ProcessInterrupted(BaseException):
            pass

        store = self.agent.archive.root
        agent = ResearchAgent(ResearchArchive(store), ROOT, offline=False, require_argument_support=True)
        self.addCleanup(agent.close_runtime)
        agent.model = SimpleNamespace()
        initial = agent.archive.get(self.task['id'])
        initial['ledger']['limits']['model_calls'] = 5
        agent.archive.save(initial)
        calls = []

        def model_reply(model, task, phase, payload, fields):
            calls.append(phase)
            charge(task['ledger'], 'model_calls')
            if calls == ['plan']:
                return dict(questions=question_specs(task), data_requests=[], calculations=[]), dict(phase='plan')
            if calls == ['plan', 'extract']:
                task['ledger']['dispatches'].append(copy.deepcopy(self.task['ledger']['dispatches'][-1]))
                raise ModelCompletionError('Isolated complete extraction JSON rejection')
            self.assertEqual(calls, ['plan', 'extract', 'extract'])
            return copy.deepcopy(self.proposal), dict(self.receipt)

        save = agent._save_model_checkpoint

        def interrupt_after_receipt(task):
            save(task)
            state = task.get('reasoning_state', {})
            if (state.get('extraction_submission_recovery', {}).get('status') == '已提交'
                    and 'extraction' not in state):
                raise ProcessInterrupted

        with patch('researchhelper.engine.model_object', side_effect=model_reply), \
                patch.object(agent, '_save_model_checkpoint', side_effect=interrupt_after_receipt), \
                patch.object(agent.collector, 'external', side_effect=AssertionError('No supplier calls')):
            with self.assertRaises(ProcessInterrupted):
                agent.start(initial['id'])
        agent.close_runtime()
        self.assertEqual(calls, ['plan', 'extract', 'extract'])

        restarted = ResearchAgent(ResearchArchive(store), ROOT, offline=False, require_argument_support=True)
        self.addCleanup(restarted.close_runtime)
        restarted.model = SimpleNamespace()
        restarted.recover_interrupted()
        interrupted = restarted.archive.get(initial['id'])
        self.assertEqual(interrupted['status'], '失败')
        self.assertEqual(interrupted['ledger']['used']['model_calls'], 3)
        with patch('researchhelper.engine.model_object', side_effect=AssertionError('No repeated authoring')), \
                patch.object(restarted.collector, 'external', side_effect=AssertionError('No supplier calls')):
            done = restarted.start(initial['id'])
        self.assertEqual((done['status'], done['batch']), ('部分完成', 2), done.get('error'))
        self.assertEqual((done['ledger']['used']['model_calls'], done['ledger']['used']['data_requests']), (3, 0))
        self.assertEqual(done['materials'], interrupted['materials'])
        self.assertEqual(restarted.archive.delivery_versions(initial['id']), [1])
        self.assertIn('受限材料报告', restarted.export(initial['id'])['html'])

    def test_actual_prompt_and_receipt_include_bounded_format_feedback(self):
        from researchhelper.model import ModelService, ModelSettings
        from researchhelper.reasoning import model_object
        _, payload = self.planned_extraction()
        self.task['reasoning_state']['extraction_submission_recovery'] = dict(version=1, status='已派发')
        with TemporaryDirectory(prefix='format-feedback-') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                               api_key='FAKE_EXTRACTION_FEEDBACK_KEY'))
            service = ModelService(settings, offline=False)
            with patch.object(service, 'reason', return_value='{"evidence_specs":[]}') as call:
                _, receipt = model_object(service, self.task, 'extract', payload, {'evidence_specs'})
            self.assertIn('上次提取响应已完整返回', call.call_args.args[1][0]['content'])
            self.assertIn('不加Markdown代码围栏', call.call_args.args[1][0]['content'])
            self.assertEqual(receipt['extraction_submission_recovery_version'], 1)
