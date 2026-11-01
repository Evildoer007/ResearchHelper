"""Declared required methods must be readable before calculation inputs exist."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, METHOD_REGISTRY
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import ENTRIES
from researchhelper import knowledge


ROOT = Path(__file__).resolve().parents[2]
METHOD = "fee_difference_v1"
KNOWLEDGE_ID = METHOD_REGISTRY[METHOD]["knowledge_id"]


class DeclaredMethodKnowledgeTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / "result/acceptance/method-dependency-workflow"
        directory.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(prefix="declared-method-", dir=directory)
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        network = patch("socket.socket.connect", side_effect=AssertionError("Network forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def task(self, method=METHOD, required=True):
        questions = [{"id": "core", "question": "核查原件正文", "kind": "fact"},
                     {"id": "method", "question": "计算登记结果", "kind": "comparison",
                      "required": required, "method_needs": [method]}]
        return self.agent.create("核查原件并计算登记结果", entry="ETF", intent="比较对象",
                                 questions=questions, cutoff="2026-10-02T18:00:00+08:00")

    def test_required_method_is_adopted_without_keyword_match_or_inputs(self):
        task = self.task()
        self.agent._adopt_method_knowledge(task)
        self.assertIn(KNOWLEDGE_ID, task["knowledge"]["index"])
        self.assertEqual(task["knowledge"]["selection_reasons"][KNOWLEDGE_ID]["reason"], "必要计算方法")
        self.assertTrue(self.agent.knowledge.method_binding(task["knowledge"], METHOD))
        self.assertEqual(task["calculation_specs"], [])
        self.assertEqual(task["calculations"], [])

    def test_only_optional_method_is_not_forced_into_knowledge(self):
        task = self.task(required=False)
        self.agent._adopt_method_knowledge(task)
        self.assertNotIn(KNOWLEDGE_ID, task["knowledge"]["index"])

    def test_unknown_declared_method_is_rejected_at_question_registration(self):
        with self.assertRaisesRegex(ContractError, "未登记的计算方法"):
            self.task(method="unregistered_method")

    def test_declared_and_explicit_calculation_method_have_one_locked_entry(self):
        task = self.task()
        task["calculation_specs"] = [{"id": "pending", "method": METHOD,
            "question_ids": ["method"], "inputs": {}, "parameters": {}}]
        self.agent._adopt_method_knowledge(task)
        entries = [item for item in task["knowledge"]["entries"] if item["id"] == KNOWLEDGE_ID]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0], next(item for item in ENTRIES if item["id"] == KNOWLEDGE_ID))
        before = copy.deepcopy(task["knowledge"])
        self.agent._adopt_method_knowledge(task)
        self.assertEqual(task["knowledge"], before)
        self.assertEqual(task["calculations"], [])

    def test_explicit_optional_calculation_still_adopts_its_method(self):
        task = self.task(required=False)
        task["calculation_specs"] = [{"method": METHOD, "question_ids": ["method"]}]
        self.agent._adopt_method_knowledge(task)
        self.assertIn(KNOWLEDGE_ID, task["knowledge"]["index"])

    def test_missing_inputs_remain_incomplete_with_no_spending_or_fabrication(self):
        result = self.agent.start(self.task()["id"], {})
        self.assertEqual(result["status"], "部分完成", result.get("error"))
        self.assertIn(KNOWLEDGE_ID, result["knowledge"]["index"])
        self.assertTrue(all(question["status"] != "已回答" for question in result["questions"]))
        self.assertFalse(result["evidence"] or result["calculations"] or result["theses"])
        self.assertEqual(result["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(result["ledger"]["used"]["data_requests"], 0)
        self.assertIsNotNone(result.get("delivery"))

    def test_required_methods_are_not_displaced_by_topic_capacity(self):
        task = self.agent.create("利率 指数 ETF 跟踪 成本 毛利 现金流", entry="混合",
            cutoff="2026-10-02T18:00:00+08:00", questions=[{"id": "core",
                "question": "计算登记结果", "kind": "comparison",
                "method_needs": list(METHOD_REGISTRY)}])
        self.agent._adopt_method_knowledge(task)
        required_ids = {definition["knowledge_id"] for definition in METHOD_REGISTRY.values()}
        self.assertTrue(required_ids <= set(task["knowledge"]["index"]))
        for method in METHOD_REGISTRY:
            self.assertTrue(self.agent.knowledge.method_binding(task["knowledge"], method))
        self.assertEqual(task["calculation_specs"], [])
        self.assertEqual(task["calculations"], [])

    def test_required_method_keeps_already_adopted_version(self):
        task = self.task()
        self.agent._adopt_method_knowledge(task)
        original = copy.deepcopy(next(item for item in task["knowledge"]["entries"]
                                      if item["id"] == KNOWLEDGE_ID))
        revised = copy.deepcopy(ENTRIES)
        next(item for item in revised if item["id"] == KNOWLEDGE_ID).update(
            version=original["version"] + 1, content=original["content"] + "修订说明。")
        with patch.object(knowledge, "ENTRIES", revised):
            self.agent._adopt_method_knowledge(task)
            retained = next(item for item in task["knowledge"]["entries"] if item["id"] == KNOWLEDGE_ID)
            self.assertEqual(retained, original)
            self.assertEqual(self.agent.knowledge.method_binding(task["knowledge"], METHOD)["knowledge_version"],
                             original["version"])

    def test_inapplicable_required_method_is_not_silently_adopted(self):
        task = self.agent.create("计算登记结果", entry="事件",
            cutoff="2026-10-02T18:00:00+08:00", questions=[{"id": "core",
                "question": "计算登记结果", "kind": "comparison", "method_needs": [METHOD]}])
        with self.assertRaisesRegex(ContractError, "必要方法知识.*不适用当前入口"):
            self.agent._adopt_method_knowledge(task)
        self.assertFalse(task["calculations"])


if __name__ == "__main__":
    unittest.main()
