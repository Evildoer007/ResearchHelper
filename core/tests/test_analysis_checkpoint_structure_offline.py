"""Malformed analysis items must not poison resumable research checkpoints.

Uses local material and fabricated HTTP responses only. Never calls a vendor
or a real model, and does not change earlier acceptance files.
"""

import copy
import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.planning import question_specs
from researchhelper.reasoning import validate_analysis_output
from tests import test_autonomous_reasoning_offline as autonomous


class AnalysisCheckpointStructureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = autonomous.AutonomousReasoningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = self.fixture.policy()
        self.network = patch("socket.socket.connect", side_effect=AssertionError("External network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def run_outputs(self, outputs):
        model = autonomous.FabricatedModel(outputs)
        with patch("researchhelper.model.urllib.request.urlopen", side_effect=model.respond):
            result = self.fixture.agent.start(self.task["id"])
        return result, model

    def test_bad_answer_item_is_rejected_before_semantic_checkpoint(self):
        bad = {"answers": {"q1": "DO_NOT_CACHE_RESPONSE"}, "theses": []}
        final, model = self.run_outputs([self.fixture.plan(self.task),
            {"evidence_specs": [self.fixture.policy_spec(self.task)]}, bad])
        self.assertEqual(final["status"], "失败")
        self.assertEqual(len(model.requests), 3)
        self.assertIsNone(final["delivery"])
        self.assertFalse("DO_NOT_CACHE_RESPONSE" in json.dumps(final))
        self.assertNotIn("input_digest", final.get("reasoning_state", {}).get("analysis", {}))
        diagnostic = final["ledger"]["dispatches"][-1]["output_contract"]["item_structure"]
        self.assertFalse(diagnostic["accepted"])
        self.assertEqual(diagnostic["invalid_fields"], ["answers.*"])

    def test_explicit_resume_reasons_again_and_saves_report_without_resetting_budget(self):
        bad = {"answers": {"q1": "DO_NOT_CACHE_RESPONSE"}, "theses": []}
        failed, _ = self.run_outputs([self.fixture.plan(self.task),
            {"evidence_specs": [self.fixture.policy_spec(self.task)]}, bad])
        final, model = self.run_outputs([self.fixture.answer()])
        self.assertEqual(final["status"], "已完成", final.get("error"))
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(final["ledger"]["used"]["model_calls"], failed["ledger"]["used"]["model_calls"] + 1)
        self.assertEqual(final["ledger"]["used"]["data_requests"], 0)
        self.assertEqual(final["questions"][0]["status"], "已回答")
        self.assertIn("2026年7月21日", self.fixture.agent.export(final["id"])["html"])

    def test_nested_containers_are_rejected_without_repair_or_task_mutation(self):
        valid = self.fixture.answer()
        proposals = [
            {"answers": {"q1": []}, "theses": []},
            {"answers": {"q1": {"answer": "text", "refs": "secret"}}, "theses": []},
            {"answers": {"q1": {"answer": "text", "calculation_refs": [False]}}, "theses": []},
            {"answers": {"q1": {"answer": 3}}, "theses": []},
            {"answers": {}, "theses": ["secret"]},
            {"answers": {}, "theses": [{"support": "secret"}]},
        ]
        before = copy.deepcopy(self.task)
        for proposal in proposals:
            with self.subTest(proposal=proposal), self.assertRaises(ContractError):
                validate_analysis_output(None, self.task, proposal, len(self.task["ledger"]["dispatches"]))
        self.assertEqual(self.task, before)
        validate_analysis_output(None, self.task, valid, len(self.task["ledger"]["dispatches"]))
        self.assertEqual(self.task, before)

    def test_legacy_bad_cached_analysis_is_not_reused_on_explicit_resume(self):
        failed, _ = self.run_outputs([self.fixture.plan(self.task),
            {"evidence_specs": [self.fixture.policy_spec(self.task)]},
            {"answers": {"q1": "DO_NOT_CACHE_RESPONSE"}, "theses": []}])
        failed["host_result"].update(answers={"q1": "LEGACY_BAD_RESPONSE"}, theses=[])
        failed["reasoning_state"]["analysis"] = {"input_digest": fingerprint({
            "questions": question_specs(failed), "evidence": failed["evidence"],
            "calculations": failed["calculations"], "supplement": None})}
        self.fixture.agent.archive.save(failed)
        final, model = self.run_outputs([self.fixture.answer()])
        self.assertEqual(final["status"], "已完成", final.get("error"))
        self.assertEqual(len(model.requests), 1)
        self.assertFalse("LEGACY_BAD_RESPONSE" in json.dumps(final))
        self.assertEqual(final["ledger"]["used"]["model_calls"], 4)

    def test_host_rejects_bad_structure_before_persisting_or_spending(self):
        before = self.fixture.agent.archive.get(self.task["id"])
        for bad in ({"answers": {"q1": "DO_NOT_CACHE_RESPONSE"}},
                    {"theses": ["DO_NOT_CACHE_RESPONSE"]}, {"answers": []}):
            with self.subTest(field=list(bad)), self.assertRaises(ContractError):
                self.fixture.agent.start(self.task["id"], bad)
            self.assertEqual(self.fixture.agent.archive.get(self.task["id"]), before)
