"""Keep executable parameters separate from evidence context, without defaults."""

import copy
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, METHOD_REGISTRY, validate_calculation_parameters
from researchhelper.reasoning import CALCULATION_STRUCTURE_RULE, calculation_plan_contract
from tests import test_calculation_input_submission_offline as fixtures


class CalculationParameterSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CalculationInputSubmissionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = self.fixture.task
        self.parameters = dict(METHOD_REGISTRY['observed_price_return_v1']['parameters'],
            start_date='2026-09-29', end_date='2026-09-30')

    def test_flat_parameter_keys_exactly_match_validator_for_all_methods(self):
        contract = calculation_plan_contract(self.task)
        for method, rule in contract['parameter_submissions'].items():
            definition = METHOD_REGISTRY[method]
            self.assertEqual(rule['fixed_values'], definition['parameters'])
            self.assertEqual(rule['required_text_fields'], definition.get('context_parameters', []))
            self.assertEqual(set(rule['allowed_keys']), set(definition['parameters']) |
                set(definition.get('context_parameters', [])))
            self.assertFalse(rule['additional_fields'])
            self.assertFalse(set(rule['evidence_only_fields']) & set(rule['allowed_keys']))

    def test_price_kind_location_is_method_specific(self):
        rules = calculation_plan_contract(self.task)['parameter_submissions']
        self.assertIn('price_kind', rules['observed_price_return_v1']['evidence_only_fields'])
        self.assertNotIn('price_kind', rules['observed_price_return_v1']['allowed_keys'])
        self.assertEqual(rules['etf_nav_premium_v1']['fixed_values']['price_kind'], 'close')
        self.assertNotIn('price_kind', rules['etf_nav_premium_v1']['evidence_only_fields'])
        self.assertIn('不能放入parameters', CALCULATION_STRUCTURE_RULE)

    def test_exact_rejected_live_shape_is_not_silently_repaired(self):
        proposal = dict(self.parameters, price_kind='close')
        before = copy.deepcopy(proposal)
        with self.assertRaisesRegex(ContractError, 'calculation_context字段误放入parameters：price_kind'):
            validate_calculation_parameters('observed_price_return_v1', proposal)
        self.assertEqual(proposal, before)
        validate_calculation_parameters('observed_price_return_v1', self.parameters)

    def test_missing_and_mismatched_keys_are_specific_without_filling(self):
        proposal = dict(self.parameters, currency='CREDENTIAL_SENTINEL')
        del proposal['start_date']
        with self.assertRaises(ContractError) as caught:
            validate_calculation_parameters('observed_price_return_v1', proposal)
        text = str(caught.exception)
        self.assertIn('缺少字段：start_date', text)
        self.assertIn('固定口径不匹配：currency', text)
        self.assertNotIn('CREDENTIAL_SENTINEL', text)
        self.assertNotIn('start_date', proposal)

    def test_unregistered_keys_and_values_do_not_enter_diagnostics(self):
        proposal = dict(self.parameters, CREDENTIAL_SENTINEL='PRIVATE_VALUE')
        with self.assertRaises(ContractError) as caught:
            validate_calculation_parameters('observed_price_return_v1', proposal)
        text = str(caught.exception)
        self.assertIn('未登记字段数量：1', text)
        self.assertNotIn('CREDENTIAL_SENTINEL', text)
        self.assertNotIn('PRIVATE_VALUE', text)

    def test_descriptor_is_detached_readonly_and_has_no_execution_authority(self):
        before_task, before_registry = copy.deepcopy(self.task), copy.deepcopy(METHOD_REGISTRY)
        with patch('urllib.request.urlopen') as network:
            contract = calculation_plan_contract(self.task)
        contract['parameter_submissions']['observed_price_return_v1']['fixed_values']['currency'] = 'USD'
        contract['parameter_submissions']['observed_price_return_v1']['required_text_fields'].clear()
        self.assertEqual(self.task, before_task)
        self.assertEqual(METHOD_REGISTRY, before_registry)
        network.assert_not_called()
