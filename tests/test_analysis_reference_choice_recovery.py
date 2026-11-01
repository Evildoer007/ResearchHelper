"""One fresh citation selection after a known, complete wrong-question reply.

Existing source, numeric and financial approval checks stay unchanged. No
provider/model is contacted, and no closed study is reopened.
"""
import copy
import tempfile
import unittest

from tests import ROOT, test_analysis_structure_recovery as fixtures
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import charge, context, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.output_schema import StructuredOutputError, analysis_output_schema, analysis_protocol, decode_analysis_citations
from researchhelper.planning import input_fingerprint
from researchhelper.reports import compose

POLICY = 'analysis_reference_choice_recovery_version'


class AnalysisReferenceChoiceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AnalysisStructureRecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = self.fixture.task
        self.task.update(analysis_prose_reference_version=1, analysis_question_map_version=1, **{POLICY:1})
        self.task['ledger']['used']['model_calls'] = 6
        self.agent = self.fixture.agent
        self.schema = analysis_output_schema(self.task)
        answer = copy.deepcopy(self.fixture.fixture.wire['answers'][0])
        answer.pop('question_id')
        self.wire = dict(version=3, binding=context(self.task), answers=dict(q1=answer, q2=None, q3=None), theses=[])
        self.before = copy.deepcopy((self.task['evidence'], self.task['calculations'], self.task['questions'], self.task['ledger']['limits']))

    def tearDown(self):
        self.assertEqual((self.task['evidence'], self.task['calculations'], self.task['questions'], self.task['ledger']['limits']), self.before)

    def reject(self, ref='two', field='refs', **changes):
        wire = copy.deepcopy(self.wire)
        wire['answers']['q1']['segments'][0][field] = [ref]
        with self.assertRaises(StructuredOutputError) as caught:
            decode_analysis_citations(wire, self.schema, self.task)
        event = dict(kind='model_calls', research_phase='analyze', status='失败', batch=self.task['batch'],
            failure_code='structured_output_rejected', structured_failure_category='schema_mismatch',
            provider_completion_status='completed', response_body_complete=True, finish_reason='stop',
            output_protocol=analysis_protocol(self.task)[0], output_schema_hash=fingerprint(self.schema),
            network_request_started=True, network_worker_reaped=True,
            structured_output_diagnostic=caught.exception.diagnostic)
        event.update(changes)
        self.task['ledger']['dispatches'].append(event)
        return event

    def test_known_wrong_question_reference_gets_one_fresh_selection_and_reserves_review(self):
        self.reject()
        issue = self.agent._completed_analysis_format_issue(self.task)
        self.assertEqual(issue['category'], 'completed_analysis_reference_choice')
        self.assertFalse(issue['automatic_repair'])
        self.assertEqual(self.agent._recover_completed_analysis_format(self.task), self.fixture.result)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 7)
        charge(self.task['ledger'], 'model_calls', delivery=True)
        self.assertIsNone(self.agent._recover_completed_analysis_format(self.task))
        self.assertEqual(self.agent._reason_on_verified_material.call_count, 1)
        self.assertFalse(self.task['reasoning_state']['rejected_analysis_submissions'][-1]['evidence_approved'])

    def test_counter_reference_uses_the_same_finite_question_scope(self):
        self.reject(field='counter_refs')
        self.assertIsNotNone(self.agent._completed_analysis_format_issue(self.task))

    def test_unknown_reference_or_source_record_is_not_an_eligible_known_scope_error(self):
        for ref in ('not_registered', 'source_only'):
            self.task['source_records'] = [dict(id='source_only')]
            self.reject(ref)
            self.assertIsNone(self.agent._recover_completed_analysis_format(self.task))
        self.agent._reason_on_verified_material.assert_not_called()

    def test_unknown_transport_stale_schema_and_incomplete_reply_are_not_redispatched(self):
        for values in (dict(network_worker_reaped=False), dict(network_request_started=False),
                       dict(response_body_complete=False), dict(finish_reason='length'),
                       dict(batch=0), dict(output_schema_hash='other'), dict(status='已派发')):
            event = self.reject(**values)
            self.assertIsNone(self.agent._recover_completed_analysis_format(self.task))
            self.task['ledger']['dispatches'].remove(event)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_legacy_invalid_policy_last_call_and_delivery_time_do_not_gain_a_retry(self):
        self.reject()
        for value in (None, True, 0, 2, 1.0):
            self.task[POLICY] = value
            self.assertIsNone(self.agent._recover_completed_analysis_format(self.task))
        self.task[POLICY] = 1
        self.task['ledger']['used']['model_calls'] = 7
        self.assertIsNone(self.agent._recover_completed_analysis_format(self.task))
        self.task['ledger']['used'].update(model_calls=6, seconds=550)
        self.assertIsNone(self.agent._recover_completed_analysis_format(self.task))
        self.agent._reason_on_verified_material.assert_not_called()

    def test_mixed_diagnostic_cannot_hide_a_binding_or_value_rejection(self):
        event = self.reject()
        event['structured_output_diagnostic']['schema_issues'].append(dict(code='enum_mismatch', path='$.binding.input_hash'))
        self.assertIsNone(self.agent._recover_completed_analysis_format(self.task))
        self.agent._reason_on_verified_material.assert_not_called()

    def test_malformed_diagnostic_never_becomes_an_eligible_selection(self):
        event = self.reject()
        original = copy.deepcopy(event['structured_output_diagnostic'])
        for field, value in (('question_id', ['q1']), ('count', True), ('category', 'unregistered_reference')):
            changed = copy.deepcopy(original)
            changed['reference_categories'][0][field] = value
            event['structured_output_diagnostic'] = changed
            self.assertIsNone(self.agent._recover_completed_analysis_format(self.task))
        event['structured_output_diagnostic'] = copy.deepcopy(original)
        event['structured_output_diagnostic']['schema_issues'][0]['path'] = ['$.answers.q1']
        self.assertIsNone(self.agent._recover_completed_analysis_format(self.task))

    def test_an_unknown_saved_resubmission_is_not_repeated_after_resume(self):
        self.reject()
        self.task['reasoning_state']['numeric_resubmission'] = dict(status='已派发', reason='analysis_output_format')
        self.task['batch'] += 1
        self.assertIsNone(self.agent._recover_completed_analysis_format(self.task))
        self.agent._reason_on_verified_material.assert_not_called()

    def test_new_formal_policy_is_input_bound_and_frozen_in_saved_report(self):
        with tempfile.TemporaryDirectory(prefix='analysis-ref-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('解释ETF折溢价', entry='ETF', questions=[dict(id='q1', kind='fact',
                    question='解释ETF折溢价', answer_scope='general_definition', knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task[POLICY], 1)
                old = copy.deepcopy(task)
                old.pop(POLICY)
                self.assertNotEqual(input_fingerprint(old), task['input_hash'])
                completed = agent.start(task['id'])
                self.assertEqual(compose(completed)[POLICY], 1)
            finally:
                agent.close_runtime()
