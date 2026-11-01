"""Independent real-PDF accounting-scope association, with no adopted facts."""
import copy
from pathlib import Path
import unittest

from researchhelper.contracts import ContractError,fingerprint
from researchhelper.evidence import validate_calculation_context_basis
from researchhelper.financial_sources import validate_financial_statement_scope,_statement_titles
# The isolated Skill/frozen runner loads top-level fixture modules and denies
# the development bootstrap. Source-mode unittest uses the tests package.
if __package__:
    from . import test_pdf_financial_tables as pdf_fixture
else:
    import test_pdf_financial_tables as pdf_fixture

ROOT=Path(__file__).resolve().parents[1]


class FinancialStatementScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pdf_fixture.PdfFinancialTableTests.setUpClass()
        cls.fixture=pdf_fixture.PdfFinancialTableTests()
        cls.record=dict(cls.fixture.record,task_id='scope_audit',research_version=1,
                        snapshot_hash=fingerprint(cls.fixture.material))

    @classmethod
    def tearDownClass(cls): pdf_fixture.PdfFinancialTableTests.tearDownClass()

    def proposal(self,scope='consolidated',parent=False,income=False,anchor_part=None,anchor_quote=None):
        if income:
            row_part,header_part,unit_part=('第32页表2','第32页表2','第32页') if parent else ('第30页表2','第30页表2','第30页')
            term,metric='营业收入','revenue'
        else:
            row_part,header_part,unit_part=('第35页表2','第35页表2','第35页') if parent else ('第34页表1','第33页表2','第33页')
            term,metric='经营活动产生的现金流量净','operating_cash_flow'
        row=self.fixture.row(row_part,term)
        item=self.fixture.proposal(row_part,row,metric=metric,token=row.split('\t')[2],header=header_part,unit_part=unit_part)
        item.update(task_id='scope_audit',research_version=1)
        item['calculation_context']['accounting_scope']=scope
        basis=lambda part,quote:dict(kind='material',artifact_id=self.record['id'],
            snapshot_hash=self.record['snapshot_hash'],part=part,quote=quote)
        if anchor_part is None: anchor_part=unit_part
        if anchor_quote is None:
            anchor_quote=('合并' if scope=='consolidated' else '母公司')+('利润表' if income else '现金流量表')
        item['calculation_context_basis']=dict(
            metric=basis(row_part,row),period=basis(header_part,self.fixture.by_id[header_part]['text'].split('\n')[0]),
            accounting_scope=basis(anchor_part,anchor_quote))
        return item

    def validate(self,item,records=None): validate_calculation_context_basis(item,[self.record] if records is None else records)

    def test_actual_consolidated_cash_flow_keeps_its_own_scope(self): self.validate(self.proposal())

    def test_actual_parent_cash_flow_can_be_used_only_as_parent_scope(self): self.validate(self.proposal(scope='parent',parent=True))

    def test_parent_amount_cannot_borrow_consolidated_cash_flow_heading(self):
        with self.assertRaises(ContractError): self.validate(self.proposal(parent=True,anchor_part='第33页'))

    def test_consolidated_amount_cannot_borrow_parent_cash_flow_heading(self):
        with self.assertRaises(ContractError): self.validate(self.proposal(scope='parent',anchor_part='第35页'))

    def test_scope_cannot_borrow_another_original_even_with_identical_bytes(self):
        item=self.proposal();foreign=copy.deepcopy(self.record);foreign['id']='another_original'
        item['calculation_context_basis']['accounting_scope']['artifact_id']=foreign['id']
        with self.assertRaises(ContractError): self.validate(item,[self.record,foreign])

    def test_cash_flow_scope_cannot_borrow_profit_statement_heading(self):
        with self.assertRaises(ContractError): self.validate(self.proposal(anchor_part='第30页',anchor_quote='合并利润表'))

    def test_actual_consolidated_revenue_keeps_its_own_statement(self): self.validate(self.proposal(income=True))

    def test_actual_parent_revenue_keeps_its_own_statement(self): self.validate(self.proposal(scope='parent',parent=True,income=True))

    def test_parent_revenue_cannot_borrow_consolidated_profit_heading(self):
        with self.assertRaises(ContractError): self.validate(self.proposal(parent=True,income=True,anchor_part='第30页'))

    def test_consolidated_revenue_cannot_borrow_parent_profit_heading(self):
        with self.assertRaises(ContractError): self.validate(self.proposal(scope='parent',income=True,anchor_part='第32页'))

    def test_metric_only_internal_check_is_not_upgraded_to_full_financial_approval(self):
        item=self.proposal();item['calculation_context'].pop('accounting_scope')
        item['calculation_context_basis'].pop('accounting_scope');self.validate(item)
        self.assertNotIn('source_check',item);self.assertNotIn('applicability_check',item)

    def test_pdf_cannot_downgrade_to_plain_column_protocol(self):
        item=self.proposal();item['record_locator']['financial_column']['version']=1
        with self.assertRaisesRegex(ContractError,'不能退回普通文本'): self.validate(item)

    def test_ambiguous_same_page_titles_do_not_select_a_scope(self):
        # Caption routing only: this modified local page is never submitted to
        # original-byte verification or accepted as an actual source.
        record=copy.deepcopy(self.record)
        page=next(part for part in record['snapshot']['parts'] if part['locator']=='第33页')
        page['text']+='\n母公司现金流量表\n'
        item=self.proposal()
        with self.assertRaisesRegex(ContractError,'不唯一'):
            validate_financial_statement_scope(item,record,item['calculation_context_basis']['accounting_scope'])

    def test_prose_mentions_do_not_become_statement_captions(self):
        text='说明：并非母公司现金流量表。\n合并现金流量表\n参见合并利润表。'
        self.assertEqual(_statement_titles(text,'现金流量表'),{'合并现金流量表'})

    def test_own_table_caption_can_disambiguate_a_shared_page(self):
        # Pure locator semantics on synthetic geometry; not a parser bypass.
        record=copy.deepcopy(self.record)
        parts=record['snapshot']['parts']
        next(part for part in parts if part['locator']=='第33页')['text']+='\n母公司现金流量表\n'
        heading=next(part for part in parts if part['locator']=='第33页表2')
        heading['text']='合并现金流量表\n'+self.fixture.by_id['第33页表2']['text']
        heading['pdf_table']['ambiguous_rows']=[0]+[index+1 for index in heading['pdf_table']['ambiguous_rows']]
        item=self.proposal(anchor_part='第33页表2')
        validate_financial_statement_scope(item,record,item['calculation_context_basis']['accounting_scope'])


if __name__=='__main__': unittest.main()
