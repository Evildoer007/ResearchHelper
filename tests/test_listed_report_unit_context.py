"""Report main codes require explicit share listing; never trading-code guesses."""
import copy
import unittest

from tests import ROOT
from researchhelper.evidence import (listed_report_unit_context_candidates,
    listed_etf_unit_context_candidates, LISTED_REPORT_UNIT_PREFIX)
from researchhelper.financial_sources import original_line_index, selected_original_span
from researchhelper.output_schema import original_span_prompt
from researchhelper.planning import input_fingerprint


class ListedReportUnitContextTests(unittest.TestCase):
    def view(self,text=None):
        return dict(directory=dict(report_date='2026-08-31'),parts=[dict(locator='页1',text=text or
            '基金主代码 159919\n基金运作方式 交易型开放式\n基金份额上市的证券交\n易所\n深圳证券交易所\n上市日期 2012年5月28日\n')])

    def test_explicit_listing_and_main_code_are_a_distinct_nonapproving_basis(self):
        view = self.view()
        rows = listed_report_unit_context_candidates(view,'159919.SZ')
        self.assertEqual(len(rows),1)
        row = rows[0]
        self.assertEqual(row['value'],LISTED_REPORT_UNIT_PREFIX+'159919.SZ')
        self.assertFalse(row['candidate_is_approval'])
        self.assertFalse(row['ac_category_inferred'])
        self.assertFalse(row['trading_code_mapping_approved'])
        self.assertEqual(selected_original_span(original_line_index(view),row['span'])['quote'],row['quote'])
        self.assertEqual(listed_etf_unit_context_candidates(view,'159919.SZ'),[])

    def test_each_required_field_missing_future_date_and_conflicting_exchange_stop(self):
        text = self.view()['parts'][0]['text']
        bad = [text.replace('基金主代码 159919','基金简称 ETF'),
               text.replace('交易型开放式','契约型开放式'),
               text.replace('基金份额上市的证券交\n易所','基金销售机构'),
               text.replace('深圳证券交易所','上海证券交易所'),
               text.replace('上市日期 2012年5月28日','上市日期 未披露'),
               text.replace('2012年5月28日','2027年5月28日'),
               text.replace('2012年5月28日','2012年2月30日'),
               text+'\n基金主代码 510300',text+'\n各类基金份额A类基金份额与C类基金份额',
               text+'\n计划上市',text.replace('基金主代码','拟基金主代码')]
        for value in bad:
            with self.subTest(value=value):
                self.assertEqual(listed_report_unit_context_candidates(self.view(value),'159919.SZ'),[])

    def test_fields_cannot_be_stitched_from_different_parts(self):
        text = self.view()['parts'][0]['text']
        first,second = text.split('基金份额上市')
        view = self.view();view['parts']=[dict(locator='页1',text=first),dict(locator='页2',text='基金份额上市'+second)]
        self.assertEqual(listed_report_unit_context_candidates(view,'159919.SZ'),[])

    def test_missing_or_invalid_report_date_does_not_approve_listing_as_current(self):
        for date in (None,True,'2026-02-30','2026年8月31日'):
            view = self.view();view['directory']['report_date']=date
            self.assertEqual(listed_report_unit_context_candidates(view,'159919.SZ'),[])

    def test_projection_quotes_are_recoverable_without_dropping_any_candidate(self):
        from tests.test_original_interval_context import OriginalIntervalContextTests as fixture
        from tests.probe_etf_extraction_context import payload_for
        fixture.setUpClass()
        try:
            task = fixture.task
            payload = payload_for(task)
            projected = original_span_prompt(payload,task)
            for original,current,choice in zip(payload['evidence_contract']['sources'],
                    projected['evidence_contract']['sources'],fixture.choices):
                encoded = current['listed_etf_unit_candidates']
                records = [dict(zip(encoded['columns'],row)) for row in encoded['rows']] if isinstance(encoded,dict) else encoded
                restored = []
                for row in records:
                    value = copy.deepcopy(row)
                    if value.pop('quote_from_span',False):
                        value['quote']=selected_original_span(choice['lines'],value['span'])['quote']
                    restored.append(value)
                self.assertEqual(restored,original['listed_etf_unit_candidates'])
        finally:
            fixture.tearDownClass()

    def test_new_task_policy_is_not_implicitly_enabled_for_old_scope(self):
        from tests.test_original_interval_context import OriginalIntervalContextTests as fixture
        from researchhelper.reasoning import evidence_contract
        fixture.setUpClass()
        try:
            old = dict(fixture.task);old.pop('listed_report_unit_context_version');old['input_hash']=input_fingerprint(old)
            self.assertNotEqual(old['input_hash'],fixture.task['input_hash'])
            self.assertTrue(all(not row.get('listed_etf_unit_candidates') for row in evidence_contract(old,fixture.views)['sources']))
        finally:
            fixture.tearDownClass()
