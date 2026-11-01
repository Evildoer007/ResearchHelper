"""Question-bound authoring choices; never approve or reopen a real study."""
import copy
import json
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import budget, context, evidence_applies_to_answer
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (StructuredOutputError, analysis_output_schema,
    analysis_selected_questions, decode_analysis_citations)
from researchhelper.reasoning import analysis_numeric_review
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.planning import input_fingerprint


class QuestionBoundAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.task = dict(id='question_scope_fixture', version=1, batch=1,
            cutoff='2026-10-16T08:00:00+08:00', input_hash='fixture',
            analysis_citation_encoding=2, analysis_question_scope_version=1,
            questions=[dict(id='q1'), dict(id='q2'), dict(id='q3')],
            evidence=[dict(id=ident, question_ids=scope, unit='元', value=value)
                      for ident, scope, value in [('one', ['q1'], 100), ('two', ['q2'], 20),
                                                 ('shared', ['q1', 'q2'], 50)]],
            calculations=[dict(id='margin', method='net_profit_margin_v1', question_ids=['q1'],
                input_refs=['one', 'shared'], output=dict(value=50))], ledger=budget())
        self.wire = dict(version=2, binding=context(self.task), answers=[dict(question_id='q1',
            segments=[dict(text='披露内容。', refs=['one'], counter_refs=[])],
            knowledge_refs=[], provenance_refs=[])], theses=[])
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline test'))
        guard.start()
        self.addCleanup(guard.stop)

    def decode(self, wire=None, task=None, schema=None):
        task = task or self.task
        return decode_analysis_citations(wire or self.wire,
            schema or analysis_output_schema(task), task)

    def test_multiple_answers_each_use_their_own_finite_scope(self):
        wire = copy.deepcopy(self.wire)
        wire['answers'].append(dict(question_id='q2', segments=[dict(text='另一依据。', refs=['two'], counter_refs=[])],
                                   knowledge_refs=[], provenance_refs=[]))
        result = self.decode(wire)
        self.assertEqual(result['answers']['q1']['refs'], ['one'])
        self.assertEqual(result['answers']['q2']['refs'], ['two'])
        self.assertNotIn('approved', result)

    def test_wrong_question_prose_and_counter_citations_reject_before_analysis(self):
        for field in ('refs', 'counter_refs'):
            wire = copy.deepcopy(self.wire)
            wire['answers'][0]['segments'][0][field] = ['two']
            with self.assertRaises(StructuredOutputError):
                self.decode(wire)

    def test_wrong_question_numeric_and_calculation_segments_reject(self):
        for segment, qid in ((dict(source='evidence', ref='two', path='value', format='signed', digits=2), 'q1'),
                             (dict(source='calculation', ref='margin', path='value', format='signed', digits=2), 'q2')):
            wire = copy.deepcopy(self.wire)
            wire['answers'][0].update(question_id=qid, segments=[segment])
            with self.assertRaises(StructuredOutputError):
                self.decode(wire)

    def test_shared_original_remains_selectable_for_each_question(self):
        for qid in ('q1', 'q2'):
            wire = copy.deepcopy(self.wire)
            wire['answers'][0].update(question_id=qid,
                segments=[dict(source='evidence', ref='shared', path='value', format='signed', digits=2)])
            self.assertEqual(self.decode(wire)['answers'][qid]['refs'], ['shared'])

    def test_verbatim_quote_choice_is_bound_to_its_own_question(self):
        task = copy.deepcopy(self.task)
        task.update(argument_support_contract=2, verbatim_evidence_segments_version=1)
        task['evidence'].append(dict(id='quote_two', task_id=task['id'], research_version=1,
            question_ids=['q2'], source_check='通过', applicability_check='通过',
            artifact=dict(kind='material'), unit='不适用', text='仅是第二题的原文。'))
        wire = copy.deepcopy(self.wire)
        wire['answers'][0]['segments'] = [dict(quote_ref='quote_two')]
        with self.assertRaises(StructuredOutputError):
            self.decode(wire, task)
        wire['answers'][0]['question_id'] = 'q2'
        self.assertEqual(self.decode(wire, task)['answers']['q2']['refs'], ['quote_two'])

    def test_new_policy_is_input_bound_not_applied_to_fixtures_or_documents(self):
        from researchhelper.contracts import DOCUMENT_CONTENT_INTENT
        with tempfile.TemporaryDirectory(prefix='question-policy-', dir=ROOT / 'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('分析制造业PMI生产与新订单')
                for policy in ('navigation_question_scope_version', 'literal_quantity_reference_version', 'original_span_diagnostics_version',
                               'review_basis_presentation_version', 'analysis_question_scope_version', 'statistical_reading_version'):
                    self.assertEqual(task[policy], 5 if policy == 'literal_quantity_reference_version' else 1)
                    changed = dict(task)
                    changed.pop(policy)
                    self.assertNotEqual(input_fingerprint(task), input_fingerprint(changed))
                for other in (agent.create_case('mechanism'), agent.create('核对文件', intent=DOCUMENT_CONTENT_INTENT)):
                    self.assertNotIn('analysis_question_scope_version', other)
                    self.assertNotIn('statistical_reading_version', other)
            finally:
                agent.close_runtime()

    def test_selected_calculation_still_brings_only_its_registered_inputs(self):
        wire = copy.deepcopy(self.wire)
        wire['answers'][0]['segments'] = [dict(source='calculation', ref='margin', path='value', format='signed', digits=2)]
        answer = self.decode(wire)['answers']['q1']
        self.assertEqual(answer['refs'], ['one', 'shared'])
        self.assertEqual(answer['calculation_refs'], ['margin'])

    def test_empty_question_catalogue_does_not_offer_unconstrained_references(self):
        wire = copy.deepcopy(self.wire)
        wire['answers'][0]['question_id'] = 'q3'
        with self.assertRaises(StructuredOutputError):
            self.decode(wire)
        wire['answers'][0]['segments'][0]['refs'] = []
        self.assertEqual(self.decode(wire)['answers']['q3']['refs'], [])

    def test_question_scope_cannot_be_widened_by_forged_schema(self):
        schema = analysis_output_schema(self.task)
        schema['properties']['answers']['items']['anyOf'][0]['properties']['question_id']['enum'].append('q2')
        with self.assertRaises(StructuredOutputError):
            self.decode(schema=schema)

    def test_task_and_schema_not_mutated_and_saved_protocol_not_upgraded(self):
        before = copy.deepcopy((self.task, self.wire))
        self.decode()
        self.assertEqual((self.task, self.wire), before)
        task = copy.deepcopy(self.task)
        task.pop('analysis_question_scope_version')
        old = analysis_output_schema(task)
        self.assertIn('properties', old['properties']['answers']['items'])
        self.assertNotIn('anyOf', old['properties']['answers']['items'])
        # Preserve old generation, not claim the old union is financially valid.
        wire = copy.deepcopy(self.wire)
        wire['answers'][0]['segments'][0]['refs'] = ['two']
        self.assertEqual(self.decode(wire, task)['answers']['q1']['refs'], ['two'])

    def test_single_question_shape_and_selected_scope_reconstruct(self):
        single = analysis_output_schema(self.task, ['q2'])
        self.assertIn('properties', single['properties']['answers']['items'])
        self.assertEqual(analysis_selected_questions(single), ['q2'])
        self.assertEqual(analysis_selected_questions(analysis_output_schema(self.task)), ['q1', 'q2', 'q3'])

    def test_model_transport_accepts_question_bound_provider_schema_once(self):
        response = dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                         content=[dict(type='output_text', text=json.dumps(self.wire))])], usage={})
        with tempfile.TemporaryDirectory(prefix='question-scope-', dir=ROOT / 'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                               api_key='OFFLINE_FIXTURE_KEY', reasoning_effort='low'))
            model = ModelService(settings, offline=False)
            with patch.object(model, '_request_bytes', return_value=json.dumps(response).encode()) as transport:
                result = json.loads(model.reason_structured_phase(self.task, [], 'analyze', analysis_output_schema(self.task)))
            self.assertEqual(result, self.decode())
            self.assertEqual(transport.call_count, 1)
            self.assertEqual(self.task['ledger']['used']['model_calls'], 1)

    def test_retained_macro_wrong_question_references_are_no_longer_advertised(self):
        path = ROOT / 'result/acceptance/one-sentence-research-v4zeo1mq/task.json'
        original = path.read_bytes()
        task = json.loads(original)
        task['analysis_question_scope_version'] = 1
        proposal = task['reasoning_state']['rejected_analysis_submissions'][0]['proposal']['answers']['q4']
        evidence = {item['id']: item for item in task['evidence']}
        invalid = [ref for ref in proposal['refs'] if not evidence_applies_to_answer(task, evidence[ref], 'q4')]
        self.assertTrue(invalid)
        wire = dict(version=2, binding=context(task), answers=[dict(question_id='q4',
            segments=[dict(text='仅回放引用，不批准原判断。', refs=[invalid[0]], counter_refs=[])],
            knowledge_refs=[], provenance_refs=[])], theses=[])
        with self.assertRaises(StructuredOutputError):
            self.decode(wire, task)
        self.assertEqual(path.read_bytes(), original)

    def test_rejection_feedback_names_registered_question_and_wrong_scope_only(self):
        path = ROOT / 'result/acceptance/one-sentence-research-v4zeo1mq/task.json'
        original = path.read_bytes()
        task = json.loads(original)
        task['analysis_question_scope_version'] = 1
        proposal = task['reasoning_state']['rejected_analysis_submissions'][0]['proposal']
        review = analysis_numeric_review(task, proposal)
        self.assertFalse(review['accepted'])
        q4 = next(item for item in review['question_failures'] if item['question_id'] == 'q4')
        self.assertEqual(q4['code'], 'reference_outside_question')
        self.assertTrue(q4['reference_ids'])
        self.assertTrue(set(q4['reference_ids']).issubset({item['id'] for item in task['evidence']}))
        self.assertEqual(set(q4), {'question_id', 'code', 'reference_ids'})
        self.assertFalse(review['approval'])
        legacy = dict(task)
        legacy.pop('analysis_question_scope_version')
        self.assertNotIn('question_failures', analysis_numeric_review(legacy, proposal))
        self.assertEqual(path.read_bytes(), original)
