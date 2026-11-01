"""URL-bearing provider responses retain business fields, never credentials."""
import copy
import json
import unittest

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.evidence import business_payload, safe_parameters, snapshot_record, validate_filing_catalog_binding
from tests import test_filing_acquisition_offline as filing_fixtures


class BusinessUrlPrivacyTests(unittest.TestCase):
    def fixture(self):
        helper = filing_fixtures.FilingAcquisitionTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        return helper

    def test_real_shaped_signed_url_has_no_token_account_or_ip(self):
        value = "http://ft.10jqka.com.cn/standardgwapi/api/news_service/pubnote/download/resource?seq=42&token=fabricated-secret&userid=fixture-account&ip=192.0.2.1"
        result = business_payload({"pdfURL": value, "seq": "42", "reportTitle": "公开研究内容"})
        self.assertEqual(result, {"pdfURL": value.split("?")[0], "seq": "42", "reportTitle": "公开研究内容"})
        self.assertEqual(business_payload(result), result)

    def test_encoded_parameter_case_fragment_userinfo_and_cloud_signatures(self):
        cases = ["https://example.com/a?%74oken=secret", "https://example.com/a?Access_Token=secret",
                 "https://example.com/a#refresh_token=secret", "https://user:secret@example.com/a",
                 "https://example.com/a?X-Amz-Signature=secret", "https://example.com/a?X-Goog-Credential=secret"]
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(business_payload(value), "https://example.com/a")

    def test_embedded_url_in_nested_message_is_also_sanitized(self):
        result = business_payload({"tables": [{"pdfURL": "https://example.com/a?sig=fabricated-signature"}],
                                   "errmsg": "下载地址：https://example.com/b?token=fabricated-token 其他说明"})
        text = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("fabricated", text)
        self.assertIn("其他说明", text)

    def test_public_urls_and_business_numbers_are_unchanged(self):
        value = {"source": "https://www.sse.com.cn/a.pdf", "url": "https://example.com/a?seq=42", "value": 12.3}
        self.assertEqual(business_payload(value), value)

    def test_private_url_cannot_enter_query_parameters(self):
        with self.assertRaises(ContractError):
            safe_parameters({"url": "https://example.com/a?api_key=fabricated-key"})

    def test_snapshot_hash_and_catalog_binding_use_retained_business_content(self):
        helper = self.fixture()
        row = copy.deepcopy(helper.row)
        row["pdfURL"] = "http://ft.10jqka.com.cn/standardgwapi/api/news_service/pubnote/download/resource?seq=42&token=fabricated-token&userid=fabricated-account&ip=192.0.2.1"
        wire = {"errorcode": 0, "tables": [row]}
        record = snapshot_record(helper.task, "ifind", "report_query", helper.params, wire)
        helper.task["source_records"].append(record)
        directory = record["catalog_entries"][0]
        self.assertNotIn("fabricated-token", json.dumps(record))
        self.assertEqual(record["hash"], fingerprint(record["snapshot"]))
        self.assertNotEqual(record["hash"], fingerprint(wire))
        self.assertTrue(record["snapshot_privacy"]["redacted"])
        self.assertEqual(record["snapshot_privacy"]["business_snapshot_hash"], record["hash"])
        self.assertEqual(validate_filing_catalog_binding(helper.task, directory), directory)
        self.assertTrue(directory["downloadable"])
        self.assertEqual(directory["original_fetch"], "ifind_temporary_resource")
        self.assertNotIn("?", directory["original_url"])
        self.assertEqual(directory["catalog_ref"]["original_fields"]["reportTitle"], row["reportTitle"])

    def test_plain_public_pdf_snapshot_does_not_claim_redaction(self):
        helper = self.fixture()
        record = helper.catalog()
        self.assertNotIn("snapshot_privacy", record)


if __name__ == "__main__":
    unittest.main()
