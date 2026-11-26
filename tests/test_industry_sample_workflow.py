"""Industry candidates to selected financial sample, with no paid dependencies.

Candidate and account-policy payloads are retained real responses. Identity
and financial payloads below are deliberately synthetic, never vendor proof.
"""
from copy import deepcopy
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from tests.test_draft_discovery_navigation import retained_inputs


def run_industry_sample(folder, resources, inputs, *, wrong_financial_code=False, missing_period=False, periods=None, question=None, include_profit=False):
    from copy import deepcopy
    import json
    from types import SimpleNamespace
    from researchhelper.archive import ResearchArchive
    from researchhelper.contracts import ContractError,canonical,charge,fingerprint,now
    from researchhelper.draft_sources import observations
    from researchhelper.engine import ResearchAgent
    from researchhelper.evidence import (snapshot_record,_record_current_acquisition,
        _controlled_financial_definitions,identity_query_parameters,identity_resolutions)
    from researchhelper.object_discovery import retained_discovery_results,research_sample_codes
    periods=periods or ('20241231','20251231')
    before,after=periods
    phases,operations,plans,contexts=[],[],[],[]
    policy,discovery=inputs
    fields=['ths_revenue_stock','ths_np_stock','ths_ncf_from_oa_stock']

    def candidate(task):
        return retained_discovery_results(task)[0]['candidates'][0]

    def transport(task,provider,operation,params,credentials):
        assert provider=='ifind' and not credentials
        if operation=='get_limit_policy':payload=deepcopy(policy['snapshot'])
        elif operation=='smart_stock_picking':
            assert params==discovery['parameters'];payload=deepcopy(discovery['snapshot'])
        elif operation=='get_thscode':
            selected=candidate(task);number=selected['code'].split('.')[0]
            assert params==identity_query_parameters('seccode',number)
            payload=dict(errorcode=0,inputParams=dict(mode='seccode',isexact=1),
                tables=[dict(seccode=number,table=dict(thscode=[selected['code']]))],
                synthetic_test_response=True)
        elif operation=='basic_data_service':
            selected=candidate(task);assert params['codes']==selected['code']
            period=params['indipara'][0]['indiparams'][0]
            assert {row['indicator'] for row in params['indipara']}==set(fields)
            values=[120,12,10] if period==after else [100,9,15]
            code='600519.SH' if wrong_financial_code and period==after else selected['code']
            payload=dict(errorcode=0,inputParams=dict(params=[dict(function=field,params=[dict(name='FDREPORT',value=period),dict(name='FL',value='1')]) for field in fields]),
                tables=[dict(thscode=code,table={field:[value] for field,value in zip(fields,values)})],
                synthetic_test_response=True)
            if missing_period and period==after:payload=dict(errorcode=-1,tables=[],synthetic_test_response=True)
        else:raise ContractError('No retained fixture for this optional dependency; no network')
        charge(task['ledger'],'data_requests',1)
        record=snapshot_record(task,provider,operation,params,payload)
        if operation=='basic_data_service':
            record.update(fixed_financial_catalog_version=1,fixed_financial_catalog_digest=fingerprint(_controlled_financial_definitions()))
        event=dict(kind='data_requests',source=provider,operation=operation,
            fingerprint=record['request_hash'],batch=task['batch'],time=now(),
            status='成功' if record['status']=='成功待核验' else '失败',
            transport_isolated=True,network_request_started=True,network_worker_reaped=True,synthetic_test_transport=True)
        task['ledger']['dispatches'].append(event);_record_current_acquisition(task,record,event)
        operations.append(operation);return record

    def reply(task,messages,*,phase,delivery=False,**options):
        phases.append(phase);charge(task['ledger'],'model_calls',1,delivery=delivery)
        payload=json.loads(messages[-1]['content']);contexts.append(deepcopy(payload))
        if phase=='draft_plan':
            step=phases.count('draft_plan')
            plans.append(dict(step=step,tools=[row['operation'] for row in payload['data_request_contract']['tools']],
                callable_codes=payload['data_request_contract']['callable_codes']))
            if step==1:requests=[dict(id='candidates',source='ifind',operation='smart_stock_picking',parameters=deepcopy(discovery['parameters']))]
            elif step==2:requests=[dict(id='selected_identity',source='ifind',operation='get_thscode',
                parameters=identity_query_parameters('seccode',candidate(task)['code'].split('.')[0]))]
            else:requests=[dict(id='sample_'+period,source='ifind',operation='basic_data_service',
                parameters=dict(codes=candidate(task)['code'],indipara=[dict(indicator=field,indiparams=[period,'1']) for field in fields])) for period in periods]
            for row in requests:row.update(question_ids=[task['questions'][0]['id']],selection_reason='按原问题选择一个候选样本，明确身份后核对两期财务原值，不外推全行业。')
            return canonical(dict(data_requests=requests))
        facts=observations(task)
        refs=lambda chosen:[row['id'] for row in facts if row['field'] in chosen]
        return canonical(dict(title='行业候选样本经营观察',
            conclusion='本次仅观察选定样本的两期经营原值，盈利和现金兑现分别判断，不能据此推断全行业表现。',
            logics=[dict(title='样本经营规模',analysis='仅使用所选证券实际返回的收入原值；一个候选的变化不代表行业均值。',data_refs=refs(['ths_revenue_stock','ths_np_stock'] if include_profit else ['ths_revenue_stock'])),
                dict(title='盈利与现金兑现',analysis='净利润和经营现金流分别核对，不以盈利变化代替现金兑现，也不从同期数据证明因果。',data_refs=refs(['ths_np_stock','ths_ncf_from_oa_stock'])),
                dict(title='样本范围',analysis='候选检索和证券代码转换不证明行业归属、实际业务暴露或完整样本，仍需对应原件。')],
            risks=['单一样本不代表行业，缺少业务暴露和完整样本时不做行业平均或板块推荐。']))

    archive=ResearchArchive(folder)
    agent=ResearchAgent(archive,resources,offline=False,model=SimpleNamespace(reason=reply),
        source_credentials={},require_argument_support=True,draft_first=True)
    agent.collector.external=transport
    try:
        task=agent.create(question or '研究证券行业，从检索候选选择一家作样本，先核对证券身份，再比较2024年和2025年收入、报表净利润与经营现金流，不以样本代表全行业。',
            entry='行业',objects=[],research_mode='current',limits=dict(model_calls=4,data_requests=100,seconds=600))
        task['synthetic']=True;archive.save(task)
        result=agent.start(task['id'])
        assert result.get('delivery'),dict(error=result.get('error'),phases=phases,operations=operations)
        return dict(task=result,paper=archive.report(task['id'],result['version']),phases=phases,
            operations=operations,plans=plans,contexts=contexts,selected=candidate(result),
            mapped=identity_resolutions(result),sample_codes=research_sample_codes(result))
    finally:agent.close_runtime()


class IndustrySampleWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='rh-industry-sample-');self.folder=Path(temporary.name);self.addCleanup(temporary.cleanup)
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No real network'));guard.start();self.addCleanup(guard.stop)
        data=retained_inputs();self.inputs=[data[3],data[0]]

    def run_case(self,**options):return run_industry_sample(self.folder,ROOT,self.inputs,**options)

    def test_candidate_identity_and_financial_apis_form_one_saved_research(self):
        result=self.run_case();task=result['task'];code=result['selected']['code']
        self.assertEqual(result['phases'],['draft_plan']*3+['draft_report'])
        self.assertEqual(result['operations'],['smart_stock_picking','get_thscode','get_limit_policy','basic_data_service','basic_data_service'])
        self.assertEqual(task['objects'],[]);self.assertEqual(result['sample_codes'],[code])
        self.assertEqual(len(task['draft_report']['facts']),6)
        self.assertEqual({row['code'] for row in task['draft_report']['facts']},{code})
        self.assertIn('basic_data_service',result['plans'][2]['tools'])
        self.assertEqual([row['codes'] for row in result['mapped'] if row['status']=='代码映射已核验'],[[code]])
        self.assertEqual(task['draft_acquisition_coverage']['unresolved'],[])
        self.assertEqual(task['ledger']['used']['model_calls'],4);self.assertEqual(task['ledger']['used']['data_requests'],5)
        self.assertIn('策略逻辑三',result['paper']['html'])

    def test_wrong_financial_subject_only_removes_its_own_period(self):
        result=self.run_case(wrong_financial_code=True);facts=result['task']['draft_report']['facts']
        self.assertEqual(len(facts),3);self.assertTrue(all(row['code']==result['selected']['code'] for row in facts))
        self.assertNotIn('600519.SH',str(facts));self.assertFalse(result['task']['draft_acquisition_coverage']['blocks_report'])
        self.assertIn('600519.SH',str(result['task']['source_records']))

    def test_missing_period_still_saves_available_financial_sample(self):
        result=self.run_case(missing_period=True);task=result['task']
        self.assertEqual(len(task['draft_report']['facts']),3);self.assertTrue(task['delivery'])
        self.assertFalse(task['draft_acquisition_coverage']['blocks_report'])
        self.assertEqual(result['phases'],['draft_plan']*3+['draft_report'])
