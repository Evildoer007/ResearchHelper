"""Generic financial amounts from retained originals, no paid research rerun.

Explicit selections are forensic controls, not the discarded model response.
No requirement, value, unit, period or original study status is rewritten.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch
from researchhelper.contracts import ContractError, registered_financial_bindings, fingerprint
from researchhelper.data_requirements import pending
from researchhelper.financial_sources import (financial_amount_tables, financial_table_candidates, _financial_amount_unit,
    _financial_amount_row_is_monetary, _financial_current_period_unambiguous, _financial_amount_period)
from researchhelper.output_schema import (financial_reference_output_schema, financial_reference_prompt,
    decode_financial_references, original_batch_output_schema, original_batch_prompt, decode_original_batch,
    _matches, ORIGINAL_SELECTION_REJECTIONS)
from researchhelper.reasoning import evidence_contract, validate_model_specs
from researchhelper.evidence import candidate_evidence
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.source_reader import validate_read_specs

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('RESEARCHHELPER_FINANCIAL_BATCH_INPUT', str(
    ROOT/'result/acceptance/one-sentence-research-2l129r9v/task.json')))


class FinancialAmountSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = SOURCE.read_bytes()
        cls.original = json.loads(cls.raw)

    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline originals only'))
        guard.start(); self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start(); self.addCleanup(worker.stop)
        self.task = copy.deepcopy(self.original)
        self.task.update(financial_amount_selection_version=1, independent_financial_selections_version=1)
        self.bundle = self.task['reasoning_state']['reading']['bundle']
        self.views = self.bundle['views']
        self.record = next(record for record in self.task['source_records'] if record['id'] == self.views[0]['id'])
        self.contract = evidence_contract(self.task, self.views)
        self.catalogue = self.contract['sources'][0]['financial_amount_tables']
        self.schema = financial_reference_output_schema(self.task, self.views)
        self.payload = dict(untrusted_sources=self.views, evidence_contract=self.contract)
        self.prompt = financial_reference_prompt(self.payload, self.task)
        self.need = next(text for question in self.task['questions'] if question['id'] == 'q2'
                         for text, rule in question['evidence_need_constraints'].items() if rule['value_kind'] == 'number')

    def tearDown(self):
        self.assertEqual(SOURCE.read_bytes(), self.raw)

    def selection(self, needle, index):
        table, reference = next((table, reference) for table in self.catalogue['tables']
            for reference, row in table['rows'].items() if needle in row['text'].split('\t')[0])
        column = next(column for column in table['columns'] if column['column_index'] == 1
                      and reference in column['row_references'])
        observation = next(choice for choice in self.prompt['untrusted_sources'][0]['observation_choices']
                           if choice['date'] == '2026-06-30')
        return dict(id='amount_control_'+str(index), object=self.record['directory']['issuer'],
            nature='已实现事实', table_reference=table['reference'], column_reference=column['reference'],
            row_reference=reference, observation_reference=observation['reference'],
            review=dict(scope='current_original_descriptive_only',
                        need_links={qid: ([fingerprint(self.need)] if qid == 'q2' else None)
                                    for qid in self.contract['question_ids']}))

    def wire(self):
        return dict(version=9, source_reference=self.prompt['financial_reference_binding'],
            current_version_only=True, financial_cells=[], text_quotes=[], amount_cells=[
                self.selection(needle, index) for index, needle in enumerate(
                    ('固定资产折旧', '存货的减少', '经营性应收', '经营性应付'), 1)])

    def decode(self, wire=None):
        return decode_financial_references(self.wire() if wire is None else wire,
                                           self.schema, (self.task, self.views))['evidence_specs']

    def test_four_original_adjustments_restore_sign_unit_period_and_column_without_metric(self):
        wire = self.wire(); before = copy.deepcopy(wire)
        self.assertTrue(_matches(wire, self.schema))
        specs = self.decode(wire)
        self.assertEqual([item['value'] for item in specs], [14282273, -37859085, -33026925, 71690439])
        self.assertTrue(all(item['unit'] == '千元' and item['period'] == '2026年半年度' for item in specs))
        self.assertTrue(all(item['record_locator']['financial_amount_column']['column_index'] == 1 for item in specs))
        self.assertTrue(all('calculation_context' not in item and 'source_check' not in item for item in specs))
        self.assertEqual(wire, before)

    def test_actual_source_applicability_and_reading_gates_cover_the_existing_numeric_need(self):
        before = copy.deepcopy(self.task['ledger'])
        specs = self.decode()
        validate_model_specs(self.task, specs)
        validate_read_specs(self.task, specs, self.bundle)
        analyzer = ResearchAnalyzer()
        for spec in specs:
            item = analyzer.assess(self.task, candidate_evidence(self.task, self.record, spec), spec['review'],
                                   self.task['source_records'])
            self.assertEqual((item['source_check'], item['applicability_check']), ('通过', '通过'))
            self.task['evidence'].append(item)
        self.assertFalse(any(item['question_id'] == 'q2' and item['text'] == self.need for item in pending(self.task)))
        self.assertEqual(self.task['ledger'], before)
        self.assertEqual(self.task['status'], '部分完成')
        self.assertIsNone(registered_financial_bindings(self.task, ['q1']))
        self.assertFalse(self.task.get('argument_support_reviews'))

    def test_opposite_column_sign_unit_period_geometry_and_calculation_injection_are_rejected(self):
        base = self.decode()[1]
        for issue in ('column', 'sign', 'unit', 'period', 'geometry', 'context', 'title_only'):
            spec = copy.deepcopy(base)
            if issue == 'column': spec['record_locator']['financial_amount_column']['column_index'] = 2
            elif issue == 'sign':
                spec.update(raw_token='37,859,085', raw_value=37859085, value=37859085)
            elif issue == 'unit': spec.update(raw_unit='元', unit='元')
            elif issue == 'period': spec['period'] = '2025年半年度'
            elif issue == 'geometry': spec['record_locator']['financial_amount_column']['header_part'] = '第77页表2'
            elif issue == 'title_only':
                spec['record_locator']['financial_amount_column'].update(
                    period_basis_part='第156页', period_basis_quote=spec['record_locator']['financial_amount_column']['report_period_quote'])
            else: spec['calculation_context'] = dict(metric='net_profit')
            with self.subTest(issue=issue), self.assertRaises(ContractError):
                candidate_evidence(self.task, self.record, spec)

    def test_batch_quarantines_unknown_raw_address_but_retains_three_independent_amounts(self):
        schema = original_batch_output_schema(self.task, self.views)
        prompt = original_batch_prompt(self.payload, self.task)
        selected = self.wire(); selected['amount_cells'][0]['row_reference'] = 'line_unknown'
        wire = dict(version=1, binding_ref=prompt['batch_binding_ref'],
            sources=[dict(source_ref=prompt['source_choices'][0]['source_ref'], selection=selected)])
        before = copy.deepcopy(wire)
        result = decode_original_batch(wire, schema, (self.task, self.views))
        self.assertEqual(len(result['evidence_specs']), 3)
        self.assertEqual(result[ORIGINAL_SELECTION_REJECTIONS]['accepted_items'], 3)
        self.assertEqual(result[ORIGINAL_SELECTION_REJECTIONS]['rejected_items'][0]['location'],
                         '$.sources[0].selection.amount_cells[0]')
        self.assertEqual(wire, before)

    def test_catalogue_exposes_actual_row_addresses_not_registered_metric_defaults(self):
        self.assertTrue(self.catalogue['scan_complete'])
        for table in self.catalogue['tables']:
            for column in table['columns']:
                if column['mode'] == 'report_current':
                    self.assertNotIn('上期', column['label'])
                    self.assertNotIn('余额', column['label'])
        self.assertTrue(self.prompt['financial_amount_rule'])
        self.assertNotIn('financial_amount_tables', self.prompt['evidence_contract']['sources'][0])
        self.assertEqual(self.schema['properties']['source_reference']['properties']['binding_ref']['enum'][0],
                         self.prompt['financial_reference_binding']['binding_ref'])

    def test_legacy_contract_and_unknown_policy_do_not_accept_the_new_amount_wire(self):
        old = copy.deepcopy(self.task); old.pop('financial_amount_selection_version')
        schema = financial_reference_output_schema(old, self.views)
        self.assertNotIn('amount_cells', schema['properties'])
        self.assertFalse(_matches(self.wire(), schema))
        for value in (0, True, 2, '1'):
            task = copy.deepcopy(self.task); task['financial_amount_selection_version'] = value
            with self.subTest(value=value), self.assertRaises(ContractError):
                financial_amount_tables(task, self.record, self.views[0])

    def test_unit_declaration_belongs_to_the_selected_table_not_another_table_on_the_page(self):
        page = '单位：千元\n项目甲 本期金额 上期金额\n甲 100 90\n单位：万元\n项目乙 本期金额 上期金额\n乙 2 1\n'
        self.assertEqual(_financial_amount_unit(page, '项目甲\t本期金额\t上期金额')['unit'], '千元')
        self.assertEqual(_financial_amount_unit(page, '项目乙\t本期金额\t上期金额')['unit'], '万元')
        non_amount = page.replace('单位：万元', '单位：台')
        self.assertIsNone(_financial_amount_unit(non_amount, '项目乙\t本期金额\t上期金额'))
        ambiguous = page.replace('项目乙', '项目甲')
        self.assertIsNone(_financial_amount_unit(ambiguous, '项目甲\t本期金额\t上期金额'))

    def test_explicit_per_share_quantity_and_rate_rows_never_inherit_the_amount_header(self):
        for label in ('基本每股收益', '稀释每股收益', '回购股份数量', '净资产收益率', '毛利率（%）', '毛利率', '平均单价'):
            self.assertFalse(_financial_amount_row_is_monetary(label))
        self.assertTrue(_financial_amount_row_is_monetary('股本'))
        self.assertTrue(_financial_amount_row_is_monetary('存货的减少（增加以负号填列）'))

    def test_report_title_does_not_invent_quarter_ytd_or_point_balance_scope(self):
        self.assertTrue(_financial_current_period_unambiguous('2026年半年度'))
        self.assertFalse(_financial_current_period_unambiguous('2026年第三季度'))
        self.assertFalse(_financial_current_period_unambiguous('2026年第二季度'))
        self.assertIsNone(_financial_amount_period('本期余额', '第1页', '2026年半年度报告全文', '第1页表1'))

    def test_existing_typed_calculation_cells_do_not_get_a_weaker_raw_alternative(self):
        typed = financial_table_candidates(self.task, self.record, self.views[0])['cells']
        addresses = {(cell['proposal_fields']['record_locator']['part'],
                      cell['proposal_fields']['record_locator']['financial_column']['row_quote'],
                      cell['proposal_fields']['record_locator']['financial_column']['column_index']) for cell in typed}
        self.assertTrue(addresses)
        raw = {(table['part'], table['rows'][reference]['text'], column['column_index'])
               for table in self.catalogue['tables'] for column in table['columns']
               for reference in column['row_references']}
        self.assertFalse(addresses.intersection(raw))

    def test_unread_report_caption_is_not_supplied_from_the_hidden_original(self):
        view = copy.deepcopy(self.views[0])
        page = next(part for part in view['parts'] if part['locator'] == '第156页')
        start = page['text'].index('67、现金流量表补充资料')
        page['text'] = page['text'][start:]
        catalogue = financial_amount_tables(self.task, self.record, view)
        self.assertFalse(any(table['part'] in ('第156页表3', '第157页表1') for table in catalogue['tables']))


if __name__ == '__main__':
    unittest.main()
