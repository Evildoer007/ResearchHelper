"""Readonly replay of actual originals, not new live extraction acceptance."""
import hashlib
import json
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.catalog_metadata import POLICY, scope_basis
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import analysis_contract
from researchhelper.argument_support import review_request, review_messages

ORIGINAL = ROOT/'result/acceptance/autonomous-data-phase-6hjk3w0z/task.json'


class CatalogMetadataBasisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_digest = hashlib.sha256(ORIGINAL.read_bytes()).hexdigest()
        cls.retained = json.loads(ORIGINAL.read_text())

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(ORIGINAL.read_bytes()).hexdigest() == cls.original_digest

    def setUp(self):
        # Change only this detached metadata policy, never old research files
        # or their financial/source receipts. This is format replay only.
        self.task = dict(self.retained, source_records=list(self.retained['source_records']))
        self.task[POLICY] = 1
        self.task['input_hash'] = input_fingerprint(self.task)
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline replay'))
        guard.start(); self.addCleanup(guard.stop)

    def test_actual_directory_dates_are_not_first_publication(self):
        result = scope_basis(self.task)
        rows = {row['provider_code']: row for row in result['records']}
        self.assertEqual(rows['510300.SH']['supplier_announcement_date'], '2026-08-29')
        self.assertEqual(rows['159919.SZ']['supplier_announcement_date'], '2026-08-31')
        self.assertTrue(all(row['supplier_publication_at'] is None for row in rows.values()))
        self.assertTrue(all(row['query_completeness'] == '未证实' for row in rows.values()))
        for key in ('financial_approval', 'first_publication_approved', 'historical_availability_approved',
                    'latest_report_completeness_approved', 'automatic_need_coverage'):
            self.assertIs(result[key], False)
        self.assertEqual(result['records_hash'], fingerprint(result['records']))

    def test_legacy_tasks_have_no_new_basis_or_hash_upgrade(self):
        task = dict(self.retained)
        self.assertNotIn(POLICY, task)
        self.assertIsNone(scope_basis(task))
        self.assertEqual(input_fingerprint(task), self.retained['input_hash'])

    def test_unknown_policy_and_boolean_version_are_rejected(self):
        for value in (True, False, 0, 2, 1.0):
            task = dict(self.task, **{POLICY:value})
            with self.subTest(value=value), self.assertRaises(ContractError):
                scope_basis(task)
        task = dict(self.task, version=True)
        with self.assertRaises(ContractError):scope_basis(task)

    def test_changed_question_cannot_keep_old_basis_binding(self):
        self.task['question'] = '改变后的问题'
        with self.assertRaises(ContractError):scope_basis(self.task)

    def test_historical_or_document_only_never_gains_exception(self):
        from researchhelper.contracts import DOCUMENT_CONTENT_INTENT
        for changes in (dict(research_mode='historical'), dict(intent=DOCUMENT_CONTENT_INTENT)):
            with self.subTest(changes=changes):
                self.assertIsNone(scope_basis(dict(self.task, **changes)))

    def test_imported_original_and_cross_version_do_not_gain_basis(self):
        for change in (dict(acquisition=None), dict(research_version=999)):
            task = dict(self.task, source_records=[dict(row, **change) if row.get('kind') == 'filing'
                                                else row for row in self.task['source_records']])
            with self.subTest(change=change):self.assertIsNone(scope_basis(task))

    def test_tampered_directory_date_is_excluded_not_repaired(self):
        rows = []
        for row in self.task['source_records']:
            if row.get('kind') == 'filing':
                row = dict(row, directory=dict(row['directory'], report_date='2026-01-01'))
            rows.append(row)
        self.assertIsNone(scope_basis(dict(self.task, source_records=rows)))

    def test_duplicate_original_is_rejected(self):
        original = next(row for row in self.task['source_records'] if row.get('kind') == 'filing')
        self.task['source_records'].append(original)
        with self.assertRaises(ContractError):scope_basis(self.task)

    def test_download_does_not_authenticate_an_imported_catalogue(self):
        task = dict(self.task, source_records=[dict(row, acquisition=None)
            if row.get('operation') == 'report_query' else row for row in self.task['source_records']])
        self.assertIsNone(scope_basis(task))

    def test_projection_detached_and_no_private_download_urls(self):
        result = scope_basis(self.task)
        result['records'][0]['query_window']['codes'] = 'changed'
        rebuilt = scope_basis(self.task)
        self.assertNotEqual(rebuilt['records'][0]['query_window']['codes'], 'changed')
        for row in rebuilt['records']:
            self.assertNotIn('original_url', row)
            self.assertNotIn('pdfURL', row)
            self.assertNotIn('original_base64', row)

    def test_analysis_and_review_share_exact_verified_projection(self):
        # This detached policy replay cannot reuse old financial approvals.
        # Exercise only the shared metadata projection, with no fact refs.
        task = dict(self.task, evidence=[], calculations=[])
        expected = scope_basis(task)
        contract = analysis_contract(task)
        self.assertEqual(contract['catalog_metadata_scope_basis'], expected)
        self.assertEqual(contract['execution']['catalog_metadata_basis_hash'], fingerprint(expected))
        question = task['questions'][0]
        proposal = dict(answers={question['id']:dict(answer='本次仅核对实际取得的供应商目录。',
            refs=[], calculation_refs=[])}, theses=[])
        request = review_request(task, proposal)
        self.assertEqual(request['scope']['catalog_metadata_scope_basis'], expected)
        self.assertIn('供应商目录公告日期', review_messages(request)[0]['content'])
        self.assertEqual(self.task['evidence'], self.retained['evidence'])

    def test_catalog_metadata_does_not_cover_unlinked_financial_needs(self):
        from researchhelper.contracts import missing_evidence_requirements
        question = self.task['questions'][0]
        self.assertTrue(scope_basis(self.task)['records'])
        self.assertEqual(len(missing_evidence_requirements(question, [])), len(question['evidence_needs']))

    def test_metadata_frozen_in_incomplete_fixture_without_customer_acceptance(self):
        from researchhelper.reports import compose, check_content
        task = dict(self.task, evidence=[], calculations=[], source_readings=[], reasoning_state={}, host_result={},
                    gaps=['独立元数据格式夹具，未分析。'])
        content = compose(task)
        check_content(content)
        self.assertEqual(content['catalog_metadata_scope_basis'], scope_basis(task))
        self.assertEqual(content[POLICY], 1)
