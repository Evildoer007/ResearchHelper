"""Shared report precision and explicit local time, without changing frozen values."""
import copy
import unittest

from researchhelper.contracts import fingerprint
from researchhelper.reports import research_date_label
from researchhelper.visual import percentage_reading_digits, statement_display_text


class ReportPrecisionTimeTests(unittest.TestCase):
    def quantity(self,value,method='etf_daily_nav_premium_v1'):
        text='折溢价'+str(value)+'%'
        calc=dict(id='observed',method=method,output=dict(value=value,unit='%'))
        binding=dict(span=[3,len(text)],display=text[3:],source_kind='calculation',ref='observed',
            unit='%',path='value',canonical_value=str(value),projection='identity')
        record=dict(answer=text,numeric_integrity=dict(fields=dict(answer=dict(
            text_hash=fingerprint(text),bindings=[binding]))))
        return record,dict(evidence=[],calculations=[calc])

    def test_daily_nav_prose_uses_the_same_four_places_as_the_figure(self):
        record,content=self.quantity(-0.014334862385334546)
        before=copy.deepcopy((record,content))
        self.assertEqual(statement_display_text(record,'answer',content),'折溢价-0.0143%')
        self.assertEqual(percentage_reading_digits(content['calculations'][0]['method'],-0.014334862385334546),4)
        self.assertEqual((record,content),before)

    def test_same_time_nav_and_zero_share_the_same_minimum_precision(self):
        for value in (0,1.123456,-1.123456):
            record,content=self.quantity(value,'etf_nav_premium_v1')
            self.assertIn(f'{value:.4f}%',statement_display_text(record,'answer',content))

    def test_other_percentages_keep_incumbent_two_places(self):
        record,content=self.quantity(50.754,'net_profit_margin_v1')
        self.assertEqual(statement_display_text(record,'answer',content),'折溢价50.75%')

    def test_small_nonzero_is_not_rendered_as_zero(self):
        record,content=self.quantity(-0.000001)
        self.assertEqual(statement_display_text(record,'answer',content),'折溢价-0.000001%')

    def test_unbound_or_changed_text_is_not_repaired(self):
        record,content=self.quantity(-0.014334862385334546)
        record['answer']+='未经绑定的原文'
        self.assertEqual(statement_display_text(record,'answer',content),record['answer'])

    def test_aware_header_moves_to_beijing_without_changing_exact_detail(self):
        value='2026-10-22T23:36:29.603000+00:00'
        self.assertEqual(research_date_label(value,compact=True),'北京时间2026年10月23日07:36')
        self.assertEqual(research_date_label(value),'2026年10月22日23:36:29.603UTC+00:00')

    def test_known_other_offset_uses_the_same_instant(self):
        self.assertEqual(research_date_label('2026-10-23T09:36:00+10:00',compact=True),
            '北京时间2026年10月23日07:36')

    def test_naive_date_and_invalid_input_never_gain_an_invented_zone(self):
        self.assertEqual(research_date_label('2026-10-21',compact=True),'2026年10月21日')
        self.assertEqual(research_date_label('2026-10-21T12:30:00',compact=True),'2026年10月21日12:30')
        self.assertEqual(research_date_label('not-a-date',compact=True),'not-a-date')
