"""Financial retrieval contracts using fabricated payloads only."""

import copy
import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from researchhelper.financial_sources import (
    FINANCIAL_RECORD_KEYS, FINANCIAL_REQUEST_REGISTRY, financial_request_shapes,
    validate_financial_request, validate_financial_snapshot,
)


class FinancialSourceRequestTests(unittest.TestCase):
    def setUp(self):
        self.task = {"id": "fixture", "version": 1, "input_hash": "fixture",
                     "cutoff": "2026-10-02T18:00:00+08:00", "objects": ["600000.SH"],
                     "source_records": [], "questions": [{"id": "q1"}]}
        self.params = {"ts_code": "600000.SH", "fields": ",".join(FINANCIAL_RECORD_KEYS) + ",revenue",
                       "start_date": "20260701", "end_date": "20260930", "period": "20260630", "report_type": "1"}
        self.row = {"ts_code": "600000.SH", "ann_date": "20260828", "f_ann_date": "20260828",
                    "end_date": "20260630", "report_type": "1", "comp_type": "2", "update_flag": "1", "revenue": 123}
        network = patch("socket.socket.connect", side_effect=AssertionError("Network forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def test_request_dates_are_announcement_window_not_reporting_period(self):
        self.assertEqual(validate_financial_request(self.task, "income", self.params), ["2026-07-01", "2026-09-30"])
        cash = dict(self.params, fields=",".join(FINANCIAL_RECORD_KEYS) + ",n_cashflow_act")
        self.assertEqual(validate_financial_request(self.task, "cashflow", cash), ["2026-07-01", "2026-09-30"])

    def test_registry_remains_raw_without_invented_units_or_permissions(self):
        self.assertEqual(set(FINANCIAL_REQUEST_REGISTRY), {"tinyshare.income", "tinyshare.cashflow"})
        for definition in FINANCIAL_REQUEST_REGISTRY.values():
            self.assertTrue(definition["raw_only"])
            self.assertEqual(definition["permission"], "未验收")
            self.assertEqual(definition["live_samples"], [])
            for field in definition["fields"].values():
                self.assertEqual(set(field), {"definition"})
        serialized = json.dumps(FINANCIAL_REQUEST_REGISTRY, ensure_ascii=False)
        for invented in ('"raw_unit"', '"currency"', '"factor"', "CNY", "CAS"):
            self.assertNotIn(invented, serialized)

    def test_shape_requires_explicit_period_without_default_example(self):
        for shape in financial_request_shapes(self.task):
            self.assertIsNone(shape["example"])
            self.assertIn("period", shape["parameter_keys"])
            self.assertEqual(shape["fixed_parameters"], {"report_type": "1"})
            self.assertEqual(shape["available_codes"], ["600000.SH"])
            self.assertIn("公告", shape["parameter_semantics"]["end_date"])
            self.assertTrue(shape["raw_fields"])
            self.assertEqual(shape["numeric_fields"], [])
            self.assertFalse(shape["numeric_evidence_registered"])

    def test_bad_parameter_values_rejected(self):
        changes = [
            {"report_type": value} for value in (1, "2", "3", "4", "5", "6", "7", "11", "")
        ] + [{"period": value} for value in ("20261031", "20261231", "2026-06-30", "20260230", "")]
        changes += [{"ts_code": value} for value in ("600001.SH", "600000.SH,000001.SZ", "510300.SH", "00700.HK", "000001", "")]
        changes += [{"start_date": "20261001", "end_date": "20260901"}, {"end_date": "20261003"},
                    {"start_date": "2026-07-01"}, {"is_calc": 1}, {"ann_date": "20260828"}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_financial_request(self.task, "income", dict(self.params, **change))

    def test_missing_cutoff_and_missing_parameters_rejected(self):
        task = dict(self.task)
        task.pop("cutoff")
        with self.assertRaises(ContractError):
            validate_financial_request(task, "income", self.params)
        for key in self.params:
            params = dict(self.params)
            params.pop(key)
            with self.subTest(key=key), self.assertRaises(ContractError):
                validate_financial_request(self.task, "income", params)

    def test_unknown_and_incomplete_fields_rejected(self):
        for fields in (",".join(FINANCIAL_RECORD_KEYS), self.params["fields"] + ",total_revenue",
                       self.params["fields"] + ",revenue", "ts_code,revenue",
                       self.params["fields"].replace("update_flag,", ""), self.params["fields"] + ",n_cashflow_act"):
            with self.subTest(fields=fields), self.assertRaises(ContractError):
                validate_financial_request(self.task, "income", dict(self.params, fields=fields))
        with self.assertRaises(ContractError):
            validate_financial_request(self.task, "income_vip", self.params)

    def test_success_preserves_every_distinct_row_and_raw_values(self):
        payload = {"code": 0, "data": [self.row, dict(self.row, revenue=None, update_flag="0", f_ann_date=None)]}
        original = copy.deepcopy(payload)
        validate_financial_snapshot(self.task, "income", self.params, payload)
        self.assertEqual(payload, original)
        self.assertEqual(len(payload["data"]), 2)

    def test_empty_and_error_responses_remain_unchanged(self):
        for payload in ({"code": 0, "data": []}, {"code": "0", "data": []}, {"code": -1, "msg": "synthetic denied", "data": None}):
            original = copy.deepcopy(payload)
            validate_financial_snapshot(self.task, "income", self.params, payload)
            self.assertEqual(payload, original)

    def test_mismatched_response_rows_rejected(self):
        for change in ({"ts_code": "600001.SH"}, {"end_date": "20260331"}, {"report_type": "2"},
                       {"report_type": 1}, {"ann_date": "20260630"}, {"f_ann_date": "20261003"},
                       {"ann_date": "20260230"}):
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_financial_snapshot(self.task, "income", self.params, {"code": 0, "data": [dict(self.row, **change)]})
        missing = dict(self.row)
        missing.pop("update_flag")
        for payload in ({"code": 0, "data": [missing]}, {"code": 0, "data": [None]},
                        {"code": 0, "data": {}}, {"code": True, "data": []}, []):
            with self.subTest(payload=payload), self.assertRaises(ContractError):
                validate_financial_snapshot(self.task, "income", self.params, payload)


if __name__ == "__main__":
    unittest.main()
