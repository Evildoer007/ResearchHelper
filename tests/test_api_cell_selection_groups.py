"""Independent wire-format regressions; no model, provider or Store writes."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
_fixture_spec=importlib.util.spec_from_file_location('cell_group_lifecycle_fixture',
    Path(__file__).with_name('test_source_requirement_lifecycle.py'))
lifecycle=importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(lifecycle)
from researchhelper.contracts import ContractError
from researchhelper.evidence import api_cell_choices, candidate_evidence, expand_api_cell_selections
from researchhelper.reasoning import analysis_contract, evidence_contract, model_object


class ApiCellSelectionGroupTests(unittest.TestCase):
    def setUp(self):
        self.fixture=lifecycle.SourceRequirementLifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.task=self.fixture.task
        self.source=self.task['source_records'][0]
        self.choices=api_cell_choices(self.task,self.source)
        _,selection=self.fixture.selection()
        self.review=selection['review']

    def group(self, count=2):
        return {'api_cell_selections':[c['candidate_id'] for c in self.choices[:count]],
                'question_ids':['q1'],'review':copy.deepcopy(self.review)}

    def test_group_preserves_actual_cells_and_normal_source_checks(self):
        before=copy.deepcopy(self.task)
        specs=expand_api_cell_selections(self.task,[self.group()])
        for choice,spec in zip(self.choices,specs):
            item=candidate_evidence(self.task,self.source,spec)
            self.assertEqual(item['value'],choice['value'])
            self.assertEqual(item['period'],choice['period'])
            self.assertEqual(item['record_locator'],choice['record_locator'])
            self.assertEqual(item['source_check'],'通过')
            self.assertEqual(item['applicability_check'],'待核验')
            self.assertEqual(item['calculation_context']['corporate_action_status'],'unknown')
            self.assertNotIn('source_check',spec)
        self.assertEqual(self.task,before)

    def test_review_copies_are_independent_and_never_auto_approved(self):
        wire=self.group()
        specs=expand_api_cell_selections(self.task,[wire])
        specs[0]['review']['conditions'].append('只改第一项')
        self.assertNotIn('只改第一项',specs[1]['review']['conditions'])
        self.assertNotIn('只改第一项',wire['review']['conditions'])

    def test_duplicate_within_group_is_rejected(self):
        group=self.group();group['api_cell_selections'][1]=group['api_cell_selections'][0]
        with self.assertRaises(ContractError):
            expand_api_cell_selections(self.task,[group])

    def test_duplicate_between_single_and_group_is_rejected(self):
        _,single=self.fixture.selection()
        with self.assertRaises(ContractError):
            expand_api_cell_selections(self.task,[single,self.group()])

    def test_bad_list_and_mixed_shapes_are_rejected_without_repair(self):
        for value in (None,[],True,'not-a-list',[True],['foreign'],['x'+str(i) for i in range(65)]):
            with self.subTest(value=value),self.assertRaises(ContractError):
                expand_api_cell_selections(self.task,[dict(self.group(),api_cell_selections=value)])
        for field,value in (('api_cell_selection',None),('value',1),('source_check','通过'),('artifact_id',self.source['id'])):
            with self.subTest(field=field),self.assertRaises(ContractError):
                expand_api_cell_selections(self.task,[dict(self.group(),**{field:value})])

    def test_changed_source_and_version_reject_previously_offered_ids(self):
        for mutate in ('value','task','version'):
            task=copy.deepcopy(self.task)
            if mutate=='value':task['source_records'][0]['snapshot']['tables'][0]['table']['close'][0]+=1
            elif mutate=='task':task['id']='other_task'
            else:task['version']+=1
            with self.subTest(mutate=mutate),self.assertRaises(ContractError):
                expand_api_cell_selections(task,[self.group()])

    def test_normal_specs_unchanged_and_not_subject_to_short_wire_shape(self):
        normal={'id':'ordinary_record','artifact_id':'declared_source'}
        self.assertEqual(expand_api_cell_selections(self.task,[normal]),[normal])

    def test_model_group_receipt_keeps_wire_hash_and_individual_specs(self):
        wire={'evidence_specs':[self.group()]}
        class Selector:
            def reason(self,task,messages):
                return json.dumps(wire)
        view={'id':self.source['id'],'kind':'api','snapshot_hash':self.source['hash']}
        result,receipt=model_object(Selector(),self.task,'extract',
            {'evidence_contract':evidence_contract(self.task),'untrusted_sources':[view]}, {'evidence_specs'})
        self.assertEqual(receipt['cell_selection_version'],1)
        self.assertEqual(receipt['cell_group_version'],1)
        self.assertEqual(receipt['expanded_cell_count'],2)
        self.assertEqual([item['id'] for item in result['evidence_specs']],[c['candidate_id'] for c in self.choices[:2]])
        self.assertNotIn('answers',result)

    def test_host_and_model_validation_use_the_same_group_decoder(self):
        from researchhelper.reasoning import validate_model_specs
        expanded=expand_api_cell_selections(self.task,[self.group()])
        ordinary=validate_model_specs(self.task,expanded)
        grouped=validate_model_specs(self.task,[self.group()])
        self.assertEqual(ordinary,grouped)

    def test_real_long_series_wire_is_smaller_without_losing_observations(self):
        path=Path(__file__).resolve().parents[1]/'result/acceptance/default-app-investment-53umuq2j/task.json'
        before=path.read_bytes(); task=json.loads(before)
        source=next(s for s in task['source_records'] if s['operation']=='cmd_history_quotation')
        choices=api_cell_choices(task,source)
        self.assertEqual(len(choices),len(source['snapshot']['tables'][0]['table']['close']))
        question=next(q for q in task['questions'] if q['id']=='q2')
        review={'question_ids':['q2'],'allowed_natures':['已实现事实'],'conditions':['只核对本窗口未复权收盘价序列'],
                'reason':'原始接口单元及观测日期均保留','evidence_need_texts':{'q2':[question['evidence_needs'][-1]]}}
        singles=[{'api_cell_selection':c['candidate_id'],'question_ids':['q2'],'review':review} for c in choices]
        groups=[{'api_cell_selections':[c['candidate_id'] for c in choices],'question_ids':['q2'],'review':review}]
        with patch('researchhelper.evidence.api_cell_choices',wraps=api_cell_choices) as resolver:
            values=expand_api_cell_selections(task,groups)
            self.assertEqual(resolver.call_count,len(task['source_records']))
        self.assertEqual(len(values),61)
        for c,spec in zip(choices,values):
            fact=candidate_evidence(task,source,spec)
            self.assertEqual(fact['value'],c['value'])
            self.assertEqual(fact['period'],c['period'])
        self.assertLess(len(json.dumps(groups,ensure_ascii=False)),len(json.dumps(singles,ensure_ascii=False))*.35)
        self.assertEqual(path.read_bytes(),before)

    def test_report_writing_guidance_preserves_evidence_and_calculation_contracts(self):
        before=copy.deepcopy(self.task)
        contract=analysis_contract(self.task)
        self.assertEqual(contract['client_writing']['version'],1)
        self.assertEqual(contract['answer_fields'],['answer','refs','calculation_refs'])
        self.assertFalse(contract['contract_is_approval'])
        self.assertFalse(contract['automatic_reference_repair'])
        self.assertEqual(self.task,before)


if __name__=='__main__':
    unittest.main()
