"""Data availability is scoped to a security, period and actual original cells."""
import copy
import importlib
import json
import re
import socket
import unittest
from unittest.mock import patch

from researchhelper.financial_data import (FinancialDataUnavailable, monetary_cell,
    proven_disclosures, core_financial_values, capital_expenditure,
    cash_reconciliation, nonrecurring_items, management_discussion,
    half_year_financial_supplements, half_year_financial_data, disclosure_context)
from researchhelper.contracts import fingerprint
SOURCE = importlib.import_module((__package__+'.' if __package__ else '')+'test_financial_data_module').SOURCE


class FinancialDataBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = SOURCE.read_bytes()
        cls.original = json.loads(cls.raw)

    def setUp(self):
        self.task = copy.deepcopy(self.original)
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external I/O'))
        guard.start(); self.addCleanup(guard.stop)
        self.record = next(record for record in proven_disclosures(self.task, '002594.SZ')
                           if '2026' in record['directory']['title'])

    def tearDown(self):
        self.assertEqual(SOURCE.read_bytes(), self.raw)

    def test_eight_consolidated_cells_have_literal_units_and_full_context(self):
        data = core_financial_values(self.task, '002594.SZ', ['20260630', '20250630'])
        self.assertEqual(data['status'], 'decoded', data['missing'])
        self.assertEqual(len(data['values']), 8)
        self.assertEqual({row['unit'] for row in data['values']}, {'千元'})
        self.assertTrue(all(row['context']['context']['accounting_scope'] == 'consolidated'
                            and row['context']['context']['currency'] == 'CNY'
                            and row['accounting_standard'] == 'CAS'
                            and row['context_complete'] for row in data['values']))
        self.assertFalse(data['api_unit_inferred'])
        self.assertFalse(data['financial_adoption'])
        self.assertFalse(data['consolidation_perimeter_comparison_performed'])

    def test_other_security_does_not_receive_byd_values(self):
        data = core_financial_values(self.task, '600519.SH', ['20260630', '20250630'])
        self.assertEqual(data['status'], 'incomplete')
        self.assertEqual(data['values'], [])
        self.assertEqual(len(data['missing']), 8)

    def test_missing_period_preserves_four_current_cells(self):
        data = core_financial_values(self.task, '002594.SZ', ['20260630', '20240630'])
        self.assertEqual(data['status'], 'partial')
        self.assertEqual(len(data['values']), 4)
        self.assertEqual({row['period'] for row in data['missing']}, {'20240630'})

    def test_malformed_period_is_explained_rather_than_raising_type_error(self):
        for periods in (None, ['20260630', []], ['20260630', '20260630'],
                        ['20260630', '20251231']):
            for operation in (core_financial_values, half_year_financial_supplements):
                with self.assertRaises(FinancialDataUnavailable):
                    operation(self.task, '002594.SZ', periods)

    def test_source_from_another_research_version_is_not_reused(self):
        self.record['research_version'] += 1
        with self.assertRaises(FinancialDataUnavailable):
            proven_disclosures(self.task, '002594.SZ')

    def test_incomplete_bridge_does_not_pass_by_its_final_total(self):
        for part in self.record['snapshot']['material']['parts']:
            if part['locator'] == '第161页':
                part['text'] = part['text'].replace('将净利润调节为经营活动现金流量', '区块缺失')
        # This is an intentionally incomplete parser input, not a purported
        # original transport. The separate provenance test covers tampering.
        self.record['hash'] = self.record['snapshot_hash'] = fingerprint(self.record['snapshot'])
        with self.assertRaises(FinancialDataUnavailable):
            cash_reconciliation(self.task, self.record, ['20260630', '20250630'])

    def test_numeric_total_mismatch_is_reported_even_when_rows_are_present(self):
        for part in self.record['snapshot']['material']['parts']:
            if part['locator'] == '第8页表1':
                lines = part['text'].splitlines()
                part['text'] = '\n'.join('合计\t123.00' if line.startswith('合计\t') else line
                                         for line in lines)
        data = nonrecurring_items(self.record)
        self.assertFalse(data['data_checks_passed'])
        self.assertEqual(data['rows'][-1]['value'], '123.00')

    def test_section_without_closing_heading_is_not_called_complete(self):
        for part in self.record['snapshot']['material']['parts']:
            lines = part['text'].splitlines(keepends=True)
            part['text'] = ''.join('章节缺失\n'
                if re.fullmatch(r'第[四五六七八九十]+节[\u4e00-\u9fff、]+', ''.join(line.split()))
                else line for line in lines)
        with self.assertRaises(FinancialDataUnavailable):
            management_discussion(self.record)

    def test_spaces_cannot_concatenate_separate_printed_numbers(self):
        for token in ('1 2', '1 ,234', '(1 2)'):
            with self.assertRaises(FinancialDataUnavailable): monetary_cell(token)

    def test_each_original_proves_its_own_report_context(self):
        for record in proven_disclosures(self.task, '002594.SZ'):
            data = disclosure_context(record)
            self.assertEqual(data['status'], 'decoded', data['missing'])
            self.assertEqual({item['value'] for item in data['currencies']}, {'CNY'})
            self.assertEqual({item['value'] for item in data['accounting_standards']}, {'CAS'})
            self.assertEqual({item['value'] for item in data['statement_units']}, {'千元'})
            self.assertFalse(data['consolidation_perimeter_comparison_performed'])

    def test_combined_data_product_covers_original_cells_but_does_not_answer_research(self):
        before = copy.deepcopy(self.task)
        data = half_year_financial_data(self.task, '002594.SZ', ['20260630', '20250630'])
        self.assertEqual(data['status'], 'ready_for_financial_validation', data['missing_context_periods'])
        self.assertTrue(data['requested_data_decoded'])
        self.assertFalse(data['financial_adoption'])
        self.assertFalse(data['research_resumed'])
        self.assertEqual((data['model_calls'], data['data_requests'], data['pdf_downloads']), (0, 0, 0))
        self.assertEqual(self.task, before)


if __name__ == '__main__':
    unittest.main()
