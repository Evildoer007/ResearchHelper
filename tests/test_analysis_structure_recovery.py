"""Registered field errors share analysis's existing one-shot correction slot."""
import copy
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tests import ROOT, test_question_bound_analysis as fixtures
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import charge, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.output_schema import (StructuredOutputError, analysis_output_schema, analysis_protocol,
                                         decode_analysis_citations)
from researchhelper.planning import input_fingerprint


class AnalysisStructureRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.QuestionBoundAnalysisTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = self.fixture.task
        self.task.update(argument_support_contract=2, analysis_structure_recovery_version=1,
            intent='综合研究', run_mode='model', status='运行中', reasoning_state={})
        self.task['ledger']['used'].update(model_calls=5, seconds=100)
        self.schema = analysis_output_schema(self.task)
        self.result = self.fixture.decode()
        self.agent = ResearchAgent.__new__(ResearchAgent)
        self.agent.model = SimpleNamespace(supports_final_delivery=lambda: True, reason_final_analysis=lambda: None)
        self.agent._save_model_checkpoint = Mock()
        def submit(task, selected, *, final_delivery):
            self.assertFalse(final_delivery)
            charge(task['ledger'], 'model_calls')
            return copy.deepcopy(self.result)
        self.agent._reason_on_verified_material = Mock(side_effect=submit)
        self.before = copy.deepcopy((self.task['evidence'], self.task['calculations'], self.task['ledger']['limits']))

    def tearDown(self):
        self.assertEqual((self.task['evidence'], self.task['calculations'], self.task['ledger']['limits']), self.before)

    def reject(self, wire=None, **changes):
        if wire is None:
            wire = copy.deepcopy(self.fixture.wire)
            wire['answers'][0]['segments'][0].pop('counter_refs')
        with self.assertRaises(StructuredOutputError) as caught:
            decode_analysis_citations(wire, self.schema, self.task)
        event = dict(kind='model_calls', research_phase='analyze', status='失败', batch=self.task['batch'],
            failure_code='structured_output_rejected', structured_failure_category='schema_mismatch',
            provider_completion_status='completed', response_body_complete=True, finish_reason='stop',
            output_protocol=analysis_protocol(self.task)[0], output_schema_hash=fingerprint(self.schema),
            structured_output_diagnostic=caught.exception.diagnostic)
        event.update(changes)
        self.task['ledger']['dispatches'].append(event)
        return event

    def test_missing_field_is_diagnosed_and_corrected_once_without_new_data(self):
        self.reject()
        issue = self.agent._completed_analysis_format_issue(self.task)
        self.assertEqual(issue['category'], 'completed_analysis_structure')
        self.assertIn(dict(path='$.answers[*].segments[*].counter_refs', code='missing_field'), issue['schema_issues'])
        data_before = self.task['ledger']['used']['data_requests']
        result = self.agent._recover_completed_analysis_format(self.task)
        self.assertEqual(result, self.result)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 6)
        self.assertEqual(self.task['ledger']['used']['data_requests'], data_before)
        self.assertFalse(self.task['reasoning_state']['rejected_analysis_submissions'][-1]['evidence_approved'])

    def test_unknown_fields_are_not_retained_as_model_names_or_values(self):
        wire = copy.deepcopy(self.fixture.wire)
        wire['PRIVATE_UNKNOWN_FIELD'] = 'PRIVATE_VALUE'
        event = self.reject(wire)
        diagnostic = event['structured_output_diagnostic']
        self.assertNotIn('PRIVATE', json.dumps(diagnostic))
        self.assertEqual(diagnostic['schema_issues'][0], dict(path='$', code='unknown_fields'))

    def test_wrong_question_citation_is_not_a_field_correction(self):
        wire = copy.deepcopy(self.fixture.wire)
        wire['answers'][0]['segments'][0]['refs'] = ['two']
        self.reject(wire)
        self.assertIsNone(self.agent._recover_completed_analysis_format(self.task))
        self.agent._reason_on_verified_material.assert_not_called()

    def test_context_mismatch_is_distinct_and_not_corrected(self):
        changed = copy.deepcopy(self.schema)
        changed['properties']['binding']['properties']['input_hash']['enum'] = ['foreign']
        with self.assertRaises(StructuredOutputError) as caught:
            decode_analysis_citations(self.fixture.wire, changed, self.task)
        self.assertEqual(caught.exception.diagnostic['schema_issues'],
                         [dict(path='$', code='context_schema_mismatch')])

    def test_unknown_completion_stale_schema_batch_or_protocol_never_resubmits(self):
        for changes in (dict(status='已派发'), dict(response_body_complete=False), dict(finish_reason='length'),
                        dict(batch=0), dict(output_schema_hash='foreign'), dict(output_protocol='foreign'),
                        dict(failure_code='http_failure')):
            event = self.reject(**changes)
            with self.subTest(changes=changes):
                self.assertIsNone(self.agent._recover_completed_analysis_format(self.task))
            self.task['ledger']['dispatches'].remove(event)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_delivery_time_and_review_call_reserves_are_kept(self):
        self.reject()
        for used in (dict(model_calls=7, seconds=100), dict(model_calls=5, seconds=550)):
            self.task['ledger']['used'].update(used)
            with self.subTest(used=used):
                self.assertIsNone(self.agent._recover_completed_analysis_format(self.task))
        self.agent._reason_on_verified_material.assert_not_called()

    def test_format_numeric_and_resume_share_the_existing_one_slot(self):
        self.reject()
        self.agent._recover_completed_analysis_format(self.task)
        self.assertIsNone(self.agent._recover_completed_analysis_format(self.task))
        self.assertEqual(self.agent._reason_on_verified_material.call_count, 1)

    def test_legacy_policy_keeps_stopping_at_the_original_rejection(self):
        self.reject()
        self.task.pop('analysis_structure_recovery_version')
        self.assertIsNone(self.agent._recover_completed_analysis_format(self.task))

    def test_policy_is_new_formal_only_and_input_bound(self):
        with tempfile.TemporaryDirectory(prefix='analysis-policy-', dir=ROOT / 'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create('研究公开统计数据')
            for policy in ('analysis_feedback_projection_version', 'analysis_structure_recovery_version', 'argument_choice_recovery_version'):
                self.assertEqual(task[policy], 1)
                changed = dict(task)
                changed.pop(policy)
                self.assertNotEqual(input_fingerprint(task), input_fingerprint(changed))
                self.assertNotIn(policy, agent.create_case('mechanism'))
                self.assertNotIn(policy, agent.create('核对原件', intent='原件内容核对'))
