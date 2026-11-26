"""Company profitability and cash conversion, source bindings and equal interfaces."""

import copy
from decimal import Decimal
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, METHOD_REGISTRY, calculate, validate_calculation_context
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import validate_calculation_context_basis
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.reasoning import validate_plan
from researchhelper.reports import compose, render
from tests import test_financial_method_interfaces_offline as interfaces
from tests import test_method_dependency_workflow_offline as legacy_workflow
from tests import test_knowledge_market_interfaces_offline as catalog_fixture

ROOT = Path(__file__).resolve().parents[2]
METHODS = ("net_profit_margin_v1", "operating_cash_profit_ratio_v1")


def prepare_financial(agent, method, *, profit=12, cash=-8, label="净利润"):
    definition = METHOD_REGISTRY[method]
    parameters = dict(definition["parameters"], period="2026年上半年", accounting_scope="consolidated", accounting_standard="CAS")
    values = {"revenue": 100, "net_profit": profit, "operating_cash_flow": cash}
    metrics = {"revenue": "营业收入", "net_profit": label, "operating_cash_flow": "经营活动产生的现金流量净额"}
    question = "按同口径核算" + definition["title"] + "，不推断投资收益"
    task = agent.create(question, entry="个股", objects=["合成测试主体"], cutoff="2026-10-02T18:00:00+08:00",
        questions=[{"id": "q1", "question": question, "kind": "fact", "method_needs": [method]}])
    specifications, inputs = [], {}
    for field in definition["input_units"]:
        value = values[field]
        statement = "合并现金流量表" if field == "operating_cash_flow" else "合并利润表"
        text = f"合成测试主体，2026年上半年，人民币，企业会计准则，{statement}：{metrics[field]}{value}百万元。发布于2026-10-01。"
        updated = agent.add_material(task["id"], field + ".txt", text.encode())
        material = updated["materials"][-1]
        anchor = {"kind": "material", "artifact_id": material["id"], "snapshot_hash": material["snapshot_hash"], "part": "段落1", "quote": text}
        context = {"entity_id": "合成测试主体", "currency": "CNY", "period": parameters["period"],
            "accounting_scope": "consolidated", "accounting_standard": "CAS", "metric": field}
        specification = {"id": "e_" + field, "artifact_id": material["id"], "object": "合成测试主体",
            "record_locator": {"part": "段落1"}, "text": text, "period": parameters["period"], "nature": "已实现事实",
            "published_at": "2026-10-01", "publication": {"at": "2026-10-01", "basis": "原件明确发布日",
                "precision": "日", "timezone": "Asia/Shanghai", "certainty": "已确认", "basis_ref": anchor},
            "raw_value": value, "value": value, "raw_token": str(value), "raw_unit": "百万元", "unit": "百万元",
            "conversion_factor": 1, "question_ids": ["q1"], "calculation_context": context,
            "calculation_context_basis": {key: copy.deepcopy(anchor) for key in context},
            "review": {"question_ids": ["q1"], "allowed_natures": ["已实现事实"], "conditions": ["仅材料所载同主体期间与报表范围"], "reason": "逐项核对金融原值与口径，仅为合成验收"}}
        specifications.append(specification)
        inputs[field] = specification["id"]
    expected = profit / 100 * 100 if method == METHODS[0] else cash / profit * 100 if profit > 0 else None
    host = {"evidence_specs": specifications, "calculations": [{"id": "financial", "method": method, "inputs": inputs,
        "parameters": parameters, "question_ids": ["q1"]}], "answers": {"q1": {"answer": f"登记口径计算结果为{expected}%，仅描述已披露原值，不形成买卖结论。",
        "refs": list(inputs.values()), "calculation_refs": ["financial"]}}}
    return task, host, expected


class EarningsCashMethodsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="earnings-cash-", dir=ROOT / "result/acceptance")
        self.addCleanup(directory.cleanup)
        self.agent = ResearchAgent(ResearchArchive(directory.name), ROOT)

    def case(self, method, **options):
        return prepare_financial(self.agent, method, **options)

    def test_decimal_recomputation_and_no_annualization(self):
        for method, values, expected in ((METHODS[0], {"revenue": 300, "net_profit": 17}, Decimal(17) / Decimal(300) * 100),
                (METHODS[1], {"operating_cash_flow": -8, "net_profit": 12}, Decimal(-8) / Decimal(12) * 100)):
            parameters = self.case(method)[1]["calculations"][0]["parameters"]
            output = calculate(method, values, parameters)
            self.assertAlmostEqual(output["value"], float(expected), delta=output["tolerance"])
            self.assertEqual(output["unit"], "%")
            self.assertEqual(set(output), {"value", "unit", "tolerance"})

    def test_negative_margin_and_cash_are_preserved(self):
        self.assertEqual(calculate(METHODS[0], {"revenue": 100, "net_profit": -12}, self.case(METHODS[0])[1]["calculations"][0]["parameters"])["value"], -12)
        self.assertEqual(calculate(METHODS[1], {"operating_cash_flow": -24, "net_profit": 12}, self.case(METHODS[1])[1]["calculations"][0]["parameters"])["value"], -200)

    def test_zero_and_negative_profit_do_not_get_absolute_denominators(self):
        parameters = self.case(METHODS[1])[1]["calculations"][0]["parameters"]
        for profit in (0, -12):
            with self.subTest(profit=profit), self.assertRaises(ContractError):
                calculate(METHODS[1], {"operating_cash_flow": 24, "net_profit": profit}, parameters)

    def test_nonfinite_missing_extra_values_and_parameters_rejected(self):
        for method in METHODS:
            _, host, _ = self.case(method)
            parameters = host["calculations"][0]["parameters"]
            values = {field: 12 for field in METHOD_REGISTRY[method]["input_units"]}
            for field in values:
                for invalid in (True, None, "12", math.nan, math.inf):
                    with self.subTest(method=method, field=field, invalid=invalid), self.assertRaises(ContractError):
                        calculate(method, {**values, field: invalid}, parameters)
                missing = dict(values)
                missing.pop(field)
                with self.assertRaises(ContractError):
                    calculate(method, missing, parameters)
            with self.assertRaises(ContractError):
                calculate(method, {**values, "forecast": 1}, parameters)
            with self.assertRaises(ContractError):
                calculate(method, values, {**parameters, "annualize": True})

    def test_context_currency_entity_scope_period_and_standard_must_match(self):
        for method in METHODS:
            _, host, _ = self.case(method)
            parameters = host["calculations"][0]["parameters"]
            records = {field: next(item for item in host["evidence_specs"] if item["id"] == reference)
                for field, reference in host["calculations"][0]["inputs"].items()}
            validate_calculation_context(method, records, parameters)
            for field, value in (("entity_id", "另一主体"), ("currency", "HKD"), ("period", "2025年度"),
                    ("accounting_scope", "parent"), ("accounting_standard", "IFRS"), ("metric", "attributable_net_profit")):
                changed = copy.deepcopy(records)
                changed["net_profit"]["calculation_context"][field] = value
                with self.subTest(method=method, field=field), self.assertRaises(ContractError):
                    validate_calculation_context(method, changed, parameters)

    def test_attributable_adjusted_and_per_share_profit_are_not_relabelled(self):
        for label in ("归属于母公司股东的净利润", "归母净利润", "扣非净利润", "每股净利润"):
            task, host, _ = self.case(METHODS[0], label=label)
            final = self.agent.start(task["id"], host)
            self.assertNotEqual(final["status"], "已完成")
            self.assertFalse(final["calculations"])
            self.assertTrue(final["rejected_evidence"])

    def test_cash_inflow_total_cannot_become_operating_net_cash_flow(self):
        task, host, _ = self.case(METHODS[1])
        cash = host["evidence_specs"][0]
        cash["calculation_context_basis"]["metric"]["quote"] = "合并现金流量表"
        final = self.agent.start(task["id"], host)
        self.assertFalse(final["calculations"])
        self.assertNotEqual(final["status"], "已完成")

    def test_metric_anchor_cannot_borrow_another_numeric_row(self):
        task, host, _ = self.case(METHODS[0])
        item = copy.deepcopy(host["evidence_specs"][1])
        item.update(task_id=task["id"], research_version=task["version"])
        item["text"] = "归属于母公司股东的净利润12百万元。"
        with self.assertRaises(ContractError):
            validate_calculation_context_basis(item, self.agent.archive.get(task["id"])["materials"])

    def test_registered_plan_accepts_real_fields_but_not_model_code(self):
        for method in METHODS:
            task, host, _ = self.case(method)
            plan = {"questions": [{"id": "q1", "question": task["question"], "kind": "fact", "required": True, "method_needs": [method]}],
                "data_requests": [], "calculations": host["calculations"]}
            validate_plan(task, plan)
            plan["calculations"][0]["execute_code"] = "not permitted"
            with self.assertRaises(ContractError):
                validate_plan(task, plan)

    def test_same_originals_reach_frozen_report_with_mathml_and_dependencies(self):
        for method in METHODS:
            task, host, expected = self.case(method)
            final = self.agent.start(task["id"], host)
            self.assertEqual(final["status"], "已完成", {"error": final.get("error"), "gaps": final["gaps"], "rejected": final["rejected_evidence"]})
            calculation = final["calculations"][0]
            self.assertAlmostEqual(calculation["output"]["value"], expected)
            self.assertEqual(calculation["method_binding"]["knowledge_version"],
                final["knowledge"]["index"][METHOD_REGISTRY[method]["knowledge_id"]])
            self.assertTrue(calculation["dependency_hash"])
            self.assertEqual(final["ledger"]["used"]["data_requests"], 0)
            self.assertEqual(final["ledger"]["used"]["model_calls"], 0)
            page = self.agent.export(task["id"])["html"]
            self.assertIn("<math ", page)
            self.assertIn(METHOD_REGISTRY[method]["title"], page)
            self.assertIn("适用限制", page)
            self.assertEqual(self.agent.archive.get(task["id"], 1)["calculations"], final["calculations"])

    def test_changed_financial_context_or_output_cannot_recompose(self):
        task, host, _ = self.case(METHODS[1])
        final = self.agent.start(task["id"], host)
        for field, value in (("output", {"value": 200, "unit": "%"}), ("limitations", "直接买入")):
            changed = copy.deepcopy(final)
            changed["calculations"][0][field] = value
            with self.assertRaises(ContractError):
                compose(changed)

    def test_required_method_brings_cash_flow_mechanism_dependency(self):
        for method in METHODS:
            package = ResearchKnowledge().read("个股", "公司研究", methods=[method])
            self.assertIn(METHOD_REGISTRY[method]["knowledge_id"], package["index"])
            self.assertIn("rh_k_cash_profit", package["index"])
            self.assertTrue(ResearchKnowledge().method_binding(package, method))
        identifiers = [entry["id"] for entry in ResearchKnowledge().catalog()]
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_document_only_intent_cannot_get_new_financial_methods(self):
        for method in METHODS:
            with self.assertRaises(ContractError):
                self.agent.create("核对材料", intent="原件内容核对", questions=[{"question": "金额", "method_needs": [method]}])

    def test_zero_profit_keeps_verified_originals_and_partial_report(self):
        task, host, _ = self.case(METHODS[1], profit=0)
        final = self.agent.start(task["id"], host)
        self.assertEqual(final["status"], "部分完成")
        self.assertEqual(len(final["evidence"]), 2)
        self.assertFalse(final["calculations"])
        self.assertTrue(final["delivery"])
        self.assertTrue(any("零或负净利润" in gap for gap in final["gaps"]))

    def test_inapplicable_cash_ratio_does_not_drop_independent_margin_answer(self):
        task, host, _ = self.case(METHODS[0], profit=0)
        questions = [{"id": "q1", "question": task["question"], "kind": "fact", "method_needs": [METHODS[0]]},
            {"id": "q2", "question": "核对经营现金流净利润比", "kind": "fact", "method_needs": [METHODS[1]]}]
        self.agent.revise_plan(task["id"], questions, "增加独立现金流对照")
        _, cash_host, _ = self.case(METHODS[1], profit=0)
        cash_specification = copy.deepcopy(cash_host["evidence_specs"][0])
        updated = self.agent.add_material(task["id"], "cash.txt", cash_specification["text"].encode())
        retained = updated["materials"][-1]
        cash_specification.update(artifact_id=retained["id"], question_ids=["q2"])
        cash_specification["review"]["question_ids"] = ["q2"]
        for anchor in [cash_specification["publication"]["basis_ref"], *cash_specification["calculation_context_basis"].values()]:
            anchor.update(artifact_id=retained["id"], snapshot_hash=retained["snapshot_hash"])
        profit_specification = host["evidence_specs"][1]
        profit_specification["question_ids"] = profit_specification["review"]["question_ids"] = ["q1", "q2"]
        host["evidence_specs"].append(cash_specification)
        cash_calculation = copy.deepcopy(cash_host["calculations"][0])
        cash_calculation.update(id="cash_ratio", question_ids=["q2"])
        host["calculations"].append(cash_calculation)
        final = self.agent.start(task["id"], host)
        self.assertEqual(final["status"], "部分完成", final.get("error"))
        self.assertEqual(final["questions"][0]["status"], "已回答")
        self.assertEqual(final["questions"][1]["status"], "未回答")
        self.assertEqual([item["method"] for item in final["calculations"]], [METHODS[0]])
        self.assertEqual(len(final["evidence"]), 3)
        self.assertTrue(final["delivery"])


class EarningsCashInterfacesTests(unittest.TestCase):
    def test_app_and_independent_skill_deliver_identical_new_method_semantics(self):
        fixture = interfaces.FinancialMethodInterfacesTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with patch.object(legacy_workflow.MethodDependencyWorkflowTests, "prepare", lambda current, method: prepare_financial(current.agent, method)):
            for method in METHODS:
                fixture.run_method(method)

    def test_updated_catalog_preserves_original_boundary_assertions(self):
        fixture = catalog_fixture.KnowledgeMarketInterfacesTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        bootstrap = fixture.fixture.request("/api/bootstrap")[0]
        catalog = {entry["id"] for entry in bootstrap["knowledge"]}
        self.assertEqual(catalog, {entry["id"] for entry in ResearchKnowledge().catalog()})
        self.assertTrue({"rh_k_cash_profit", "rh_k_rate_transmission", "rh_k_index_structure", "rh_k_etf_exposure"}.issubset(catalog))
        self.assertEqual(bootstrap["field_registry"]["tinyshare.daily_basic"]["permission"], "未验收")
        task = fixture.app_task()
        self.assertEqual(task["status"], "部分完成", task.get("error"))
        self.assertIn("rh_k_cash_profit", task["knowledge"]["index"])
        self.assertNotIn("rh_k_rate_transmission", task["knowledge"]["index"])
        self.assertEqual(task["ledger"]["used"]["model_calls"], 0)
        self.assertFalse(task["evidence"] or task["theses"])


if __name__ == "__main__":
    unittest.main()
