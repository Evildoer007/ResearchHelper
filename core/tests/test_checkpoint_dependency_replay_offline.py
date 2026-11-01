"""Regressions for real-document delivery losing previously answered questions."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.reasoning import evidence_contract

ROOT = Path(__file__).resolve().parents[2]


class CheckpointDependencyReplayTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="checkpoint-dependency-")
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)

    def checkpoint(self, question="文件载明什么？"):
        task = self.agent.create(question, intent="原件内容核对", objects=["报告"],
            questions=[{"id": "q1", "question": "原件如何记载盈利与估值？", "kind": "fact"}])
        task = self.agent.add_material(task["id"], "report.txt", "报告\n\n原件记载收入增长。".encode())
        spec = {"id": "quote", "artifact_id": task["materials"][0]["id"], "object": "报告",
                "record_locator": {"part": "段落2"}, "text": "原件记载收入增长。", "period": "原件区间",
                "nature": "第三方估计", "unit": "不适用", "question_ids": ["q1"],
                "usage_scope": "document_content", "published_at": None,
                "publication": evidence_contract(task)["document_publication_template"],
                "review": {"question_ids": ["q1"], "allowed_natures": ["第三方估计"],
                           "conditions": ["仅核对原件"], "reason": "摘录对应原件内容问题"}}
        checkpoint = self.agent.start(task["id"], {"evidence_specs": [spec]}, defer_delivery=True)
        self.assertEqual(checkpoint["questions"][0]["status"], "已回答")
        return checkpoint

    def test_adopted_knowledge_dependencies_are_not_also_excluded(self):
        package = self.agent.knowledge.read("混合", "券商板块研究正文核对", context={
            "intent": "原件内容核对", "questions": [{"id": "q1", "question": "原件如何记载盈利与估值？", "required": True}]})
        self.assertFalse(set(package["index"]).intersection(item["id"] for item in package["excluded"]))

    def test_same_knowledge_retrieval_is_idempotent_before_delivery(self):
        task = self.checkpoint("券商板块研究正文核对")
        before = copy.deepcopy(task["knowledge"])
        self.agent._adopt_method_knowledge(task)
        self.assertEqual(task["knowledge"], before)
        done = self.agent.start(task["id"])
        self.assertEqual(done["status"], "已完成", done.get("error"))

    def test_evidence_replay_cannot_reuse_answer_node_after_answers_are_cleared(self):
        task = self.checkpoint()
        next(n for n in task["nodes"] if n["id"] == "evidence")["status"] = "待执行"
        self.agent.archive.save(task)
        with patch.object(self.agent, "_step", wraps=self.agent._step) as steps:
            done = self.agent.start(task["id"])
        self.assertEqual(done["status"], "已完成", done.get("error"))
        self.assertIn("answer", [call.args[1] for call in steps.call_args_list])
        self.assertEqual(done["questions"][0]["answer"], task["questions"][0]["answer"])
        self.assertEqual(done["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(done["ledger"]["used"]["data_requests"], 0)

    def test_unchanged_checkpoint_delivers_without_replaying_research(self):
        task = self.checkpoint()
        with patch.object(self.agent, "_step", wraps=self.agent._step) as steps:
            done = self.agent.start(task["id"])
        self.assertEqual(done["status"], "已完成", done.get("error"))
        self.assertEqual([call.args[1] for call in steps.call_args_list], ["compose", "render"])
