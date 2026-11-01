"""Request-column notices inform analysis without deleting usable observations."""
from copy import deepcopy
from pathlib import Path
import json,socket,tempfile,unittest
from unittest.mock import patch
from tests import ROOT
from core.session import Session
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import canonical,fingerprint
from researchhelper.draft_sources import observations,writing_context,request_field_context
from researchhelper.drafts import normalize_report
from researchhelper.draft_workflow import _write
from tests.test_draft_financial_period import sample,add_record,rehash


def financial_task():
    task=sample();task['source_records']=[]
    task.update(questions=[],evidence=[],calculations=[],knowledge={})
    add_record(task,'20251231',(120,12),fields=('ths_revenue_stock','ths_np_stock'))
    return task


def semantic_task(query):
    task=financial_task();source=task['source_records'][0]
    source.update(operation='smart_stock_picking',parameters=dict(searchstring=query,searchtype='stock'))
    source['snapshot']['tables']=[dict(table={'股票代码':['600519.SH'],'股票简称':['贵州茅台'],
        '营业收入[20251231]':[120],'归属于母公司所有者的净利润[20251231]':[12],
        '成交量[20261030]':[5992729]})]
    rehash(source);return task


def notice(task):return request_field_context(task)['notices'][0]


class DraftRequestFieldsTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external I/O'))
        guard.start();self.addCleanup(guard.stop)

    def test_normal_fixed_fields_have_no_prompt_or_report_noise(self):
        task=financial_task();context=writing_context(task,observations(task))
        self.assertNotIn('request_context',context)
        self.assertTrue(all('request_scope' not in item for item in context['observations']))
        self.assertNotIn('request_context',normalize_report({},task,observations(task)))

    def test_additional_numeric_and_text_columns_are_retained_and_identified(self):
        task=financial_task();source=task['source_records'][0]
        source['snapshot']['tables'][0]['table'].update(volume=[5],主营业务=['研发'])
        rehash(source);before=canonical(task);facts=observations(task);context=writing_context(task,facts)
        self.assertEqual(notice(task)['additional_fields'],['volume','主营业务'])
        extra=next(item for item in context['observations'] if item['field']=='volume')
        self.assertEqual(extra['request_scope'],'additional');self.assertEqual(extra['rows'][0][-1],5)
        self.assertIn('研发',context['other_data'][0]['content']);self.assertEqual(canonical(task),before)
        self.assertEqual(len(facts),3)

    def test_missing_fields_and_wholly_empty_fields_are_distinct(self):
        task=financial_task();source=task['source_records'][0]
        source['snapshot']['tables'][0]['table']={'ths_np_stock':[None]};rehash(source)
        self.assertEqual(notice(task)['missing_fields'],['ths_revenue_stock'])
        self.assertEqual(notice(task)['empty_fields'],['ths_np_stock'])

    def test_zero_is_present_while_partial_nulls_are_reported_without_dropping_rows(self):
        task=financial_task();source=task['source_records'][0]
        source['snapshot']['tables'][0]['table'].update(ths_revenue_stock=[0,None],ths_np_stock=[0,1]);rehash(source)
        self.assertEqual(notice(task)['partially_empty_fields'],['ths_revenue_stock'])
        self.assertNotIn('empty_fields',notice(task));self.assertEqual(len(observations(task)),4)

    def test_zero_length_requested_column_has_no_values(self):
        task=financial_task();source=task['source_records'][0]
        source['snapshot']['tables'][0]['table']['ths_np_stock']=[];rehash(source)
        self.assertEqual(notice(task)['empty_fields'],['ths_np_stock'])

    def test_registered_semantic_request_identifies_provider_added_volume(self):
        task=semantic_task('600519.SH2025年年报营业收入、归属于母公司所有者的净利润')
        self.assertEqual(notice(task)['additional_fields'],['成交量[20261030]'])
        self.assertNotIn('股票代码',canonical(notice(task)));self.assertNotIn('股票简称',canonical(notice(task)))
        context=writing_context(task,observations(task))
        self.assertEqual([item['field'] for item in context['observations'] if item.get('request_scope')=='additional'],['成交量[20261030]'])

    def test_unregistered_unit_wording_is_not_silently_registered_or_substring_matched(self):
        task=semantic_task('600519.SH2025年年报营业收入、归属于母公司所有者的净利润，分别列出原始金额和计量单位')
        self.assertEqual(notice(task)['request_scope'],'unspecified')
        self.assertNotIn('additional_fields',notice(task))
        context=writing_context(task,observations(task))
        self.assertEqual(len(context['observations']),3)
        self.assertTrue(all(item['request_scope']=='unspecified' for item in context['observations']))

    def test_derived_financial_contract_cannot_change_literal_request_scope(self):
        task=semantic_task('600519.SH2025年年报营业收入、归属于母公司所有者的净利润')
        before=notice(task)
        task['source_records'][0]['financial_query_contract']=dict(fields=['成交量'],period='20261030')
        self.assertEqual(notice(task),before)

    def test_quote_scope_still_excludes_unrequested_cells_from_writing(self):
        task=financial_task();task['draft_quote_scope_version']=2;source=task['source_records'][0]
        source.update(operation='cmd_history_quotation',parameters=dict(codes='600519.SH',indicators='close',
            startdate='2026-10-29',enddate='2026-10-30',functionpara=dict(Currency='RMB')))
        source['snapshot']['tables']=[dict(thscode='600519.SH',time=['2026-10-29','2026-10-30'],
            table=dict(close=[100,101],open=[99999,99999]))];rehash(source)
        context=writing_context(task,observations(task))
        self.assertEqual(notice(task)['additional_fields'],['open'])
        self.assertNotIn('99999',canonical(context));self.assertEqual(len(context['observations']),1)

    def test_edb_matches_indicator_ids_and_keeps_different_requested_roles(self):
        task=financial_task();source=task['source_records'][0]
        source.update(operation='edb_service',parameters=dict(indicators='M1,M3',startdate='2026-09-01',enddate='2026-09-30'))
        source['snapshot']['tables']=[dict(id=[code],time=['2026-09-30'],value=[value]) for code,value in [('M1',0),('M2',1)]]
        rehash(source);context=writing_context(task,observations(task))
        self.assertEqual(notice(task)['dimension'],'indicator_id')
        self.assertEqual(notice(task)['missing_fields'],['M3']);self.assertEqual(notice(task)['additional_fields'],['M2'])
        self.assertNotIn('value',canonical(notice(task)))
        self.assertEqual([item['code'] for item in context['observations'] if item.get('request_scope')=='additional'],['M2'])

    def test_foreign_or_changed_requests_cannot_supply_notices_or_numbers(self):
        for change in ('task_id','parameters','snapshot'):
            task=financial_task();source=task['source_records'][0]
            if change=='task_id':source[change]='foreign'
            elif change=='parameters':source[change]['indipara'][0]['indicator']='foreign'
            else:source[change]['tables'][0]['table']['foreign']=[1]
            with self.subTest(change=change):
                self.assertEqual(request_field_context(task),{});self.assertEqual(writing_context(task,observations(task))['observations'],[])

    def test_a_supported_empty_response_reports_missing_columns_without_blocking_prose(self):
        task=financial_task();source=task['source_records'][0];source['snapshot']['tables']=[];rehash(source)
        report=normalize_report(dict(title='保留初稿',conclusion='可先讨论条件。'),task,observations(task))
        self.assertEqual(report['conclusion'],'可先讨论条件。')
        self.assertEqual(report['request_context']['notices'][0]['missing_fields'],['ths_np_stock','ths_revenue_stock'])

    def test_new_report_freezes_detached_task_and_request_binding(self):
        task=financial_task();source=task['source_records'][0]
        source['snapshot']['tables'][0]['table']['volume']=[5];rehash(source)
        before=canonical(task);report=normalize_report({},task,observations(task))
        context=report['request_context'];self.assertEqual(context['task_id'],task['id'])
        self.assertEqual(context['research_version'],1);self.assertEqual(context['input_hash'],task['input_hash'])
        self.assertEqual(len(context['source_binding']),64);self.assertEqual(len(context['request_hash']),64)
        frozen=deepcopy(report);source['parameters']['indipara'].append(dict(indicator='volume',indiparams=[]));rehash(source)
        self.assertNotIn('request_context',normalize_report({},task,observations(task)))
        self.assertEqual(frozen,report);self.assertNotEqual(before,canonical(task))

    def test_skill_and_app_writer_receive_the_same_guidance_without_calls(self):
        captured=[]
        class Model:
            def reason(self,task,messages,**options):captured.append(messages);return '{}'
        class Engine:model=Model()
        with tempfile.TemporaryDirectory(dir=ROOT/'result') as folder:
            # The unit test owns a temporary archive within RH; independent
            # package acceptance separately exercises the installation boundary.
            with patch('core.session.project_store',return_value=ResearchArchive(Path(folder)/'data/researchhelper')):
                session=Session(folder)
            self.addCleanup(session.agent.close_runtime)
            task=session.create('比较贵州茅台600519.SH2025年收入与净利润',objects=['600519.SH'])
            source=add_record(task,'20251231',(120,),fields=('ths_revenue_stock',))
            source['snapshot']['tables'][0]['table']['volume']=[5]
            # A controlled retained record tests projection, not API admission.
            source['content_version']=1;rehash(source)
            session.archive.save(task)
            _write(Engine(),task,observations(task))
            app=json.loads(captured[0][1]['content'])['data']['request_context']
            skill=session.tools(task['id'])['first_report']['request_context']
            self.assertEqual(app,skill);self.assertEqual(session.status(task['id'])['ledger']['used']['model_calls'],0)
            self.assertEqual(session.status(task['id'])['ledger']['used']['data_requests'],0)

    def test_non_ifind_response_is_not_given_ifind_request_semantics(self):
        task=financial_task();source=task['source_records'][0];source['source']='other';rehash(source)
        self.assertEqual(request_field_context(task),{})
        self.assertTrue(all('request_scope' not in item for item in writing_context(task,observations(task))['observations']))


if __name__=='__main__':unittest.main()
