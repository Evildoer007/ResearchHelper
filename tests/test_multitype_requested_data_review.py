"""Receipt checks certify observations, not success of every data workflow."""
import hashlib
import json
import unittest

from tests import ROOT
from tests.review_multitype_requested_data import load_task


FOLDER = ROOT / 'result/acceptance/multitype-requested-data-flzmh5zu'


class MultitypeRequestedDataReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review = json.loads((FOLDER / 'independent-review.json').read_text())
        cls.cases = {c['name']: c for c in cls.review['cases']}

    def test_original_matrix_and_all_eight_closed_receipts_are_preserved(self):
        self.assertEqual(hashlib.sha256((FOLDER / 'receipt.json').read_bytes()).hexdigest(),
                         self.review['matrix_sha256'])
        self.assertEqual(len(self.cases), 8)
        for case in self.cases.values():
            self.assertTrue(case['production_unchanged'] and case['source_unchanged'] and case['owned_runtime_closed'])

    def test_explicit_responses_do_not_imply_autonomous_requirements_pass(self):
        explicit = json.loads((FOLDER / 'explicit-receipt.json').read_text())
        self.assertEqual(len(explicit['results']), 13)
        self.assertTrue(all(r['status'] == 'returned' for r in explicit['results']))
        self.assertFalse(self.review['all_asset_types_accepted'])
        failed = {n for n, c in self.cases.items() if c['status'] == 'requested_sample_incomplete'}
        self.assertEqual(failed, {'hk', 'etf', 'index', 'industry', 'chain_company', 'event'})

    def test_literal_disclosure_precision_is_not_rewritten_as_exact_yuan(self):
        case = self.cases['equity']
        self.assertEqual(case['status'], 'requested_sample_complete')
        self.assertEqual(len(case['fields']), 7)
        self.assertTrue(all(case['fields'].values()))
        self.assertTrue(all(p == 'literal_rounded_yi_yuan_summary' for p in case['disclosed_precision'].values()))

    def test_cpi_required_values_pass_despite_deferred_export_compaction(self):
        case = self.cases['macro']
        self.assertEqual(case['status'], 'requested_sample_complete')
        self.assertEqual(case['pending_requirements'], [])
        self.assertTrue(all(case['fields'].values()))

    def test_name_and_price_download_does_not_hide_wrong_daily_period_requirement(self):
        from pathlib import Path
        task = load_task(Path(self.cases['hk']['receipt']).parent)
        self.assertEqual(len(task['evidence']), 0)
        self.assertTrue(any(s['operation'] == 'cmd_history_quotation' for s in task['source_records']))
        needs = task['questions'][0]['evidence_need_constraints']
        daily = next(v for k, v in needs.items() if '每个交易日的收盘价' in k)
        self.assertEqual(daily['minimum_count'], 5)
        self.assertEqual(daily['period_kind'], 'interval')

    def test_no_analysis_report_or_installed_app_acceptance_is_inferred(self):
        self.assertFalse(self.review['report_accepted'] or self.review['installed_app_accepted'])
        self.assertEqual(self.review['model_calls'], 0)
        self.assertEqual(self.review['network_calls'], 0)


if __name__ == '__main__':
    unittest.main()
