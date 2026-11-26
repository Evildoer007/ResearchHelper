"""A display correction cannot borrow another financial measure's growth."""
from copy import deepcopy
import socket,unittest
from unittest.mock import patch
from tests.test_draft_client_copy import fixture
from tests.test_draft_financial_period import add_record,rehash
from researchhelper.contracts import canonical
from researchhelper.draft_sources import observations
from researchhelper.draft_analysis import analyze
from researchhelper.publication_content import prepare_draft_client_copy


def profit_fixture(*,known_parent=False):
    task,_,_=fixture()
    for period,value in [('20241231',20),('20251231',12)]:
        add_record(task,period,(value,),fields=('ths_np_atoopc_stock',))
    facts=observations(task)
    if known_parent:
        for row in facts:
            if row['field']=='ths_np_atoopc_stock':row['unit']='元'
    task['draft_analysis']=analyze(task,facts)
    return task,facts


def run_amount_identity_research(folder,resources,policy):
    """Real orchestration with retained policy and explicitly synthetic replies."""
    import json
    from copy import deepcopy
    from types import SimpleNamespace
    from researchhelper.archive import ResearchArchive
    from researchhelper.engine import ResearchAgent
    from researchhelper.contracts import charge,canonical,fingerprint,now
    from researchhelper.draft_sources import observations
    from researchhelper.evidence import snapshot_record,_record_current_acquisition,_controlled_financial_definitions
    phases=[];operations=[];captured={}
    measures=[('ths_np_atoopc_stock','1',(20,12)),('ths_np_stock','2',(10,12))]
    periods=('20241231','20251231')
    def transport(task,provider,operation,params,credentials):
        assert provider=='ifind' and not credentials
        if operation=='get_limit_policy':payload=deepcopy(policy)
        elif operation=='basic_data_service':
            assert params['codes']=='600519.SH' and len(params['indipara'])==1
            item=params['indipara'][0];period,statement=item['indiparams'];field=item['indicator']
            measure=next(row for row in measures if row[:2]==(field,statement));value=measure[2][periods.index(period)]
            payload=dict(errorcode=0,synthetic_test_response=True,
                inputParams=dict(params=[dict(function=field,params=[dict(name='FDREPORT',value=period),dict(name='FL',value=statement)])]),
                tables=[dict(thscode='600519.SH',table={field:[value]})])
        else:raise AssertionError('Unexpected data operation: '+operation)
        charge(task['ledger'],'data_requests');record=snapshot_record(task,provider,operation,params,payload)
        if operation=='basic_data_service':record.update(fixed_financial_catalog_version=1,fixed_financial_catalog_digest=fingerprint(_controlled_financial_definitions()))
        event=dict(kind='data_requests',source=provider,operation=operation,fingerprint=record['request_hash'],batch=task['batch'],time=now(),
            status='成功',transport_isolated=True,network_request_started=True,network_worker_reaped=True,synthetic_test_transport=True)
        task['ledger']['dispatches'].append(event);_record_current_acquisition(task,record,event);operations.append(operation);return record
    def reply(task,messages,*,phase,delivery=False,**options):
        phases.append(phase);charge(task['ledger'],'model_calls',delivery=delivery)
        if phase=='draft_plan':
            return canonical(dict(data_requests=[dict(id='profit_'+statement+'_'+period,source='ifind',operation='basic_data_service',
                parameters=dict(codes='600519.SH',indipara=[dict(indicator=field,indiparams=[period,statement])]),
                question_ids=[task['questions'][0]['id']],selection_reason='分别比较两类利润，不互相替代。')
                for field,statement,values in measures for period in periods]))
        assert phase=='draft_report';captured['writing']=json.loads(messages[1]['content'])
        facts=observations(task);logics=[]
        for field,statement,values in measures:
            refs=[row['id'] for row in facts if row['field']==field]
            logics.append(dict(title='归母利润变化' if field=='ths_np_atoopc_stock' else '母公司报表利润变化',
                analysis='归母净利润由20元降至12元。' if field=='ths_np_atoopc_stock' else '母公司报表净利润由10元升至12元。',
                data_refs=refs,calculation_refs=[row['id'] for row in task['draft_analysis']['results'] if set(row['data_refs'])<=set(refs)],
                risk='合成响应仅用于程序验收，不代表真实财务或投资结论。'))
        return canonical(dict(title='利润口径流程验收',report_scope='research',
            conclusion='归母净利润12元，母公司报表净利润由10元升至12元；两类利润不能相互替代。',
            logics=logics,risks=['合成响应仅用于程序验收，不代表真实财务。']))
    archive=ResearchArchive(folder);agent=ResearchAgent(archive,resources,offline=False,model=SimpleNamespace(reason=reply),source_credentials={},require_argument_support=True,draft_first=True)
    agent.collector.external=transport
    try:
        task=agent.create('研究600519.SH的两类利润变化，比较2024年和2025年归母净利润与母公司报表净利润，分别解释其投资含义并生成报告。',
            entry='个股',objects=['600519.SH'],research_mode='current',limits=dict(model_calls=3,data_requests=100,seconds=600))
        task['synthetic']=True;archive.save(task);task=agent.start(task['id'])
        assert task.get('delivery'),dict(error=task.get('error'),phases=phases,operations=operations)
        return dict(task=task,paper=archive.report(task['id'],task['version']),phases=phases,operations=operations,writing=captured['writing'])
    finally:agent.close_runtime()


class ClientAmountMetricIdentityTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)

    def revise(self,task,facts,text,**extra):
        original=dict(title='经营观察',conclusion=text,logics=[],**extra)
        before=canonical((task,facts,original));result,changes=prepare_draft_client_copy(task,original,facts)
        self.assertEqual(canonical((task,facts,original)),before)
        self.assertTrue(all(change['financial_approval'] is False for change in changes))
        return result,changes

    def test_an_unavailable_parent_measure_does_not_borrow_statement_profit(self):
        task,facts,_=fixture()
        for name in ['归母净利润','归属于母公司所有者的净利润']:
            with self.subTest(name=name):
                text=name+'12元，经营判断保持不变。';result,changes=self.revise(task,facts,text)
                self.assertEqual(result['conclusion'],text);self.assertEqual(changes,[])

    def test_known_parent_amounts_remain_verbatim_beside_unknown_statement_profit(self):
        task,facts=profit_fixture(known_parent=True)
        for name in ['归母净利润','归属于母公司所有者的净利润']:
            with self.subTest(name=name):
                text=name+'12元。';result,changes=self.revise(task,facts,text)
                self.assertEqual(result['conclusion'],text);self.assertEqual(changes,[])

    def test_available_unknown_parent_measure_uses_only_its_own_comparison(self):
        task,facts=profit_fixture();result,changes=self.revise(task,facts,'归母净利润12元。')
        self.assertEqual(result['conclusion'],'归母净利润同比-40.00%。')
        altered=next(change for change in changes if change['path']=='conclusion')
        selected={row['id']:row for row in facts}
        self.assertTrue(all(selected[ref]['field']=='ths_np_atoopc_stock' for ref in altered['data_refs']))
        calc=next(row for row in task['draft_analysis']['results'] if row['id']==altered['calculation_refs'][0])
        self.assertEqual(calc['display'],'-40.00')

    def test_plain_statement_profit_still_uses_its_own_comparison(self):
        task,facts=profit_fixture();result,_=self.revise(task,facts,'净利润12元。')
        self.assertEqual(result['conclusion'],'净利润同比20.00%。')

    def test_distinct_deducted_and_per_share_measures_are_not_total_net_profit(self):
        task,facts,_=fixture()
        for name in ['扣非净利润','扣非归母净利润','扣除非经常性损益后的净利润',
                     '扣除非经常性损益后归属于母公司所有者的净利润','每股净利润']:
            with self.subTest(name=name):
                text=name+'12元。';result,changes=self.revise(task,facts,text)
                self.assertEqual(result['conclusion'],text);self.assertEqual(changes,[])

    def test_per_person_revenue_is_not_total_revenue(self):
        task,facts,_=fixture()
        for name in ['人均营业收入','单位营业收入','每股营业收入']:
            with self.subTest(name=name):
                text=name+'120元。';result,changes=self.revise(task,facts,text)
                self.assertEqual(result['conclusion'],text);self.assertEqual(changes,[])

    def test_a_metric_label_cannot_reclassify_a_different_canonical_field(self):
        task,facts,_=fixture()
        for row in facts:
            if row['field']=='ths_np_stock':row['label']='归母净利润'
        task['draft_analysis']=analyze(task,facts);text='归母净利润12元。'
        result,changes=self.revise(task,facts,text);self.assertEqual(result['conclusion'],text);self.assertEqual(changes,[])

    def test_a_known_match_is_not_rewritten_from_another_unknown_scale(self):
        task,_,_=fixture()
        for period,value in [('20241231',100),('20251231',120)]:
            source=add_record(task,period,(value,),fields=('ths_revenue_stock',))
            source['parameters']['indipara'][0]['indiparams'][1]='2';rehash(source)
        facts=observations(task)
        for row in facts:
            if row['field']=='ths_revenue_stock' and row['artifact_id'] in ('source-1','source-2'):row['unit']='元'
        task['draft_analysis']=analyze(task,facts);text='营业收入120元，仍需观察需求。'
        result,changes=self.revise(task,facts,text);self.assertEqual(result['conclusion'],text);self.assertEqual(changes,[])

    def test_an_unrelated_known_price_does_not_hide_an_unknown_financial_amount(self):
        task,facts,_=fixture();facts.append(dict(facts[-1],id='known-price',field='close',label='收盘价',value=120,raw_value=120,unit='元'))
        task['draft_analysis']=analyze(task,facts);result,_=self.revise(task,facts,'营业收入120元，收盘价120元。')
        self.assertEqual(result['conclusion'],'营业收入同比20.00%，收盘价120元。')

    def test_an_unmatched_second_endpoint_cannot_turn_the_first_into_a_percentage(self):
        task,facts,_=fixture();result,_=self.revise(task,facts,'营业收入由120元升至999元，后者仅为假设。')
        self.assertEqual(result['conclusion'],'营业收入由120（单位未明）升至999元，后者仅为假设。')
        self.assertNotIn('同比',result['conclusion'])

    def test_exact_observed_endpoints_keep_the_existing_relative_correction(self):
        task,facts,_=fixture();result,_=self.revise(task,facts,'营业收入由100元升至120元，其他条件不变。')
        self.assertEqual(result['conclusion'],'营业收入同比20.00%，其他条件不变。')

    def test_reversed_endpoints_are_not_recast_as_forward_growth(self):
        task,facts,_=fixture();result,_=self.revise(task,facts,'营业收入由120元降至100元。')
        self.assertEqual(result['conclusion'],'营业收入由120（单位未明）降至100（单位未明）。')
        self.assertNotIn('同比',result['conclusion'])

    def test_a_known_first_endpoint_does_not_hide_an_unknown_second_endpoint(self):
        task,facts,_=fixture();facts[0]['unit']='元';task['draft_analysis']=analyze(task,facts)
        result,_=self.revise(task,facts,'营业收入由100元升至120元。')
        self.assertEqual(result['conclusion'],'营业收入由100元升至120（单位未明）。')

    def test_a_known_second_endpoint_stays_unchanged(self):
        task,facts,_=fixture();facts[3]['unit']='元';task['draft_analysis']=analyze(task,facts)
        result,_=self.revise(task,facts,'营业收入由100元升至120元。')
        self.assertEqual(result['conclusion'],'营业收入由100（单位未明）升至120元。')

    def test_correction_does_not_silently_remove_a_contradictory_direction(self):
        task,facts,_=fixture();result,_=self.revise(task,facts,'营业收入由100元降至120元。')
        self.assertEqual(result['conclusion'],'营业收入由100（单位未明）降至120（单位未明）。')
        task,facts=profit_fixture();result,_=self.revise(task,facts,'归母净利润由20元升至12元。')
        self.assertEqual(result['conclusion'],'归母净利润由20（单位未明）升至12（单位未明）。')

    def test_matching_uses_the_raw_decimal_rather_than_a_rounded_float(self):
        task,facts,_=fixture();fact=deepcopy(facts[4]);raw='9007199254740993.0000000001'
        fact.update(value=float(raw),raw_value=raw);facts=[fact];task['draft_analysis']=analyze(task,facts)
        result,_=self.revise(task,facts,'净利润'+raw+'元。')
        self.assertEqual(result['conclusion'],'净利润'+raw+'（单位未明）。')

    def test_mixed_sentence_corrects_only_the_matching_financial_measure(self):
        task,facts,_=fixture();result,_=self.revise(task,facts,'营业收入120元与归母净利润12元分别观察。')
        self.assertEqual(result['conclusion'],'营业收入同比20.00%与归母净利润12元分别观察。')

    def test_one_sentence_workflow_keeps_known_parent_profit_and_its_own_growth(self):
        import tempfile
        from tests import ROOT
        from tests.test_draft_discovery_navigation import retained_inputs
        with tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance') as folder:
            result=run_amount_identity_research(folder,ROOT,retained_inputs()[3]['snapshot'])
            self.assertEqual(result['phases'],['draft_plan','draft_report'])
            self.assertEqual(result['operations'],['get_limit_policy']+['basic_data_service']*4)
            facts=result['task']['draft_report']['facts'];self.assertEqual(len(facts),4)
            self.assertEqual({row['unit'] for row in facts if row['field']=='ths_np_atoopc_stock'},{'元'})
            self.assertEqual({row['unit'] for row in facts if row['field']=='ths_np_stock'},{'原始单位待核对'})
            conclusion=result['task']['draft_report']['conclusion']
            self.assertIn('归母净利润12元',conclusion);self.assertNotIn('归母净利润同比20.00%',conclusion)
            self.assertIn('母公司报表净利润同比20.00%',conclusion)
            context=result['writing']['data']['observations']
            self.assertTrue(all('value' in row['columns'] for row in context if row['field']=='ths_np_atoopc_stock'))
            self.assertTrue(all('raw_value_available' in row['columns'] for row in context if row['field']=='ths_np_stock'))

    def test_argument_and_risk_fields_share_the_same_metric_identity(self):
        task,facts,_=fixture();text='归母净利润12元。'
        proposal=dict(title='经营观察',conclusion='继续观察。',risks=[text],
            logics=[dict(title='利润口径',analysis=text,argument=dict(claim=text),implication=text,risk=text,data_refs=[row['id'] for row in facts])])
        result,changes=prepare_draft_client_copy(task,proposal,facts)
        self.assertEqual(result,proposal);self.assertEqual(changes,[])


if __name__=='__main__':unittest.main()
