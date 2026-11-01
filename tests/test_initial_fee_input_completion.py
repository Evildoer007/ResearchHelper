"""Bounded initial numeric preparation; mocked calls never approve research."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests import test_fee_context_mapping as fixtures
from researchhelper.contracts import ContractError, charge, context, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelCompletionError
from researchhelper.output_schema import original_span_output_schema, decode_original_spans
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import evidence_contract

POLICY='initial_fee_input_completion_version'


class InitialFeeInputCompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.FeeContextMappingTests.setUpClass()
        cls.base=fixtures.FeeContextMappingTests()

    @classmethod
    def tearDownClass(cls):fixtures.FeeContextMappingTests.tearDownClass()

    def setUp(self):
        self.task=copy.deepcopy(self.base.task)
        self.task.update(status='运行中',run_mode='model',delivery=None,pending_content=None,evidence=[],**{POLICY:1})
        self.task['ledger']['used'].update(model_calls=4,data_requests=7,seconds=100)
        self.task['input_hash']=input_fingerprint(self.task)
        schema=original_span_output_schema(self.task,self.base.views)
        wire=self.base.wire();item=wire['selections'][0]
        item['text_span']=copy.deepcopy(item['contexts']['metric']['span'])
        item.update(kind='text',id='initial_text')
        item.pop('contexts');item.pop('number_reference')
        wire['binding_ref']=schema['properties']['binding_ref']['enum'][0]
        first=decode_original_spans(wire,schema,(self.task,self.base.views))['evidence_specs']
        self.task['host_result']={'evidence_specs':first}
        self.task['reasoning_state'].pop('fee_input_completion',None)
        self.task['reasoning_state'].pop('extraction_submission_recovery',None)
        self.task['reasoning_state']['extraction']={'input_digest':'isolated_input',
                                                  'evidence_specs_hash':fingerprint(first)}
        self.initial=copy.deepcopy(first)
        self.payload=dict(question=self.task['question'],questions=copy.deepcopy(self.task['questions']),
                          untrusted_sources=copy.deepcopy(self.base.views),
                          evidence_contract=evidence_contract(self.task,self.base.views))
        self.agent=ResearchAgent.__new__(ResearchAgent)
        self.agent.model=SimpleNamespace(supports_structured_analysis=lambda:True,
                                         reason_structured_original_spans=lambda:None)
        self.agent._active=Mock(return_value=self.task)
        self.agent._active_supplement=Mock(return_value=None)
        self.agent._save_model_checkpoint=Mock()
        self.agent._gap=Mock()
        self.limits=copy.deepcopy(self.task['ledger']['limits'])
        self.reserve=copy.deepcopy(self.task['ledger']['reserve'])

    def tearDown(self):
        self.assertEqual(self.task['ledger']['limits'],self.limits)
        self.assertEqual(self.task['ledger']['reserve'],self.reserve)
        self.assertEqual(self.task['ledger']['used']['data_requests'],7)
        self.assertEqual(self.task['evidence'],[])

    def submit(self,model,task,phase,payload,fields):
        self.assertEqual(phase,'extract');self.assertEqual(fields,{'evidence_specs'})
        self.assertEqual(payload['input_completion']['unbound_input_requirements'],
            self.payload['evidence_contract']['calculation_input_requirements']['unbound_requirements'])
        self.agent._save_model_checkpoint.assert_called()
        charge(task['ledger'],'model_calls')
        schema=original_span_output_schema(task,self.base.views)
        self.assertTrue(all(row['properties']['kind']['enum']==['number']
            for row in schema['properties']['selections']['items']['anyOf']))
        prefix=task['reasoning_state']['fee_input_completion']['id_prefix']
        specs=[]
        for index in (0,1):
            wire=self.base.wire(index)
            wire['selections'][0]['id']=prefix+'fee_'+str(index)
            wire['binding_ref']=schema['properties']['binding_ref']['enum'][0]
            specs.extend(decode_original_spans(wire,schema,(task,self.base.views))['evidence_specs'])
        result={'evidence_specs':specs}
        return result,dict(phase='extract',output_hash=fingerprint(result))

    def complete(self):
        return self.agent._complete_initial_fee_inputs(self.task,self.payload,'isolated_input',None)

    def test_one_numeric_pass_preserves_initial_text_and_does_not_adopt_evidence(self):
        with patch('researchhelper.engine.model_object',side_effect=self.submit) as model,\
             patch('researchhelper.engine.record_extraction_validation'):
            result=self.complete();self.complete()
        self.assertEqual(model.call_count,1)
        self.assertEqual(self.task['ledger']['used']['model_calls'],5)
        self.assertEqual(result['evidence_specs'][:len(self.initial)],self.initial)
        added=result['evidence_specs'][len(self.initial):]
        self.assertEqual(len(added),2)
        self.assertTrue(all(set(spec['calculation_context'])=={'fund_id','share_class','period','metric','fee_basis','rate_period'} for spec in added))
        self.assertTrue(all('source_check' not in spec for spec in added))
        record=self.task['reasoning_state']['fee_input_completion']
        self.assertEqual(record['status'],'已提交')
        self.assertFalse(record['automatic_repair']);self.assertFalse(record['financial_approval'])

    def test_three_remaining_calls_do_not_start_unfinishable_preparation(self):
        self.task['ledger']['used']['model_calls']=5
        with patch('researchhelper.engine.model_object') as model:self.complete()
        model.assert_not_called()

    def test_delivery_time_is_not_spent_on_input_preparation(self):
        self.task['ledger']['used']['seconds']=550
        with patch('researchhelper.engine.model_object') as model:self.complete()
        model.assert_not_called()

    def test_failed_pass_is_not_retried_and_original_text_is_kept(self):
        def failed(*args):
            charge(self.task['ledger'],'model_calls');raise ModelCompletionError('Isolated failed completion')
        with patch('researchhelper.engine.model_object',side_effect=failed) as model:
            self.complete();self.complete()
        self.assertEqual(model.call_count,1)
        self.assertEqual(self.task['host_result']['evidence_specs'],self.initial)
        self.assertEqual(self.task['reasoning_state']['fee_input_completion']['status'],'未采用')

    def test_unknown_interruption_survives_resume_without_redispatch(self):
        def interrupted(*args):
            charge(self.task['ledger'],'model_calls');raise RuntimeError('Isolated unknown interruption')
        with patch('researchhelper.engine.model_object',side_effect=interrupted) as model:
            with self.assertRaises(RuntimeError):self.complete()
            self.task['batch']+=1;self.complete()
        self.assertEqual(model.call_count,1)
        self.assertEqual(self.task['reasoning_state']['fee_input_completion']['status'],'已派发')

    def test_pending_schema_rejects_changed_source_or_task_binding(self):
        def changed(model,task,phase,payload,fields):
            charge(task['ledger'],'model_calls')
            task['reasoning_state']['fee_input_completion']['views_hash']='0'*64
            original_span_output_schema(task,self.base.views)
        with patch('researchhelper.engine.model_object',side_effect=changed) as model:self.complete()
        self.assertEqual(model.call_count,1)
        self.assertEqual(self.task['host_result']['evidence_specs'],self.initial)
        self.assertEqual(self.task['reasoning_state']['fee_input_completion']['status'],'未采用')

    def test_empty_numeric_selection_is_not_completion_or_approval(self):
        def empty(*args):
            charge(self.task['ledger'],'model_calls')
            return {'evidence_specs':[]},{'phase':'extract'}
        with patch('researchhelper.engine.model_object',side_effect=empty),\
             patch('researchhelper.engine.record_extraction_validation'):
            self.complete()
        self.assertEqual(self.task['host_result']['evidence_specs'],self.initial)
        self.assertEqual(self.task['reasoning_state']['fee_input_completion']['status'],'未形成输入')

    def test_non_numeric_proposal_is_rejected_recorded_and_never_overwrites_text(self):
        def text_again(*args):
            charge(self.task['ledger'],'model_calls')
            spec=copy.deepcopy(self.initial[0])
            spec['id']=self.task['reasoning_state']['fee_input_completion']['id_prefix']+'text'
            return {'evidence_specs':[spec]},{'phase':'extract'}
        with patch('researchhelper.engine.model_object',side_effect=text_again),\
             patch('researchhelper.engine.record_extraction_validation') as audit:
            self.complete()
        record=self.task['reasoning_state']['fee_input_completion']
        self.assertEqual(record['status'],'未采用')
        self.assertFalse(record['evidence_specs_accepted'])
        self.assertIn('rejected_proposal',record)
        self.assertEqual(self.task['host_result']['evidence_specs'],self.initial)
        self.assertFalse(audit.call_args.args[4])

    def test_active_supplement_and_unsupported_gateway_do_not_start_an_extra_pass(self):
        self.agent._active_supplement.return_value={'round':1}
        with patch('researchhelper.engine.model_object') as model:self.complete()
        model.assert_not_called()
        self.agent._active_supplement.return_value=None
        self.agent.model.supports_structured_analysis=lambda:False
        with patch('researchhelper.engine.model_object') as model:self.complete()
        model.assert_not_called()

    def test_legacy_host_existing_numeric_or_explicit_plan_do_not_get_extra_calls(self):
        original=copy.deepcopy(self.task)
        variations=[{POLICY:None},dict(run_mode='host'),dict(status='已完成'),dict(delivery={'saved':True}),
                    dict(calculation_specs=[{'method':'fee_difference_v2'}])]
        for values in variations:
            self.task=copy.deepcopy(original);self.task.update(values)
            with self.subTest(values=values),patch('researchhelper.engine.model_object') as model:
                self.complete();model.assert_not_called()
        self.task=original
        self.task['host_result']['evidence_specs'][0]['raw_value']=.15
        self.task['reasoning_state']['extraction']['evidence_specs_hash']=fingerprint(self.task['host_result']['evidence_specs'])
        with patch('researchhelper.engine.model_object') as model:self.complete()
        model.assert_not_called()
