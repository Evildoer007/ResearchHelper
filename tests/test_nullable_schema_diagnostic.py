"""A nullable answer's object errors cannot be hidden by its null branch."""
import copy
import unittest

from tests import test_analysis_reference_choice_recovery as fixtures
from researchhelper.contracts import charge, context, fingerprint
from researchhelper.output_schema import (_matches, object_schema, schema_diagnostic,
    StructuredOutputError, analysis_output_schema, analysis_protocol, decode_analysis_citations,
    registered_analysis_reference_choice_issues)


class NullableSchemaDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AnalysisReferenceChoiceRecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = copy.deepcopy(self.fixture.task)
        for ident in ('two_more','two_last'):
            evidence = copy.deepcopy(next(item for item in self.task['evidence'] if item['id']=='two'))
            evidence['id'] = ident
            self.task['evidence'].append(evidence)
        self.schema = analysis_output_schema(self.task)
        self.wire = copy.deepcopy(self.fixture.wire)
        self.wire['binding'] = context(self.task)
        self.wire['answers']['q1']['segments'][0]['refs'] = ['two','two_more','two_last']
        self.before = copy.deepcopy((self.task['evidence'],self.task['calculations'],self.task['ledger']['limits']))

    def tearDown(self):
        self.assertEqual((self.task['evidence'],self.task['calculations'],self.task['ledger']['limits']),self.before)

    def reject(self, wire=None):
        with self.assertRaises(StructuredOutputError) as caught:
            decode_analysis_citations(self.wire if wire is None else wire,self.schema,self.task)
        event = dict(kind='model_calls',research_phase='analyze',status='失败',batch=self.task['batch'],
            failure_code='structured_output_rejected',structured_failure_category='schema_mismatch',
            provider_completion_status='completed',response_body_complete=True,finish_reason='stop',
            output_protocol=analysis_protocol(self.task)[0],output_schema_hash=fingerprint(self.schema),
            network_request_started=True,network_worker_reaped=True,
            structured_output_diagnostic=caught.exception.diagnostic)
        self.task['ledger']['dispatches'].append(event)
        return event

    def test_three_object_reference_errors_are_not_misdiagnosed_as_null_type(self):
        event = self.reject()
        diagnostic = event['structured_output_diagnostic']
        self.assertEqual(diagnostic['reference_categories'],[
            dict(question_id='q1',field='refs',category='evidence_out_of_scope',count=3)])
        self.assertEqual(diagnostic['schema_issues'],[
            dict(path='$.answers.q1.segments[*].refs[*]',code='enum_mismatch')]*3)
        self.assertFalse(_matches(self.wire,self.schema))

    def test_existing_scope_correction_gets_one_fresh_call_and_keeps_final_review(self):
        self.reject()
        agent = self.fixture.agent
        issue = agent._completed_analysis_format_issue(self.task)
        self.assertEqual(issue['category'],'completed_analysis_reference_choice')
        self.assertEqual(agent._recover_completed_analysis_format(self.task),self.fixture.fixture.result)
        self.assertEqual(self.task['ledger']['used']['model_calls'],7)
        charge(self.task['ledger'],'model_calls',delivery=True)
        self.assertIsNone(agent._recover_completed_analysis_format(self.task))
        self.assertEqual(agent._reason_on_verified_material.call_count,1)
        self.assertFalse(self.task['reasoning_state']['rejected_analysis_submissions'][-1]['evidence_approved'])

    def test_unknown_reference_or_binding_error_still_cannot_use_scope_correction(self):
        for issue in ('unknown','binding'):
            wire = copy.deepcopy(self.wire)
            if issue=='unknown': wire['answers']['q1']['segments'][0]['refs'].append('unknown')
            else: wire['binding']['input_hash'] = 'foreign'
            event = self.reject(wire)
            self.assertIsNone(self.fixture.agent._recover_completed_analysis_format(self.task))
            self.task['ledger']['dispatches'].remove(event)
        self.fixture.agent._reason_on_verified_material.assert_not_called()

    def test_real_scalar_answer_is_not_a_known_reference_choice_failure(self):
        wire = copy.deepcopy(self.wire); wire['answers']['q1'] = 'PRIVATE_INVALID_SCALAR'
        event = self.reject(wire)
        self.assertIsNone(registered_analysis_reference_choice_issues(self.schema,event['structured_output_diagnostic']))
        self.assertNotIn('PRIVATE_INVALID_SCALAR',str(event))

    def test_null_and_boolean_keep_their_exact_json_types_without_coercion(self):
        schema = {'anyOf':[{'type':'integer','enum':[1]},{'type':'null'}]}
        self.assertTrue(_matches(None,schema))
        self.assertTrue(_matches(1,schema))
        self.assertFalse(_matches(True,schema))
        self.assertEqual(schema_diagnostic(None,schema)['schema_issues'],[])
        self.assertFalse(_matches('1',schema))

    def test_nullable_objects_report_their_actual_unknown_and_missing_fields(self):
        schema = {'anyOf':[object_schema({'one':{'type':'string','enum':['safe']},
            'two':{'type':'integer','enum':[2]}}),{'type':'null'}]}
        diagnostic = schema_diagnostic(dict(one='PRIVATE_INVALID',PRIVATE_KEY='PRIVATE_VALUE'),schema)
        self.assertEqual(diagnostic['schema_issues'],[
            dict(path='$',code='unknown_fields'),dict(path='$.one',code='enum_mismatch'),
            dict(path='$.two',code='missing_field')])
        self.assertNotIn('PRIVATE',str(diagnostic))
        self.assertFalse(diagnostic['automatic_repair'])

    def test_nested_unions_remain_bounded_and_never_approve_wrong_values(self):
        child = object_schema({'value':{'type':'string','enum':['ok']}})
        schema = {'anyOf':[{'anyOf':[child,{'type':'array','items':child}]},{'type':'null'}]}
        diagnostic = schema_diagnostic(dict(value='wrong'),schema)
        self.assertEqual(diagnostic['schema_issues'],[dict(path='$.value',code='enum_mismatch')])
        self.assertFalse(_matches(dict(value='wrong'),schema))
        many = schema_diagnostic([dict(value='wrong')]*40,schema)
        self.assertLessEqual(len(many['schema_issues']),8)

    def test_unknown_or_unreaped_transport_does_not_gain_an_extra_submission(self):
        event = self.reject()
        event['network_worker_reaped'] = False
        self.assertIsNone(self.fixture.agent._recover_completed_analysis_format(self.task))
        self.fixture.agent._reason_on_verified_material.assert_not_called()
