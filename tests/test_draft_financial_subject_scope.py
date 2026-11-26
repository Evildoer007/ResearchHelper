"""Financial rows must belong to the literal request in both model phases.

Fixtures are synthetic. Every raw response survives; a local mismatch cannot
prevent an independent valid row or the first report from being delivered.
"""
from copy import deepcopy
import json
import socket
import unittest
from unittest.mock import patch

from tests.test_draft_financial_period import sample,rehash
from researchhelper.contracts import canonical
from researchhelper.draft_sources import (VERSION,READER_POLICY,observations,
    source_context,writing_context,source_issues)
from researchhelper.draft_analysis import analyze

CODE='600519.SH'
OTHER='000001.SZ'
SENTINEL=987654321


def fixture(tables=None,codes=CODE,reader=VERSION):
    task=sample();task[READER_POLICY]=reader
    if tables is not None:
        task['source_records']=task['source_records'][:1]
        task['source_records'][0]['snapshot']['tables']=deepcopy(tables)
    for record in task['source_records']:
        record['parameters']['codes']=codes
        rehash(record)
    return task


def table(code=CODE,values=None,**extra):
    columns=dict(ths_revenue_stock=[100] if values is None else values,**extra)
    result=dict(time=['2026-10-31']*len(columns['ths_revenue_stock']),table=columns)
    if code is not None:result['thscode']=code
    return result


class FinancialSubjectScopeTests(unittest.TestCase):
    def setUp(self):
        blocked=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        blocked.start();self.addCleanup(blocked.stop)

    def assert_absent_from_model(self,task,*tokens):
        payload=canonical(dict(planning=source_context(task),writing=writing_context(task,observations(task))))
        for token in tokens:self.assertNotIn(str(token),payload)

    def test_wrong_table_subject_never_reaches_facts_calculation_or_models(self):
        task=fixture([table(OTHER,[SENTINEL])]);before=canonical(task)
        self.assertEqual(observations(task),[])
        self.assertEqual(analyze(task,observations(task))['results'],[])
        self.assert_absent_from_model(task,OTHER,SENTINEL)
        self.assertEqual(canonical(task),before)
        self.assertTrue(source_issues(task)[0]['partial'])
        self.assertIn('财务',source_issues(task)[0]['reason'])

    def test_valid_table_survives_foreign_table_in_the_same_response(self):
        task=fixture([table(OTHER,[SENTINEL]),table()]);before=canonical(task)
        self.assertEqual([(row['code'],row['raw_value'],row['locator']['table']) for row in observations(task)],[(CODE,100,1)])
        self.assert_absent_from_model(task,OTHER,SENTINEL)
        self.assertEqual(canonical(task),before)

    def test_row_subjects_are_selected_individually_and_keep_original_locators(self):
        task=fixture([table(None,[SENTINEL,100],thscode=[OTHER,CODE])])
        rows=observations(task);self.assertEqual([(row['code'],row['raw_value'],row['locator']['row']) for row in rows],[(CODE,100,1)])
        projected=json.loads(source_context(task)[0]['content'])['tables'][0]
        self.assertEqual(projected['table']['ths_revenue_stock'],[100])
        self.assertEqual(projected['thscode'],CODE);self.assert_absent_from_model(task,OTHER,SENTINEL)

    def test_requested_multiple_subjects_keep_separate_row_codes(self):
        task=fixture([table(None,[100,200],thscode=[CODE,OTHER])],codes=CODE+','+OTHER)
        self.assertEqual([(row['code'],row['value']) for row in observations(task)],[(CODE,100),(OTHER,200)])
        self.assertEqual(source_issues(task),[])

    def test_missing_subject_in_a_multiple_code_response_is_not_a_comma_joined_security(self):
        task=fixture([table(None,[100,200])],codes=CODE+','+OTHER)
        self.assertEqual(observations(task),[])
        self.assertEqual(json.loads(source_context(task)[0]['content']),dict(tables=[]))

    def test_single_requested_code_can_supply_an_omitted_echo(self):
        task=fixture([table(None,[100])])
        self.assertEqual([(row['code'],row['value']) for row in observations(task)],[(CODE,100)])
        self.assertEqual(source_issues(task),[])

    def test_valid_table_code_covers_a_multiple_code_request(self):
        task=fixture([table(CODE,[100]),table(OTHER,[200])],codes=CODE+','+OTHER)
        self.assertEqual({row['code'] for row in observations(task)},{CODE,OTHER})

    def test_table_and_row_identifiers_cannot_contradict_even_when_both_requested(self):
        task=fixture([table(CODE,[SENTINEL,100],thscode=[OTHER,CODE])],codes=CODE+','+OTHER)
        self.assertEqual([(row['code'],row['value']) for row in observations(task)],[(CODE,100)])
        self.assert_absent_from_model(task,SENTINEL)

    def test_two_row_identifier_columns_must_agree(self):
        task=fixture([table(None,[SENTINEL,100],thscode=[OTHER,CODE],ts_code=[CODE,CODE])],codes=CODE+','+OTHER)
        self.assertEqual([(row['code'],row['value']) for row in observations(task)],[(CODE,100)])
        self.assert_absent_from_model(task,SENTINEL)

    def test_numeric_or_malformed_echo_is_not_mapped_by_guessing(self):
        for code in (600519,True,['600519.SH'],'600519','600519.SZ'):
            with self.subTest(code=code):
                task=fixture([table(code,[SENTINEL])])
                self.assertEqual(observations(task),[]);self.assert_absent_from_model(task,SENTINEL)

    def test_misaligned_numeric_column_does_not_displace_valid_subject_rows(self):
        task=fixture([table(None,[100,200],thscode=[CODE,OTHER],ths_np_stock=[SENTINEL])],codes=CODE+','+OTHER)
        self.assertEqual([row['value'] for row in observations(task)],[100,200])
        self.assert_absent_from_model(task,SENTINEL)
        self.assertTrue(source_issues(task)[0]['partial'])

    def test_misaligned_identity_columns_do_not_index_a_different_row(self):
        task=fixture([table(None,[SENTINEL,100],thscode=[CODE,OTHER],ts_code=[CODE])],codes=CODE+','+OTHER)
        self.assertEqual(observations(task),[]);self.assert_absent_from_model(task,SENTINEL)

    def test_reader_twenty_preserves_its_additional_business_text_policy(self):
        task=fixture([table(None,[SENTINEL,100],thscode=[OTHER,CODE],主营业务=['其他公司的业务','本公司的业务'])],reader=20)
        context=writing_context(task,observations(task));body=canonical(context)
        self.assertIn('本公司的业务',body);self.assertNotIn('其他公司的业务',body)
        self.assertNotIn(str(SENTINEL),body)
        self.assertNotIn('ths_revenue_stock',context['other_data'][0]['content'])

    def test_scalar_values_and_missing_response_dates_keep_the_requested_report_period(self):
        value=dict(thscode=CODE,table=dict(ths_revenue_stock=100))
        task=fixture([value]);rows=observations(task)
        self.assertEqual([(r['raw_value'],r['period'],r['period_basis']) for r in rows],[(100,'20241231','请求D报告期')])
        self.assertEqual(json.loads(source_context(task)[0]['content'])['tables'][0]['table']['ths_revenue_stock'],[100])

    def test_zero_and_missing_cells_remain_available_on_the_correct_subject(self):
        task=fixture([table(None,[0,None],thscode=[CODE,OTHER])],codes=CODE+','+OTHER)
        self.assertEqual([(r['code'],r['raw_value']) for r in observations(task)],[(CODE,0),(OTHER,None)])

    def test_unrequested_fields_do_not_set_row_shape_or_enter_either_model_phase(self):
        task=fixture([table(预期利润=[SENTINEL, SENTINEL+1], 其他金额=[SENTINEL])])
        original=deepcopy(task['source_records'])
        self.assertEqual([(row['field'],row['raw_value']) for row in observations(task)], [('ths_revenue_stock',100)])
        self.assert_absent_from_model(task,SENTINEL,SENTINEL+1)
        self.assertEqual(task['source_records'],original)

    def test_repeated_cells_do_not_hide_other_fields_or_securities(self):
        task=fixture([table(),table(CODE,[SENTINEL],ths_np_stock=[10]),table(OTHER,[200])],codes=CODE+','+OTHER)
        rows=observations(task)
        self.assertEqual([(row['code'],row['field'],row['raw_value'],row['locator']['table']) for row in rows],
                         [(CODE,'ths_np_stock',10,1),(OTHER,'ths_revenue_stock',200,2)])
        self.assert_absent_from_model(task,SENTINEL)
        self.assertIn('重复',source_issues(task)[0]['reason'])

    def test_malformed_value_does_not_hide_valid_zero_or_null(self):
        task=fixture([table(CODE,[True],ths_np_stock=[0],ths_ncf_from_oa_stock=[None])])
        self.assertEqual([(row['field'],row['raw_value']) for row in observations(task)],
                         [('ths_np_stock',0),('ths_ncf_from_oa_stock',None)])
        self.assertTrue(source_issues(task)[0]['partial'])

    def test_mismatched_statement_echo_excludes_even_plausible_numbers(self):
        task=fixture([table(CODE,[SENTINEL])])
        record=task['source_records'][0]
        record['snapshot']['inputParams']['params'][0]['params'][1]['value']='2'
        rehash(record)
        self.assertEqual(observations(task),[])
        self.assert_absent_from_model(task,SENTINEL)

    def test_yoy_keeps_valid_numbers_when_an_independent_foreign_response_exists(self):
        task=fixture();expected=analyze(task,observations(task))['results']
        task['source_records'][0]['snapshot']['tables'].append(table(OTHER,[SENTINEL]));rehash(task['source_records'][0])
        actual=analyze(task,observations(task))['results']
        self.assertEqual([(r['label'],r['display']) for r in actual],[(r['label'],r['display']) for r in expected])
        self.assert_absent_from_model(task,SENTINEL,OTHER)

    def test_invalid_table_is_local_and_reported_without_hiding_valid_cells(self):
        task=fixture([None,dict(thscode=OTHER,table='invalid'),table()])
        self.assertEqual([row['value'] for row in observations(task)],[100])
        self.assertTrue(source_issues(task)[0]['partial'])
        self.assert_absent_from_model(task,OTHER)

    def test_empty_optional_code_column_does_not_hide_an_explicit_single_subject(self):
        task=fixture([table(CODE,[100],ts_code=[])])
        self.assertEqual([(row['code'],row['value']) for row in observations(task)],[(CODE,100)])

    def test_saved_reader_nine_retains_the_original_interpretation(self):
        task=fixture([table(OTHER,[SENTINEL])],reader=9)
        self.assertEqual([(r['code'],r['raw_value']) for r in observations(task)],[(OTHER,SENTINEL)])

    def test_version_ten_keeps_annual_unit_definitions_and_provenance(self):
        task=fixture();task['cutoff']='2026-11-04T00:00:00+08:00'
        rows=observations(task)
        self.assertEqual({r['unit'] for r in rows},{'元'})
        self.assertTrue(all(r['unit_reference']['kind']=='calibrated_definition' for r in rows))


class FinancialSubjectWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_draft_first_workflow import DraftFirstWorkflowTests
        cls.flow_type=DraftFirstWorkflowTests;cls.flow_type.setUpClass()

    @classmethod
    def tearDownClass(cls):cls.flow_type.tearDownClass()

    def setUp(self):
        self.flow=self.flow_type('runTest');self.flow.setUp();self.addCleanup(self.flow.doCleanups)
        self.addCleanup(self.flow.agent.close_runtime)
        self.flow.responses=deepcopy(self.flow.responses)
        self.received=[];original=self.flow.model.reason
        def reason(task,messages,**options):
            if options['phase']=='draft_report':self.received.append(json.loads(messages[-1]['content']))
            return original(task,messages,**options)
        self.flow.model.reason=reason

    def run_once(self):
        from tests.test_draft_first_workflow import QUESTION
        task=self.flow.agent.create(QUESTION,research_mode='current',
            limits=dict(data_requests=100,model_calls=2,seconds=600))
        task=self.flow.agent.start(task['id'])
        self.assertTrue(task['delivery'],task.get('error'))
        return task,self.flow.agent.archive.report(task['id'],task['version'])

    def test_first_report_uses_valid_financial_rows_and_preserves_bad_original_in_workpaper(self):
        for key,payload in self.flow.responses.items():
            if key.startswith('basic_data_service:'):
                wrong=deepcopy(payload['tables'][0]);wrong['thscode']='600519.SH'
                wrong['table']={field:[SENTINEL] for field in wrong['table']};payload['tables'].insert(0,wrong)
        task,paper=self.run_once()
        self.assertEqual(task[READER_POLICY],VERSION)
        self.assertEqual({r['code'] for r in task['draft_report']['facts']},{'002594.SZ'})
        self.assertNotIn(str(SENTINEL),canonical(self.received));self.assertNotIn(str(SENTINEL),paper['html'])
        self.assertIn(str(SENTINEL),canonical(task['source_records']))
        self.assertTrue(task['delivery']);self.assertTrue(source_issues(task))
        from lxml import html
        main=html.document_fromstring(paper['html']).xpath('//main')[0].text_content()
        self.assertNotIn(source_issues(task)[0]['reason'],main)
        for record in task['source_records']:
            if record['operation']=='basic_data_service':
                self.assertFalse(record['metadata_contract']['shape_validated'])
                contract=record['draft_response_contract']
                self.assertTrue(contract['projected_shape_validated'])
                self.assertFalse(contract['whole_response_approved'])
                self.assertFalse(contract['numeric_evidence_approved'])
        self.assertEqual(self.flow.model.calls,['draft_plan','draft_report'])

    def test_wrong_only_financial_response_does_not_block_the_first_report(self):
        for key,payload in self.flow.responses.items():
            if key.startswith('basic_data_service:'):
                for response in payload['tables']:response['thscode']='600519.SH'
        task,paper=self.run_once()
        self.assertTrue(task['delivery']);self.assertTrue(paper['html'])
        self.assertEqual(task['draft_report']['facts'],[])
        self.assertEqual(task['draft_report']['calculations'],[])
        self.assertEqual(self.flow.model.calls,['draft_plan','draft_report'])


    def test_inconsistent_report_period_echo_cannot_be_rescued_by_a_valid_subject(self):
        for key,payload in self.flow.responses.items():
            if key.startswith('basic_data_service:'):
                for reflected in payload['inputParams']['params']:
                    for parameter in reflected['params']:
                        if parameter['name']=='FDREPORT':parameter['value']='19991231'
        task,paper=self.run_once()
        self.assertEqual(task['draft_report']['facts'],[])
        self.assertTrue(paper['html'])
        for record in task['source_records']:
            if record['operation']=='basic_data_service':
                self.assertEqual(record['status'],'返回结构异常')
                self.assertNotIn('draft_response_contract',record)

    def test_failed_business_response_is_not_promoted_by_the_projection(self):
        for key,payload in self.flow.responses.items():
            if key.startswith('basic_data_service:'):payload['errorcode']=-1
        task,paper=self.run_once()
        self.assertEqual(task['draft_report']['facts'],[]);self.assertTrue(paper['html'])
        for record in task['source_records']:
            if record['operation']=='basic_data_service':self.assertNotIn('draft_response_contract',record)

    def test_additional_financial_fields_stay_in_the_workpaper(self):
        for key,payload in self.flow.responses.items():
            if key.startswith('basic_data_service:'):
                payload['tables'][0]['table']['主营业务']=['汽车与相关技术']
                payload['tables'][0]['table']['ths_np_atoopc_stock']=[SENTINEL]
        task,paper=self.run_once()
        self.assertEqual(len(task['draft_report']['facts']),8)
        for token in ('汽车与相关技术', str(SENTINEL)):
            self.assertNotIn(token,canonical(self.received))
            self.assertNotIn(token,paper['html'])
            self.assertIn(token,canonical(task['source_records']))
        self.assertTrue(paper['html'])


if __name__=='__main__':unittest.main()
