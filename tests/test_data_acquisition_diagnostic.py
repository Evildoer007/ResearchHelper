"""Data inventory cannot promote HTTP or parsing success to research approval."""
import unittest

from tests.probe_data_acquisition_current import table_inventory, material_inventory


class AcquisitionInventoryTests(unittest.TestCase):
    def source(self, values, dates=None):
        return dict(snapshot=dict(tables=[dict(thscode='510500.SH',
            time=['2026-09-30'] if dates is None else dates, table=dict(close=values))]))

    def test_actual_column_and_date_retained(self):
        row = table_inventory(self.source([9.2]))[0]
        self.assertEqual(row['code'], '510500.SH')
        self.assertEqual(row['dates'], ['2026-09-30'])
        self.assertEqual(row['columns'][0]['numeric_count'], 1)
        self.assertNotIn('unit_approved', row)

    def test_missing_not_filled(self):
        column = table_inventory(self.source([None]))[0]['columns'][0]
        self.assertEqual(column['numeric_count'], 0)
        self.assertEqual(column['missing_count'], 1)

    def test_boolean_not_financial_number(self):
        column = table_inventory(self.source([True]))[0]['columns'][0]
        self.assertEqual(column['numeric_count'], 0)

    def test_nonfinite_not_counted(self):
        column = table_inventory(self.source([float('nan')]))[0]['columns'][0]
        self.assertEqual(column['numeric_count'], 0)

    def test_misaligned_not_repaired(self):
        column = table_inventory(self.source([9.2, 9.3]))[0]['columns'][0]
        self.assertFalse(column['aligned'])

    def test_empty_or_malformed_not_parsed(self):
        self.assertEqual(table_inventory(dict(snapshot=dict(tables=[]))), [])
        self.assertEqual(table_inventory(dict(snapshot=dict(tables=[None, dict(table=[])]))), [])

    def test_filing_reads_material_not_snapshot_parts(self):
        row = material_inventory(dict(snapshot=dict(material=dict(parts=[
            dict(locator='第1页', text='公告正文'), dict(locator='第2页', text='')]))))
        self.assertEqual(row['parsed_parts'], 2)
        self.assertEqual(row['readable_parts'], 1)
        self.assertEqual(row['located_readable_parts'], 1)

    def test_official_html_reads_snapshot_parts(self):
        row = material_inventory(dict(snapshot=dict(parts=[dict(locator='段落1', text='原文')])))
        self.assertEqual(row['readable_parts'], 1)

    def test_scanned_material_not_claimed_readable(self):
        row = material_inventory(dict(snapshot=dict(material=dict(parts=[dict(locator='第1页', text='')]))))
        self.assertEqual(row['readable_parts'], 0)
