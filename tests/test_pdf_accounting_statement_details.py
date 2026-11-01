"""Observed accounting glyphs and balance-sheet columns from retained PDFs."""
import base64
import copy
import io
import json
import unittest

import pdfplumber
from tests import ROOT
from researchhelper.pdf_material import (duration_statement_part,point_statement_part,
    TABLE_EXTRACT_VERSIONS,_accounting_chunk)
from researchhelper.financial_data import monetary_cell

SOURCE=ROOT/'result/acceptance/one-sentence-research-8s4to77o/task.json'


class PDFAccountingStatementDetailsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_bytes=SOURCE.read_bytes();task=json.loads(cls.original_bytes)
        records=[record for record in task['source_records'] if record.get('kind')=='filing']
        cls.pages={};cls.legacy={}
        for record in records:
            year=2026 if '2026年' in record['directory']['title'] else 2025
            numbers=(65,66,67) if year==2026 else (63,64,65)
            cls.legacy[year]=next((part for part in record['snapshot']['material']['parts']
                if part['locator']==f'第{numbers[-1]}页期间列式文本'),None)
            with pdfplumber.open(io.BytesIO(base64.b64decode(record['snapshot']['material']['original_base64']))) as document:
                for number in numbers:
                    page=document.pages[number-1]
                    cls.pages[(year,number)]=(page.extract_words(x_tolerance=1,y_tolerance=2,keep_blank_chars=False,return_chars=True),page.width,page.height,number)

    @classmethod
    def tearDownClass(cls):
        assert SOURCE.read_bytes()==cls.original_bytes

    def profit(self,year,*,modern=True,words=None):
        entry=self.pages[(year,67 if year==2026 else 65)]
        return duration_statement_part(words if words is not None else entry[0],*entry[1:],accounting_parentheses=modern)

    def test_negative_expense_and_investment_preserve_the_original_parentheses(self):
        result=self.profit(2026)
        expense=next(line.split('\t') for line in result['text'].splitlines() if line.startswith('财务费用\t'))
        investment=next(line.split('\t') for line in result['text'].splitlines() if line.startswith('投资(损失)/收益\t'))
        self.assertEqual([monetary_cell(value) for value in expense[-2:]],[5096026,-3247033])
        self.assertEqual([monetary_cell(value) for value in investment[-2:]],[-177085,1375879])

    def test_single_word_accounting_amounts_are_read_from_their_digit_edge(self):
        result=self.profit(2025)
        row=next(line.split('\t') for line in result['text'].splitlines() if line.startswith('财务费用\t'))
        self.assertEqual([monetary_cell(value) for value in row[-2:]],[-3247033,69006])

    def test_legacy_geometry_is_reproduced_without_upgrading_old_material(self):
        for year in (2025,2026):self.assertEqual(self.profit(year,modern=False),self.legacy[year])
        self.assertTrue({'pdf-tables-2','pdf-tables-3','pdf-tables-4','pdf-tables-5','pdf-tables-6'}<=set(TABLE_EXTRACT_VERSIONS))

    def test_detached_closing_glyph_does_not_supply_an_amount(self):
        words=copy.deepcopy(self.pages[(2026,67)][0])
        expense=next(word for word in words if word['text']=='财务费用')
        closing=next(word for word in words if word['text']==')' and abs(word['top']-expense['top'])<1)
        closing['x0']+=10;closing['x1']+=10
        result=self.profit(2026,words=words)
        self.assertFalse(any(line.startswith('财务费用\t') for line in result['text'].splitlines()))

    def test_missing_second_column_cannot_turn_a_note_number_into_an_amount(self):
        words=self.pages[(2026,67)][0];expense=next(word for word in words if word['text']=='财务费用')
        filtered=[word for word in words if not (abs(word['top']-expense['top'])<1 and word['x0']>460)]
        result=self.profit(2026,words=filtered)
        self.assertFalse(any(line.startswith('财务费用\t') for line in result['text'].splitlines()))
        self.assertTrue(any('营业收入' in line for line in result['text'].splitlines()))

    def test_balance_sheet_dates_and_continuation_retain_note_and_amount_columns(self):
        for year,start in ((2026,65),(2025,63)):
            assets=point_statement_part(*self.pages[(year,start)])
            state=dict(page=start,caption=assets['pdf_table']['statement_caption'],dates=assets['pdf_table']['dates'])
            liabilities=point_statement_part(*self.pages[(year,start+1)],continuation=state)
            self.assertEqual(assets['pdf_table']['dates'],[f'{year}-06-30',f'{year-1}-12-31'])
            self.assertEqual(liabilities['pdf_table']['statement_caption']['part'],f'第{start}页')
            self.assertTrue(any(line.startswith('应收账款\t3\t') for line in assets['text'].splitlines()))
            self.assertTrue(any(line.startswith('合同负债\t') for line in liabilities['text'].splitlines()))

    def test_continuation_without_the_caption_or_same_dates_is_rejected(self):
        entry=self.pages[(2026,66)]
        self.assertIsNone(point_statement_part(*entry))
        self.assertIsNone(point_statement_part(*entry,continuation=dict(page=65,caption=dict(text='1、合并资产负债表',part='第65页'),dates=['2025-06-30','2024-12-31'])))

    def test_an_unproved_single_word_parenthesis_cannot_expand_a_column(self):
        word=dict(text='(123)',x0=10,x1=35,top=10,bottom=20)
        self.assertIsNone(_accounting_chunk(dict(words=[word]),0,30,100))


if __name__=='__main__':unittest.main()
