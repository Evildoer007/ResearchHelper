"""Independent cross-version workflow checks using retained originals only."""

import copy
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.reasoning import phase_output_example
from researchhelper.reports import build_version_changes, compose, render, validate_version_changes
from tests import test_research_coverage_offline as fixtures
from tests import test_tracking_contract_offline as tracking_fixture

ROOT = Path(__file__).resolve().parents[2]
RULE = tracking_fixture.RULE
NEW_RULE = dict(RULE, indicator="政策资格下一版", condition="新条款公布后重新核对资格", benchmark="本版资格条款")


class VersionReviewTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="version-review-", dir=ROOT / "result/acceptance")
        self.addCleanup(folder.cleanup)
        self.fixture = fixtures.ResearchCoverageTests.__new__(fixtures.ResearchCoverageTests)
        self.fixture.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        self.agent = self.fixture.agent

    def host_result(self, task, rule=NEW_RULE, title="本版适用资格解释", second=False):
        thesis = self.fixture.thesis()
        thesis.update(title=title, tracking=copy.deepcopy(rule))
        theses = [thesis]
        if second:
            theses.append(dict(thesis, title="本版生效日期解释", tracking=None))
        return {"evidence_specs": self.fixture.specs(task),
                "answers": {"q1": {"answer": "政策自2026年7月21日起生效，适用于符合条件的企业。",
                                      "refs": ["effective_date", "eligibility"], "calculation_refs": []}},
                "theses": theses}

    def complete(self, rule=RULE):
        task = self.fixture.task(kind="mechanism")
        result = self.agent.start(task["id"], self.host_result(task, rule, "原版适用资格解释"))
        self.assertEqual(result["status"], "已完成", result.get("error"))
        return result

    def update(self, done, rule=NEW_RULE, cutoff="2026-08-20T18:00:00+08:00", second=False):
        result = self.agent.update(done["id"], cutoff=cutoff, host_result=self.host_result(done, rule, second=second))
        self.assertEqual(result["status"], "部分完成", result.get("error"))
        self.assertTrue(result["delivery"], result.get("error"))
        return result

    def test_original_and_current_conditions_belong_to_distinct_versions(self):
        before = self.complete()
        old_report = self.agent.archive.report(before["id"], 1)
        after = self.update(before)
        self.assertEqual(after["update_from"]["conditions"], before["tracking"])
        self.assertEqual(after["tracking"][0]["indicator"], NEW_RULE["indicator"])
        self.assertEqual(after["tracking"][0]["research_version"], 2)
        self.assertEqual(after["changes"]["prior_basis"]["conditions"][0]["research_version"], 1)
        self.assertEqual(self.agent.archive.report(before["id"], 1), old_report)

    def test_new_conditions_are_visible_even_when_original_has_none(self):
        before = self.complete(None)
        after = self.update(before)
        self.assertEqual(after["update_from"]["conditions"], [])
        self.assertEqual(after["changes"]["original_conditions"], [])
        self.assertEqual(after["tracking"][0]["indicator"], NEW_RULE["indicator"])
        paper = self.agent.export(after["id"])["html"]
        self.assertIn(NEW_RULE["indicator"], paper)
        self.assertIn("原版本未登记适用的验证条件", paper)

    def test_removed_rule_does_not_replace_original_rule_or_imply_resolution(self):
        before = self.complete()
        after = self.update(before, None)
        self.assertEqual(after["tracking"], [])
        self.assertEqual(after["changes"]["original_conditions"], before["tracking"])
        self.assertIn("未取得新时点证据", " ".join(after["gaps"]))

    def test_named_theses_are_not_paired_by_reused_ordinal_id(self):
        before = self.complete()
        after = self.update(before, second=True)
        old, new = after["changes"]["prior_basis"], after["changes"]["current_basis"]
        self.assertEqual(old["theses"][0]["id"], new["theses"][0]["id"])
        self.assertNotEqual(old["theses"][0]["title"], new["theses"][0]["title"])
        self.assertEqual(len(new["theses"]), 2)
        self.assertNotIn("matches", after["changes"])

    def test_extraction_revision_on_same_snapshot_is_not_missed(self):
        before = self.complete()
        after = self.update(before)
        changed = copy.deepcopy(after)
        changed["evidence"][0]["text"] = "同一原件的另一段提取内容"
        result = build_version_changes(changed, self.agent.archive.report(before["id"], 1))
        self.assertEqual(result["revised"], ["effective_date"])
        self.assertEqual(changed["evidence"][0]["snapshot_hash"], before["evidence"][0]["snapshot_hash"])
        with self.assertRaises(ContractError):
            compose(changed)  # Comparison alone never approves a changed excerpt.

    def test_three_versions_use_immediate_frozen_predecessor(self):
        first = self.complete()
        second = self.update(first)
        frozen = [self.agent.archive.report(first["id"], version) for version in (1, 2)]
        third = self.update(second, None, "2026-09-20T18:00:00+08:00")
        self.assertEqual(third["changes"]["prior_version"], 2)
        self.assertEqual(third["changes"]["research_version"], 3)
        self.assertEqual(third["changes"]["prior_basis"]["conditions"], second["tracking"])
        self.assertEqual([self.agent.archive.report(first["id"], version) for version in (1, 2)], frozen)

    def test_failed_third_update_does_not_show_second_updates_diff(self):
        second = self.update(self.complete())
        old = self.agent.archive.report(second["id"], 2)
        step = self.agent._step

        def interrupted(task, node, host_result):
            if node == 'evidence':
                raise ContractError('模拟新版取证中断')
            return step(task, node, host_result)

        with patch.object(self.agent, '_step', side_effect=interrupted):
            result = self.agent.update(second["id"], cutoff="2026-09-20T18:00:00+08:00",
                                       host_result=self.host_result(second))
        self.assertEqual(result["status"], "失败")
        self.assertEqual(result['error'], '模拟新版取证中断')
        self.assertNotIn("changes", result)
        self.assertEqual(result["update_from"]["version"], 2)
        self.assertEqual(self.agent.archive.report(second["id"], 2), old)

    def test_report_rejects_changed_or_omitted_current_diff_fields(self):
        after = self.update(self.complete())
        for field in ("added", "prior_judgments", "current_judgments", "original_conditions", "current_basis"):
            task = copy.deepcopy(after)
            task["changes"][field] = ["fabricated"]
            with self.subTest(field=field), self.assertRaises(ContractError):
                compose(task)

    def test_archive_rechecks_old_basis_against_original_delivery(self):
        after = self.update(self.complete())
        task = copy.deepcopy(after)
        task["changes"]["prior_basis"]["theses"][0]["title"] = "事后篡改原观点"
        # Internal consistency is insufficient: the saved predecessor is authority.
        from researchhelper.reports import _version_changes
        task["changes"] = _version_changes(task, task["changes"]["prior_basis"], task["update_from"]["digest"])
        with self.assertRaises(ContractError):
            self.agent.archive.deliver(task, compose(task), "untrusted")

    def test_forged_parent_task_version_digest_and_conditions_are_rejected(self):
        after = self.update(self.complete())
        for field, value in (("version", 0), ("digest", "untrusted"), ("conditions", [])):
            task = copy.deepcopy(after)
            task["update_from"][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_version_changes(task, self.agent.archive.report(task["id"], 1))

    def test_same_bytes_with_new_receipt_do_not_prove_new_real_world_facts(self):
        after = self.update(self.complete())
        self.assertEqual(after["changes"]["added"], [])
        self.assertEqual(after["changes"]["revised"], [])
        self.assertEqual(after["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(after["ledger"]["used"]["data_requests"], 0)

    def test_report_has_named_versions_full_original_rule_and_toc(self):
        before = self.complete(dict(RULE, benchmark="<原条件>"))
        after = self.update(before)
        content = self.agent.export(after["id"])["content"]
        paper = render(content, ROOT, report_profile="detailed")
        for text in ("版本1判断", "版本2判断", "原版适用资格解释", "本版适用资格解释", "下一次主动更新", "&lt;原条件&gt;", 'href="#changes"'):
            self.assertIn(text, paper)
        self.assertEqual(fingerprint(self.agent.archive.report(after["id"], 2)["content"]), after["delivery"]["digest"])

    def test_prepared_update_reads_before_execution_and_keeps_old_report(self):
        first = self.complete()
        old = self.agent.archive.report(first["id"], 1)
        task = self.agent.prepare_update(first["id"], "2026-08-20T18:00:00+08:00")
        self.assertEqual(task["status"], "待开始")
        self.assertEqual(task["ledger"]["dispatches"], [])
        self.assertEqual((task["evidence"], task["tracking"], task["theses"]), ([], [], []))
        self.assertNotIn("changes", task)
        review = self.agent.review_baseline(task)
        self.assertEqual(review["original_conditions"], first["tracking"])
        self.assertEqual(review["prior_theses"][0]["title"], first["theses"][0]["title"])
        self.assertNotIn("support", review["prior_theses"][0])
        self.assertEqual(self.agent.archive.report(first["id"], 1), old)
        done = self.agent.start(task["id"], self.host_result(task))
        self.assertTrue(done["delivery"], done.get("error"))

    def test_app_analysis_receives_same_frozen_context_without_old_evidence(self):
        done = self.update(self.complete())
        with patch("researchhelper.engine.model_object", return_value=(phase_output_example('analyze', done), {})) as model:
            self.agent._reason_on_verified_material(done)
        payload = model.call_args.args[3]
        self.assertEqual(payload["version_review"], self.agent.review_baseline(done))
        self.assertEqual(payload["version_review"]["prior_version"], 1)
        self.assertTrue(all(t.get("support") is None for t in payload["version_review"]["prior_theses"]))
        self.assertEqual([e["id"] for e in payload["evidence"]], [e["id"] for e in done["evidence"]])
