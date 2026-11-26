"""Readable quantities and source-level disclosure without rewriting frozen research."""
import copy
from html.parser import HTMLParser
from pathlib import Path
import unittest

from tests.argument_review_fixture import ENTITY, financial_review_fixture
from researchhelper.contracts import fingerprint
from researchhelper.reports import READER_VERSION, report_heading
from researchhelper.visual import (render, statement_display_text, statement_paragraphs,
    reading_diagnostics)

ROOT=Path(__file__).resolve().parents[1]


class MainPaper(HTMLParser):
    def __init__(self,page):
        super().__init__();self.main=False;self.text=[];self.links=[];self.targets=[]
        self.feed(page)
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if tag=='main':self.main=True
        if values.get('id'):self.targets.append(values['id'])
        if self.main and tag=='a':self.links.append(values)
    def handle_endtag(self,tag):
        if tag=='main':self.main=False
    def handle_data(self,text):
        if self.main:self.text.append(text)


class ReportReadingPrecisionTests(unittest.TestCase):
    def setUp(self):
        _, self.frozen = financial_review_fixture()
        self.content = copy.deepcopy(self.frozen)
        self.question=self.content['questions'][0]

    def render(self):
        self.content['content_hash']=fingerprint({key:value for key,value in self.content.items() if key!='content_hash'})
        before=copy.deepcopy(self.content)
        page=render(self.content,ROOT)
        self.assertEqual(self.content,before)
        return page

    def quantity(self,value,unit='%',kind='calculation',metric=None,projection='identity'):
        ident='synthetic_value';text=str(value)+unit
        item=dict(id=ident,output=dict(value=value,unit=unit)) if kind=='calculation' else dict(
            id=ident,value=value,unit=unit,calculation_context=dict(metric=metric))
        content=dict(evidence=[item] if kind=='evidence' else [],calculations=[item] if kind=='calculation' else [])
        record=dict(answer=text,numeric_integrity=dict(fields=dict(answer=dict(text_hash=fingerprint(text),bindings=[
            dict(span=[0,len(text)],display=text,source_kind=kind,ref=ident,unit=unit,path='value',
                 canonical_value=str(value),projection=projection)]))))
        return record,content

    def test_heading_uses_frozen_verified_entity_and_actual_methods(self):
        self.assertEqual(report_heading(self.content), ENTITY+'盈利与现金流')

    def test_user_short_title_is_preserved(self):
        self.content['title']='指定研究标题'
        self.assertEqual(report_heading(self.content),'指定研究标题')

    def test_unused_or_unverified_company_cannot_rename_report(self):
        self.content['calculations']=[]
        for item in self.content['evidence']:item.setdefault('calculation_context',{})['entity_id']='未核验名字'
        self.assertNotIn('未核验名字',report_heading(self.content))

    def test_large_money_and_ratios_are_readable_without_source_change(self):
        text=statement_display_text(self.question,'answer',self.content)
        self.assertIn('840.00亿元',text);self.assertIn('426.30亿元',text)
        self.assertIn('654.63亿元',text);self.assertIn('50.75%',text);self.assertIn('153.56%',text)
        self.assertIn('84000000000.4800元',self.question['answer'])
        self.assertIn('2026年1月1日至2026年6月30日',text)

    def test_numeric_links_keep_exact_original_and_existing_target(self):
        parsed=MainPaper(self.render())
        links=[item for item in parsed.links if item.get('class')=='quantity-link']
        self.assertTrue(links)
        self.assertTrue(any('84,000,000,000.48元' in item['title'] for item in links))
        self.assertTrue(any('84000000000.4800元' in item['data-original-display'] for item in links))
        self.assertTrue(all(item['href'][1:] in parsed.targets for item in links))
        self.assertTrue(all(item['aria-haspopup']=='dialog' for item in links))

    def test_reading_counts_stay_out_of_customer_paper_but_remain_in_record(self):
        page=self.render();text=''.join(MainPaper(page).text)
        notes=reading_diagnostics(self.content)
        self.assertTrue(notes)
        for note in notes:
            self.assertNotIn(note,text)
            self.assertIn(note,page)
        self.assertNotIn('本轮读取',text)
        self.assertIn('原件读取范围',page)
        self.assertNotIn('<details',page)

    def test_business_risk_not_hidden_by_reading_keywords(self):
        risk='本轮读取发现收入确认存在尚未核验的争议，须进一步复核。'
        self.content['optional_findings'].append(risk)
        self.assertIn(risk,''.join(MainPaper(self.render()).text))

    def test_frozen_claim_words_not_shortened_or_rejudged(self):
        original=self.content['theses'][0]
        page=self.render()
        self.assertIn('本期数据支持现金流覆盖报表净利润的判断。回款仍需单独核对。',page)
        self.assertEqual(original['judgment'],'支持')
        self.assertIn(original['title'],page)

    def test_small_percent_never_rounded_to_zero(self):
        record,content=self.quantity(0.000001)
        self.assertEqual(statement_display_text(record,'answer',content),'0.000001%')

    def test_small_money_never_rounded_to_zero(self):
        record,content=self.quantity(0.004,'元','evidence','revenue')
        self.assertEqual(statement_display_text(record,'answer',content),'0.004元')

    def test_negative_amount_preserves_sign(self):
        record,content=self.quantity(-123456789,'元','evidence','net_profit')
        self.assertEqual(statement_display_text(record,'answer',content),'-1.23亿元')

    def test_price_per_unit_is_not_formatted_like_company_revenue(self):
        record,content=self.quantity(1234.5678,'元','evidence','close')
        self.assertEqual(statement_display_text(record,'answer',content),'1234.5678元')

    def test_no_new_statement_when_integrity_or_span_does_not_match(self):
        for change in ('hash','span','value'):
            record,content=self.quantity(12.3456)
            proof=record['numeric_integrity']['fields']['answer']
            if change=='hash':proof['text_hash']='changed'
            elif change=='span':proof['bindings'][0]['span']=[1,len(record['answer'])]
            else:proof['bindings'][0]['canonical_value']='999'
            self.assertEqual(statement_display_text(record,'answer',content),record['answer'])

    def test_nonidentity_projection_not_reinterpreted(self):
        record,content=self.quantity(-12.3456,projection='absolute')
        self.assertEqual(statement_display_text(record,'answer',content),record['answer'])

    def test_missing_reference_is_not_repaired(self):
        record,content=self.quantity(12.3456);content['calculations']=[]
        self.assertEqual(statement_display_text(record,'answer',content),record['answer'])

    def test_prose_and_unknown_html_are_escaped_without_binding_inference(self):
        record=dict(answer='<img src=x onerror=bad()>代码600519.SH，2026年10月16日，比例12.3456%。')
        markup=''.join(statement_paragraphs(record,'answer',dict(evidence=[],calculations=[])))
        self.assertNotIn('<img ',markup);self.assertIn('&lt;img ',markup)
        self.assertIn('12.3456%',markup);self.assertNotIn('quantity-link',markup)

    def test_overlapping_binding_cannot_duplicate_the_text(self):
        record,content=self.quantity(12.3456)
        proof=record['numeric_integrity']['fields']['answer']
        proof['bindings'].append(copy.deepcopy(proof['bindings'][0]))
        self.assertEqual(statement_display_text(record,'answer',content),'12.35%')

    def test_new_reader_identity_and_offline_chart_data_retained(self):
        page=self.render()
        self.assertIn(f"data-reader-version='{READER_VERSION}'",page)
        self.assertIn('84000000000.48',page)
        self.assertIn('42630000000.78',page)
        self.assertIn('65462628001.2',page)
        self.assertEqual(self.content['content_hash'],self.frozen['content_hash'])


if __name__=='__main__':unittest.main()
