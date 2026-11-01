"""Empty vendor queries are limitations, not permission failures or facts."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector, IFIND_REPORT_FIELDS


class IFindNoDataTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.archive = ResearchArchive(self.folder.name)
        self.task = ResearchAgent(self.archive, Path(__file__).resolve().parents[2]).create(
            '核对同花顺300033.SZ的公告内容', objects=['同花顺', '300033.SZ'])
        self.collector = EvidenceCollector(offline=False, journal=self.archive.save)
        self.identity = {'secname': '同花顺', 'functionpara': {'mode': 'secname',
            'sectype': '', 'market': '', 'tradestatus': '0', 'isexact': '1'}}
        self.directory = {'codes': '300033.SZ', 'functionpara': {'reportType': '901'},
            'beginrDate': '2026-09-21', 'endrDate': '2026-09-30',
            'outputpara': ','.join(field + ':Y' for field in IFIND_REPORT_FIELDS)}

    def query(self, operation, code):
        parameters = self.identity if operation == 'get_thscode' else self.directory
        payload = {'errorcode': code, 'errmsg': '权限过期 private_access customer_data no data'}
        with patch.object(self.collector, '_http_json', return_value=payload) as request:
            with self.assertRaises(ContractError) as failure:
                self.collector.external(self.task, 'ifind', operation, parameters,
                                        {'access_token': 'private_access'})
        self.assertEqual(request.call_count, 1)
        event = self.task['ledger']['dispatches'][-1]
        persisted = self.archive.get(self.task['id'])
        self.assertEqual(persisted['ledger'], self.task['ledger'])
        self.assertEqual(persisted['source_records'], [])
        self.assertEqual(persisted['evidence'], [])
        self.assertNotIn('private_access', str(failure.exception))
        self.assertNotIn('customer_data', json.dumps(persisted))
        return str(failure.exception), event

    def check_empty_query(self, operation, code):
        message, event = self.query(operation, code)
        self.assertIn(operation, message)
        self.assertIn('-4001', message)
        self.assertIn('未返回数据', message)
        self.assertIn('不能据此证明', message)
        self.assertNotIn('检查独立权限', message)
        self.assertEqual(event['supplier_rejection_category'], 'no_data')
        self.assertEqual(event['status'], '未返回数据')
        self.assertEqual(event['business_code'], code)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 1)

    def test_numeric_no_data_directory_has_precise_limitation(self):
        self.check_empty_query('report_query', -4001)

    def test_string_no_data_directory_has_precise_limitation(self):
        self.check_empty_query('report_query', '-4001')

    def test_numeric_no_data_identity_does_not_guess_identity(self):
        self.check_empty_query('get_thscode', -4001)

    def test_string_no_data_identity_does_not_guess_identity(self):
        self.check_empty_query('get_thscode', '-4001')

    def test_unknown_error_cannot_be_reclassified_by_supplier_text(self):
        message, event = self.query('report_query', -6102)
        self.assertIn('-6102', message)
        self.assertNotIn('no_data', str(event))
        self.assertEqual(event['status'], '业务失败')

    def test_malformed_code_cannot_be_reclassified(self):
        message, event = self.query('report_query', '-4001private_access')
        self.assertNotIn('business_code', event)
        self.assertNotIn('no_data', str(event))

    def test_boolean_is_not_a_known_code(self):
        message, event = self.query('report_query', False)
        self.assertNotIn('business_code', event)
        self.assertNotIn('no_data', str(event))

    def test_auth_failure_is_not_treated_as_an_empty_query(self):
        with patch.object(self.collector, '_http_json', return_value={
                'errorcode': -4001, 'errmsg': 'private_refresh'}) as request:
            with self.assertRaises(ContractError) as failure:
                self.collector.external(self.task, 'ifind', 'get_thscode', self.identity,
                                        {'refresh_token': 'private_refresh'})
        self.assertEqual(request.call_count, 1)
        event = self.task['ledger']['dispatches'][0]
        self.assertEqual(event['operation'], 'get_access_token')
        self.assertEqual(event['status'], '鉴权失败')
        self.assertNotIn('supplier_rejection_category', event)
        self.assertNotIn('private_refresh', str(failure.exception))

    def test_success_empty_directory_remains_unverified(self):
        with patch.object(self.collector, '_http_json', return_value={
                'errorcode': 0, 'tables': [], 'inputParams': {}}) as request:
            source = self.collector.external(self.task, 'ifind', 'report_query', self.directory,
                                             {'access_token': 'private_access'})
        self.assertEqual(request.call_count, 1)
        self.assertEqual(source['status'], '成功但为空')
        self.assertEqual(source['catalog_entries'], [])
        self.assertEqual(source['source_check'], '待核验')
        self.assertEqual(source['applicability_check'], '待核验')
        self.assertEqual(self.task['evidence'], [])


if __name__ == '__main__':
    unittest.main()
