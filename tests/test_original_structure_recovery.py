"""One existing correction slot for exact original-field structure errors."""
import copy
import json
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests import ROOT
from researchhelper.contracts import ContractError, charge, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.output_schema import original_span_output_schema
from researchhelper.reasoning import extractable_source_views

RETAINED = ROOT / 'result/acceptance/one-sentence-research-ve7xgdlc/task.json'


class OriginalStructureRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.original = RETAINED.read_bytes()
        self.task = json.loads(self.original)
        self.task['original_span_diagnostics_version'] = 1
        self.payload = dict(untrusted_sources=extractable_source_views(self.task))
        self.schema = original_span_output_schema(self.task, self.payload['untrusted_sources'])
        self.event = self.task['ledger']['dispatches'][-1]
        self.event.update(output_schema_hash=fingerprint(self.schema), structured_output_diagnostic=dict(
            version=1, automatic_repair=False, submitted_values_retained=False,
            schema_issues=[dict(path='$.selections[*]', code='unknown_fields')]))
        self.index = len(self.task['ledger']['dispatches']) - 1
        self.agent = ResearchAgent.__new__(ResearchAgent)
        self.agent.model = SimpleNamespace()
        self.agent._active = Mock(return_value=self.task)
        self.agent._save_model_checkpoint = Mock()
        self.before = copy.deepcopy(self.task['ledger'])
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline recovery test'))
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(), self.original)
        self.assertEqual(self.task['ledger']['limits'], self.before['limits'])

    def recover(self):
        return self.agent._recover_completed_extraction_format(self.task, self.payload, 'private-fixture-input',
                                                               failed_dispatch_index=self.index)

    def submit(self, model, task, phase, payload, expected):
        self.assertEqual(phase, 'extract')
        self.assertEqual(payload, self.payload)
        self.assertEqual(expected, {'evidence_specs'})
        self.assertEqual(task['reasoning_state']['extraction_submission_recovery']['status'], '已派发')
        self.agent._save_model_checkpoint.assert_called()
        charge(task['ledger'], 'model_calls')
        return dict(evidence_specs=[]), dict(phase='extract')

    def test_known_exact_structure_failure_uses_one_call_same_input_and_no_new_budget(self):
        with patch('researchhelper.engine.model_object', side_effect=self.submit) as call:
            self.assertEqual(self.recover()[0], dict(evidence_specs=[]))
        self.assertEqual(call.call_count, 1)
        self.assertEqual(self.task['ledger']['used']['model_calls'], self.before['used']['model_calls'] + 1)
        self.assertEqual(self.task['ledger']['used']['data_requests'], self.before['used']['data_requests'])
        marker = self.task['reasoning_state']['extraction_submission_recovery']
        self.assertEqual(marker['format_issue']['category'], 'completed_original_structure')
        self.assertFalse(marker['automatic_repair'])
        self.assertFalse(marker['further_acquisition_allowed'])
        self.assertEqual(self.task['evidence'], [])

    def test_semantic_reference_binding_and_range_errors_do_not_gain_a_retry(self):
        for issue in [dict(path='$.binding_ref', code='enum_mismatch'),
                      dict(path='$.selections[*].number_ref', code='pattern_mismatch'),
                      dict(path='$', code='context_schema_mismatch'),
                      dict(path='$.selections[*].text_span.start', code='bound_mismatch')]:
            self.event['structured_output_diagnostic']['schema_issues'] = [issue]
            with self.subTest(issue=issue), patch('researchhelper.engine.model_object') as call:
                self.assertIsNone(self.recover())
                call.assert_not_called()

    def test_forged_diagnostic_path_and_changed_schema_are_not_trusted(self):
        self.event['structured_output_diagnostic']['schema_issues'] = [dict(path='$.PRIVATE_UNKNOWN', code='missing_field')]
        with patch('researchhelper.engine.model_object') as call:
            self.assertIsNone(self.recover())
            call.assert_not_called()
        self.event['structured_output_diagnostic']['schema_issues'] = [dict(path='$', code='unknown_fields')]
        self.event['output_schema_hash'] = 'foreign'
        with patch('researchhelper.engine.model_object') as call:
            self.assertIsNone(self.recover())
            call.assert_not_called()

    def test_unknown_incomplete_prior_batch_or_other_phase_never_resubmits(self):
        original = copy.deepcopy(self.event)
        for changed in [dict(status='已派发'), dict(response_body_complete=False),
                        dict(finish_reason='length'), dict(research_phase='review_support'),
                        dict(batch=self.task['batch'] - 1), dict(failure_code='http_failure')]:
            self.event.clear()
            self.event.update(original)
            self.event.update(changed)
            with self.subTest(changed=changed), patch('researchhelper.engine.model_object') as call:
                self.assertIsNone(self.recover())
                call.assert_not_called()

    def test_three_remaining_calls_and_delivery_time_are_protected(self):
        self.task['ledger']['used']['model_calls'] = self.task['ledger']['limits']['model_calls'] - 3
        with patch('researchhelper.engine.model_object') as call:
            self.assertIsNone(self.recover())
            call.assert_not_called()
        self.task['ledger']['used'] = dict(self.before['used'], seconds=550)
        with patch('researchhelper.engine.model_object') as call:
            self.assertIsNone(self.recover())
            call.assert_not_called()

    def test_shared_slot_second_failure_and_resume_do_not_reset_correction(self):
        def rejected(*args):
            self.submit(*args)
            raise ContractError('Independent second rejection')
        with patch('researchhelper.engine.model_object', side_effect=rejected) as call:
            self.assertIsNone(self.recover())
            self.assertIsNone(self.recover())
        self.assertEqual(call.call_count, 1)
        self.assertEqual(self.task['reasoning_state']['extraction_submission_recovery']['status'], '未采用')

    def test_legacy_tasks_keep_the_original_stop_behavior(self):
        self.task.pop('original_span_diagnostics_version')
        with patch('researchhelper.engine.model_object') as call:
            self.assertIsNone(self.recover())
            call.assert_not_called()
