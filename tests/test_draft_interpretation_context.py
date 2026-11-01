"""Writing uses actual metric scope without another model or delivery gate."""
from copy import deepcopy
import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests import ROOT
from tests.test_client_report_audience import fixture
from tests.test_draft_first_workflow import SOURCE
from core.session import Session
from researchhelper.contracts import canonical,ENTRIES,fingerprint
from researchhelper.draft_analysis import analyze,interpretation_context
from researchhelper.draft_sources import observations
from researchhelper.draft_workflow import _write
from researchhelper.drafts import normalize_report
from researchhelper.evidence import snapshot_record


class DraftInterpretationTests(unittest.TestCase):
    def setUp(self):
        guard=patch('socket.socket.connect',side_effect=AssertionError('No external I/O'))
        guard.start();self.addCleanup(guard.stop)
        self.task,_=fixture();self.facts=self.task['draft_report']['facts']

    def rules(self,task=None,facts=None):
        return {row['id']:row for row in interpretation_context(
            task or self.task,self.facts if facts is None else facts)['rules']}

    def test_parent_profit_and_consolidated_cash_flow_are_not_a_cash_coverage_ratio(self):
        rules=self.rules()
        self.assertEqual(rules['parent_profit_scope']['objects'],['600519.SH'])
        self.assertIn('不能用归母净利润代替',rules['parent_profit_scope']['text'])
        self.assertIn('归因需有',rules['cash_attribution']['text'])
        self.assertFalse(any(row['method']=='raw_multiple_v1' for row in self.task['draft_analysis']['results']))

    def test_same_basis_statement_profit_keeps_registered_ratio_and_scope(self):
        facts=deepcopy(self.facts)
        for row in facts:
            if row['field']=='ths_np_atoopc_stock':row['field']='ths_np_stock'
        calculations=analyze(self.task,facts)['results']
        self.assertTrue(any(row['method']=='raw_multiple_v1' for row in calculations))
        self.assertNotIn('parent_profit_scope',self.rules(facts=facts))
        self.assertIn('cash_attribution',self.rules(facts=facts))

    def test_rules_apply_only_to_the_objects_with_those_actual_fields(self):
        facts=self.facts+[dict(self.facts[-2],id='other',code='000001.SZ')]
        rules=self.rules(facts=facts)
        self.assertEqual(rules['cash_attribution']['objects'],['600519.SH'])
        self.assertEqual(rules['close_scope']['objects'],['000001.SZ','600519.SH'])
        self.assertIn('不能自动代替该观察时点的TTM分母',rules['valuation_scope']['text'])

    def test_balance_and_macro_guidance_is_not_assigned_to_unrelated_price_data(self):
        facts=deepcopy(self.facts)
        facts[0].update(field='ths_inventory_stock')
        self.assertIn('balance_flow_scope',self.rules(facts=facts))
        self.assertNotIn('macro_contribution',self.rules())
        task=deepcopy(self.task);task['source_records'][0]['operation']='edb_service'
        task['source_records'][0]['snapshot']={'tables':[]}
        facts[0].update(field='value',code='M_TEST')
        rules=self.rules(task,facts)
        self.assertEqual(rules['macro_contribution']['objects'],['M_TEST'])

    def test_failed_foreign_and_missing_values_do_not_become_observed_scope(self):
        for mode in ('failed','foreign','missing'):
            task=deepcopy(self.task);facts=deepcopy(self.facts)
            if mode=='failed':task['source_records'][0].update(kind='api',status='失败')
            elif mode=='foreign':task['source_records'][0]['task_id']='other'
            else:
                for row in facts:
                    if row['artifact_id']=='financial':row['value']=None
            with self.subTest(mode=mode):self.assertNotIn('cash_attribution',self.rules(task,facts))

    def test_writing_scope_is_bound_readonly_and_does_not_approve_financial_claims(self):
        before=canonical(self.task);guide=interpretation_context(self.task,self.facts)
        self.assertEqual(guide['binding']['facts_hash'],fingerprint(self.facts))
        self.assertFalse(guide['financial_approval']);self.assertFalse(guide['delivery_blocking'])
        self.assertEqual(interpretation_context(self.task,[])['rules'],[])
        self.assertEqual(canonical(self.task),before)

    def test_existing_writer_receives_calculation_conditions_and_the_same_frozen_context(self):
        captured=[]
        class Model:
            def reason(self,task,messages,**options):captured.append((messages,options));return '{}'
        class Engine:model=Model()
        before=canonical(self.task);_write(Engine(),self.task,self.facts)
        self.assertEqual(len(captured),1)
        data=json.loads(captured[0][0][1]['content'])['data']
        self.assertEqual(data['interpretation_context'],interpretation_context(self.task,self.facts))
        original={row['id']:row for row in self.task['draft_analysis']['results']}
        self.assertTrue(data['calculations'])
        for row in data['calculations']:self.assertEqual(row['condition'],original[row['id']]['condition'])
        report=normalize_report(dict(conclusion='保留原判断。'),self.task,self.facts)
        self.assertEqual(report['interpretation_context'],data['interpretation_context'])
        self.assertEqual(report['conclusion'],'保留原判断。');self.assertEqual(canonical(self.task),before)

    def test_independent_host_reads_the_same_observation_bound_guidance(self):
        with TemporaryDirectory() as folder:
            session=Session(folder);self.addCleanup(session.agent.close_runtime)
            task=session.create('比较归母净利润和经营现金流',entry='个股',research_mode='current')
            task['source_records']=[dict(id='cash-fixture',task_id=task['id'],research_version=task['version'],
                kind='api',content_version=1,status='成功待核验',obtained_at=task['cutoff'],
                source='ifind',operation='basic_data_service',parameters=dict(report_period='20251231',statement_type='1'),
                snapshot=dict(tables=[dict(thscode='600519.SH',table=dict(ths_np_atoopc_stock=[5],ths_ncf_from_oa_stock=[4]))]))]
            source=task['source_records'][0]
            source['hash']=fingerprint(source['snapshot'])
            source['request_hash']=fingerprint({key:source[key] for key in ('source','operation','parameters')})
            task['synthetic']=True;session.archive.save(task)
            task=session.draft(task['id'],defer_delivery=True,data_requests=[])
            guide=session.tools(task['id'])['first_report']['interpretation_context']
            self.assertEqual(guide,interpretation_context(task,task['draft_observations']))
            self.assertIn('parent_profit_scope',{row['id'] for row in guide['rules']})
            task=session.draft(task['id'],report=dict(conclusion='同一时期仍需区分合并与归母口径。'))
            self.assertTrue(task['delivery']);self.assertEqual(task['draft_report']['interpretation_context'],guide)
            self.assertEqual(task['ledger']['used']['model_calls'],0)
            self.assertEqual(task['ledger']['used']['data_requests'],0)

    def test_each_entry_can_still_deliver_a_relevant_conditional_outline(self):
        forbidden={'事件':('利率与风险溢价传导',),'指数':('跟踪与交易约束','对照净值、市场价格和费用'),'宏观':('利率与风险溢价传导',),
                   'ETF':('现金兑现与反证',),'混合':('现金兑现与反证',)}
        for entry in ENTRIES:
            task=dict(self.task,entry=entry,question='围绕宏观事件解释该对象的变化')
            report=normalize_report(None,task,[]);text=canonical(report['logics'])
            with self.subTest(entry=entry):
                self.assertTrue(report['logics']);self.assertFalse(report['interpretation_context']['delivery_blocking'])
                for phrase in forbidden.get(entry,()):
                    self.assertNotIn(phrase,text)

    def test_incidental_event_word_does_not_reclassify_an_etf_or_company(self):
        for entry in ('ETF','个股'):
            task=dict(self.task,entry=entry,question='某事件对该对象的影响')
            report=normalize_report(None,task,[])
            self.assertNotIn('事件变化与实际暴露',[row['title'] for row in report['logics']])

    def test_financial_echo_contradictions_are_rejected_before_draft_normalization(self):
        before=SOURCE.read_bytes();task=json.loads(before)
        original=next(row for row in task['source_records'] if row['operation']=='basic_data_service')
        for field,value in [('FDREPORT','19991231'),('FL','2')]:
            payload=deepcopy(original['snapshot'])
            values=payload['inputParams']['params'][0]['params']
            next(row for row in values if row['name']==field)['value']=value
            record=snapshot_record(task,'ifind','basic_data_service',original['parameters'],payload)
            with self.subTest(field=field):
                self.assertEqual(record['status'],'返回结构异常')
                self.assertEqual(observations(dict(task,source_records=[record])),[])
        self.assertEqual(SOURCE.read_bytes(),before)


if __name__=='__main__':unittest.main()
