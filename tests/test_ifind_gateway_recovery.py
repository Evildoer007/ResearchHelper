"""Metered gateway recovery with local transport substitutes only.

No actual supplier request, credential, model or financial approval.
"""
import copy
import json
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector, IFIND_REPORT_FIELDS

POLICY = 'ifind_gateway_recovery_version'


class IFindGatewayRecoveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task = self.agent.create('核对510300.SH正式报告', objects=['510300.SH'], research_mode='current')
        self.collector = EvidenceCollector(offline=False, journal=Mock())
        self.params = dict(codes='510300.SH', functionpara={}, beginrDate='2026-01-01', endrDate='2026-09-30',
                          outputpara=','.join(field+':Y' for field in IFIND_REPORT_FIELDS))
        self.digest = fingerprint(dict(source='ifind', operation='report_query', parameters=self.params))
        self.calls = []
        network = patch('socket.socket.connect', side_effect=AssertionError('Local transport fixture'))
        network.start(); self.addCleanup(network.stop)
        sleeper = patch('researchhelper.evidence.time.sleep')
        sleeper.start(); self.addCleanup(sleeper.stop)

    def query(self, outcomes, *, reaped=True, started=True, credentials=None):
        outcomes = iter(outcomes)
        def http(task, request, timeout, event):
            self.calls.append(dict(url=request.full_url, body=request.data))
            event.update(transport_isolated=True, network_worker_reaped=reaped, network_request_started=started)
            value = next(outcomes)
            if type(value) is int:
                raise urllib.error.HTTPError(request.full_url, value, 'LOCAL_PRIVATE_BODY', {}, None)
            if isinstance(value, BaseException):raise value
            return copy.deepcopy(value)
        with patch.object(self.collector, '_http_json', side_effect=http):
            return self.collector.external(self.task, 'ifind', 'report_query', self.params,
                credentials or dict(access_token='LOCAL_PRIVATE_ACCESS'))

    def test_known_gateway_failure_keeps_both_metered_attempts_same_request(self):
        result = self.query([502, dict(errorcode=0, tables=[])])
        self.assertEqual(result['status'], '成功但为空')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0], self.calls[1])
        events = self.task['ledger']['dispatches']
        self.assertEqual([row['attempt'] for row in events], [1,2])
        self.assertEqual(events[0]['http_status'], 502)
        self.assertTrue(events[0]['gateway_retry_scheduled'])
        self.assertEqual(events[1]['gateway_retry_of_dispatch'], 0)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 2)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)
        self.assertEqual(self.task['evidence'], [])
        self.assertNotIn('LOCAL_PRIVATE', json.dumps(self.task))

    def test_503_also_has_one_bounded_retry(self):
        self.query([503, dict(errorcode=0, tables=[])])
        self.assertEqual(len(self.calls), 2)

    def test_second_gateway_failure_stops_without_third_send(self):
        with self.assertRaisesRegex(ContractError, '服务暂不可用'):
            self.query([502, 503, dict(errorcode=0, tables=[])])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.task['ledger']['used']['data_requests'], 2)

    def test_other_http_statuses_never_retry_or_expose_body(self):
        categories = {401: 'authentication_failed', 403: 'permission_denied', 429: 'rate_limited'}
        for status in (400, 401, 403, 429, 500, 504):
            before = len(self.calls)
            with self.subTest(status=status), self.assertRaises(ContractError) as error:
                self.query([status, dict(errorcode=0, tables=[])])
            self.assertEqual(len(self.calls), before+1)
            self.assertNotIn('LOCAL_PRIVATE', str(error.exception))
            self.assertEqual(self.task['ledger']['dispatches'][-1].get('supplier_rejection_category'),
                             categories.get(status))
            # Independent request range for the next fixture; never clears a ledger.
            self.params['beginrDate'] = '2026-01-%02d' % (status % 20 + 1)

    def test_unknown_transport_and_unreaped_response_never_retry(self):
        with self.assertRaises(ContractError):self.query([OSError('LOCAL_PRIVATE')])
        self.assertEqual(len(self.calls), 1)
        self.params['beginrDate']='2026-02-01'
        with self.assertRaises(ContractError):self.query([502], reaped=False)
        self.assertEqual(len(self.calls), 2)
        self.params['beginrDate']='2026-03-01'
        with self.assertRaises(ContractError):self.query([503], started=False)
        self.assertEqual(len(self.calls), 3)

    def test_authentication_is_not_retried_or_refreshed(self):
        with self.assertRaises(ContractError):self.query([503], credentials=dict(refresh_token='LOCAL_PRIVATE_REFRESH'))
        self.assertEqual(len(self.calls),1)
        self.assertTrue(self.calls[0]['url'].endswith('/get_access_token'))

    def test_explicit_gateway_retry_does_not_reauthenticate(self):
        self.query([dict(errorcode=0,data=dict(access_token='LOCAL_ACCESS_TOKEN')),502,
                    dict(errorcode=0,tables=[])], credentials=dict(refresh_token='LOCAL_PRIVATE_REFRESH'))
        self.assertEqual(len(self.calls),3)
        self.assertEqual(sum(row['url'].endswith('/get_access_token') for row in self.calls),1)

    def test_cumulative_three_send_limit_survives_reentry(self):
        self.task['ledger']['dispatches'] = [dict(kind='data_requests',fingerprint=self.digest,
            status='HTTP调用失败',gateway_retry_scheduled=True) for _ in range(2)]
        self.task['ledger']['used']['data_requests'] = 2
        with self.assertRaises(ContractError):self.query([502])
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.task['ledger']['used']['data_requests'],3)
        with self.assertRaisesRegex(ContractError, '三次累计'):self.query([dict(errorcode=0,tables=[])])
        self.assertEqual(len(self.calls),1)

    def test_budget_exhaustion_and_cancel_stop_before_another_send(self):
        self.task['ledger']['limits']['data_requests'] = 1
        with self.assertRaises(ContractError):self.query([502,dict(errorcode=0,tables=[])])
        self.assertEqual(len(self.calls),1)
        self.task['ledger']['limits']['data_requests'] = 100
        self.params['beginrDate'] = '2026-02-01'
        self.collector.active = Mock(side_effect=ContractError('Fixture cancellation'))
        with self.assertRaisesRegex(ContractError,'Fixture cancellation'):self.query([502,dict(errorcode=0,tables=[])])
        self.assertEqual(len(self.calls),2)

    def test_legacy_or_boolean_policy_does_not_enable_retries(self):
        for index, value in enumerate((None, True, False, 0, 2), 1):
            self.task[POLICY] = value
            self.params['beginrDate'] = '2026-02-%02d' % index
            with self.subTest(value=value), self.assertRaises(ContractError):self.query([502])
        self.assertEqual(len(self.calls),5)
