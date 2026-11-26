"""Writing uses actual metric scope without another model or delivery gate."""
from copy import deepcopy
import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests import ROOT
from tests.test_client_report_audience import fixture
from tests.test_draft_financial_period import add_record
from tests.market_observation_fixtures import FIXED_FINANCIAL_RESPONSES, append_simulated_ifind_response, read_captures
from core.session import Session
from researchhelper.contracts import canonical,ENTRIES,fingerprint
from researchhelper.draft_analysis import analyze,interpretation_context
from researchhelper.draft_analysis import execute
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

    def test_faster_profit_decline_does_not_identify_a_below_gross_profit_cause(self):
        # Revenue falls from100 to90, cost stays60 and other net deductions
        # stay10. Profit falls from30 to20 with no expense/tax increase.
        revenue=execute('raw_change_v1',dict(a=100,b=90))['value']
        profit=execute('raw_change_v1',dict(a=30,b=20))['value']
        self.assertLess(profit,revenue)
        self.assertEqual((100-60)-30,(90-60)-20)
        self.assertIn('profit_attribution',self.rules())
        self.assertIn('基数不同',self.rules()['profit_attribution']['text'])
        self.assertFalse(interpretation_context(self.task,self.facts)['delivery_blocking'])

    def test_profit_guidance_needs_same_object_observed_fields(self):
        rows=[deepcopy(r) for r in self.facts if r['field'] in ('ths_revenue_stock','ths_np_atoopc_stock')]
        self.assertIn('profit_attribution',self.rules(facts=rows))
        for row in rows:
            if row['field']=='ths_np_atoopc_stock':row['code']='000001.SZ'
        self.assertNotIn('profit_attribution',self.rules(facts=rows))
        self.assertNotIn('profit_attribution',self.rules(facts=[r for r in self.facts if r['field']=='close']))

    def test_margin_ratio_does_not_establish_unit_product_profit(self):
        rows=deepcopy(self.facts)
        cost=dict(rows[0],id='cost',field='ths_operating_cost_stock',label='营业成本',value=60,raw_value=60)
        rows.append(cost)
        self.assertIn('gross_margin_scope',self.rules(facts=rows))
        # Identical revenue/cost totals permit different sales volumes and
        # therefore different gross profit per unit. No volume is inferred.
        self.assertNotEqual((100-60)/10,(100-60)/20)
        self.assertIn('不是单位产品毛利',self.rules(facts=rows)['gross_margin_scope']['text'])
        cost['value']=None
        self.assertNotIn('gross_margin_scope',self.rules(facts=rows))

    def test_valuation_guidance_preserves_reading_units_and_conditional_business_logic(self):
        text=self.rules()['valuation_scope']['text']
        self.assertIn('盈利兑现',text);self.assertIn('风险补偿',text)
        self.assertIn('unit_basis',text);self.assertIn('未知时称读数',text)

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
            task=session.create('比较600519.SH的归母净利润和经营现金流',entry='个股',objects=['600519.SH'],research_mode='current')
            fields = ('ths_np_atoopc_stock', 'ths_ncf_from_oa_stock')
            source = add_record(task, '20251231', (5, 4), fields=fields)
            source.update(content_version=1, obtained_at=task['cutoff'])
            task['synthetic']=True;session.archive.save(task)
            task=session.draft(task['id'],defer_delivery=True,data_requests=[])
            self.assertEqual([(row['field'], row['period'], row['raw_value']) for row in task['draft_observations']],
                             [(fields[0], '20251231', 5), (fields[1], '20251231', 4)])
            guide=session.tools(task['id'])['first_report']['interpretation_context']
            self.assertEqual(guide,interpretation_context(task,task['draft_observations']))
            self.assertIn('parent_profit_scope',{row['id'] for row in guide['rules']})
            task=session.draft(task['id'],report=dict(conclusion='同一时期仍需区分合并与归母口径。'))
            self.assertTrue(task['delivery']);self.assertEqual(task['draft_report']['interpretation_context'],guide)
            self.assertEqual(task['ledger']['used']['model_calls'],0)
            self.assertEqual(task['ledger']['used']['data_requests'],0)

    def test_each_entry_saves_a_limited_report_when_all_observations_are_missing(self):
        with TemporaryDirectory(prefix='rh-empty-evidence-') as folder:
            session = Session(folder)
            try:
                for entry in ENTRIES:
                    with self.subTest(entry=entry):
                        task = session.create('当前是否有充分依据支持增持？列出判断成立的条件。', entry=entry)
                        done = session.draft(task['id'])
                        self.assertEqual(done['status'], '部分完成')
                        draft = done['draft_report']
                        self.assertEqual(draft['facts'], [])
                        self.assertEqual(draft['logics'], [])
                        self.assertFalse(draft['interpretation_context']['delivery_blocking'])
                        saved = session.archive.report(task['id'], 1)
                        self.assertTrue(saved['html_integrity_verified'])
                        self.assertIn('未取得足以形成研究判断的资料', saved['html'])
                        self.assertEqual(session.export(task['id']).read_text(), saved['html'])
            finally:
                session.agent.close_runtime()

    def test_incidental_event_word_does_not_reclassify_an_etf_or_company(self):
        for entry in ('ETF','个股'):
            task=dict(self.task,entry=entry,question='某事件对该对象的影响')
            report=normalize_report(None,task,[])
            self.assertNotIn('事件变化与实际暴露',[row['title'] for row in report['logics']])

    def test_financial_echo_contradictions_are_rejected_before_draft_normalization(self):
        before = FIXED_FINANCIAL_RESPONSES.read_bytes()
        captures = read_captures(before, grouped=True)
        policy, capture = captures['get_limit_policy:'], captures['basic_data_service:20260630']
        with TemporaryDirectory() as folder:
            session = Session(folder)
            try:
                task = session.create('核对比亚迪002594.SZ的2026年上半年合并报表营业收入、营业成本、净利润和经营现金流')
                retained_policy = append_simulated_ifind_response(task, 'get_limit_policy',
                                                                  policy['parameters'], policy['snapshot'])
                original = snapshot_record(task, 'ifind', 'basic_data_service',
                                           capture['parameters'], capture['snapshot'])
                self.assertEqual(original['status'], '成功待核验')
                for field, value in [('FDREPORT', '19991231'), ('FL', '2')]:
                    payload = deepcopy(capture['snapshot'])
                    fields = payload['inputParams']['params'][0]['params']
                    next(row for row in fields if row['name'] == field)['value'] = value
                    record = snapshot_record(task, 'ifind', 'basic_data_service', capture['parameters'], payload)
                    with self.subTest(field=field):
                        self.assertEqual(record['status'], '返回结构异常')
                        self.assertEqual(observations(dict(task, source_records=[retained_policy, record])), [])
            finally:
                session.agent.close_runtime()
        self.assertEqual(FIXED_FINANCIAL_RESPONSES.read_bytes(), before)


if __name__=='__main__':unittest.main()
