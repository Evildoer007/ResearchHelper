"""Phase-specific model output checks, using fabricated transport only."""

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelService, ModelSettings
from researchhelper.planning import question_specs
from researchhelper.reasoning import COMMON_RULE, PHASE_FIELDS, RULES_VERSION, model_object

ROOT = Path(__file__).resolve().parents[2]


class ModelPhaseContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rh-phase-contract-")
        self.addCleanup(temporary.cleanup)
        self.agent = ResearchAgent(ResearchArchive(temporary.name), ROOT, offline=False)
        settings = ModelSettings(temporary.name)
        settings.save({"endpoint": "https://example.invalid/chat", "model": "offline-fixture",
                       "api_key": "FABRICATED_PHASE_TEST_KEY"})
        self.agent.model = ModelService(settings, offline=False, journal=self.agent._journal)
        self.task = self.agent.create("核查原件内容，不形成投资判断", entry="事件",
            cutoff="2026-10-01T00:00:00+08:00", questions=[{"id": "q1", "question": "核查原件内容", "kind": "fact"}])
        network = patch("socket.socket.connect", side_effect=AssertionError("External network forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def plan(self):
        return {"questions": question_specs(self.task), "data_requests": [], "calculations": []}

    @staticmethod
    def response(content):
        return io.BytesIO(json.dumps({"model": "offline-fixture", "choices": [{"finish_reason": "stop",
            "message": {"content": content, "reasoning_content": "PRIVATE_REASONING_NOT_FOR_ARCHIVE"}}],
            "usage": {"total_tokens": 1}}).encode())

    def run_response(self, content):
        with patch("researchhelper.model.urllib.request.urlopen", return_value=self.response(content)) as transport:
            result = self.agent.start(self.task["id"])
        transport.assert_called_once()
        stored = self.agent.archive.get(result["id"])
        dispatch = stored["ledger"]["dispatches"][0]
        self.assertEqual(dispatch["status"], "成功")
        self.assertEqual(stored["ledger"]["used"]["model_calls"], 1)
        self.assertEqual(stored["ledger"]["used"]["data_requests"], 0)
        self.assertIn("elapsed_seconds", dispatch)
        self.assertNotIn("PRIVATE_REASONING_NOT_FOR_ARCHIVE", json.dumps(stored))
        return stored, dispatch["output_contract"]

    def rejected(self, content, code):
        stored, diagnostic = self.run_response(content)
        self.assertEqual(stored["status"], "失败")
        self.assertIsNone(stored.get("delivery"))
        self.assertFalse(diagnostic["shape_accepted"])
        self.assertEqual(diagnostic["phase"], "plan")
        self.assertEqual(diagnostic["failure_code"], code)
        self.assertEqual(stored["questions"], self.task["questions"])
        self.assertEqual(stored["data_need_specs"], [])
        return stored, diagnostic

    def test_valid_plan_keeps_transport_and_shape_acceptance_separate(self):
        stored, diagnostic = self.run_response(json.dumps(self.plan()))
        self.assertEqual(stored["status"], "部分完成")
        self.assertTrue(diagnostic["shape_accepted"])
        self.assertNotIn("failure_code", diagnostic)
        self.assertEqual(diagnostic["rules_version"], RULES_VERSION)

    def test_other_phase_fields_are_identified_without_adopting(self):
        _, diagnostic = self.rejected('{"evidence_specs":[]}', "phase_fields_mismatch")
        self.assertEqual(diagnostic["extra_registered_fields"], ["evidence_specs"])
        self.assertEqual(diagnostic["unknown_field_count"], 0)
        self.assertEqual(diagnostic["missing_fields"], sorted(PHASE_FIELDS["plan"]))

    def test_unknown_field_names_and_values_are_not_archived(self):
        value = dict(self.plan(), PRIVATE_CUSTOMER_NAME_DO_NOT_ARCHIVE="PRIVATE_VALUE_DO_NOT_ARCHIVE")
        stored, diagnostic = self.rejected(json.dumps(value), "phase_fields_mismatch")
        self.assertEqual(diagnostic["unknown_field_count"], 1)
        self.assertEqual(diagnostic["extra_registered_fields"], [])
        serialized = json.dumps(stored)
        self.assertNotIn("PRIVATE_CUSTOMER_NAME_DO_NOT_ARCHIVE", serialized)
        self.assertNotIn("PRIVATE_VALUE_DO_NOT_ARCHIVE", serialized)

    def test_nested_private_fields_are_rejected_without_recording_the_value(self):
        value = self.plan()
        value["questions"][0]["email"] = "private-test@example.invalid"
        stored, _ = self.rejected(json.dumps(value), "private_content")
        self.assertNotIn("private-test@example.invalid", json.dumps(stored))

    def test_non_object_and_malformed_json_have_distinct_diagnostics(self):
        self.rejected('[]', "non_object_output")

    def test_invalid_json_is_durable_without_raw_output(self):
        stored, _ = self.rejected('PRIVATE_INVALID_JSON_DO_NOT_ARCHIVE', "invalid_json")
        self.assertNotIn("PRIVATE_INVALID_JSON_DO_NOT_ARCHIVE", json.dumps(stored))

    def test_duplicate_keys_do_not_silently_override(self):
        self.rejected('{"questions":[],"questions":[],"data_requests":[],"calculations":[]}', "ambiguous_json")

    def test_nonfinite_json_is_rejected(self):
        self.rejected('{"questions":[],"data_requests":[],"calculations":[NaN]}', "ambiguous_json")

    def test_shape_acceptance_does_not_imply_semantic_plan_approval(self):
        value = self.plan()
        value["questions"][0]["method_needs"] = ["not_registered"]
        stored, diagnostic = self.run_response(json.dumps(value))
        self.assertTrue(diagnostic["shape_accepted"])
        self.assertEqual(stored["status"], "失败")
        self.assertFalse(stored["reasoning_state"]["rejected_plan"]["adopted"])

    def test_phase_schema_is_appended_and_extraction_grammar_not_in_plan(self):
        with patch("researchhelper.model.urllib.request.urlopen", return_value=self.response(json.dumps(self.plan()))) as transport:
            self.agent.start(self.task["id"])
        messages = json.loads(transport.call_args.args[0].data)["messages"]
        system = messages[0]["content"]
        self.assertIn("本次阶段=plan", system)
        self.assertIn(json.dumps(sorted(PHASE_FIELDS["plan"]), separators=(",", ":")), system)
        self.assertNotIn("publication为{", system)
        self.assertNotIn("usage_scope必须", COMMON_RULE)

    def test_internal_phase_mismatch_does_not_dispatch(self):
        with patch.object(self.agent.model, "reason") as reason:
            with self.assertRaises(ContractError):
                model_object(self.agent.model, self.task, "plan", {}, {"evidence_specs"})
            with self.assertRaises(ContractError):
                model_object(self.agent.model, self.task, "unknown", {}, {"questions"})
        reason.assert_not_called()

    def test_each_registered_phase_uses_only_its_exact_top_level(self):
        class StubModel:
            def reason(stub, task, messages):
                stub.messages = messages
                return json.dumps(stub.value)
        model = StubModel()
        for phase, fields in PHASE_FIELDS.items():
            with self.subTest(phase=phase):
                model.value = {key: [] for key in fields}
                payload = {"purpose": "offline fixture"}
                value, receipt = model_object(model, self.task, phase, payload, fields)
                self.assertEqual(value, model.value)
                self.assertEqual(receipt["context_hash"], fingerprint(payload))
                self.assertEqual(receipt["output_hash"], fingerprint(value))
                self.assertIn("本次阶段=" + phase, model.messages[0]["content"])
                self.assertEqual(self.task["ledger"]["used"]["model_calls"], 0)

    def test_cancellation_between_transport_and_validation_is_preserved(self):
        calls = []
        def journal(task):
            self.agent._journal(task)
            calls.append(task["id"])
            if len(calls) == 2:
                self.agent.cancel(task["id"])
        self.agent.model.journal = journal
        with patch("researchhelper.model.urllib.request.urlopen", return_value=self.response(json.dumps(self.plan()))):
            self.agent.start(self.task["id"])
        stored = self.agent.archive.get(self.task["id"])
        self.assertEqual(stored["status"], "已取消")
        self.assertEqual(stored["ledger"]["used"]["model_calls"], 1)
        self.assertIsNone(stored.get("delivery"))
        self.assertNotIn("plan", stored.get("reasoning_state", {}))


if __name__ == "__main__":
    unittest.main()
