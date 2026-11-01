"""Full research delivery from retained synthetic TXT sources, without fixtures bypass."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import METHOD_REGISTRY
from researchhelper.engine import ResearchAgent

ROOT = Path(__file__).resolve().parents[2]


class MethodDependencyWorkflowTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / "result/acceptance/method-dependency-workflow"
        directory.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(prefix="isolated-", dir=directory)
        self.addCleanup(folder.cleanup)
        self.archive = ResearchArchive(folder.name)
        self.agent = ResearchAgent(self.archive, ROOT, offline=True)
        network = patch("socket.socket.connect", side_effect=AssertionError("External networking forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def prepare(self, method):
        params = copy.deepcopy(METHOD_REGISTRY[method]["parameters"])
        if method == "observed_price_return_v1":
            params.update(start_date="2026-09-29", end_date="2026-09-30")
            common = dict(security_id="合成测试主体", currency="CNY", price_basis="unadjusted", price_kind="close",
                          window_start=params["start_date"], window_end=params["end_date"], corporate_action_status="unknown")
            rows = [("start_price", 10, "2026-09-29", dict(common, observation_date="2026-09-29"), "人民币未复权收盘价"),
                    ("end_price", 11, "2026-09-30", dict(common, observation_date="2026-09-30"), "人民币未复权收盘价")]
            question, expected = "计算已观测区间价格变化，不解释为总收益", 10
        elif method == "etf_nav_premium_v1":
            params.update(valuation_time="2026-09-30T15:00:00+08:00")
            common = dict(fund_id="合成测试主体", share_class="A类份额", currency="CNY", valuation_time=params["valuation_time"])
            rows = [("price", 10.5, "2026-09-30", dict(common, price_kind="close"), "人民币收盘价"),
                    ("nav", 10, "2026-09-30", dict(common, nav_kind="official"), "人民币官方单位净值")]
            question, expected = "计算同估值时点ETF净值折溢价，不推断可实现套利", 5
        elif method == "gross_margin_v1":
            params.update(period="2026年上半年", accounting_scope="consolidated", accounting_standard="CAS")
            common = dict(entity_id="合成测试主体", currency="CNY", period=params["period"], accounting_scope="consolidated", accounting_standard="CAS")
            rows = [("revenue", 100, params["period"], dict(common, metric="revenue"), "人民币合并利润表企业会计准则营业收入"),
                    ("cost", 60, params["period"], dict(common, metric="operating_cost"), "人民币合并利润表企业会计准则营业成本")]
            question, expected = "计算同口径营业毛利率", 40
        else:
            rows = [("fee_a", .5, "2025年度", None, "年度费用"), ("fee_b", .3, "2026年度", None, "年度费用")]
            question, expected = "核对跨期费用是否允许比较", None
        task = self.agent.create(question, objects=["合成测试主体"], cutoff="2026-10-02T18:00:00+08:00",
                                 questions=[{"id": "q1", "question": question, "kind": "fact", "method_needs": [method]}])
        specs, inputs = [], {}
        for field, value, period, context, financial_text in rows:
            unit = METHOD_REGISTRY[method]["input_units"][field]
            literals = " ".join(str(v) for k, v in (context or {}).items() if k in
                {"security_id", "fund_id", "entity_id", "share_class", "period", "observation_date", "valuation_time", "window_start", "window_end"})
            text = f"合成验收材料，非真实金融数据。合成测试主体 {period} {literals}。{financial_text} {value}{unit}。发布于2026-10-01。"
            current = self.agent.add_material(task["id"], field + ".txt", text.encode())
            artifact = current["materials"][-1]
            anchor = {"kind": "material", "artifact_id": artifact["id"], "snapshot_hash": artifact["snapshot_hash"], "part": "段落1", "quote": text}
            spec = {"id": "e_" + field, "artifact_id": artifact["id"], "object": "合成测试主体",
                    "record_locator": {"part": "段落1"}, "text": text, "period": period, "nature": "已实现事实",
                    "published_at": "2026-10-01", "publication": {"at": "2026-10-01", "basis": "原件明确发布日期",
                        "precision": "日", "timezone": "Asia/Shanghai", "certainty": "已确认", "basis_ref": anchor},
                    "raw_value": value, "value": value, "raw_token": str(value), "raw_unit": unit, "unit": unit,
                    "conversion_factor": 1, "question_ids": ["q1"],
                    "review": {"question_ids": ["q1"], "allowed_natures": ["已实现事实"],
                        "conditions": ["仅合成测试主体、材料所载期间和明确金融口径"], "reason": "原件值与口径逐项核对，非真实市场研究"}}
            if context is not None:
                spec["calculation_context"] = context
                spec["calculation_context_basis"] = {key: {"kind": "unverified"} if key == "corporate_action_status" else copy.deepcopy(anchor) for key in context}
            specs.append(spec)
            inputs[field] = spec["id"]
        host = {"evidence_specs": specs, "calculations": [{"id": "calc_main", "method": method, "inputs": inputs,
                "parameters": params, "question_ids": ["q1"]}], "answers": {"q1": {"answer": f"合成原件计算结果为{expected}，仅在登记口径内解释。",
                "refs": list(inputs.values()), "calculation_refs": ["calc_main"]}}}
        return task, host, expected

    def assert_saved(self, result):
        self.assertIsNotNone(result.get("delivery"), result.get("error"))
        saved = self.archive.report(result["id"], result["version"])
        self.assertTrue(saved["digest"])
        self.assertIn("合成测试主体", saved["html"])
        self.assertEqual(saved["content"]["calculations"], result["calculations"])
        self.assertEqual(saved["content"]["evidence"], result["evidence"])
        restored = self.archive.get(result["id"], result["version"])
        self.assertEqual(restored["calculations"], result["calculations"])
        self.assertEqual(result["ledger"]["used"]["data_requests"], 0)
        self.assertEqual(result["ledger"]["used"]["model_calls"], 0)

    def test_three_methods_from_retained_sources_to_saved_reports(self):
        for method in ("observed_price_return_v1", "etf_nav_premium_v1", "gross_margin_v1"):
            with self.subTest(method=method):
                task, host, expected = self.prepare(method)
                result = self.agent.start(task["id"], host)
                self.assertEqual(result["status"], "已完成", {"error": result.get("error"), "gaps": result["gaps"], "rejected": result["rejected_evidence"]})
                self.assertEqual(len(result["evidence"]), 2)
                self.assertEqual(len(result["calculations"]), 1)
                calculation = result["calculations"][0]
                self.assertAlmostEqual(calculation["output"]["value"], expected)
                self.assertTrue(calculation["method_binding"])
                self.assertTrue(calculation["dependency_hash"])
                self.assertEqual(result["questions"][0]["status"], "已回答")
                self.assert_saved(result)

    def test_fee_different_periods_reject_calculation(self):
        task, host, _ = self.prepare("fee_difference_v1")
        result = self.agent.start(task["id"], host)
        self.assertEqual(len(result["evidence"]), 2)
        self.assertEqual(result["calculations"], [])
        self.assertNotEqual(result["questions"][0]["status"], "已回答")
        self.assertTrue(any("共同适用期间" in gap for gap in result["gaps"]))
        self.assert_saved(result)

    def test_unknown_context_missing_basis_and_tampered_sources_do_not_compute(self):
        for mutation in ("unknown", "missing_basis", "hash", "context", "source"):
            with self.subTest(mutation=mutation):
                task, host, _ = self.prepare("gross_margin_v1")
                evidence = host["evidence_specs"][0]
                if mutation == "unknown":
                    evidence["calculation_context"]["accounting_scope"] = "unknown"
                elif mutation == "missing_basis":
                    evidence.pop("calculation_context_basis")
                elif mutation == "hash":
                    evidence["calculation_context_basis"]["currency"]["snapshot_hash"] = "forged"
                elif mutation == "context":
                    evidence["calculation_context"]["currency"] = "USD"
                else:
                    altered = self.archive.get(task["id"])
                    altered["materials"][0]["snapshot"]["parts"][0]["text"] += "伪造追加原文"
                    self.archive.save(altered)
                result = self.agent.start(task["id"], host)
                self.assertTrue(result["rejected_evidence"], result.get("error"))
                self.assertEqual(result["calculations"], [])
                self.assertNotEqual(result["questions"][0]["status"], "已回答")
                self.assert_saved(result)


if __name__ == "__main__":
    unittest.main()
