"""Independent six-case extraction checks; keep prior failures unchanged."""
import json
import os
from pathlib import Path
import sys
import unittest
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.native_pdf_text import pdf_text_key


class NativePDFCollectionText(unittest.TestCase):
    def test_only_observed_glyph_variants_are_equivalent(self):
        self.assertEqual(pdf_text_key('业务部⻔、⻝品、⺟公司、⻰头'),
                         pdf_text_key('业务部门、食品、母公司、龙头'))

    def test_financial_content_cannot_be_normalized_away(self):
        expected = pdf_text_key('2026年7月上涨15.39%，母公司营业收入30亿元，未构成投资建议。')
        for text in ('2026年7月下跌15.39%，母公司营业收入30亿元，未构成投资建议。',
                     '2026年7月上涨15.3%，母公司营业收入30亿元，未构成投资建议。',
                     '2026年7月上涨15.39%，母公司营业收入30亿港元，未构成投资建议。',
                     '2026年7月上涨15.39%，母公司营业收入30亿元。'):
            with self.subTest(text=text):
                self.assertNotEqual(pdf_text_key(text), expected)

    def test_all_client_blocks_in_both_variants_of_six_papers(self):
        output = Path(os.environ.get('RESEARCHHELPER_PRINT_COLLECTION',
            str(ROOT / 'result/acceptance/native-print-collection-qxo5bws5'))).resolve()
        self.assertTrue(output.is_relative_to(ROOT / 'result/acceptance'))
        receipt = json.loads((output / 'receipt.json').read_text())
        self.assertEqual(set(receipt['cases']), {'semi', 'ipo', 'broker', 'consumer', 'medicine', 'internet'})
        for key in receipt['cases']:
            blocks = json.loads((output / key / 'client-blocks.json').read_text())
            for variant in ('closed-reader', 'open-reader'):
                pdf = PdfReader(output / key / (variant + '.pdf'))
                self.assertEqual(len(pdf.pages), 1)
                text = pdf_text_key(''.join(page.extract_text() or '' for page in pdf.pages))
                for block in blocks:
                    with self.subTest(case=key, variant=variant, block=block):
                        self.assertIn(pdf_text_key(block), text)


if __name__ == '__main__':
    unittest.main()
