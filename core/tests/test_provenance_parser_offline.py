"""Bounded original revalidation, private cache integrity and cancellation."""

import base64
import copy
import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import extract_material, candidate_evidence
from researchhelper import material_parser as parser
from researchhelper.source_reader import _verify_original_text, binding, read
from tests.test_material_parser_offline import docx_bytes, stalled_parser
from tests import test_filing_acquisition_offline as filing_fixtures

synthetic_pdf = filing_fixtures.synthetic_pdf

ROOT = Path(__file__).resolve().parents[2]


def publication_pdf():
    """Actual Unicode PDF bytes, not fabricated output from a parser stub."""
    text = "Synthetic filing only. 合成原件，发布2026-09-29。"
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=300)
    cmap = DecodedStreamObject()
    mappings = [f"<{ord(char):04x}> <{ord(char):04x}>" for char in sorted(set(text))]
    cmap.set_data(("/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> def\n"
        "/CMapName /SyntheticUnicode def /CMapType 2 def\n1 begincodespacerange\n<0000> <ffff>\nendcodespacerange\n"
        + str(len(mappings)) + " beginbfchar\n" + "\n".join(mappings) + "\nendbfchar\nendcmap\nCMapName currentdict /CMap defineresource pop end end").encode())
    descendant = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/CIDFontType2"),
        NameObject("/BaseFont"): NameObject("/SyntheticUnicode"), NameObject("/DW"): NumberObject(1000),
        NameObject("/CIDSystemInfo"): DictionaryObject({NameObject("/Registry"): NameObject("/Adobe"),
            NameObject("/Ordering"): NameObject("/Identity"), NameObject("/Supplement"): NumberObject(0)})})
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type0"),
        NameObject("/BaseFont"): NameObject("/SyntheticUnicode"), NameObject("/Encoding"): NameObject("/Identity-H"),
        NameObject("/DescendantFonts"): ArrayObject([writer._add_object(descendant)]), NameObject("/ToUnicode"): writer._add_object(cmap)})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(("BT /F1 12 Tf 20 250 Td <" + text.encode("utf-16-be").hex() + "> Tj ET").encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class ProvenanceParserTests(unittest.TestCase):
    def setUp(self):
        with parser._verification_lock:
            self.previous = copy.copy(parser._verified_extractions)
            parser._verified_extractions.clear()
        self.addCleanup(self.restore)

    def restore(self):
        with parser._verification_lock:
            parser._verified_extractions.clear()
            parser._verified_extractions.update(self.previous)

    def material(self, name="original.pdf", payload=None):
        payload = synthetic_pdf() if payload is None else payload
        material = extract_material(name, payload)
        material["original_base64"] = base64.b64encode(payload).decode()
        return material, payload

    def test_cold_pdf_revalidation_is_bounded_and_repeated_checks_parse_once(self):
        material, payload = self.material()
        original = parser.MaterialParser.parse
        calls = []
        def measured(current, *args, **kwargs):
            calls.append(args[0])
            result = original(current, *args, **kwargs)
            self.assertTrue(result["parse_runtime"]["parser_worker_reaped"])
            return result
        with patch.object(parser.MaterialParser, "parse", measured):
            for _ in range(12):
                self.assertEqual(parser.verified_material(material, payload)["parts"], material["parts"])
        self.assertEqual(calls, ["original.pdf"])

    def test_initial_upload_seeds_only_private_extraction_not_research_approval(self):
        payload = synthetic_pdf()
        material = parser.MaterialParser().parse("original.pdf", payload)
        self.assertEqual(material["source_check"], "待核验")
        self.assertEqual(material["applicability_check"], "待核验")
        with patch.object(parser.MaterialParser, "parse", side_effect=AssertionError("Unexpected second parser")):
            parser.verified_material(material, payload)

    def test_docx_original_paragraphs_and_tables_verify_once(self):
        material, payload = self.material("terms.docx", docx_bytes())
        parser.verified_material(material, payload)
        self.assertEqual(len(parser._verified_extractions), 1)
        self.assertTrue(any(part["locator"] == "表1" for part in material["parts"]))
        with patch.object(parser.MaterialParser, "parse", side_effect=AssertionError("Repeated DOCX parser")):
            parser.verified_material(material, payload)

    def test_cached_text_mutation_locator_and_extraction_version_rejected(self):
        material, payload = self.material()
        parser.verified_material(material, payload)
        for modify in (lambda record: record["parts"][0].update(text="fabricated"),
                lambda record: record["parts"][0].update(locator="第2页"),
                lambda record: record.update(extract_version="forged")):
            changed = copy.deepcopy(material)
            modify(changed)
            with self.assertRaises(ContractError):
                parser.verified_material(changed, payload)

    def test_same_extension_rename_reuses_extraction_different_type_does_not(self):
        material, payload = self.material()
        parser.verified_material(material, payload)
        renamed = dict(material, name="renamed.PDF")
        with patch.object(parser.MaterialParser, "parse", side_effect=AssertionError("Same bytes reparsed")):
            parser.verified_material(renamed, payload)
        changed = dict(material, name="fake.docx")
        with self.assertRaises(ContractError):
            parser.verified_material(changed, payload)

    def test_retained_diagnostics_do_not_seed_private_cache(self):
        material, payload = self.material()
        material["parse_runtime"] = {"parser_worker_reaped": True, "wall_clock_deadline": True}
        with patch.object(parser.MaterialParser, "worker_target", staticmethod(stalled_parser)):
            token = parser.begin_verification({"id": "cold"}, lambda task: None, .6)
            started = time.monotonic()
            try:
                with self.assertRaisesRegex(ContractError, "未取得完整正文"):
                    parser.verified_material(material, payload)
            finally:
                parser.end_verification(token)
            self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(parser._verified_extractions)

    def test_cancelled_cached_verification_rejected_without_starting_worker(self):
        material, payload = self.material()
        parser.verified_material(material, payload)
        def stopped(task):
            raise ContractError("任务已停止")
        token = parser.begin_verification({"id": "cancelled"}, stopped, 5)
        try:
            with patch.object(parser.MaterialParser, "parse", side_effect=AssertionError("Worker after cancellation")):
                with self.assertRaisesRegex(ContractError, "任务已停止"):
                    parser.verified_material(material, payload)
        finally:
            parser.end_verification(token)

    def test_expired_cached_verification_rejected_and_context_restored(self):
        material, payload = self.material()
        parser.verified_material(material, payload)
        token = parser.begin_verification({"id": "expired"}, lambda task: None, 0)
        try:
            with self.assertRaisesRegex(ContractError, "时限"):
                parser.verified_material(material, payload)
        finally:
            parser.end_verification(token)
        self.assertIsNone(parser._verification_context.get())
        parser.verified_material(material, payload)

    def test_parallel_task_contexts_do_not_share_cancel_signal(self):
        material, payload = self.material()
        parser.verified_material(material, payload)
        barrier = threading.Barrier(2)
        results = []
        def run(ident):
            def active(task):
                self.assertEqual(task["id"], ident)
                if ident == "cancelled":
                    raise ContractError("任务已停止")
            token = parser.begin_verification({"id": ident}, active, 5)
            try:
                barrier.wait(timeout=3)
                parser.verified_material(material, payload)
                results.append((ident, "passed"))
            except ContractError:
                results.append((ident, "cancelled"))
            finally:
                parser.end_verification(token)
        workers = [threading.Thread(target=run, args=(ident,)) for ident in ("cancelled", "active")]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(set(results), {("cancelled", "cancelled"), ("active", "passed")})

    def test_cached_bytes_do_not_allow_cross_task_reading_or_rehashed_forgery(self):
        with tempfile.TemporaryDirectory(prefix="provenance-reader-", dir=ROOT / "result/acceptance") as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, isolate_materials=True)
            task = agent.create("核对原件内容", intent="原件内容核对")
            task = agent.add_material(task["id"], "original.pdf", synthetic_pdf())
            record = task["materials"][0]
            selection = [{"artifact_id": record["id"], "snapshot_hash": record["snapshot_hash"], "queries": ["Synthetic"],
                "parts": [], "question_ids": ["q1"], "reason": "合成材料定位"}]
            read(task, selection, binding(task))
            changed = copy.deepcopy(task)
            changed["materials"][0]["snapshot"]["parts"][0]["text"] = "forged Synthetic"
            changed["materials"][0]["snapshot_hash"] = fingerprint(changed["materials"][0]["snapshot"])
            selection[0]["snapshot_hash"] = changed["materials"][0]["snapshot_hash"]
            with self.assertRaises(ContractError):
                read(changed, selection, binding(changed))
            selection[0]["snapshot_hash"] = record["snapshot_hash"]
            foreign = copy.deepcopy(task)
            foreign["id"] = "another-task"
            with self.assertRaises(ContractError):
                read(foreign, selection, binding(foreign))

    def test_cache_is_bounded_and_keeps_no_original_text_or_approvals(self):
        for index in range(parser.MAX_VERIFIED_EXTRACTIONS + 3):
            payload = f"synthetic private body {index}".encode()
            material = extract_material("record.txt", payload)
            parser.verified_material(material, payload)
        self.assertEqual(len(parser._verified_extractions), parser.MAX_VERIFIED_EXTRACTIONS)
        self.assertTrue(all(isinstance(value, str) and len(value) == 64 for value in parser._verified_extractions.values()))
        self.assertNotIn("synthetic private body", str(parser._verified_extractions))

    def test_source_reader_checks_binary_bytes_again_after_cache_hit(self):
        material, payload = self.material()
        material["original_base64"] = base64.b64encode(payload).decode()
        record = {"kind": "material", "snapshot": material}
        _verify_original_text(record)
        material["original_base64"] = base64.b64encode(payload + b"modified").decode()
        with self.assertRaisesRegex(ContractError, "哈希"):
            _verify_original_text(record)

    def test_unknown_publication_time_uses_real_pdf_not_parent_parser_fixture(self):
        fixture = filing_fixtures.FilingAcquisitionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        payload = publication_pdf()
        self.assertIn("合成原件，发布2026-09-29。", extract_material("original.pdf", payload)["parts"][0]["text"])
        for ctime in (None, "", "2026-09-29 17:00:00"):
            with self.subTest(ctime=ctime):
                record = fixture.catalog({"errorcode": 0, "tables": [dict(fixture.row, ctime=ctime)]})
                self.assertTrue(record["catalog_entries"][0]["gaps"])
                self.assertTrue(record["catalog_entries"][0]["downloadable"])
                self.assertIsNone(record["catalog_entries"][0]["published_at"])
                fixture.transport(payload=payload)
                filing = fixture.collector.external(fixture.task, "ifind", "filing_original", fixture.selection(record), {})
                fixture.task["source_records"].append(filing)
                specification = {"id": "e1", "object": "600000.SH", "published_at": "2026-09-30", "period": fixture.row["reportDate"],
                    "nature": "已实现事实", "unit": "不适用", "record_locator": {"part": "第1页"}, "text": "Synthetic filing only.", "value": None}
                with self.assertRaises(ContractError):
                    candidate_evidence(fixture.task, filing, specification)
                specification["publication"] = {"basis_ref": {"artifact_id": filing["id"], "snapshot_hash": filing["hash"],
                    "kind": "directory", "field": "published_at"}}
                with self.assertRaises(ContractError):
                    candidate_evidence(fixture.task, filing, specification)
                specification.update(published_at="2026-09-29", text="合成原件，发布2026-09-29。")
                specification["publication"] = {"basis_ref": {"artifact_id": filing["id"], "snapshot_hash": filing["hash"],
                    "kind": "material", "part": "第1页", "quote": specification["text"]}}
                candidate = candidate_evidence(fixture.task, filing, specification)
                self.assertEqual(candidate["source_check"], "通过")
                self.assertEqual(candidate["applicability_check"], "待核验")


if __name__ == "__main__":
    unittest.main()
