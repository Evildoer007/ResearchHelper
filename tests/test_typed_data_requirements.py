"""Typed obligations reject authentic but irrelevant ratios, without networking."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import (ContractError,evidence_requirements,missing_evidence_requirements,fingerprint,
    validate_question_coverage)
from researchhelper.data_requirements import (POLICY,FIELD,normalized,validate_links,validate_plan,
    api_cell_need_groups,count_satisfied,unit_matches,pending)
from researchhelper.engine import ResearchAgent
from researchhelper.output_schema import (planning_output_schema,decode_planning_output,original_span_output_schema,
    _original_span_choices,decode_original_spans)
from researchhelper.reasoning import validate_plan as validate_business_plan
from researchhelper.planning import input_fingerprint,question_specs


def constraint(kind='number',dimension='amount',count=1,period='interval'):
    return dict(value_kind=kind,unit_dimension=dimension,minimum_count=count,period_kind=period)


class TypedDataRequirementsTests(unittest.TestCase):
    def task(self):
        question=dict(id='q1',evidence_needs=['公司营业收入'],**{FIELD:{'公司营业收入':constraint()}})
        return dict(id='test',version=1,questions=[question],evidence=[],**{POLICY:1})

    def item(self,task,**changes):
        item=dict(id='e1',question_ids=['q1'],text='独立类型测试，不作为真实来源',value=100,unit='元',
            period='2026-01-01/2026-06-30',object='600030.SH',record_locator=dict(part='合成测试行'),
            applicability_review=dict(evidence_need_ids={'q1':[fingerprint('公司营业收入')]}))
        item.update(changes);return item

    def test_authentic_percentages_and_text_cannot_discharge_amount_need(self):
        task=self.task();mapping={'q1':[fingerprint('公司营业收入')]}
        for changes in (dict(unit='%'),dict(value=None),dict(value=True),dict(value=float('nan')),
                        dict(unit='元',period='2026-01-01'),dict(period=None)):
            with self.subTest(changes=changes),self.assertRaises(ContractError):
                validate_links(task,self.item(task,**changes),mapping)
        validate_links(task,self.item(task),mapping)

    def test_dimension_and_period_are_not_substituted(self):
        task=self.task();mapping={'q1':[fingerprint('公司营业收入')]}
        for unit in ('元','千元','万元','百万元','千万元','亿元'):
            validate_links(task,self.item(task,unit=unit),mapping)
        for unit in ('%','百分点','GWh','点','元/股'):
            self.assertFalse(unit_matches(unit,'amount'))
        self.assertTrue(unit_matches('GWh','physical'))
        self.assertFalse(unit_matches('个百分点','physical'))

    def test_scaled_count_units_keep_literal_values_and_cannot_fill_other_dimensions(self):
        name='报告期末客户数量'
        q=dict(id='q1',evidence_needs=[name],**{FIELD:{name:constraint(dimension='count',period='point')}})
        task=dict(self.task(),questions=[q],api_need_compatibility_version=2)
        mapping={'q1':[fingerprint(name)]}
        base=dict(question_ids=['q1'],value=123.4,period='2026-06-30',
                  applicability_review=dict(evidence_need_ids=mapping))
        units=('户','万户','万家','万人','万人次','亿人','百万份','千万股','万笔')
        for unit in units:
            item=dict(base,text='合成原值123.4'+unit,unit=unit)
            with self.subTest(unit=unit):
                validate_links(task,item,mapping)
                self.assertTrue(count_satisfied(q,name,[item]))
                self.assertEqual((item['value'],item['unit']),(123.4,unit))
        non_counts=('万元','万亿元','%','百分点','万户/年','万人次／日','每万户','万户左右')
        for unit in non_counts:
            item=dict(base,unit=unit)
            with self.subTest(unit=unit),self.assertRaises(ContractError):
                validate_links(task,item,mapping)
        cells=[dict(base,unit='万户',candidate_id='counts'),dict(base,unit='万元',candidate_id='amounts')]
        self.assertEqual(api_cell_need_groups(task,cells),[
            dict(need_choices={'q1':[name]},candidate_ids=['counts']),
            dict(need_choices={},candidate_ids=['amounts']),
        ])

    def test_five_requested_observations_cannot_be_filled_by_duplicate_excerpts(self):
        task=self.task();q=task['questions'][0];q[FIELD]['公司营业收入']['minimum_count']=5
        repeated=[self.item(task,id='e'+str(i)) for i in range(5)]
        self.assertFalse(count_satisfied(q,'公司营业收入',repeated))
        distinct=[self.item(task,id='e'+str(i),text='合成不同观测'+str(i),record_locator={'row':i}) for i in range(5)]
        self.assertTrue(count_satisfied(q,'公司营业收入',distinct))
        self.assertTrue(missing_evidence_requirements(q,repeated))
        self.assertFalse(missing_evidence_requirements(q,distinct))

    def test_constraints_are_complete_versioned_and_not_downgraded(self):
        task=self.task();prior=copy.deepcopy(task)
        for changes in ({}, {'公司营业收入':constraint(count=True)}, {'公司营业收入':constraint(dimension='not_applicable')}):
            q=copy.deepcopy(task['questions'][0]);q[FIELD]=changes
            with self.assertRaises(ContractError):normalized(q)
        new=copy.deepcopy(task['questions']);new[0][FIELD]['公司营业收入']=constraint('text','not_applicable',1,'any')
        with self.assertRaises(ContractError):validate_plan(task,new)
        self.assertEqual(task,prior)
        task[POLICY]=True
        with self.assertRaises(ContractError):validate_plan(task,task['questions'])

    def test_missing_numeric_obligation_remains_pending_even_with_a_linked_ratio(self):
        task=self.task();task['evidence']=[self.item(task,unit='%')]
        self.assertEqual(len(pending(task)),1)
        task['evidence']=[self.item(task)]
        self.assertEqual(pending(task),[])
        packet=evidence_requirements(task['questions'][0]);packet[0]['constraints']['minimum_count']=8
        self.assertEqual(task['questions'][0][FIELD]['公司营业收入']['minimum_count'],1)

    def test_actual_planning_container_round_trips_the_same_host_business_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=ResearchAgent(ResearchArchive(Path(folder)),ROOT,offline=True,require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task=agent.create('核对公司营业收入',research_mode='current')
            schema=planning_output_schema(task)
            question=dict(id='q1',question=task['question'],required=True,group='main',kind='fact',
                dependencies=[],evidence_needs=[dict(text='公司营业收入',**constraint())],method_needs=[])
            wire=dict(version=3,main_question=question,additional_questions=[],data_requests=[],calculations=[])
            proposal=decode_planning_output(wire,schema)
            adopted=validate_business_plan(task,proposal)
            self.assertEqual(adopted[0][FIELD],{'公司营业收入':constraint()})
            self.assertEqual(adopted[0]['evidence_needs'],['公司营业收入'])
            del proposal['questions'][0][FIELD]
            with self.assertRaises(ContractError):validate_business_plan(task,proposal)

    def test_host_skill_plan_uses_same_constraints_without_model_or_data_calls(self):
        from core.session import Session
        with tempfile.TemporaryDirectory() as folder:
            session=Session(folder);self.addCleanup(session.agent.close_runtime)
            task=session.create('核对公司营业收入')
            self.assertIn('typed_data_requirements',session.tools(task['id'])['questions'])
            question=question_specs(task)[0];question.update(kind='fact',
                evidence_needs=['公司营业收入'],**{FIELD:{'公司营业收入':constraint()}})
            planned=session.plan(task['id'],[question],'独立测试明确数值需求',task['input_hash'])
            self.assertEqual(planned['questions'][0][FIELD],question[FIELD])
            self.assertEqual(planned['ledger']['used']['data_requests'],0)
            self.assertEqual(planned['ledger']['used']['model_calls'],0)
            question[FIELD]['公司营业收入']=constraint('text','not_applicable',1,'any')
            with self.assertRaises(ContractError):session.plan(task['id'],[question],'不能降级既有金额要求')

    def test_plan_and_policy_constraints_change_the_input_fingerprint(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=ResearchAgent(ResearchArchive(Path(folder)),ROOT,offline=True,require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task=agent.create('核对公司营业收入');before=input_fingerprint(task)
            task[POLICY]=2
            self.assertNotEqual(before,input_fingerprint(task))


class RetainedWrongRatioSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=ROOT/'result/acceptance/autonomous-data-phase-qyxtw_d0/task.json.gz'
        cls.original_bytes_hash=hashlib.sha256(path.read_bytes()).hexdigest()
        with gzip.open(path,'rt',encoding='utf-8') as stream:cls.old=json.load(stream)
        cls.path=path

    def typed(self):
        # Explicit-code offline protocol copy, not fabricated name authority.
        # No source gate is upgraded and the original task stays closed.
        task=copy.deepcopy(self.old);task[POLICY]=1;q=task['questions'][0]
        q[FIELD]={text:constraint('text','not_applicable',1,'any') if i<2 else constraint(count=5 if i==4 else 1)
            for i,text in enumerate(q['evidence_needs'])}
        task['objects']=sorted({s['directory']['issuer'] for s in task['source_records']
            if s['operation']=='filing_original'})
        task['input_hash']=input_fingerprint(task)
        return task

    def test_the_seven_real_wrong_ratios_are_rejected_under_the_new_contract(self):
        task=self.typed();numbers=[e for e in task['evidence'] if e.get('value') is not None]
        self.assertEqual(len(numbers),7)
        for item in numbers:
            with self.assertRaises(ContractError):validate_links(task,item,item['applicability_review']['evidence_need_ids'])
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),self.original_bytes_hash)

    def test_actual_original_selector_does_not_offer_percent_shortcuts_for_amounts(self):
        task=self.typed();views=task['reasoning_state']['reading']['bundle']['views']
        schema=original_span_output_schema(task,views)
        numeric=[v['properties'] for v in schema['properties']['selections']['items']['anyOf']
            if v['properties']['kind']['enum']==['number']]
        self.assertTrue(numeric)
        for choice in numeric:
            references=choice['number_reference'].get('anyOf',[choice['number_reference']])
            self.assertFalse(any('quantity_ref' in r['properties'] for r in references))
            for reference in references:
                if 'unit' in reference['properties']:
                    self.assertNotIn('%',reference['properties']['unit']['enum'])

    def test_amount_with_literal_header_and_full_period_still_decodes_without_auto_approval(self):
        task=self.typed();views=task['reasoning_state']['reading']['bundle']['views']
        choice=_original_span_choices(task,views)[0];schema=original_span_output_schema(task,views)
        props=next(v['properties'] for v in schema['properties']['selections']['items']['anyOf']
            if v['properties']['kind']['enum']==['number'])
        token=next(t for t in choice['number_tokens'] if t['token']=='49,691,932,907.10')
        unit=next(u for u in choice['unit_references'] if u['unit']=='元')
        period=next(p for p in choice['period_anchors'] if p['period']=='2026-01-01/2026-06-30')
        links=props['review']['properties']['need_links']['items']['anyOf'][0]['properties']
        wire=dict(version=12,binding_ref=schema['properties']['binding_ref']['enum'][0],selections=[dict(
            id='typed_amount',source_ref=choice['source_ref'],kind='number',object=props['object']['enum'][0],
            nature='已实现事实',temporal_reference=dict(period_ref=period['ref']),
            number_reference=dict(number_ref=token['ref'],unit_ref=unit['ref']),
            review=dict(scope='current_original_descriptive_only',need_links=[dict(question_id='q1',need_ids=[
                links['need_ids']['items']['enum'][0]])]))])
        spec=decode_original_spans(wire,schema,(task,views))['evidence_specs'][0]
        self.assertEqual(spec['value'],49691932907.10)
        self.assertEqual(spec['unit'],'元')
        self.assertEqual(spec['period'],'2026-01-01/2026-06-30')
        self.assertFalse('source_check' in spec or 'applicability_check' in spec)
        validate_links(task,spec,validate_question_coverage(task,spec['question_ids'],spec['review']))


if __name__=='__main__':unittest.main()
