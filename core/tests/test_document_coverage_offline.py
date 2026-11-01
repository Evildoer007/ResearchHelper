"""Prerecorded original-reading obligations, with zero network or model calls."""

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import (ContractError, DOCUMENT_CONTENT_INTENT,
    document_content_answer, document_content_requirements, fingerprint, validate_evidence)
from researchhelper.engine import ResearchAgent
from researchhelper.reasoning import evidence_contract
from researchhelper.reports import compose

ROOT = Path(__file__).resolve().parents[2]
TEXT = "发行规模300亿元。\n\n期限3年。\n\n票面利率1.56%。\n\n发行日期2026年9月17日。"
NEEDS = ["发行规模", "期限", "票面利率", "发行日期"]


class DocumentCoverageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rh-doc-coverage-", dir=ROOT / "result/acceptance")
        self.addCleanup(temporary.cleanup)
        self.agent = ResearchAgent(ResearchArchive(temporary.name), ROOT, offline=True)

    def task(self, extra=None):
        question = "核对发行规模、期限、票面利率及日期"
        questions = [{"id": "q1", "question": question, "evidence_needs": NEEDS}]
        if extra:
            questions.append(extra)
        task = self.agent.create(question, intent=DOCUMENT_CONTENT_INTENT, objects=["测试发行人"], questions=questions)
        return self.agent.add_material(task["id"], "original.txt", TEXT.encode())

    def specs(self, task):
        needs = document_content_requirements(task["questions"][0])
        specs = []
        for index, text in enumerate(TEXT.split("\n\n")):
            specs.append({"id": "e" + str(index + 1), "artifact_id": task["materials"][0]["id"],
                "object": "测试发行人", "record_locator": {"part": "段落" + str(index + 1)},
                "text": text, "period": "原件陈述", "nature": "合同政策条款", "unit": "不适用",
                "question_ids": ["q1"], "usage_scope": "document_content", "published_at": None,
                "publication": evidence_contract(task)["document_publication_template"],
                "review": {"question_ids": ["q1"], "allowed_natures": ["合同政策条款"],
                    "conditions": ["仅核对原件"], "reason": "逐字摘录对应" + NEEDS[index],
                    "evidence_need_ids": {"q1": [needs[index]["id"]]}}})
        return specs

    def run_specs(self, task, specs):
        with patch.object(self.agent.collector, "external", side_effect=AssertionError("Network forbidden")):
            return self.agent.start(task["id"], {"evidence_specs": specs})

    def test_three_of_four_items_stay_partial_and_name_missing_date(self):
        task = self.task()
        done = self.run_specs(task, self.specs(task)[:3])
        self.assertEqual(done["status"], "部分完成", done.get("error"))
        self.assertEqual(len(done["evidence"]), 3)
        self.assertEqual(done["questions"][0]["status"], "未回答")
        self.assertIn("发行日期", "".join(done["gaps"]))
        self.assertIsNotNone(done["delivery"])
        self.assertEqual(done["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(done["ledger"]["used"]["data_requests"], 0)

    def test_all_four_covered_save_exact_quotes(self):
        task = self.task()
        done = self.run_specs(task, self.specs(task))
        self.assertEqual(done["status"], "已完成", done.get("error"))
        self.assertEqual(done["questions"][0]["answer"], document_content_answer(done["evidence"]))
        self.assertIn("2026年9月17日", self.agent.export(done["id"])["html"])

    def test_absent_mapping_does_not_approve_coverage(self):
        task = self.task()
        specs = self.specs(task)
        for item in specs:
            del item["review"]["evidence_need_ids"]
        done = self.run_specs(task, specs)
        self.assertEqual(len(done["evidence"]), 4)
        self.assertEqual(done["status"], "部分完成")

    def test_unknown_requirement_rejects_only_affected_excerpt(self):
        task = self.task()
        specs = self.specs(task)
        specs[-1]["review"]["evidence_need_ids"] = {"q1": ["unregistered"]}
        done = self.run_specs(task, specs)
        self.assertEqual(len(done["evidence"]), 3)
        self.assertEqual(done["status"], "部分完成")
        self.assertIn("未登记", done["rejected_evidence"][0]["rejection"])

    def test_other_question_mapping_is_rejected(self):
        task = self.task({"id": "q2", "question": "可选日期", "required": False, "evidence_needs": ["日期"]})
        specs = self.specs(task)
        specs[0]["review"]["evidence_need_ids"] = {"q2": [document_content_requirements(task["questions"][1])[0]["id"]]}
        done = self.run_specs(task, specs)
        self.assertEqual(len(done["evidence"]), 3)
        self.assertIn("对应的问题", done["rejected_evidence"][0]["rejection"])

    def test_non_list_and_duplicate_requirement_ids_are_rejected(self):
        for form in ("string", "duplicate", "empty"):
            task = self.task()
            spec = self.specs(task)[0]
            ident = spec["review"]["evidence_need_ids"]["q1"][0]
            spec["review"]["evidence_need_ids"]["q1"] = ident if form == "string" else [ident, ident] if form == "duplicate" else []
            done = self.run_specs(task, [spec])
            self.assertEqual(done["evidence"], [], form)

    def test_optional_missing_requirements_do_not_block_complete_core(self):
        task = self.task({"id": "q2", "question": "可选信息", "required": False, "evidence_needs": ["可选信息"]})
        done = self.run_specs(task, self.specs(task))
        self.assertEqual(done["status"], "已完成", done.get("error"))
        self.assertEqual(done["gaps"], [])
        self.assertTrue(done["optional_findings"])

    def test_unrelated_rejection_does_not_block_covered_core(self):
        task = self.task()
        specs = self.specs(task)
        bad = copy.deepcopy(specs[0])
        bad.update(id="irrelevant", text="原件不存在的句子")
        done = self.run_specs(task, specs + [bad])
        self.assertEqual(done["status"], "已完成", done.get("error"))
        self.assertEqual(len(done["rejected_evidence"]), 1)

    def test_report_rechecks_forged_answered_state_with_incomplete_coverage(self):
        task = self.task()
        done = self.run_specs(task, self.specs(task)[:3])
        done["questions"][0].update(status="已回答", answer=document_content_answer(done["evidence"]),
            refs=[e["id"] for e in done["evidence"]], calculation_refs=[])
        with self.assertRaisesRegex(ContractError, "全部依据需求"):
            compose(done)

    def test_report_cannot_borrow_covered_excerpt_outside_answer_refs(self):
        task = self.task()
        done = self.run_specs(task, self.specs(task))
        q = done["questions"][0]
        q["refs"] = q["refs"][:3]
        q["answer"] = document_content_answer(done["evidence"][:3])
        with self.assertRaisesRegex(ContractError, "全部依据需求"):
            compose(done)

    def test_mapping_tamper_invalidates_receipt(self):
        task = self.task()
        done = self.run_specs(task, self.specs(task))
        changed = copy.deepcopy(done["evidence"][0])
        changed["applicability_review"]["evidence_need_ids"]["q1"].append(document_content_requirements(done["questions"][0])[1]["id"])
        with self.assertRaisesRegex(ContractError, "核验记录"):
            validate_evidence(changed, done)

    def test_ids_survive_reordering_without_changing_need_meaning(self):
        question = {"id": "q1", "evidence_needs": NEEDS}
        reordered = dict(question, evidence_needs=list(reversed(NEEDS)))
        self.assertEqual({x["text"]: x["id"] for x in document_content_requirements(question)},
                         {x["text"]: x["id"] for x in document_content_requirements(reordered)})

    def test_readonly_contract_exposes_exact_plan_and_text_grammar(self):
        task = self.task()
        before = fingerprint(task)
        contract = evidence_contract(task)
        self.assertEqual(contract["question_requirements"]["q1"], document_content_requirements(task["questions"][0]))
        self.assertEqual(contract["material_text_contract"]["unit"], "不适用")
        self.assertIn("evidence_need_ids", contract["review_fields"])
        self.assertEqual(before, fingerprint(task))

    def test_empty_date_unit_is_not_silently_filled(self):
        task = self.task()
        specs = self.specs(task)
        specs[-1]["unit"] = ""
        done = self.run_specs(task, specs)
        self.assertEqual(done["status"], "部分完成")
        self.assertEqual(done["host_result"]["evidence_specs"][-1]["unit"], "")

    def test_preserved_real_proposal_no_longer_false_completes(self):
        archive = ROOT / "result/acceptance/live-document-content/isolated-model-lhnkksqr/data/researchhelper/researchhelper.sqlite3"
        if not archive.exists():
            self.skipTest("Retained local probe is not shipped with the package")
        with sqlite3.connect(archive.as_uri() + "?mode=ro", uri=True) as db:
            old = json.loads(db.execute("SELECT body FROM tasks WHERE id=?", ("rh_eeb020c9657147728be5c03d2f70bf12",)).fetchone()[0])
        from researchhelper.planning import question_specs
        task = self.agent.create(old["question"], intent=old["intent"], objects=old["objects"],
            questions=question_specs(old), cutoff=old["cutoff"])
        import base64
        material = old["materials"][0]["snapshot"]
        task = self.agent.add_material(task["id"], material["name"], base64.b64decode(material["original_base64"]))
        specs = copy.deepcopy(old["host_result"]["evidence_specs"])
        for item in specs:
            item["artifact_id"] = task["materials"][0]["id"]
        done = self.run_specs(task, specs)
        self.assertEqual(old["status"], "已完成")  # The faulty historical receipt remains untouched.
        self.assertEqual(done["status"], "部分完成", done.get("error"))
        self.assertEqual(len(done["evidence"]), 3)
        self.assertIn("日期", "".join(done["gaps"]))
