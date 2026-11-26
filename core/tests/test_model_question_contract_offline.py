"""Validate the real planning vocabulary and keep fabricated methods rejected."""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, METHOD_REGISTRY
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelService, ModelSettings
from researchhelper.planning import QUESTION_FIELDS, QUESTION_KINDS, normalize_questions, question_contract, question_specs
from researchhelper.reasoning import PHASE_RULES, validate_plan

ROOT = Path(__file__).resolve().parents[2]


class ModelQuestionContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rh-question-contract-")
        self.addCleanup(temporary.cleanup)
        self.agent = ResearchAgent(ResearchArchive(temporary.name), ROOT, offline=False)
        settings = ModelSettings(temporary.name)
        settings.save({"endpoint": "https://example.invalid/chat", "model": "offline-fixture", "api_key": "FABRICATED_TEST_KEY"})
        self.agent.model = ModelService(settings, offline=False, journal=self.agent._journal)
        network = patch("socket.socket.connect", side_effect=AssertionError("No network in offline acceptance"))
        network.start()
        self.addCleanup(network.stop)
        self.task = self.agent.create("核查原件发行条款，不评价投资机会", entry="事件", cutoff="2026-10-01T00:00:00+08:00",
            questions=[{"id": "terms", "question": "原件发行条款是什么", "kind": "fact"}])

    def test_schema_is_derived_from_the_validator(self):
        schema = question_contract()
        self.assertEqual(schema["registered_method_ids"], sorted(set(METHOD_REGISTRY) - {"fee_difference_v1"}))
        self.assertIn("fee_difference_v2", schema["registered_method_ids"])
        legacy = copy.deepcopy(self.task)
        legacy["fee_method_catalogue_version"] = 1
        old_methods = question_contract(legacy)["registered_method_ids"]
        self.assertIn("fee_difference_v1", old_methods)
        self.assertNotIn("fee_difference_v2", old_methods)
        self.assertNotIn("fee_difference_v1", schema["registered_method_ids"])
        self.assertEqual(schema["fields"], sorted(QUESTION_FIELDS))
        self.assertEqual(schema["kinds"], list(QUESTION_KINDS))
        self.assertEqual(schema["no_calculation_example"]["method_needs"], [])

    def test_actual_app_planner_receives_schema_and_allows_factual_path(self):
        received = []

        def model_call(model, task, phase, payload, fields):
            received.append(copy.deepcopy(payload))
            return {"questions": question_specs(task), "data_requests": [], "calculations": []}, {"phase": phase}

        with patch("researchhelper.engine.model_object", side_effect=model_call):
            final = self.agent.start(self.task["id"])
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["question_contract"], question_contract())
        self.assertEqual(final["status"], "部分完成")
        self.assertEqual(final["calculation_specs"], [])
        self.assertEqual(final["ledger"]["used"]["data_requests"], 0)

    def test_textual_verification_is_not_promoted_to_calculation(self):
        proposed = {"questions": question_specs(self.task), "data_requests": [], "calculations": []}
        proposed["questions"][0]["evidence_needs"] = ["原文阅读", "公开时间核查", "条款比对"]
        questions = validate_plan(self.task, proposed)
        self.assertEqual(questions[0]["method_needs"], [])
        self.assertIn("不是已登记计算方法", PHASE_RULES["plan"])

    def test_invented_method_and_wrong_dependency_remain_rejected(self):
        for field, value in (("method_needs", ["publication_verification"]), ("dependencies", ["原文核查"])):
            questions = question_specs(self.task)
            questions[0][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                normalize_questions(questions, self.task["question"], self.task["intent"])

    def test_schema_is_plain_serializable_without_credentials(self):
        value = json.dumps(question_contract(), ensure_ascii=False)
        self.assertNotIn("api_key", value)
        self.assertNotIn("token", value)

    def test_invalid_plan_is_retained_without_adoption_or_data_dispatch(self):
        proposed = {"questions": question_specs(self.task), "calculations": [], "data_requests": [
            {"id": "unsupported", "source": "ifind", "operation": "invented_report",
             "parameters": {}, "question_ids": ["terms"], "selection_reason": "仅测试拒绝"}]}
        response = io.BytesIO(json.dumps({"model": "offline-fixture", "choices": [
            {"message": {"content": json.dumps(proposed)}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 1}}).encode())
        original = copy.deepcopy(self.task["questions"])
        with patch("researchhelper.model.urllib.request.urlopen", return_value=response) as transport:
            final = self.agent.start(self.task["id"])
        transport.assert_called_once()
        self.assertEqual(final["status"], "失败")
        self.assertEqual(final["questions"], original)
        self.assertEqual(final["data_need_specs"], [])
        self.assertEqual(final["ledger"]["used"]["model_calls"], 1)
        self.assertEqual(final["ledger"]["used"]["data_requests"], 0)
        rejection = self.agent.archive.get(final["id"])["reasoning_state"]["rejected_plan"]
        self.assertFalse(rejection["adopted"])
        self.assertEqual(rejection["proposed"], proposed)
        self.assertIn("未登记", rejection["reason"])
        self.assertEqual(rejection["receipt"]["phase"], "plan")


if __name__ == "__main__":
    unittest.main()
