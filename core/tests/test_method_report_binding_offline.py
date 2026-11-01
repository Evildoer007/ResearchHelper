"""Report-boundary regressions using retained synthetic originals, never providers."""

import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.contracts import ContractError, calculation_dependency_hash, fingerprint
from researchhelper import knowledge
from researchhelper.reports import compose, render
from tests import test_method_dependency_workflow_offline as workflow

ROOT = Path(__file__).resolve().parents[2]
METHODS = ("observed_price_return_v1", "etf_nav_premium_v1", "gross_margin_v1")


class MethodReportBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workflow.MethodDependencyWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def task(self, method="gross_margin_v1"):
        task, host, _ = self.fixture.prepare(method)
        result = self.fixture.agent.start(task["id"], host)
        self.assertTrue(result.get("delivery"), result.get("error"))
        return result

    def test_all_methods_render_mathml_frozen_versions_and_closed_details(self):
        for method in METHODS:
            with self.subTest(method=method):
                task = self.task(method)
                content = compose(task)
                original = copy.deepcopy(content)
                with patch("researchhelper.reports.ResearchKnowledge.method_binding", side_effect=AssertionError("Renderer must not upgrade knowledge")):
                    page = render(content, ROOT)
                self.assertEqual(original, content)
                self.assertIn("<math ", page)
                self.assertIn("<details class='calculation-details'>", page)
                self.assertNotIn("<details class='calculation-details' open", page)
                self.assertIn(task["calculations"][0]["method_title"], page)
                self.assertIn("锁定版本1", page)
                self.assertIn("适用限制", page)
                self.assertIn(f"计算值{task['calculations'][0]['output']['value']:.4f}%", page)

    def test_method_binding_or_implementation_tampering_rejected_even_rehashed(self):
        task = self.task()
        for key, value in (("knowledge_version", 999), ("knowledge_hash", "forged"),
                           ("definition_hash", "forged"), ("implementation", METHODS[0])):
            altered = copy.deepcopy(task)
            item = altered["calculations"][0]
            item["method_binding"][key] = value
            item["dependency_hash"] = calculation_dependency_hash(item)
            with self.subTest(key=key), self.assertRaises(ContractError):
                compose(altered)
        for version in (999, True):
            altered = copy.deepcopy(task)
            altered["calculations"][0]["implementation_version"] = version
            with self.assertRaises(ContractError):
                compose(altered)

    def test_original_version_hash_and_context_changes_rejected(self):
        task = self.task()
        ref = task["calculations"][0]["input_refs"][0]
        for key, value in (("content_version", 99), ("snapshot_hash", "changed"), ("evidence_hash", "changed")):
            altered = copy.deepcopy(task)
            item = altered["calculations"][0]
            item["input_versions"][ref][key] = value
            item["dependency_hash"] = calculation_dependency_hash(item)
            with self.subTest(key=key), self.assertRaises(ContractError):
                compose(altered)
        altered = copy.deepcopy(task)
        altered["evidence"][0]["content_version"] += 1
        with self.assertRaises(ContractError):
            compose(altered)

    def test_parameter_context_mismatch_rejected_after_hash_recomputed(self):
        task = self.task()
        item = task["calculations"][0]
        item["parameters"]["accounting_scope"] = "parent"
        item["dependency_hash"] = calculation_dependency_hash(item)
        with self.assertRaises(ContractError):
            compose(task)

    def test_dependency_hash_limitations_and_result_tampering_rejected(self):
        task = self.task()
        for field, value in (("dependency_hash", "forged"), ("limitations", "没有限制"),
                             ("method_title", "收益预测"), ("method", METHODS[0])):
            altered = copy.deepcopy(task)
            altered["calculations"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                compose(altered)
        altered = copy.deepcopy(task)
        altered["calculations"][0]["output"]["value"] += 1
        with self.assertRaises(ContractError):
            compose(altered)

    def test_locked_knowledge_tampering_and_unbound_legacy_cannot_recompose(self):
        task = self.task()
        altered = copy.deepcopy(task)
        altered["knowledge"]["entries"][0]["content"] += "篡改"
        with self.assertRaises(ContractError):
            compose(altered)
        frozen = compose(task)
        for field in ("method_binding", "input_versions", "dependency_hash"):
            task["calculations"][0].pop(field)
            frozen["calculations"][0].pop(field)
        with self.assertRaises(ContractError):
            compose(task)
        frozen["content_hash"] = fingerprint({k: v for k, v in frozen.items() if k != "content_hash"})
        before = copy.deepcopy(frozen)
        self.assertIn("历史计算未记录方法知识绑定", render(frozen, ROOT))
        self.assertEqual(before, frozen)

    def test_withdrawn_method_cannot_publish_but_frozen_report_stays_readable(self):
        task = self.task()
        frozen = compose(task)
        entries = copy.deepcopy(knowledge.ENTRIES)
        target = task["calculations"][0]["method_binding"]["knowledge_id"]
        next(entry for entry in entries if entry["id"] == target)["known_error"] = True
        with patch.object(knowledge, "ENTRIES", entries):
            with self.assertRaises(ContractError):
                compose(task)
            self.assertIn("同口径营业毛利率", render(frozen, ROOT))

    def test_non_numeric_report_also_checks_locked_knowledge(self):
        task = self.fixture.agent.create_case("mechanism")
        task = self.fixture.agent.start(task["id"])
        self.assertFalse(task["calculations"])
        frozen = compose(task)
        altered = copy.deepcopy(task)
        altered["knowledge"]["entries"][0]["content"] += "未经发布的正文"
        with self.assertRaises(ContractError):
            compose(altered)
        withdrawn = copy.deepcopy(knowledge.ENTRIES)
        target = task["knowledge"]["entries"][0]["id"]
        next(entry for entry in withdrawn if entry["id"] == target)["known_error"] = "待复核"
        with patch.object(knowledge, "ENTRIES", withdrawn):
            with self.assertRaises(ContractError):
                compose(task)
            self.assertIn(task["theses"][0]["title"], render(frozen, ROOT))

    def test_retained_api_response_does_not_claim_full_provider_authority(self):
        task = self.task()
        task["synthetic"] = False
        task["source_records"] = [{"kind": "api", "status": "成功待核验"}]
        scope = compose(task)["scope"]
        self.assertIn("留存材料与接口响应", scope)
        self.assertIn("不代表供应商全部权限", scope)
        self.assertNotIn("未联网核验", scope)

    def test_existing_scenario_still_recomputes_and_unbound_override_rejected(self):
        task = self.fixture.agent.create_case("value")
        task = self.fixture.agent.start(task["id"])
        self.assertTrue(task.get("delivery"), task.get("error"))
        self.assertEqual(len(compose(task)["calculations"]), 2)
        item = task["calculations"][1]
        item["scenario_overrides"]["unregistered"] = {"reason": "不能绕过绑定"}
        item["dependency_hash"] = calculation_dependency_hash(item)
        with self.assertRaises(ContractError):
            compose(task)
