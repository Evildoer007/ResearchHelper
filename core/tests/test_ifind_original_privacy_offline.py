"""Observed iFinD resource route with synthetic authorizations only."""
import copy
import io
import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from researchhelper.evidence import (IFIND_RESOURCE_HOST, IFIND_RESOURCE_PATH, _ifind_authorized_resource,
    _download_filing_pdf, _local_https_proxy, _PublicFilingConnection)
from tests import test_filing_acquisition_offline as fixtures


class IfindOriginalPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FilingAcquisitionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task, self.collector = self.fixture.task, self.fixture.collector
        self.row = copy.deepcopy(self.fixture.row)
        self.row["seq"] = "42"
        self.row["pdfURL"] = "http://" + IFIND_RESOURCE_HOST + IFIND_RESOURCE_PATH + "?seq=42&token=fabricated-signature&userid=fixture-user&ip=192.0.2.1"

    def response(self, **changes):
        row = dict(self.row, **changes)
        return io.BytesIO(json.dumps({"errorcode": 0, "tables": [row]}).encode())

    def catalog(self):
        with patch("researchhelper.evidence.urllib.request.urlopen", return_value=self.response()):
            record = self.collector.external(self.task, "ifind", "report_query", self.fixture.params, {"access_token": "fabricated-source"})
        self.task["source_records"].append(record)
        return record

    def selection(self, record):
        return {"catalog_id": record["id"], "source_id": "42"}

    def test_signature_is_memory_only_bound_to_selection_and_never_clear_http(self):
        catalog = self.catalog()
        factory, connection = self.fixture.transport()
        record = self.collector.external(self.task, "ifind", "filing_original", self.selection(catalog), {})
        self.assertEqual(factory.call_args.args, (IFIND_RESOURCE_HOST,))
        self.assertEqual(factory.call_args.kwargs["port"], 443)
        self.assertIn("token=fabricated-signature", connection.request.call_args.args[1])
        self.assertEqual(record["stages"]["parse"], "已解析")
        self.assertNotIn("fabricated-signature", json.dumps([catalog, record, self.task["ledger"]]))
        self.assertFalse(self.collector._ifind_original_cache)
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 2)
        self.assertEqual(record["stages"]["source_check"], "待核验")

    def test_restarted_collector_requeries_same_scope_with_same_ledger(self):
        catalog = self.catalog()
        self.collector._ifind_original_cache.clear()
        self.fixture.transport()
        with patch("researchhelper.evidence.urllib.request.urlopen", return_value=self.response()) as query:
            record = self.collector.external(self.task, "ifind", "filing_original", self.selection(catalog), {"access_token": "fabricated-source"})
        self.assertEqual(query.call_count, 1)
        self.assertEqual(json.loads(query.call_args.args[0].data), self.fixture.params)
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 3)
        self.assertEqual(record["directory"]["source_id"], "42")

    def test_changed_public_row_never_silently_replaces_selected_filing(self):
        catalog = self.catalog()
        self.collector._ifind_original_cache.clear()
        with patch("researchhelper.evidence.urllib.request.urlopen", return_value=self.response(reportTitle="不同公告")), \
                patch("researchhelper.evidence._download_filing_pdf") as download:
            with self.assertRaisesRegex(ContractError, "不一致"):
                self.collector.external(self.task, "ifind", "filing_original", self.selection(catalog), {"access_token": "fabricated-source"})
        download.assert_not_called()
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 2)

    def test_expired_signature_requires_credentials_without_stealing_download_budget(self):
        catalog = self.catalog()
        key = next(iter(self.collector._ifind_original_cache))
        self.collector._ifind_original_cache[key] = (self.row["pdfURL"], 0)
        with self.assertRaisesRegex(ContractError, "配置"):
            self.collector.external(self.task, "ifind", "filing_original", self.selection(catalog), {})
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 1)

    def test_selected_seq_host_path_duplicates_and_unknown_parameters_are_rejected(self):
        urls = [self.row["pdfURL"].replace("seq=42", "seq=43"),
                self.row["pdfURL"].replace(IFIND_RESOURCE_HOST, "evil.example"),
                self.row["pdfURL"].replace(IFIND_RESOURCE_PATH, "/other"),
                self.row["pdfURL"] + "&token=another", self.row["pdfURL"] + "&other=1"]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ContractError):
                _ifind_authorized_resource(url, "42")

    def test_redacted_public_locator_cannot_itself_be_downloaded(self):
        catalog = self.catalog()
        with self.assertRaises(ContractError):
            _download_filing_pdf(catalog["catalog_entries"][0]["original_url"], 5)

    def test_signed_transport_errors_do_not_echo_authorization_or_follow_redirect(self):
        catalog = self.catalog()
        self.fixture.transport(status=302)
        with self.assertRaises(ContractError) as failure:
            self.collector.external(self.task, "ifind", "filing_original", self.selection(catalog), {})
        self.assertNotIn("fabricated-signature", str(failure.exception))
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 2)
        self.assertFalse(self.collector._ifind_original_cache)

    def test_local_proxy_keeps_selected_hostname_as_connect_target_and_tls_name(self):
        from unittest.mock import MagicMock
        raw = MagicMock()
        addresses = [(2, 1, 6, "", ("127.0.0.1", 7890))]
        with patch("researchhelper.evidence.urllib.request.getproxies", return_value={"https": "http://127.0.0.1:7890"}), \
                patch("socket.getaddrinfo", return_value=addresses) as dns, patch("socket.socket", return_value=raw), \
                patch.object(_PublicFilingConnection, "_tunnel") as tunnel:
            connection = _PublicFilingConnection(IFIND_RESOURCE_HOST, use_local_proxy=True)
            connection._context = MagicMock()
            connection.connect()
            self.assertEqual(dns.call_args.args, ("127.0.0.1", 7890))
            raw.connect.assert_called_once_with(("127.0.0.1", 7890))
            tunnel.assert_called_once()
            self.assertEqual(connection._tunnel_host, IFIND_RESOURCE_HOST)
            self.assertEqual(connection._tunnel_port, 443)
            self.assertEqual(connection._context.wrap_socket.call_args.kwargs["server_hostname"], IFIND_RESOURCE_HOST)
            connection.close()

    def test_proxy_is_not_an_arbitrary_private_or_external_routing_escape(self):
        for value in ("http://192.168.1.1:7890", "http://evil.example:7890", "http://user:secret@127.0.0.1:7890", "https://127.0.0.1:7890", "http://127.0.0.1:7890/other"):
            with self.subTest(value=value), patch("researchhelper.evidence.urllib.request.getproxies", return_value={"https": value}):
                with self.assertRaises(ContractError):
                    _local_https_proxy()

    def test_direct_connection_still_rejects_fake_ip_without_explicit_proxy(self):
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("198.18.0.110", 443))]):
            with self.assertRaisesRegex(ContractError, "公共网络"):
                _PublicFilingConnection(IFIND_RESOURCE_HOST).connect()

    def test_signature_reacquisition_reserves_download_budget_before_query(self):
        catalog = self.catalog()
        self.collector._ifind_original_cache.clear()
        self.task["ledger"]["limits"]["data_requests"] = 2
        with patch("researchhelper.evidence.urllib.request.urlopen") as query:
            with self.assertRaisesRegex(ContractError, "原预算"):
                self.collector.external(self.task, "ifind", "filing_original", self.selection(catalog), {"access_token": "fabricated-source"})
        query.assert_not_called()
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 1)


if __name__ == "__main__":
    unittest.main()
