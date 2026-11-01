"""Transport-boundary fixtures for current-token acquisition, budget and secrecy."""
import copy
import io
import json
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from researchhelper.contracts import ContractError, budget
from researchhelper.evidence import EvidenceCollector


ACCESS = "access-fixture-not-real"
REFRESH = "refresh-fixture-not-real"
PARAMS = {"codes": "600000.SH", "indicators": "close", "startdate": "2026-09-01", "enddate": "2026-09-02", "functionpara": {"Fill": "Blank"}}


def response(value):
    return io.BytesIO(json.dumps(value).encode())


class IfindAuthResolutionTests(unittest.TestCase):
    def setUp(self):
        self.task = {"id": "auth-resolution-fixture", "version": 1, "batch": 0, "ledger": budget()}
        self.journal = []
        self.collector = EvidenceCollector(False, lambda task: self.journal.append(copy.deepcopy(task)))
        self.business = {"errorcode": 0, "tables": [{"thscode": "600000.SH", "time": ["2026-09-01"], "table": {"close": [10]}}]}

    def acquire(self, credentials=None):
        return self.collector.external(self.task, "ifind", "cmd_history_quotation", PARAMS, credentials or {"refresh_token": REFRESH})

    def test_get_current_token_then_business_with_no_secret_archive(self):
        self.business["note"] = ACCESS + " " + REFRESH
        replies = [response({"errorcode": 0, "data": {"access_token": ACCESS, "refresh_token": REFRESH}}), response(self.business)]
        with patch("urllib.request.urlopen", side_effect=replies) as transport:
            result = self.acquire()
        auth, business = [call.args[0] for call in transport.call_args_list]
        self.assertEqual(auth.full_url, "https://quantapi.51ifind.com/api/v1/get_access_token")
        self.assertEqual(auth.get_method(), "POST")
        self.assertEqual(auth.get_header("Refresh_token"), REFRESH)
        self.assertEqual(business.get_header("Access_token"), ACCESS)
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 2)
        self.assertEqual(self.task["ledger"]["dispatches"][0]["purpose"], "鉴权")
        self.assertEqual(len(self.journal), 4)
        serialized = json.dumps([result, self.task, self.journal])
        self.assertNotIn(ACCESS, serialized)
        self.assertNotIn(REFRESH, serialized)
        self.assertNotIn(REFRESH, repr(self.collector._ifind_access_cache))

    def test_sensitive_headers_are_not_forwarded_by_urllib_redirect(self):
        with patch("urllib.request.urlopen", side_effect=[response({"errorcode": 0, "data": {"access_token": ACCESS}}), response(self.business)]) as transport:
            self.acquire()
        for call in transport.call_args_list:
            request = call.args[0]
            self.assertNotIn("Access_token", request.headers)
            self.assertNotIn("Refresh_token", request.headers)
            redirected = urllib.request.HTTPRedirectHandler().redirect_request(request, None, 302, "Found", {}, "https://redirect.invalid/path")
            self.assertIsNone(redirected.get_header("Access_token"))
            self.assertIsNone(redirected.get_header("Refresh_token"))

    def test_explicit_access_wins_without_exchange_or_refresh_fallback(self):
        with patch("urllib.request.urlopen", return_value=response(self.business)) as transport:
            self.acquire({"access_token": ACCESS, "refresh_token": REFRESH})
        self.assertEqual(transport.call_count, 1)
        self.assertTrue(transport.call_args.args[0].full_url.endswith('/cmd_history_quotation'))
        with patch("urllib.request.urlopen", return_value=response({"errorcode": -1, "errmsg": ACCESS})) as transport, self.assertRaises(ContractError):
            self.acquire({"access_token": ACCESS, "refresh_token": REFRESH})
        self.assertEqual(transport.call_count, 1)

    def test_cache_is_bound_to_refresh_and_expires_in_five_minutes(self):
        clock = [0]
        calls = []
        def transport(request, **_):
            calls.append(request.full_url)
            return response({"errorcode": 0, "data": {"access_token": ACCESS}} if request.full_url.endswith('get_access_token') else self.business)
        with patch("time.monotonic", side_effect=lambda: clock[0]), patch("urllib.request.urlopen", side_effect=transport):
            self.acquire()
            self.acquire()
            self.assertEqual(sum(url.endswith('get_access_token') for url in calls), 1)
            self.acquire({"refresh_token": "different-refresh-fixture"})
            self.assertEqual(sum(url.endswith('get_access_token') for url in calls), 2)
            clock[0] = 301
            self.acquire({"refresh_token": "different-refresh-fixture"})
            self.assertEqual(sum(url.endswith('get_access_token') for url in calls), 3)
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 7)

    def test_auth_failure_and_malformed_token_never_retry_or_expose_body(self):
        for body in ({"errorcode": -1, "errmsg": REFRESH + ACCESS}, {"errorcode": False, "data": {"access_token": ACCESS}}, {"errorcode": 0, "data": {}}, {"errorcode": 0, "data": {"access_token": 3}}, {"errorcode": 0, "data": {"access_token": "bad\r\nheader"}}, []):
            with self.subTest(body_type=type(body).__name__), patch("urllib.request.urlopen", return_value=response(body)) as transport, self.assertRaises(ContractError) as raised:
                self.acquire()
            self.assertEqual(transport.call_count, 1)
            self.assertNotIn(REFRESH, str(raised.exception))
            self.assertNotIn(ACCESS, str(raised.exception))
            self.assertIsNone(self.collector._ifind_access_cache)

    def test_transport_error_is_single_dispatch_and_sanitized(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError(REFRESH + ACCESS)) as transport, self.assertRaises(ContractError) as raised:
            self.acquire()
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 1)
        self.assertNotIn(REFRESH, str(raised.exception))
        self.assertEqual(self.journal[-1]["ledger"]["dispatches"][0]["status"], "鉴权传输失败")

    def test_request_cap_includes_authentication(self):
        self.task["ledger"] = budget({"data_requests": 1})
        with patch("urllib.request.urlopen", return_value=response({"errorcode": 0, "data": {"access_token": ACCESS}})) as transport, self.assertRaises(ContractError):
            self.acquire()
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 1)

    def test_auth_elapsed_time_and_delivery_reserve_stop_business_dispatch(self):
        self.task["ledger"] = budget({"seconds": 100})
        clock = [0]
        def delayed(request, timeout):
            self.assertLessEqual(timeout, 40)
            clock[0] = 41
            return response({"errorcode": 0, "data": {"access_token": ACCESS}})
        with patch("time.monotonic", side_effect=lambda: clock[0]), patch("urllib.request.urlopen", side_effect=delayed) as transport, self.assertRaises(ContractError):
            self.acquire()
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(self.task["ledger"]["used"]["seconds"], 41)
        self.assertEqual(self.journal[-1]["ledger"]["used"]["seconds"], 41)

    def test_offline_and_missing_credentials_stop_before_network(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")) as transport:
            self.collector.offline = True
            with self.assertRaises(ContractError):
                self.acquire()
            self.collector.offline = False
            for credentials in ({"access_token": "", "refresh_token": ""}, {"refresh_token": 12}):
                with self.assertRaises(ContractError):
                    self.acquire(credentials)
            self.task["ledger"] = budget({"seconds": 60})
            with self.assertRaises(ContractError):
                self.acquire()
        self.assertEqual(transport.call_count, 0)
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 0)
