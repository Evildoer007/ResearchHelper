"""Offline safety checks for the separate, single-send financial inspection."""
import unittest

from tests.inspect_ifind_structured_financial_query import inspect_response
from researchhelper.contracts import ContractError


class StructuredQueryInspectionTests(unittest.TestCase):
    def test_returned_success_remains_unapproved(self):
        result = inspect_response({"errorcode": 0, "tables": [
            {"table": {"净利润[20260630]": [1]}, "thscode": ["600519.SH"]}]}, ())
        self.assertEqual(result["outcome"], "retained_response_for_review")
        self.assertFalse(result["financial_evidence_approved"])
        self.assertEqual(result["tables"][0]["columns"], ["净利润[20260630]"])

    def test_boolean_and_vendor_failure_are_not_success(self):
        for code in (False, True, -4001, "-5", None):
            with self.subTest(code=code):
                result = inspect_response({"errorcode": code}, ())
                self.assertEqual(result["outcome"], "supplier_rejected_query")

    def test_unknown_structure_does_not_invent_table(self):
        for tables in (None, {}, "not-a-table"):
            result = inspect_response({"errorcode": 0, "tables": tables}, ())
            self.assertEqual(result["tables"], [])
            self.assertFalse(result["financial_evidence_approved"])

    def test_private_value_in_body_is_not_retained(self):
        with self.assertRaises(ContractError):
            inspect_response({"errorcode": 0, "errmsg": "fixture-secret"}, ("fixture-secret",))

    def test_non_object_is_not_empty_result(self):
        for payload in (None, [], "", 0):
            with self.subTest(payload=payload), self.assertRaises(ContractError):
                inspect_response(payload, ())


if __name__ == "__main__":
    unittest.main()
