"""Exercise autonomous readiness, not merely a hand-invoked calculation."""
import copy
import unittest
try:
    from . import test_fixed_financial_runtime_chain as fixture
except ImportError:
    import test_fixed_financial_runtime_chain as fixture
from researchhelper.reasoning import extractable_source_views, evidence_contract
from researchhelper.acquisition import initial_acquisition_coverage, _numeric_api_source_paths
from researchhelper.contracts import fingerprint
from researchhelper.evidence import api_calculation_context_contract


class FixedFinancialAutonomyTests(unittest.TestCase):
    setUp = fixture.FixedFinancialRuntimeChainTests.setUp
    collect = fixture.FixedFinancialRuntimeChainTests.collect
    replay = fixture.FixedFinancialRuntimeChainTests.replay

    def test_registered_response_enters_the_actual_extraction_queue(self):
        views = extractable_source_views(self.task)
        self.assertEqual([view['id'] for view in views], [self.source['id']])
        source = evidence_contract(self.task,views)['sources'][0]
        self.assertEqual(source['calculation_context_contract']['status'], 'registered_constraints')
        self.assertEqual(len(source['cell_choices']), 4)
        self.assertIn('current_availability_reference', source)

    def test_method_readiness_uses_api_without_an_original_obligation(self):
        self.task['method_source_coverage_version'] = 1
        self.source['question_ids'] = ['q1']
        coverage = initial_acquisition_coverage(self.task)
        self.assertTrue(coverage['ready'], coverage)
        self.assertEqual(coverage['pending_method_inputs'], [])
        self.assertTrue(all(row['api_source_ids']==[self.source['id']] for row in coverage['method_inputs']))
        self.assertEqual(coverage['pending_original_question_ids'], [])

    def test_unregistered_import_does_not_enter_numeric_extraction(self):
        self.source.pop('fixed_financial_catalog_digest')
        self.assertEqual(extractable_source_views(self.task), [])

    def test_partial_single_field_keeps_its_metric_source_path(self):
        record = copy.deepcopy(self.source)
        record['snapshot']['tables'][0]['table'] = {
            'ths_revenue_stock': record['snapshot']['tables'][0]['table']['ths_revenue_stock']}
        record['hash'] = record['snapshot_hash'] = fingerprint(record['snapshot'])
        self.assertEqual(api_calculation_context_contract(record)['registered_numeric_fields'], ['ths_revenue_stock'])
        paths = _numeric_api_source_paths(record)
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0]['tag_value'], 'revenue')

    def test_old_policy_does_not_gain_numeric_readiness(self):
        self.task.pop('fixed_financial_catalog_version')
        self.assertEqual(extractable_source_views(self.task), [])
