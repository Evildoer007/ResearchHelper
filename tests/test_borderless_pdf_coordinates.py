"""Coordinate-only diagnostics do not approve financial values or rewrite PDFs."""
import copy
import json
import unittest

from tests import ROOT
from researchhelper.pdf_material import aligned_statement_part

COORDINATES = ROOT / 'result/acceptance/borderless-pdf-layout-ngx1acrf/coordinates.json'


class BorderlessCoordinatesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = COORDINATES.read_bytes()
        cls.pages = json.loads(cls.original)

    def part(self, page=None):
        page = self.pages[0] if page is None else page
        return aligned_statement_part(page['words'], page['width'], page['height'], page['page'])

    def test_actual_current_period_revenue_keeps_both_amounts_and_note_column(self):
        part = self.part()
        self.assertIsNotNone(part)
        row = next(line.split('\t') for line in part['text'].splitlines() if '一、营业收入' in line)
        self.assertEqual(row, ['一、营业收入', '', '49,691,932,907.10', '33,127,176,031.34'])
        self.assertEqual(part['pdf_table']['period_columns'], {'2':part['pdf_table']['period_caption']})
        self.assertEqual(part['pdf_table']['period_caption'].replace(' ', ''), '2026年1月1日至6月30日止期间')
        self.assertNotIn('column_bounds', part['pdf_table'])  # No fictitious physical rules.

    def test_actual_total_profit_is_not_relabelled_as_parent_attributable_profit(self):
        part = self.part(self.pages[1])
        rows = [line.split('\t') for line in part['text'].splitlines()]
        total = next(row for row in rows if row[0] == '五、净利润')
        parent = next(row for row in rows if row[0] == '归属于母公司股东的净利润')
        self.assertEqual(total[2], '23,887,522,068.03')
        self.assertEqual(parent[2], '23,343,231,403.68')

    def test_original_word_coordinates_and_strings_are_preserved(self):
        part = self.part()
        words = self.pages[0]['words']
        for cells in part['pdf_table']['word_cells'].values():
            for cell in cells:
                for word in cell:
                    self.assertIn(word, words)
        self.assertEqual(COORDINATES.read_bytes(), self.original)

    def test_duplicate_year_headers_are_ambiguous_not_resolved_by_order(self):
        page = copy.deepcopy(self.pages[0])
        for word in page['words']:
            if word['text'] == '2025':
                word['text'] = '2026'
        self.assertIsNone(self.part(page))

    def test_missing_period_caption_cannot_infer_half_year_from_report_name(self):
        page = copy.deepcopy(self.pages[0])
        page['words'] = [w for w in page['words'] if not 110 <= w['top'] <= 120]
        self.assertIsNone(self.part(page))

    def test_overlapping_numeric_words_are_not_merged_or_summed(self):
        page = copy.deepcopy(self.pages[0])
        page['words'].append(copy.deepcopy(next(w for w in page['words'] if w['text']=='49,691,932,907.10')))
        self.assertIsNone(self.part(page))

    def test_wrapped_parent_profit_prefix_is_not_lost(self):
        page = copy.deepcopy(self.pages[1])
        word = next(w for w in page['words'] if w['text']=='五、净利润')
        word['text'] = '净利润'
        prefix = dict(word, text='归属于母公司股东的', x1=word['x0']+90,
                      top=word['top']-16, bottom=word['bottom']-16)
        page['words'].append(prefix)
        part = self.part(page)
        self.assertIsNotNone(part)
        row = next(line.split('\t') for line in part['text'].splitlines() if '23,887,522,068.03' in line)
        self.assertEqual(row[0], '归属于母公司股东的 净利润')
