"""Independent document-only acceptance. Local originals and fabricated models.

No vendor, production archive, foreground application or real model is used.
"""

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import (ContractError, DOCUMENT_CONTENT_INTENT, DOCUMENT_CONTENT_LIMITATION,
    document_content_answer, fingerprint, validate_calculation_context, validate_evidence)
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence, evidence_binding, validate_publication_basis
from researchhelper.model import ModelService, ModelSettings
from researchhelper.reasoning import MODEL_EVIDENCE_FIELDS, evidence_contract, validate_model_specs, validate_plan
from researchhelper.reports import compose
from researchhelper.source_reader import binding, read, validate_read_specs
from tests.test_autonomous_reasoning_offline import FabricatedModel, AutonomousReasoningTests

ROOT = Path(__file__).resolve().parents[2]
TEXT = "材料示例政策\n\n政策自2027年1月1日起生效。\n\n费用上限为100百万元。"
CUTOFF = "2026-07-20T18:00:00+08:00"


class DocumentContentTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="document-content-", dir=ROOT / "result/acceptance")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.agent = ResearchAgent(ResearchArchive(self.root), ROOT, offline=True)

    def material_task(self, intent=DOCUMENT_CONTENT_INTENT, text=TEXT):
        task = self.agent.create("文件写了什么？", intent=intent, objects=["材料示例政策"], cutoff=CUTOFF)
        return self.agent.add_material(task["id"], "policy.txt", text.encode())

    def spec(self, task, **changes):
        record = task["materials"][0]
        value = {"id": "clause", "artifact_id": record["id"], "object": "材料示例政策",
            "record_locator": {"part": "段落2"}, "text": "政策自2027年1月1日起生效。",
            "period": "原件条款", "nature": "合同政策条款", "unit": "不适用", "question_ids": ["q1"],
            "usage_scope": "document_content", "published_at": None,
            "publication": {"at": None, "basis": "首次公开时间未核验", "certainty": "未知", "precision": "未知", "timezone": "未知", "basis_ref": None},
            "review": {"question_ids": ["q1"], "allowed_natures": ["合同政策条款"],
                       "conditions": ["仅核对该原件所载条款"], "reason": "摘录直接对应文件内容问题"}}
        value.update(changes)
        return value

    def run_spec(self, task, spec):
        return self.agent.start(task["id"], {"evidence_specs": [spec]})

    def test_unknown_publication_answers_exact_original_and_saves_scoped_report(self):
        task = self.material_task()
        final = self.run_spec(task, self.spec(task))
        self.assertEqual(final["status"], "已完成", final.get("error"))
        self.assertEqual(final["questions"][0]["kind"], "fact")
        self.assertEqual(final["questions"][0]["answer"], document_content_answer(final["evidence"]))
        self.assertEqual(final["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(final["ledger"]["used"]["data_requests"], 0)
        self.assertEqual((final["theses"], final["calculations"], final["tracking"]), ([], [], []))
        report = self.agent.export(task["id"])
        self.assertIn(DOCUMENT_CONTENT_LIMITATION, report["html"])
        from lxml import html as dom
        self.assertIn("原件摘录", dom.fromstring(report["html"]).xpath("//main//h2/text()"))
        self.assertIn("未核验", report["html"])
        self.assertIsNone(final["evidence"][0]["published_at"])
        validate_evidence(final["evidence"][0], final)

    def test_same_unknown_excerpt_is_rejected_in_normal_research(self):
        task = self.material_task("综合研究")
        final = self.run_spec(task, self.spec(task))
        self.assertEqual(final["evidence"], [])
        self.assertNotEqual(final["status"], "已完成")
        self.assertIn("普通研究", str(final["rejected_evidence"]))

    def test_scope_is_mandatory_and_cannot_be_removed_from_verified_record(self):
        task = self.material_task()
        spec = self.spec(task)
        del spec["usage_scope"]
        with self.assertRaises(ContractError):
            candidate_evidence(task, task["materials"][0], spec)
        final = self.run_spec(task, self.spec(task))
        edited = copy.deepcopy(final["evidence"][0])
        del edited["usage_scope"]
        self.assertNotEqual(evidence_binding(edited), evidence_binding(final["evidence"][0]))
        with self.assertRaises(ContractError):
            validate_evidence(edited, final)

    def test_receipt_cannot_be_reused_in_another_task_or_reclassified_scope(self):
        task = self.material_task()
        final = self.run_spec(task, self.spec(task))
        other = self.material_task()
        with self.assertRaises(ContractError):
            validate_evidence(final["evidence"][0], other)
        changed = dict(final, intent="综合研究")
        with self.assertRaises(ContractError):
            validate_evidence(final["evidence"][0], changed)

    def test_current_goal_cannot_be_changed_to_investment_or_calculation(self):
        for kind in ("comparison", "mechanism", "investment"):
            with self.subTest(kind=kind), self.assertRaises(ContractError):
                self.agent.create("问题", intent=DOCUMENT_CONTENT_INTENT, questions=[{"question": "问题", "kind": kind}])
        with self.assertRaises(ContractError):
            self.agent.create("问题", intent=DOCUMENT_CONTENT_INTENT, questions=[{"question": "问题", "method_needs": ["fee_difference_v1"]}])

    def test_host_tools_and_theses_are_rejected_before_any_dispatch(self):
        for field in ("theses", "calculations", "data_requests"):
            task = self.material_task()
            with self.subTest(field=field), self.assertRaises(ContractError):
                self.agent.start(task["id"], {field: [{"untrusted": True}]})
            stored = self.agent.archive.get(task["id"])
            self.assertEqual(stored["status"], "待开始")
            self.assertEqual(stored["ledger"]["dispatches"], [])

    def test_document_evidence_is_never_a_financial_calculation_input(self):
        item = {"usage_scope": "document_content", "unit": "%/年", "period": "2026"}
        with self.assertRaises(ContractError):
            validate_calculation_context("fee_difference_v1", {"fee_a": item, "fee_b": item}, {"unit": "%/年"})

    def test_original_numbers_still_require_literal_units_and_values(self):
        task = self.material_task()
        numeric = self.spec(task, id="amount", record_locator={"part": "段落3"}, text="费用上限为100百万元。",
            raw_token="100", raw_value=100, value=100, unit="百万元", raw_unit="百万元", conversion_factor=1)
        final = self.run_spec(task, numeric)
        self.assertEqual(final["evidence"][0]["value"], 100)
        for changes in ({"value": 101}, {"raw_unit": "万元"}, {"raw_token": "101"}):
            another = self.material_task()
            bad = dict(numeric, artifact_id=another["materials"][0]["id"], **changes)
            result = self.run_spec(another, bad)
            self.assertEqual(result["evidence"], [], changes)

    def test_wrong_page_or_paraphrased_text_is_rejected(self):
        for changes in ({"record_locator": {"part": "第99页"}}, {"text": "这个政策已经生效且利好企业"}):
            task = self.material_task()
            result = self.run_spec(task, self.spec(task, **changes))
            self.assertEqual(result["evidence"], [])

    def test_original_integrity_remains_required_at_export(self):
        task = self.material_task()
        final = self.run_spec(task, self.spec(task))
        changed = copy.deepcopy(final)
        changed["evidence"][0]["artifact"]["snapshot"]["original_base64"] = "dGFtcGVy"
        with self.assertRaises(ContractError):
            compose(changed)

    def test_scope_does_not_confirm_time_even_when_file_has_future_dates(self):
        task = self.material_task()
        spec = self.spec(task, published_at="2027-01-01")
        with self.assertRaises(ContractError):
            validate_model_specs(task, [spec])
        spec = self.spec(task)
        spec["publication"]["certainty"] = "已确认"
        with self.assertRaises(ContractError):
            candidate_evidence(task, task["materials"][0], spec)

    def test_original_filing_with_unknown_directory_date_can_be_read(self):
        task = self.agent.create("文件写了什么？", intent=DOCUMENT_CONTENT_INTENT, objects=["材料示例政策"], cutoff=CUTOFF)
        directory = {"issuer": "材料示例政策", "source": "独立验收文件", "source_id": "fixture-001", "title": "政策原件",
            "published_at": None, "original_url": "https://example.invalid/policy.txt",
            "query_coverage": {"code": "600000.SH", "start": "2026-07-01", "end": "2026-07-20"}}
        task = self.agent.add_filing(task["id"], directory, "policy.txt", TEXT.encode())
        proxy = dict(task, materials=[task["source_records"][0]])
        final = self.run_spec(task, self.spec(proxy))
        self.assertEqual(final["status"], "已完成", final.get("error"))

    def test_api_directory_and_market_response_cannot_be_doc_evidence(self):
        task = self.material_task()
        artifact = dict(task["materials"][0], kind="api")
        with self.assertRaises(ContractError):
            candidate_evidence(task, artifact, self.spec(task))

    def test_read_bundle_accepts_unknown_time_but_rejects_unread_excerpt(self):
        task = self.material_task()
        record = task["materials"][0]
        bundle = read(task, [{"artifact_id": record["id"], "snapshot_hash": record["snapshot_hash"],
            "queries": ["生效"], "parts": [], "question_ids": ["q1"], "reason": "核对条款"}], binding(task))
        validate_read_specs(task, [self.spec(task)], bundle)
        with self.assertRaises(ContractError):
            validate_read_specs(task, [self.spec(task, text="未读材料推断")], bundle)

    def test_generated_answer_cannot_be_augmented_by_host_or_after_verification(self):
        task = self.material_task()
        result = self.agent.start(task["id"], {"evidence_specs": [self.spec(task)],
            "answers": {"q1": {"answer": "建议买入相关企业", "refs": ["clause"]}}})
        self.assertEqual(result["status"], "失败")
        self.assertIsNone(result["delivery"])
        task = self.material_task()
        final = self.run_spec(task, self.spec(task))
        final["questions"][0]["answer"] += "因此可以买入"
        with self.assertRaises(ContractError):
            compose(final)

    def test_document_report_rejects_financial_output_injected_after_run(self):
        task = self.material_task()
        final = self.run_spec(task, self.spec(task))
        for field in ("calculations", "theses", "tracking"):
            forged = copy.deepcopy(final)
            forged[field] = [{"judgment": "支持"}]
            with self.subTest(field=field), self.assertRaises(ContractError):
                compose(forged)

    def test_model_only_plans_and_extracts_with_same_contract_and_no_analysis_call(self):
        self.agent.offline = False
        settings = ModelSettings(self.root)
        settings.save({"endpoint": "https://example.invalid/model", "model": "fabricated", "api_key": "fabricated-only"})
        self.agent.model = ModelService(settings, offline=False, journal=self.agent._journal)
        task = self.material_task()
        model = FabricatedModel([AutonomousReasoningTests.plan(task), {"evidence_specs": [self.spec(task)]}])
        with patch("researchhelper.model.urllib.request.urlopen", side_effect=model.respond):
            final = self.agent.start(task["id"])
        self.assertEqual(final["status"], "已完成", final.get("error"))
        self.assertEqual(final["ledger"]["used"]["model_calls"], 2)
        self.assertEqual(len(model.requests), 2)
        payload = json.loads(model.requests[1]["messages"][1]["content"])
        self.assertEqual(payload["evidence_contract"]["usage_scope"], "document_content")

    def test_model_plan_cannot_dispatch_tools_in_doc_mode(self):
        task = self.material_task()
        with self.assertRaises(ContractError):
            validate_plan(task, AutonomousReasoningTests.plan(task, calculations=[{"id": "extra"}]))

    def test_shared_evidence_contract_is_read_only_and_task_bound(self):
        task = self.material_task()
        before = fingerprint(task)
        contract = evidence_contract(task)
        self.assertEqual(before, fingerprint(task))
        self.assertEqual(contract["allowed_fields"], sorted(MODEL_EVIDENCE_FIELDS - {"candidate_code_reference"}))
        self.assertEqual(contract["sources"][0]["snapshot_hash"], task["materials"][0]["snapshot_hash"])
        self.assertFalse(contract["template_is_approval"])
        self.assertEqual(contract["binding"]["input_hash"], task["input_hash"])
        self.assertNotIn("original_base64", json.dumps(contract))

    def test_skill_and_app_expose_identical_bound_evidence_contract(self):
        task = self.material_task()
        spec = importlib.util.spec_from_file_location("document_skill_source", ROOT / "core/session.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        session = module.Session.__new__(module.Session)
        session.archive = self.agent.archive
        self.assertEqual(session.tools(task["id"])["evidence"], evidence_contract(task))

    def test_unknown_ordinary_publication_and_other_original_date_still_fail(self):
        task = self.material_task("综合研究")
        spec = self.spec(task)
        del spec["usage_scope"]
        with self.assertRaises(ContractError):
            validate_model_specs(task, [spec])
        task = self.agent.add_material(task["id"], "other.txt", "发布日期：2026年7月19日。".encode())
        spec.update(published_at="2026-07-19", publication={"at": "2026-07-19", "certainty": "已确认", "precision": "日", "timezone": "Asia/Shanghai",
            "basis": "另一文件", "basis_ref": {"kind": "material", "artifact_id": task["materials"][1]["id"],
                "snapshot_hash": task["materials"][1]["snapshot_hash"], "part": "段落1", "quote": "发布日期：2026年7月19日。"}})
        with self.assertRaises(ContractError):
            validate_publication_basis(spec, task["materials"])

    def test_same_original_publication_anchor_still_passes_normal_research(self):
        task = self.agent.create("这份政策什么时候生效？", objects=["材料示例政策"], cutoff=CUTOFF,
            questions=[{"id": "q1", "question": "这份政策什么时候生效？", "kind": "fact"}])
        task = self.agent.add_material(task["id"], "policy.txt", ("材料示例政策\n\n发布日期：2026年7月19日。\n\n政策自2026年7月21日起生效。").encode())
        spec = AutonomousReasoningTests.policy_spec(task)
        validate_model_specs(task, [spec])
        final = self.agent.start(task["id"], {"evidence_specs": [spec], **AutonomousReasoningTests.answer()})
        self.assertEqual(final["status"], "已完成", final.get("error"))

    def test_restore_and_update_preserve_scope_without_rewriting_old_report(self):
        task = self.material_task()
        final = self.run_spec(task, self.spec(task))
        old = self.agent.export(task["id"])["html"]
        restored = ResearchAgent(ResearchArchive(self.root), ROOT, offline=True)
        self.assertEqual(restored.export(task["id"])["html"], old)
        updated = restored.update(task["id"], cutoff="2026-07-21T18:00:00+08:00", host_result={"evidence_specs": [self.spec(final)]})
        self.assertEqual(updated["intent"], DOCUMENT_CONTENT_INTENT)
        self.assertEqual(restored.archive.report(final["id"], final["version"])["html"], old)


if __name__ == "__main__":
    unittest.main()
