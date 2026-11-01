"""New independent tests for archive-before-dispatch follow-up preparation."""
import tempfile
import unittest
from pathlib import Path

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent

ROOT = Path(__file__).resolve().parents[2]


class AttachmentPreparationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=ROOT / "result/acceptance", prefix="attachment-prepare-")
        self.addCleanup(directory.cleanup)
        self.agent = ResearchAgent(ResearchArchive(directory.name), ROOT)

    def test_deferred_follow_up_preserves_parent_and_does_not_dispatch(self):
        parent = self.agent.create("核查原问题")
        self.agent.add_material(parent["id"], "original.txt", b"Original preserved")
        parent = self.agent.start(parent["id"])
        child = self.agent.follow_up(parent["id"], "核查新问题", start=False)
        self.assertEqual(child["status"], "待开始")
        self.assertEqual(child["batch"], 0)
        self.assertEqual(child["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(child["materials"], [])
        self.agent.add_material(child["id"], "new.txt", b"Separate original")
        self.assertEqual(len(self.agent.archive.get(parent["id"])["materials"]), 1)
        self.assertEqual(self.agent.archive.get(parent["id"])["delivery"], parent["delivery"])
        self.assertEqual(self.agent.archive.get(child["id"])["status"], "待开始")

    def test_existing_follow_up_default_still_runs(self):
        parent = self.agent.start(self.agent.create("原问题")["id"])
        child = self.agent.follow_up(parent["id"], "新问题")
        self.assertNotEqual(child["status"], "待开始")
        self.assertEqual(child["parent"], parent["id"])

    def test_invalid_preparation_option_does_not_create_a_task(self):
        parent = self.agent.create("原问题")
        with self.assertRaises(ContractError):
            self.agent.follow_up(parent["id"], "新问题", start="false")
        self.assertEqual(len(self.agent.archive.list()), 1)
