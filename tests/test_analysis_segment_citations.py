"""Explicit author-selected citations; no live service or semantic approval."""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.contracts import budget, context
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (ANALYSIS_CITATION_FUNCTION, ANALYSIS_CITATION_PROTOCOL,
    StructuredOutputError, analysis_output_schema, analysis_protocol, decode_analysis_citations,
    decode_analysis_output, structured_json_completion)

ROOT = Path(__file__).resolve().parents[1]


class AnalysisSegmentCitationTests(unittest.TestCase):
    def setUp(self):
        self.task = dict(id='rh_segment_fixture', version=1, batch=1,
            cutoff='2026-10-16T08:00:00+08:00', input_hash='explicit-fixture',
            analysis_citation_encoding=2, questions=[dict(id='q1'), dict(id='q2')],
            evidence=[dict(id=ident, question_ids=['q1'], unit='元', value=value)
                      for ident, value in [('income', 100), ('profit', 20), ('inventory', 50), ('cause', 10)]] +
                     [dict(id='other_question', question_ids=['q2'], unit='元', value=3)],
            calculations=[dict(id='margin', method='net_profit_margin_v1', question_ids=['q1'],
                input_refs=['profit', 'income'], output=dict(value=20))], ledger=budget())
        self.wire = dict(version=2, binding=context(self.task), answers=[dict(question_id='q1',
            segments=[dict(text='存货变化见披露。', refs=['inventory'], counter_refs=[]),
                      dict(source='calculation', ref='margin', path='value', format='signed', digits=2)],
            knowledge_refs=[], provenance_refs=[])], theses=[])
        self.schema = analysis_output_schema(self.task, ['q1'])
        self.original = copy.deepcopy((self.task, self.wire, self.schema))
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external network'))
        guard.start()
        self.addCleanup(guard.stop)

    def decode(self, wire=None, schema=None, task=None):
        return decode_analysis_citations(self.wire if wire is None else wire,
            self.schema if schema is None else schema, self.task if task is None else task)

    def test_only_explicit_prose_and_registered_calculation_inputs_are_cited(self):
        answer = self.decode()['answers']['q1']
        self.assertEqual(answer['refs'], ['inventory', 'profit', 'income'])
        self.assertEqual(answer['calculation_refs'], ['margin'])
        self.assertNotIn('cause', answer['refs'])
        self.assertNotIn('other_question', answer['refs'])
        self.assertEqual(answer['numeric_segments']['answer'][0], {'text': '存货变化见披露。'})
        self.assertEqual(answer['numeric_segments']['answer'][1], self.wire['answers'][0]['segments'][1])

    def test_mention_without_explicit_citation_does_not_gain_global_evidence(self):
        wire = copy.deepcopy(self.wire)
        wire['answers'][0]['segments'] = [dict(text='存货变化见披露。', refs=[], counter_refs=[])]
        answer = self.decode(wire)['answers']['q1']
        self.assertEqual(answer['refs'], [])
        self.assertNotIn('approved', answer)

    def test_fact_quantity_cites_only_its_selected_original(self):
        wire = copy.deepcopy(self.wire)
        wire['answers'][0]['segments'] = [dict(source='evidence', ref='inventory', path='value',
                                             format='signed', digits=0)]
        answer = self.decode(wire)['answers']['q1']
        self.assertEqual(answer['refs'], ['inventory'])
        self.assertEqual(answer['calculation_refs'], [])

    def test_thesis_keeps_support_counter_and_complete_unchanged_prose(self):
        wire = copy.deepcopy(self.wire)
        wire['theses'] = [dict(title=[dict(text='变化原因尚不足以解释本期水平。', refs=['cause'], counter_refs=[])],
            judgment='证据不足', question_ids=['q1'],
            investment_meaning=[dict(text='仍需核对组成。', refs=[], counter_refs=['inventory'])],
            limitation=[dict(text='不能推出投资收益。', refs=['cause'], counter_refs=[])],
            knowledge_refs=[], tracking=dict(applicable=False, indicator='', source='', window='',
                                             condition='', benchmark=''))]
        thesis = self.decode(wire)['theses'][0]
        self.assertEqual(thesis['support'], ['cause'])
        self.assertEqual(thesis['counter'], ['inventory'])
        self.assertEqual(thesis['calculation_refs'], [])
        self.assertEqual(thesis['numeric_segments']['title'], [{'text': '变化原因尚不足以解释本期水平。'}])

    def test_counter_sources_are_still_in_answer_direct_reference_set(self):
        wire = copy.deepcopy(self.wire)
        wire['answers'][0]['segments'] = [dict(text='相反依据。', refs=[], counter_refs=['cause'])]
        self.assertEqual(self.decode(wire)['answers']['q1']['refs'], ['cause'])

    def test_repeated_reference_across_segments_is_merged_in_order(self):
        wire = copy.deepcopy(self.wire)
        wire['answers'][0]['segments'].append(dict(text='补充解释。', refs=['inventory', 'cause'], counter_refs=[]))
        self.assertEqual(self.decode(wire)['answers']['q1']['refs'], ['inventory', 'profit', 'income', 'cause'])

    def test_duplicate_in_one_explicit_reference_list_is_rejected(self):
        wire = copy.deepcopy(self.wire)
        wire['answers'][0]['segments'][0]['refs'] *= 2
        with self.assertRaises(StructuredOutputError):
            self.decode(wire)

    def test_unknown_and_other_question_citations_are_not_accepted(self):
        for reference in ('invented', 'other_question'):
            wire = copy.deepcopy(self.wire)
            wire['answers'][0]['segments'][0]['refs'] = [reference]
            with self.assertRaises(StructuredOutputError):
                self.decode(wire)

    def test_missing_segment_reference_fields_are_not_filled(self):
        for field in ('refs', 'counter_refs'):
            wire = copy.deepcopy(self.wire)
            wire['answers'][0]['segments'][0].pop(field)
            with self.assertRaises(StructuredOutputError):
                self.decode(wire)

    def test_old_root_reference_fields_cannot_override_new_segment_choices(self):
        wire = copy.deepcopy(self.wire)
        wire['answers'][0]['refs'] = ['cause']
        with self.assertRaises(StructuredOutputError):
            self.decode(wire)

    def test_missing_or_invalid_registered_calculation_input_is_rejected(self):
        for inputs in (None, ['invented'], ['profit', 'profit']):
            task = copy.deepcopy(self.task)
            task['calculations'][0]['input_refs'] = inputs
            with self.assertRaises(StructuredOutputError):
                self.decode(task=task)

    def test_task_version_batch_cutoff_and_input_binding_cannot_be_reused(self):
        for key, value in (('id', 'foreign'), ('version', 2), ('batch', 2),
                           ('cutoff', '2026-10-15T08:00:00+08:00'), ('input_hash', 'changed')):
            wire = copy.deepcopy(self.wire)
            wire['binding'][key] = value
            with self.assertRaises(StructuredOutputError):
                self.decode(wire)

    def test_missing_task_or_legacy_task_cannot_decode_new_protocol(self):
        with self.assertRaises(StructuredOutputError):
            decode_analysis_citations(self.wire, self.schema, None)
        task = copy.deepcopy(self.task)
        task.pop('analysis_citation_encoding')
        with self.assertRaises(StructuredOutputError):
            self.decode(task=task)

    def test_forged_schema_cannot_extend_the_citation_catalogue(self):
        schema = copy.deepcopy(self.schema)
        schema['properties']['binding']['properties']['id']['enum'] = ['foreign']
        wire = copy.deepcopy(self.wire)
        wire['binding']['id'] = 'foreign'
        with self.assertRaises(StructuredOutputError):
            self.decode(wire, schema)

    def test_duplicate_question_answers_are_not_overwritten(self):
        wire = copy.deepcopy(self.wire)
        wire['answers'].append(copy.deepcopy(wire['answers'][0]))
        with self.assertRaises(StructuredOutputError):
            self.decode(wire)

    def test_conversion_does_not_mutate_input_or_approve_financial_quality(self):
        decoded = self.decode()
        self.assertEqual((self.task, self.wire, self.schema), self.original)
        self.assertEqual(set(decoded), {'answers', 'theses'})
        self.assertNotIn('argument_support_reviews', decoded)

    def test_legacy_schema_and_decoder_keep_the_original_explicit_root_citations(self):
        task = copy.deepcopy(self.task)
        task.pop('analysis_citation_encoding')
        self.assertEqual(analysis_protocol(task), ('strict_analysis_v1', 'rh_submit_analysis_v1'))
        schema = analysis_output_schema(task, ['q1'])
        wire = dict(version=1, answers=[dict(question_id='q1', segments=[dict(text='原版本正文。')],
            refs=['cause'], calculation_refs=[], knowledge_refs=[], provenance_refs=[])], theses=[])
        self.assertEqual(decode_analysis_output(wire, schema)['answers']['q1']['refs'], ['cause'])

    def test_responses_decoder_routes_only_the_explicit_new_protocol(self):
        value = json.loads(structured_json_completion(json.dumps(self.wire), self.schema,
            protocol=ANALYSIS_CITATION_PROTOCOL, context=self.task))
        self.assertEqual(value, self.decode())
        with self.assertRaises(StructuredOutputError):
            structured_json_completion(json.dumps(self.wire), self.schema)

    def test_model_normal_and_reserved_analysis_select_the_same_new_protocol(self):
        with tempfile.TemporaryDirectory(prefix='segment-model-', dir=ROOT / 'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                               api_key='FAKE_SEGMENT_KEY'))
            model = ModelService(settings, offline=False)
            with patch.object(model, 'reason', return_value='{}') as call:
                model.reason_structured_phase(self.task, [], 'analyze', self.schema)
                self.assertEqual(call.call_args.kwargs['output_protocol'], ANALYSIS_CITATION_PROTOCOL)
                model.reason_final_analysis(self.task, [], self.schema)
                self.assertEqual(call.call_args.kwargs['output_protocol'], ANALYSIS_CITATION_PROTOCOL)
                self.assertTrue(call.call_args.kwargs['delivery'])
        self.assertEqual(analysis_protocol(self.task), (ANALYSIS_CITATION_PROTOCOL, ANALYSIS_CITATION_FUNCTION))

    def test_actual_model_transport_and_decoder_preserve_original_reasoning(self):
        response = dict(object='response', status='completed', error=None, incomplete_details=None,
            output=[dict(type='message', role='assistant', status='completed',
                         content=[dict(type='output_text', text=json.dumps(self.wire))])], usage={})
        with tempfile.TemporaryDirectory(prefix='segment-transport-', dir=ROOT / 'result/acceptance') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                api_key='FAKE_SEGMENT_KEY', reasoning_effort='low'))
            model = ModelService(settings, offline=False)
            with patch.object(model, '_request_bytes', return_value=json.dumps(response).encode()) as call:
                decoded = json.loads(model.reason_structured_phase(self.task, [], 'analyze', self.schema))
            self.assertEqual(decoded, self.decode())
            body = json.loads(call.call_args.args[1].data)
            self.assertEqual(body['text']['format']['name'], ANALYSIS_CITATION_FUNCTION)
            self.assertEqual(body['reasoning'], {'effort': 'low'})
            self.assertEqual(self.task['ledger']['used']['model_calls'], 1)
            self.assertEqual(self.task['ledger']['used']['data_requests'], 0)

    def test_empty_catalogue_does_not_create_citations(self):
        task = dict(copy.deepcopy(self.task), evidence=[], calculations=[])
        schema = analysis_output_schema(task, ['q1'])
        wire = dict(version=2, binding=context(task), answers=[dict(question_id='q1',
            segments=[dict(text='尚无资料。', refs=[], counter_refs=[])], knowledge_refs=[], provenance_refs=[])], theses=[])
        self.assertEqual(self.decode(wire, schema, task)['answers']['q1']['refs'], [])
        wire['answers'][0]['segments'][0]['refs'] = ['invented']
        with self.assertRaises(StructuredOutputError):
            self.decode(wire, schema, task)

    def test_formal_new_task_prompt_and_receipt_use_explicit_segment_selection(self):
        from researchhelper.archive import ResearchArchive
        from researchhelper.contracts import DOCUMENT_CONTENT_INTENT
        from researchhelper.engine import ResearchAgent
        from researchhelper.reasoning import analysis_contract, model_object, PHASE_FIELDS
        with tempfile.TemporaryDirectory(prefix='segment-formal-', dir=ROOT / 'result/acceptance') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('研究这个标的的现金流变化与本期组成')
                self.assertEqual(task['analysis_citation_encoding'], 2)
                self.assertEqual(task['argument_context_version'], 2)
                self.assertEqual(task['extraction_submission_recovery_version'], 1)
                self.assertNotIn('analysis_citation_encoding', agent.create_case('etf'))
                self.assertNotIn('analysis_citation_encoding', agent.create('核对原文', intent=DOCUMENT_CONTENT_INTENT))
                settings = ModelSettings(folder)
                settings.save(dict(endpoint='https://api.deepseek.com/responses', model='deepseek-flash',
                                   api_key='FAKE_SEGMENT_KEY'))
                model = ModelService(settings, offline=False)
                contract = analysis_contract(task)
                self.assertEqual(contract['citation_composition']['version'], 2)
                with patch.object(model, 'reason_structured_phase', return_value='{"answers":{},"theses":[]}') as call:
                    _, receipt = model_object(model, task, 'analyze',
                        {'questions': task['questions'], 'analysis_contract': contract}, PHASE_FIELDS['analyze'])
                prompt = json.dumps(call.call_args.args[1], ensure_ascii=False)
                self.assertIn('只按固定结果容器rh_submit_analysis_v2提交版本2', prompt)
                self.assertIn('不在回答、论点或复核维度根部重抄', prompt)
                self.assertNotIn('refs和calculation_refs仍必须包含实际引用', prompt)
                self.assertEqual(receipt['output_protocol'], ANALYSIS_CITATION_PROTOCOL)
                self.assertEqual(receipt['analysis_citation_encoding'], 2)
            finally:
                for worker in agent.close_runtime():
                    worker.join(timeout=5)
                    self.assertFalse(worker.is_alive())
