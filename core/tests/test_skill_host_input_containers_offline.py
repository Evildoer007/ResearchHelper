"""Skill rejects malformed falsey inputs before saving or dispatching."""

import copy
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from researchhelper.evidence import EvidenceCollector
from tests import test_skill_source_execution_offline as fixture


class SkillHostInputContainersTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.SkillSourceExecutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.session = self.fixture.module.Session(self.fixture.project)
        self.task = self.session.create("核对材料中的事实", questions=[
            {"id": "q1", "question": "材料说明了什么", "kind": "fact"}])

    def reject(self, field, value):
        before = copy.deepcopy(self.session.status(self.task["id"]))
        with self.assertRaises(ContractError):
            self.session.run(self.task["id"], **{field: value})
        self.assertEqual(self.session.status(self.task["id"]), before)

    def test_falsey_wrong_answers_are_not_repaired(self):
        for value in ([], "", False, 0):
            with self.subTest(value=value):
                self.reject("answers", value)

    def test_falsey_wrong_array_fields_are_not_repaired(self):
        for field in ("evidence", "theses", "calculations", "evidence_specs", "data_requests"):
            for value in ({}, "", False, 0):
                with self.subTest(field=field, value=value):
                    self.reject(field, value)

    def test_nonempty_wrong_containers_are_rejected_without_saving(self):
        for field in ("evidence", "theses", "calculations", "evidence_specs", "data_requests"):
            with self.subTest(field=field):
                self.reject(field, {"invalid": "not an array"})
        self.reject("answers", [{"q1": "not an object"}])

    def test_omitted_arguments_use_typed_defaults_without_answering(self):
        result = self.session.run(self.task["id"], defer_delivery=True)
        self.assertEqual(result["run_mode"], "host")
        self.assertEqual(result["host_result"], {
            "evidence": [], "answers": {}, "theses": [], "calculations": [],
            "evidence_specs": [], "data_requests": []})
        self.assertFalse(result.get("delivery"))
        self.assertTrue(all(q["status"] != "已回答" for q in result["questions"]))
        self.assertEqual(result["ledger"]["dispatches"], [])

    def test_valid_declared_source_request_still_dispatches(self):
        session = self.fixture.module.Session(self.fixture.project, allow_network=True,
            source_credentials={"ifind": {"refresh_token": fixture.REFRESH}})
        task = session.create("核对600000.SH历史收盘价", objects=["600000.SH"],
            cutoff="2026-09-03T18:00:00+08:00", questions=[
                {"id": "q1", "question": "收盘价是多少", "kind": "fact"}])
        parameters = copy.deepcopy(fixture.PARAMETERS)
        parameters["functionpara"] = {"Currency": "RMB", "CPS": "1", "Fill": "Omit"}
        # This is input-boundary coverage, not isolated-worker acceptance.
        # Keep fabricated HTTP in this guarded process; never spawn a worker
        # that would bypass the socket/urllib mocks.
        def local_http(*args):
            return EvidenceCollector._http_json(session.agent.collector, *args)
        with patch.object(session.agent.collector, "_http_json", side_effect=local_http), \
                patch("urllib.request.urlopen", side_effect=fixture.SkillSourceExecutionTests.replies()) as transport:
            result = session.run(task["id"], data_requests=[{
                "id": "daily_close", "source": "ifind", "operation": "cmd_history_quotation",
                "parameters": parameters, "question_ids": ["q1"]}])
        self.assertEqual(transport.call_count, 2, result.get("error"))
        self.assertEqual(len(result["source_records"]), 1)
        self.assertEqual(result["ledger"]["used"]["data_requests"], 2)
        self.assertEqual(result["ledger"]["used"]["model_calls"], 0)
        self.assertTrue(all(q["status"] != "已回答" for q in result["questions"]))
