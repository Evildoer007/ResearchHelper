"""New immutable acceptance fixtures; never changes prior tests or results."""
import copy
import unittest
from tests.review_requested_asset_data_matrix import review,broker_fields,MATRIX
from tests.test_asset_data_acceptance_review import load_retained_task


class RequestedAssetDataReviewTests(unittest.TestCase):
    def test_sources_originals_requests_and_ledger_are_bound(self):
        result=review()
        self.assertEqual(len(result['autonomous_samples']),3)
        self.assertFalse(result['all_asset_types_accepted'])
        self.assertFalse(result['report_accepted'])
        self.assertEqual(sum(row['raw_series_complete'] is True for row in result['explicit_samples']),6)

    def test_seven_broker_amounts_are_present_not_shareholder_percentages(self):
        result=review();broker=next(r for r in result['autonomous_samples'] if r['name']=='broker')
        self.assertTrue(broker['required_data_complete'])
        self.assertEqual(len(broker['required_fields']),7)
        self.assertFalse(broker['gaps'])

    def test_wrong_label_date_unit_or_subject_does_not_pass_on_number_alone(self):
        result=review();broker=next(r for r in result['autonomous_samples'] if r['name']=='broker')
        from pathlib import Path
        original=load_retained_task(Path(broker['receipt']).parent)
        for field,value in [('text','持股比例'),('unit','千元'),('object','300750.SZ'),('period','2025-01-01/2025-06-30')]:
            task=copy.deepcopy(original)
            for e in task['evidence']:e[field]=value
            self.assertFalse(any(broker_fields(task).values()))

    def test_index_and_industry_are_not_approved_by_a_successful_driver_status(self):
        result=review()
        for row in result['autonomous_samples']:
            if row['name']!='broker':self.assertFalse(row['required_data_complete'])
        topics=[r for r in result['explicit_samples'] if r['candidate_count'] is not None]
        self.assertEqual(len(topics),2)
        self.assertTrue(all(r['candidate_count']>0 and not r['complete_universe_approved'] for r in topics))
