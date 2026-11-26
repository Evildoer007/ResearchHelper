"""Statement-wide units require explicit scope and the actual PDF header."""
import copy
from decimal import Decimal
import importlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from researchhelper.contracts import ContractError,calculate,fingerprint
from researchhelper.data_requirements import reported_duration_label
from researchhelper.financial_sources import (financial_table_candidates,financial_context_cells,
    financial_statement_unit_scope,financial_statement_unit_scope_enabled,
    financial_reporting_currency_choices,validate_financial_metric_number,
    financial_reporting_standard_choices,validate_financial_standard_basis,expand_financial_value_references,
    validate_financial_statement_scope)
from researchhelper.pdf_material import duration_statement_part,aligned_statement_part,TABLE_EXTRACT_VERSIONS

fixture=importlib.import_module((__package__+'.' if __package__ else '')+'financial_duration_fixture')


class FinancialStatementUnitScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original=fixture.ORIGINAL.read_bytes()
        cls.fixture=fixture.retained_duration_layout()

    def setUp(self):
        self.task,self.record,self.view,self.words=copy.deepcopy(self.fixture)
        guard=patch('socket.socket.connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(fixture.ORIGINAL.read_bytes(),self.original)

    def cells(self):return financial_table_candidates(self.task,self.record,self.view,maximum=64)['cells']

    def rebind_projection(self):
        self.record['hash']=self.record['snapshot_hash']=fingerprint(self.record['snapshot'])
        self.view['snapshot_hash']=self.record['hash']
        self.view['parts']=copy.deepcopy(self.record['snapshot']['material']['parts'])

    def test_real_numbered_caption_and_stacked_headers_preserve_two_periods(self):
        for number in (67,71):
            values=self.words[number];part=duration_statement_part(number=number,**values)
            self.assertEqual(part['pdf_table']['period_columns'],{
                '2':'截至2026年 6月30日止 6个月期间','3':'截至2025年 6月30日止 6个月期间'})
            self.assertTrue(part['pdf_table']['word_cells'])
            self.assertIsNone(aligned_statement_part(number=number,**values))

    def test_actual_four_metrics_keep_thousand_yuan_and_distinct_periods(self):
        cells=self.cells()
        self.assertEqual(len(cells),8)
        amounts={(c['metric'],c['proposal_fields']['period']):c['proposal_fields']['value'] for c in cells}
        period='截至2026年 6月30日止 6个月期间'
        self.assertEqual({k[0]:v for k,v in amounts.items() if k[1]==period},dict(
            revenue=344815421,operating_cost=279826859,net_profit=12334059,operating_cash_flow=37334744))
        self.assertTrue(all(c['proposal_fields']['raw_unit']==c['proposal_fields']['unit']=='千元' for c in cells))
        self.assertNotIn(12325408,[c['proposal_fields']['value'] for c in cells])

    def test_profit_and_cash_flow_link_their_own_captions_to_the_global_unit(self):
        for part,caption in (('第67页期间列式文本','合并利润表'),('第71页期间列式文本','合并现金流量表')):
            actual=financial_statement_unit_scope(self.record,part)
            self.assertEqual(actual['statement_caption'],caption)
            self.assertEqual(actual['unit_part'],'第65页')
            self.assertIn('财务报表及附注的单位为：千元',actual['unit_quote'])

    def test_actual_presentation_currency_provides_eight_context_candidates_not_approval(self):
        self.assertTrue(financial_reporting_currency_choices(self.record,self.view))
        choices=financial_context_cells(self.task,self.record,self.view)
        self.assertEqual(len(choices),8)
        self.assertTrue(all(c['context']['currency']=='CNY' and c['context']['accounting_scope']=='consolidated'
                            and not c['candidate_is_approval'] for c in choices))
        for c in choices:
            self.assertIn(c['context']['period'],c['bases']['period']['quote'])

    def test_legacy_task_does_not_receive_scoped_candidates(self):
        self.task.pop('financial_statement_unit_scope_version')
        self.assertEqual(self.cells(),[])

    def test_unread_global_declaration_is_not_offered(self):
        self.view['parts']=[p for p in self.view['parts'] if p['locator']!='第65页']
        self.assertEqual(self.cells(),[])

    def test_another_chapter_cannot_supply_the_unit(self):
        for p in self.record['snapshot']['material']['parts']:
            if p['locator']=='第66页':p['text']='第九节 其他事项\n'+p['text']
        self.rebind_projection()
        self.assertEqual(self.cells(),[])

    def test_mere_thousand_yuan_mention_is_not_a_global_declaration(self):
        for p in self.record['snapshot']['material']['parts']:
            if p['locator']=='第65页':p['text']=p['text'].replace('财务报表及附注的单位为：千元','本期捐赠千元金额')
        self.rebind_projection();self.assertEqual(self.cells(),[])

    def test_local_unit_is_not_hidden_by_the_global_declaration(self):
        for p in self.record['snapshot']['material']['parts']:
            if p['locator']=='第67页':p['text']='单位为：万元\n'+p['text']
        self.rebind_projection()
        self.assertIsNone(financial_statement_unit_scope(self.record,'第67页期间列式文本'))

    def test_unscoped_unit_cannot_come_from_another_page_or_a_later_table(self):
        cell=next(cell for cell in self.cells() if cell['metric']=='revenue')
        proposal=copy.deepcopy(cell['proposal_fields'])
        proposal['calculation_context']=dict(metric='revenue',period=proposal['period'])
        locator=proposal['record_locator']
        anchor=dict(part=locator['part'],quote=proposal['text'])
        validate_financial_metric_number(proposal,self.record,anchor,task=self.task)

        locator.pop('financial_unit_scope')
        locator['unit_part']='第7页'
        proposal.update(raw_unit='元',unit='元')
        with self.assertRaises(ContractError):
            validate_financial_metric_number(proposal,self.record,anchor,task=self.task)

        page=next(p for p in self.record['snapshot']['material']['parts'] if p['locator']=='第67页')
        page['text']+='\n其他明细表\n单位：元'
        self.rebind_projection()
        locator['unit_part']='第67页'
        with self.assertRaises(ContractError):
            validate_financial_metric_number(proposal,self.record,anchor,task=self.task)
        self.assertEqual({cell['metric'] for cell in self.cells()},{'operating_cash_flow'})

    def test_local_unit_after_statement_caption_takes_precedence(self):
        page=next(p for p in self.record['snapshot']['material']['parts'] if p['locator']=='第67页')
        page['text']='其他明细表\n单位：元\n'+page['text']
        self.rebind_projection()
        self.assertEqual({cell['metric'] for cell in self.cells()},{'operating_cash_flow'})

        page['text']=page['text'].replace('2、合并利润表','2、合并利润表\n单位：万元')
        self.rebind_projection()
        self.assertEqual({cell['metric']:cell['proposal_fields']['raw_unit'] for cell in self.cells()},
                         dict(revenue='万元',operating_cost='万元',net_profit='万元',operating_cash_flow='千元'))

    def test_altered_scope_reference_is_rejected_at_numeric_validation(self):
        cell=self.cells()[0];proposal=copy.deepcopy(cell['proposal_fields'])
        proposal['calculation_context']=dict(metric=cell['metric'],period=proposal['period'])
        proposal['artifact_id']=self.record['id']
        proposal['record_locator']['financial_unit_scope']['unit']='元'
        with self.assertRaises(ContractError):
            validate_financial_metric_number(proposal,self.record,dict(part=proposal['record_locator']['part'],quote=proposal['text']),task=self.task)

    def test_actual_table_cannot_use_the_inline_path_after_omitting_its_column(self):
        cell = self.cells()[0]
        proposal = copy.deepcopy(cell['proposal_fields'])
        proposal.update(artifact_id=self.record['id'], calculation_context=dict(
            metric=cell['metric'], period=proposal['period'], accounting_scope='consolidated'))
        scope = proposal['record_locator']['financial_unit_scope']
        anchor = dict(part=scope['target_part'], quote=scope['statement_caption'])
        validate_financial_statement_scope(proposal, self.record, anchor)
        proposal['record_locator'].pop('financial_column')
        with self.assertRaises(ContractError):
            validate_financial_statement_scope(proposal, self.record, anchor)

    def test_old_task_cannot_adopt_a_new_scope_reference(self):
        cell=self.cells()[0];proposal=copy.deepcopy(cell['proposal_fields']);proposal['artifact_id']=self.record['id']
        proposal['calculation_context']=dict(metric=cell['metric'],period=proposal['period'])
        self.task.pop('financial_statement_unit_scope_version')
        with self.assertRaises(ContractError):
            validate_financial_metric_number(proposal,self.record,dict(part=proposal['record_locator']['part'],quote=proposal['text']),task=self.task)

    def test_unknown_boolean_and_text_policy_versions_are_rejected(self):
        for value in (True,'1',2,1.0):
            with self.assertRaises(ContractError):financial_statement_unit_scope_enabled(dict(self.task,financial_statement_unit_scope_version=value))

    def test_explicit_duration_does_not_supply_an_invalid_or_missing_period(self):
        self.assertTrue(reported_duration_label('截至2026年 6月30日止 6个月期间'))
        for label in ('截至2026年2月30日止2个月期间','截至2026年6月30日止0个月期间',
                      '截至2026年6月30日止9个月期间','截至2026年6月30日','2026年6个月期间'):
            self.assertFalse(reported_duration_label(label))

    def test_missing_header_duration_or_shifted_value_column_abstains(self):
        original=self.words[67]
        for change in ('duration','position'):
            values=copy.deepcopy(original)
            for w in values['words']:
                if change=='duration' and w['text']=='6个月期间':w['text']='期间不明'
                if change=='position' and w['text']=='344,815,421':w['x0']+=8;w['x1']+=8
            part=duration_statement_part(number=67,**values)
            if change=='duration':self.assertIsNone(part)
            else:self.assertNotIn('344,815,421',part['text'])

    def test_same_unit_arithmetic_reconciles_to_decimal_without_implicit_scaling(self):
        period='截至2026年 6月30日止 6个月期间'
        values={c['metric']:c['proposal_fields']['value'] for c in self.cells() if c['proposal_fields']['period']==period}
        expected={
            'gross_margin_v1':(Decimal(values['revenue'])-Decimal(values['operating_cost']))/Decimal(values['revenue'])*100,
            'net_profit_margin_v1':Decimal(values['net_profit'])/Decimal(values['revenue'])*100,
            'operating_cash_profit_ratio_v1':Decimal(values['operating_cash_flow'])/Decimal(values['net_profit'])*100}
        inputs={
            'gross_margin_v1':dict(revenue=values['revenue'],cost=values['operating_cost']),
            'net_profit_margin_v1':dict(revenue=values['revenue'],net_profit=values['net_profit']),
            'operating_cash_profit_ratio_v1':dict(operating_cash_flow=values['operating_cash_flow'],net_profit=values['net_profit'])}
        for method,data in inputs.items():
            output=calculate(method,data,dict(amount_unit='千元',currency='CNY',period=period,
                accounting_scope='consolidated',accounting_standard='CAS'))
            self.assertLessEqual(abs(Decimal(str(output['value']))-expected[method]),Decimal(str(output['tolerance'])))

    def test_accounting_standard_comes_from_report_preparation_not_share_payment(self):
        choices=financial_reporting_standard_choices(self.record,self.view)
        self.assertTrue(any(c['value']=='CAS' and c['part']=='第80页' for c in choices))
        self.assertFalse(any(c['part']=='第39页' for c in choices))

    def test_share_payment_standard_mention_is_rejected_as_report_basis(self):
        text=next(p['text'] for p in self.view['parts'] if p['locator']=='第39页')
        with self.assertRaises(ContractError):
            validate_financial_standard_basis(dict(calculation_context=dict(accounting_standard='CAS')),
                self.record,dict(part='第39页',quote=text))

    def test_report_preparation_declaration_passes_literal_standard_check(self):
        choice=next(c for c in financial_reporting_standard_choices(self.record,self.view) if c['part']=='第80页')
        validate_financial_standard_basis(dict(calculation_context=dict(accounting_standard='CAS')),
            self.record,dict(part=choice['part'],quote=choice['quote']))

    def test_selected_reference_keeps_scope_through_normal_decoder(self):
        cell=self.cells()[0]
        spec=dict(id='detached_cell_selection',artifact_id=self.record['id'],
            financial_value_reference=cell['candidate_id'],question_ids=['q1'],
            calculation_context=dict(metric=cell['metric'],period=cell['proposal_fields']['period']))
        decoded=expand_financial_value_references(self.task,[spec],[self.view])[0]
        self.assertEqual(decoded['record_locator']['financial_unit_scope'],cell['proposal_fields']['record_locator']['financial_unit_scope'])
        validate_financial_metric_number(decoded,self.record,
            dict(part=decoded['record_locator']['part'],quote=decoded['text']),task=self.task)

    def test_new_policy_is_bound_and_report_frozen(self):
        from researchhelper.archive import ResearchArchive
        from researchhelper.engine import ResearchAgent
        from researchhelper.knowledge import ResearchKnowledge
        from researchhelper.planning import input_fingerprint
        from researchhelper.reports import compose,check_content
        with tempfile.TemporaryDirectory() as folder:
            agent=ResearchAgent(ResearchArchive(Path(folder)),fixture.ROOT,offline=True,require_argument_support=True)
            task=agent.create('核对报表金额',entry='个股',research_mode='current')
            self.assertEqual(task['financial_statement_unit_scope_version'],1)
            old=copy.deepcopy(task);old.pop('financial_statement_unit_scope_version')
            self.assertNotEqual(input_fingerprint(task),input_fingerprint(old))
            task['knowledge']=ResearchKnowledge().read(task['entry'],task['question'],context=task)
            task['status']='部分完成';task['gaps']=['仅离线契约检查。']
            content=compose(task);check_content(content)
            self.assertEqual(content['financial_statement_unit_scope_version'],1)

    def test_old_parse_versions_remain_explicitly_supported(self):
        self.assertTrue({'pdf-tables-2','pdf-tables-3','pdf-tables-4','pdf-tables-5'}<=set(TABLE_EXTRACT_VERSIONS))


if __name__=='__main__':unittest.main()
