"""Point-level research uses registered index identity, not a stock-price alias."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, METHOD_REGISTRY, calculate, validate_calculation_parameters
from researchhelper.engine import ResearchAgent
from researchhelper.numeric_integrity import _quantities

ROOT = Path(__file__).resolve().parents[2]
METHOD = "observed_index_change_v1"


class IndexChangeWorkflowTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / "result/acceptance/index-change"
        root.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(prefix="isolated-", dir=root)
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        guard = patch("socket.socket.connect", side_effect=AssertionError("No external calls"))
        guard.start(); self.addCleanup(guard.stop)

    def prepare(self, variant="price", end=1050, *, currency="人民币", future=False, predicted=False):
        start_date, end_date = "2026-09-29", "2026-10-03" if future else "2026-09-30"
        parameters = dict(METHOD_REGISTRY[METHOD]["parameters"], start_date=start_date, end_date=end_date,
                          index_variant=variant, index_currency=currency)
        question = "核对合成指数已观测区间变化，不解释为基金实际收益"
        task = self.agent.create(question, entry="指数", objects=["合成指数"], cutoff="2026-10-02T18:00:00+08:00",
            questions=[{"id": "q1", "question": question, "kind": "fact", "method_needs": [METHOD]}])
        specs = []
        label = {"price": "价格指数", "gross_total_return": "全收益指数", "net_total_return": "净全收益指数"}[variant]
        for field, value, observation in (("start_level", 1000, start_date), ("end_level", end, end_date)):
            classification = "第三方估计，未经官方确认。" if predicted else ""
            text = (f"合成验收材料，非真实市场数据。{classification}合成指数 {label}，计价币种{currency}，基期2004-12-31，基点100。"
                    f"区间{start_date}至{end_date}，观测日{observation}，收盘点位{value}点。发布于2026-10-01。")
            added = self.agent.add_material(task["id"], field + ".txt", text.encode())
            record = added["materials"][-1]
            anchor = {"kind": "material", "artifact_id": record["id"], "snapshot_hash": record["snapshot_hash"],
                      "part": "段落1", "quote": text}
            context = {"index_id": "合成指数", "index_variant": variant, "index_currency": currency,
                       "index_base": "基期2004-12-31，基点100", "level_kind": "close", "observation_date": observation,
                       "window_start": start_date, "window_end": end_date}
            nature = "第三方估计" if predicted else "已实现事实"
            specs.append({"id": field, "artifact_id": record["id"], "object": "合成指数", "record_locator": {"part": "段落1"},
                "text": text, "period": observation, "nature": nature, "unit": "点", "raw_unit": "点",
                "raw_token": str(value), "raw_value": value, "value": value, "conversion_factor": 1,
                "published_at": "2026-10-01", "publication": {"at": "2026-10-01", "basis": "合成原件明确发布日期",
                    "precision": "日", "timezone": "Asia/Shanghai", "certainty": "已确认", "basis_ref": copy.deepcopy(anchor)},
                "question_ids": ["q1"], "review": {"question_ids": ["q1"], "allowed_natures": [nature],
                    "conditions": ["仅合成测试原件，核对同指数版本、币种、基期及收盘点位"], "reason": "原值及口径逐项定位，非真实研究质量证明"},
                "calculation_context": context, "calculation_context_basis": {key: copy.deepcopy(anchor) for key in context}})
        host = {"evidence_specs": specs, "calculations": [{"id": "index_change", "method": METHOD,
            "inputs": {"start_level": "start_level", "end_level": "end_level"}, "parameters": parameters, "question_ids": ["q1"]}],
            "answers": {"q1": {"answer": f"该版本已观测区间变化为{(end / 1000 - 1) * 100:.2f}%，不代表基金实际收益。",
                                "refs": ["start_level", "end_level"], "calculation_refs": ["index_change"]}}}
        return task, host

    def run_host(self, task, host):
        return self.agent.start(task["id"], host)

    def test_three_variants_save_and_restore_same_version_reports(self):
        for variant in ("price", "gross_total_return", "net_total_return"):
            with self.subTest(variant=variant):
                task, host = self.prepare(variant)
                result = self.run_host(task, host)
                self.assertEqual(result["status"], "已完成", {"error": result.get("error"), "gaps": result["gaps"], "rejected": result["rejected_evidence"]})
                self.assertAlmostEqual(result["calculations"][0]["output"]["value"], 5)
                self.assertEqual(result["calculations"][0]["inputs"], {"start_level": 1000, "end_level": 1050})
                self.assertIn("rh_k_index_change", result["knowledge"]["index"])
                saved = self.agent.archive.report(task["id"], 1)
                self.assertEqual(saved["content"]["calculations"], result["calculations"])
                self.assertIn("<math", saved["html"])
                self.assertIn("L₀", saved["html"])
                self.assertEqual(self.agent.archive.get(task["id"], 1)["calculations"], result["calculations"])
                self.assertEqual(result["ledger"]["used"]["model_calls"], 0)
                self.assertEqual(result["ledger"]["used"]["data_requests"], 0)

    def test_negative_and_zero_end_levels_keep_signed_output(self):
        for end, expected in ((950, -5), (0, -100)):
            with self.subTest(end=end):
                task, host = self.prepare(end=end)
                result = self.run_host(task, host)
                self.assertEqual(result["status"], "已完成", result.get("error"))
                self.assertAlmostEqual(result["calculations"][0]["output"]["value"], expected)

    def test_currency_is_explicit_not_defaulted_to_rmb(self):
        task, host = self.prepare(currency="美元")
        result = self.run_host(task, host)
        self.assertEqual(result["status"], "已完成", result.get("error"))
        self.assertEqual(result["calculations"][0]["parameters"]["index_currency"], "美元")

    def assert_not_computed(self, result):
        self.assertFalse(result["calculations"])
        self.assertNotEqual(result["questions"][0]["status"], "已回答")

    def test_different_identity_base_currency_or_variant_do_not_compute(self):
        for key, value in (("index_id", "另一个指数"), ("index_base", "另一个基期"), ("index_currency", "美元"),
                           ("index_variant", "gross_total_return")):
            with self.subTest(key=key):
                task, host = self.prepare()
                host["evidence_specs"][1]["calculation_context"][key] = value
                self.assert_not_computed(self.run_host(task, host))

    def test_missing_context_basis_and_wrong_snapshot_are_not_approved(self):
        for mutation in ("missing", "hash", "missing_base"):
            with self.subTest(mutation=mutation):
                task, host = self.prepare()
                item = host["evidence_specs"][1]
                if mutation == "missing": item.pop("calculation_context_basis")
                elif mutation == "hash": item["calculation_context_basis"]["index_variant"]["snapshot_hash"] = "invalid"
                else:
                    item["calculation_context"].pop("index_base")
                    item["calculation_context_basis"].pop("index_base")
                self.assert_not_computed(self.run_host(task, host))

    def test_future_and_predicted_levels_do_not_become_observed_changes(self):
        for option in (dict(future=True), dict(predicted=True)):
            with self.subTest(option=option):
                task, host = self.prepare(**option)
                result = self.run_host(task, host)
                self.assert_not_computed(result)
                self.assertEqual(len(result["evidence"]), 2, result["rejected_evidence"])
                self.assertTrue(any("未来窗口或预测" in gap for gap in result["gaps"]), result["gaps"])

    def test_net_total_return_cannot_be_relabelled_as_gross(self):
        task, host = self.prepare("net_total_return")
        for item in host["evidence_specs"]:
            item["calculation_context"]["index_variant"] = "gross_total_return"
        host["calculations"][0]["parameters"]["index_variant"] = "gross_total_return"
        result = self.run_host(task, host)
        self.assert_not_computed(result)
        self.assertTrue(result["rejected_evidence"])

    def test_unknown_variant_missing_parameters_and_invalid_windows_reject(self):
        _, host = self.prepare()
        params = host["calculations"][0]["parameters"]
        for change in ({"index_variant": "unknown"}, {"end_date": params["start_date"]}, {"unit": "元"}):
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_calculation_parameters(METHOD, dict(params, **change))
        incomplete = dict(params); incomplete.pop("index_currency")
        with self.assertRaises(ContractError): validate_calculation_parameters(METHOD, incomplete)
        with self.assertRaises(ContractError): calculate(METHOD, {"start_level": 0, "end_level": 1000}, params)

    def test_hallucinated_point_claim_is_rejected(self):
        task, host = self.prepare()
        host["answers"]["q1"]["answer"] = "终点为9999点，区间变化为5%。"
        result = self.run_host(task, host)
        self.assertNotEqual(result["questions"][0]["status"], "已回答")

    def test_point_quantity_grammar_keeps_chinese_numbers_and_excludes_prose(self):
        self.assertEqual([float(item["value"]) for item in _quantities("指数收盘3000点，上涨十点。")], [3000, 10])
        self.assertFalse(_quantities("第一点说明这一点，上午9点发布。"))
        self.assertEqual(len(_quantities("这3000点是当前点位。")), 1)


if __name__ == "__main__":
    unittest.main()
