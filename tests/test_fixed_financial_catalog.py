"""Offline checks for metadata reuse without financial approval or file trust."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modules.evidencecollector.src import ifind_financial_catalog as catalog


class FixedFinancialCatalogueTests(unittest.TestCase):
    def test_only_confirmed_fields_and_distinct_profit_meaning(self):
        rows = catalog.catalogue()["definitions"]
        self.assertEqual(set(rows), {"ths_revenue_stock", "ths_np_atoopc_stock",
                                    "ths_operating_cost_stock", "ths_np_stock", "ths_ncf_from_oa_stock",
                                    "ths_account_receivable_stock", "ths_inventory_stock", "ths_accounts_payable_stock"})
        self.assertEqual(rows["ths_np_atoopc_stock"]["meaning"], "归属于母公司所有者的净利润")
        self.assertNotEqual(rows["ths_np_atoopc_stock"]["metric"], "net_profit")
        self.assertEqual(rows["ths_np_stock"]["metric"], "net_profit")
        self.assertEqual(rows["ths_np_stock"]["meaning"], "净利润")
        self.assertIsNone(catalog.definition("ths_net_profit_stock"))
        self.assertIsNone(catalog.parameter_template("unknown"))
        self.assertIsNone(catalog.definition({"indicator": "ths_revenue_stock"}))

    def test_ordered_explicit_parameters_without_latest_defaults(self):
        for indicator in catalog.catalogue()["definitions"]:
            rows = catalog.parameter_template(indicator)
            self.assertEqual([row["name"] for row in rows], ["D", "C"])
            self.assertEqual([row["type"] for row in rows], ["enum_or_input", "enum"])
            self.assertEqual(rows[0]["choice"], {})
            self.assertTrue(rows[0]["validation"]["explicit_quarter_end_required"])
            self.assertTrue(rows[0]["validation"]["not_after_task_cutoff"])
            self.assertTrue(rows[0]["validation"]["not_after_current_date"])
            self.assertEqual(set(rows[1]["choice"].values()), {"1", "2", "3", "4"})
            self.assertNotIn("defaultValue", json.dumps(rows))

    def test_supplement_has_its_own_exact_provenance(self):
        expected_files = {
            "ths_operating_cost_stock": "3-get_indicator_params.json",
            "ths_np_stock": "4-get_indicator_params.json",
            "ths_ncf_from_oa_stock": "5-get_indicator_params.json",
        }
        root = "result/acceptance/fixed-financial-definitions-20261023-v2/"
        for indicator, parameter_file in expected_files.items():
            row = catalog.definition(indicator)
            proof = row["source_proof"]
            self.assertEqual(proof["indicator_response"]["artifact"], root + "2-get_indicators.json")
            self.assertEqual(proof["indicator_response"]["sha256"],
                             "97fd83fb38c3938f8a5e4ee7ab4bb6bace3e2662d495b4158c42569704651ee0")
            self.assertEqual(proof["parameter_response"]["artifact"], root + parameter_file)
            self.assertEqual(proof["parameter_response"]["sha256"],
                             "5d7f0d20c1647a81db26c983817c1ec5f5c59743b501bb658d67a7704fb495ff")
            self.assertIn("一般企业", proof["observed_catalogue_path"][0])
            self.assertEqual(row["data_attributes"]["unit"], "unverified")
            self.assertEqual(row["data_attributes"]["currency"], "unverified")
        snapshot = json.dumps(catalog.catalogue())
        self.assertNotIn("600519.SH", snapshot)
        self.assertNotIn("20260630", snapshot)

    def test_metadata_reuse_does_not_approve_data_or_authority(self):
        snapshot = catalog.catalogue()
        self.assertFalse(snapshot["authorization_granted"])
        self.assertFalse(snapshot["financial_values_approved"])
        for row in snapshot["definitions"].values():
            self.assertTrue(row["parameter_reuse_allowed"])
            self.assertFalse(row["financial_values_approved"])
            self.assertEqual(set(row["data_attributes"].values()), {"unverified"})
            for key in ("indicator_response", "parameter_response"):
                self.assertRegex(row["source_proof"][key]["sha256"], r"^[0-9a-f]{64}$")
                self.assertFalse(Path(row["source_proof"][key]["artifact"]).is_absolute())

    def test_returned_values_cannot_mutate_trusted_metadata(self):
        before = catalog.catalogue_digest()
        catalog.catalogue()["definitions"].clear()
        catalog.definition("ths_revenue_stock")["data_attributes"]["unit"] = "元"
        catalog.parameter_template("ths_revenue_stock")[1]["choice"].clear()
        self.assertEqual(catalog.catalogue_digest(), before)
        self.assertEqual(len(catalog.parameter_template("ths_revenue_stock")[1]["choice"]), 4)

    def test_corrupted_builtin_content_fails_closed(self):
        with patch.dict(catalog._CATALOGUE, {"authorization_granted": True}):
            for reader, args in ((catalog.catalogue, ()), (catalog.catalogue_digest, ()),
                                 (catalog.definition, ("ths_revenue_stock",)),
                                 (catalog.parameter_template, ("ths_revenue_stock",))):
                with self.assertRaises(ValueError):
                    reader(*args)

    def test_standalone_import_without_runtime_artifacts(self):
        # A copied module models the frozen App/Skill package: no repository,
        # task Store, relative imports or diagnostic JSON files are present.
        source = Path(catalog.__file__).read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            module_path = Path(directory) / "ifind_financial_catalog.py"
            module_path.write_bytes(source)
            spec = importlib.util.spec_from_file_location("isolated_catalog", module_path)
            isolated = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(isolated)
            with patch("builtins.open", side_effect=AssertionError("Unexpected file read")):
                self.assertEqual(isolated.catalogue_digest(), catalog.catalogue_digest())
                self.assertEqual(isolated.catalogue(), catalog.catalogue())
