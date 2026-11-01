"""Verify the exact scope of real positives and the retained ETF failure."""
import json
import unittest
from tests import ROOT


class TypedActualDataReviewTests(unittest.TestCase):
    def read(self,name):
        return json.loads((ROOT/'result/acceptance'/name).read_text())

    def test_all_seven_amounts_were_independently_matched_not_merely_downloaded(self):
        review=self.read('autonomous-data-phase-divibnb5/typed-data-review.json')
        self.assertEqual(review['status'],'requested_data_complete')
        self.assertEqual(len(review['requested_fields']),7)
        self.assertTrue(all(review['requested_fields'].values()))
        self.assertFalse(review['pending_requirements'] or review['gaps'] or review['rejected'])
        self.assertTrue(review['owned_runtime_closed'] and review['production_unchanged'] and review['source_unchanged'])
        self.assertFalse(review['all_asset_types_accepted'] or review['installed_app_accepted'] or review['report_accepted'])

    def test_cpi_month_and_both_values_remain_exact(self):
        review=self.read('typed-cross-asset-fb54pa3i/receipt.json')
        case=next(c for c in review['cases'] if c['name']=='cpi')
        self.assertEqual(case['status'],'requested_data_complete')
        self.assertEqual({(r['value'],r['unit'],r['period']) for r in case['benchmark']},
            {(0.9,'%','2026-09'),(0.2,'%','2026-09')})
        self.assertTrue(all(r['matches'] for r in case['benchmark']))

    def test_both_funds_have_both_disclosed_annual_fees_and_full_period(self):
        review=self.read('typed-cross-asset-pwem3uog/receipt.json');case=review['cases'][0]
        self.assertEqual(case['name'],'etf');self.assertEqual(case['status'],'requested_data_complete')
        self.assertEqual(len(case['benchmark']),4)
        self.assertTrue(all(r['matches'] and r['period']=='2026-01-01/2026-06-30' for r in case['benchmark']))
        self.assertTrue(case['owned_runtime_closed'] and case['production_unchanged'] and case['source_unchanged'])
        self.assertFalse(case['pending_requirements'] or case['gaps'] or case['rejected'])

    def test_previous_capacity_failure_is_not_rewritten_as_success(self):
        review=self.read('typed-cross-asset-fb54pa3i/receipt.json')
        case=next(c for c in review['cases'] if c['name']=='etf')
        self.assertEqual(case['status'],'requested_data_incomplete')
        self.assertEqual(case['evidence_count'],0);self.assertTrue(case['gaps'])
        self.assertFalse(any(r['matches'] for r in case['benchmark']))


if __name__=='__main__':unittest.main()
