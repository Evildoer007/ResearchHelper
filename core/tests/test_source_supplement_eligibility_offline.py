"""Question-led supplements, with no supplier calls or production credentials."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import DOCUMENT_CONTENT_INTENT, charge, fingerprint
from researchhelper.engine import ResearchAgent

ROOT = Path(__file__).resolve().parents[2]


class SourceSupplementEligibilityTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / "result/acceptance/source-supplement"
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="isolated-", dir=base)
        self.addCleanup(temporary.cleanup)
        self.agent = ResearchAgent(ResearchArchive(temporary.name), ROOT, offline=False,
            source_credentials={"ifind": {"access_token": "FAKE_IFIND_TOKEN"},
                                "tinyshare": {"token": "FAKE_TINYSHARE_TOKEN"}})
        block = patch("socket.socket.connect", side_effect=AssertionError("No external calls"))
        block.start()
        self.addCleanup(block.stop)
        self.task = self.agent.create("核查600000.SH公告的适用条件", entry="事件", objects=["600000.SH"],
            cutoff="2026-10-01T12:00:00+08:00", questions=[
                {"id": "q1", "question": "公告的适用条件是什么", "kind": "fact", "evidence_needs": ["披露原件中的适用条件"]},
                {"id": "q2", "question": "该事件通过何种渠道影响经营", "kind": "mechanism", "evidence_needs": ["独立经营披露与相反渠道"]},
            ])

    def options(self, task=None):
        return self.agent._supplement_options(task or self.task)

    def test_registered_evidence_need_opens_incremental_sources_without_old_request(self):
        self.assertEqual(self.task["data_need_specs"], [])
        self.assertEqual(self.task["calculation_specs"], [])
        before = copy.deepcopy(self.task)
        options = self.options()
        self.assertEqual(options["sources"], ["ifind", "tinyshare"])
        self.assertEqual(options["source_question_ids"], {"ifind": ["q1", "q2"], "tinyshare": ["q1", "q2"]})
        self.assertEqual(self.task, before)

    def test_answered_optional_and_unregistered_needs_do_not_enable_source_actions(self):
        self.task["questions"][0]["status"] = "已回答"
        self.task["questions"][1]["required"] = False
        self.assertEqual(self.options()["sources"], [])
        self.task["questions"][1]["required"] = True
        self.task["questions"][1]["evidence_needs"] = []
        self.assertEqual(self.options()["sources"], [])

    def test_offline_and_missing_credentials_do_not_enable_source_actions(self):
        self.agent.offline = True
        self.assertEqual(self.options()["sources"], [])
        self.agent.offline = False
        self.agent.source_credentials = {}
        self.assertEqual(self.options()["sources"], [])

    def test_unknown_security_does_not_create_codes_or_executable_source_action(self):
        task = self.agent.create("研究某公司", objects=["某公司"], questions=[
            {"question": "核查披露", "kind": "fact", "evidence_needs": ["披露原件"]}])
        before = copy.deepcopy(task)
        self.assertEqual(self.options(task)["sources"], [])
        self.assertEqual(task, before)

    def test_document_content_intent_never_enables_external_sources(self):
        task = self.agent.create("核对600000.SH文件文字", objects=["600000.SH"], intent=DOCUMENT_CONTENT_INTENT,
            questions=[{"question": "文件中的条件", "kind": "fact", "evidence_needs": ["原文条件"]}])
        self.assertEqual(self.options(task)["sources"], [])

    def test_permission_failure_keeps_independent_other_source_branch(self):
        need = {"id": "denied", "source": "ifind", "operation": "report_query", "parameters": {}, "question_ids": ["q1"]}
        self.task["data_need_specs"] = [need]
        self.task["ledger"]["dispatches"].append({"kind": "data_requests", "failure_code": "permission_denied",
            "fingerprint": fingerprint({key: need[key] for key in ("source", "operation", "parameters")})})
        self.assertEqual(self.options()["sources"], ["tinyshare"])
        self.assertEqual(self.options()["source_question_ids"], {"tinyshare": ["q2"]})

    def activate(self):
        self.task.update(status="运行中", run_mode="model")
        self.agent.archive.save(self.task)

    def stop(self):
        return {"question_ids": ["q1"], "reason": "", "expected_change": "", "data_requests": [],
                "calculations": [], "artifact_ids": [], "stop_reason": "没有适用于该缺口的新请求"}

    def test_explicit_stop_keeps_budget_round_and_core_requirements(self):
        self.activate()
        before = copy.deepcopy(self.task)
        with patch("researchhelper.engine.model_object", return_value=(self.stop(), {"synthetic": True})) as decide:
            changed = self.agent._maybe_supplement(self.task["id"], self.task["batch"])
        self.assertFalse(changed)
        decide.assert_called_once()
        result = self.agent.archive.get(self.task["id"])
        self.assertEqual(result["ledger"]["supplements"], 0)
        self.assertEqual(result["questions"], before["questions"])
        self.assertEqual(result["ledger"], before["ledger"])

    def test_no_budget_stops_before_decision_or_supplier_dispatch(self):
        self.task["ledger"]["used"]["model_calls"] = self.task["ledger"]["limits"]["model_calls"] - self.task["ledger"]["reserve"]["model_calls"] - 2
        self.activate()
        with patch("researchhelper.engine.model_object") as decide, patch.object(self.agent.collector, "external") as external:
            self.assertFalse(self.agent._maybe_supplement(self.task["id"], self.task["batch"]))
        decide.assert_not_called()
        external.assert_not_called()

    def test_new_legal_filing_request_is_checkpointed_in_same_version_and_budget(self):
        from researchhelper.evidence import FILING_REQUEST_REGISTRY
        need = {"id": "new_filing", "source": "ifind", "operation": "report_query", "question_ids": ["q1"],
            "selection_reason": "优先iFinD，补查必要披露", "parameters": {"codes": "600000.SH", "beginrDate": "2026-09-01",
            "endrDate": "2026-10-01", "functionpara": {"reportType": "901"},
            "outputpara": FILING_REQUEST_REGISTRY["ifind.report_query"]["parameters"]["outputpara"]}}
        proposal = dict(self.stop(), reason="现有材料缺少适用条件", expected_change="披露原件可能核对条件与例外",
                        data_requests=[need], stop_reason="")
        self.activate()
        def decision(model, task, phase, payload, fields):
            self.assertEqual(phase, "supplement")
            self.assertIn("ifind", payload["executable_actions"]["sources"])
            charge(task["ledger"], "model_calls")
            return proposal, {"synthetic": True}
        with patch("researchhelper.engine.model_object", side_effect=decision), patch.object(self.agent.collector, "external") as external:
            self.assertTrue(self.agent._maybe_supplement(self.task["id"], self.task["batch"]))
        external.assert_not_called()  # planning persists before any real tool execution
        result = self.agent.archive.get(self.task["id"])
        self.assertEqual(result["version"], self.task["version"])
        self.assertEqual(result["ledger"]["supplements"], 1)
        self.assertEqual(result["ledger"]["used"]["model_calls"], 1)
        self.assertEqual(result["ledger"]["used"]["data_requests"], 0)
        self.assertEqual(result["supplement_history"][0]["proposal"]["data_requests"], [need])
        self.assertEqual(result["ledger"]["limits"], self.task["ledger"]["limits"])
        self.assertEqual(result["ledger"]["reserve"], self.task["ledger"]["reserve"])


if __name__ == "__main__":
    unittest.main()
