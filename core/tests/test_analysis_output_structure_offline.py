"""Typed analysis examples and safe consumer diagnostics, no live services."""

import copy
import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.reasoning import (ANALYSIS_OUTPUT_VERSION, PHASE_FIELDS, PROMPT_FORMAT_VERSION,
                                      analysis_contract, model_object, phase_output_example)
from tests import test_document_content_offline as documents


class AnalysisOutputStructureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = documents.DocumentContentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = self.fixture.material_task()
        self.calls, self.journals = [], []
        network = patch("socket.socket.connect", side_effect=AssertionError("External network forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def model(self, response=None, *, journaled=False):
        calls, journals = self.calls, self.journals

        class Model:
            def reason_phase(self, task, messages, phase):
                calls.append((phase, copy.deepcopy(messages)))
                if journaled:
                    task["ledger"]["dispatches"].append({"kind": "model_calls", "status": "成功", "usage": {"total_tokens": 1}})
                sample = messages[0]["content"].split(
                    "JSON格式样例，仅说明结构，不代表本任务没有证据：", 1)[1].split("。请输出合法json：", 1)[0]
                return sample if response is None else json.dumps(response)

            def journal(self, task):
                journals.append(copy.deepcopy(task["ledger"]["dispatches"][-1]))

        return Model()

    def analyze(self, response=None, *, journaled=False):
        self.fixture.agent.model = self.model(response, journaled=journaled)
        return self.fixture.agent._reason_on_verified_material(self.task)

    def test_each_phase_sample_has_exact_fields_and_correct_container(self):
        for phase, fields in PHASE_FIELDS.items():
            with self.subTest(phase=phase):
                example = phase_output_example(phase)
                self.assertEqual(set(example), set(fields))
                self.assertEqual(type(example.get("answers")), dict if phase == "analyze" else type(None))
                model = self.model()
                value, receipt = model_object(model, self.task, phase, {}, fields)
                self.assertEqual(value, example)
                self.assertEqual(receipt["prompt_format_version"], PROMPT_FORMAT_VERSION)

    def test_shipped_example_is_accepted_by_actual_analysis_consumer(self):
        self.assertEqual(self.analyze(), {"answers": {}, "theses": []})
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(all(q["status"] != "已回答" for q in self.task["questions"]))
        self.assertEqual(self.task["evidence"], [])

    def test_analysis_version_is_separate_from_other_phases(self):
        for phase, fields in PHASE_FIELDS.items():
            _, receipt = model_object(self.model(), self.task, phase, {}, fields)
            if phase == "analyze":
                self.assertEqual(receipt["analysis_output_version"], ANALYSIS_OUTPUT_VERSION)
            else:
                self.assertNotIn("analysis_output_version", receipt)

    def test_contract_exposes_mapping_and_remains_read_only(self):
        before = fingerprint(self.task)
        contract = analysis_contract(self.task)
        self.assertEqual(contract["output_structure"], {
            "version": 2, "answers": {"type": "object", "key": "question_id", "empty": {}},
            "theses": {"type": "array", "empty": []}})
        self.assertEqual(fingerprint(self.task), before)

    def test_all_wrong_answers_containers_are_rejected_without_repair(self):
        for answers in ([], "DO_NOT_ARCHIVE_PAYLOAD", None, True, 3):
            with self.subTest(answers_type=type(answers).__name__):
                count = len(self.calls)
                with self.assertRaisesRegex(ContractError, "模型回答或论点结构无效"):
                    self.analyze({"answers": answers, "theses": []})
                self.assertEqual(len(self.calls), count + 1)
                self.assertNotIn("analysis", self.task.get("reasoning_state", {}))

    def test_all_wrong_theses_containers_are_rejected(self):
        for theses in ({}, "text", None, False, 4.2):
            with self.subTest(theses_type=type(theses).__name__):
                with self.assertRaises(ContractError):
                    self.analyze({"answers": {}, "theses": theses})

    def test_rejected_shape_journals_only_fixed_types(self):
        with self.assertRaises(ContractError):
            self.analyze({"answers": "DO_NOT_ARCHIVE_PAYLOAD", "theses": []}, journaled=True)
        event = self.task["ledger"]["dispatches"][-1]
        self.assertEqual(event["status"], "成功")
        self.assertEqual(event["usage"], {"total_tokens": 1})
        self.assertEqual(event["output_contract"]["field_structure"], {
            "accepted": False, "expected_types": {"answers": "object", "theses": "array"},
            "actual_types": {"answers": "string", "theses": "array"}, "invalid_fields": ["answers"]})
        self.assertNotIn("DO_NOT_ARCHIVE_PAYLOAD", json.dumps(self.task))
        self.assertEqual(len(self.journals), 2)
        self.assertEqual(len(self.calls), 1)

    def test_accepted_shape_has_durable_diagnostic_and_receipt(self):
        self.analyze(journaled=True)
        diagnostic = self.task["ledger"]["dispatches"][-1]["output_contract"]
        self.assertTrue(diagnostic["field_structure"]["accepted"])
        self.assertEqual(diagnostic["field_structure"]["invalid_fields"], [])
        self.assertEqual(diagnostic["analysis_output_version"], 2)
        self.assertEqual(self.task["reasoning_state"]["analysis"]["receipt"]["analysis_output_version"], 2)

    def test_host_stub_creates_no_synthetic_ledger_dispatch(self):
        before = copy.deepcopy(self.task["ledger"])
        self.analyze()
        self.assertEqual(self.task["ledger"], before)

    def test_wrong_shape_does_not_relabel_previous_dispatch(self):
        self.task["ledger"]["dispatches"].append({"kind": "model_calls", "status": "成功", "output_contract": {"phase": "extract"}})
        before = copy.deepcopy(self.task["ledger"])
        with self.assertRaises(ContractError):
            self.analyze({"answers": [], "theses": []})
        self.assertEqual(self.task["ledger"], before)

    def test_serialization_example_still_preserves_escape_characters(self):
        self.analyze()
        example = self.calls[0][1][0]["content"].split(
            "字符串转义样例，仅说明序列化，不是原件内容：", 1)[1].split("。转义后的字符串", 1)[0]
        self.assertEqual(json.loads(example)["text"], '引号"、反斜杠\\及换行\n')

    def test_nonempty_answer_mapping_keeps_exact_original_proposal(self):
        proposal = {"answers": {"q1": {"answer": "见原件", "refs": [], "calculation_refs": []}}, "theses": []}
        self.assertEqual(self.analyze(proposal), proposal)
        self.assertEqual(self.task["evidence"], [])
        self.assertTrue(all(q["status"] != "已回答" for q in self.task["questions"]))
