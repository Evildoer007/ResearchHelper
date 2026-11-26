"""Calculation context must be anchored in retained sources; no external calls."""
import copy
import unittest

from researchhelper.contracts import ContractError
from researchhelper.evidence import (candidate_evidence, evidence_binding, extract_material,
    material_record, snapshot_record, validate_calculation_context_basis, validate_provenance, direct_code_reference)


class CalculationContextSourcesTests(unittest.TestCase):
    def setUp(self):
        self.task = {"id": "context-test", "version": 1, "input_hash": "synthetic-test", "source_records": [], "materials": []}
        text = "发布日期：2026-09-30。示例公司 A类份额 2026-09-30 人民币 未复权 收盘价 官方单位净值 合并利润表 企业会计准则 营业收入 营业成本。\n\n无公司行为。\n\nIOPV参考净值。\n\n营业总成本。"
        self.material = material_record(self.task, extract_material("context.txt", text.encode()), text.encode())
        self.task["materials"].append(self.material)
        self.context = {"entity_id": "示例公司", "share_class": "A类份额", "period": "2026-09-30",
                        "currency": "CNY", "price_basis": "unadjusted", "price_kind": "close", "nav_kind": "official",
                        "accounting_scope": "consolidated", "accounting_standard": "CAS", "metric": "revenue"}

    def anchor(self, quote=None, part="段落1"):
        return {"kind": "material", "artifact_id": self.material["id"], "snapshot_hash": self.material["snapshot_hash"],
                "part": part, "quote": quote or self.material["parts"][0]["text"]}

    def item(self):
        return {"task_id": self.task["id"], "research_version": 1, "calculation_context": copy.deepcopy(self.context),
                "calculation_context_basis": {key: self.anchor() for key in self.context}}

    def check(self, item):
        validate_calculation_context_basis(item, self.task["materials"] + self.task["source_records"])

    def test_material_context_and_explicit_unknown(self):
        item = self.item()
        self.check(item)
        item["calculation_context"]["corporate_action_status"] = "unknown"
        item["calculation_context_basis"]["corporate_action_status"] = {"kind": "unverified"}
        self.check(item)
        item["calculation_context"]["corporate_action_status"] = "verified_none"
        item["calculation_context_basis"]["corporate_action_status"] = self.anchor("无公司行为。", "段落2")
        self.check(item)

    def test_missing_forged_cross_task_and_changed_context_rejected(self):
        mutations = [lambda x: x.pop("calculation_context_basis"),
                     lambda x: x["calculation_context_basis"].pop("currency"),
                     lambda x: x["calculation_context_basis"]["currency"].update(snapshot_hash="forged"),
                     lambda x: x.update(task_id="other"), lambda x: x.update(research_version=2),
                     lambda x: x["calculation_context"].update(currency="USD"),
                     lambda x: x["calculation_context"].update(entity_id="另一公司"),
                     lambda x: x["calculation_context_basis"]["currency"].update(quote="伪造人民币"),
                     lambda x: x["calculation_context_basis"]["currency"].update(part="段落999"),
                     lambda x: x["calculation_context"].update(currency=True)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                item = self.item()
                mutate(item)
                with self.assertRaises(ContractError):
                    self.check(item)

    def test_nav_cost_and_boolean_company_action_cannot_be_relabelled(self):
        for field, value, quote, part in (("nav_kind", "official", "IOPV参考净值。", "段落3"),
                ("metric", "operating_cost", "营业总成本。", "段落4"),
                ("corporate_action_status", "present", "无公司行为。", "段落2")):
            item = self.item()
            item["calculation_context"] = {field: value}
            item["calculation_context_basis"] = {field: self.anchor(quote, part)}
            with self.assertRaises(ContractError):
                self.check(item)
        item["calculation_context"] = {"corporate_action_status": True}
        item["calculation_context_basis"] = {"corporate_action_status": {"kind": "unverified"}}
        with self.assertRaises(ContractError):
            self.check(item)

    def test_api_derivation_uses_exact_cell_identity_and_request(self):
        from tests.test_identity_period_provenance import IdentityPeriodFixture, CODE, SESSION
        fixture = IdentityPeriodFixture()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.task = fixture.task
        record = fixture.artifact
        spec = copy.deepcopy(fixture.spec)
        del spec["identity"]
        spec["direct_code_reference"] = direct_code_reference(self.task, record)
        context = dict(security_id=CODE, currency="CNY", price_basis="unadjusted", price_kind="close",
            observation_date=SESSION, window_start=SESSION, window_end=SESSION)
        spec.update(calculation_context=context,
            calculation_context_basis={key: dict(kind="api", artifact_id=record["id"],
                snapshot_hash=record["hash"], field=key) for key in context})
        item = candidate_evidence(self.task, record, spec)
        self.check(item)
        before = evidence_binding(item)
        item["calculation_context"]["window_end"] = "2026-10-01"
        self.assertNotEqual(before, evidence_binding(item))
        with self.assertRaises(ContractError):
            validate_provenance(item, self.task)
        spec["calculation_context_basis"]["currency"]["field"] = "price_basis"
        with self.assertRaises(ContractError):
            candidate_evidence(self.task, record, spec)

    def test_absent_context_retains_old_contract_and_anchors_are_bound(self):
        validate_calculation_context_basis({}, ())
        item = self.item()
        before = evidence_binding(item)
        item["calculation_context_basis"]["currency"]["part"] = "段落2"
        self.assertNotEqual(before, evidence_binding(item))

    def test_material_candidate_entry_requires_retained_anchors(self):
        spec = {"id": "material_context", "object": "示例公司", "record_locator": {"part": "段落1"},
                "text": self.material["parts"][0]["text"], "published_at": "2026-09-30", "period": "2026-09-30",
                "publication": dict(at="2026-09-30", precision="日", timezone="Asia/Shanghai", certainty="已确认",
                    basis="合成原件明确公布日", basis_ref=self.anchor("发布日期：2026-09-30。")),
                "nature": "已实现事实", "unit": "不适用", "calculation_context": self.context,
                "calculation_context_basis": self.item()["calculation_context_basis"]}
        result = candidate_evidence(self.task, self.material, spec)
        self.assertEqual(result["source_check"], "通过")
        # No applicability/publication approval is granted by context extraction.
        self.assertEqual(result["applicability_check"], "待核验")
        self.task["materials"] = []
        with self.assertRaises(ContractError):
            candidate_evidence(self.task, self.material, spec)


if __name__ == "__main__":
    unittest.main()
