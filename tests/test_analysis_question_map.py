"""Fixed question keys keep citation choices in their actual answer scope."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT, test_question_bound_analysis as fixtures
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import context
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (StructuredOutputError, analysis_output_schema,
    analysis_selected_questions, decode_analysis_citations)
from researchhelper.planning import input_fingerprint


class AnalysisQuestionMapTests(unittest.TestCase):
    def setUp(self):
        self.base = fixtures.QuestionBoundAnalysisTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.task = dict(copy.deepcopy(self.base.task), analysis_question_map_version=1)
        self.schema = analysis_output_schema(self.task)

    def wire(self):
        original = copy.deepcopy(self.base.wire['answers'][0])
        ident = original.pop('question_id')
        values = {qid: None for qid in analysis_selected_questions(self.schema)}
        values[ident] = original
        return dict(version=3, binding=context(self.task), answers=values, theses=[])

    def decode(self, wire=None, schema=None, task=None):
        return decode_analysis_citations(wire or self.wire(), schema or self.schema, task or self.task)

    def test_each_fixed_property_has_its_own_citation_grammar_without_question_discriminator(self):
        self.assertEqual(self.schema['properties']['version']['enum'], [3])
        answers = self.schema['properties']['answers']
        self.assertEqual(answers['type'], 'object')
        self.assertEqual(analysis_selected_questions(self.schema), ['q1', 'q2', 'q3'])
        for ident, expected in (('q1', ['one', 'shared']), ('q2', ['two', 'shared'])):
            item = answers['properties'][ident]['anyOf'][0]
            self.assertNotIn('question_id', item['properties'])
            prose = item['properties']['segments']['items']['anyOf'][0]
            self.assertEqual(prose['properties']['refs']['items']['enum'], expected)

    def test_roundtrip_preserves_original_canonical_proposal(self):
        self.assertEqual(self.decode(), self.base.decode())

    def test_two_independent_answers_do_not_borrow_each_others_sources(self):
        wire = self.wire()
        wire['answers']['q2'] = dict(segments=[dict(text='另一题依据。', refs=['two'], counter_refs=[])],
                                   knowledge_refs=[], provenance_refs=[])
        result = self.decode(wire)
        self.assertEqual(result['answers']['q1']['refs'], ['one'])
        self.assertEqual(result['answers']['q2']['refs'], ['two'])
        wire['answers']['q2']['segments'][0]['refs'] = ['one']
        with self.assertRaises(StructuredOutputError):
            self.decode(wire)

    def test_null_explicitly_withholds_answer_without_marking_it_complete(self):
        wire = self.wire()
        wire['answers'] = dict.fromkeys(wire['answers'])
        self.assertEqual(self.decode(wire)['answers'], {})
        self.assertNotIn('approved', self.decode(wire))

    def test_unknown_missing_redundant_or_nonnull_scalar_values_are_rejected(self):
        variants = []
        wire = self.wire(); wire['answers']['unknown'] = None; variants.append(wire)
        wire = self.wire(); wire['answers'].pop('q2'); variants.append(wire)
        wire = self.wire(); wire['answers']['q1']['question_id'] = 'q2'; variants.append(wire)
        for value in (0, False, 'null', []):
            wire = self.wire(); wire['answers']['q2'] = value; variants.append(wire)
        for wire in variants:
            with self.subTest(answers=type(wire['answers'])), self.assertRaises(StructuredOutputError):
                self.decode(wire)

    def test_quantity_and_computation_still_require_their_exact_scope(self):
        wire = self.wire()
        wire['answers']['q1']['segments'] = [dict(source='calculation', ref='margin', path='value', format='signed', digits=2)]
        result = self.decode(wire)['answers']['q1']
        self.assertEqual(result['calculation_refs'], ['margin'])
        self.assertEqual(result['refs'], ['one', 'shared'])
        wire['answers']['q2'] = wire['answers'].pop('q1')
        wire['answers']['q1'] = None
        with self.assertRaises(StructuredOutputError):
            self.decode(wire)

    def test_selected_subset_keeps_single_question_shape_and_exact_scope(self):
        schema = analysis_output_schema(self.task, ['q2'])
        self.assertEqual(analysis_selected_questions(schema), ['q2'])
        self.assertEqual(schema['properties']['answers']['type'], 'array')
        wire = dict(version=2, binding=context(self.task), answers=[], theses=[])
        self.assertEqual(self.decode(wire, schema)['answers'], {})

    def test_knowledge_only_questions_keep_the_original_empty_model_shape(self):
        task = copy.deepcopy(self.task)
        task['definition_projection_version'] = 1
        for question in task['questions']:
            question['answer_scope'] = 'general_definition'
        schema = analysis_output_schema(task)
        self.assertEqual(schema['properties']['version']['enum'], [2])
        self.assertEqual(schema['properties']['answers']['maxItems'], 0)
        wire = dict(version=2, binding=context(task), answers=[], theses=[])
        self.assertEqual(self.decode(wire, schema, task)['answers'], {})

    def test_forged_schema_and_changed_bindings_do_not_gain_approval(self):
        schema = copy.deepcopy(self.schema)
        item = schema['properties']['answers']['properties']['q1']['anyOf'][0]
        item['properties']['segments']['items']['anyOf'][0]['properties']['refs']['items']['enum'].append('two')
        with self.assertRaises(StructuredOutputError):
            self.decode(schema=schema)
        wire = self.wire(); wire['binding']['input_hash'] = 'foreign'
        with self.assertRaises(StructuredOutputError):
            self.decode(wire)

    def test_legacy_arrays_are_not_rewritten_or_silently_upgraded(self):
        self.assertEqual(analysis_output_schema(self.base.task)['properties']['version']['enum'], [2])
        self.assertEqual(analysis_output_schema(self.base.task)['properties']['answers']['type'], 'array')
        with self.assertRaises(StructuredOutputError):
            self.decode(self.base.wire)

    def test_two_writable_questions_and_a_definition_reconstruct_idempotently(self):
        task = copy.deepcopy(self.task)
        task['definition_projection_version'] = 1
        task['questions'][-1]['answer_scope'] = 'general_definition'
        schema = analysis_output_schema(task)
        self.assertEqual(analysis_selected_questions(schema), ['q1', 'q2'])
        self.assertEqual(schema, analysis_output_schema(task, ['q1', 'q2']))
        wire = self.wire(); wire['answers'].pop('q3')
        self.assertEqual(self.decode(wire, schema, task), self.base.decode())

    def test_actual_model_transport_uses_fixed_properties_and_existing_decoder_once(self):
        response = dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                         content=[dict(type='output_text', text=json.dumps(self.wire()))])], usage={})
        with tempfile.TemporaryDirectory(prefix='question-map-', dir=ROOT / 'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                               api_key='FAKE_QUESTION_MAP_KEY'))
            model = ModelService(settings, offline=False)
            with patch.object(model, '_request_bytes', return_value=json.dumps(response).encode()) as call:
                result = json.loads(model.reason_structured_phase(self.task, [], 'analyze', self.schema))
            self.assertEqual(call.call_count, 1)
            body = json.loads(call.call_args.args[1].data)
            self.assertEqual(body['text']['format']['schema']['properties']['answers']['type'], 'object')
        self.assertEqual(result, self.decode())
        self.assertEqual(self.task['ledger']['used']['model_calls'], 1)

    def test_new_formal_policy_is_input_bound_and_documents_or_cases_are_exempt(self):
        with tempfile.TemporaryDirectory(prefix='map-policy-', dir=ROOT / 'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create('比较基金价格')
            self.assertEqual(task['analysis_question_map_version'], 1)
            changed = dict(task); changed.pop('analysis_question_map_version')
            self.assertNotEqual(input_fingerprint(task), input_fingerprint(changed))
            self.assertNotIn('analysis_question_map_version', agent.create_case('mechanism'))
            self.assertNotIn('analysis_question_map_version', agent.create('核对文件', intent='原件内容核对'))
