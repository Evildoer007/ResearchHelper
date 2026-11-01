"""Conditional calculation fields at extraction entry, without source adoption."""

import copy
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, METHOD_REGISTRY, validate_calculation_context
from researchhelper.reasoning import (CALCULATION_STRUCTURE_RULE, PHASE_RULES,
                                     calculation_input_requirements, evidence_contract)
from tests import test_api_context_basis_templates_offline as fixtures


class CalculationInputSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.APIContextBasisTemplateTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task, self.agent = self.fixture.task, self.fixture.agent
        self.fixture.record('ifind')
        self.task['calculation_specs'] = [{
            'id': 'change', 'method': 'observed_price_return_v1',
            'inputs': {'start_price': 'start', 'end_price': 'end'}}]

    def test_actual_input_ids_have_required_context_and_exactly_one_basis_format(self):
        before = copy.deepcopy(self.task)
        contract = calculation_input_requirements(self.task)
        self.assertEqual(contract['binding_version'], 2)
        self.assertEqual(set(contract['proposal_requirements']), {'start', 'end'})
        for value in contract['proposal_requirements'].values():
            self.assertEqual(value['required_fields'], ['calculation_context'])
            self.assertEqual(value['exactly_one_basis_field'], ['calculation_context_basis', 'api_context_reference'])
            self.assertEqual(set(value['required_context_fields']), set(METHOD_REGISTRY['observed_price_return_v1']['binding_context']))
        contract['proposal_requirements']['start']['required_context_fields'].clear()
        self.assertEqual(self.task, before)

    def test_no_plan_does_not_impose_context_on_unrelated_facts(self):
        self.task['calculation_specs'] = []
        self.assertEqual(calculation_input_requirements(self.task)['proposal_requirements'], {})

    def test_valuation_without_binding_context_keeps_existing_assumption_contract(self):
        self.task['calculation_specs'] = [{'id': 'pv', 'method': 'residual_present_value_v1',
                                          'inputs': {'receipt': 'cash'}}]
        self.assertEqual(calculation_input_requirements(self.task)['proposal_requirements'], {})

    def test_multiple_methods_union_required_fields_without_inventing_values(self):
        self.task['calculation_specs'].append({'id': 'nav', 'method': 'etf_nav_premium_v1',
            'inputs': {'price': 'start', 'nav': 'nav_value'}})
        requirements = calculation_input_requirements(self.task)['proposal_requirements']['start']
        expected = set(METHOD_REGISTRY['observed_price_return_v1']['binding_context']) | set(METHOD_REGISTRY['etf_nav_premium_v1']['binding_context'])
        self.assertEqual(set(requirements['required_context_fields']), expected)
        self.assertNotIn('calculation_context', requirements)

    def test_actual_model_payload_has_same_conditional_contract_as_host_grammar(self):
        self.task['host_result'] = {}
        with patch.object(self.agent, '_reasoning_available', return_value=True), \
                patch.object(self.agent, '_save_model_checkpoint'), \
                patch('researchhelper.engine.model_object', return_value=({'evidence_specs': []}, {})) as model:
            self.agent._extract_model_sources(self.task)
        payload = model.call_args.args[3]
        expected = evidence_contract(self.task)['calculation_input_requirements']['proposal_requirements']
        self.assertEqual(payload['calculation_input_targets'], expected)
        self.assertEqual(payload['evidence_contract']['calculation_input_requirements']['proposal_requirements'], expected)
        self.assertFalse(self.task['evidence'])
        self.assertEqual(self.task['ledger']['used']['model_calls'], 0)

    def test_phase_rule_does_not_treat_existing_registered_methods_as_new_only(self):
        self.assertIn('本轮已登记计算输入', PHASE_RULES['extract'])
        self.assertNotIn('新计算方法所需口径', PHASE_RULES['extract'])

    def test_plan_guidance_preserves_unknown_action_limit_instead_of_inventing_none(self):
        self.assertIn('允许公司行为状态unknown', CALCULATION_STRUCTURE_RULE)
        self.assertIn('用户未要求核验公司行为或总收益时', CALCULATION_STRUCTURE_RULE)
        self.assertTrue(METHOD_REGISTRY['observed_price_return_v1']['limitations'].startswith('仅未复权价格变化'))

    def test_missing_input_diagnostic_identifies_only_missing_slots(self):
        parameters = {'currency': 'CNY', 'start_date': '2026-09-29', 'end_date': '2026-09-30',
                      'price_basis': 'unadjusted', 'unit': '元', 'return_kind': 'price_change'}
        bindings = {'start_price': {'object': '510300.SH', 'unit': '元', 'period': '2026-09-29', 'calculation_context': {}},
                    'end_price': {'object': '510300.SH', 'unit': '元', 'period': '2026-09-30'}}
        with self.assertRaisesRegex(ContractError, '终点价格') as caught:
            validate_calculation_context('observed_price_return_v1', bindings, parameters)
        self.assertNotIn('起点价格', str(caught.exception))
