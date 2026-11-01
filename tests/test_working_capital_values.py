"""Read-only replay of the one bounded actual acquisition; no retries."""
import hashlib,itertools,json,socket,unittest
from decimal import Decimal
from unittest.mock import patch
from tests import ROOT
from researchhelper.draft_sources import observations
from researchhelper.draft_analysis import analyze
from researchhelper.contracts import fingerprint

LIVE=ROOT/'result/acceptance/working-capital-values-live-h_cxwzya'
FIELDS={'ths_account_receivable_stock','ths_inventory_stock','ths_accounts_payable_stock'}


class ActualWorkingCapitalValuesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths=[LIVE/name for name in ('task.json','raw-values.json','receipt.json')]
        cls.hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in cls.paths}
        cls.task=json.loads(cls.paths[0].read_text());cls.saved=json.loads(cls.paths[1].read_text())
        cls.receipt=json.loads(cls.paths[2].read_text())

    @classmethod
    def tearDownClass(cls):
        assert all(hashlib.sha256(p.read_bytes()).hexdigest()==sha for p,sha in cls.hashes.items())

    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Do not repeat actual acquisition'))
        guard.start();self.addCleanup(guard.stop)

    def test_every_requested_code_period_field_returned_once(self):
        expected=set(itertools.product(['002594.SZ','600519.SH'],['20260630','20250630'],FIELDS))
        keys=[(row['code'],row['period'],row['field']) for row in self.saved]
        self.assertEqual(set(keys),expected);self.assertEqual(len(keys),len(expected))
        self.assertTrue(all(row['value'] is not None for row in self.saved))

    def test_saved_values_reconstruct_from_unchanged_provider_snapshots(self):
        self.assertEqual(observations(self.task),self.saved)
        records={row['id']:row for row in self.task['source_records']}
        for fact in self.saved:
            record=records[fact['artifact_id']];where=fact['locator']
            raw=record['snapshot']['tables'][where['table']]['table'][where['field']][where['row']]
            self.assertEqual(Decimal(str(raw)),Decimal(str(fact['raw_value'])))
            self.assertEqual(fingerprint(record['snapshot']),record['hash'])

    def test_no_directory_retrieval_model_pdf_or_shared_store_change(self):
        self.assertEqual([row['operation'] for row in self.receipt['calls']],
                         ['get_limit_policy','basic_data_service','basic_data_service'])
        self.assertEqual(self.receipt['actual_used']['data_requests'],4)
        self.assertEqual(self.receipt['actual_used']['model_calls'],0)
        self.assertEqual(self.receipt['pdf_downloads'],0)
        for key in ('production_unchanged','temporary_store_removed','owned_workers_stopped','temporary_credentials_cleared'):
            self.assertTrue(self.receipt[key])

    def test_no_guessed_unit_or_cashflow_contribution(self):
        self.assertEqual({row['unit'] for row in self.saved},{'原始单位待核对'})
        calculations=analyze(self.task,self.saved)['results']
        self.assertEqual(len(calculations),6)
        self.assertEqual({row['code'] for row in calculations},{'002594.SZ','600519.SH'})
        for row in calculations:
            self.assertEqual(row['period_kind'],'point_in_time')
            self.assertIn('不能视为当期经营现金流贡献',row['condition'])
            self.assertTrue(row['conditional_equal_scale']);self.assertFalse(row['financial_approval'])
