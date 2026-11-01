"""Extraction instructions and shared gates, using synthetic originals only."""

import copy
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, DOCUMENT_CONTENT_INTENT, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence
from researchhelper.reasoning import PHASE_RULES, evidence_contract, model_object
from researchhelper.reasoning import CONVERSATION_RULES_VERSION, PHASE_FIELDS, RULES_VERSION
from tests import test_fee_source_units_offline as fee_tests
from tests import test_followup_context_offline as followup_tests


ROOT = Path(__file__).resolve().parents[2]
TITLE = "2026年9月20日示例政策公布"
BODY = "监管机构授权中心公布，2026年9月20日样例利率为2.25%。"


class ExtractionBindingGuidanceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="rh-extraction-binding-")
        self.addCleanup(directory.cleanup)
        blocker = patch.object(socket.socket, "connect", side_effect=AssertionError("External network forbidden"))
        blocker.start()
        self.addCleanup(blocker.stop)
        self.agent = ResearchAgent(ResearchArchive(directory.name), ROOT, offline=True)
        task = self.agent.create("核对样例利率及其机制解释", entry="事件", objects=["示例政策"],
            cutoff="2026-09-21T18:00:00+08:00", questions=[
                {"id": "rates", "question": "样例利率是多少？", "kind": "fact", "evidence_needs": ["样例利率"]},
                {"id": "margin", "question": "是否能识别净息差方向？", "kind": "mechanism", "dependencies": ["rates"]}])
        self.task = self.agent.add_material(task["id"], "合成验收材料.txt", (TITLE + "\n\n" + BODY).encode())
        self.artifact = self.task["materials"][0]

    def spec(self, *, title=True, broad=False):
        requirement = evidence_contract(self.task)["question_requirements"]["rates"][0]["id"]
        return {"id": "ev_rate", "artifact_id": self.artifact["id"], "object": "示例政策",
            "record_locator": {"part": "段落2"}, "text": BODY,
            "period": "2026-09-20", "nature": "已实现事实", "unit": "%",
            "raw_token": "2.25", "raw_unit": "%", "raw_value": 2.25, "value": 2.25,
            "conversion_factor": 1, "published_at": "2026-09-20",
            "question_ids": ["rates", "margin"] if broad else ["rates"],
            "publication": {"at": "2026-09-20", "precision": "日", "timezone": "Asia/Shanghai",
                "certainty": "已确认", "basis": "留存标题的发布日期，按日末核验",
                "basis_ref": {"kind": "material", "artifact_id": self.artifact["id"],
                    "snapshot_hash": self.artifact["snapshot_hash"],
                    "part": "段落1" if title else "段落2", "quote": TITLE if title else BODY}},
            "review": {"question_ids": ["rates"], "allowed_natures": ["已实现事实"],
                "conditions": ["只用于样例利率核对，不作为净息差方向依据"], "reason": "正文直接列明样例利率",
                "evidence_need_ids": {"rates": [requirement]}}}

    def test_contract_requires_matching_evidence_and_review_question_sets(self):
        binding = evidence_contract(self.task)["question_binding_contract"]
        self.assertEqual(binding["fields"], ["question_ids", "review.question_ids"])
        self.assertTrue(binding["same_set_required"])
        self.assertFalse(binding["automatic_binding_repair"])

    def test_dependencies_do_not_automatically_expand_evidence_purpose(self):
        binding = evidence_contract(self.task)["question_binding_contract"]
        self.assertFalse(binding["dependency_implies_applicability"])
        self.assertIn("实际支持", binding["selection_rule"])

    def test_extract_instructions_describe_exact_question_binding(self):
        self.assertIn("question_ids与review.question_ids必须为相同的问题集合", PHASE_RULES["extract"])
        self.assertIn("依赖关系不自动授予", PHASE_RULES["extract"])

    def test_publication_contract_describes_original_clause_binding(self):
        contract = evidence_contract(self.task)["publication_reference_contract"]
        self.assertIn("同一语句", contract["date_association_rule"])
        self.assertIn("报告期", contract["date_association_rule"])

    def test_publication_anchor_can_differ_from_numeric_excerpt_in_same_original(self):
        contract = evidence_contract(self.task)["publication_reference_contract"]
        self.assertIn("可以不同", contract["material_locator_rule"])
        self.assertFalse(contract["automatic_reference_repair"])

    def test_readonly_contract_does_not_produce_or_modify_evidence(self):
        before = fingerprint(self.task)
        evidence_contract(self.task)
        self.assertEqual(fingerprint(self.task), before)
        self.assertFalse(self.task["evidence"])

    def test_app_model_receives_guidance_and_same_bound_contract(self):
        class Stub:
            def reason(stub, task, messages):
                stub.messages = messages
                return '{"evidence_specs":[]}'
        stub = Stub()
        contract = evidence_contract(self.task)
        model_object(stub, self.task, "extract", {"evidence_contract": contract}, {"evidence_specs"})
        self.assertIn("question_ids与review.question_ids必须为相同的问题集合", stub.messages[0]["content"])
        self.assertEqual(json.loads(stub.messages[1]["content"])["evidence_contract"], contract)

    def test_bad_release_anchor_is_still_rejected_without_silent_repair(self):
        spec = self.spec(title=False)
        before = fingerprint(spec)
        with self.assertRaisesRegex(ContractError, "公开时间未对应"):
            candidate_evidence(self.task, self.artifact, spec)
        self.assertEqual(fingerprint(spec), before)

    def test_literal_title_and_consistent_fact_binding_pass_both_gates(self):
        spec = self.spec()
        item = candidate_evidence(self.task, self.artifact, spec)
        item = ResearchAnalyzer().assess(self.task, item, spec["review"], [self.artifact])
        self.assertEqual(item["source_check"], "通过")
        self.assertEqual(item["applicability_check"], "通过")
        self.assertEqual(item["question_ids"], ["rates"])
        self.assertEqual(item["value"], 2.25)

    def test_good_publication_anchor_does_not_approve_bad_question_binding(self):
        spec = self.spec(broad=True)
        item = candidate_evidence(self.task, self.artifact, spec)
        with self.assertRaisesRegex(ContractError, "材料适用性必须绑定"):
            ResearchAnalyzer().assess(self.task, item, spec["review"], [self.artifact])

    def test_document_only_keeps_its_separate_publication_boundary(self):
        task = self.agent.create("核对原件所载样例利率", entry="事件", intent=DOCUMENT_CONTENT_INTENT,
            questions=[{"id": "doc", "question": "原件写了什么？", "kind": "fact"}])
        task = self.agent.add_material(task["id"], "合成验收材料.txt", BODY.encode())
        contract = evidence_contract(task)
        self.assertIsNone(contract["publication_reference_contract"])
        self.assertEqual(contract["document_publication_template"]["certainty"], "未知")
        self.assertTrue(contract["question_binding_contract"]["same_set_required"])

    def test_skill_exposes_same_contract_without_a_second_model(self):
        spec = importlib.util.spec_from_file_location("binding_guidance_skill", ROOT / "core/session.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        session = module.Session.__new__(module.Session)
        session.archive = self.agent.archive
        self.assertEqual(session.tools(self.task["id"])["evidence"], evidence_contract(self.task))
        self.assertIsNone(self.agent.model)


class ExtractionGuidanceVersionTests(unittest.TestCase):
    """Explicit v25 replacements for two v24-pinned cases; originals stay intact."""

    def test_conversation_protocol_version_is_separate_and_recorded_v25(self):
        fixture = followup_tests.FollowUpContextTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        parent = fixture.parent()
        child = fixture.follow(parent)
        self.assertEqual(RULES_VERSION, 25)
        for task in (parent, child):
            with patch("researchhelper.reasoning._record_output_contract") as record:
                _, receipt = model_object(fixture.agent.model, task, "extract", {}, PHASE_FIELDS["extract"])
            diagnostic = record.call_args.args[3]
            self.assertEqual(receipt["rules_version"], 25)
            if task is child:
                self.assertEqual(receipt["conversation_rules_version"], CONVERSATION_RULES_VERSION)
                self.assertEqual(diagnostic["conversation_rules_version"], CONVERSATION_RULES_VERSION)
            else:
                self.assertNotIn("conversation_rules_version", receipt)
                self.assertNotIn("conversation_rules_version", diagnostic)

    def test_knowledge_revision_preserves_original_entry_and_arithmetic_version_v25(self):
        identifier = fee_tests.METHOD_REGISTRY[fee_tests.METHOD]["knowledge_id"]
        old = fee_tests.RETAINED_VERSIONS[(identifier, 1)]
        self.assertEqual(old["version"], 1)
        self.assertNotIn("previous_version", old)
        latest = fee_tests.ResearchKnowledge().read("ETF", "比较费率", [fee_tests.METHOD])
        self.assertEqual(latest["index"][identifier], 2)
        historical = copy.deepcopy(latest)
        historical["entries"] = [copy.deepcopy(old) if entry["id"] == identifier else entry for entry in historical["entries"]]
        historical["index"][identifier] = 1
        fee_tests.ResearchKnowledge().validate_package(historical)
        self.assertEqual(fingerprint(old), fingerprint(fee_tests.RETAINED_VERSIONS[(identifier, 1)]))
        self.assertEqual(fee_tests.METHOD_REGISTRY[fee_tests.METHOD]["implementation_version"], 1)
        self.assertEqual(RULES_VERSION, 25)
        self.assertIn("raw_unit与unit仍填%", PHASE_RULES["extract"])


if __name__ == "__main__":
    unittest.main()
