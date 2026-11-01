"""Single-module checks on actual retained BYD disclosures, zero external I/O."""
import copy
from decimal import Decimal
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from researchhelper.financial_data import (FinancialDataUnavailable, monetary_cell,
    proven_disclosures, capital_expenditure, cash_reconciliation, nonrecurring_items,
    management_discussion, half_year_financial_supplements)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'result/acceptance/one-sentence-research-bx8iekji/task.json'


class FinancialDataModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = SOURCE.read_bytes()
        cls.original = json.loads(cls.raw)

    def setUp(self):
        self.task = copy.deepcopy(self.original)
        blocker = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external I/O'))
        blocker.start(); self.addCleanup(blocker.stop)
        records = proven_disclosures(self.task, '002594.SZ')
        self.record = next(record for record in records if '2026' in record['directory']['title'])

    def tearDown(self):
        self.assertEqual(SOURCE.read_bytes(), self.raw)

    def test_capex_both_periods_from_the_actual_consolidated_cashflow_table(self):
        data = capital_expenditure(self.task, self.record, ['20260630', '20250630'])
        self.assertEqual([(row['period'], row['value'], row['unit']) for row in data['values']],
            [('20260630', '44703066', '千元'), ('20250630', '80524516', '千元')])
        self.assertTrue(all(row['citation']['part'] == '第71页期间列式文本' for row in data['values']))
        self.assertFalse(data['financial_adoption'])

    def test_cashflow_bridge_is_complete_and_reconciles_both_columns(self):
        data = cash_reconciliation(self.task, self.record, ['20260630', '20250630'])
        self.assertTrue(data['data_checks_passed'])
        self.assertGreater(len(data['rows']), 15)
        self.assertEqual(data['rows'][0]['label'], '净利润')
        self.assertEqual(data['checks']['20260630']['disclosed_cash_flow'], '37334744')
        self.assertEqual(data['checks']['20250630']['disclosed_cash_flow'], '31833471')
        inventory = next(row for row in data['rows'] if '存货的增加' in row['label'])
        self.assertEqual(inventory['values']['20260630'], '-53231176')
        self.assertEqual(inventory['values']['20250630'], '-25987262')
        self.assertFalse(data['financial_adoption'])

    def test_parentheses_and_missing_cells_preserve_sign_and_absence(self):
        self.assertEqual(str(monetary_cell('(225,782 )')), '-225782')
        self.assertEqual(str(monetary_cell('（2,207,606)')), '-2207606')
        for value in ('', '-', '—', '12,34', '(-100)', '1e8', None):
            with self.assertRaises(FinancialDataUnavailable): monetary_cell(value)

    def test_changed_original_snapshot_is_not_a_data_source(self):
        self.record['snapshot']['material']['parts'][0]['text'] += 'changed'
        with self.assertRaises(FinancialDataUnavailable):
            proven_disclosures(self.task, '002594.SZ')

    def test_data_decoding_does_not_change_task_or_spend_budget(self):
        before = copy.deepcopy(self.task)
        capital_expenditure(self.task, self.record, ['20260630', '20250630'])
        cash_reconciliation(self.task, self.record, ['20260630', '20250630'])
        half_year_financial_supplements(self.task, '002594.SZ', ['20260630', '20250630'])
        self.assertEqual(self.task, before)

    def test_nonrecurring_totals_match_parent_profit_minus_adjusted_parent_profit(self):
        records = proven_disclosures(self.task, '002594.SZ')
        expected = {'20260630': Decimal('-47935000'), '20250630': Decimal('1910792000')}
        for record in records:
            data = nonrecurring_items(record)
            self.assertTrue(data['data_checks_passed'])
            self.assertEqual(Decimal(data['rows'][-1]['value']), expected[data['period']])
            self.assertEqual(data['unit'], '元')
            self.assertIn('不是合并报表净利润', data['checks']['profit_scope'])
            self.assertFalse(data['financial_adoption'])

    def test_full_management_sections_have_explicit_boundaries_and_literal_fragments(self):
        for record in proven_disclosures(self.task, '002594.SZ'):
            data = management_discussion(record)
            self.assertTrue(data['complete_section'])
            self.assertIn('第三节', data['start']['quote'])
            self.assertIn('第四节', data['end']['quote'])
            self.assertGreater(len(data['fragments']), 10)
            parts = {part['locator']: part['text'] for part in record['snapshot']['material']['parts']}
            self.assertTrue(all(fragment['quote'] in parts[fragment['part']] for fragment in data['fragments']))
            self.assertFalse(data['interpretation_performed'])

    def test_all_supplemental_fields_decode_without_any_model_or_download(self):
        data = half_year_financial_supplements(self.task, '002594.SZ', ['20260630', '20250630'])
        self.assertTrue(data['all_requested_fields_decoded'], data['fields'])
        self.assertTrue(data['all_data_checks_passed'])
        self.assertEqual(len(data['fields']), 6)
        self.assertEqual((data['model_calls'], data['data_requests']), (0, 0))
        self.assertFalse(data['financial_adoption'])

    def test_missing_period_does_not_discard_other_decoded_fields_or_fill_values(self):
        data = half_year_financial_supplements(self.task, '002594.SZ', ['20260630', '20240630'])
        self.assertFalse(data['all_requested_fields_decoded'])
        self.assertEqual(data['fields']['nonrecurring_20260630']['status'], 'decoded')
        capex = data['fields']['capital_expenditure']
        self.assertEqual(capex['status'], 'partial')
        self.assertEqual([item['period'] for item in capex['values']], ['20260630'])
        self.assertEqual([item['period'] for item in capex['missing']], ['20240630'])
        self.assertEqual(data['fields']['management_20240630']['status'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
