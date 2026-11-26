"""Exact cell parsing and corroboration. Fixtures are synthetic, never disclosures."""
from copy import deepcopy
from decimal import Decimal,localcontext
from pathlib import Path
import unittest
from unittest.mock import patch
from researchhelper.financial_data import monetary_cell,core_financial_values,FinancialDataUnavailable


def controlled_cells(tokens, *,units=None,scopes=None,currencies=None,standards=None):
    """Isolate the comparison with explicit parser outputs, not transport evidence.

    The real retained-original chain is checked separately. These test doubles
    never grant a financial approval or create a provider acquisition record.
    """
    task=dict(id='synthetic-corroboration',version=1,batch=0,cutoff='2026-08-01',input_hash='fixture')
    records=[];tables={};contexts={};shared={}
    for index,token in enumerate(tokens):
        ident='synthetic-original-'+str(index);part='第1页';quote='合成金额 '+token
        record=dict(id=ident,kind='filing',hash='snapshot-'+str(index),snapshot=dict(material=dict(hash='original-'+str(index),parts=[dict(locator=part,text=quote)])))
        key='synthetic-cell-'+str(index);unit=units[index] if units else '元'
        fields=dict(period='截至2026年6月30日止6个月期间',raw_token=token,raw_unit=unit,text=quote,record_locator=dict(part=part,financial_unit_scope=dict(part=part,quote=quote)))
        tables[ident]=dict(cells=[dict(metric='revenue',candidate_id=key,proposal_fields=fields)])
        contexts[ident]=[dict(financial_value_reference=key,context=dict(accounting_scope=scopes[index] if scopes else 'consolidated',currency=currencies[index] if currencies else 'CNY'))]
        shared[ident]=dict(entity_id=[dict(value='合成主体',basis=dict(part=part,quote=quote))],accounting_standard=[dict(value=standards[index] if standards else 'CAS',basis=dict(part=part,quote=quote))])
        records.append(record)
    return task,records,tables,contexts,shared


def reconcile(tokens,**options):
    task,records,tables,contexts,shared=controlled_cells(tokens,**options);before=deepcopy((task,records,tables,contexts,shared))
    with patch('researchhelper.financial_data.proven_disclosures',return_value=records), \
         patch('researchhelper.financial_data.financial_table_candidates',side_effect=lambda task,record,view,maximum:deepcopy(tables[record['id']])), \
         patch('researchhelper.financial_data.financial_context_cells',side_effect=lambda task,record,view:deepcopy(contexts[record['id']])), \
         patch('researchhelper.financial_data.financial_shared_context_choices',side_effect=lambda task,record,view:deepcopy(shared[record['id']])):
        result=core_financial_values(task,'999991.SH',['20260630','20250630'])
    assert (task,records,tables,contexts,shared)==before
    return result


class FinancialCellEquivalenceTests(unittest.TestCase):
    def test_equivalent_decimal_spellings_corroborate_without_changing_quotes(self):
        tokens=['1,000','1000.00','+1000.000']
        result=reconcile(tokens)
        self.assertEqual(len(result['values']),1,result['missing'])
        row=result['values'][0]
        self.assertEqual(Decimal(row['value']),Decimal('1000'))
        self.assertEqual([item['raw_token'] for item in row['corroborating_original_cells']],tokens)
        self.assertEqual([item['citation']['artifact_id'] for item in row['corroborating_original_cells']],['synthetic-original-0','synthetic-original-1','synthetic-original-2'])
        self.assertFalse(result['financial_adoption'] or result['api_unit_inferred'])
        self.assertEqual(result['status'],'partial')

    def test_signed_equivalent_amounts_and_zero_are_not_false_conflicts(self):
        for tokens in [['(1,000)','-1000.00','−1000.000'],['0','0.00','(0.000)']]:
            with self.subTest(tokens=tokens):
                result=reconcile(tokens);self.assertEqual(len(result['values']),1,result['missing'])
                self.assertEqual(len(result['values'][0]['corroborating_original_cells']),3)

    def test_real_amount_difference_is_still_an_open_conflict(self):
        for tokens in [['1000','1000.01'],['(1000)','1000'],['12345678901234567890.12345678901','12345678901234567890.12345678902']]:
            with self.subTest(tokens=tokens):
                result=reconcile(tokens);self.assertEqual(result['values'],[])
                gap=next(row for row in result['missing'] if row['period']=='20260630' and row['metric']=='revenue')
                self.assertEqual(len(gap['candidates']),2)

    def test_equal_numbers_do_not_merge_incompatible_financial_context(self):
        for options in [dict(units=['元','千元']),dict(currencies=['CNY','USD']),dict(scopes=['consolidated','parent']),dict(standards=['CAS','IFRS'])]:
            with self.subTest(options=options):self.assertEqual(reconcile(['1000','1000.00'],**options)['values'],[])

    def test_missing_cells_remain_missing_while_an_available_cell_survives(self):
        result=reconcile(['1000','1000.00'])
        self.assertEqual(len(result['values']),1);self.assertEqual(len(result['missing']),7)
        self.assertEqual({row['period'] for row in result['values']},{'20260630'})
        self.assertTrue(all(row['metric']!='revenue' or row['period']!='20260630' for row in result['missing']))

    def test_parenthesized_amount_is_exact_under_the_host_decimal_context(self):
        values=['(1,234.5678)','(12345678901234567890.12345678901234567890)']
        for precision in [6,28,50]:
            with localcontext() as context:
                context.prec=precision
                for token in values:
                    with self.subTest(precision=precision,token=token):
                        expected=Decimal('-'+token[1:-1].replace(',',''))
                        actual=monetary_cell(token)
                        self.assertEqual(actual.as_tuple(),expected.as_tuple())

    def test_current_filing_parser_preserves_the_exact_amount_and_its_basis(self):
        from researchhelper.draft_filing_amounts import _consolidated_cells
        from researchhelper.pdf_material import TEXT_TABLE_VERSION

        token = '(12345678901234567890.12345678901234567890)'
        period = '2026 年 1 月 1 日至 6 月 30 日止期间'
        lines = ['合并利润表', period, '（金额单位均为人民币元）',
                 '项目\t2026年\t2025年', '营业收入\t'+token+'\t1000.00']
        parts = [
            dict(locator='第9页', text='\n'.join(lines)),
            dict(locator='第9页列式文本', text='\n'.join(lines),
                 pdf_table=dict(page=9, version=TEXT_TABLE_VERSION,
                    representation='aligned_words', geometry_only=True,
                    header_row=3, period_columns={'1': period},
                    word_cells={'4': [[dict(text=cell)] for cell in lines[4].split('\t')]})),
        ]
        before = deepcopy(parts)
        expected = Decimal('-'+token[1:-1])
        with localcontext() as context:
            context.prec = 6
            rows = list(_consolidated_cells(parts, '核对营业收入', bound_scope=True))
        self.assertEqual(len(rows), 1)
        row, = rows
        self.assertEqual(row['value'].as_tuple(), expected.as_tuple())
        self.assertEqual((row['raw_token'], row['unit'], row['period']),
                         (token, '元', '2026-01-01/2026-06-30'))
        self.assertEqual(row['locator']['raw_token'], token)
        self.assertEqual(row['locator']['part'], '第9页列式文本')
        self.assertEqual(row['locator']['quote'], lines[4])
        self.assertEqual(parts, before)

    def test_invalid_and_missing_original_tokens_are_not_repaired(self):
        for token in ['',None,'—','12,34','(-100)','(1 2)']:
            with self.subTest(token=token),self.assertRaises(FinancialDataUnavailable):monetary_cell(token)
