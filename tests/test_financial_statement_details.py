"""Statement-detail decoding and the existing source/application gates."""
import copy
import importlib
import os
import socket
import unittest
from unittest.mock import patch

fixture=importlib.import_module((__package__+'.' if __package__ else '')+'statement_detail_fixture')
from researchhelper.financial_data import (working_capital_balances,profit_components,
    FinancialDataUnavailable,statement_details_enabled,original_amount_cells,report_comparability_sections)
from researchhelper.reasoning import (evidence_contract,expand_financial_value_references,
    expand_material_publication_references,expand_material_context_references,validate_model_specs)
from researchhelper.source_reader import read,binding,validate_read_specs
from researchhelper.output_schema import original_batch_output_schema,original_batch_prompt,decode_original_batch,_financial_amount_choices
from researchhelper.evidence import candidate_evidence
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError
from researchhelper.contracts import fingerprint,canonical


class FinancialStatementDetailsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw=fixture.SOURCE.read_bytes()
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external I/O'))
        guard.start();cls.addClassCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker=patch.object(MaterialParser,'worker_target',staticmethod(frozen_pdf_worker))
            worker.start();cls.addClassCleanup(worker.stop)
        cls.task,cls.requests,cls.witness=fixture.replay_statement_details()
        cls.records={int(record['directory']['title'].split('年')[0][-4:]):record for record in cls.task['source_records'] if record.get('kind')=='filing'}
        cls.balances={year:working_capital_balances(record) for year,record in cls.records.items()}
        cls.profits=profit_components(cls.records[2026],['20260630','20250630'])
        cls.bundle=read(cls.task,cls.requests,binding(cls.task),reader_version=7)
        cls.views=cls.bundle['views'];cls.contract=evidence_contract(cls.task,cls.views)
        cls.schema=original_batch_output_schema(cls.task,cls.views)
        cls.prompt=original_batch_prompt(dict(untrusted_sources=cls.views,evidence_contract=cls.contract),cls.task)

    @classmethod
    def tearDownClass(cls):assert fixture.SOURCE.read_bytes()==cls.raw

    def test_sixteen_balance_values_preserve_the_four_fields_and_two_dates(self):
        expected={2026:{'accounts_receivable':('50126342','37004685'),'inventory':('189990772','138420580'),
            'accounts_payable':('176356021','186742375'),'contract_liabilities':('79796755','51471055')},
            2025:{'accounts_receivable':('43381542','62298988'),'inventory':('140839351','116036237'),
            'accounts_payable':('235214038','241643424'),'contract_liabilities':('48908232','43729585')}}
        for year,data in self.balances.items():
            self.assertEqual(data['status'],'decoded',data['missing']);self.assertEqual(len(data['values']),8)
            for metric,values in expected[year].items():
                self.assertEqual([next(row['value'] for row in data['values'] if row['metric']==metric and row['period']==day)
                    for day in (str(year)+'0630',str(year-1)+'1231')],list(values))
            self.assertFalse(data['calculation_performed'] or data['financial_adoption'])

    def test_thirty_two_profit_components_keep_finance_cost_and_negative_losses(self):
        self.assertEqual(self.profits['status'],'decoded',self.profits['missing'])
        self.assertEqual(len(self.profits['values']),32)
        selected={(row['period'],row['metric']):row['value'] for row in self.profits['values']}
        self.assertEqual(selected[('20260630','financial_expense')],'5096026')
        self.assertEqual(selected[('20250630','financial_expense')],'-3247033')
        self.assertEqual(selected[('20260630','investment_income')],'-177085')
        self.assertEqual(selected[('20260630','credit_impairment')],'-465413')

    def test_each_value_has_its_own_statement_date_unit_and_exact_quote(self):
        for data in [*self.balances.values(),self.profits]:
            for row in data['values']:
                self.assertEqual(row['unit'],'千元')
                self.assertTrue(all(row[key]['part'] and row[key]['quote'] for key in ('citation','unit_basis','period_basis','statement_basis')))
                self.assertIn('合并',row['statement_basis']['quote'])
                self.assertEqual(len(row['period']),8)

    def test_model_view_retains_text_but_resolves_coordinates_from_original_hashes(self):
        from researchhelper.source_reader import CONTEXT_LIMIT
        self.assertEqual(CONTEXT_LIMIT,80000)
        self.assertLessEqual(len(canonical(self.views)),CONTEXT_LIMIT)
        checked=0
        for view in self.views:
            record=next(record for record in self.task['source_records'] if record['id']==view['id'])
            originals={part['locator']:part for part in record['snapshot']['material']['parts']}
            for part in view['parts']:
                shape=part.get('pdf_table',{})
                if 'word_cells_reference' not in shape:continue
                original=originals[part['locator']]['pdf_table']
                self.assertIn('word_cells',original);self.assertNotIn('word_cells',shape)
                self.assertEqual(shape['word_cells_reference'],fingerprint(original['word_cells']))
                self.assertIn(part['text'],originals[part['locator']]['text']);checked+=1
        self.assertGreater(checked,2)

    def test_reading_projection_policy_is_bound_and_strict(self):
        old=dict(self.task);old.pop('table_geometry_projection_version')
        self.assertNotEqual(binding(old),binding(self.task))
        for value in (True,2,'1',1.0):
            with self.assertRaises(ContractError):binding(dict(old,table_geometry_projection_version=value))

    def test_actual_consolidation_changes_keep_complete_bounded_original_sections(self):
        for record in self.records.values():
            data=report_comparability_sections(record)
            self.assertTrue(data['consolidation_changes']['complete_section'])
            self.assertFalse(data['comparability_approved'])
            self.assertTrue(data['consolidation_changes']['fragments'])

    def test_missing_policy_change_heading_is_not_a_no_change_conclusion(self):
        prior=report_comparability_sections(self.records[2025])['accounting_changes']
        self.assertTrue(prior['complete_section'])
        self.assertIn('质量保证',''.join(fragment['quote'] for fragment in prior['fragments']))
        current=report_comparability_sections(self.records[2026])['accounting_changes']
        self.assertFalse(current['complete_section'] or current['absence_is_proof'])

    def test_absent_balance_row_preserves_the_other_six_values(self):
        record=copy.deepcopy(self.records[2026])
        for part in record['snapshot']['material']['parts']:
            if part.get('pdf_table',{}).get('point_columns'):
                lines=part['text'].splitlines()
                # Leave row addresses stable; an absent label is not zero.
                part['text']='\n'.join('项目缺失'+line[len('应收账款'):] if line.startswith('应收账款\t') else line for line in lines)
        data=working_capital_balances(record)
        self.assertEqual(data['status'],'partial');self.assertEqual(len(data['values']),6)
        self.assertEqual({row['metric'] for row in data['missing']},{'accounts_receivable'})

    def test_changed_coordinate_text_cannot_supply_a_monetary_value(self):
        record=copy.deepcopy(self.records[2026])
        part=next(part for part in record['snapshot']['material']['parts'] if part.get('pdf_table',{}).get('point_columns'))
        part['text']=part['text'].replace('50,126,342','50,126,343')
        with self.assertRaises(FinancialDataUnavailable):working_capital_balances(record)

    def test_old_policy_does_not_offer_new_details_or_rewrite_old_sources(self):
        old=copy.deepcopy(self.task);old.pop('financial_statement_details_version')
        cells=original_amount_cells(old,next(record for record in old['source_records'] if record.get('id')==self.records[2026]['id']))
        self.assertFalse(any(cell['field'] in ('working_capital','profit_components') for cell in cells))
        for value in (True,2,'1',1.0):
            with self.assertRaises(FinancialDataUnavailable):statement_details_enabled(dict(old,financial_statement_details_version=value))

    def selected_specs(self):
        index=next(index for index,view in enumerate(self.views) if view['id']==self.records[2026]['id'])
        source=self.contract['sources'][index];choice=self.prompt['source_choices'][index]
        catalogue=source['financial_amount_tables'];selected=[]
        for reference,cell in _financial_amount_choices(self.task,catalogue).items():
            native=cell['table']['rows'][cell['row']].get('cells',{}).get(cell['column']['reference'],{})
            if native.get('key') not in ('working_capital/20260630/inventory','profit_components/20250630/financial_expense'):continue
            observation=next(item['reference'] for item in self.prompt['untrusted_sources'][index]['observation_choices']
                if item['date']==native['period'][:4]+'-'+native['period'][4:6]+'-'+native['period'][6:])
            selected.append(dict(id='detail_'+str(len(selected)+1),object='002594.SZ',nature='已实现事实',
                amount_reference=reference,observation_reference=observation,review=dict(scope='current_original_descriptive_only',
                need_links={question['id']:([] if question['id']=='q3' else None) for question in self.task['questions']})))
        self.assertEqual(len(selected),2,str(dict(offered_detail_keys=[cell['key'] for table in catalogue['tables']
            for row in table['rows'].values() for cell in row.get('cells',{}).values()
            if cell['field'] in ('working_capital','profit_components')],unread=catalogue.get('unread_supplement_cells',[]),
            coverage=self.bundle['coverage'][index])))
        wire=dict(version=1,binding_ref=self.prompt['batch_binding_ref'],sources=[dict(source_ref=choice['source_ref'],
            selection=dict(version=9,source_reference=choice['binding'],current_version_only=True,
                financial_cells=[],text_quotes=[],amount_cells=selected))])
        specs=decode_original_batch(wire,self.schema,(self.task,self.views))['evidence_specs']
        specs=expand_financial_value_references(self.task,specs,self.views)
        specs=expand_material_publication_references(self.task,specs,self.views)
        return expand_material_context_references(self.task,specs,self.views)

    def test_selected_point_and_negative_expense_pass_the_shared_source_and_applicability_gates(self):
        specs=self.selected_specs();validate_model_specs(self.task,specs);validate_read_specs(self.task,specs,self.bundle)
        checked=[]
        for spec in specs:
            record=next(record for record in self.task['source_records'] if record['id']==spec['artifact_id'])
            item=candidate_evidence(self.task,record,spec)
            checked.append(ResearchAnalyzer().assess(self.task,item,spec['review'],self.task['source_records']))
        self.assertEqual({item['value'] for item in checked},{189990772,-3247033})
        self.assertTrue(all(item['calculation_context'] is None if 'calculation_context' in item else True for item in checked))
        self.assertFalse(self.task['evidence'] or self.task['calculations'])

    def test_model_cannot_inject_a_calculation_metric_into_an_original_detail(self):
        spec=self.selected_specs()[0];spec['calculation_context']={'metric':'net_profit'}
        record=next(record for record in self.task['source_records'] if record['id']==spec['artifact_id'])
        with self.assertRaises(ContractError):candidate_evidence(self.task,record,spec)


if __name__=='__main__':unittest.main()
