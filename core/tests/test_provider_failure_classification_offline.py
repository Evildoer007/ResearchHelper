"""Independent vendor-failure checks. Responses are fixtures, not permissions."""
import json
import sys
import types
import unittest
from unittest.mock import Mock, patch

from researchhelper.contracts import ContractError, budget
from researchhelper.evidence import EvidenceCollector


class ProviderFailureClassificationTests(unittest.TestCase):
    def setUp(self):
        self.task = {"id": "rh_provider_fixture", "version": 1, "batch": 1, "ledger": budget()}
        self.parameters = {"ts_code": "510300.SH", "start_date": "20260929", "end_date": "20260930",
                           "fields": "ts_code,trade_date,close"}
        self.collector = EvidenceCollector(offline=False)

    def dispatch_failure(self, exception):
        client = Mock()
        client.fund_daily.side_effect = exception
        provider = types.SimpleNamespace(pro_api=Mock(return_value=client))
        with patch.dict(sys.modules, {"tinyshare": provider}), self.assertRaises(ContractError) as error:
            self.collector.external(self.task, "tinyshare", "fund_daily", self.parameters,
                                    {"token": "private-fixture-secret"})
        provider.pro_api.assert_called_once()
        client.fund_daily.assert_called_once_with(**self.parameters)
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 1)
        self.assertEqual(self.task["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(len(self.task["ledger"]["dispatches"]), 1)
        self.assertNotIn("private-fixture-secret", str(error.exception) + json.dumps(self.task))
        return self.task["ledger"]["dispatches"][0], str(error.exception)

    def test_expired_credential_is_classified_without_echo_retry_or_fallback(self):
        permission = type("TinySharePermissionError", (Exception,), {})
        event, message = self.dispatch_failure(permission("积分授权码已过期：private-fixture-secret"))
        self.assertEqual(event["failure_code"], "credential_expired")
        self.assertEqual(event["status"], "授权已过期")
        self.assertIn("更新ResearchHelper独立配置", message)

    def test_permission_failure_is_not_claimed_to_be_expiration(self):
        permission = type("TinySharePermissionError", (Exception,), {})
        event, message = self.dispatch_failure(permission("未开通权限：private-fixture-secret"))
        self.assertEqual(event["failure_code"], "permission_denied")
        self.assertNotIn("过期", message)

    def test_unknown_transport_error_does_not_guess_permission(self):
        event, message = self.dispatch_failure(TimeoutError("private-fixture-secret connection timed out"))
        self.assertNotIn("failure_code", event)
        self.assertEqual(event["status"], "调用失败待核查")
        self.assertIn("不猜测权限", message)

    def test_offline_blocks_provider_import_and_dispatch(self):
        provider = types.SimpleNamespace(pro_api=Mock())
        with patch.dict(sys.modules, {"tinyshare": provider}), self.assertRaisesRegex(ContractError, "离线"):
            EvidenceCollector().external(self.task, "tinyshare", "fund_daily", self.parameters,
                                         {"token": "private-fixture-secret"})
        provider.pro_api.assert_not_called()
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 0)
