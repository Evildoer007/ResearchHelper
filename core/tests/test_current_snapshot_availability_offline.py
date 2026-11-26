"""Current API availability with real local SDK sends; no external provider.

The transport fixture returns explicitly synthetic rows. First publication is
never invented; imported, historical and rebound versions remain inadmissible.
"""

import copy
import json
from pathlib import Path
import unittest

from researchhelper.archive import ResearchArchive
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.contracts import ContractError, fingerprint, now, validate_evidence
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence, current_snapshot_reference, direct_code_reference, snapshot_record
from researchhelper.reasoning import evidence_contract, validate_model_specs
from tests import test_sdk_wire_budget_offline as wire

ROOT = Path(__file__).resolve().parents[2]


class CurrentSnapshotAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = wire.SDKWireBudgetTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent = ResearchAgent(ResearchArchive(self.fixture.root / "store"), ROOT, offline=True)

    def acquired(self, mode="current"):
        task = self.agent.create("核对本次取得的510300.SH收盘价", entry="ETF", objects=["510300.SH"],
            research_mode=mode, questions=[{"id": "q1", "question": "核对收盘原值", "kind": "fact"}])
        record = self.fixture.run_sdk(task)
        task["source_records"].append(record)
        task = self.agent.archive.save(task)
        return task, record

    def spec(self, task, record):
        return {"id": "close", "artifact_id": record["id"], "object": "510300.SH",
            "record_locator": {"row": 0, "field": "close"}, "period": "2026-09-30", "nature": "已实现事实",
            "published_at": None, "publication": {"at": None, "basis_ref": None, "certainty": "未知",
                "precision": "未知", "timezone": "未知", "basis": "首次公开未知，当前响应另有取数记录"},
            "availability": current_snapshot_reference(task, record), "raw_value": 4.5, "value": 4.5,
            "raw_unit": "元", "unit": "元", "conversion_factor": 1, "question_ids": ["q1"],
            "direct_code_reference": direct_code_reference(task, record),
            "review": {"question_ids": ["q1"], "allowed_natures": ["已实现事实"],
                "conditions": ["仅核对本次响应记录的收盘原值"], "reason": "登记字段、对象、观测日及原值一致"}}

    def approved(self, task, record, spec=None):
        spec = spec or self.spec(task, record)
        validate_model_specs(task, [spec])
        item = candidate_evidence(task, record, spec)
        ResearchAnalyzer().assess(task, item, spec["review"], task["source_records"])
        validate_evidence(item, task)
        return item

    def test_current_response_passes_both_receipts_without_publication_claim(self):
        task, record = self.acquired()
        item = self.approved(task, record)
        self.assertIsNone(item["published_at"])
        self.assertEqual(item["publication"]["certainty"], "未知")
        self.assertIn("不证明历史当时可得", "".join(item["applicability_review"]["conditions"]))
        self.assertEqual(task["ledger"]["used"]["data_requests"], 2)
        self.assertEqual(task["ledger"]["used"]["model_calls"], 0)

    def test_saved_result_and_report_preserve_current_basis(self):
        task, record = self.acquired()
        spec = self.spec(task, record)
        done = self.agent.start(task["id"], {"evidence_specs": [spec], "answers": {"q1": {
            "answer": "本次取得的版本记录收盘价为4.5元。", "refs": ["close"]}}})
        self.assertEqual(done["status"], "已完成", done.get("error"))
        self.assertEqual(len(done["evidence"]), 1)
        report = self.agent.export(task["id"])
        self.assertEqual(report["content"]["evidence"][0]["availability"], spec["availability"])
        self.assertIn("未核验", report["html"])
        self.assertIn("不证明历史当时可得", report["html"])
        restarted = ResearchAgent(ResearchArchive(self.fixture.root / "store"), ROOT, offline=True)
        self.assertEqual(restarted.export(task["id"])["digest"], report["digest"])
        validate_evidence(restarted.archive.get(task["id"])["evidence"][0], restarted.archive.get(task["id"]))

    def test_imported_response_does_not_mint_availability(self):
        task, record = self.acquired()
        imported = snapshot_record(task, record["source"], record["operation"], record["parameters"], record["snapshot"])
        self.assertNotIn("acquisition", imported)
        self.assertIsNone(current_snapshot_reference(task, imported))
        changed = copy.deepcopy(task)
        changed["source_records"] = [imported]
        with self.assertRaises(ContractError):
            self.approved(changed, imported, self.spec(task, record))

    def test_historical_task_does_not_accept_current_availability(self):
        task, record = self.acquired()
        spec = self.spec(task, record)
        changed = copy.deepcopy(task)
        changed["research_mode"] = "historical"
        with self.assertRaises(ContractError):
            self.approved(changed, record, spec)

    def test_legacy_task_cannot_gain_current_permission_from_spec(self):
        task, record = self.acquired()
        spec = self.spec(task, record)
        task.pop("research_mode")
        with self.assertRaises(ContractError):
            self.approved(task, record, spec)

    def test_getting_time_may_not_be_written_as_first_publication(self):
        task, record = self.acquired()
        spec = self.spec(task, record)
        spec["published_at"] = record["obtained_at"]
        with self.assertRaises(ContractError):
            self.approved(task, record, spec)

    def test_without_available_reference_strict_publication_gate_remains(self):
        task, record = self.acquired()
        spec = self.spec(task, record)
        spec.pop("availability")
        with self.assertRaises(ContractError):
            validate_model_specs(task, [spec])

    def test_modified_snapshot_or_request_cannot_reuse_acquisition(self):
        task, record = self.acquired()
        for change in ("snapshot", "request"):
            changed = copy.deepcopy(record)
            if change == "snapshot":
                changed["snapshot"]["data"][0]["close"] = 99
                changed["hash"] = fingerprint(changed["snapshot"])
            else:
                changed["parameters"]["start_date"] = "20260929"
            self.assertIsNone(current_snapshot_reference(task, changed))

    def test_cross_task_and_rebound_version_are_rejected(self):
        task, record = self.acquired()
        for key, value in (("id", "another_task"), ("version", 2)):
            changed = copy.deepcopy(task)
            changed[key] = value
            self.assertIsNone(current_snapshot_reference(changed, record))

    def test_modified_acquisition_and_boolean_dispatch_index_are_rejected(self):
        task, record = self.acquired()
        for key, value in (("obtained_at", "2099-01-01T00:00:00+00:00"), ("dispatch_index", False),
                           ("snapshot_hash", "invented"), ("batch", 99)):
            changed = copy.deepcopy(record)
            changed["acquisition"][key] = value
            self.assertIsNone(current_snapshot_reference(task, changed))

    def test_uncompleted_or_unstarted_transport_cannot_approve_current_record(self):
        task, record = self.acquired()
        for key, value in (("status", "失败"), ("network_request_started", False), ("network_worker_reaped", False),
                           ("response_snapshot_hash", "invented"), ("sdk_wire_requests_started", 0)):
            changed = copy.deepcopy(task)
            changed["ledger"]["dispatches"][0][key] = value
            self.assertIsNone(current_snapshot_reference(changed, record))

    def test_future_observation_is_not_accepted_by_current_mode(self):
        task, record = self.acquired()
        spec = self.spec(task, record)
        spec["period"] = "2099-01-01"
        with self.assertRaises(ContractError):
            validate_model_specs(task, [spec])

    def test_mode_and_availability_enter_their_dependency_fingerprints(self):
        from researchhelper.planning import input_fingerprint
        from researchhelper.evidence import evidence_binding
        task, record = self.acquired()
        current_hash = input_fingerprint(task)
        changed = copy.deepcopy(task)
        changed["research_mode"] = "historical"
        self.assertNotEqual(current_hash, input_fingerprint(changed))
        item = self.approved(task, record)
        before = evidence_binding(item)
        item["availability"]["obtained_at"] = now()
        self.assertNotEqual(before, evidence_binding(item))

    def test_contract_exposes_fixed_reference_only_for_real_current_acquisition(self):
        task, record = self.acquired()
        before = fingerprint(task)
        contract = evidence_contract(task)
        self.assertEqual(contract["sources"][0]["current_availability_reference"], self.spec(task, record)["availability"])
        self.assertEqual(fingerprint(task), before)
        task["research_mode"] = "historical"
        self.assertNotIn("current_availability_reference", evidence_contract(task)["sources"][0])

    def test_current_task_cannot_smuggle_a_historical_cutoff(self):
        with self.assertRaises(ContractError):
            self.agent.create("历史机会", research_mode="current", cutoff="2020-01-01")


if __name__ == "__main__":
    unittest.main()
