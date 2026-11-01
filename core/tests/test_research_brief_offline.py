"""Shared, conditional research planning guidance; no financial quality claim."""
import copy
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_INTENT, ENTRIES, INTENTS, canonical, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.planning import question_specs
from researchhelper.reasoning import model_object, validate_plan

ROOT = Path(__file__).resolve().parents[2]
CUTOFF = "2026-07-20T18:00:00+08:00"


class ResearchBriefTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="research-brief-", dir=ROOT / "result/acceptance")
        self.addCleanup(directory.cleanup)
        self.agent = ResearchAgent(ResearchArchive(directory.name), ROOT, offline=True)
        denied = patch.object(socket.socket, "connect", side_effect=AssertionError("No network"))
        denied.start()
        self.addCleanup(denied.stop)

    def task(self, entry="ETF", intent="比较对象", questions=None):
        return self.agent.create("核对所给材料的指定维度，不扩大对象范围", entry=entry, intent=intent,
                                 objects=["合成对象甲", "合成对象乙"], cutoff=CUTOFF, questions=questions)

    def brief(self, task):
        from researchhelper.planning import research_brief
        return research_brief(task)

    def test_all_forty_pairs_keep_identity_and_conditional_not_mandatory_checks(self):
        for entry in ENTRIES:
            for intent in INTENTS:
                if intent == DOCUMENT_CONTENT_INTENT:
                    continue
                with self.subTest(entry=entry, intent=intent):
                    task = self.task(entry, intent)
                    brief = self.brief(task)
                    self.assertEqual(brief["entry"], entry)
                    self.assertEqual(brief["intent"], intent)
                    self.assertEqual(brief["binding"]["input_hash"], task["input_hash"])
                    self.assertEqual(brief["declared_scope"]["objects"], task["objects"])
                    self.assertEqual(brief["declared_scope"]["question"], task["question"])
                    self.assertTrue(brief["entry_focus"])
                    self.assertTrue(brief["intent_goal"])
                    self.assertEqual(brief["advisory_only"], True)
                    self.assertEqual(brief["automatic_plan_change"], False)
                    self.assertEqual(brief["financial_fact_or_approval"], False)

    def test_entry_contexts_are_distinct_and_not_universal_valuation_templates(self):
        briefs = [self.brief(self.task(entry=entry)) for entry in ENTRIES]
        self.assertEqual(len({brief["entry_focus"] for brief in briefs}), len(ENTRIES))
        self.assertEqual(len({tuple(brief["conditional_checks"]) for brief in briefs}), len(ENTRIES))
        for brief in briefs:
            self.assertEqual(brief["required_method_ids"], [])
            self.assertIsNone(brief["required_thesis_count"])

    def test_document_content_has_no_investment_or_provider_guidance(self):
        for entry in ENTRIES:
            brief = self.brief(self.task(entry, DOCUMENT_CONTENT_INTENT))
            self.assertEqual(brief["conditional_checks"], [])
            self.assertFalse(brief["external_acquisition_allowed_by_brief"])
            self.assertEqual(brief["evidence_purpose"], "document_content")
            self.assertEqual(brief["required_method_ids"], [])

    def test_reading_brief_does_not_change_task_or_grant_data_actions(self):
        task = self.task()
        before = copy.deepcopy(task)
        brief = self.brief(task)
        brief["declared_scope"]["objects"].append("不应写入的新对象")
        brief["conditional_checks"].clear()
        self.assertEqual(task, before)
        self.assertEqual(self.brief(task)["declared_scope"]["objects"], before["objects"])
        self.assertEqual(task["ledger"]["dispatches"], [])

    def test_scope_tampering_is_rejected_before_planning_dispatch(self):
        task = self.task()
        task["objects"].append("未经登记的对象")
        with self.assertRaises(ContractError):
            self.brief(task)

    def test_explicit_fee_comparison_not_forced_to_do_unrelated_checks(self):
        task = self.task(questions=[dict(id="fees", kind="comparison", question="合同披露费用如何比较")])
        before = copy.deepcopy(task)
        self.brief(task)
        adopted = validate_plan(task, dict(questions=question_specs(task), data_requests=[], calculations=[]))
        self.assertEqual(adopted, question_specs(task))
        self.assertEqual(task, before)

    def test_source_skill_and_app_share_brief_without_second_model(self):
        spec = importlib.util.spec_from_file_location("source_research_brief_skill", ROOT / "core/session.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        directory = tempfile.TemporaryDirectory(prefix="brief-host-project-", dir=ROOT / "result/acceptance")
        self.addCleanup(directory.cleanup)
        session = module.Session(directory.name)
        task = session.create("核对合成政策与对象影响", entry="事件", intent="验证观点", cutoff=CUTOFF)
        self.assertEqual(session.tools(task["id"])["research_brief"], self.brief(task))
        self.assertIsNone(session.agent.model)
        self.assertEqual(session.status(task["id"])["ledger"]["dispatches"], [])

    def test_model_plan_receives_exact_shared_brief_and_preserves_response_contract(self):
        task = self.task()
        received = []
        class Host:
            def reason(self, task, messages):
                received.append(copy.deepcopy(messages))
                return json.dumps(dict(questions=question_specs(task), data_requests=[], calculations=[]))
        payload = {"question": task["question"], "intent": task["intent"]}
        original = copy.deepcopy(payload)
        value, receipt = model_object(Host(), task, "plan", payload, {"questions", "data_requests", "calculations"})
        self.assertIn("research_brief=" + canonical(self.brief(task)), received[0][0]["content"])
        self.assertEqual(json.loads(received[0][1]["content"]), original)
        self.assertEqual(payload, original)
        self.assertEqual(receipt["research_brief_version"], 1)
        self.assertEqual(receipt["research_brief_hash"], fingerprint(self.brief(task)))
        self.assertEqual(receipt["context_hash"], fingerprint(payload))
        self.assertEqual(set(value), {"questions", "data_requests", "calculations"})

    def test_wrong_brief_cannot_override_current_scope(self):
        task = self.task()
        calls = []
        class Host:
            def reason(self, *args):
                calls.append(True)
                raise AssertionError("Wrong brief must be rejected before dispatch")
        brief = self.brief(task)
        brief["entry"] = "个股"
        with self.assertRaises(ContractError):
            model_object(Host(), task, "plan", {"research_brief": brief}, {"questions", "data_requests", "calculations"})
        self.assertEqual(calls, [])

    def test_actual_app_plan_receives_brief_without_changing_explicit_questions(self):
        received = []
        task = self.task(questions=[dict(id="fees", kind="comparison", question="合同披露费用如何比较")])
        self.agent.offline = False
        self.agent.model = object()
        def reason(model, current, phase, payload, fields):
            received.append(copy.deepcopy(payload))
            return dict(questions=question_specs(current), data_requests=[], calculations=[]), {"phase": phase}
        with patch("researchhelper.engine.model_object", side_effect=reason):
            final = self.agent.start(task["id"])
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["research_brief"], self.brief(task))
        self.assertEqual(question_specs(final), question_specs(task))
        self.assertEqual(final["status"], "部分完成")
        self.assertEqual(final["ledger"]["used"]["data_requests"], 0)


if __name__ == "__main__":
    unittest.main()
