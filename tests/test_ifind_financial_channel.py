"""Negative controls for the new retained iFinD financial-data diagnostic."""
import copy
import json
import unittest
from unittest.mock import patch

from tests import ROOT
from tests.review_ifind_financial_channel import inspect_response


class IFindFinancialChannelTests(unittest.TestCase):
    def setUp(self):
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline controls only'))
        guard.start(); self.addCleanup(guard.stop)
        folder = ROOT/'result/acceptance/ifind-financial-basic-20261023'
        self.request = json.loads((folder/'request.json').read_text())
        self.response = json.loads((folder/'raw-response.json').read_text())

    def test_complete_actual_batch_is_retained_without_unit_approval(self):
        before = copy.deepcopy(self.response)
        result = inspect_response(self.request, self.response)
        self.assertEqual((result['codes'], result['fields'], result['numeric_cells']), (48, 2, 96))
        self.assertFalse(result['units_verified'] or result['currency_verified'] or result['financial_values_approved'])
        self.assertEqual(self.response, before)

    def test_row_order_does_not_bind_the_wrong_company(self):
        expected = inspect_response(self.request, self.response)
        self.response['tables'].reverse()
        self.assertEqual(inspect_response(self.request, self.response), expected)

    def test_missing_cell_stays_missing_not_zero(self):
        self.response['tables'][0]['table']['ths_revenue_stock'][0] = None
        result = inspect_response(self.request, self.response)
        self.assertEqual((result['numeric_cells'], result['missing_cells']), (95, 1))
        self.assertIsNone(result['values'][self.response['tables'][0]['thscode']]['ths_revenue_stock'])

    def test_report_period_cannot_be_replaced_by_latest_default(self):
        self.response['inputParams']['params'][0]['params'][1]['value'] = '8'
        with self.assertRaises(ValueError): inspect_response(self.request, self.response)

    def test_parent_report_cannot_replace_consolidated_report(self):
        self.response['inputParams']['params'][0]['params'][2]['value'] = '2'
        with self.assertRaises(ValueError): inspect_response(self.request, self.response)

    def test_duplicate_company_cannot_substitute_for_missing_company(self):
        self.response['tables'][-1] = copy.deepcopy(self.response['tables'][0])
        with self.assertRaises(ValueError): inspect_response(self.request, self.response)

    def test_foreign_code_is_not_adopted(self):
        self.response['tables'][0]['thscode'] = 'AAPL.US'
        with self.assertRaises(ValueError): inspect_response(self.request, self.response)

    def test_misaligned_or_extra_numeric_cells_are_rejected(self):
        self.response['tables'][0]['table']['ths_revenue_stock'].append(1)
        with self.assertRaises(ValueError): inspect_response(self.request, self.response)

    def test_boolean_or_nonfinite_values_are_not_numeric_evidence(self):
        for value in (True, float('nan'), float('inf')):
            self.response['tables'][0]['table']['ths_revenue_stock'][0] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                inspect_response(self.request, self.response)

    def test_business_error_is_not_an_empty_success(self):
        self.response['errorcode'] = -1
        with self.assertRaises(ValueError): inspect_response(self.request, self.response)
