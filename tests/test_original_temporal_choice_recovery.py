"""Bounded choice resubmission fixtures; no real model or data acceptance."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from researchhelper.contracts import ContractError, budget, charge, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.output_schema import (object_schema, array_schema, registered_temporal_choice_issues)

PATH = '$.selections[*].temporal_reference.observation_anchor'
POLICY = 'original_temporal_choice_recovery_version'


class OriginalTemporalChoiceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.schema = object_schema(dict(version=dict(type='integer', enum=[12]),
            selections=array_schema(object_schema(dict(temporal_reference=dict(anyOf=[
                object_schema(dict(observation_anchor=dict(type='integer', enum=[0, 2]))),
                object_schema(dict(period_ref=dict(type='string', enum=['p1'])))]))))))
        self.event = dict(kind='model_calls', research_phase='extract', status='失败', batch=1,
            failure_code='structured_output_rejected', structured_failure_category='invalid_original_spans',
            provider_completion_status='completed', response_body_complete=True, finish_reason='stop',
            output_protocol='strict_original_spans_v1', output_schema_hash=fingerprint(self.schema),
            network_request_started=True, network_worker_reaped=True,
            structured_output_diagnostic=dict(version=1, automatic_repair=False,
                submitted_values_retained=False, schema_issues=[dict(path=PATH, code='enum_mismatch')]))
        ledger = budget()
        ledger['used']['model_calls'] = 4
        ledger['dispatches'] = [self.event]
        self.task = dict(id='fixture', version=1, batch=1, intent='综合研究', status='部分完成',
            argument_support_contract=2, original_span_diagnostics_version=1,
            extraction_submission_recovery_version=1, ledger=ledger, reasoning_state={}, evidence=[],
            calculations=[], **{POLICY:1})
        self.payload = dict(untrusted_sources=[dict(id='fixture')])
        self.agent = ResearchAgent.__new__(ResearchAgent)
        self.agent.model = SimpleNamespace()
        self.agent._active = Mock(return_value=self.task)
        self.agent._save_model_checkpoint = Mock()
        schema = patch('researchhelper.output_schema.original_span_output_schema', return_value=self.schema)
        schema.start(); self.addCleanup(schema.stop)
        network = patch('socket.socket.connect', side_effect=AssertionError('Offline fixture'))
        network.start(); self.addCleanup(network.stop)

    def recover(self):
        return self.agent._recover_completed_extraction_format(self.task, self.payload,
            'same-input', failed_dispatch_index=0)

    def submit(self, model, task, phase, payload, expected):
        self.assertEqual(payload, self.payload)
        self.assertEqual(phase, 'extract')
        self.assertEqual(expected, {'evidence_specs'})
        charge(task['ledger'], 'model_calls')
        return dict(evidence_specs=[]), dict(phase='extract')

    def test_one_same_input_submission_without_source_or_approval_changes(self):
        before = copy.deepcopy(self.payload)
        with patch('researchhelper.engine.model_object', side_effect=self.submit) as call:
            self.assertEqual(self.recover()[0], dict(evidence_specs=[]))
            self.assertIsNone(self.recover())
        self.assertEqual(call.call_count, 1)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 5)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 0)
        marker = self.task['reasoning_state']['extraction_submission_recovery']
        self.assertEqual(marker['format_issue']['category'], 'completed_original_temporal_choice')
        self.assertFalse(marker['automatic_repair'])
        self.assertFalse(marker['further_acquisition_allowed'])
        self.assertEqual(self.task['evidence'], [])
        self.assertEqual(self.task['calculations'], [])
        self.assertEqual(self.payload, before)

    def test_other_enums_binding_ranges_or_mixed_rejections_do_not_retry(self):
        for path in ('$.binding_ref', '$.selections[*].source_ref', '$.selections[*].number_ref',
                     '$.selections[*].temporal_reference.period_ref', '$.PRIVATE_UNKNOWN'):
            self.event['structured_output_diagnostic']['schema_issues'] = [dict(path=path, code='enum_mismatch')]
            with self.subTest(path=path), patch('researchhelper.engine.model_object') as call:
                self.assertIsNone(self.recover()); call.assert_not_called()

    def test_incomplete_unreaped_prior_batch_or_changed_schema_never_retry(self):
        original = copy.deepcopy(self.event)
        for changes in (dict(status='已派发'), dict(response_body_complete=False),
                        dict(network_request_started=False), dict(network_worker_reaped=False),
                        dict(batch=0), dict(finish_reason='length'), dict(output_schema_hash='changed')):
            self.event.clear(); self.event.update(copy.deepcopy(original)); self.event.update(changes)
            with self.subTest(changes=changes), patch('researchhelper.engine.model_object') as call:
                self.assertIsNone(self.recover()); call.assert_not_called()

    def test_legacy_boolean_policy_and_unregistered_path_stay_stopped(self):
        for value in (None, True, False, 0, 2):
            self.task[POLICY] = value
            with self.subTest(value=value), patch('researchhelper.engine.model_object') as call:
                self.assertIsNone(self.recover()); call.assert_not_called()
        schema = object_schema(dict(observation_anchor=dict(type='integer', enum=[0])))
        self.assertIsNone(registered_temporal_choice_issues(schema, self.event['structured_output_diagnostic']))

    def test_original_delivery_and_time_reserves_are_preserved(self):
        for changes in (dict(model_calls=5), dict(seconds=550)):
            old = dict(self.task['ledger']['used'])
            self.task['ledger']['used'].update(changes)
            with self.subTest(changes=changes), patch('researchhelper.engine.model_object') as call:
                self.assertIsNone(self.recover()); call.assert_not_called()
            self.task['ledger']['used'] = old

    def test_second_failure_is_durable_and_does_not_loop(self):
        with patch('researchhelper.engine.model_object', side_effect=ContractError('Fixture second failure')) as call:
            self.assertIsNone(self.recover())
            self.assertIsNone(self.recover())
        self.assertEqual(call.call_count, 1)
        self.assertEqual(self.task['reasoning_state']['extraction_submission_recovery']['status'], '未采用')

    def test_only_values_free_registered_diagnostics_are_eligible(self):
        diagnostic = self.event['structured_output_diagnostic']
        self.assertEqual(registered_temporal_choice_issues(self.schema, diagnostic), diagnostic['schema_issues'])
        for changes in (dict(submitted_values_retained=True), dict(automatic_repair=True), dict(version=True),
                        dict(schema_issues=[]), dict(schema_issues=[dict(path=PATH, code='enum_mismatch', value=7)])):
            with self.subTest(changes=changes):
                self.assertIsNone(registered_temporal_choice_issues(self.schema, dict(diagnostic, **changes)))
