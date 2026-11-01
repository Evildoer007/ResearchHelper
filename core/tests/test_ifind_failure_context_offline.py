"""Source failures retain actionable scope, never provider free-form secrets."""
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector
from researchhelper.contracts import ContractError


class IFindFailureContextTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.archive = ResearchArchive(self.folder.name)
        self.task = ResearchAgent(self.archive, Path(__file__).resolve().parents[2]).create(
            '核对512480.SH', objects=['512480.SH'])
        self.collector = EvidenceCollector(offline=False, journal=self.archive.save)
        self.params = {'seccode': '512480', 'functionpara': {'mode': 'seccode', 'sectype': '',
            'market': '', 'tradestatus': '0', 'isexact': '1'}}

    def query_failure(self, payload):
        with patch.object(self.collector, '_http_json', return_value=payload) as request:
            with self.assertRaises(ContractError) as failure:
                self.collector.external(self.task, 'ifind', 'get_thscode', self.params,
                    {'access_token': 'private_fixture_access'})
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 1)
        event = self.task['ledger']['dispatches'][-1]
        self.assertEqual(self.archive.get(self.task['id'])['ledger']['dispatches'][-1], event)
        return str(failure.exception), event

    def test_numeric_code_and_operation_reach_user_without_raw_message(self):
        message, event = self.query_failure({'errorcode': -6102, 'errmsg': 'private_fixture_access and customer private data'})
        self.assertIn('get_thscode', message)
        self.assertIn('-6102', message)
        self.assertNotIn('private', message)
        self.assertEqual((event['source'], event['operation'], event['business_code']), ('ifind', 'get_thscode', -6102))

    def test_malformed_provider_code_is_not_echoed_or_persisted(self):
        message, event = self.query_failure({'errorcode': 'private_fixture_access', 'errmsg': 'private_fixture_access'})
        self.assertIn('get_thscode', message)
        self.assertNotIn('private_fixture', message)
        self.assertNotIn('business_code', event)
        self.assertNotIn('private_fixture', str(event))

    def test_http_failure_has_endpoint_and_status_without_response_body(self):
        error = urllib.error.HTTPError('https://quantapi.51ifind.com/api/v1/get_thscode', 403, 'private_fixture_access', {}, None)
        with patch.object(self.collector, '_http_json', side_effect=error) as request:
            with self.assertRaises(ContractError) as failure:
                self.collector.external(self.task, 'ifind', 'get_thscode', self.params, {'access_token': 'private_fixture_access'})
        self.assertEqual(request.call_count, 1)
        self.assertIn('get_thscode', str(failure.exception))
        self.assertIn('403', str(failure.exception))
        self.assertNotIn('private_fixture_access', str(failure.exception))
        self.assertEqual(self.task['ledger']['dispatches'][-1]['operation'], 'get_thscode')

    def test_auth_failure_preserves_code_without_dispatching_business_query(self):
        with patch.object(self.collector, '_http_json', return_value={'errorcode': '-4001', 'errmsg': 'private_fixture_refresh'}) as request:
            with self.assertRaises(ContractError) as failure:
                self.collector.external(self.task, 'ifind', 'get_thscode', self.params, {'refresh_token': 'private_fixture_refresh'})
        self.assertEqual(request.call_count, 1)
        self.assertIn('get_access_token', str(failure.exception))
        self.assertIn('-4001', str(failure.exception))
        self.assertNotIn('private_fixture_refresh', str(failure.exception))
        self.assertEqual(self.task['ledger']['dispatches'][0]['business_code'], '-4001')
