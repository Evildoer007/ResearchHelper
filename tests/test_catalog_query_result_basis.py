"""Full returned catalogue rows, not all-market completeness or new facts."""
import hashlib
import json
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.catalog_metadata import POLICY, QUERY_POLICY, QUERY_COLUMNS, scope_basis
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import analysis_contract

ORIGINAL = ROOT/'result/acceptance/autonomous-data-phase-6hjk3w0z/task.json'


class CatalogQueryResultBasisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.digest = hashlib.sha256(ORIGINAL.read_bytes()).hexdigest()
        cls.retained = json.loads(ORIGINAL.read_text())

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(ORIGINAL.read_bytes()).hexdigest() == cls.digest

    def setUp(self):
        self.task = dict(self.retained, **{POLICY:1, QUERY_POLICY:1})
        self.task['input_hash'] = input_fingerprint(self.task)
        network = patch('socket.socket.connect',side_effect=AssertionError('Offline replay'))
        network.start(); self.addCleanup(network.stop)

    def test_every_actual_returned_row_is_projected_in_provider_order(self):
        basis = scope_basis(self.task)
        sources = {row['id']:row for row in self.task['source_records']}
        panels = basis['catalog_query_results']
        self.assertEqual(len(panels),len({row['catalog_artifact_id'] for row in basis['records']}))
        self.assertGreater(sum(panel['returned_row_count'] for panel in panels),len(basis['records']))
        for panel in panels:
            parent = sources[panel['catalog_artifact_id']]
            self.assertEqual(panel['columns'],QUERY_COLUMNS)
            self.assertEqual(panel['returned_row_count'],len(parent['catalog_entries']))
            self.assertEqual([row[0] for row in panel['rows']],
                             [row['source_id'] for row in parent['catalog_entries']])
            self.assertTrue(panel['all_returned_rows_projected'])
            self.assertEqual(panel['rows_hash'],fingerprint(panel['rows']))
            self.assertEqual(panel['catalog_snapshot_hash'],parent['hash'])
            self.assertEqual(panel['catalog_request_hash'],parent['request_hash'])
            self.assertEqual(panel['query_window'],parent['parameters'])
            self.assertEqual(panel['query_completeness'],'未证实')
        for flag in ('financial_approval','latest_report_completeness_approved','first_publication_approved',
                     'historical_availability_approved','automatic_need_coverage'):
            self.assertFalse(basis[flag])

    def test_old_projection_keeps_same_shape_and_new_policy_is_bound(self):
        old = dict(self.task); old.pop(QUERY_POLICY); old['input_hash']=input_fingerprint(old)
        self.assertNotEqual(self.task['input_hash'],old['input_hash'])
        self.assertNotIn('catalog_query_results',scope_basis(old))
        self.assertEqual(scope_basis(old)['records'],scope_basis(self.task)['records'])
        self.assertEqual(scope_basis(old)['limitation'],__import__('researchhelper.catalog_metadata',fromlist=['LIMITATION']).LIMITATION)

    def test_unregistered_policy_or_changed_input_is_rejected(self):
        for value in (True, False, 0, 2, 1.0):
            with self.subTest(value=value), self.assertRaises(ContractError):
                scope_basis(dict(self.task,**{QUERY_POLICY:value}))
        with self.assertRaises(ContractError):scope_basis(dict(self.task,question='不同问题'))

    def test_no_private_urls_and_projection_is_detached(self):
        basis = scope_basis(self.task)
        serialized = json.dumps(basis['catalog_query_results'])
        # The requested outputpara legitimately names pdfURL; that is a
        # field name, not a retained download address or private capability.
        for name in ('"original_url":','"pdfURL":','"original_base64":','https://'):
            self.assertFalse(name in serialized, 'Private URL/value leaked into query projection')
        basis['catalog_query_results'][0]['rows'][0][2] = '修改副本'
        self.assertNotEqual(scope_basis(self.task)['catalog_query_results'][0]['rows'][0][2],'修改副本')

    def test_imported_parent_cannot_authenticate_full_return_list(self):
        task = dict(self.task, source_records=[dict(row,acquisition=None)
            if row.get('operation')=='report_query' else row for row in self.task['source_records']])
        self.assertIsNone(scope_basis(task))

    def test_analysis_and_review_share_all_returned_rows(self):
        from researchhelper.argument_support import review_request, review_messages
        task = dict(self.task,evidence=[],calculations=[])
        expected = scope_basis(task)
        self.assertEqual(analysis_contract(task)['catalog_metadata_scope_basis'],expected)
        qid = task['questions'][0]['id']
        request = review_request(task,dict(answers={qid:dict(answer='核对实际目录。',refs=[],calculation_refs=[])},theses=[]))
        self.assertEqual(request['scope']['catalog_metadata_scope_basis'],expected)
        self.assertIn('全部目录行',review_messages(request)[0]['content'])
