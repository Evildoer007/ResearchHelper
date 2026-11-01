"""Downloading prose is not sufficient evidence for a numeric method."""
import unittest

from tests.accept_autonomous_data_phase import method_input_inventory


class DataPhaseMethodGapTests(unittest.TestCase):
    def task(self, evidence):
        return dict(id='sample', version=1,
            questions=[dict(id='q1', method_needs=['net_profit_margin_v1'])], evidence=evidence)

    def test_no_numeric_input_stays_pending(self):
        result = method_input_inventory(self.task([dict(text='原文中有营业收入', unit='不适用')]))
        self.assertFalse(result[0]['units_available'])
        self.assertEqual(result[0]['numeric_candidates'], 0)

    def test_unchecked_api_values_do_not_fill_inputs(self):
        rows = [dict(value=100, unit='元', question_ids=['q1']) for _ in range(2)]
        self.assertFalse(method_input_inventory(self.task(rows))[0]['units_available'])

    def test_wrong_question_values_do_not_fill_inputs(self):
        rows = [dict(value=100, unit='元', question_ids=['q2'],
            source_check='通过', applicability_check='通过') for _ in range(2)]
        self.assertFalse(method_input_inventory(self.task(rows))[0]['units_available'])

    def test_units_available_is_explicitly_not_calculation_approval(self):
        rows = [dict(value=100, unit='元', question_ids=['q1'],
            source_check='通过', applicability_check='通过') for _ in range(2)]
        result = method_input_inventory(self.task(rows))[0]
        self.assertTrue(result['units_available'])
        self.assertFalse(result['compatible_financial_roles_available'])
        self.assertFalse(result['financial_calculation_approved'])

    def financial(self, metric, scope='consolidated'):
        return dict(value=100,unit='元',question_ids=['q1'],source_check='通过',applicability_check='通过',
            calculation_context=dict(entity_id='示例企业',currency='CNY',period='示例半年期间',
                                     accounting_scope=scope,accounting_standard='CAS',metric=metric))

    def test_duplicate_revenue_cannot_fill_net_profit_role(self):
        rows = [self.financial('revenue'),self.financial('revenue')]
        self.assertFalse(method_input_inventory(self.task(rows))[0]['compatible_financial_roles_available'])

    def test_parent_profit_cannot_fill_consolidated_revenue_group(self):
        rows = [self.financial('revenue'),self.financial('net_profit','parent')]
        self.assertFalse(method_input_inventory(self.task(rows))[0]['compatible_financial_roles_available'])

    def test_matching_roles_and_context_remain_candidates_not_calculation_approval(self):
        rows = [self.financial('revenue'),self.financial('net_profit')]
        result = method_input_inventory(self.task(rows))[0]
        self.assertTrue(result['compatible_financial_roles_available'])
        self.assertFalse(result['financial_calculation_approved'])
