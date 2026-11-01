"""Scoped retained-document definitions must never become universal defaults."""

import hashlib
import json
import unittest
from unittest.mock import patch

from modules.evidencecollector.src import ifind_financial_catalog as catalog


class CalibratedCatalogueTests(unittest.TestCase):
    def test_exact_code_period_statement_and_metric_scope(self):
        rows = catalog.evidence_definitions()
        self.assertEqual(set(rows), {"ths_revenue_stock", "ths_operating_cost_stock",
                                    "ths_np_stock", "ths_ncf_from_oa_stock"})
        for code, definition in rows.items():
            claims = definition["claims"]
            self.assertEqual(claims["indicator"], code)
            self.assertEqual(claims["applicable_codes"], ["600519.SH"])
            self.assertEqual(claims["applicable_periods"], ["20260630"])
            self.assertEqual(claims["report_type"], "1")
            self.assertEqual(claims["parameter_order"], ["D", "C"])
            self.assertEqual(claims["unit"], "元")
            self.assertEqual(claims["currency"], "CNY")
            self.assertEqual(claims["accounting_standard"], "CAS")
            self.assertEqual(claims["statement_scope"], "consolidated")
            self.assertEqual(claims["period_kind"], "cumulative")
        self.assertEqual(rows["ths_np_stock"]["claims"]["metric"], "net_profit")

    def test_original_proof_and_hash_match_without_old_values(self):
        rows = catalog.evidence_definitions()
        for definition in rows.values():
            basis = definition["basis"]
            self.assertEqual(basis["kind"], "vendor_document")
            self.assertEqual(basis["payload"]["claims"], definition["claims"])
            canonical = json.dumps(basis["payload"], ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False)
            self.assertEqual(hashlib.sha256(canonical.encode()).hexdigest(), basis["hash"])
            self.assertEqual(basis["payload"]["calibration_sha256"],
                             "2b03880729c4a8eab9c0a7ecbad771caa466413b3ab8e2c36fda37ca41aa94fd")
            self.assertEqual(basis["obtained_at"], "2026-10-23T13:34:30+00:00")
        serialized = json.dumps(rows)
        for value in ("90703260964", "9473762565", "46033330566", "70690750119"):
            self.assertNotIn(value, serialized)

    def test_returned_evidence_is_detached_and_generic_units_remain_unverified(self):
        baseline = catalog.evidence_definitions()
        modified = catalog.evidence_definitions()
        modified["ths_np_stock"]["claims"]["applicable_codes"].append("000001.SZ")
        modified["ths_np_stock"]["basis"]["payload"]["excerpts"].clear()
        self.assertEqual(catalog.evidence_definitions(), baseline)
        for definition in catalog.catalogue()["definitions"].values():
            self.assertEqual(definition["data_attributes"]["unit"], "unverified")
            self.assertEqual(definition["data_attributes"]["currency"], "unverified")

    def test_modified_calibration_is_rejected(self):
        with patch.dict(catalog._EVIDENCE_DEFINITIONS, {"forged": {}}):
            with self.assertRaises(ValueError):
                catalog.evidence_definitions()
            with self.assertRaises(ValueError):
                catalog.catalogue_digest()
