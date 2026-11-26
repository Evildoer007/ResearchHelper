"""Task research purposes survive source reuse and reach both writing hosts.

Network denied. Actual source/SQLite/workflow code runs against retained
provider responses; model calls are fake and never count as model acceptance.
"""
from copy import deepcopy
import json
import socket
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import ROOT
from tests import test_draft_first_workflow as financial
from core.session import Session
from researchhelper.contracts import canonical
from researchhelper.draft_workflow import _plan, _write
from researchhelper.evidence import snapshot_record, record_request_purpose
from researchhelper.planning import draft_research_context
from researchhelper.publication_content import client_report_brief, CLIENT_REPORT_BRIEF, RESEARCH_LOGIC_BRIEF, REPORT_CONDITION_BRIEF


class DraftResearchHandoffTests(unittest.TestCase):
    def setUp(self):
        network = patch.object(socket.socket, 'connect', side_effect=AssertionError('No real calls'))
        network.start(); self.addCleanup(network.stop)
        self.temp = TemporaryDirectory(prefix='rh-research-handoff-')
        self.addCleanup(self.temp.cleanup)
        self.session = Session(self.temp.name)
        self.addCleanup(self.session.agent.close_runtime)
        self.task = self.session.create('研究600519.SH的经营变化与可能投资含义', objects=['600519.SH'],
            research_mode='current')

    def source(self, task=None):
        task = self.task if task is None else task
        parameters = dict(codes='600519.SH', indicators='close', startdate='2026-10-29', enddate='2026-10-30',
                          functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
        response = dict(errorcode=0, tables=[dict(thscode='600519.SH', time=['2026-10-29','2026-10-30'],
                                                  table=dict(close=[100,101]))])
        record = snapshot_record(task, 'ifind', 'cmd_history_quotation', parameters, response)
        record.update(data_need_ids=['price'], question_ids=[task['questions'][0]['id']],
                      selection_reason='观察价格表现，用于检验定价路径。')
        return record

    def test_initial_chat_receives_conditional_research_requirements_instead_of_the_generic_fallback(self):
        brief = client_report_brief(self.task)
        self.assertIn('report_scope为research', brief)
        self.assertIn('从什么变化中获利', brief)
        self.assertIn('证据不足时只写待验证的假设', brief)
        self.assertEqual(brief.count('投资研究开头交代时点和核心判断'), 1)
        self.assertEqual(brief.count(REPORT_CONDITION_BRIEF), 1)
        self.assertNotEqual(brief, CLIENT_REPORT_BRIEF)

    def test_explicit_fact_and_comparison_do_not_acquire_investment_obligations(self):
        for kind in ('fact','comparison'):
            task = self.session.create('只核对指定数据', questions=[dict(id='q1',question='只核对指定数据',kind=kind,required=True)])
            self.assertNotIn(RESEARCH_LOGIC_BRIEF, client_report_brief(task))
            before = canonical(task)
            context = draft_research_context(task)
            self.assertFalse(context['automatic_plan_change'])
            self.assertFalse(context['financial_approval'])
            self.assertEqual(context['extra_model_calls'],0)
            self.assertEqual(canonical(task), before)

    def test_legacy_source_keeps_its_actual_purpose_and_question_without_inventing_a_request_id(self):
        record = self.source(); self.task['source_records'] = [record]
        before = canonical(self.task)
        rows = draft_research_context(self.task)['source_purposes']
        self.assertEqual(rows, [dict(source_id=record['id'],question_ids=record['question_ids'],purpose=record['selection_reason'])])
        self.assertEqual(canonical(self.task), before)

    def test_source_reuse_preserves_two_distinct_purposes_without_changing_raw_data(self):
        record = self.source(); before = {key:deepcopy(record[key]) for key in ('snapshot','hash','parameters','request_hash')}
        need = dict(id='valuation_check', selection_reason='与盈利观察对照，但不以价格上涨证明估值便宜。')
        record_request_purpose(record, need, record['question_ids'])
        record['data_need_ids'].append(need['id']); self.task['source_records'] = [record]
        rows = draft_research_context(self.task)['source_purposes']
        self.assertEqual([row['data_need_id'] for row in rows], ['price','valuation_check'])
        self.assertEqual([row['purpose'] for row in rows], [record['selection_reason'], need['selection_reason']])
        for key,value in before.items():self.assertEqual(record[key],value)
        record_request_purpose(record, need, record['question_ids'])
        self.assertEqual(len(record['request_purposes']),2)

    def test_legacy_merged_aliases_are_not_backfilled_with_guessed_pairings(self):
        record = self.source(); record['data_need_ids'] += ['old_alias']
        need = dict(id='new_need', selection_reason='按新问题检验同一价格序列。')
        record_request_purpose(record, need, record['question_ids'])
        record['data_need_ids'].append('new_need'); self.task['source_records'] = [record]
        entries = draft_research_context(self.task)['source_purposes']
        self.assertNotIn('data_need_id', entries[0])
        self.assertEqual(entries[1]['data_need_id'], 'new_need')

    def test_foreign_version_changed_snapshot_and_failed_source_do_not_supply_a_purpose(self):
        for change in ('task','version','snapshot','failure','input'):
            record = self.source()
            if change=='task':record['task_id']='another-task'
            elif change=='version':record['research_version']+=1
            elif change=='snapshot':record['snapshot']['tables'][0]['table']['close'][0]=999
            elif change=='failure':record['status']='业务失败待按官方错误定义核查'
            else:record['input_hash']='other-input'
            task = deepcopy(self.task); task['source_records']=[record]
            with self.subTest(change=change):self.assertEqual(draft_research_context(task)['source_purposes'],[])

    def test_foreign_question_or_request_identity_is_not_a_planning_link(self):
        for change in ('question','request'):
            record = self.source()
            record['request_purposes']=[dict(data_need_id='price',question_ids=record['question_ids'],selection_reason='保留用途。')]
            if change=='question':record['request_purposes'][0]['question_ids']=['foreign']
            else:record['request_purposes'][0]['data_need_id']='foreign'
            task=deepcopy(self.task);task['source_records']=[record]
            with self.subTest(change=change):self.assertFalse(draft_research_context(task)['source_purposes'])

    def test_account_policy_and_missing_purpose_are_not_business_arguments(self):
        account = snapshot_record(self.task, 'ifind', 'get_limit_policy', {}, dict(errorcode=0))
        account.update(selection_reason='检查账户能力。', question_ids=[self.task['questions'][0]['id']])
        quote = self.source(); quote.pop('selection_reason')
        self.task['source_records']=[account,quote]
        self.assertEqual(draft_research_context(self.task)['source_purposes'],[])

    def test_long_purpose_is_omitted_whole_without_losing_the_source_or_observations(self):
        record=self.source();record['selection_reason']='完整口径说明。'*2000
        self.task['source_records']=[record];before=canonical(self.task)
        context=draft_research_context(self.task,limit=1200)
        self.assertLessEqual(len(canonical(context)),1200)
        self.assertEqual(context['omitted_purposes'],1)
        self.assertEqual(context['source_purposes'],[])
        self.assertEqual(canonical(self.task),before)

    def test_plan_writer_and_skill_share_the_same_detached_context_with_only_the_two_existing_phases(self):
        self.task['source_records']=[self.source()];self.session.archive.save(self.task)
        calls=[]
        class Model:
            def reason(self, task, messages, **options):
                calls.append((options['phase'],json.loads(messages[1]['content'])))
                return '{"data_requests":[]}' if options['phase']=='draft_plan' else '{}'
        engine=SimpleNamespace(model=Model())
        before=canonical(self.task)
        _plan(engine,self.task,1);_write(engine,self.task,[])
        expected=self.session.tools(self.task['id'])['first_report']['research_context']
        self.assertEqual([phase for phase,_ in calls],['draft_plan','draft_report'])
        for _,payload in calls:self.assertEqual(payload['research_context'],expected)
        self.assertEqual(canonical(self.task),before)
        expected['source_purposes'].clear()
        self.assertTrue(draft_research_context(self.task)['source_purposes'])

    def test_one_returned_number_does_not_complete_a_multi_field_multi_object_request(self):
        from researchhelper.draft_sources import acquisition_coverage
        source=self.source();self.task['source_records']=[source]
        request=dict(id='price',source='ifind',operation='cmd_history_quotation',parameters=dict(source['parameters'],
            codes='600519.SH,000001.SZ',indicators='close,open'))
        before=canonical(self.task);coverage=acquisition_coverage(self.task,[request])
        self.assertTrue(coverage['needs_followup']);self.assertFalse(coverage['blocks_report'])
        missing={(r['code'],r['field']) for r in coverage['unresolved'][0]['missing']}
        self.assertEqual(missing,{('600519.SH','open'),('000001.SZ','close'),('000001.SZ','open')})
        self.assertEqual(canonical(self.task),before)

    def test_unknown_units_do_not_turn_successful_raw_acquisition_into_a_gap(self):
        from researchhelper.draft_sources import acquisition_coverage
        source=self.source();self.task['source_records']=[source]
        request=dict(id='price',source='ifind',operation=source['operation'],parameters=source['parameters'])
        coverage=acquisition_coverage(self.task,[request])
        self.assertEqual(coverage['complete_request_ids'],['price']);self.assertEqual(coverage['unresolved'],[])
        self.assertFalse(coverage['unit_approval']);self.assertFalse(coverage['financial_approval'])

    def test_later_compatible_cells_fill_a_gap_but_another_statement_cannot(self):
        from researchhelper.draft_sources import acquisition_coverage
        def record(ident,field,statement='1'):
            from tests.test_draft_financial_period import add_record,rehash
            fixture=dict(self.task,source_records=[])
            row=add_record(fixture,'20241231',[100],fields=[field])
            row['parameters']['indipara'][0]['indiparams'][1]=statement;rehash(row)
            row.update(id=ident,data_need_ids=[ident]);return row
        first=record('revenue','ths_revenue_stock');self.task['source_records']=[first]
        request=dict(id='revenue',source='ifind',operation='basic_data_service',parameters=dict(codes='600519.SH',
            report_period='20241231',statement_type='1',indicators=['ths_revenue_stock','ths_np_stock']))
        coverage=acquisition_coverage(self.task,[request]);self.assertTrue(coverage['needs_followup'])
        self.assertEqual(coverage['unresolved'][0]['missing'],[dict(code='600519.SH',field='ths_np_stock',period='20241231',statement='1')])
        self.task['source_records'].append(record('wrong_statement','ths_np_stock','2'))
        self.assertTrue(acquisition_coverage(self.task,[request])['needs_followup'])
        self.task['source_records'].append(record('profit','ths_np_stock'))
        self.assertEqual(acquisition_coverage(self.task,[request])['complete_request_ids'],['revenue'])

    def test_foreign_or_changed_response_does_not_satisfy_the_data_plan(self):
        from researchhelper.draft_sources import acquisition_coverage
        source=self.source();request=dict(id='price',source='ifind',operation=source['operation'],parameters=source['parameters'])
        source['task_id']='foreign';self.task['source_records']=[source]
        coverage=acquisition_coverage(self.task,[request])
        self.assertEqual(coverage['complete_request_ids'],[]);self.assertEqual(coverage['unresolved'][0]['reason'],'no_usable_response')

    def test_coverage_does_not_import_an_old_versions_unfulfilled_request(self):
        from researchhelper.draft_sources import acquisition_coverage
        source=self.source();self.task['source_records']=[source]
        request=dict(id='unfulfilled',source='ifind',operation=source['operation'],parameters=source['parameters'])
        binding=dict(task_id=self.task['id'],research_version=self.task['version'],input_hash=self.task['input_hash'])
        self.task['draft_source_attempts']=[dict(proposed=request,binding=dict(binding,research_version=99))]
        self.assertEqual(acquisition_coverage(self.task)['evaluated_requests'],0)
        self.task['draft_source_attempts']=[dict(proposed=request,binding=binding)]
        coverage=acquisition_coverage(self.task)
        self.assertEqual(coverage['evaluated_requests'],1)
        self.assertFalse(coverage['research_sufficiency_approved'])
        self.assertEqual(coverage['scope'],'recorded_requests_only')

    def test_host_can_prepare_planning_knowledge_without_model_or_provider_dispatch(self):
        prepared=self.session.draft(self.task['id'],defer_delivery=True,data_requests=[])
        self.assertEqual(prepared['ledger']['used']['model_calls'],0)
        self.assertEqual(prepared['ledger']['used']['data_requests'],0)
        contract=self.session.tools(self.task['id'])['first_report']
        self.assertIn('prepare',contract)
        from researchhelper.knowledge import ResearchKnowledge
        self.assertEqual(contract['knowledge_context'],ResearchKnowledge().draft_context(prepared))
        self.assertEqual(contract['acquisition_coverage']['evaluated_requests'],0)
        self.assertFalse(contract['acquisition_coverage']['research_sufficiency_approved'])

    def test_purpose_metadata_is_not_copied_into_public_research_prose(self):
        self.task['source_records']=[self.source()];self.session.archive.save(self.task)
        done=self.session.draft(self.task['id'],report=dict(title='研究样例',conclusion='保留判断。',
            logics=[dict(title='条件判断',analysis='需要结合经营事实评估。')]))
        self.assertTrue(done.get('delivery'))
        from lxml import html
        text=html.document_fromstring(self.session.archive.report(self.task['id'],1)['html']).xpath('//main')[0].text_content()
        self.assertNotIn(self.task['source_records'][0]['selection_reason'],text)
        self.assertNotIn('source_purposes',text)
        self.assertEqual(done['ledger']['used']['model_calls'],0)


class ActualAcquisitionHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):financial.DraftFirstWorkflowTests.setUpClass()
    @classmethod
    def tearDownClass(cls):financial.DraftFirstWorkflowTests.tearDownClass()
    def setUp(self):
        self.control=financial.DraftFirstWorkflowTests();self.addCleanup(self.control.doCleanups);self.control.setUp()
        self.agent=self.control.agent
    def test_engine_records_dispatched_purpose_and_writer_receives_the_saved_context(self):
        captured=[];base=self.control.model.reason
        def capture(task,messages,**options):
            captured.append((options['phase'],json.loads(messages[1]['content'])))
            return base(task,messages,**options)
        self.control.model.reason=capture
        task=self.agent.create(financial.QUESTION,research_mode='current')
        done=self.agent.start(task['id'])
        self.assertTrue(done.get('delivery'),done.get('error'))
        records=[row for row in done['source_records'] if row['operation']=='basic_data_service']
        self.assertEqual(len(records),2)
        for source in records:
            self.assertEqual(len(source['request_purposes']),1)
            self.assertEqual(source['request_purposes'][0]['data_need_id'],source['data_need_ids'][0])
            self.assertEqual(source['request_purposes'][0]['selection_reason'],'比较本期与上年同期。')
        planned=dict(captured)['draft_plan']['knowledge_context']
        self.assertTrue(planned['entries'])
        self.assertEqual(planned['binding']['task_id'],done['id'])
        locked={entry['id']:entry for entry in done['knowledge']['entries']}
        for entry in planned['entries']:
            self.assertEqual(entry['version'],locked[entry['id']]['version'])
            self.assertEqual(entry['conditions'],locked[entry['id']]['conditions'])
        context=dict(captured)['draft_report']['research_context']
        self.assertEqual({row['source_id'] for row in context['source_purposes']},{source['id'] for source in records})
        from researchhelper.archive import ResearchArchive
        saved = ResearchArchive(self.control.directory.name).get(done['id'])
        self.assertEqual(context,draft_research_context(saved))
        self.assertEqual(self.control.model.calls,['draft_plan','draft_report'])
        self.assertEqual(done['ledger']['used']['data_requests'],3)

    def partial_transport(self):
        base=self.agent.collector.external
        def transport(task,source,operation,params,credentials):
            if operation!='basic_data_service':return base(task,source,operation,params,credentials)
            # Shape the fake supplier response before actual source admission,
            # so no test changes a failed status or bypasses validation.
            key=operation+':'+financial.period(params)
            responses=self.control.responses
            response=deepcopy(responses[key])
            requested={item['indicator'] for item in params['indipara']}
            for table in response.get('tables',[]):
                columns=table.get('table',{})
                for field in list(columns):
                    if field not in requested or (financial.period(params)=='20260630' and len(requested)>1 and field=='ths_operating_cost_stock'):
                        del columns[field]
            if isinstance(response.get('datatype'),list):
                response['datatype']=[item for item in response['datatype'] if item.get('itemid') in requested]
            if isinstance(response.get('inputParams'),dict) and isinstance(response['inputParams'].get('params'),list):
                response['inputParams']['params']=[item for item in response['inputParams']['params'] if item.get('function') in requested]
            response['dataVol']=len(requested)
            self.control.responses=dict(responses,**{key:response})
            try:return base(task,source,operation,params,credentials)
            finally:self.control.responses=responses
        self.agent.collector.external=transport

    def test_partial_success_replans_only_the_missing_field_before_writing(self):
        self.partial_transport();base=self.control.model.reason;planned=[];writing=[]
        def respond(task,messages,**options):
            result=base(task,messages,**options);payload=json.loads(messages[1]['content'])
            if options['phase']=='draft_plan':
                planned.append(payload)
                if len(planned)==2:
                    missing=payload['acquisition_coverage']['unresolved'][0]['missing']
                    self.assertEqual([row['field'] for row in missing],['ths_operating_cost_stock'])
                    request=dict(id='missing_operating_cost',source='ifind',operation='basic_data_service',
                        parameters=dict(codes='002594.SZ',report_period='20260630',statement_type='1',indicators=['ths_operating_cost_stock']),
                        question_ids=[task['questions'][0]['id']],selection_reason='补齐已规划的营业成本，用于区分收入和成本的盈利传导。')
                    return json.dumps(dict(data_requests=[request]),ensure_ascii=False)
            else:writing.append(payload)
            return result
        self.control.model.reason=respond
        task=self.agent.create(financial.QUESTION,research_mode='current',limits=dict(model_calls=3,data_requests=12,seconds=180))
        done=self.agent.start(task['id']);self.assertTrue(done.get('delivery'),done.get('error'))
        self.assertEqual(self.control.model.calls,['draft_plan','draft_plan','draft_report'])
        self.assertEqual(done['ledger']['used']['data_requests'],4)
        self.assertEqual(len(done['draft_report']['facts']),8)
        self.assertTrue(any(row['method']=='raw_change_v1' and set(row['data_refs'])=={'D5','D8'}
            for row in done['draft_report']['calculations']))
        self.assertEqual(writing[0]['data']['acquisition_coverage']['unresolved'],[])
        self.assertFalse(writing[0]['data']['acquisition_coverage']['blocks_report'])

    def test_limited_model_budget_still_saves_a_partial_report(self):
        self.partial_transport()
        task=self.agent.create(financial.QUESTION,research_mode='current',limits=dict(model_calls=2,data_requests=12,seconds=180))
        done=self.agent.start(task['id']);self.assertTrue(done.get('delivery'),done.get('error'))
        self.assertEqual(self.control.model.calls,['draft_plan','draft_report'])
        self.assertEqual(done['ledger']['used']['model_calls'],2)
        self.assertEqual(done['ledger']['used']['data_requests'],3)
        self.assertTrue(done['draft_acquisition_coverage']['unresolved'])
        self.assertEqual(len(done['draft_report']['facts']),7)

    def test_actual_cached_request_retains_both_reasons_without_another_dispatch(self):
        base=self.control.model.reason
        def with_reuse(task,messages,**options):
            result=base(task,messages,**options)
            if options['phase']=='draft_plan':
                plan=json.loads(result)
                request=deepcopy(plan['data_requests'][0])
                request.update(id='reuse_financial',selection_reason='复用相同字段，观察现金和利润的分化。')
                plan['data_requests'].append(request)
                return json.dumps(plan,ensure_ascii=False)
            return result
        self.control.model.reason=with_reuse
        task=self.agent.create(financial.QUESTION,research_mode='current')
        done=self.agent.start(task['id'])
        self.assertTrue(done.get('delivery'),done.get('error'))
        source=next(row for row in done['source_records'] if 'reuse_financial' in row.get('data_need_ids',[]))
        self.assertEqual(done['ledger']['used']['data_requests'],3)
        self.assertEqual(self.control.model.calls,['draft_plan','draft_report'])
        self.assertEqual([row['data_need_id'] for row in source['request_purposes']],['financial_20260630','reuse_financial'])
        purposes=draft_research_context(done)['source_purposes']
        self.assertEqual([row['data_need_id'] for row in purposes if row['source_id']==source['id']],
                         ['financial_20260630','reuse_financial'])


if __name__=='__main__':unittest.main()

def test_strategy_valuation_request_with_anniversary_uses_two_valid_provider_windows():
    from researchhelper.draft_workflow import _acquire
    from researchhelper.engine import ResearchAgent
    from researchhelper.archive import ResearchArchive
    from researchhelper.market_series import series_settings, validate_series_request

    with TemporaryDirectory(prefix='rh-valuation-anniversary-') as temp:
        agent = ResearchAgent(archive=ResearchArchive(temp), draft_first=True)
        task = agent._prepare_task('研究华荣股份603855.SH过去一年估值，写两条策略逻辑。')
        request = dict(id='annual_pe', source='ifind', operation='date_sequence',
            question_ids=[task['questions'][0]['id']], selection_reason='核对一年PE区间。',
            parameters=dict(codes='603855.SH', startdate='2025-11-24', enddate='2026-11-24',
                functionpara=series_settings(task, '603855.SH'),
                indipara=[dict(indicator='ths_pe_ttm_stock', indiparams=['', '100'])]))
        if task['cutoff'] < '2026-11-25':
            task['cutoff'] = '2026-11-25T00:00:00+00:00'
        with patch.object(agent, '_check_run_control'), patch.object(agent, '_acquire_sources') as dispatch,                 patch('researchhelper.draft_workflow._checkpoint'):
            _acquire(agent, task, [request])
        sent = [call.args[1][0] for call in dispatch.call_args_list]
        assert len(sent) == 2 and sent[0]['id'] != sent[1]['id']
        assert [(item['parameters']['startdate'], item['parameters']['enddate']) for item in sent] == [
            ('2026-05-26', '2026-11-24'), ('2025-11-24', '2026-05-25')]
        for item in sent:
            assert item['question_ids'] == request['question_ids']
            assert item['parameters']['indipara'] == request['parameters']['indipara']
            validate_series_request(task, 'date_sequence', item['parameters'])
        attempts = task['draft_source_attempts']
        assert all(trace['proposed'] == request for trace in attempts)
        agent.close_runtime()
