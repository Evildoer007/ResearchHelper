"""Bank mechanism adoption is question-local and never supplies business facts."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import ENTRIES, ResearchKnowledge
from researchhelper import knowledge

ROOT = Path(__file__).resolve().parents[2]
BANK = "rh_k_bank_rate_repricing"
METRICS = "rh_k_bank_interest_metrics"


class BankKnowledgeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.knowledge = ResearchKnowledge()
        root = ROOT / "result/acceptance/bank-knowledge"
        root.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(prefix="isolated-", dir=root)
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        guard = patch("socket.socket.connect", side_effect=AssertionError("No external calls"))
        guard.start()
        self.addCleanup(guard.stop)

    def test_bank_rate_question_adopts_both_channels_and_metric_dependency(self):
        package = self.knowledge.read("事件", "LPR下降怎样影响银行净息差？")
        self.knowledge.validate_package(package)
        self.assertIn(BANK, package["index"])
        self.assertIn(METRICS, package["index"])
        item = next(item for item in package["entries"] if item["id"] == BANK)
        self.assertIn("压缩", item["channels"][0])
        self.assertIn("相反", item["channels"][1])
        self.assertIn("另取证", item["conditions"])
        self.assertIn("不能判断", item["counterexample"])
        self.assertNotIn("implementation", item)

    def test_policy_fact_and_broad_bank_question_do_not_force_mechanism(self):
        for query in ("LPR公告写了什么？", "银行经营情况", "货币政策利率变化", "核对贷款合同的日期"):
            with self.subTest(query=query):
                self.assertNotIn(BANK, self.knowledge.read("混合", query)["index"])

    def test_topics_cannot_be_joined_across_independent_questions(self):
        package = self.knowledge.read("混合", "分析以下问题", context={"questions": [
            {"id": "policy", "question": "LPR公告写了什么？"},
            {"id": "bank", "question": "银行名称是否正确？"}]})
        self.assertNotIn(BANK, package["index"])
        package = self.knowledge.read("混合", "分析以下问题", context={"questions": [
            {"id": "policy", "question": "LPR公告写了什么？"},
            {"id": "transmission", "question": "LPR变化如何影响银行？"}]})
        self.assertEqual(package["selection_reasons"][BANK]["question_ids"], ["transmission"])

    def test_optional_bank_question_does_not_expand_required_scope(self):
        package = self.knowledge.read("事件", "LPR公告内容", context={"questions": [
            {"id": "optional", "question": "LPR如何影响银行净息差？", "required": False}]})
        self.assertNotIn(BANK, package["index"])

    def test_margin_and_interest_income_remain_distinct_without_calculation(self):
        package = self.knowledge.read("行业", "银行净利息收入和净息差为何不同向？")
        item = next(item for item in package["entries"] if item["id"] == METRICS)
        self.assertIn("平均生息资产", item["content"])
        self.assertIn("不能与净息差互换", item["content"])
        self.assertIn("年化", item["conditions"])
        self.assertNotIn("implementation", item)

    def test_sources_and_locked_versions_are_complete_but_not_task_facts(self):
        package = self.knowledge.read("事件", "银行LPR利率传导")
        for item in (entry for entry in package["entries"] if entry["id"] in (BANK, METRICS)):
            self.assertEqual(item["version"], 1)
            self.assertTrue(item["source_reference"]["sections"])
            self.assertTrue(item["source_reference"]["source_version"])
            self.assertEqual(item["access"], "public")
            self.assertIn("不是历史当时规则", item["temporal_scope"])
            self.assertTrue(item["verification_indicators"])
            self.assertFalse(set(item) & {"objects", "securities", "value", "raw_value"})
        changed = copy.deepcopy(package)
        next(item for item in changed["entries"] if item["id"] == BANK)["channels"] = ["降息一定利好"]
        with self.assertRaises(ContractError):
            self.knowledge.validate_package(changed)

    def test_host_empty_run_has_knowledge_but_no_fabricated_answer(self):
        before = fingerprint(ENTRIES)
        task = self.agent.create("LPR变化如何影响银行净息差？", entry="事件", intent="解释变化",
                                 cutoff="2025-05-20T18:00:00+08:00")
        result = self.agent.start(task["id"], {})
        self.assertIn(BANK, result["knowledge"]["index"])
        self.assertEqual(result["status"], "部分完成", result.get("error"))
        self.assertFalse(result["evidence"] or result["theses"] or result["calculations"])
        self.assertFalse(any(question.get("answer") for question in result["questions"]))
        self.assertEqual(result["ledger"]["used"]["data_requests"], 0)
        self.assertEqual(result["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(fingerprint(ENTRIES), before)

    def test_model_plan_receives_complete_bank_knowledge_not_only_title(self):
        task = self.agent.create("银行LPR利率传导", entry="事件", intent="解释变化",
                                 cutoff="2025-05-20T18:00:00+08:00")
        captured = []
        class FabricatedModel:
            def reason(self, task, messages):
                payload = json.loads(messages[-1]["content"])
                captured.append(payload)
                return json.dumps({"questions": payload["questions"], "data_requests": [], "calculations": []}, ensure_ascii=False)
        self.agent.offline = False
        self.agent.model = FabricatedModel()
        result = self.agent.start(task["id"])
        self.assertTrue(captured)
        item = next(item for item in captured[0]["knowledge"]["entries"] if item["id"] == BANK)
        for field in ("conditions", "channels", "counterexample", "verification_indicators", "source_reference"):
            self.assertTrue(item[field])
        self.assertEqual(result["status"], "部分完成", result.get("error"))
        self.assertFalse(result["evidence"] or result["theses"])

    def test_existing_checkpoint_does_not_gain_a_default_bank_judgment(self):
        original = copy.deepcopy(ENTRIES)
        task = self.agent.create("银行LPR利率传导", entry="事件", intent="解释变化",
                                 cutoff="2025-05-20T18:00:00+08:00")
        old = [item for item in original if item["id"] not in (BANK, METRICS)]
        with patch.object(knowledge, "ENTRIES", old):
            self.agent._adopt_method_knowledge(task)
        retained = copy.deepcopy(task["knowledge"]["entries"])
        self.agent._adopt_method_knowledge(task)
        self.assertIn(BANK, task["knowledge"]["index"])
        for item in retained:
            self.assertEqual(item, next(entry for entry in task["knowledge"]["entries"] if entry["id"] == item["id"]))
        self.assertFalse(task["theses"] or task["evidence"])


if __name__ == "__main__":
    unittest.main()
