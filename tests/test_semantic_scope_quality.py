"""Independent review of vendor success versus actual requested data.

These are explicitly scoped diagnostics, not autonomous-Chat, date, unit,
complete-universe, or financial approval. Original responses stay immutable.
"""
from decimal import Decimal
import gzip
import hashlib
import json
import unittest

from tests import ROOT
from researchhelper.contracts import fingerprint


class SemanticScopeQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=ROOT/'result/acceptance/semantic-scope-current'
        cls.receipt=json.loads((cls.folder/'receipt.json').read_text())
        cls.raw=(cls.folder/'responses.json.gz').read_bytes()
        with gzip.open(cls.folder/'responses.json.gz','rt',encoding='utf-8') as incoming:cls.rows=json.load(incoming)

    @classmethod
    def tearDownClass(cls):
        assert (cls.folder/'responses.json.gz').read_bytes()==cls.raw

    def test_hashes_and_bounded_real_requests_without_model_or_production_changes(self):
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(),self.receipt['responses_sha256'])
        self.assertEqual(len(self.rows),6)
        self.assertEqual(self.receipt['ledger']['used']['data_requests'],7)
        self.assertEqual(self.receipt['ledger']['used']['model_calls'],0)
        self.assertTrue(self.receipt['production_unchanged'] and self.receipt['private_store_removed'])
        self.assertFalse(self.receipt['financial_approval'] or self.receipt['product_tool_registered'])
        for raw,record in zip(self.rows,self.receipt['results']):
            self.assertEqual(fingerprint(raw['payload']),record['snapshot_hash'])

    def test_returned_weights_do_not_cover_the_returned_member_set_or_confirm_date_units(self):
        members=self.rows[0]['payload']['tables'][0]['table']
        weights=self.rows[1]['payload']['tables'][0]['table']
        self.assertEqual(len(set(members['股票代码'])),300)
        self.assertEqual(len(set(weights['股票代码'])),299)
        self.assertEqual(set(members['股票代码'])-set(weights['股票代码']),{'002294.SZ'})
        self.assertEqual(sum(Decimal(x) for x in weights['沪深300个股权重']),Decimal('99.941'))
        self.assertTrue(all(isinstance(x,str) for x in weights['沪深300个股权重']))
        self.assertFalse(any('日期' in name or '单位' in name for name in weights))
        self.assertNotIn('descrs',self.rows[1]['payload'])
        self.assertNotIn('outParams',self.rows[1]['payload'])

    def test_segment_rows_and_parent_profit_cannot_substitute_company_totals(self):
        fields=self.rows[3]['payload']['tables'][0]['table']
        self.assertEqual(len(fields['股票代码']),46)
        self.assertEqual(len(set(fields['股票代码'])),28)
        self.assertNotIn('营业收入[20260630]',fields)
        self.assertNotIn('净利润[20260630]',fields)
        self.assertIn('主营构成(按行业)-业务收入[20260630]',fields)
        self.assertIn('归属于母公司所有者的净利润[20260630]',fields)
        self.assertTrue(any(fields['股票代码'].count(code)>1 for code in fields['股票代码']))

    def test_industry_query_labels_and_business_failure_do_not_become_permissions_or_units(self):
        chain=self.rows[4]['payload']['tables'][0]['table']
        self.assertIn('营业收入[20260630]',chain)
        self.assertIn('存货[20260630]',chain)
        self.assertFalse(any('单位' in name for name in chain))
        for index in (2,5):
            self.assertEqual(self.rows[index]['payload']['errorcode'],-4001)
            self.assertEqual(self.rows[index]['payload'].get('tables',[]),[])
        self.assertFalse(self.receipt['financial_approval'])
