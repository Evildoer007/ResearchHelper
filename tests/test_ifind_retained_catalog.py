"""Read-only replay of an actual iFinD response, not a generated provider sample.

The original capability task is closed. Tests do not reopen it, request data,
approve investment evidence, or write to its SQLite store.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if importlib.util.find_spec('researchhelper') is None:
    from core.bootstrap import configure_imports
    configure_imports()
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import (IFIND_REPORT_FIELDS, _filing_catalog,
    validate_filing_catalog_binding, validate_filing_original_request)

OBSERVATION = ROOT / 'result/acceptance/ifind-filing-filters-oeesa1k9'


class RetainedFilingCatalogTests(unittest.TestCase):
    def setUp(self):
        self.files = [OBSERVATION / 'receipt.json', OBSERVATION / 'unfiltered-source.json',
                      OBSERVATION / 'store/researchhelper.sqlite3']
        self.digests = [hashlib.sha256(file.read_bytes()).hexdigest() for file in self.files]
        receipt = json.loads(self.files[0].read_text())
        self.assertEqual(receipt['case'], 'single-etf-filings-unfiltered-retention-20261014-v3')
        self.assertTrue(receipt['task_closed'])
        self.assertEqual(receipt['wire_calls'], ['get_access_token', 'report_query'])
        self.source = json.loads(self.files[1].read_text())
        self.assertEqual(receipt['calls'][0]['response_sha256'], self.digests[1])
        with sqlite3.connect(self.files[2].as_uri() + '?mode=ro', uri=True) as connection:
            body = connection.execute('select body from tasks where id=?', (receipt['task_id'],)).fetchone()[0]
        self.task = json.loads(body)
        self.assertEqual(self.task['status'], '已取消')
        # Only a memory projection adopts the retained response for replay.
        self.task['source_records'] = [copy.deepcopy(self.source)]
        self.entry = copy.deepcopy(self.source['catalog_entries'][0])

    def tearDown(self):
        self.assertEqual([hashlib.sha256(file.read_bytes()).hexdigest() for file in self.files], self.digests)

    def test_actual_two_rows_and_request_are_retained(self):
        self.assertEqual(self.source['parameters']['codes'], '510300.SH')
        self.assertEqual(self.source['parameters']['functionpara'], {})
        self.assertEqual(len(self.source['catalog_entries']), 2)
        self.assertEqual(self.source['catalog_coverage']['returned_rows'], 2)
        self.assertEqual(self.source['hash'], fingerprint(self.source['snapshot']))
        self.assertEqual({row['source_id'] for row in self.source['catalog_entries']}, {'5347748122', '5346091044'})

    def test_catalog_is_rederived_from_actual_fields(self):
        rebuilt = _filing_catalog(self.task, self.source)
        for key in ('catalog_entries', 'catalog_status', 'catalog_coverage', 'catalog_limitations', 'status'):
            self.assertEqual(rebuilt[key], self.source[key])
        for entry in self.source['catalog_entries']:
            self.assertEqual(validate_filing_catalog_binding(self.task, entry), entry)
            self.assertEqual(set(entry['catalog_ref']['original_fields']), set(IFIND_REPORT_FIELDS))

    def test_unknown_publication_stays_unknown_and_unapproved(self):
        self.assertEqual(self.source['catalog_coverage']['completeness'], '未证实')
        self.assertEqual(self.source['source_check'], '待核验')
        self.assertEqual(self.source['applicability_check'], '待核验')
        for entry in self.source['catalog_entries']:
            self.assertIsNone(entry['published_at'])
            self.assertEqual(entry['publication_precision'], '未知')
            self.assertIn('发布时间缺失、格式或时区不明确', entry['gaps'])
            self.assertEqual(entry['source_check'], '待核验')
            self.assertEqual(entry['applicability_check'], '待核验')
        self.assertFalse(self.task['evidence'])

    def test_original_selection_keeps_bound_id_despite_unknown_publication(self):
        selected = validate_filing_original_request(self.task, {
            'catalog_id': self.source['id'], 'source_id': self.entry['source_id']})
        self.assertEqual(selected, self.entry)
        self.assertTrue(selected['downloadable'])
        self.assertIsNone(selected['published_at'])

    def test_modified_candidate_fields_are_rejected(self):
        for key, value in [('title', '伪造标题'), ('issuer', '510500.SH'),
                           ('report_date', '2026-10-13'), ('published_at', '2026-10-09'),
                           ('downloadable', False)]:
            with self.subTest(key=key):
                entry = copy.deepcopy(self.entry)
                entry[key] = value
                with self.assertRaises(ContractError):
                    validate_filing_catalog_binding(self.task, entry)

    def test_changed_raw_response_cannot_keep_original_hash(self):
        self.task['source_records'][0]['snapshot']['errorcode'] = -4001
        with self.assertRaises(ContractError):
            validate_filing_catalog_binding(self.task, self.entry)

    def test_changed_request_cannot_keep_response_identity(self):
        self.task['source_records'][0]['parameters']['functionpara'] = {'reportType': '901'}
        with self.assertRaises(ContractError):
            validate_filing_catalog_binding(self.task, self.entry)

    def test_other_task_and_other_research_version_are_rejected(self):
        for changes in ({'id': 'another-task'}, {'version': self.task['version'] + 1}):
            with self.subTest(changes=changes):
                task = copy.deepcopy(self.task)
                task.update(changes)
                with self.assertRaises(ContractError):
                    validate_filing_catalog_binding(task, self.entry)

    def test_ambiguous_duplicate_catalog_record_is_rejected(self):
        self.task['source_records'].append(copy.deepcopy(self.source))
        with self.assertRaises(ContractError):
            validate_filing_original_request(self.task, {
                'catalog_id': self.source['id'], 'source_id': self.entry['source_id']})

    def test_unknown_source_and_external_url_are_not_download_instructions(self):
        for params in ({'catalog_id': self.source['id'], 'source_id': 'unknown'},
                       {'catalog_id': self.source['id'], 'source_id': self.entry['source_id'],
                        'url': 'https://www.sse.com.cn/unknown.pdf'}):
            with self.subTest(params=params):
                with self.assertRaises(ContractError):
                    validate_filing_original_request(self.task, params)

    def test_public_resource_locator_contains_no_ephemeral_authentication(self):
        self.assertTrue(self.source['snapshot_privacy']['redacted'])
        self.assertEqual(self.source['snapshot_privacy']['business_snapshot_hash'], self.source['hash'])
        for entry in self.source['catalog_entries']:
            for url in (entry['original_url'], entry['catalog_ref']['original_fields']['pdfURL']):
                parsed = urlsplit(url)
                self.assertIsNone(parsed.username)
                self.assertIsNone(parsed.password)
                keys = {name.lower() for name, _ in parse_qsl(parsed.query)}
                self.assertFalse(keys & {'token', 'access_token', 'refresh_token', 'ip', 'account', 'password', 'userid'})

    def test_acquisition_does_not_change_announced_date_or_publication_precision(self):
        for entry in self.source['catalog_entries']:
            raw = entry['catalog_ref']['original_fields']
            self.assertEqual(entry['report_date'], raw['reportDate'])
            self.assertNotEqual(entry['report_date'], self.source['obtained_at'][:10])
            self.assertIsNone(entry['published_at'])


if __name__ == '__main__':
    unittest.main()
