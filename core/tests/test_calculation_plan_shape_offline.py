"""Typed calculation plans, with synthetic inputs and no provider calls."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, METHOD_REGISTRY
from researchhelper.engine import ResearchAgent
from researchhelper.planning import question_specs
from researchhelper.reasoning import PHASE_RULES, validate_plan

ROOT = Path(__file__).resolve().parents[2]


class CalculationPlanShapeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="rh-calculation-shape-")
        self.addCleanup(folder.cleanup)
        guard = patch("socket.socket.connect", side_effect=AssertionError("External calls forbidden"))
        guard.start()
        self.addCleanup(guard.stop)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        self.task = self.agent.create("核查510300.SH两日未复权价格变化，不作买卖推荐",
            entry="ETF", objects=["510300.SH"], cutoff="2026-10-07T18:00:00+08:00")
        self.proposal = {"questions": question_specs(self.task), "data_requests": [],
            "calculations": [{"id": "price_change", "method": "observed_price_return_v1",
                "inputs": {"start_price": "price_start", "end_price": "price_end"},
                "parameters": dict(METHOD_REGISTRY["observed_price_return_v1"]["parameters"],
                    start_date="2026-09-29", end_date="2026-09-30"),
                "assumptions": {}, "question_ids": ["q1"]}]}
        self.proposal["questions"][0]["method_needs"] = ["observed_price_return_v1"]

    def test_plan_explains_empty_assumptions_object(self):
        self.assertIn("assumptions始终为JSON对象，没有用户假设时填{}", PHASE_RULES["plan"])

    def test_supplement_explains_same_empty_assumptions_object(self):
        self.assertIn("assumptions始终为JSON对象，没有用户假设时填{}", PHASE_RULES["supplement"])

    def test_plan_and_supplement_explain_all_container_types(self):
        for phase in ("plan", "supplement"):
            with self.subTest(phase=phase):
                self.assertIn("inputs和parameters均为JSON对象", PHASE_RULES[phase])
                self.assertIn("question_ids为非空问题id列表", PHASE_RULES[phase])

    def test_wrong_assumptions_shape_has_precise_error_without_mutation(self):
        for value in ([], None, "", 0, False):
            proposed = copy.deepcopy(self.proposal)
            proposed["calculations"][0]["assumptions"] = value
            before = copy.deepcopy(proposed)
            with self.subTest(value=value), self.assertRaisesRegex(ContractError, "assumptions须为JSON对象"):
                validate_plan(self.task, proposed)
            self.assertEqual(proposed, before)
            self.assertEqual(self.agent.archive.get(self.task["id"]), self.task)

    def test_wrong_parameters_shape_has_precise_error(self):
        proposed = copy.deepcopy(self.proposal)
        proposed["calculations"][0]["parameters"] = []
        with self.assertRaisesRegex(ContractError, "parameters须为JSON对象"):
            validate_plan(self.task, proposed)

    def test_empty_assumptions_object_is_valid_without_creating_evidence(self):
        before = copy.deepcopy(self.proposal)
        validate_plan(self.task, self.proposal)
        self.assertEqual(before, self.proposal)
        self.assertEqual(self.agent.archive.get(self.task["id"]), self.task)
        self.assertEqual(self.task["evidence"], [])
        self.assertEqual(self.task["ledger"]["used"]["model_calls"], 0)

    def test_question_binding_is_still_required(self):
        proposed = copy.deepcopy(self.proposal)
        proposed["calculations"][0]["question_ids"] = []
        with self.assertRaises(ContractError):
            validate_plan(self.task, proposed)

    def test_unrequested_assumption_is_still_rejected(self):
        proposed = copy.deepcopy(self.proposal)
        proposed["calculations"][0]["assumptions"] = {"rate": 0.1}
        with self.assertRaises(ContractError):
            validate_plan(self.task, proposed)


if __name__ == "__main__":
    unittest.main()
