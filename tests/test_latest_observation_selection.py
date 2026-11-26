"""Explicit latest/range intent survives compilation, source reuse and reading."""
from pathlib import Path
from copy import deepcopy
import json,socket,tempfile,unittest
from unittest.mock import patch
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError,canonical,fingerprint
from researchhelper.evidence import data_request_contract,snapshot_record,record_request_purpose
from researchhelper.market_series import compile_observation_window,observation_selection,validate_observation_scope
from researchhelper.output_schema import acquisition_output_schema,decode_acquisition_output,provider_request_schema,ACQUISITION_PROTOCOL
from researchhelper.draft_sources import observations,writing_context,source_issues,source_context
from researchhelper.draft_analysis import analyze
from researchhelper.draft_workflow import _serialize_request
from researchhelper.drafts import draft_figures
from researchhelper.planning import input_fingerprint


class LatestObservationSelectionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)
        self.agent=ResearchAgent(ResearchArchive(tmp.name),ROOT,offline=True,draft_first=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task=self.agent.create('比较2024、2025年财务，并取得最近价格与估值',objects=['600519.SH'],cutoff='2026-11-03T06:00:00+08:00')

    def request(self,operation='cmd_history_quotation'):
        p=dict(codes='600519.SH',functionpara=dict(Fill='Blank') if operation=='date_sequence' else dict(CPS='1',Currency='RMB',Fill='Omit'),
            observation_window=dict(mode='latest_available',as_of='2026-11-03',lookback_calendar_days=14))
        if operation=='date_sequence':p['indipara']=[dict(indicator='ths_pe_ttm_stock')]
        else:p['indicators']='close'
        return dict(id='latest',source='ifind',operation=operation,parameters=p,question_ids=['q1'],selection_reason='取得本次最近观测，不将财务年度用作行情起点。')

    def decode(self,request):
        desc=dict(data_request_contract=data_request_contract(self.task))
        schema=acquisition_output_schema(self.task,desc)
        wire=dict(version=1,binding_ref=schema['properties']['binding_ref']['enum'][0],data_requests=[request],stop_reason='')
        return decode_acquisition_output(wire,schema,desc)['data_requests'][0]

    def source(self,operation='cmd_history_quotation',values=None,dates=None):
        request=self.decode(self.request(operation));params=request['parameters']
        field='ths_pe_ttm_stock' if operation=='date_sequence' else 'close'
        payload=dict(errorcode=0,tables=[dict(thscode='600519.SH',time=dates or ['2026-10-30','2026-11-02','2026-11-03'],
            table={field:values if values is not None else [100,110,None]})])
        record=snapshot_record(self.task,'ifind',operation,params,payload)
        record.update(data_need_ids=[request['id']],question_ids=['q1'])
        record_request_purpose(record,request,['q1']);self.task['source_records'].append(record)
        return record,request

    def test_latest_price_and_pe_compile_without_financial_year_dates(self):
        for operation in ('cmd_history_quotation','date_sequence'):
            request=self.request(operation);original=deepcopy(request);value=self.decode(request)
            self.assertEqual(value['parameters']['startdate'],'2026-10-21')
            self.assertEqual(value['parameters']['enddate'],'2026-11-03')
            self.assertNotIn('observation_window',value['parameters'])
            self.assertEqual(value['observation_scope']['mode'],'latest_available')
            validate_observation_scope(self.task,value);self.agent._validated_data_needs(self.task,[value])
            if operation=='date_sequence':self.assertEqual(value['parameters']['indipara'][0]['indiparams'],['','100'])
            self.assertEqual(request,original)

    def test_host_selector_compiles_through_the_same_preflight(self):
        request=self.request();compiled=_serialize_request(request,self.task)
        self.assertEqual(compiled,self.decode(request))
        self.agent._validated_data_needs(self.task,[compiled])

    def test_explicit_range_remains_unchanged(self):
        request=self.request();request['parameters'].pop('observation_window')
        request['parameters'].update(startdate='2025-01-01',enddate='2026-11-02')
        self.assertEqual(self.decode(request),request)
        self.assertEqual(_serialize_request(request,self.task),request)

    def test_latest_keeps_last_available_row_and_original_snapshot(self):
        record,_=self.source();before=canonical(record)
        facts=observations(self.task)
        self.assertEqual([(r['period'],r['value']) for r in facts],[('2026-11-02',110)])
        self.assertEqual(canonical(record),before)
        self.assertEqual(source_issues(self.task),[])
        presented=writing_context(self.task,facts)['observations']
        self.assertEqual([(row['period'],row['rows']) for row in presented],[('2026-11-02',[['D1',110]])])

    def test_latest_close_reuses_only_the_exact_prior_close_for_arithmetic(self):
        record, _ = self.source(values=[1330, 1340, 1335, None],
            dates=['2026-10-27', '2026-10-28', '2026-10-29', '2026-11-03'])
        # Use consecutive actual observations even when the latest nonempty
        # row precedes the cutoff. Calendar-backed dates are tested separately.
        before = canonical(record)
        facts = observations(self.task)
        self.assertEqual([(row['id'], row['period'], row['value'], row.get('previous_close_for')) for row in facts],
            [('D1', '2026-10-29', 1335, None), ('D2', '2026-10-28', 1340, '2026-10-29')])
        self.assertEqual(facts[1]['locator'], dict(table=0, field='close', row=1))
        results = analyze(self.task, facts)['results']
        self.assertEqual([(row['comparison_kind'], row['data_refs'], row['display']) for row in results],
            [('previous_session', ['D2', 'D1'], '-0.37')])
        projected = json.loads(source_context(self.task)[0]['content'])['tables']
        self.assertEqual(projected, [dict(thscode='600519.SH',time=['2026-10-29'],table=dict(close=[1335]))])
        self.assertEqual(writing_context(self.task, facts)['observations'][0]['rows'], [['D1', 1335]])
        self.assertEqual(draft_figures(facts), [])
        self.assertEqual(canonical(record), before)
        self.assertEqual(source_issues(self.task), [])

    def test_latest_close_does_not_substitute_missing_conflicting_or_unconfirmed_predecessor(self):
        for days, values in [
                (['2026-10-30', '2026-11-02', '2026-11-03'], [100, None, 110]),
                (['2026-11-02', '2026-11-02', '2026-11-03'], [100, 101, 110]),
                (['2026-10-29', '2026-10-30', '2026-11-02'], [100, 105, 110]),
                (['2026-11-02', '2026-11-03'], [0, 110])]:
            with self.subTest(days=days, values=values):
                self.task['source_records'] = []
                self.source(values=values, dates=days)
                facts = observations(self.task)
                self.assertEqual([row['value'] for row in facts], [110])
                self.assertEqual(analyze(self.task, facts)['results'], [])

    def test_v2_tasks_keep_their_one_point_projection(self):
        self.task['market_observation_selection_version'] = 2
        self.source(values=[1340, 1335], dates=['2026-11-02', '2026-11-03'])
        facts = observations(self.task)
        self.assertEqual([row['value'] for row in facts], [1335])
        self.assertEqual(analyze(self.task, facts)['results'], [])

    def test_latest_close_conflicting_predecessor_from_an_overlapping_response(self):
        self.source(values=[1340, 1335], dates=['2026-11-02', '2026-11-03'])
        self.source(values=[1341, 1335], dates=['2026-11-02', '2026-11-03'])
        facts = observations(self.task)
        self.assertEqual([row['value'] for row in facts], [1335, 1335, 1340, 1341])
        self.assertEqual(analyze(self.task, facts)['results'], [])

    def test_latest_open_and_valuation_fields_still_use_single_points(self):
        need = self.request()
        need['parameters']['indicators'] = 'open,close'
        request = self.decode(need)
        record = snapshot_record(self.task, 'ifind', request['operation'], request['parameters'], dict(errorcode=0,
            tables=[dict(thscode='600519.SH',time=['2026-11-02', '2026-11-03'],
                         table=dict(open=[1320, 1345],close=[1340, 1335]))]))
        record.update(data_need_ids=[request['id']], question_ids=['q1'])
        record_request_purpose(record, request, ['q1'])
        self.task['source_records'].append(record)
        self.source('date_sequence',values=[20,21],dates=['2026-11-02','2026-11-03'])
        self.assertEqual([(row['field'], row['value']) for row in observations(self.task)],
            [('open', 1345), ('close', 1335), ('ths_pe_ttm_stock', 21), ('close', 1340)])

    def multiple_fields(self,tables):
        request=self.request('date_sequence')
        request['parameters']['indipara'].append(dict(indicator='ths_pb_mrq_stock'))
        request=self.decode(request)
        record=snapshot_record(self.task,'ifind','date_sequence',request['parameters'],dict(errorcode=0,tables=tables))
        record.update(data_need_ids=[request['id']],question_ids=['q1'])
        record_request_purpose(record,request,['q1']);self.task['source_records'].append(record)
        return record

    def test_each_field_keeps_its_own_latest_available_date(self):
        record=self.multiple_fields([dict(thscode='600519.SH',time=['2026-10-30','2026-11-02','2026-11-03'],
            table=dict(ths_pe_ttm_stock=[20,21,None],ths_pb_mrq_stock=[5,None,6]))])
        before=canonical(record);facts=observations(self.task)
        self.assertEqual([(r['field'],r['period'],r['value'],r['locator']['row']) for r in facts],
            [('ths_pe_ttm_stock','2026-11-02',21,1),('ths_pb_mrq_stock','2026-11-03',6,2)])
        projected=json.loads(source_context(self.task)[0]['content'])['tables']
        self.assertEqual(projected,[dict(thscode='600519.SH',time=['2026-11-02'],table=dict(ths_pe_ttm_stock=[21])),
            dict(thscode='600519.SH',time=['2026-11-03'],table=dict(ths_pb_mrq_stock=[6]))])
        written=writing_context(self.task,facts)['observations']
        self.assertIn('2026-11-02',canonical(written));self.assertIn('2026-11-03',canonical(written))
        self.assertNotIn('2026-10-30',canonical(written))
        self.assertEqual(source_issues(self.task),[]);self.assertEqual(canonical(record),before)

    def test_zero_and_fully_missing_fields_keep_independent_dates(self):
        self.multiple_fields([dict(thscode='600519.SH',time=['2026-10-30','2026-11-02','2026-11-03'],
            table=dict(ths_pe_ttm_stock=[10,0,None],ths_pb_mrq_stock=[None,None,None]))])
        self.assertEqual([(r['field'],r['period'],r['value']) for r in observations(self.task)],
            [('ths_pe_ttm_stock','2026-11-02',0),('ths_pb_mrq_stock','2026-11-03',None)])
        self.assertEqual(source_issues(self.task),[])

    def test_same_date_fields_share_a_projection_without_extra_cells(self):
        self.multiple_fields([dict(thscode='600519.SH',time=['2026-11-02','2026-11-03'],
            table=dict(ths_pe_ttm_stock=[20,21],ths_pb_mrq_stock=[5,6]))])
        self.assertEqual(json.loads(source_context(self.task)[0]['content'])['tables'],
            [dict(thscode='600519.SH',time=['2026-11-03'],table=dict(ths_pe_ttm_stock=[21],ths_pb_mrq_stock=[6]))])

    def test_cross_table_ambiguity_excludes_only_the_affected_latest_field(self):
        record=self.multiple_fields([dict(thscode='600519.SH',time=['2026-11-02','2026-11-03'],
            table=dict(ths_pe_ttm_stock=[20,21],ths_pb_mrq_stock=[5,None])),
            dict(thscode='600519.SH',time=['2026-11-03'],table=dict(ths_pe_ttm_stock=[22]))])
        before=canonical(record)
        self.assertEqual([(r['field'],r['period'],r['value']) for r in observations(self.task)],
            [('ths_pb_mrq_stock','2026-11-02',5)])
        self.assertTrue(source_issues(self.task)[0]['partial']);self.assertEqual(canonical(record),before)

    def test_different_objects_are_selected_independently(self):
        self.task['objects'].append('600036.SH')
        request=self.request();request['parameters'].update(codes='600519.SH,600036.SH',indicators='close,open')
        request=self.decode(request)
        record=snapshot_record(self.task,'ifind','cmd_history_quotation',request['parameters'],dict(errorcode=0,tables=[
            dict(time=['2026-11-02','2026-11-03','2026-11-02','2026-11-03'],
                table=dict(thscode=['600519.SH','600519.SH','600036.SH','600036.SH'],
                    close=[20,None,None,10],open=[None,6,1,None]))]))
        record.update(data_need_ids=[request['id']],question_ids=['q1'])
        record_request_purpose(record,request,['q1']);self.task['source_records'].append(record)
        self.assertEqual({(r['code'],r['field'],r['period'],r['value']) for r in observations(self.task)},
            {('600519.SH','close','2026-11-02',20),('600519.SH','open','2026-11-03',6),
             ('600036.SH','close','2026-11-03',10),('600036.SH','open','2026-11-02',1)})

    def test_writer_excludes_other_fields_at_the_selected_union_of_dates(self):
        self.multiple_fields([dict(thscode='600519.SH',time=['2026-11-02','2026-11-03'],
            table=dict(ths_pe_ttm_stock=[21,None],ths_pb_mrq_stock=[None,6]))])
        unrestricted=deepcopy(self.task);unrestricted.pop('market_observation_selection_version')
        earlier_facts=observations(unrestricted)
        context=writing_context(self.task,earlier_facts)
        self.assertEqual(sum(len(series['rows']) for series in context['observations']),2)
        self.assertEqual({series['field']:(series['period'],series['rows'][0][series['columns'].index('value')])
            for series in context['observations']},
            dict(ths_pe_ttm_stock=('2026-11-02',21),ths_pb_mrq_stock=('2026-11-03',6)))

    def test_legacy_pending_selection_is_not_silently_migrated(self):
        self.task['market_observation_selection_version']=1
        self.multiple_fields([dict(thscode='600519.SH',time=['2026-11-02','2026-11-03'],
            table=dict(ths_pe_ttm_stock=[21,None],ths_pb_mrq_stock=[None,6]))])
        self.assertEqual([(r['field'],r['period'],r['value']) for r in observations(self.task)],
            [('ths_pe_ttm_stock','2026-11-03',None),('ths_pb_mrq_stock','2026-11-03',6)])
        self.assertNotIn('select_per',observation_selection(self.task))

    def test_invalid_empty_tables_do_not_discard_independent_fields(self):
        for malformed in (dict(thscode='600519.SH',table=dict(ths_pe_ttm_stock=[])),
                          dict(thscode='600519.SH',time=None,table=dict(ths_pe_ttm_stock=[])),
                          dict(thscode='600519.SH',time=['2026-11-03'],table=None)):
            with self.subTest(malformed=malformed):
                self.task['source_records']=[]
                self.multiple_fields([malformed,dict(thscode='600519.SH',time=['2026-11-03'],
                    table=dict(ths_pe_ttm_stock=[21],ths_pb_mrq_stock=[6]))])
                self.assertEqual([r['value'] for r in observations(self.task)],[21,6])

    def test_intentional_projection_retires_stale_notice_without_losing_history(self):
        record,_=self.source()
        self.task['draft_notices']=[dict(stage='来源读取',artifact_id=record['id'],message='旧的范围异常提示',time='原记录时间')]
        self.agent.archive.save(self.task)
        done=self.agent.start(self.task['id'],dict(data_requests=[],draft_report=None),defer_delivery=True)
        self.assertFalse(any(row['stage']=='来源读取' for row in done.get('draft_notices',[])))
        self.assertEqual(done['draft_notice_history'][0]['message'],'旧的范围异常提示')
        self.assertFalse(done['draft_notice_history'][0]['financial_approval'])
        self.assertEqual(done['ledger']['used']['model_calls'],0)

    def test_actual_scope_error_still_keeps_its_active_notice(self):
        record,_=self.source(values=[100,110,999],dates=['2026-10-30','2026-11-02','2026-11-04'])
        self.assertTrue(source_issues(self.task))
        self.agent.archive.save(self.task)
        done=self.agent.start(self.task['id'],dict(data_requests=[],draft_report=None),defer_delivery=True)
        self.assertTrue(any(row['stage']=='来源读取' for row in done['draft_notices']))

    def test_missing_quote_stays_missing_and_zero_is_available(self):
        for values,expected in [([None,None,None],('2026-11-03',None)),([100,0,None],('2026-11-02',0))]:
            self.task['source_records']=[];self.source(values=values)
            self.assertEqual([(r['period'],r['value']) for r in observations(self.task)],[expected])

    def test_future_and_outside_rows_never_reenter_latest(self):
        self.source(values=[9,100,999],dates=['2025-01-01','2026-11-02','2026-11-04'])
        self.assertEqual([r['value'] for r in observations(self.task)],[100])

    def test_duplicate_last_dates_are_not_arbitrarily_selected(self):
        self.source(values=[100,110,111],dates=['2026-10-30','2026-11-02','2026-11-02'])
        # Do not silently substitute the older observation for an ambiguous latest date.
        self.assertEqual(observations(self.task),[])

    def test_source_reused_for_explicit_range_keeps_that_range_available(self):
        record,_=self.source();record['data_need_ids'].append('range')
        self.assertEqual([r['value'] for r in observations(self.task)],[100,110,None])

    def test_invalid_scope_is_local_and_does_not_change_original_data(self):
        record,_=self.source();before=fingerprint(record['snapshot'])
        record['observation_requests'][0]['scope']['as_of']='2026-11-04'
        self.assertEqual(observations(self.task),[])
        self.assertTrue(any('观测范围' in r['reason'] for r in source_issues(self.task)))
        self.assertEqual(fingerprint(record['snapshot']),before)

    def test_future_scope_invalid_days_and_mixed_dates_do_not_dispatch(self):
        for mode in ('future','days','mixed','foreign_operation'):
            request=self.request()
            if mode=='future':request['parameters']['observation_window']['as_of']='2026-11-04'
            elif mode=='days':request['parameters']['observation_window']['lookback_calendar_days']=365
            elif mode=='mixed':request['parameters']['startdate']='2025-01-01'
            else:request['operation']='basic_data_service'
            with self.subTest(mode=mode),self.assertRaises(ContractError):self.decode(request)
        self.assertEqual(self.task['ledger']['used']['data_requests'],0)

    def test_legacy_task_keeps_plain_dates_and_original_reading(self):
        record,request=self.source();self.task.pop('market_observation_selection_version')
        self.assertEqual([r['value'] for r in observations(self.task)],[100,110,None])
        with self.assertRaises(ContractError):_serialize_request(self.request(),self.task)

    def test_explicit_update_reuses_original_without_calling_old_view_current_latest(self):
        record,request=self.source();self.agent.archive.save(self.task)
        first=self.agent.start(self.task['id'],dict(data_requests=[],draft_report=dict(title='原版',conclusion='保留实际观测。')))
        frozen=self.agent.archive.report(first['id'],1)['html']
        updated=self.agent.prepare_update(first['id'],cutoff='2026-11-03T23:59:59.999999+08:00')
        self.assertEqual(updated['research_mode'],'historical')
        self.assertEqual([r['value'] for r in observations(updated)],[100,110,None])
        self.assertFalse(any(not r.get('partial') for r in source_issues(updated)))
        copied=updated['source_records'][0]
        record_request_purpose(copied,request,['q1'])
        self.assertEqual([r['value'] for r in observations(updated)],[110])
        self.assertEqual(self.agent.archive.report(first['id'],1)['html'],frozen)
        self.assertEqual(updated['ledger']['used']['data_requests'],0)

    def test_scope_policy_is_bound_in_task_input_and_not_adopted_on_reopen(self):
        original=input_fingerprint(self.task);self.task.pop('market_observation_selection_version')
        self.assertNotEqual(input_fingerprint(self.task),original)
        self.agent.archive.save(self.task)
        self.assertNotIn('market_observation_selection_version',self.agent.archive.get(self.task['id']))

    def test_provider_schema_exposes_explicit_latest_and_range_branches(self):
        desc=dict(data_request_contract=data_request_contract(self.task));schema=acquisition_output_schema(self.task,desc)
        wire=provider_request_schema(self.task,schema,ACQUISITION_PROTOCOL)
        self.assertIn('observation_window',canonical(wire));self.assertIn('startdate',canonical(wire))
        self.assertEqual(self.task['ledger']['used']['model_calls'],0)


if __name__=='__main__':unittest.main()
