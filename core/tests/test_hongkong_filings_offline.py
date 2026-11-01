"""Single-security HK filing contracts with synthetic data and no network."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (
    FILING_REQUEST_REGISTRY, candidate_evidence, data_request_contract,
    filing_record, snapshot_record, validate_filing_original_request,
    validate_ifind_report_request,
)
from researchhelper.planning import question_specs
from researchhelper.reasoning import research_scope_contract, validate_plan


ROOT = Path(__file__).resolve().parents[2]


class HongKongFilingTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.getaddrinfo", "urllib.request.urlopen"):
            blocker = patch(target, side_effect=AssertionError("Offline contracts forbid network and DNS"))
            blocker.start()
            self.addCleanup(blocker.stop)
        folder = tempfile.TemporaryDirectory(prefix="rh-hk-filing-")
        self.addCleanup(folder.cleanup)
        agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        self.task = agent.create("核对03968.HK公告条款", entry="事件", objects=["03968.HK"],
            cutoff="2026-10-02T18:00:00+08:00",
            questions=[{"id": "q1", "question": "公告条款是什么", "kind": "fact"}])
        self.params = {"codes": "03968.HK", "functionpara": {"reportType": "901"},
            "beginrDate": "2026-09-01", "endrDate": "2026-10-02",
            "outputpara": FILING_REQUEST_REGISTRY["ifind.report_query"]["parameters"]["outputpara"]}
        self.row = {"reportDate": "2026-09-30", "thscode": "03968.HK", "secName": "合成主体",
            "ctime": "2026-09-30T17:00:00+08:00", "reportTitle": "合成公告，仅用于离线契约",
            "pdfURL": "https://static.cninfo.com.cn/finalpage/synthetic-hk-fixture.pdf", "seq": "synthetic_hk_1"}

    def catalog(self, **changes):
        record = snapshot_record(self.task, "ifind", "report_query", self.params,
            {"errorcode": 0, "tables": [dict(self.row, **changes)]})
        self.task["source_records"].append(record)
        return record

    def test_explicit_single_hk_request_and_generated_plan(self):
        self.assertEqual(validate_ifind_report_request(self.task, self.params), ["2026-09-01", "2026-10-02"])
        example = next(tool["example"] for tool in data_request_contract(self.task)["tools"]
                       if tool["operation"] == "report_query")
        self.assertIsNotNone(example)
        self.assertEqual(example["parameters"]["codes"], "03968.HK")
        validate_plan(self.task, {"questions": question_specs(self.task),
            "data_requests": [example], "calculations": []})

    def test_hk_market_templates_preserve_tinyshare_boundary(self):
        # Supersedes only the old HK filing-example prohibition; retain both
        # its positive iFinD quotation and negative Tinyshare assertions.
        tools = data_request_contract(self.task)["tools"]
        self.assertTrue(next(tool for tool in tools if tool["operation"] == "cmd_history_quotation")["example"])
        self.assertTrue(all(tool["example"] is None for tool in tools if tool["source"] == "tinyshare"))
        self.assertTrue(next(tool for tool in tools if tool["operation"] == "report_query")["example"])

    def test_request_scope_type_and_date_rejections(self):
        changes = [{"codes": code} for code in ("03968.HK,00700.HK", "3968.HK", "600036.SH", "00700.HK")]
        changes += [{"mode": "allHKStock"}, {"url": self.row["pdfURL"]},
            {"functionpara": {"reportType": "901", "mode": "allHKStock"}},
            {"functionpara": {"reportType": "902"}}, {"functionpara": {"reportType": 901}},
            {"endrDate": "2026-10-03"}, {"beginrDate": "2026-10-02", "endrDate": "2026-09-01"}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_ifind_report_request(self.task, dict(self.params, **change))

    def test_response_identity_never_aliases_a_h_or_leading_zero(self):
        for code in ("600036.SH", "3968.HK", "00700.HK"):
            with self.subTest(code=code):
                record = self.catalog(thscode=code)
                entry = record["catalog_entries"][0]
                self.assertIn("响应证券身份超出请求范围", entry["gaps"])
                self.assertFalse(entry["downloadable"])
                with self.assertRaises(ContractError):
                    validate_filing_original_request(self.task,
                        {"catalog_id": record["id"], "source_id": entry["source_id"]})

    def test_unknown_publication_time_cannot_become_ordinary_evidence(self):
        record = self.catalog(ctime=None)
        directory = validate_filing_original_request(self.task,
            {"catalog_id": record["id"], "source_id": self.row["seq"]})
        self.assertIsNone(directory["published_at"])
        # Actual local text extraction isolates publication semantics without
        # claiming this synthetic directory is a real HKEX/PDF sample.
        filing = filing_record(self.task, directory, "synthetic.txt", b"Synthetic filing only.")
        self.task["source_records"].append(filing)
        self.assertEqual(filing["stages"]["source_check"], "待核验")
        self.assertEqual(filing["stages"]["applicability_check"], "待核验")
        spec = {"id": "hk_evidence", "object": "03968.HK", "published_at": "2026-09-30",
            "period": "2026-09-30", "nature": "已实现事实", "unit": "不适用", "value": None,
            "record_locator": {"part": "段落1"}, "text": "Synthetic filing only.", "question_ids": ["q1"]}
        for changes in ({}, {"published_at": None}, {"usage_scope": "document_content"},
                {"publication": {"basis_ref": {"artifact_id": filing["id"], "snapshot_hash": filing["hash"],
                    "kind": "directory", "field": "published_at"}}}):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                candidate_evidence(self.task, filing, dict(copy.deepcopy(spec), **changes))
        self.assertEqual(research_scope_contract(self.task)["mode"], "research")
        self.assertFalse(research_scope_contract(self.task)["automatic_mode_switch_allowed"])
