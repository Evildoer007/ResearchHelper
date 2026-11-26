"""Shared research workflow with fabricated directory responses and retained PDF bytes.

No real supplier, model, App production archive or foreground process is used.
The transport is mocked, while contracts, parsing, two reviews and delivery run.
"""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, charge, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (IFIND_REPORT_FIELDS, filing_record, snapshot_record,
    validate_filing_original_request, validate_ifind_report_request)
from researchhelper.model import ModelService, ModelSettings
from researchhelper.planning import question_specs
from researchhelper.reasoning import source_views, validate_plan
from tests.test_autonomous_reasoning_offline import FabricatedModel

ROOT = Path(__file__).resolve().parents[2]
PARAMETERS = {"codes": "600000.SH", "functionpara": {"reportType": "901"},
    "beginrDate": "2026-10-01", "endrDate": "2026-10-02",
    "outputpara": ",".join(field + ":Y" for field in IFIND_REPORT_FIELDS)}
RESPONSE = {"errorcode": 0, "tables": [{"reportDate": "2026-10-01", "thscode": "600000.SH",
    "secName": "合成测试主体", "ctime": "2026-10-01T18:00:00+08:00", "reportTitle": "合成测试政策公告",
    "pdfURL": "https://static.cninfo.com.cn/finalpage/2026-10-01/10001.PDF", "seq": "10001"}]}


def policy_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=160)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
        DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 20 100 Td (Policy effective on 2026-10-02.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class FilingWorkflowTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="filing-workflow-", dir=ROOT / "result/acceptance")
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=False,
            source_credentials={"ifind": {"access_token": "fabricated-only"}})
        self.task = self.agent.create("600000.SH公告中的政策何时生效？", entry="个股", objects=["600000.SH"],
            cutoff="2026-10-02T18:00:00+08:00", questions=[{"id": "q1", "question": "600000.SH公告中的政策何时生效？", "kind": "fact"}])
        self.need = {"id": "directory", "source": "ifind", "operation": "report_query",
            "parameters": copy.deepcopy(PARAMETERS), "question_ids": ["q1"]}
        self.dispatches = []

    def mocked_transport(self, task, source, operation, parameters, credentials):
        self.assertEqual(source, "ifind")
        if operation == "report_query":
            validate_ifind_report_request(task, parameters)
            record = snapshot_record(task, source, operation, parameters, copy.deepcopy(RESPONSE))
        elif operation == "filing_original":
            directory = validate_filing_original_request(task, parameters)
            record = filing_record(task, directory, "original.pdf", policy_pdf(), task["source_records"])
            record.update(operation=operation, parameters=copy.deepcopy(parameters), status="成功待核验",
                request_hash=fingerprint({"source": source, "operation": operation, "parameters": parameters}))
        else:
            raise AssertionError("Unexpected supplier operation: " + operation)
        charge(task["ledger"], "data_requests")
        task["ledger"]["dispatches"].append({"kind": "data_requests", "fingerprint": record["request_hash"],
            "batch": task["batch"], "attempt": 1, "status": "成功"})
        self.agent._journal(task)
        self.dispatches.append(operation)
        return record

    def spec(self, record):
        return {"id": "policy_effective", "artifact_id": record["id"], "object": record["directory"]["issuer"],
            "record_locator": {"part": "第1页"}, "text": "Policy effective on 2026-10-02.",
            "period": "政策条款", "nature": "合同政策条款", "unit": "不适用", "question_ids": ["q1"],
            "published_at": record["directory"]["published_at"],
            "publication": {"at": record["directory"]["published_at"], "precision": "秒", "timezone": "Asia/Shanghai",
                "certainty": "已确认", "basis": "留存合成目录的明确发布时间，测试不证明真实供应商时点",
                "basis_ref": {"kind": "directory", "artifact_id": record["id"], "snapshot_hash": record["hash"], "field": "published_at"}},
            "review": {"question_ids": ["q1"], "allowed_natures": ["合同政策条款"],
                "conditions": ["只核对合成政策条款的生效日，不判断经营效果"], "reason": "逐页原文与公布、生效时点分别核对"}}

    @staticmethod
    def answer():
        return {"answers": {"q1": {"answer": "政策自2026年10月2日起生效。", "refs": ["policy_effective"], "calculation_refs": []}}, "theses": []}

    def download_proposal(self, task):
        catalog = next(record for record in task["source_records"] if record.get("operation") == "report_query")
        return {"question_ids": ["q1"], "reason": "目录只有标题，缺政策原文", "expected_change": "取得实际PDF以核对条款生效日",
            "data_requests": [{"id": "original", "source": "ifind", "operation": "filing_original",
                "parameters": {"catalog_id": catalog["id"], "source_id": "10001"}, "question_ids": ["q1"],
                "selection_reason": "核对本公告政策条款的实际生效日期"}],
            "calculations": [], "artifact_ids": [], "stop_reason": ""}

    def test_catalog_only_does_not_answer_body_question(self):
        with patch.object(self.agent.collector, "external", side_effect=self.mocked_transport):
            result = self.agent.start(self.task["id"], {"data_requests": [self.need]}, defer_delivery=True)
        self.assertEqual(result["status"], "待开始", result.get("error"))
        self.assertEqual(len(result["source_records"][0]["catalog_entries"]), 1)
        self.assertFalse(result["evidence"] or result["theses"] or result["delivery"])
        self.assertEqual(result["questions"][0]["status"], "未回答")
        view = source_views(result)[0]
        self.assertEqual(view["catalog_entries"][0]["source_id"], "10001")

    def test_model_selects_retained_directory_and_delivers_actual_pdf_fact(self):
        settings = ModelSettings(self.agent.archive.root)
        settings.save({"endpoint": "https://example.invalid/model", "model": "fabricated-fixture-model", "api_key": "fabricated-only"})
        self.agent.model = ModelService(settings, offline=False, journal=self.agent._journal)
        plan = {"questions": question_specs(self.task), "data_requests": [self.need], "calculations": []}
        # A directory is navigation input, so it does not spend an extraction call.
        outputs = [plan,
            lambda _: self.download_proposal(self.agent.archive.get(self.task["id"])),
            lambda _: {"evidence_specs": [self.spec(next(record for record in self.agent.archive.get(self.task["id"])["source_records"] if record["kind"] == "filing"))]},
            self.answer()]
        model = FabricatedModel(outputs)
        with patch.object(self.agent.collector, "external", side_effect=self.mocked_transport), \
                patch("researchhelper.model.urllib.request.urlopen", side_effect=model.respond):
            result = self.agent.start(self.task["id"])
        self.assertEqual(result["status"], "已完成", result.get("error") or result["gaps"])
        self.assertEqual(self.dispatches, ["report_query", "filing_original"])
        self.assertEqual(result["ledger"]["used"]["data_requests"], 2)
        self.assertEqual(result["ledger"]["used"]["model_calls"], 4)
        self.assertEqual(len(model.requests), 4)
        self.assertFalse(model.outputs)
        self.assertEqual(result["ledger"]["supplements"], 1)
        self.assertEqual(result["evidence"][0]["source_check"], "通过")
        self.assertEqual(result["evidence"][0]["applicability_check"], "通过")
        self.assertTrue(result["delivery"])
        self.assertIn("政策自2026年10月2日起生效", self.agent.export(result["id"])["html"])
        self.assertFalse(result["theses"])

    def test_host_stages_share_budget_and_reuse_retained_original(self):
        with patch.object(self.agent.collector, "external", side_effect=self.mocked_transport):
            first = self.agent.start(self.task["id"], {"data_requests": [self.need]}, defer_delivery=True)
            second = self.agent.start(self.task["id"], {"supplement": self.download_proposal(first)}, defer_delivery=True)
            record = next(record for record in second["source_records"] if record["kind"] == "filing")
            final = self.agent.start(self.task["id"], {"data_requests": [self.need], "evidence_specs": [self.spec(record)], **self.answer()})
        self.assertEqual(final["status"], "已完成", final.get("error") or final["gaps"])
        self.assertEqual(self.dispatches, ["report_query", "filing_original"])
        self.assertEqual(final["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(final["ledger"]["used"]["data_requests"], 2)
        self.assertEqual(final["version"], 1)
        self.assertLessEqual(final["ledger"]["supplements"], 2)

    def test_plan_rejects_unknown_catalog_and_unannounced_security(self):
        plan = {"questions": question_specs(self.task), "data_requests": [copy.deepcopy(self.need)], "calculations": []}
        self.assertEqual(validate_plan(self.task, plan)[0]["id"], "q1")
        plan["data_requests"][0]["parameters"]["codes"] = "000001.SZ"
        with self.assertRaises(ContractError):
            validate_plan(self.task, plan)
        plan["data_requests"] = [{"id": "original", "source": "ifind", "operation": "filing_original",
            "parameters": {"catalog_id": "missing", "source_id": "10001"}, "question_ids": ["q1"]}]
        with self.assertRaises(ContractError):
            validate_plan(self.task, plan)
        self.assertEqual(self.task["ledger"]["used"]["data_requests"], 0)


if __name__ == "__main__":
    unittest.main()
