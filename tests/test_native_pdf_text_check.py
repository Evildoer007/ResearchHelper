"""Read existing native PDFs; glyph equivalence cannot excuse lost content."""
import json
from pathlib import Path
import sys
import unittest
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.accept_native_hidden_print import pdf_text_key


class NativePDFTextCheck(unittest.TestCase):
    def test_font_radicals_and_wrapped_text_are_equivalent(self):
        self.assertEqual(pdf_text_key('核⼼结论\n⻛险⻅顶增⻓'), pdf_text_key('核心结论 风险见顶增长'))

    def test_missing_text_different_numbers_or_units_do_not_match(self):
        for actual in ('87%', '87%以上', '87%以下', '87个百分点', '87.0%'):
            with self.subTest(actual=actual):
                self.assertNotEqual(pdf_text_key(actual), pdf_text_key('87%以上，Q1扩张100%'))

    def test_frozen_native_pdf_retains_all_21_client_blocks(self):
        output = ROOT / 'result/acceptance/native-hidden-print-0bn8t8b9'
        blocks = json.loads((output / 'client-blocks.json').read_text())
        self.assertEqual(len(blocks), 21)
        for name in ('closed-reader', 'open-reader'):
            reader = PdfReader(output / (name + '.pdf'))
            self.assertEqual(len(reader.pages), 1)
            text = pdf_text_key(''.join(page.extract_text() or '' for page in reader.pages))
            for block in blocks:
                with self.subTest(output=name, block=block):
                    self.assertIn(pdf_text_key(block), text)


if __name__ == '__main__':
    unittest.main()
