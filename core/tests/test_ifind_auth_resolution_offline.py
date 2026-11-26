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
PARAMS = {"codes": "600000.SH", "indicators": "close", "startdate": "2026-09-01", "enddate": "2026-09-02",
          "functionpara": {"Currency": "RMB", "CPS": "1", "Fill": "Omit"}}


def response(value):
    return io.BytesIO(json.dumps(value).encode())


class IfindAuthResolutionTests(unittest.TestCase):
    def setUp(self):
        self.task = {"id": "auth-resolution-fixture", "version": 1, "batch": 0, "ledger": budget(),
                     "cutoff": "2026-09-02", "objects": ["600000.SH"]}
        network = patch("socket.socket.connect", side_effect=AssertionError("No external network"))
        network.start()
        self.addCleanup(network.stop)
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

    def test_access_only_has_no_token_exchange(self):
        with patch("urllib.request.urlopen", return_value=response(self.business)) as transport:
            self.acquire({"access_token": ACCESS})
        self.assertEqual(transport.call_count, 1)
        self.assertTrue(transport.call_args.args[0].full_url.endswith('/cmd_history_quotation'))
        self.assertEqual(transport.call_args.args[0].get_header("Access_token"), ACCESS)

    def test_refresh_takes_priority_over_a_saved_access_token(self):
        saved_access = "stale-access-fixture"
        replies = [response({"errorcode": 0, "data": {"access_token": ACCESS}}), response(self.business)]
        with patch("urllib.request.urlopen", side_effect=replies) as transport:
            self.acquire({"access_token": saved_access, "refresh_token": REFRESH})
        auth, business = [call.args[0] for call in transport.call_args_list]
        self.assertTrue(auth.full_url.endswith('/get_access_token'))
        self.assertEqual(auth.get_header("Refresh_token"), REFRESH)
        self.assertEqual(business.get_header("Access_token"), ACCESS)
        self.assertNotIn(saved_access, json.dumps(self.journal))

    def test_failed_refresh_records_authentication_without_using_saved_access(self):
        error = urllib.error.HTTPError("https://example.invalid", 401, REFRESH, {}, None)
        with patch("urllib.request.urlopen", side_effect=error) as transport, self.assertRaisesRegex(ContractError, "HTTP状态401") as raised:
            self.acquire({"access_token": ACCESS, "refresh_token": REFRESH})
        self.assertEqual(transport.call_count, 1)
        self.assertTrue(transport.call_args.args[0].full_url.endswith('/get_access_token'))
        self.assertNotIn(REFRESH, str(raised.exception))
        event = self.task["ledger"]["dispatches"][-1]
        self.assertEqual(event["http_status"], 401)
        self.assertEqual(event["supplier_rejection_category"], "authentication_failed")

    def test_cache_is_bound_to_refresh_and_expires_in_five_minutes(self):
        clock = [0]
        calls = []
        def transport(request, **_):
            calls.append(request.full_url)
            return response({"errorcode": 0, "data": {"access_token": ACCESS}} if request.full_url.endswith('get_access_token') else self.business)
        with patch("time.monotonic", side_effect=lambda: clock[0]), patch("urllib.request.urlopen", side_effect=transport):
            self.assertEqual(self.collector.ifind_authentication_cost({"refresh_token": REFRESH}), 1)
            self.acquire()
            self.assertEqual(self.collector.ifind_authentication_cost({"refresh_token": REFRESH}), 0)
            self.acquire()
            self.assertEqual(sum(url.endswith('get_access_token') for url in calls), 1)
            self.assertEqual(self.collector.ifind_authentication_cost({"refresh_token": "different-refresh-fixture"}), 1)
            self.acquire({"refresh_token": "different-refresh-fixture"})
            self.assertEqual(sum(url.endswith('get_access_token') for url in calls), 2)
            clock[0] = 301
            self.assertEqual(self.collector.ifind_authentication_cost({"refresh_token": "different-refresh-fixture"}), 1)
            self.assertEqual(self.collector.ifind_authentication_cost({"access_token": ACCESS, "refresh_token": REFRESH}), 1)
            self.assertEqual(self.collector.ifind_authentication_cost({"access_token": ACCESS}), 0)
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
