"""A required thesis must not invalidate unchanged host material readings."""

import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from researchhelper.planning import input_fingerprint
from tests import test_autonomous_reasoning_offline as fixture


class HostThesisReadBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.AutonomousReasoningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        guard = patch("socket.socket.connect", side_effect=AssertionError("External calls forbidden"))
        guard.start();self.addCleanup(guard.stop)
        self.agent = self.fixture.agent

    def material_task(self, *, legacy=False):
        task = self.fixture.policy(questions=[{"id": "q1", "question": "仅根据条款能否识别经营变化？", "kind": "mechanism"}])
        if legacy:
            task["requested_theses"] = True
            task["input_hash"] = input_fingerprint(task)
            self.agent.archive.save(task)
        spec = self.fixture.policy_spec(task)
        source = task["materials"][0]
        catalog = self.agent.source_catalog(task["id"])
        reading = self.agent.read_materials(task["id"], [{
            "artifact_id": source["id"], "snapshot_hash": source["snapshot_hash"], "queries": ["发布日期", "生效"],
            "parts": [], "reason": "核对原文条款与公开时间", "question_ids": ["q1"]}], catalog["binding"])
        return task, spec, reading

    def complete(self, *, legacy=False):
        task, spec, reading = self.material_task(legacy=legacy)
        proposal = {"evidence_specs": [spec], "reading_hash": reading["reading_hash"]}
        checkpoint = self.agent.start(task["id"], proposal, defer_delivery=True)
        self.assertTrue(checkpoint.get("host_checkpoint"), checkpoint.get("error"))
        result = self.agent.start(task["id"], dict(proposal,
            answers={"q1": {"answer": "仅根据该条款不能识别经营变化；条款内容不等于实际经营结果。", "refs": ["effective_date"], "calculation_refs": []}},
            theses=[{"title": "条款不足以识别经营变化", "judgment": "证据不足", "question_ids": ["q1"],
                     "support": ["effective_date"], "counter": [], "calculation_refs": [],
                     "investment_meaning": "不能从条款直接推出买卖结论。", "limitation": "本次材料仅提供政策条款，不能排除其他方向。"}]))
        self.assertEqual(result["status"], "已完成", result.get("error"))
        self.assertEqual(result["input_hash"], checkpoint["input_hash"])
        self.assertEqual(result["requested_theses"], legacy)
        self.assertEqual(result["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(result["ledger"]["used"]["data_requests"], 0)

    def test_required_thesis_preserves_same_reading_binding(self):
        self.complete()

    def test_legacy_redundant_route_flag_is_not_rewritten(self):
        self.complete(legacy=True)

    def test_real_plan_changes_still_reject_old_readings(self):
        task, spec, reading = self.material_task()
        self.agent.revise_plan(task["id"], [{"id": "q1", "question": task["questions"][0]["question"],
            "kind": "mechanism", "evidence_needs": ["明确核对条款生效范围"]}], "增加真实证据需求", task["input_hash"])
        with self.assertRaisesRegex(ContractError, "读取记录不属于"):
            self.agent.start(task["id"], {"evidence_specs": [spec], "reading_hash": reading["reading_hash"]})

