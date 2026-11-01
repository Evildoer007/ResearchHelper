"""The calculator's arithmetic and context guards, isolated from adoption.

Inputs are original data-module cells. These tests intentionally do not mint
evidence receipts, call the Analyzer, resume a task or publish a research result.
"""
import copy
import importlib
from decimal import Decimal
import json
import socket
import unittest
from unittest.mock import patch

from researchhelper.contracts import (ContractError, MethodNotApplicable,
    FINANCIAL_INPUT_METRICS, calculate, validate_calculation_context)
from researchhelper.financial_data import core_financial_values
SOURCE = importlib.import_module((__package__+'.' if __package__ else '')+'test_financial_data_module').SOURCE


class SingleSecurityCalculationModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = SOURCE.read_bytes()
        task = json.loads(cls.raw)
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('No external I/O')):
            cls.data = core_financial_values(task, '002594.SZ', ['20260630', '20250630'])
        assert cls.data['status'] == 'decoded'

    @classmethod
    def tearDownClass(cls):
        assert SOURCE.read_bytes() == cls.raw

    def inputs(self, method, period):
        selected = {row['metric']: row for row in self.data['values'] if row['period'] == period}
        bindings = {}
        for name, metric in FINANCIAL_INPUT_METRICS[method].items():
            row = selected[metric]
            context = dict(row['context']['context'], entity_id=row['issuer'],
                           accounting_standard=row['accounting_standard'])
            amount = Decimal(row['value'])
            self.assertEqual(amount, amount.to_integral_value())
            bindings[name] = dict(object=row['issuer'], period=row['period_literal'],
                unit=row['unit'], value=int(amount), calculation_context=context)
        row = next(iter(bindings.values()))
        params = {key:row['calculation_context'][key]
                  for key in ('currency', 'period', 'accounting_scope', 'accounting_standard')}
        params['amount_unit'] = row['unit']
        return bindings, params

    def test_six_ratios_match_independent_decimal_arithmetic(self):
        for period in ('20260630', '20250630'):
            for method in FINANCIAL_INPUT_METRICS:
                bindings, params = self.inputs(method, period)
                validate_calculation_context(method, bindings, params)
                values = {name:row['value'] for name, row in bindings.items()}
                d = {name:Decimal(number) for name, number in values.items()}
                if method == 'gross_margin_v1': expected = (d['revenue']-d['cost'])/d['revenue']*100
                elif method == 'net_profit_margin_v1': expected = d['net_profit']/d['revenue']*100
                else: expected = d['operating_cash_flow']/d['net_profit']*100
                output = calculate(method, values, params)
                self.assertLessEqual(abs(Decimal(str(output['value']))-expected), Decimal(str(output['tolerance'])))
                self.assertEqual(output['unit'], '%')

    def test_mixed_object_period_currency_unit_and_basis_are_rejected(self):
        method = 'gross_margin_v1'
        for field, value in (('object', '600519.SH'), ('period', '2025年上半年'), ('unit', '元')):
            bindings, params = self.inputs(method, '20260630')
            bindings['cost'][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_calculation_context(method, bindings, params)
        for field, value in (('currency', 'USD'), ('entity_id', '600519.SH'),
                             ('accounting_scope', 'parent'), ('accounting_standard', 'IFRS'),
                             ('metric', 'parent_net_profit')):
            bindings, params = self.inputs(method, '20260630')
            bindings['cost']['calculation_context'][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_calculation_context(method, bindings, params)

    def test_missing_input_or_missing_context_is_not_repaired(self):
        method = 'gross_margin_v1'
        for missing in ('cost', 'context'):
            bindings, params = self.inputs(method, '20260630')
            if missing == 'cost': del bindings['cost']
            else: del bindings['cost']['calculation_context']
            with self.assertRaises(ContractError): validate_calculation_context(method, bindings, params)

    def test_zero_or_negative_profit_does_not_publish_coverage_ratio(self):
        method = 'operating_cash_profit_ratio_v1'
        bindings, params = self.inputs(method, '20260630')
        values = {name:row['value'] for name, row in bindings.items()}
        for profit in (0, -1):
            with self.assertRaises(MethodNotApplicable): calculate(method, dict(values, net_profit=profit), params)

    def test_document_only_excerpts_do_not_enter_financial_calculation(self):
        bindings, params = self.inputs('gross_margin_v1', '20260630')
        bindings['cost']['usage_scope'] = 'document_content'
        with self.assertRaises(ContractError): validate_calculation_context('gross_margin_v1', bindings, params)

    def test_calculator_does_not_mutate_its_inputs_or_approve_data(self):
        before = copy.deepcopy(self.data)
        bindings, params = self.inputs('gross_margin_v1', '20260630')
        original = copy.deepcopy((bindings, params))
        calculate('gross_margin_v1', {name:row['value'] for name,row in bindings.items()}, params)
        self.assertEqual((bindings, params), original)
        self.assertEqual(self.data, before)
        self.assertFalse(self.data['financial_adoption'])


if __name__ == '__main__':
    unittest.main()
