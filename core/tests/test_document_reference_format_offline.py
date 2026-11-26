"""Task-bound reference grammar, including prior stored v1 receipts."""

import unittest

from researchhelper.contracts import (ContractError, document_content_requirements,
    fingerprint, missing_document_requirements, validate_document_coverage)
from tests import test_document_coverage_offline as coverage_fixture


class DocumentReferenceFormatTests(unittest.TestCase):
    def setUp(self):
        self.fixture = coverage_fixture.DocumentCoverageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_new_dictionary_scope_does_not_repeat_question_prefix(self):
        task = self.fixture.task()
        requirements = document_content_requirements(task["questions"][0])
        self.assertEqual(requirements[0]["id"], fingerprint(requirements[0]["text"]))
        done = self.fixture.run_specs(task, self.fixture.specs(task))
        self.assertEqual(done["status"], "已完成", done.get("error"))

    def test_exact_legacy_references_remain_readable_without_rewriting_review(self):
        task = self.fixture.task()
        specs = self.fixture.specs(task)
        for spec in specs:
            spec["review"]["evidence_need_ids"]["q1"] = ["q1:" + x for x in spec["review"]["evidence_need_ids"]["q1"]]
        done = self.fixture.run_specs(task, specs)
        self.assertEqual(done["status"], "已完成", done.get("error"))
        self.assertEqual(done["evidence"][0]["applicability_review"]["evidence_need_ids"], specs[0]["review"]["evidence_need_ids"])
        self.fixture.agent.export(done["id"])

    def test_prefix_from_another_question_is_never_inferred_or_stripped(self):
        task = self.fixture.task()
        ident = document_content_requirements(task["questions"][0])[0]["id"]
        with self.assertRaises(ContractError):
            validate_document_coverage(task, ["q1"], {"evidence_need_ids": {"q1": ["q2:" + ident]}})

    def test_shortened_hash_or_need_description_is_not_coerced_into_reference(self):
        task = self.fixture.task()
        need = document_content_requirements(task["questions"][0])[0]
        for invalid in (need["id"][:8], need["text"]):
            with self.subTest(reference=invalid), self.assertRaises(ContractError):
                validate_document_coverage(task, ["q1"], {"evidence_need_ids": {"q1": [invalid]}})

    def test_duplicate_aliases_cannot_increase_coverage(self):
        task = self.fixture.task()
        q = task["questions"][0]
        ident = document_content_requirements(q)[0]["id"]
        items = [{"applicability_review": {"evidence_need_ids": {"q1": [ident, "q1:" + ident]}}}]
        self.assertEqual(len(missing_document_requirements(q, items)), 3)

    def test_frozen_legacy_delivery_still_exports_unchanged(self):
        # Actual v1 result from the previous turn, not a newly minted fixture.
        import hashlib
        import json
        import sqlite3
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        receipt = root / "result/acceptance/document-coverage-replay.json"
        if not receipt.exists():
            self.skipTest("Retained v1 replay is not shipped with the package")
        record = json.loads(receipt.read_text())
        case = next(c for c in record["checks"] if c["mode"] == "host_reviewed_correction")
        report = Path(case["report"])
        archive = report.parent.parent / "data/researchhelper/researchhelper.sqlite3"
        before = hashlib.sha256(archive.read_bytes()).hexdigest()
        with sqlite3.connect(archive.as_uri() + "?mode=ro", uri=True) as db:
            task = json.loads(db.execute("SELECT body FROM tasks WHERE id=?", (case["task_id"],)).fetchone()[0])
        from researchhelper.reports import compose
        compose(task)
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), before)

    def test_retained_model_hash_links_work_but_unanswered_plan_item_stays_open(self):
        import base64
        import copy
        import json
        import sqlite3
        from pathlib import Path
        from researchhelper.planning import question_specs
        root = Path(__file__).resolve().parents[2]
        archive = root / "result/acceptance/live-document-content/isolated-model-ki9_hjxg/data/researchhelper/researchhelper.sqlite3"
        if not archive.exists():
            self.skipTest("Retained v12 model output is not shipped with the package")
        with sqlite3.connect(archive.as_uri() + "?mode=ro", uri=True) as db:
            old = json.loads(db.execute("SELECT body FROM tasks WHERE id=?", ("rh_63752788426a46b4a5b25d8c635fedaa",)).fetchone()[0])
        agent = self.fixture.agent
        task = agent.create(old["question"], intent=old["intent"], objects=old["objects"],
            questions=question_specs(old), cutoff=old["cutoff"])
        original = old["materials"][0]["snapshot"]
        task = agent.add_material(task["id"], original["name"], base64.b64decode(original["original_base64"]))
        specs = copy.deepcopy(old["host_result"]["evidence_specs"])
        for spec in specs:
            spec["artifact_id"] = task["materials"][0]["id"]
        done = self.fixture.run_specs(task, specs)
        self.assertEqual(len(done["evidence"]), 6, done.get("error"))
        self.assertEqual(done["rejected_evidence"], [])
        self.assertEqual(done["status"], "部分完成")
        self.assertIn("仅核对留存文字", "".join(done["gaps"]))
