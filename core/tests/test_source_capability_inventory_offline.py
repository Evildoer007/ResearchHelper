"""Public capability claims match executable registries, without provider calls."""
import copy
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, budget, fingerprint
from researchhelper.evidence import (CAPABILITIES, FIELD_REGISTRY, IDENTITY_REQUEST_REGISTRY,
    FILING_REQUEST_REGISTRY, EvidenceCollector, data_request_contract, extract_material, snapshot_record)
from researchhelper.financial_sources import FINANCIAL_REQUEST_REGISTRY
from researchhelper.reasoning import source_views


class SourceCapabilityInventoryTests(unittest.TestCase):
    def setUp(self):
        self.task = {"id": "capability-fixture", "version": 1, "question": "核对600000.SH原文",
            "objects": ["600000.SH"], "cutoff": "2026-10-01T12:00:00+08:00",
            "questions": [{"id": "q1"}], "ledger": budget(), "source_records": [], "materials": []}
        self.task["input_hash"] = fingerprint({"question": self.task["question"], "objects": self.task["objects"]})

    def test_public_operations_exactly_match_implemented_registries(self):
        expected = set(FIELD_REGISTRY) | set(IDENTITY_REQUEST_REGISTRY) | set(FILING_REQUEST_REGISTRY) | set(FINANCIAL_REQUEST_REGISTRY)
        actual = [source + "." + operation["operation"] for source, capability in CAPABILITIES.items()
                  for operation in capability["registered_operations"]]
        self.assertEqual(set(actual), expected)
        self.assertEqual(len(actual), len(set(actual)))

    def test_provider_endpoints_exclude_internal_and_documented_only_operations(self):
        for source, capability in CAPABILITIES.items():
            registered = capability["registered_operations"]
            self.assertEqual(set(capability.get("endpoints", [])), {item["operation"] for item in registered if not item["internal"]})
            self.assertFalse(set(capability.get("endpoints", [])) & {item["operation"] for item in capability.get("documented_only", [])})
        originals = next(item for item in CAPABILITIES["ifind"]["registered_operations"] if item["operation"] == "filing_original")
        self.assertTrue(originals["internal"])

    def test_documented_only_requests_are_blocked_before_network_or_budget(self):
        collector = EvidenceCollector(offline=False)
        before = copy.deepcopy(self.task)
        with patch("researchhelper.evidence.urllib.request.urlopen") as transport:
            for item in CAPABILITIES["ifind"]["documented_only"]:
                with self.subTest(operation=item["operation"]), self.assertRaisesRegex(ContractError, "未登记"):
                    collector.external(self.task, "ifind", item["operation"], {}, {"access_token": "FAKE_TOKEN"})
        transport.assert_not_called()
        self.assertEqual(self.task, before)

    def test_documented_only_cannot_create_a_new_registered_snapshot(self):
        for item in CAPABILITIES["ifind"]["documented_only"]:
            with self.subTest(operation=item["operation"]), self.assertRaisesRegex(ContractError, "未登记"):
                snapshot_record(self.task, "ifind", item["operation"], {}, {"errorcode": 0, "tables": []})

    def test_public_inventory_and_planning_tools_have_identical_scope(self):
        before = copy.deepcopy(self.task)
        tools = data_request_contract(self.task)["tools"]
        planned = {item["source"] + "." + item["operation"] for item in tools}
        public = {source + "." + item["operation"] for source, capability in CAPABILITIES.items()
                  for item in capability["registered_operations"]}
        self.assertEqual(public, planned)
        self.assertEqual(self.task, before)

    def test_financial_records_remain_raw_only_without_approval_or_credentials(self):
        financial = [item for item in CAPABILITIES["tinyshare"]["registered_operations"] if item["operation"] in ("income", "cashflow")]
        self.assertEqual(len(financial), 2)
        for item in financial:
            self.assertTrue(item["raw_only"])
            self.assertEqual(item["fields"], sorted(FINANCIAL_REQUEST_REGISTRY["tinyshare." + item["operation"]]["fields"]))
            self.assertNotIn("approved", item)
        self.assertEqual(CAPABILITIES["wind"]["registered_operations"], [])
        import json
        serialized = json.dumps(CAPABILITIES)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("access_token", serialized)

    def test_html_format_claim_has_an_actual_parser(self):
        self.assertIn("HTML", CAPABILITIES["material"]["formats"])
        parsed = extract_material("sample.html", b"<html><body><p>Retained statement.</p></body></html>")
        self.assertEqual(parsed["status"], "已解析")
        self.assertTrue(any("Retained statement." in part["text"] for part in parsed["parts"]))

    def test_old_retained_original_is_readable_without_upgrading_it(self):
        snapshot = {"errorcode": 0, "tables": []}
        self.task["source_records"] = [{"id": "legacy-source", "kind": "api", "source": "ifind",
            "task_id": self.task["id"], "research_version": 1, "operation": "basic_data_service",
            "parameters": {}, "snapshot": snapshot, "hash": fingerprint(snapshot), "content_version": 1,
            "status": "成功待核验"}]
        before = copy.deepcopy(self.task)
        views = source_views(self.task)
        self.assertEqual(views[0]["raw_response"], snapshot)
        self.assertEqual(views[0]["operation"], "basic_data_service")
        self.assertEqual(self.task, before)
        self.assertNotIn("basic_data_service", CAPABILITIES["ifind"]["endpoints"])


if __name__ == "__main__":
    unittest.main()
