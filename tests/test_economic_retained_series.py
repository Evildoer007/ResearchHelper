"""Independent current-code replay of real search and nine original points."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import unittest

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.economic_sources import discovered_indicators, validate_economic_request

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT/'result/acceptance/economic-discovery-live-20261023-v1'


class EconomicRetainedSeriesTests(unittest.TestCase):
    def setUp(self):
        self.original = (FOLDER/'task.json').read_bytes()
        self.task = json.loads(self.original)
        self.receipt = json.loads((FOLDER/'receipt.json').read_text())
        self.series = next(row for row in self.task['source_records'] if row['operation'] == 'edb_service')

    def tearDown(self):
        self.assertEqual((FOLDER/'task.json').read_bytes(), self.original)

    def test_actual_search_to_series_has_task_bound_original_snapshots(self):
        self.assertEqual(self.receipt['status'], 'search_to_raw_series_returned')
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 4)
        for source in self.task['source_records']:
            self.assertEqual(source['hash'], fingerprint(source['snapshot']))
            self.assertEqual(source['acquisition']['task_id'], self.task['id'])
            self.assertEqual(source['acquisition']['snapshot_hash'], source['hash'])
            event = self.task['ledger']['dispatches'][source['acquisition']['dispatch_index']]
            self.assertEqual(event['operation'], source['operation'])
            self.assertFalse(event.get('synthetic_test_transport', False))
        selected = [row for row in discovered_indicators(self.task) if row['displayId'] == 'M002826730']
        self.assertEqual(len(selected), 1)
        self.assertEqual((selected[0]['dataSource'], selected[0]['freq'], selected[0]['unit']), ('国家统计局', 'M', '%'))
        validate_economic_request(self.task, 'edb_service', self.series['parameters'])

    def test_nine_returned_months_have_aligned_finite_unmodified_values(self):
        snapshot = self.series['snapshot']
        self.assertEqual(snapshot['errorcode'], 0)
        self.assertEqual(len(snapshot['tables']), 1)
        table = snapshot['tables'][0]
        self.assertEqual(table['id'], ['M002826730'])
        self.assertEqual({date[:7] for date in table['time']}, {f'2026-{month:02d}' for month in range(1, 10)})
        self.assertEqual(len(set(table['time'])), 9)
        for name in ('value', 'rtime', 'index_name'):
            self.assertEqual(len(table[name]), 9)
        self.assertTrue(all(Decimal(value).is_finite() for value in table['value']))
        self.assertEqual(table['value'], ['0.9', '0.8', '0.5', '1.0', '1.2', '1.2', '1.0', '1.3', '0.2'])
        self.assertFalse(self.series['economic_contract']['numeric_evidence_registered'])

    def test_original_receipt_does_not_claim_autonomous_or_historical_acceptance(self):
        self.assertFalse(self.receipt['autonomous_research_accepted'])
        self.assertFalse(self.receipt['historical_vintage_approved'])
        self.assertFalse(self.receipt['financial_approval'])
        self.assertTrue(self.receipt['temporary_credentials_cleared'])
        self.assertTrue(self.receipt['production_unchanged'])
