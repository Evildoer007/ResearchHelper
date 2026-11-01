"""Financial method acquisition avoids raw-only detours; no source is approved."""
import copy
import importlib
import unittest
from unittest.mock import patch

Fixture = importlib.import_module('tests.test_initial_acquisition_navigation' if __package__
                                 else 'test_initial_acquisition_navigation').InitialAcquisitionNavigationTests


class FinancialAcquisitionFocusTests(unittest.TestCase):
    setUp = Fixture.setUp
    proposal = staticmethod(Fixture.proposal)
    respond = Fixture.respond
    acquire = Fixture.acquire
    run_navigation = Fixture.run_navigation
    def test_financial_methods_offer_originals_not_raw_only_financial_queries(self):
        self.task['questions'][0]['method_needs'] = ['net_profit_margin_v1']
        tools = [{'source':'ifind','operation':operation} for operation in
                 ('get_thscode','report_query','filing_original','smart_stock_picking','cmd_history_quotation')]
        self.decisions = [{'data_requests': [], 'stop_reason': 'synthetic decision only'}]
        with patch('researchhelper.acquisition.data_request_contract', return_value={'tools':tools}):
            self.run_navigation()
        payload = self.model.call_args.args[3]
        self.assertEqual(set(payload['navigation']['allowed_operations']), {'get_thscode','report_query','filing_original'})
        self.assertFalse(payload['data_request_contract']['acquisition_priority']['financial_approval'])
        self.assertEqual(self.task['ledger']['supplements'],0)

    def test_explicit_query_metadata_remains_available(self):
        self.task['questions'][0].update(method_needs=['net_profit_margin_v1'],
            evidence_need_scopes={'requested raw query':'query_provenance'})
        self.decisions = [{'data_requests': [], 'stop_reason': 'synthetic decision only'}]
        with patch('researchhelper.acquisition.data_request_contract', return_value={'tools':[
                {'source':'ifind','operation':'smart_stock_picking'}]}):
            self.run_navigation()
        self.assertIn('smart_stock_picking', self.model.call_args.args[3]['navigation']['allowed_operations'])


# Reusing the fixture must not run its inherited cohort twice in this module.
del Fixture
