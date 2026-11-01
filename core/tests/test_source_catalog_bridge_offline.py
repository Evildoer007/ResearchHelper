"""Late directory-to-original research under the default eight-call budget.

Supplier and model responses are fabricated. Parsing, evidence review,
budgeting, checkpoints, report saving and recovery use the actual kernel.
"""
import copy
import unittest
from unittest.mock import patch

from researchhelper.contracts import evidence_requirements
from researchhelper.evidence import snapshot_record
from researchhelper.model import ModelService, ModelSettings
from researchhelper.planning import question_specs
from tests.test_autonomous_reasoning_offline import FabricatedModel
from tests import test_filing_workflow_offline as filing_fixture


class SourceCatalogBridgeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = filing_fixture.FilingWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent = self.fixture.agent
        self.task = self.agent.create("600000.SH公告中的政策何时生效？", entry="事件", objects=["600000.SH"],
            cutoff="2026-10-02T18:00:00+08:00", questions=[{"id": "q1", "question": "600000.SH公告中的政策何时生效？",
                "kind": "fact", "evidence_needs": ["政策生效日期"]}])
        self.fixture.task = self.task
        settings = ModelSettings(self.agent.archive.root)
        settings.save({"endpoint": "https://example.invalid/model", "model": "fabricated-fixture-model", "api_key": "FAKE_MODEL_KEY"})
        self.agent.model = ModelService(settings, offline=False, journal=self.agent._journal)
        self.agent.add_material(self.task["id"], "initial.txt", b"Published on 2026-09-30. Draft only; effective date is not stated.")
        self.task = self.agent.archive.get(self.task["id"])

    def catalog(self, **changes):
        record = snapshot_record(self.task, "ifind", "report_query", self.fixture.need["parameters"], copy.deepcopy(filing_fixture.RESPONSE))
        record.update(question_ids=["q1"], **changes)
        self.task["source_records"] = [record]
        return record

    def directory_proposal(self):
        return {"question_ids": ["q1"], "reason": "首份材料没有生效条款，需查披露目录",
            "expected_change": "取得可定位的原件候选后核查正文", "data_requests": [self.fixture.need],
            "calculations": [], "artifact_ids": [], "stop_reason": ""}

    def extract(self, _):
        task = self.agent.archive.get(self.task["id"])
        original = next(record for record in task["source_records"] if record["kind"] == "filing")
        spec = self.fixture.spec(original)
        spec["review"]["evidence_need_ids"] = {"q1": [item["id"] for item in evidence_requirements(task["questions"][0])]}
        return {"evidence_specs": [spec]}

    def test_metadata_only_directory_never_spends_an_extraction_call(self):
        self.catalog()
        self.task["materials"] = []
        self.task["host_result"] = {}
        with patch("researchhelper.engine.model_object", return_value=({"evidence_specs": []}, {"synthetic": True})) as extract:
            self.agent._extract_model_sources(self.task)
        extract.assert_not_called()
        self.assertFalse(self.task["evidence"])

    def test_two_late_supplements_save_verified_pdf_fact_under_default_budget(self):
        states = []
        def download(_):
            task = self.agent.archive.get(self.task["id"])
            states.append(copy.deepcopy(task))
            self.assertEqual(task["evidence"], [])
            self.assertNotEqual(task["questions"][0]["status"], "已回答")
            self.assertTrue(task["supplement_history"][0]["results"]["filing_candidates_added"])
            return self.fixture.download_proposal(task)
        model = FabricatedModel([
            {"questions": question_specs(self.task), "data_requests": [], "calculations": []},
            {"evidence_specs": []}, self.directory_proposal(), download, self.extract, self.fixture.answer(),
        ])
        with patch.object(self.agent.collector, "external", side_effect=self.fixture.mocked_transport), \
                patch("researchhelper.model.urllib.request.urlopen", side_effect=model.respond):
            result = self.agent.start(self.task["id"])
        self.assertEqual(result["status"], "已完成", result.get("error") or result["gaps"])
        self.assertEqual(self.fixture.dispatches, ["report_query", "filing_original"])
        self.assertEqual(result["ledger"]["used"]["model_calls"], 6)
        self.assertEqual(result["ledger"]["limits"]["model_calls"], 8)
        self.assertEqual(result["ledger"]["reserve"]["model_calls"], 1)
        self.assertEqual(result["ledger"]["used"]["data_requests"], 2)
        self.assertEqual(result["ledger"]["supplements"], 2)
        self.assertEqual(result["version"], self.task["version"])
        self.assertEqual(result["evidence"][0]["source_check"], "通过")
        self.assertEqual(result["evidence"][0]["applicability_check"], "通过")
        self.assertTrue(result["delivery"] and len(states) == 1)
        restored = self.agent.archive.get(self.task["id"])
        self.assertEqual(restored["ledger"], result["ledger"])
        from researchhelper.archive import ResearchArchive
        from researchhelper.engine import ResearchAgent
        reopened = ResearchAgent(ResearchArchive(self.agent.archive.root), self.agent.resources, offline=True)
        self.assertEqual(reopened.archive.get(self.task["id"])["ledger"], result["ledger"])
        self.assertEqual(self.agent.export(self.task["id"]), reopened.export(restored["id"]))
        self.assertFalse(model.outputs)

    def test_initial_directory_fixture_uses_no_metadata_extraction(self):
        # Retain every old original/PDF, review, budget and saving assertion.
        # Only remove the now-unused empty directory extraction response and
        # update the number of real model dispatches from five to four.
        import hashlib
        from pathlib import Path
        path = Path(filing_fixture.__file__)
        before = path.read_bytes()
        source = before.decode()
        old_queue = 'outputs = [plan, {"evidence_specs": []},'
        old_count = 'self.assertEqual(result["ledger"]["used"]["model_calls"], 5)'
        self.assertEqual(source.count(old_queue), 1)
        self.assertEqual(source.count(old_count), 1)
        source = source.replace(old_queue, 'outputs = [plan,').replace(old_count,
            'self.assertEqual(result["ledger"]["used"]["model_calls"], 4)')
        namespace = {"__file__": str(path), "__name__": "catalog_bridge_fixture"}
        exec(compile(source, str(path) + "[metadata phase fixture]", "exec"), namespace)
        fixture = namespace["FilingWorkflowTests"]()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.test_model_selects_retained_directory_and_delivers_actual_pdf_fact()
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), hashlib.sha256(before).hexdigest())

    def test_tampered_directory_is_not_executable_progress(self):
        record = self.catalog()
        record["catalog_entries"][0]["report_title"] = "篡改标题"
        self.assertEqual(self.agent._supplement_filing_candidates(self.task, {"q1"}), [])

    def test_old_version_and_foreign_task_are_not_executable_progress(self):
        for changes in ({"research_version": self.task["version"] + 1}, {"task_id": "foreign-task"}):
            with self.subTest(changes=changes):
                self.catalog(**changes)
                self.assertEqual(self.agent._supplement_filing_candidates(self.task, {"q1"}), [])

    def test_unrelated_and_already_attempted_originals_are_not_new_candidates(self):
        record = self.catalog()
        record["question_ids"] = ["not-selected"]
        self.assertEqual(self.agent._supplement_filing_candidates(self.task, {"q1"}), [])
        record["question_ids"] = ["q1"]
        from researchhelper.contracts import fingerprint
        params = {"catalog_id": record["id"], "source_id": "10001"}
        key = fingerprint({"source": "ifind", "operation": "filing_original", "parameters": params})
        self.task["source_attempts"] = {key: {"status": "失败或中断"}}
        self.assertEqual(self.agent._supplement_filing_candidates(self.task, {"q1"}), [])

    def test_empty_directory_stops_without_second_round_or_fabricated_fact(self):
        def transport(task, source, operation, parameters, credentials):
            self.assertEqual(operation, "report_query")
            from researchhelper.contracts import charge
            charge(task["ledger"], "data_requests")
            return snapshot_record(task, source, operation, parameters, {"errorcode": 0, "tables": []})
        model = FabricatedModel([{ "questions": question_specs(self.task), "data_requests": [], "calculations": []},
            {"evidence_specs": []}, self.directory_proposal()])
        with patch.object(self.agent.collector, "external", side_effect=transport), \
                patch("researchhelper.model.urllib.request.urlopen", side_effect=model.respond):
            result = self.agent.start(self.task["id"])
        self.assertEqual(result["ledger"]["supplements"], 1)
        self.assertEqual(result["ledger"]["used"]["model_calls"], 3)
        self.assertEqual(result["evidence"], [])
        self.assertNotEqual(result["questions"][0]["status"], "已回答")
        self.assertIn("未增加", result["supplement_stop_reason"])


if __name__ == "__main__":
    unittest.main()
