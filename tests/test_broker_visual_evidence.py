"""Numerical provenance of the five-pass visual enrichment, independent of layout."""
import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'broker_visual_evidence', ROOT / 'modules/reportcomposer/examples/broker_report.py')
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


class VisualEvidenceTests(unittest.TestCase):
    def test_derivations_preserve_original_values_and_units(self):
        data = REPORT.load_case()
        original = copy.deepcopy(data)
        facts = REPORT.visual_evidence(data)
        self.assertEqual(data, original)
        self.assertEqual(facts['sector']['sample_count'], 6)
        self.assertEqual(sum(facts['sector']['quadrants'].values()), 6)
        self.assertEqual(facts['sector']['quadrants']['down_up'], 4)
        self.assertEqual(facts['earnings']['mean'], 54)
        self.assertEqual(facts['earnings']['deviations_pp'], [15, -2, -13])
        self.assertEqual(facts['valuation']['distance_percentile_points'], 23)
        self.assertEqual(facts['payout']['prior_inferred'], 29)
        self.assertTrue(facts['payout']['prior_is_inferred'])
        self.assertEqual(facts['yields'], dict(low_spread_pp=1.9, high_spread_pp=2.2))
        units = {row['id']: row['unit'] for row in facts['methods']}
        self.assertEqual(units['distance_to_percentile_scale_midpoint'], 'percentile_points')
        self.assertEqual(units['interval_difference'], 'percentage_points')
        self.assertEqual(units['subtract_reported_change'], 'percent_inferred')

    def test_derivations_use_input_not_display_constants(self):
        data = REPORT.load_case()
        for row, value in zip(data['heads'], [10, 40, 70]):
            row['revenue'] = value
        data['valuation']['percentile'] = 46
        data['dividends'].update(payout=40, payout_change_pp=15, a=[1, 2], h=[4, 5])
        facts = REPORT.visual_evidence(data)
        self.assertEqual(facts['earnings']['mean'], 40)
        self.assertEqual(facts['earnings']['deviations_pp'], [-30, 0, 30])
        self.assertEqual(facts['valuation']['distance_percentile_points'], 4)
        self.assertEqual(facts['payout']['prior_inferred'], 25)
        self.assertEqual(facts['yields'], dict(low_spread_pp=2, high_spread_pp=4))

    def test_method_qualifications_are_retained_in_source_inspection(self):
        data = REPORT.load_case()
        evidence = REPORT.evidence(data, REPORT.figures(data))
        for limitation in ['不是全行业平均', '50只是刻度参照', '推算值不是新增独立观测',
                           '不是同一公司A/H股息差', '每点表示1个百分点']:
            self.assertIn(limitation, evidence)
        paper = REPORT.render(data)
        main = paper.split('<main>', 1)[1].split('</main>', 1)[0]
        self.assertNotIn('推算值不是新增独立观测', main)
        self.assertNotIn('<details', main)

    def test_output_embeds_shared_renderer_and_original_source_dataset(self):
        data = REPORT.load_case()
        renderer = ROOT / 'modules/visualrenderer/assets/evidence-charts.js'
        paper = REPORT.render(data)
        self.assertIn(renderer.read_text(), paper)
        self.assertEqual(paper.count('global.RHEvidenceCharts=Object.freeze({mount})'), 1)
        self.assertIn(json.dumps(data, ensure_ascii=False, separators=(',', ':')), paper)
        self.assertNotIn('src="https://', paper)


if __name__ == '__main__':
    unittest.main()
