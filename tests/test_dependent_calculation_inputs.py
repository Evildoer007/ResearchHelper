"""Explicit dependency numbers feed registered methods, not unrelated judgments.

All replay is on a memory copy. The retained failed study remains unchanged.
"""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from researchhelper.contracts import (ContractError, calculation_dependency_questions,
    calculation_input_applies, evidence_applies_to_answer, missing_evidence_requirements)
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.output_schema import deferred_calculation_output_schema
from researchhelper.reasoning import analysis_contract
from researchhelper.numeric_integrity import bind_numeric_statement

ROOT=Path(__file__).resolve().parents[1]
RETAINED=ROOT/'result/acceptance/one-sentence-research-xny50jzr/task.json'


class DependentCalculationInputsTests(unittest.TestCase):
    def setUp(self):
        self.digest=hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        self.task=json.loads(RETAINED.read_text())
        self.agent=ResearchAgent.__new__(ResearchAgent);self.agent.knowledge=ResearchKnowledge()
        self.methods=('net_profit_margin_v1','operating_cash_profit_ratio_v1')
        self.original_scopes={item['id']:copy.deepcopy(item['question_ids']) for item in self.task['evidence']}
        self.by_metric={item['calculation_context']['metric']:item for item in self.task['evidence']
                        if item.get('calculation_context')}
        self.target=next(q for q in self.task['questions'] if q['id']=='q1')
        self.task['gaps']=[];self.task['gap_records']=[]
        guard=patch('socket.socket.connect',side_effect=AssertionError('No network'))
        guard.start();self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(RETAINED.read_bytes()).hexdigest(),self.digest)

    def spec(self,method):
        inputs={'net_profit':self.by_metric['net_profit']['id']}
        other='revenue' if method=='net_profit_margin_v1' else 'operating_cash_flow'
        inputs[other]=self.by_metric[other]['id']
        return dict(id='derived_'+other,method=method,inputs=inputs,question_ids=['q1'],assumptions={},
            parameters=dict(accounting_scope='consolidated',accounting_standard='CAS',amount_unit='元',
                            currency='CNY',period='2026年半年度'))

    def calculate(self):
        for method in self.methods:self.agent._calculate_spec(self.task,self.spec(method))

    def test_actual_dependency_exposes_deferred_method_without_retagging(self):
        self.assertEqual(calculation_dependency_questions(self.task,'q1'),{'q2'})
        self.assertIsNotNone(deferred_calculation_output_schema(self.task))
        self.assertEqual(self.original_scopes,{item['id']:item['question_ids'] for item in self.task['evidence']})

    def test_both_registered_methods_execute_on_verified_dependency_numbers(self):
        self.calculate()
        self.assertEqual(len(self.task['calculations']),2)
        self.assertFalse(self.task['gaps'])
        self.assertTrue(all(c['question_ids']==['q1'] for c in self.task['calculations']))
        self.assertEqual(self.original_scopes,{item['id']:item['question_ids'] for item in self.task['evidence']})

    def test_indirect_fact_only_available_with_its_actual_calculation_reference(self):
        item=self.by_metric['net_profit']
        self.assertFalse(evidence_applies_to_answer(self.task,item,'q1',[]))
        self.calculate()
        self.assertFalse(evidence_applies_to_answer(self.task,item,'q1',[]))
        self.assertTrue(evidence_applies_to_answer(self.task,item,'q1',['derived_revenue']))

    def test_same_object_without_dependency_cannot_reuse_input(self):
        self.target['dependencies']=[]
        self.assertIsNone(deferred_calculation_output_schema(self.task))
        self.calculate();self.assertFalse(self.task['calculations']);self.assertTrue(self.task['gaps'])

    def test_method_must_be_explicitly_planned_for_indirect_input(self):
        self.target['method_needs']=[]
        self.assertFalse(calculation_input_applies(self.task,self.by_metric['net_profit'],'q1',self.methods[0]))

    def test_cross_task_version_unapproved_and_document_only_inputs_rejected(self):
        item=self.by_metric['net_profit']
        for field,value in (('task_id','foreign'),('research_version',99),('applicability_check','待核验'),
                            ('usage_scope','document_content')):
            changed=copy.deepcopy(item);changed[field]=value
            self.assertFalse(calculation_input_applies(self.task,changed,'q1',self.methods[0]))

    def test_transitive_dependency_is_explicit_and_cycles_are_rejected(self):
        self.task['questions'].append(dict(id='q3',dependencies=['q2'],kind='fact',method_needs=[]))
        self.target['dependencies']=['q3']
        self.assertEqual(calculation_dependency_questions(self.task,'q1'),{'q2','q3'})
        self.assertTrue(calculation_input_applies(self.task,self.by_metric['net_profit'],'q1',self.methods[0]))
        self.task['questions'][-1]['dependencies']=['q1']
        with self.assertRaises(ContractError):calculation_dependency_questions(self.task,'q1')

    def test_entity_period_and_unit_checks_still_control_execution(self):
        for field,value in (('period','2025年半年度'),('currency','USD')):
            original=copy.deepcopy(self.by_metric['revenue']['calculation_context'])
            self.by_metric['revenue']['calculation_context'][field]=value
            self.agent._calculate_spec(self.task,self.spec(self.methods[0]))
            self.assertFalse(self.task['calculations'])
            self.by_metric['revenue']['calculation_context']=original

    def test_analysis_contract_exposes_only_actual_calculation_inputs(self):
        self.calculate()
        contract=analysis_contract(self.task,['q1'])['by_question']['q1']
        expected={item['id'] for item in self.by_metric.values()}
        self.assertEqual(set(contract['evidence_ids']),expected)
        self.assertEqual(len(contract['calculations']),2)
        projected=ResearchAgent._reasoning_evidence_payload(self.task,['q1'])['evidence']
        self.assertEqual({item['id'] for item in projected},expected)
        self.assertTrue(all(item['question_ids']==['q2'] for item in projected))

    def test_dependency_does_not_cover_new_financial_requirement(self):
        self.target['evidence_needs']=['需要另行核验的盈利质量机制']
        self.calculate()
        self.assertTrue(missing_evidence_requirements(self.target,list(self.by_metric.values())))

    def test_unsupported_quantity_reference_without_cited_calculation_rejected(self):
        self.calculate();item=self.by_metric['net_profit']
        proposal=dict(question_ids=['q1'],numeric_segments={'answer':[
            dict(source='evidence',ref=item['id'],path='value',format='signed',digits=2)]})
        with self.assertRaises(ContractError):bind_numeric_statement(self.task,proposal,['answer'],[item['id']],[])

    def test_removing_dependency_invalidates_indirect_existing_calculation_use(self):
        self.calculate();item=self.by_metric['net_profit']
        self.target['dependencies']=[]
        self.assertFalse(evidence_applies_to_answer(self.task,item,'q1',['derived_revenue']))


if __name__=='__main__':unittest.main()
