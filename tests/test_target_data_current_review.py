"""Regression checks for the current matrix, including its genuine failures.

Passing these tests means the receipt describes the observations correctly;
it does not mean every research data requirement passed.
"""
import hashlib
import json
from pathlib import Path
import unittest
from tests import ROOT

FOLDER=ROOT/'result/acceptance/target-data-current-5kwdf1us'


class TargetDataCurrentReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receipt=json.loads((FOLDER/'receipt.json').read_text())
        cls.explicit=json.loads((FOLDER/'explicit-receipt.json').read_text())
        cls.cases={row['name']:row for row in cls.receipt['fresh_cases']}

    def test_current_explicit_samples_have_all_five_dates_and_no_missing_cells(self):
        rows=[r for r in self.explicit['results'] if 'tables' in r.get('inspection',{})]
        self.assertEqual(len(rows),6)
        expected=['2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16']
        for row in rows:
            self.assertTrue(row['inspection']['usable'])
            for table in row['inspection']['tables']:
                self.assertEqual(table['dates'],expected)
                self.assertEqual(table['missing_cells'],0)
        self.assertEqual(len(self.explicit['results']),13)
        self.assertTrue(all(r['status']=='returned' for r in self.explicit['results']))

    def test_candidates_and_fifty_members_are_not_full_universe_or_weights_approval(self):
        rows=self.explicit['results']
        members=next(r for r in rows if r['label']=='上证50成分名单')['inspection']['contract']
        self.assertEqual(members['observed_rows'],50)
        self.assertFalse(members['complete_set_verified'] or members['weights_available'])
        candidates=[r for r in rows if r['label'] in ('证券行业股票','算力产业链股票')]
        self.assertEqual(len(candidates),2)
        for row in candidates:
            self.assertGreater(row['inspection']['contract']['returned_unique_codes'],0)
            self.assertFalse(row['inspection']['complete_universe_approved'])

    def test_wrong_shareholding_percentages_do_not_cover_any_requested_amount(self):
        case=self.cases['broker']
        self.assertEqual(case['status'],'data_phase_sample_passed')
        self.assertFalse(case['required_data_complete'])
        self.assertEqual(len(case['required_fields']),7)
        self.assertFalse(any(case['required_fields'].values()))
        numeric=[e for e in case['requested_fields'] if e['value'] is not None]
        self.assertEqual(len(numeric),7)
        self.assertTrue(all(e['value']==100 and e['unit']=='%' for e in numeric))

    def test_missing_index_channel_and_incomplete_industry_remain_failures(self):
        for name in ('index_weights','industry'):
            case=self.cases[name]
            self.assertFalse(case['required_data_complete'])
            self.assertEqual(case['evidence_count'],0)
            self.assertTrue(case['gaps'])
        self.assertEqual(self.cases['index_weights']['used']['data_requests'],0)
        self.assertEqual(self.cases['industry']['bounded_probe']['data_requests'],24)

    def test_every_observed_run_is_closed_and_its_original_receipt_is_unchanged(self):
        self.assertEqual(self.receipt['status'],'closed_with_per_scope_results')
        for row in self.receipt['fresh_cases']+self.receipt['retained_cases']:
            path=Path(row['receipt'])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),row['receipt_sha256'])
            original=json.loads(path.read_text())
            self.assertTrue(original['owned_runtime_closed'])
            self.assertTrue(original['production_unchanged'] and original['source_unchanged'])

    def test_retained_reviews_do_not_become_fresh_or_installed_app_acceptance(self):
        self.assertEqual(len(self.receipt['retained_cases']),7)
        self.assertTrue(all(not r['fresh'] for r in self.receipt['retained_cases']))
        self.assertFalse(self.receipt['all_types_complete'])
        self.assertFalse(self.receipt['installed_app_accepted'] or self.receipt['report_accepted'])


if __name__=='__main__':unittest.main()
