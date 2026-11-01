"""Preserve every named valuation sample from the supplied one-page PDF.

This is a new regression suite; previous report tests and frozen exports remain
unchanged. Sample names are transcribed literally, not silently corrected.
"""
import copy
import json
from pathlib import Path
import unittest

from researchhelper.report_examples import example_figures, render_example

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = [
    [('阳光电源', 34.9), ('宁德时代', 37.8), ('蓝色光标', 28.8),
     ('时代电气', 55.8), ('隆基绿能', 29.3), ('华大科技', 20.5)],
    [('新易盛', 19.9), ('中际旭创', 17.4), ('胜宏科技', 16.0),
     ('源杰科技', 15.8), ('天孚通信', 14.3), ('沪电股份', 14.4)],
]


class FullSampleChartTests(unittest.TestCase):
    def setUp(self):
        self.item = json.loads((ROOT/'products/app/data/report_catalog.json').read_text())['sources']['semi']

    def test_all_twelve_samples_and_original_bounds_are_preserved(self):
        before = copy.deepcopy(self.item)
        figure = example_figures('semi', self.item)[-1]
        self.assertEqual([list(zip(group['categories'], group['values']))
                          for group in figure['samples']], EXPECTED)
        for index, group in enumerate(figure['samples']):
            self.assertEqual(min(group['values']), figure['series'][0]['data'][index])
            self.assertEqual(max(group['values']), figure['series'][1]['data'][index])
        self.assertEqual(before, self.item)

    def test_literal_names_and_all_numbers_have_accessible_data_rows(self):
        paper = render_example('semi', ROOT)
        self.assertIn('class="chart-intervals chart-samples"', paper)
        self.assertIn('不同样本', paper)
        for group in EXPECTED:
            for name, value in group:
                self.assertIn(f'<th scope="row">{name}</th>', paper)
                self.assertIn(f'<td class="num">{value:.1f}</td>', paper)
        self.assertIn('sampleComparisonOption', paper)

    def test_interval_only_compatibility_and_other_reports_do_not_change(self):
        legacy = copy.deepcopy(self.item)
        legacy.pop('valuation_samples', None)
        figure = example_figures('semi', legacy)[-1]
        self.assertEqual(figure['presentation'], 'intervals')
        self.assertNotIn('samples', figure)
        for key, item in json.loads((ROOT/'products/app/data/report_catalog.json').read_text())['sources'].items():
            if key != 'semi':
                self.assertTrue(all('samples' not in figure for figure in example_figures(key, item)))

    def test_mismatched_bounds_or_non_numeric_samples_are_rejected(self):
        changed = copy.deepcopy(self.item)
        changed['valuation_samples'][0]['values'][0] = 90
        with self.assertRaises(ValueError):
            example_figures('semi', changed)
        changed = copy.deepcopy(self.item)
        changed['valuation_samples'][1]['values'][0] = '19.9'
        with self.assertRaises(ValueError):
            example_figures('semi', changed)


if __name__ == '__main__':
    unittest.main()
