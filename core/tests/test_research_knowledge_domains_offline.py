"""Finite domain knowledge is conditional, source-located and version-bound."""
import copy
import unittest

from researchhelper.contracts import ContractError, METHOD_REGISTRY, fingerprint
from researchhelper.knowledge import ENTRIES, RETAINED_VERSIONS, ResearchKnowledge


class ResearchKnowledgeDomainTests(unittest.TestCase):
    def setUp(self):
        self.knowledge = ResearchKnowledge()

    def domain(self, entry, query, ident, context=None):
        package = self.knowledge.read(entry, query, context=context)
        self.knowledge.validate_package(package)
        self.assertIn(ident, package["index"])
        self.assertTrue(package["selection_reasons"][ident]["matched_terms"])
        self.assertLess(len(package["entries"]), len(ENTRIES))
        return next(item for item in package["entries"] if item["id"] == ident)

    def test_cashflow_conditions_and_counterchannels(self):
        item = self.domain("个股", "利润增长为何现金流下降，资本开支和营运资金如何影响", "rh_k_cash_profit")
        self.assertTrue(item["channels"] and item["verification_indicators"])
        self.assertIn("应付", item["counterexample"])
        self.assertIn("合并范围", item["conditions"])

    def test_macro_interest_is_conditional_not_directional_fact(self):
        item = self.domain("宏观", "降息怎样经由信贷影响企业融资成本", "rh_k_rate_transmission")
        self.assertIn("盈利预期下降", item["counterexample"])
        self.assertIn("ECB", item["conditions"])
        self.assertEqual(item["knowledge_role"], "general_principle")

    def test_index_and_etf_have_distinct_observation_requirements(self):
        index = self.domain("指数", "指数权重和市场宽度是否分化", "rh_k_index_structure")
        etf = self.domain("ETF", "ETF实际暴露和跟踪差异", "rh_k_etf_exposure")
        self.assertIn("期末权重", index["counterexample"])
        self.assertIn("旧持仓", etf["counterexample"])
        self.assertNotIn("implementation", index)
        self.assertNotIn("implementation", etf)

    def test_required_question_context_and_mixed_entry_retrieve_relevant_content(self):
        context = {"intent": "解释变化", "questions": [{"id": "q2", "question": "资本开支与现金流", "required": True},
                    {"id": "optional", "question": "指数宽度", "required": False}]}
        package = self.knowledge.read("混合", "分析原问题", context=context)
        self.assertIn("rh_k_cash_profit", package["index"])
        self.assertIn("q2", package["selection_reasons"]["rh_k_cash_profit"]["question_ids"])
        self.assertNotIn("rh_k_index_structure", package["index"])
        self.assertTrue(package["excluded"])
        mixed = self.knowledge.read("混合", "铜价传导与加工费")
        self.assertIn("rh_k_cost_pass", mixed["index"])

    def test_unknown_sector_keeps_gap_even_with_general_knowledge(self):
        package = self.knowledge.read("行业", "某先进材料的专用工艺良率", context={"industry": "先进材料"})
        self.assertIn("rh_k_fact", package["index"])
        self.assertTrue(package["unmatched"] and package["unmatched_questions"])
        self.assertNotIn("rh_k_cash_profit", package["index"])
        self.assertTrue(any("行业专用" in reason for reason in package["unmatched"]))
        self.assertFalse(any("objects" in item or "securities" in item for item in package["entries"]))

    def test_necessary_methods_not_dropped_and_sources_are_explicit(self):
        package = self.knowledge.read("混合", "现金流 利率 指数 跟踪 成本 费用 收款", methods=tuple(METHOD_REGISTRY))
        for method in METHOD_REGISTRY:
            self.assertIn(METHOD_REGISTRY[method]["knowledge_id"], package["index"])
            self.knowledge.method_binding(package, method)
        domains = {"rh_k_cash_profit", "rh_k_rate_transmission", "rh_k_index_structure", "rh_k_etf_exposure"}
        new = [item for item in ENTRIES if item["id"] in domains]
        self.assertEqual({item["id"] for item in new}, domains)
        for item in new:
            self.assertIsNone(item["publication_date"])
            self.assertIn("未知", item["publication_date_status"])
            self.assertEqual(item["access"], "public")
            self.assertTrue(item["source_reference"]["sections"])
            self.assertTrue(item["source_reference"]["url"].startswith("https://"))
            self.assertTrue(item["conditions"] and item["counterexample"] and item["verification_indicators"])
            self.assertTrue(item["dependencies"])
            self.assertIn("不是历史当时规则", item["temporal_scope"])

    def test_deepcopy_version_index_dependencies_and_read_only_authority(self):
        before = fingerprint(ENTRIES)
        package = self.knowledge.read("个股", "现金流")
        changed = copy.deepcopy(package)
        next(x for x in changed["entries"] if x["id"] == "rh_k_cash_profit")["verification_indicators"].append("伪造指标")
        with self.assertRaises(ContractError):
            self.knowledge.validate_package(changed)
        changed = copy.deepcopy(package)
        changed["index"]["rh_k_cash_profit"] = 99
        with self.assertRaises(ContractError):
            self.knowledge.validate_package(changed)
        changed = copy.deepcopy(package)
        changed["entries"] = [x for x in changed["entries"] if x["id"] != "rh_k_fact"]
        changed["index"].pop("rh_k_fact")
        with self.assertRaises(ContractError):
            self.knowledge.validate_package(changed)
        with self.assertRaises(ContractError):
            self.knowledge.publish(package)
        self.assertEqual(fingerprint(ENTRIES), before)
        for item in ENTRIES[:8]:
            self.assertGreaterEqual(item["version"], 1)
            self.assertEqual(item, RETAINED_VERSIONS[(item["id"], item["version"])])
            self.assertEqual(RETAINED_VERSIONS[(item["id"], 1)]["version"], 1)

    def test_capacity_omission_stays_visible_for_partially_matched_core_question(self):
        context = {"questions": [
            {"id": "mixed", "question": "利率与现金流", "required": True},
            {"id": "other_a", "question": "利率 指数 ETF 跟踪 成本 毛利", "required": True},
            {"id": "other_b", "question": "利率 指数 ETF 跟踪 成本 毛利", "required": True},
            {"id": "rates", "question": "利率传导", "required": True}]}
        package = self.knowledge.read("混合", "利率 指数 ETF 跟踪 成本 毛利 现金流",
                                      methods=("fee_difference_v1",), context=context)
        self.assertIn("rh_k_rate_transmission", package["index"])
        self.assertNotIn("rh_k_cash_profit", package["index"])
        self.assertIn("mixed", package["selection_reasons"]["rh_k_rate_transmission"]["question_ids"])
        gap = next(item for item in package["unmatched_questions"] if item["id"] == "mixed")
        self.assertIn("rh_k_cash_profit", {entry["id"] for entry in gap["omitted_entries"]})
        self.assertTrue(any("利润、现金流与再投资约束" in text for text in package["unmatched"]))
        omitted = next(item for item in package["excluded"] if item["id"] == "rh_k_cash_profit")
        self.assertIn("mixed", omitted["question_ids"])
        self.assertIn("rh_k_fee_difference", package["index"])
        self.assertLessEqual(len(package["entries"]), 6)  # four optional + fact + required method
        self.knowledge.validate_package(package)


if __name__ == "__main__":
    unittest.main()
