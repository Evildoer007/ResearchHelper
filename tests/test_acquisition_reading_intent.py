"""Declared download reading intent saves a call without relaxing evidence.

Retained real bytes are used only in an explicit-code offline protocol fixture.
No old task is resumed, original receipt rewritten, or financial verdict made.
"""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from tests import ROOT
from tests.test_asset_data_acceptance_review import load_retained_task
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError,charge,fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.planning import input_fingerprint,question_specs
from researchhelper.reasoning import extractable_source_views,validate_plan
from researchhelper.reports import compose
from researchhelper.source_reader import (acquisition_reading_requests,validate_acquisition_reading_queries,
    context_sources,binding,read,validate_reading)
from researchhelper.output_schema import acquisition_output_schema
from researchhelper.evidence import data_request_contract


POLICY='acquisition_reading_intent_version'
WORDS=['营业收入','归属于母公司股东的净利润','分部报告','金额单位']


class AcquisitionReadingIntentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=ROOT/'result/acceptance/one-sentence-research-s_wg0m9f'
        cls.original=load_retained_task(cls.folder);cls.protected=fingerprint(cls.original)

    def setUp(self):
        self.task=copy.deepcopy(self.original)
        self.task['objects']=sorted({s['directory']['issuer'] for s in self.task['source_records']
                                    if s['operation']=='filing_original'})
        self.task[POLICY]=1
        self.task['input_hash']=input_fingerprint(self.task)
        state=self.task['reasoning_state']['acquisition']
        state['binding']['input_hash']=self.task['input_hash']
        for decision in state['decisions']:
            decision['binding']=copy.deepcopy(state['binding'])
            for need in decision.get('data_requests',[]):
                if need['operation']=='filing_original':need['reading_queries']=list(WORDS)
            decision['reading_intent_hash']=fingerprint(dict(binding=decision['binding'],
                data_requests=decision.get('data_requests',[])))
        self.ids=[v['id'] for v in extractable_source_views(self.task)]
        self.context=context_sources(self.task,self.ids)
        self.questions=[q['id'] for q in self.task['questions']]
        self.need=next(n for d in state['decisions'] for n in d.get('data_requests',[]) if n['operation']=='filing_original')

    def tearDown(self):
        self.assertEqual(fingerprint(load_retained_task(self.folder)),self.protected)

    def requests(self):
        return acquisition_reading_requests(self.task,self.context['catalog'],self.questions)

    def test_each_actual_original_uses_its_own_explicit_queries_and_source_binding(self):
        requests=self.requests();self.assertEqual(len(requests),2)
        catalogue={item['id']:item for item in self.context['catalog']['items']}
        for request in requests:
            self.assertEqual(request['queries'],WORDS)
            self.assertEqual({k:request[k] for k in ('artifact_id','snapshot_hash')},catalogue[request['artifact_id']]['request_binding'])
            self.assertEqual(request['question_ids'],self.questions)
            self.assertEqual(request['parts'],[])

    def test_same_bounded_reader_verifies_actual_text_and_not_financial_approval(self):
        bundle=read(self.task,self.requests(),binding(self.task),self.ids)
        validate_reading(self.task,bundle)
        self.assertEqual(len(bundle['views']),2)
        self.assertTrue(any('49,691,932,907.10' in part['text'] for view in bundle['views'] for part in view['parts']))
        self.assertEqual(bundle['selection'],self.requests())

    def test_missing_empty_or_changed_intent_keeps_normal_selection(self):
        for mode in ('missing','empty','changed'):
            task=copy.deepcopy(self.task);state=task['reasoning_state']['acquisition']
            need=next(n for d in state['decisions'] for n in d.get('data_requests',[]) if n['operation']=='filing_original')
            if mode=='missing':need.pop('reading_queries')
            elif mode=='empty':need['reading_queries']=[]
            else:need['reading_queries']=['different']
            if mode!='changed':
                for d in state['decisions']:d['reading_intent_hash']=fingerprint(dict(binding=d['binding'],data_requests=d.get('data_requests',[])))
            self.assertIsNone(acquisition_reading_requests(task,self.context['catalog'],self.questions),mode)

    def test_old_tasks_unknown_versions_and_stale_acquisition_binding_do_not_upgrade(self):
        old=copy.deepcopy(self.task);old.pop(POLICY)
        self.assertIsNone(acquisition_reading_requests(old,self.context['catalog'],self.questions))
        for version in (True,'1',2):
            task=copy.deepcopy(self.task);task[POLICY]=version
            with self.subTest(version=version),self.assertRaises(ContractError):
                acquisition_reading_requests(task,self.context['catalog'],self.questions)
        task=copy.deepcopy(self.task);task['reasoning_state']['acquisition']['binding']['input_hash']='other'
        self.assertIsNone(acquisition_reading_requests(task,self.context['catalog'],self.questions))

    def test_wrong_source_receipt_or_incomplete_catalogue_is_not_read(self):
        for mode in ('ledger','source_hash','partial'):
            task=copy.deepcopy(self.task);catalogue=copy.deepcopy(self.context['catalog'])
            if mode=='ledger':
                for d in task['ledger']['dispatches']:
                    if d.get('operation')=='filing_original':d['status']='失败'
                self.assertIsNone(acquisition_reading_requests(task,catalogue,self.questions))
            elif mode=='partial':
                catalogue['next_offset']=24
                self.assertIsNone(acquisition_reading_requests(task,catalogue,self.questions))
            else:
                next(i for i in catalogue['items'] if i['kind']=='filing')['snapshot_hash']='other'
                with self.assertRaises(ContractError):acquisition_reading_requests(task,catalogue,self.questions)

    def test_query_metadata_cannot_enter_other_endpoints_or_exceed_reader_limits(self):
        for words in ('营业收入',['a']*2,['a'*81],['a']*9,[True],[' ']):
            need=copy.deepcopy(self.need);need['reading_queries']=words
            with self.subTest(words=words),self.assertRaises(ContractError):validate_acquisition_reading_queries(self.task,need)
        wrong=copy.deepcopy(self.need);wrong['operation']='report_query'
        with self.assertRaises(ContractError):validate_acquisition_reading_queries(self.task,wrong)
        old=copy.deepcopy(self.task);old.pop(POLICY)
        with self.assertRaises(ContractError):validate_acquisition_reading_queries(old,self.need)

    def test_planning_and_dispatch_share_the_same_metadata_validation(self):
        plan=dict(questions=question_specs(self.task),data_requests=[copy.deepcopy(self.need)],calculations=[])
        validate_plan(self.task,plan)
        ResearchAgent._validated_data_needs(self.task,plan['data_requests'])
        plan['data_requests'][0]['reading_queries']=['a']*9
        with self.assertRaises(ContractError):validate_plan(self.task,plan)
        with self.assertRaises(ContractError):ResearchAgent._validated_data_needs(self.task,plan['data_requests'])

    def test_only_original_download_schema_exposes_reading_queries(self):
        schema=acquisition_output_schema(self.task,dict(data_request_contract=data_request_contract(self.task)))
        variants=schema['properties']['data_requests']['items']['anyOf']
        original=0
        for variant in variants:
            props=variant['properties'];operation=props['operation']['enum'][0]
            if operation in ('filing_original','release_original'):
                original+=1;self.assertEqual(props['reading_queries']['maxItems'],8)
                self.assertNotIn('reading_queries',props['parameters']['properties'])
            else:self.assertNotIn('reading_queries',props)
        self.assertGreater(original,0)

    def test_engine_skips_only_the_reading_model_and_keeps_budget_and_delivery_reserve(self):
        task=copy.deepcopy(self.task);task.update(status='运行中',run_mode='model')
        task['reasoning_state'].pop('reading',None);task['reasoning_state'].pop('extraction',None)
        task['ledger']['used']['model_calls']=4
        protected=fingerprint({k:task[k] for k in ('evidence','questions','source_records','calculations')})
        agent=ResearchAgent.__new__(ResearchAgent);agent.model=SimpleNamespace()
        agent._active_supplement=Mock(return_value=None);agent._active=Mock();agent._reasoning_available=Mock(return_value=True)
        agent._save_model_checkpoint=Mock();agent._select_model_reading=Mock(side_effect=AssertionError('redundant read call'))
        agent._complete_initial_fee_inputs=Mock(return_value={'evidence_specs':[]})
        def extract(model,actual,phase,payload,expected):
            self.assertEqual(phase,'extract');self.assertIsNotNone(payload['source_reading'])
            charge(actual['ledger'],'model_calls');return {'evidence_specs':[]},{}
        with patch('researchhelper.engine.model_object',side_effect=extract) as calls:
            agent._extract_model_sources(task);self.assertEqual(calls.call_count,1)
        self.assertEqual(task['ledger']['used']['model_calls'],5)
        self.assertEqual(task['ledger']['reserve']['model_calls'],1)
        self.assertEqual(task['reasoning_state']['reading']['receipt']['execution'],'declared_acquisition_reading')
        self.assertEqual(fingerprint({k:task[k] for k in ('evidence','questions','source_records','calculations')}),protected)
        agent._select_model_reading.assert_not_called()

    def test_new_policy_is_input_bound_and_frozen(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance') as folder:
            agent=ResearchAgent(ResearchArchive(Path(folder)),ROOT,offline=True,require_argument_support=True)
            try:
                task=agent.create('解释ETF折溢价',entry='ETF',questions=[dict(id='q1',kind='fact',
                    question='解释ETF折溢价',answer_scope='general_definition',knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task[POLICY],1);old=dict(task);old.pop(POLICY)
                self.assertNotEqual(input_fingerprint(old),task['input_hash'])
                self.assertEqual(compose(agent.start(task['id']))[POLICY],1)
            finally:agent.close_runtime()


if __name__=='__main__':unittest.main()
