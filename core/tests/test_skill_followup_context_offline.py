"""Host Skill prepares a new question without starting a second researcher."""

import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "followup_skill", ROOT / "core/session.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SkillFollowupContextTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="skill-followup-")
        self.addCleanup(folder.cleanup)
        self.session = MODULE.Session(folder.name)
        # Source Skill assets are assembled only in the published installation.
        # Exercise the source interface with the canonical local report assets;
        # the separate published-Skill probe does not override this path.
        self.session.agent.resources = ROOT

    def test_prepares_bound_question_without_dispatch_or_parent_rewrite(self):
        parent = self.session.create("分析招商银行600036.SH的主要风险")
        parent = self.session.run(parent["id"])
        frozen = copy.deepcopy(self.session.archive.report(parent["id"], 1))
        old = copy.deepcopy(self.session.status(parent["id"]))
        with patch.object(self.session.agent, "start", side_effect=AssertionError("不得自动启动")):
            child = self.session.follow_up(parent["id"], "那它的盈利风险呢？")
        self.assertEqual(child["status"], "待开始")
        self.assertEqual(child["objects"], ["600036.SH"])
        self.assertEqual(child["evidence"], [])
        self.assertEqual(child["materials"], [])
        self.assertIsNone(self.session.agent.model)
        self.assertEqual(child["ledger"]["used"]["model_calls"], 0)
        tools = self.session.tools(child["id"])
        context = tools["evidence"]["research_scope"]["conversation_context"]
        self.assertEqual(context["parent_binding"]["task_id"], parent["id"])
        self.assertIn("600036.SH", context["previous_question"]["text"])
        self.assertNotIn("previous_answer", context)
        self.assertEqual(tools["analysis"]["research_scope"]["conversation_context"], child['conversation_context'])
        self.assertEqual(self.session.status(parent["id"]), old)
        self.assertEqual(self.session.archive.report(parent["id"], 1), frozen)
        reopened = MODULE.Session(self.session.project)
        self.assertEqual(reopened.tools(child["id"]), tools)

    def test_explicit_new_security_replaces_previous_scope(self):
        parent = self.session.create("分析招商银行600036.SH的主要风险")
        child = self.session.follow_up(parent["id"], "改看腾讯00700.HK的风险")
        self.assertEqual(child["objects"], ["00700.HK"])
        context = self.session.tools(child["id"])["evidence"]["research_scope"]["conversation_context"]
        self.assertEqual(context["scope_relation"], "replace")
        self.assertEqual(child["parent"], parent["id"])


if __name__ == "__main__":
    unittest.main()
