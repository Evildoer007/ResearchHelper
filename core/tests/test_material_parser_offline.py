"""Bounded local extraction, immutable originals and cancellation; no providers."""

import base64
import hashlib
import io
import json
import multiprocessing
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import IFIND_REPORT_FIELDS, extract_material
from researchhelper.material_parser import MaterialParser, MAX_BYTES, MAX_DOCX_EXPANDED_BYTES
from researchhelper.source_transport import IsolatedEvidenceCollector
from tests.test_filing_acquisition_offline import synthetic_pdf

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = ROOT / "result/acceptance/one-sentence-research-2l129r9v/task.json"


def stalled_parser(connection, deadline):
    connection.recv()
    time.sleep(30)


def invalid_parser(connection, deadline):
    connection.recv()
    connection.send({"kind": "response", "body": {"hash": "wrong", "parts": [], "source_check": "通过"}})
    connection.close()


def docx_bytes(*, expanded=False):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as document:
        document.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        document.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        document.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>合成材料金额100万元。</w:t></w:r></w:p><w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="2000"/></w:tblGrid><w:tr><w:tc><w:tcPr/><w:p><w:r><w:t>期限12个月</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        if expanded:
            with document.open("unused.xml", "w") as part:
                chunk = b"a" * (1024 * 1024)
                for _ in range(MAX_DOCX_EXPANDED_BYTES // len(chunk) + 1):
                    part.write(chunk)
    return output.getvalue()


class LocalOriginalCollector(IsolatedEvidenceCollector):
    def __init__(self, agent, entered=None):
        super().__init__(offline=False, journal=agent._journal, active=agent._check_run_control)
        self.entered = entered

    def _download_original(self, task, url, timeout, event, source_id=None):
        if self.entered:
            self.entered.set()
        return synthetic_pdf()


class MaterialParserTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="material-parser-", dir=ROOT / "result/acceptance")
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(Path(folder.name)), ROOT, isolate_materials=True)

    def assert_reaped(self, result):
        runtime = result["parse_runtime"]
        self.assertTrue(runtime["parser_worker_reaped"])
        self.assertTrue(runtime["transport_sender_stopped"])
        self.assertTrue(runtime["transport_receiver_stopped"])
        self.assertFalse(runtime["network_request_started"])
        self.assertFalse(any(child.pid == runtime.get("parser_worker_pid") for child in multiprocessing.active_children()))

    def filing_task(self, entered=None):
        task = self.agent.create("核对公告内容", entry="个股", objects=["600000.SH"], cutoff="2026-10-02T18:00:00+08:00")
        params = {"codes": "600000.SH", "functionpara": {"reportType": "901"}, "beginrDate": "2026-09-01", "endrDate": "2026-10-02", "outputpara": ','.join(field + ':Y' for field in IFIND_REPORT_FIELDS)}
        row = {"reportDate": "2026-09-30", "thscode": "600000.SH", "secName": "合成主体", "ctime": "2026-09-29T17:00:00+08:00", "reportTitle": "合成公告", "pdfURL": "https://static.cninfo.com.cn/finalpage/fixture.pdf", "seq": "fixture_1"}
        task = self.agent.add_snapshot(task["id"], "ifind", "report_query", params, {"errorcode": 0, "tables": [row]})
        self.agent.offline = False
        self.agent.collector = LocalOriginalCollector(self.agent, entered)
        host = {"data_requests": [{"id": "original", "source": "ifind", "operation": "filing_original", "parameters": {"catalog_id": task["source_records"][0]["id"], "source_id": "fixture_1"}, "question_ids": ["q1"], "selection_reason": "只读本机合成原件"}]}
        return task, host

    def test_retained_public_pdf_matches_all_original_pages_without_new_calls(self):
        task = json.loads(ORIGINAL.read_bytes())
        expected = next(record['snapshot']['material'] for record in task['source_records'] if record['kind'] == 'filing')
        payload = base64.b64decode(expected['original_base64'], validate=True)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), "d96b5d49ae75bec5913d032de781873565f5405bf99fd8b011eab152549862b9")
        result = MaterialParser().parse(expected['name'], payload, extract_version=expected['extract_version'])
        self.assertEqual(result["parts"], expected["parts"])
        self.assertEqual(result["hash"], expected["hash"])
        self.assertEqual(sum('pdf_table' not in part for part in result['parts']), 182)
        self.assertEqual(result['parse_runtime']['timeout_seconds'], 90)
        self.assert_reaped(result)

    def test_docx_preserves_paragraph_and_table_locators(self):
        payload = docx_bytes()
        result = MaterialParser().parse("terms.docx", payload)
        self.assertEqual(result["parts"], extract_material("terms.docx", payload)["parts"])
        self.assertIn("100万元", str(result["parts"]))
        self.assertIn("12个月", str(result["parts"]))
        self.assertEqual(result['parse_runtime']['timeout_seconds'], 30)
        self.assert_reaped(result)

    def test_invalid_pdf_does_not_expose_exception_or_invent_excerpt(self):
        result = MaterialParser().parse("bad.pdf", b"not a PDF PRIVATE_PARSE_TEXT")
        self.assertEqual(result["parts"], [])
        self.assertEqual(result["status"], "解析受限")
        self.assertEqual(result["source_check"], "待核验")
        self.assertNotIn("PRIVATE_PARSE_TEXT", str(result))
        self.assert_reaped(result)

    def test_stalled_parser_has_deadline_and_is_reaped(self):
        parser = MaterialParser()
        parser.worker_target = stalled_parser
        started = time.monotonic()
        result = parser.parse("stall.pdf", synthetic_pdf(), timeout=.6)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(result["parts"], [])
        self.assertEqual(result["parse_runtime"]["failure_code"], "response_deadline_exceeded")
        self.assert_reaped(result)

    def test_cancellation_does_not_accept_late_text(self):
        parser = MaterialParser()
        parser.worker_target = stalled_parser
        def cancelled(task):
            raise ContractError("取消")
        result = parser.parse("cancel.pdf", synthetic_pdf(), active=cancelled)
        self.assertEqual(result["parts"], [])
        self.assertEqual(result["parse_runtime"]["failure_code"], "transport_cancelled")
        self.assert_reaped(result)

    def test_invalid_result_cannot_assign_approved_provenance(self):
        parser = MaterialParser()
        parser.worker_target = invalid_parser
        result = parser.parse("bad.pdf", synthetic_pdf())
        self.assertEqual(result["parse_runtime"]["failure_code"], "invalid_parser_response")
        self.assertEqual(result["source_check"], "待核验")
        self.assertEqual(result["parts"], [])
        self.assert_reaped(result)

    def test_worker_start_failure_preserves_identity_and_stops_ipc(self):
        with patch("multiprocessing.context.SpawnProcess.start", side_effect=OSError("PRIVATE_START_ERROR")):
            result = MaterialParser().parse("failure.pdf", synthetic_pdf())
        self.assertEqual(result["parse_runtime"]["failure_code"], "worker_start_failed")
        self.assertNotIn("PRIVATE_START_ERROR", str(result))
        self.assert_reaped(result)

    def test_zero_remaining_time_does_not_launch_parser(self):
        result = MaterialParser().parse("expired.pdf", synthetic_pdf(), timeout=0)
        self.assertEqual(result["parts"], [])
        self.assertNotIn("parser_worker_pid", result["parse_runtime"])
        self.assertEqual(result["parse_runtime"]["failure_code"], "response_deadline_exceeded")

    def test_docx_expansion_is_rejected_before_extraction(self):
        payload = docx_bytes(expanded=True)
        self.assertLess(len(payload), MAX_BYTES)
        result = MaterialParser().parse("large.docx", payload)
        self.assertEqual(result["parse_runtime"]["failure_code"], "document_expansion_limit")
        self.assertEqual(result["parts"], [])
        self.assert_reaped(result)

    def test_size_limit_and_unknown_extension_preserve_existing_rejection(self):
        with self.assertRaises(ContractError):
            MaterialParser().parse("large.pdf", b"a" * (MAX_BYTES + 1))
        with self.assertRaises(ContractError):
            MaterialParser().parse("unknown.exe", b"a")

    def test_plain_text_remains_direct_and_keeps_units(self):
        result = MaterialParser().parse("amount.txt", "金额100万元。".encode())
        self.assertIn("100万元", str(result["parts"]))
        self.assertNotIn("parse_runtime", result)

    def test_runtime_metadata_does_not_change_snapshot_identity(self):
        task = self.agent.create("核对文件内容", intent="原件内容核对")
        first = self.agent.add_material(task["id"], "original.pdf", synthetic_pdf())
        second = self.agent.add_material(task["id"], "original.pdf", synthetic_pdf())
        self.assertEqual(len(second["materials"]), 1)
        self.assertEqual(first["materials"][0]["id"], second["materials"][0]["id"])
        self.assertEqual(base64.b64decode(second["materials"][0]["original_base64"]), synthetic_pdf())
        self.assertEqual(second["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(second["ledger"]["used"]["data_requests"], 0)
        self.assert_reaped(second["materials"][0])

    def test_downloaded_original_is_checkpointed_once_and_still_unapproved(self):
        task, host = self.filing_task()
        final = self.agent.start(task["id"], host)
        records = [record for record in final["source_records"] if record.get("operation") == "filing_original"]
        self.assertEqual(len(records), 1)
        self.assertEqual(base64.b64decode(records[0]["snapshot"]["material"]["original_base64"]), synthetic_pdf())
        self.assertEqual(records[0]["stages"]["source_check"], "待核验")
        self.assertEqual(final["evidence"], [])
        self.assertEqual(final["ledger"]["used"]["data_requests"], 1)
        self.assertEqual(final["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(records[0]['parse_runtime']['timeout_seconds'], 90)
        self.assert_reaped(records[0])

    def test_actual_agent_cancellation_keeps_bytes_but_no_body_or_evidence(self):
        entered = threading.Event()
        task, host = self.filing_task(entered)
        result = []
        with patch.object(MaterialParser, "worker_target", staticmethod(stalled_parser)):
            worker = threading.Thread(target=lambda: result.append(self.agent.start(task["id"], host)))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.agent.cancel(task["id"])
            finally:
                worker.join(5)
        self.assertFalse(worker.is_alive())
        stored = self.agent.archive.get(task["id"])
        self.assertEqual(stored["status"], "已取消")
        records = [record for record in stored["source_records"] if record.get("operation") == "filing_original"]
        self.assertEqual(len(records), 1)
        self.assertEqual(base64.b64decode(records[0]["snapshot"]["material"]["original_base64"]), synthetic_pdf())
        self.assertEqual(records[0]["snapshot"]["material"]["parts"], [])
        self.assertEqual(stored["evidence"], [])
        self.assertEqual(stored["ledger"]["used"]["data_requests"], 1)
        self.assertEqual(stored["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(stored["ledger"]["dispatches"][-1]["status"], "原件已获取，研究未完成")
        self.assertEqual(self.agent.run_controls, {})
        self.assert_reaped(records[0])


if __name__ == "__main__":
    unittest.main()
