from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from core.docs import (_read_pasted_material, date_from_text, read_pages, source_label,
                       usable_document_date)


class PastedMaterialTests(unittest.TestCase):
    def test_material_header_keeps_explicit_publication_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "material.txt"
            path.write_text(
                "来源：公司公告\n资料日期：2026-09-15\n录入时间：2026-09-15T10:00:00+08:00\n---\n原文。",
                encoding="utf-8",
            )
            self.assertEqual(_read_pasted_material(path), ("公司公告", "2026-09-15", "原文。"))

    def test_document_date_gate_rejects_unknown_future_and_stale(self) -> None:
        self.assertFalse(usable_document_date("")[0])
        future = (date.today() + timedelta(days=1)).isoformat()
        stale = (date.today() - timedelta(days=91)).isoformat()
        self.assertFalse(usable_document_date(future)[0])
        self.assertFalse(usable_document_date(stale)[0])
        self.assertTrue(usable_document_date(date.today().isoformat())[0])

    def test_date_parser_accepts_common_source_formats(self) -> None:
        self.assertEqual(date_from_text("2026年9月5日发布"), "2026-09-05")
        self.assertEqual(date_from_text("报告_20260905_v2"), "2026-09-05")

    def test_pasted_text_keeps_manual_source_out_of_extractable_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "补充文字-20260902-abc123.txt"
            path.write_text(
                "来源：公司公告《2026 年半年报》p4\n"
                "录入时间：2026-09-02T10:00:00+08:00\n"
                "---\n"
                "公司披露上半年收入同比增长 20%。\n",
                encoding="utf-8",
            )
            self.assertEqual(source_label(path), "公司公告《2026 年半年报》p4")
            self.assertEqual(read_pages(path), ["公司披露上半年收入同比增长 20%。"])

    def test_plain_text_falls_back_to_filename_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "20260902-机构-主题.txt"
            path.write_text("一段普通文字材料。", encoding="utf-8")
            self.assertIn("机构", source_label(path))
            self.assertEqual(read_pages(path), ["一段普通文字材料。"])
