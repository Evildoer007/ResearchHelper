"""Replay real acquired source shapes without model/data calls or new dumps."""
import copy
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.draft_sources import observations, source_context
from researchhelper.evidence import extract_material, material_record
from researchhelper.index_sources import parse_csi_workbook
from researchhelper.etf_sources import parse_snapshot

MATRIX = ROOT/'result/acceptance/data-acquisition-matrix-current/sources.json.gz'
MACRO = ROOT/'result/acceptance/economic-discovery-live-20261023-v1/task.json'
WEIGHTS = ROOT/'result/acceptance/csi-official-files-k3mznoa2/000300closeweight.xls'
NAV = ROOT/'result/acceptance/sse-etf-nav-definition-fc8tyqoa/public-response.json'


def task_for(*records):
    return dict(id=records[0]['task_id'] if records else 'draft_source_fixture',
        version=records[0]['research_version'] if records else 1,
        source_records=list(records), materials=[])


def record(snapshot, operation='cmd_history_quotation', **extra):
    return dict(id='source_fixture', task_id='draft_source_fixture', research_version=1,
        source='ifind', operation=operation, snapshot=snapshot,
        parameters=dict(functionpara=dict(Currency='RMB', CPS='1')), **extra)


class DraftSourceTypesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (MATRIX, MACRO, WEIGHTS, NAV)}
        with gzip.open(MATRIX, 'rt') as stream:
            cls.matrix = json.load(stream)
        cls.macro = json.loads(MACRO.read_text())

    @classmethod
    def tearDownClass(cls):
        assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in cls.hashes.items())

    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No live I/O'))
        guard.start(); self.addCleanup(guard.stop)

    def market(self, code):
        return next(row for row in self.matrix if row.get('operation') == 'cmd_history_quotation'
            and row.get('snapshot', {}).get('tables', [{}])[0].get('thscode') == code)

    def test_stock_quotes_keep_real_code_dates_prices_and_units(self):
        source = self.market('600519.SH')
        values = observations(task_for(source))
        self.assertEqual(len(values), sum(len(column) for table in source['snapshot']['tables'] for column in table['table'].values()))
        first = next(row for row in values if row['field'] == 'close')
        self.assertEqual(first['code'], '600519.SH')
        self.assertEqual(first['period'], source['snapshot']['tables'][0]['time'][0])
        self.assertEqual(first['value'], source['snapshot']['tables'][0]['table']['close'][0])
        self.assertEqual(first['unit'], '元')

    def test_etf_market_prices_do_not_become_nav(self):
        values = observations(task_for(self.market('510300.SH')))
        self.assertTrue(values)
        self.assertTrue(all(row['field'] != 'unit_nav' for row in values))
        self.assertEqual(next(row for row in values if row['field'] == 'close')['label'], '收盘价')

    def test_index_points_do_not_inherit_currency_price_units(self):
        values = observations(task_for(self.market('000300.SH')))
        self.assertEqual(next(row for row in values if row['field'] == 'close')['unit'], '点')
        self.assertIn('000300.SH', {row['code'] for row in values})
        self.assertTrue(all(row['unit'] == '点' for row in values))

    def test_real_economic_string_values_are_not_lost(self):
        values = observations(self.macro)
        self.assertEqual(len(values), 9)
        self.assertEqual({row['period'][:7] for row in values}, {f'2026-{month:02d}' for month in range(1, 10)})
        self.assertEqual(values[0]['raw_value'], '0.9')
        self.assertEqual(values[0]['value'], .9)
        self.assertEqual(values[0]['unit'], '%')
        self.assertEqual(values[0]['label'], 'CPI:当月同比')

    def test_economic_release_date_is_not_substituted_for_observation_date(self):
        values = observations(self.macro)
        self.assertEqual(values[0]['period'], '2026-09-30')
        self.assertNotEqual(values[0]['period'], '2026-10-14')

    def test_missing_economic_metadata_does_not_block_values(self):
        task = copy.deepcopy(self.macro)
        task['source_records'] = [row for row in task['source_records'] if row.get('operation') != 'search_edb']
        values = observations(task)
        self.assertEqual(len(values), 9)
        self.assertEqual(values[0]['unit'], '原始单位待核对')

    def test_real_smart_financial_columns_keep_their_row_company_and_period(self):
        source = next(row for row in self.matrix if row.get('operation') == 'smart_stock_picking')
        values = observations(task_for(source))
        self.assertEqual(len(values), 3)
        self.assertTrue(all(row['code'] == '600519.SH' and row['period'] == '20260630' for row in values))
        self.assertTrue(all(row['field'] not in ('股票代码', '股票简称') for row in values))

    def test_multi_company_rows_do_not_share_one_identifier(self):
        source = record(dict(tables=[dict(table={'股票代码': ['000001.SZ', '600519.SH'],
            '股票简称': ['平安银行', '贵州茅台'], '营业收入[20260630]': ['10.5', '20.5']})]), 'smart_stock_picking')
        values = observations(task_for(source))
        self.assertEqual([(row['code'], row['value']) for row in values], [('000001.SZ', 10.5), ('600519.SH', 20.5)])

    def test_real_exchange_nav_keeps_valuation_day_and_raw_decimal(self):
        snapshot = parse_snapshot(NAV.read_bytes(), '510300.SH', '2026-10-27T00:00:00+08:00')
        source = record(snapshot, 'etf_pcf'); source['source'] = 'official'
        values = observations(task_for(source))
        self.assertEqual(len(values), 1)
        self.assertEqual((values[0]['period'], values[0]['raw_value'], values[0]['value']), ('2026-10-21', '4.1856', 4.1856))
        self.assertEqual(values[0]['unit'], '元')

    def test_real_weight_file_keeps_all_300_rows_without_normalizing(self):
        parsed = parse_csi_workbook(WEIGHTS.read_bytes(), '000300.SH', 'weights')
        source = record(dict(parsed=parsed, role='weights'), 'index_file'); source['source'] = 'official'
        values = observations(task_for(source))
        self.assertEqual(len(values), 300)
        self.assertEqual(values[0]['code'], '000001.SZ')
        self.assertTrue(all(row['unit'] == '%' and row['axis_role'] == 'cross_section' for row in values))
        self.assertEqual(sum(Decimal(str(row['value'])) for row in values), Decimal('100.001'))

    def test_zero_negative_missing_and_numeric_text_keep_their_original_positions(self):
        source = record(dict(tables=[dict(thscode='600519.SH',
            time=[f'2026-10-{i:02d}' for i in range(1, 6)], table=dict(close=[None, 0, -1, True, '2.3']))]))
        values = observations(task_for(source))
        self.assertEqual([(row['period'], row['value']) for row in values],
            [('2026-10-01', None), ('2026-10-02', 0), ('2026-10-03', -1), ('2026-10-05', 2.3)])

    def test_foreign_source_does_not_erase_valid_independent_values(self):
        good = copy.deepcopy(self.market('600519.SH')); bad = copy.deepcopy(good)
        bad['id'] = 'foreign'; bad['task_id'] = 'foreign_task'
        self.assertEqual(len(observations(task_for(good, bad))), len(observations(task_for(good))))

    def test_material_context_contains_prose_not_base64(self):
        task = task_for()
        payload = '需求回升可能改善经营杠杆，仍需核对订单和成本。'.encode()
        parsed = extract_material('research.txt', payload)
        original = material_record(task, parsed, payload); task['materials'] = [original]
        context = source_context(task)
        self.assertIn('需求回升', context[0]['content'])
        self.assertNotIn('original_base64', context[0]['content'])
        self.assertTrue(original['snapshot']['original_base64'])

    def test_nested_filing_context_contains_actual_parts(self):
        source = next(row for row in self.matrix if row.get('kind') == 'filing')
        context = source_context(task_for(source))
        self.assertTrue(context)
        self.assertIn('parts', context[0]['content'])
        self.assertNotIn('original_base64', context[0]['content'])

    def test_context_budget_does_not_lose_a_second_document_to_first_binary(self):
        first = record(dict(parts=[dict(locator='段落1', text='资料' * 2000)], original_base64='A' * 100000),
            'release_original');first['kind'] = 'material'
        second = copy.deepcopy(first); second['id'] = 'second'
        second['snapshot']['parts'] = [dict(locator='段落1', text='第二份材料的结论')]
        context = source_context(task_for(first, second), limit=800)
        self.assertEqual(len(context), 2)
        self.assertTrue(context[0]['truncated'])
        self.assertIn('第二份材料的结论', context[1]['content'])
        self.assertLessEqual(sum(len(row['content']) for row in context), 800)

    def test_account_limits_and_catalogue_ids_are_not_business_observations(self):
        source = record(dict(tables=[dict(table=dict(remaining=[50000], id=['20260101']))]), 'get_limit_policy')
        self.assertEqual(observations(task_for(source)), [])
        self.assertEqual(source_context(task_for(source)), [])

    def test_reading_never_changes_source_or_promotes_approval(self):
        task = copy.deepcopy(self.macro); before = copy.deepcopy(task)
        values = observations(task); source_context(task)
        self.assertEqual(task, before)
        self.assertTrue(all(row['status'] == '原始取数' for row in values))
        self.assertFalse(any(row.get('financial_approval') is True for row in values))


if __name__ == '__main__':
    unittest.main()
