"""Tinyshare transport, raw observations and first-report handoff; no real calls."""
import contextlib
import copy
import json
from pathlib import Path
import socket
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import fingerprint
from researchhelper.draft_sources import observations, source_context, writing_context, acquisition_coverage, source_issues
from researchhelper.draft_analysis import analyze
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import snapshot_record
from researchhelper.source_catalogue import source_catalogue
from researchhelper.source_transport import _source_worker
from researchhelper.planning import input_fingerprint

POLICY = 'draft_tinyshare_reader_version'
CODE = '600519.SH'
KEYS = ['ts_code','ann_date','f_ann_date','end_date','report_type','comp_type','update_flag']


def parameters(operation='daily', period='20251231', fields=None):
    if operation in ('income','cashflow'):
        fields = fields or (['revenue','oper_cost','n_income'] if operation == 'income' else ['n_cashflow_act'])
        return dict(ts_code=CODE, start_date='20260101',end_date='20261030',period=period,
            report_type='1',fields=','.join(KEYS+fields))
    return dict(ts_code=CODE,start_date='20261029',end_date='20261030',
        fields=','.join(['ts_code','trade_date']+(fields or ['close','amount','vol'])))


def market_rows():
    return [dict(ts_code=CODE,trade_date='20261029',close='100.125',amount=12000,vol=300),
            dict(ts_code=CODE,trade_date='20261030',close='110.1375',amount=15000,vol=400)]


def financial_row(period='20251231', **values):
    return dict(ts_code=CODE,ann_date='20260415',f_ann_date='20260415',end_date=period,
        report_type='1',comp_type='1',update_flag='1',**(values or dict(revenue=120,oper_cost=60,n_income=12)))


class TinyshareDraftHandoffTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='tinyshare-handoff-',dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        network = patch.object(socket.socket,'connect',side_effect=AssertionError('No real network'))
        network.start();self.addCleanup(network.stop)
        self.archive = ResearchArchive(Path(folder.name)/'store')
        self.agent = ResearchAgent(self.archive,ROOT,offline=True,draft_first=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('比较600519.SH的已取得行情和财务，生成报告',entry='个股',
            objects=[CODE],research_mode='current')
        self.task[POLICY] = 1

    def source(self,operation='daily',rows=None,params=None):
        params = params or parameters(operation)
        rows = (market_rows() if operation not in ('income','cashflow') else [financial_row()]) if rows is None else rows
        record = snapshot_record(self.task,'tinyshare',operation,params,dict(code=0,data=copy.deepcopy(rows)))
        record.update(data_need_ids=['source_request'],question_ids=['q1'])
        self.task['source_records'].append(record)
        return record

    def test_market_rows_reach_facts_with_literal_units_without_scaling(self):
        record = self.source();before=fingerprint(self.task)
        facts = observations(self.task)
        self.assertEqual(len(facts),6)
        self.assertEqual([row['field'] for row in facts],['close','amount','vol']*2)
        self.assertEqual([row['unit'] for row in facts],['元','千元','手']*2)
        self.assertEqual([row['raw_value'] for row in facts],['100.125',12000,300,'110.1375',15000,400])
        self.assertEqual(facts[0]['locator'],dict(row=0,field='close'))
        self.assertTrue(all(row['artifact_id']==record['id'] for row in facts))
        self.assertEqual(fingerprint(self.task),before)

    def test_linked_close_projection_uses_dated_question_and_retains_raw_row_positions(self):
        self.task = self.agent.create('核对600519.SH从2026-10-28至2026-10-29的收盘价。', research_mode='current')
        rows = [dict(ts_code=CODE,trade_date='20261028',close='98.50'),
                dict(ts_code=CODE,trade_date='20261029',close='100.125'),
                dict(ts_code=CODE,trade_date='20261030',close='110.1375')]
        record = self.source(rows=rows, params=dict(parameters(fields=['close']),start_date='20261028'))
        facts = observations(self.task)
        self.assertEqual([row['period'] for row in facts], ['20261028','20261029'])
        self.assertEqual([row['locator']['row'] for row in facts], [0,1])
        self.assertEqual(json.loads(source_context(self.task)[0]['content'])['data'], rows[:2])
        self.assertEqual(record['snapshot']['data'], rows)

    def test_financial_route_lists_the_actual_fixed_ifind_api(self):
        family=next(row for row in source_catalogue(self.task)['families'] if row['id']=='financial')
        route=next(row for row in family['routes'] if row['source']=='ifind')
        self.assertIn('basic_data_service',route['operations'])

    def test_registered_financial_operation_is_not_rejected_before_sdk(self):
        sent=[]
        connection=SimpleNamespace(recv=lambda:dict(kind='tinyshare',operation='income',parameters=parameters('income'),token='synthetic-test'),
            send=lambda message:sent.append(message),close=lambda:None)
        sdk=SimpleNamespace(pro_api=lambda *a,**k:SimpleNamespace(income=lambda **kw:
            SimpleNamespace(to_dict=lambda **options:[financial_row()])))
        def wire(connection,deadline,record):
            record['sdk_wire_results']=[]
            return contextlib.nullcontext()
        with patch.dict('os.environ',{},clear=False), patch.dict('sys.modules',{'tinyshare':sdk}), patch('researchhelper.source_transport._sdk_wire_scope',side_effect=wire):
            _source_worker(connection,time.monotonic()+10)
        final=sent[-1]
        self.assertEqual(final['kind'],'response',final)
        self.assertEqual(final['body'],[financial_row()])

    def test_successful_financial_rows_keep_actual_fields_and_report_period(self):
        record = self.source('income')
        facts = observations(self.task)
        self.assertEqual([r['field'] for r in facts],['revenue','oper_cost','n_income'])
        self.assertEqual({r['period'] for r in facts},{'20251231'})
        self.assertEqual({r['period_basis'] for r in facts},{'来源end_date报告期'})
        self.assertEqual({r['unit'] for r in facts},{'原始单位待核对'})
        self.assertFalse(any('unit_reference' in r for r in facts))
        self.assertEqual(record['snapshot']['data'][0]['ann_date'],'20260415')
        self.assertEqual(len(analyze(self.task,facts)['results']),2)

    def test_market_change_and_financial_yoy_are_calculated_from_raw_cells(self):
        self.source()
        prior=parameters('income',period='20241231');prior['start_date']='20250101'
        row=financial_row('20241231',revenue=100,oper_cost=50,n_income=10)
        row.update(ann_date='20250415',f_ann_date='20250415')
        self.source('income',rows=[row],params=prior)
        self.source('income')
        facts=observations(self.task);before=fingerprint(facts)
        result=analyze(self.task,facts)
        price=next(r for r in result['results'] if r['label']=='收盘价变化')
        self.assertEqual(price['display'],'10.00')
        growth=[r for r in result['results'] if r['method']=='raw_change_v1' and r['period']=='20251231']
        self.assertEqual(len(growth),3)
        self.assertTrue(all(r['display']=='20.00' and r['conditional_equal_scale'] for r in growth))
        self.assertTrue(all(not r['financial_approval'] for r in result['results']))
        self.assertEqual(fingerprint(facts),before)

    def test_foreign_future_and_unrequested_cells_do_not_reach_writer(self):
        rows=market_rows()
        rows[0]['forecast_not_requested']=987654321
        rows += [dict(rows[0],ts_code='000001.SZ',close=876543210),
                 dict(rows[0],trade_date='20991231',close=765432109)]
        record=self.source(rows=rows);before=fingerprint(record)
        facts=observations(self.task)
        self.assertEqual(len(facts),6)
        for data in (source_context(self.task),writing_context(self.task,facts)):
            text=json.dumps(data)
            self.assertFalse(any(token in text for token in ('987654321','876543210','765432109','forecast_not_requested')))
        self.assertTrue(source_issues(self.task)[0]['partial'])
        self.assertEqual(fingerprint(record),before)

    def test_duplicate_market_dates_are_not_arbitrarily_selected(self):
        rows=market_rows();rows.append(dict(rows[0],close=999))
        self.source(rows=rows)
        facts=observations(self.task)
        self.assertEqual(len(facts),3)
        self.assertEqual({r['period'] for r in facts},{'20261030'})
        self.assertEqual(analyze(self.task,facts)['results'],[])

    def test_missing_values_remain_missing_and_zero_remains_zero(self):
        rows=market_rows();rows[0].update(close=None,amount=0,vol=None)
        self.source(rows=rows);facts=observations(self.task)
        self.assertIsNone(facts[0]['value']);self.assertIsNone(facts[0]['raw_value'])
        self.assertEqual((facts[1]['value'],facts[1]['unit']),(0,'千元'))
        self.assertIsNone(facts[2]['value'])
        result=analyze(self.task,facts)
        self.assertFalse(any(r['method']=='raw_change_v1' for r in result['results']))

    def test_business_error_cannot_supply_numbers_despite_a_success_label(self):
        record=self.source();record['snapshot']['code']=500
        record['hash']=fingerprint(record['snapshot'])
        self.assertEqual(observations(self.task),[])
        self.assertTrue(source_issues(self.task))

    def test_owned_task_version_and_hash_are_required(self):
        self.source();original=copy.deepcopy(self.task)
        for change in ('task','version','hash'):
            self.task=copy.deepcopy(original);record=self.task['source_records'][0]
            if change=='task':record['task_id']='different'
            elif change=='version':record['research_version']+=1
            else:record['hash']='different'
            with self.subTest(change=change):self.assertEqual(observations(self.task),[])

    def test_financial_versions_are_not_silently_selected_or_blended(self):
        self.source('income',rows=[financial_row(),financial_row(revenue=130,oper_cost=60,n_income=12)])
        facts=observations(self.task)
        self.assertEqual(len(facts),6)
        self.assertEqual([r['raw_value'] for r in facts if r['field']=='revenue'],[120,130])
        self.assertFalse(any(r['method']=='raw_margin_v1' for r in analyze(self.task,facts)['results']))

    def test_wrong_period_financial_response_does_not_hide_another_valid_source(self):
        self.source('income')
        invalid=self.source('income',rows=[financial_row('20241231',revenue=999,oper_cost=998,n_income=997)])
        self.assertEqual(invalid['status'],'返回结构异常')
        facts=observations(self.task)
        self.assertEqual(len(facts),3)
        self.assertNotIn(999,[r['value'] for r in facts])

    def test_requested_empty_fields_remain_unresolved_without_blocking_report(self):
        record=self.source(rows=[dict(market_rows()[0],close=None)])
        request=dict(id='source_request',source='tinyshare',operation='daily',parameters=record['parameters'])
        coverage=acquisition_coverage(self.task,[request])
        self.assertNotIn(request['id'],coverage['complete_request_ids'])
        self.assertEqual(coverage['unresolved'][0]['missing'],[dict(code=CODE,field='close')])
        self.assertFalse(coverage['blocks_report'])

    def test_financial_coverage_uses_report_period_not_announcement_window(self):
        record=self.source('income')
        request=dict(id='source_request',source='tinyshare',operation='income',parameters=record['parameters'])
        coverage=acquisition_coverage(self.task,[request])
        self.assertEqual(coverage['complete_request_ids'],['source_request'])
        self.assertEqual(coverage['unresolved'],[])

    def test_reader_is_adopted_and_bound_but_legacy_tasks_keep_old_reading(self):
        current=self.agent.create('核对600519.SH行情',objects=[CODE])
        self.assertEqual(current.get(POLICY),1)
        self.source();original=input_fingerprint(self.task)
        self.task.pop(POLICY)
        self.assertNotEqual(input_fingerprint(self.task),original)
        self.assertEqual(observations(self.task),[])

    def test_daily_valuation_and_capital_units_are_literal_registered_units(self):
        params=parameters('daily_basic',fields=['pe_ttm','total_share','total_mv','turnover_rate'])
        rows=[dict(ts_code=CODE,trade_date='20261030',pe_ttm=None,total_share=12000,total_mv=30000,turnover_rate=0)]
        self.source('daily_basic',params=params,rows=rows)
        facts=observations(self.task)
        self.assertEqual([r['unit'] for r in facts],['倍','万股','万元','%'])
        self.assertEqual([r['raw_value'] for r in facts],[None,12000,30000,0])
        self.assertEqual({r['unit_reference']['registry'] for r in facts},{'tinyshare.daily_basic'})

    def test_explicit_update_adopts_reader_without_changing_the_legacy_delivery(self):
        self.source();self.task.pop(POLICY)
        self.task['input_hash']=input_fingerprint(self.task);self.archive.save(self.task)
        host=dict(data_requests=[],draft_report=dict(title='原值观察',conclusion='只使用实际取得资料。'))
        old=self.agent.start(self.task['id'],host)
        self.assertTrue(old.get('delivery'),old.get('error'))
        self.assertEqual(old['draft_report']['facts'],[])
        frozen=self.archive.report(old['id'],1);original=fingerprint(self.archive.get(old['id'],1))
        new=self.agent.update(old['id'],host_result=host)
        self.assertEqual(new.get(POLICY),1)
        self.assertEqual(len(new['draft_report']['facts']),6)
        self.assertEqual(self.archive.report(old['id'],1),frozen)
        self.assertEqual(fingerprint(self.archive.get(old['id'],1)),original)

    def test_financial_visuals_share_registered_calculations_and_comparability_note(self):
        from researchhelper.drafts import compose,normalize_report,source_binding
        from researchhelper.draft_visuals import _validated,planned_figures
        from researchhelper.publication_content import reader_notes
        prior=parameters('income',period='20241231');prior['start_date']='20250101'
        row=financial_row('20241231',revenue=100,oper_cost=50,n_income=10)
        row.update(ann_date='20250415',f_ann_date='20250415')
        self.source('income',rows=[row],params=prior);self.source('income')
        facts=observations(self.task);self.task['draft_analysis']=analyze(self.task,facts)
        report=normalize_report(dict(title='经营原值对照',conclusion='比较可比期间的变化。',logics=[dict(
            title='收入与成本变化',analysis='结合收入、成本及净利润原值同比，保留尺度条件。',
            data_refs=[r['id'] for r in facts],calculation_refs=[r['id'] for r in self.task['draft_analysis']['results']])]),self.task,facts)
        self.task['draft_report']=dict(report,facts=facts,calculations=self.task['draft_analysis']['results'],source_binding=source_binding(self.task))
        content=compose(self.task);before=fingerprint(content)
        self.assertEqual(len(_validated(content)),7)
        notes=reader_notes(content);self.assertTrue(any('金额单位尚未确认' in note for note in notes))
        figures=planned_figures(content,report_profile='standard')
        self.assertTrue(any(f.get('field')=='financial_comparison' and f.get('unit')=='%' for f in figures))
        self.assertFalse(any(f.get('field') in ('revenue','oper_cost','n_income') and f.get('unit') in ('',None,'原值','接口原值','原始单位待核对') for f in figures))
        self.assertEqual(fingerprint(content),before)

    def test_engine_saves_both_report_profiles_with_actual_charts_and_raw_dataset(self):
        from lxml import html
        from researchhelper.report_designer import design
        self.source();self.source('income');self.archive.save(self.task)
        report=dict(title='行情与经营观察',conclusion='比较实际取得的行情与经营原值。',logics=[
            dict(title='价格路径',analysis='只比较已取得收盘价，不推定总回报。',data_refs=['D1','D4']),
            dict(title='经营数据',analysis='按报告期对照收入、成本和报表净利润。',data_refs=['D7','D8','D9'])])
        done=self.agent.start(self.task['id'],dict(data_requests=[],draft_report=report))
        self.assertTrue(done.get('delivery'),done.get('error'))
        self.assertEqual(done['ledger']['used']['data_requests'],0)
        self.assertEqual(done['ledger']['used']['model_calls'],0)
        content=self.archive.report(done['id'],done['version'])['content']
        for profile in ('standard','detailed'):
            tree=html.document_fromstring(design(content,ROOT,profile=profile)['html'])
            document=json.loads(tree.get_element_by_id('rh-publication-document').text)
            self.assertEqual(document['template']['id'],'report-'+profile)
            self.assertEqual(len(document['dataset']['rows']),9)
            self.assertTrue(tree.xpath('//*[@data-echart] | //*[contains(@class,"chart")]'))
            self.assertIn('千元',tree.text_content())
        self.assertEqual(len(done['source_records']),2)


if __name__=='__main__':
    unittest.main()
