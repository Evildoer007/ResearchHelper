"""Explicit same-question calculated support; no inferred or repaired refs."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT, test_question_bound_analysis as fixtures
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, context
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (StructuredOutputError, analysis_output_schema,
    decode_analysis_citations)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import analysis_numeric_review


class AnalysisProseCalculationTests(unittest.TestCase):
    def setUp(self):
        self.base = fixtures.QuestionBoundAnalysisTests()
        self.base.setUp(); self.addCleanup(self.base.doCleanups)
        self.task = dict(copy.deepcopy(self.base.task),analysis_prose_reference_version=1)
        self.wire = copy.deepcopy(self.base.wire)
        self.wire['answers'][0]['segments'] = [dict(text='计算结果仅描述同口径比率。',
            refs=['margin'],counter_refs=[])]

    def decode(self, wire=None, task=None, schema=None):
        task = self.task if task is None else task
        return decode_analysis_citations(self.wire if wire is None else wire,
            analysis_output_schema(task) if schema is None else schema,task)

    def test_explicit_calculation_support_restores_only_its_complete_inputs(self):
        result = self.decode()['answers']['q1']
        self.assertEqual(result['refs'],['one','shared'])
        self.assertEqual(result['calculation_refs'],['margin'])
        self.assertEqual(result['numeric_segments']['answer'],[dict(text='计算结果仅描述同口径比率。')])
        self.assertNotIn('approved',result)

    def test_direct_fact_and_numeric_result_keep_order_and_deduplicate_support(self):
        wire = copy.deepcopy(self.wire)
        wire['answers'][0]['segments'] += [dict(text='输入披露另行保留。',refs=['one'],counter_refs=[]),
            dict(source='calculation',ref='margin',path='value',format='signed',digits=2)]
        result = self.decode(wire)['answers']['q1']
        self.assertEqual(result['refs'],['one','shared'])
        self.assertEqual(result['calculation_refs'],['margin'])

    def test_other_question_calculation_does_not_enter_the_reference_pool(self):
        wire = copy.deepcopy(self.wire);wire['answers'][0]['question_id']='q2'
        with self.assertRaises(StructuredOutputError) as caught:self.decode(wire)
        self.assertEqual(caught.exception.diagnostic['reference_categories'],[
            dict(question_id='q2',field='refs',category='calculation_out_of_scope',count=1)])

    def test_counter_reference_stays_a_fact_not_a_calculation(self):
        wire = copy.deepcopy(self.wire)
        wire['answers'][0]['segments'][0].update(refs=[],counter_refs=['margin'])
        with self.assertRaises(StructuredOutputError):self.decode(wire)

    def test_unknown_and_source_record_refs_remain_rejected_without_value_retention(self):
        self.task['source_records']=[dict(id='synthetic_original_record')]
        wire = copy.deepcopy(self.wire)
        wire['answers'][0]['segments'][0]['refs']=['synthetic_original_record','PRIVATE_REJECTED_TOKEN']
        with self.assertRaises(StructuredOutputError) as caught:self.decode(wire)
        diagnostic = caught.exception.diagnostic
        self.assertEqual({row['category'] for row in diagnostic['reference_categories']},
                         {'source_record_not_evidence','unregistered_reference'})
        self.assertNotIn('PRIVATE_REJECTED_TOKEN',json.dumps(diagnostic))
        self.assertNotIn('synthetic_original_record',json.dumps(diagnostic))
        self.assertFalse(diagnostic['submitted_values_retained'])
        self.assertFalse(diagnostic['automatic_repair'])

    def test_legacy_keeps_rejecting_calculation_ids_in_prose(self):
        task = copy.deepcopy(self.task);task.pop('analysis_prose_reference_version')
        with self.assertRaises(StructuredOutputError):self.decode(task=task)

    def test_ambiguous_cross_kind_names_cannot_choose_a_meaning_by_order(self):
        self.task['evidence'].append(dict(self.task['evidence'][0],id='margin'))
        with self.assertRaises(ContractError):analysis_output_schema(self.task)

    def test_fixed_question_map_keeps_calculation_in_its_own_question(self):
        self.task['analysis_question_map_version']=1
        wire = copy.deepcopy(self.wire)
        answer = wire['answers'][0];answer.pop('question_id')
        wire.update(version=3,answers=dict(q1=answer,q2=None,q3=None))
        self.assertEqual(self.decode(wire)['answers']['q1']['calculation_refs'],['margin'])
        wire['answers'].update(q1=None,q2=answer)
        with self.assertRaises(StructuredOutputError):self.decode(wire)

    def test_wire_scope_cannot_be_widened_with_a_forged_schema(self):
        schema = analysis_output_schema(self.task)
        schema['description']='foreign schema'
        with self.assertRaises(StructuredOutputError):self.decode(schema=schema)

    def test_real_adapter_performs_one_submission_and_uses_the_shared_decoder(self):
        response=dict(object='response',status='completed',error=None,incomplete_details=None,
            output=[dict(type='message',role='assistant',status='completed',
                content=[dict(type='output_text',text=json.dumps(self.wire))])],usage={})
        with tempfile.TemporaryDirectory(prefix='prose-calculation-',dir=ROOT/'result/acceptance') as folder:
            settings=ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',
                               api_key='FAKE_TEST_KEY'))
            model=ModelService(settings,offline=False)
            with patch.object(model,'_request_bytes',return_value=json.dumps(response).encode()) as call:
                result=json.loads(model.reason_structured_phase(self.task,[],'analyze',analysis_output_schema(self.task)))
            self.assertEqual(call.call_count,1)
            self.assertEqual(result,self.decode())
            self.assertEqual(self.task['ledger']['used']['model_calls'],1)

    def test_new_formal_policy_is_bound_and_not_added_to_documents_or_cases(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=ResearchAgent(ResearchArchive(folder),ROOT,offline=True,require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task=agent.create('核对当前经营表现')
            self.assertEqual(task['analysis_prose_reference_version'],1)
            old=copy.deepcopy(task);old.pop('analysis_prose_reference_version')
            self.assertNotEqual(input_fingerprint(old),input_fingerprint(task))
            self.assertNotIn('analysis_prose_reference_version',agent.create_case('mechanism'))
            self.assertNotIn('analysis_prose_reference_version',agent.create('核对原文',intent='原件内容核对'))

    def test_referencing_a_calculation_does_not_allow_unbound_numeric_prose(self):
        path=ROOT/'result/acceptance/one-sentence-research-zy9srfaq/task.json'
        original=path.read_bytes();task=json.loads(original)
        task['analysis_prose_reference_version']=1
        calculation=task['calculations'][0];qid=calculation['question_ids'][0]
        schema=analysis_output_schema(task)
        answer=dict(segments=[dict(text='净利润率为50%。',refs=[calculation['id']],counter_refs=[])],
                    knowledge_refs=[],provenance_refs=[])
        wire=dict(version=3,binding=context(task),answers=dict.fromkeys(
            schema['properties']['answers']['properties']),theses=[])
        wire['answers'][qid]=answer
        proposal=decode_analysis_citations(wire,schema,task)
        self.assertFalse(analysis_numeric_review(task,proposal)['accepted'])
        self.assertEqual(path.read_bytes(),original)

    def test_actual_prompt_exposes_only_each_questions_executed_calculations(self):
        from tests.test_analysis_feedback_projection import payload_for, checked_prompt
        path=ROOT/'result/acceptance/one-sentence-research-zy9srfaq/task.json'
        original=path.read_bytes();task=json.loads(original)
        task['analysis_prose_reference_version']=1
        payload=payload_for(task)
        for item in payload['analysis_contract']['by_question'].values():
            expected=[calculation['id'] for calculation in item['calculations']]
            self.assertEqual(item.get('text_calculation_ids',[]),expected)
        captured=checked_prompt(task,payload)
        messages='\n'.join(item['content'] for item in captured['messages'])
        self.assertIn('refs选本题支持判断的证据或已执行计算id',messages)
        self.assertNotIn('只选择实际支持本段判断或构成反证的证据id',messages)
        self.assertEqual(path.read_bytes(),original)
