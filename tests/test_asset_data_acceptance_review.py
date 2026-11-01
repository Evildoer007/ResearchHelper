"""Independent, offline review of retained real acquisition runs.

No provider calls, model calls, task resumption, or production writes. These
checks certify the stated samples, not general research or installed-App use.
"""
import base64
import copy
import hashlib
import json
from pathlib import Path
import unittest
import zipfile

from tests import ROOT
from researchhelper.contracts import ContractError, fingerprint, validate_evidence
from researchhelper.evidence import identity_candidates


CASES = {
    'named_hk_price': 'ruwlnu8l',
    'index_close': 'l016_rsb',
    'etf_annual_fees': 'rwlr5xxw',
    'equity_financials': 'mmuy0ffi',
}


def load_retained_task(folder):
    folder = Path(folder)
    if (folder / 'task.json').exists():
        task = json.loads((folder / 'task.json').read_text())
    else:
        with zipfile.ZipFile(folder / 'raw-qa.zip') as archive:
            task = json.loads(archive.read('task.json'))
    return task


def load_case(name):
    folder = ROOT / 'result/acceptance' / ('autonomous-data-phase-' + CASES[name])
    return load_retained_task(folder), json.loads((folder / 'receipt.json').read_text())


def verify_source(source, task):
    assert source['task_id'] == task['id']
    assert source['research_version'] == task['version']
    assert fingerprint(source['snapshot']) == source.get('snapshot_hash', source['hash'])
    assert source['request_hash'] == fingerprint(dict(
        source=source['source'], operation=source['operation'], parameters=source['parameters']))
    if source['operation'] == 'filing_original':
        material = source['snapshot']['material']
        original = base64.b64decode(material['original_base64'], validate=True)
        assert hashlib.sha256(original).hexdigest() == material['hash']
        assert source['acquisition']['original_hash'] == material['hash']
        assert original.startswith(b'%PDF-')
    assert any(event.get('fingerprint') == source['request_hash'] and event['status'] == '成功'
               for event in task['ledger']['dispatches'])


class AssetDataAcceptanceReviewTests(unittest.TestCase):
    def test_all_runs_are_closed_and_raw_snapshots_match(self):
        for name in CASES:
            with self.subTest(case=name):
                task, receipt = load_case(name)
                self.assertTrue(receipt['owned_runtime_closed'])
                self.assertTrue(receipt['production_unchanged'])
                self.assertTrue(receipt['source_unchanged'])
                self.assertFalse(receipt['supplied_plan'])
                self.assertFalse(receipt['supplied_evidence'])
                self.assertFalse(receipt['report_accepted'])
                self.assertEqual(task['status'], '已取消')
                for source in task['source_records']:
                    verify_source(source, task)
                for item in task['evidence']:
                    validate_evidence(item, task)

    def test_named_hk_prices_match_every_original_cell_but_not_issuer_identity(self):
        task, _ = load_case('named_hk_price')
        candidates = identity_candidates(task)
        self.assertTrue(candidates)
        self.assertFalse(any(row['identity_approved'] for row in candidates))
        prices = [source for source in task['source_records']
                  if source['operation'] == 'cmd_history_quotation']
        self.assertEqual(len(prices), 1)
        table = prices[0]['snapshot']['tables'][0]
        self.assertEqual(table['thscode'], '0700.HK')
        self.assertEqual(prices[0]['parameters']['functionpara']['Currency'], 'RMB')
        self.assertEqual(len(task['evidence']), len(table['time']))
        self.assertEqual(table['time'], ['2026-10-12', '2026-10-13', '2026-10-14',
                                        '2026-10-15', '2026-10-16'])
        for item in task['evidence']:
            row = item['record_locator']['row']
            self.assertEqual(item['raw_value'], table['table']['close'][row])
            self.assertEqual(item['calculation_context']['observation_date'], table['time'][row])
            self.assertEqual(item['calculation_context']['currency'], 'CNY')
            self.assertEqual(item['identity']['basis_ref']['kind'], 'candidate_provider_observation')

    def test_index_points_match_original_cells_and_calendar(self):
        task, _ = load_case('index_close')
        source = next(row for row in task['source_records']
                      if row['operation'] == 'cmd_history_quotation')
        table = source['snapshot']['tables'][0]
        self.assertEqual(table['thscode'], '000300.SH')
        self.assertEqual(len(task['evidence']), 5)
        self.assertTrue(any(row['operation'] == 'get_trade_dates' for row in task['source_records']))
        for item in task['evidence']:
            self.assertEqual(item['unit'], '点')
            self.assertEqual(item['raw_value'], table['table']['close'][item['record_locator']['row']])

    def test_fee_inputs_retain_fund_period_basis_frequency_and_original_provenance(self):
        task, receipt = load_case('etf_annual_fees')
        self.assertFalse(receipt['pending_method_inputs'])
        self.assertEqual(len(task['evidence']), 4)
        covered = set()
        for item in task['evidence']:
            context = item['calculation_context']
            self.assertEqual(item['unit'], '%')
            self.assertEqual(context['rate_period'], 'annual')
            self.assertEqual(context['fee_basis'], 'net_assets')
            self.assertEqual(context['period'], '2026-01-01/2026-06-30')
            self.assertTrue(context['share_class'])
            covered.add((item['object'], context['metric']))
            validate_evidence(item, task)
        self.assertEqual(covered, {(code, metric) for code in ('510300.SH', '510500.SH')
                                   for metric in ('management_fee', 'custody_fee')})

    def test_financial_download_does_not_become_amount_extraction_success(self):
        task, receipt = load_case('equity_financials')
        self.assertEqual(receipt['status'], 'data_phase_incomplete')
        self.assertEqual(task['evidence'], [])
        self.assertTrue(receipt['pending_method_inputs'])
        originals = [row for row in task['source_records'] if row['operation'] == 'filing_original']
        self.assertEqual(len(originals), 1)
        self.assertIn('2026', originals[0]['directory']['title'])
        self.assertIn('半年度报告', originals[0]['directory']['title'])
        self.assertEqual(originals[0]['parameters']['source_id'], '5207770858')

    def test_changing_an_accepted_value_without_original_evidence_is_rejected(self):
        task, _ = load_case('etf_annual_fees')
        item = copy.deepcopy(task['evidence'][0])
        item['value'] += 1
        with self.assertRaises(ContractError):
            validate_evidence(item, task)


if __name__ == '__main__':
    unittest.main()
