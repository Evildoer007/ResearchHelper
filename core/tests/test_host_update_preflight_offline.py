"""Malformed updates do not allocate a version or disturb frozen delivery."""
import copy
import unittest
from unittest.mock import patch
from researchhelper.contracts import ContractError
from tests import test_version_review_offline as fixture


class HostUpdatePreflightTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.VersionReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        guard = patch("socket.socket.connect", side_effect=AssertionError("External calls forbidden"))
        guard.start();self.addCleanup(guard.stop)
        self.agent = self.fixture.agent

    def reject_update(self, proposal):
        second = self.fixture.update(self.fixture.complete())
        before = copy.deepcopy(self.agent.archive.get(second["id"]))
        reports = [self.agent.archive.report(second["id"], v) for v in (1, 2)]
        with self.assertRaises(ContractError):
            self.agent.update(second["id"], cutoff="2026-09-20T18:00:00+08:00", host_result=proposal)
        self.assertEqual(self.agent.archive.get(second["id"]), before)
        self.assertEqual([self.agent.archive.report(second["id"], v) for v in (1, 2)], reports)
        with self.assertRaises(ContractError):
            self.agent.archive.get(second["id"], 3)

    def test_bad_evidence_container_does_not_allocate_version(self):
        self.reject_update({"evidence_specs": "invalid"})

    def test_bad_answers_do_not_allocate_version(self):
        self.reject_update({"answers": []})

    def test_execution_failure_still_keeps_new_version_without_stale_diff(self):
        second = self.fixture.update(self.fixture.complete())
        old = self.agent.archive.report(second["id"], 2)
        with patch.object(self.agent, "_step", side_effect=OSError("fabricated execution fault")):
            result = self.agent.update(second["id"], cutoff="2026-09-20T18:00:00+08:00",
                host_result={"evidence_specs": []})
        self.assertEqual(result["status"], "失败")
        self.assertNotIn("changes", result)
        self.assertEqual(result["update_from"]["version"], 2)
        self.assertEqual(self.agent.archive.report(second["id"], 2), old)
