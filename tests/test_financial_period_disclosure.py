"""A partial financial window stays usable and is described as it actually is."""
from copy import deepcopy
import json
import socket
import tempfile
import unittest
from unittest.mock import patch

from lxml import html
from tests import ROOT
from tests.test_draft_financial_period import sample,rehash
from researchhelper.contracts import canonical,fingerprint
from researchhelper.draft_sources import FINANCIAL_PERIOD_GAP_POLICY,financial_period_scope,observations,client_writing_context
from researchhelper.draft_analysis import analyze
from researchhelper.drafts import normalize_report,compose,render
from researchhelper.publication_content import prepare_draft_client_copy


def partial_fixture():
    task=sample();task.update(draft_source_reader_version=14,draft_client_copy_version=2,
        questions=[],knowledge={},evidence=[],theses=[],calculations=[],changes=[],tracking=[],limitations=[])
    task['draft_source_attempts']=[]
    for index,source in enumerate(task['source_records']):
        task['draft_source_attempts'].append(dict(status='已提交',binding=dict(task_id=task['id'],research_version=task['version'],input_hash=task['input_hash']),
            encoded=dict(id='financial'+str(index),source='ifind',operation='basic_data_service',parameters=deepcopy(source['parameters']))))
    source=task['source_records'][1];source['snapshot']['tables'][0]['thscode']='000001.SZ';rehash(source)
    facts=observations(task);task['draft_analysis']=analyze(task,facts)
    proposal=dict(title='经营表现观察',conclusion='本次仅观察选定样本的两期经营原值；不能据此推断全行业。',
        logics=[dict(title='盈利与现金',analysis='本报告使用两期财务数据判断经营，现金流不等同于利润。',data_refs=[row['id'] for row in facts])],risks=['单一样本不代表行业。'])
    return task,facts,proposal


class FinancialPeriodDisclosureTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'));guard.start();self.addCleanup(guard.stop)

    def test_scope_is_bound_to_current_attempts_and_actual_originals(self):
        task,facts,proposal=partial_fixture();before=canonical((task,facts,proposal));scope=financial_period_scope(task,facts)
        self.assertEqual(scope['available_periods'],['20241231']);self.assertEqual(len(scope['comparisons']),3)
        self.assertTrue(all(row['missing_periods']==['20251231'] for row in scope['comparisons']))
        self.assertIn('仅覆盖2024-12-31',scope['notes'][0]);self.assertIn('2025-12-31',scope['notes'][0])
        self.assertFalse(scope['blocks_report']);self.assertFalse(scope['financial_approval'])
        self.assertEqual(canonical((task,facts,proposal)),before)

    def test_positive_coverage_claim_changes_without_removing_analysis(self):
        task,facts,proposal=partial_fixture();before=canonical(proposal)
        result,changes=prepare_draft_client_copy(task,proposal,facts)
        self.assertNotIn('两期经营原值',result['conclusion']);self.assertIn('现有经营原值',result['conclusion'])
        self.assertIn('不能据此推断全行业',result['conclusion']);self.assertIn('现金流不等同于利润',result['logics'][0]['analysis'])
        self.assertEqual(result['title'],proposal['title']);self.assertTrue(all(row['reason']=='financial_period_scope' for row in changes))
        self.assertEqual(canonical(proposal),before)

    def test_negative_hypothetical_and_quoted_statements_are_preserved(self):
        for text in ['本次未取得两期财务数据。','如果本次使用两期财务数据，需要先核对。','本报告计划比较两期财务数据。','原文称“本次已取得两期财务数据”。','本次不使用两期经营原值。']:
            with self.subTest(text=text):
                task,facts,proposal=partial_fixture();proposal['conclusion']=text
                result,_=prepare_draft_client_copy(task,proposal,facts)
                self.assertEqual(result['conclusion'],text)

    def test_writer_receives_compact_scope_without_another_call(self):
        task,facts,_=partial_fixture();data=client_writing_context(task,facts)
        self.assertEqual(data['financial_period_scope']['available_periods'],['20241231'])
        self.assertNotIn('comparisons',data['financial_period_scope']);self.assertNotIn('financial_refs',data['financial_period_scope'])
        self.assertIn('单期资料不写成两期',data['financial_period_scope']['rule'])

    def test_legacy_copy_policy_does_not_rewrite_saved_semantics(self):
        task,facts,proposal=partial_fixture();task['draft_client_copy_version']=1
        result,_=prepare_draft_client_copy(task,proposal,facts)
        self.assertEqual(result['conclusion'],proposal['conclusion']);self.assertIsNone(financial_period_scope(task,facts))
        self.assertNotIn('financial_period_scope',client_writing_context(task,facts))

    def test_foreign_rejected_and_stale_attempts_cannot_create_gaps(self):
        for mutation in ('task','version','input','rejected'):
            with self.subTest(mutation=mutation):
                task,facts,_=partial_fixture();attempt=task['draft_source_attempts'][1]
                if mutation=='rejected':attempt['status']='参数未通过'
                else:attempt['binding'][{'task':'task_id','version':'research_version','input':'input_hash'}[mutation]]='foreign'
                self.assertEqual(financial_period_scope(task,facts)['notes'],[])

    def test_later_valid_response_removes_obsolete_missing_period_notice(self):
        task,facts,_=partial_fixture();source=task['source_records'][1];source['snapshot']['tables'][0]['thscode']='600519.SH';rehash(source)
        facts=observations(task);scope=financial_period_scope(task,facts)
        self.assertEqual(scope['notes'],[]);self.assertTrue(all(not row['missing_periods'] for row in scope['comparisons']))

    def test_partial_field_does_not_cancel_an_available_comparison(self):
        task,facts,proposal=partial_fixture();source=task['source_records'][1]
        source['snapshot']['tables'][0].update(thscode='600519.SH',table=dict(ths_revenue_stock=[120]));rehash(source)
        facts=observations(task);task['draft_analysis']=analyze(task,facts);scope=financial_period_scope(task,facts)
        self.assertEqual(scope['available_periods'],['20241231','20251231']);self.assertNotIn('仅覆盖',scope['notes'][0])
        self.assertNotIn('营业收入尚缺',scope['notes'][0]);self.assertIn('净利润',scope['notes'][0])
        proposal['logics'][0]['data_refs']=[row['id'] for row in facts if row['field']=='ths_np_stock']
        result,_=prepare_draft_client_copy(task,proposal,facts)
        self.assertNotIn('两期财务数据',result['logics'][0]['analysis']);self.assertEqual(result['conclusion'],proposal['conclusion'])

    def test_visible_note_is_frozen_with_the_report_not_model_supplied(self):
        task,facts,proposal=partial_fixture();proposal['financial_period_scope']=dict(notes=['伪造范围'])
        draft=normalize_report(proposal,task,facts)
        self.assertIn('financial_period_scope',draft);self.assertNotIn('伪造范围',canonical(draft))
        self.assertEqual(draft['financial_period_scope']['facts_hash'],fingerprint(facts))

    def test_different_statement_does_not_fill_a_requested_period(self):
        task,facts,_=partial_fixture();source=task['source_records'][1]
        source['snapshot']['tables'][0]['thscode']='600519.SH'
        for item in source['parameters']['indipara']:item['indiparams'][1]='2'
        rehash(source);facts=observations(task);scope=financial_period_scope(task,facts)
        self.assertTrue(all(row['available_periods']==['20241231'] for row in scope['comparisons']))
        self.assertIn('合并报表',scope['notes'][0]);self.assertIn('2025-12-31',scope['notes'][0])

    def test_no_values_still_yields_a_scope_and_a_nonblocking_report(self):
        task,facts,proposal=partial_fixture();task['source_records'][0]['status']='失败'
        facts=observations(task);task['draft_analysis']=analyze(task,facts)
        scope=financial_period_scope(task,facts);self.assertEqual(scope['available_periods'],[])
        self.assertIn('尚未取得',scope['notes'][0]);self.assertFalse(scope['blocks_report'])
        draft=normalize_report(proposal,task,facts)
        self.assertIn('尚未取得所需财务原值',draft['conclusion']);self.assertNotIn('两期经营原值',draft['conclusion'])

    def test_missing_single_period_field_is_visible_and_can_be_filled(self):
        task,_,proposal=partial_fixture()
        task[FINANCIAL_PERIOD_GAP_POLICY]=1
        task['source_records'].pop()
        task['draft_source_attempts'].pop()
        source=task['source_records'][0]
        table=source['snapshot']['tables'][0]['table']
        net_profit=table.pop('ths_np_stock')
        rehash(source)
        facts=observations(task)
        task['draft_analysis']=analyze(task,facts)
        scope=financial_period_scope(task,facts)
        self.assertEqual(scope['comparisons'],[])
        self.assertEqual(len(scope['notes']),1)
        self.assertRegex(scope['notes'][0],r'净利润尚缺2024-12-31')
        self.assertNotIn('营业收入',scope['notes'][0])
        self.assertEqual(normalize_report(proposal,task,facts)['financial_period_scope'],scope)
        table['ths_np_stock']=net_profit
        rehash(source)
        self.assertEqual(financial_period_scope(task,observations(task))['notes'],[])

    def test_different_fields_keep_their_own_missing_periods(self):
        task,_,_=partial_fixture()
        task[FINANCIAL_PERIOD_GAP_POLICY]=1
        first,last=task['source_records']
        first['snapshot']['tables'][0]['table'].pop('ths_np_stock')
        last['snapshot']['tables'][0].update(thscode='600519.SH',table=dict(ths_np_stock=[12]))
        rehash(first);rehash(last)
        notes=financial_period_scope(task,observations(task))['notes']
        self.assertEqual(len(notes),2)
        profit=next(note for note in notes if '净利润' in note)
        revenue=next(note for note in notes if '营业收入' in note)
        self.assertIn('2024-12-31',profit);self.assertNotIn('2025-12-31',profit)
        self.assertIn('2025-12-31',revenue);self.assertNotIn('2024-12-31',revenue)

    def test_document_content_copy_is_not_rewritten(self):
        task,facts,proposal=partial_fixture();task['intent']='原件内容核对'
        result,_=prepare_draft_client_copy(task,proposal,facts)
        self.assertEqual(result['conclusion'],proposal['conclusion'])
