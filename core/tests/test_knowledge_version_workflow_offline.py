"""Knowledge authority, checkpoint version locking and safe save retry tests."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper import knowledge
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent

ROOT = Path(__file__).resolve().parents[2]
METHOD_ID = "rh_k_fee_difference"


class KnowledgeVersionWorkflowTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / "result/acceptance/knowledge-version-workflow"
        root.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(prefix="isolated-", dir=root)
        self.addCleanup(folder.cleanup)
        self.archive = ResearchArchive(folder.name)
        self.agent = ResearchAgent(self.archive, ROOT, offline=True)
        self.old = copy.deepcopy(next(x for x in knowledge.ENTRIES if x["id"] == METHOD_ID))
        network = patch("socket.socket.connect", side_effect=AssertionError("No network in knowledge test"))
        network.start()
        self.addCleanup(network.stop)

    def publication(self, **changes):
        entries = copy.deepcopy(knowledge.ENTRIES)
        next(x for x in entries if x["id"] == METHOD_ID).update(changes)
        return entries

    def checkpoint(self, cancelled=False):
        task = self.agent.create_case("etf")
        task["ledger"]["used"].update(data_requests=2, model_calls=1, seconds=3)
        self.archive.save(task)
        original = self.agent._step
        def stop(task, node, host):
            if node == "evidence":
                if cancelled:
                    self.agent.cancel(task["id"])
                raise RuntimeError("Synthetic checkpoint interruption")
            return original(task, node, host)
        with patch.object(self.agent, "_step", side_effect=stop):
            stopped = self.agent.start(task["id"])
        self.assertEqual(stopped["status"], "已取消" if cancelled else "失败")
        self.assertEqual(stopped["knowledge"]["index"][METHOD_ID], 1)
        return stopped

    def save_checkpoint(self):
        task = self.agent.create_case("etf")
        with patch.object(self.archive, "deliver", side_effect=OSError("Synthetic save failure")):
            failed = self.agent.start(task["id"])
        self.assertEqual(failed["status"], "失败")
        self.assertTrue(failed.get("pending_content") and failed.get("pending_html"))
        return failed

    def test_read_is_deep_copy_of_authority(self):
        authority = fingerprint(knowledge.ENTRIES)
        package = self.agent.knowledge.read("ETF", "费用", ("fee_difference_v1",))
        package["entries"][0]["scope"].append("调用者伪造范围")
        package["entries"][0]["content"] = "调用者改写"
        package["index"][METHOD_ID] = 99
        self.assertEqual(fingerprint(knowledge.ENTRIES), authority)
        fresh = self.agent.knowledge.read("ETF", "费用", ("fee_difference_v1",))
        self.agent.knowledge.validate_package(fresh)
        self.assertEqual(fresh["index"][METHOD_ID], 1)

    def test_tampered_body_or_index_cannot_resume(self):
        for field in ("body", "index"):
            with self.subTest(field=field):
                task = self.checkpoint()
                if field == "body":
                    task["knowledge"]["entries"][0]["content"] += "伪造正文"
                else:
                    task["knowledge"]["index"][METHOD_ID] = 99
                self.archive.save(task)
                before = copy.deepcopy(task["ledger"])
                with self.assertRaises(ContractError):
                    self.agent.start(task["id"])
                self.assertEqual(self.archive.get(task["id"])["ledger"], before)

    def test_cancelled_and_failed_resume_keep_v1_then_active_update_uses_v2(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                task = self.checkpoint(cancelled)
                previous = copy.deepcopy(task["ledger"]["used"])
                revised = self.publication(version=2, content=self.old["content"] + " 普通修订：补充年度口径说明。")
                with patch.dict(knowledge.RETAINED_VERSIONS, {(METHOD_ID, 1): self.old}), patch.object(knowledge, "ENTRIES", revised):
                    resumed = self.agent.start(task["id"])
                    self.assertEqual(resumed["status"], "已完成", resumed.get("error"))
                    self.assertEqual(resumed["knowledge"]["index"][METHOD_ID], 1)
                    self.assertEqual(resumed["calculations"][0]["method_binding"]["knowledge_version"], 1)
                    for name in ("data_requests", "model_calls"):
                        self.assertEqual(resumed["ledger"]["used"][name], previous[name])
                    self.assertGreaterEqual(resumed["ledger"]["used"]["seconds"], previous["seconds"])
                    frozen = copy.deepcopy(self.archive.report(task["id"], resumed["version"]))
                    updated = self.agent.update(task["id"], cutoff="2026-12-31T18:00:00+08:00")
                    # Updating a fixed offline case cannot claim fresh evidence.
                    self.assertEqual(updated["status"], "部分完成", updated.get("error"))
                    self.assertTrue(any("未取得新时点证据" in gap for gap in updated["gaps"]))
                    self.assertEqual(updated["knowledge"]["index"][METHOD_ID], 2)
                    self.assertEqual(updated["calculations"][0]["method_binding"]["knowledge_version"], 2)
                    self.assertIn({"id": METHOD_ID, "prior_version": 1, "current_version": 2},
                                  updated["changes"]["knowledge_changes"])
                    self.assertEqual(self.archive.report(task["id"], resumed["version"]), frozen)

    def test_disabled_or_known_error_blocks_checkpoint_start_without_refund(self):
        for changes in ({"state": "停用"}, {"known_error": "合成错误通知"}):
            with self.subTest(changes=changes):
                task = self.checkpoint()
                with patch.object(knowledge, "ENTRIES", self.publication(**changes)):
                    with self.assertRaises(ContractError):
                        self.agent.start(task["id"])
                self.assertEqual(self.archive.get(task["id"])["ledger"], task["ledger"])

    def test_disabled_or_known_error_blocks_save_retry_without_refund(self):
        for changes in ({"state": "停用"}, {"known_error": "合成错误通知"}):
            with self.subTest(changes=changes):
                failed = self.save_checkpoint()
                with patch.object(knowledge, "ENTRIES", self.publication(**changes)):
                    with self.assertRaises(ContractError):
                        self.agent.retry_save(failed["id"])
                self.assertEqual(self.archive.get(failed["id"])["ledger"], failed["ledger"])
                with self.assertRaises(ContractError):
                    self.archive.report(failed["id"], failed["version"])

    def test_save_retry_rejects_changed_pending_content(self):
        failed = self.save_checkpoint()
        failed["pending_content"]["questions"][0]["answer"] += "未经核验的改写"
        self.archive.save(failed)
        with self.assertRaises(ContractError):
            self.agent.retry_save(failed["id"])
        self.assertEqual(self.archive.get(failed["id"])["ledger"], failed["ledger"])


if __name__ == "__main__":
    unittest.main()
