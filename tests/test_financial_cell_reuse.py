"""Shared source cells retain their identity across distinct question scopes."""
import copy
import importlib
import unittest
from researchhelper.contracts import ContractError
from researchhelper.financial_sources import original_date_anchors

Fixture=importlib.import_module('tests.test_financial_value_reference' if __package__
                              else 'test_financial_value_reference').FinancialValueReferenceTests


class FinancialCellReuseTests(unittest.TestCase):
    setUpClass=classmethod(Fixture.setUpClass.__func__)
    tearDownClass=classmethod(Fixture.tearDownClass.__func__)
    setUp=Fixture.setUp
    candidates_for=Fixture.candidates_for
    compact=Fixture.compact
    expand=Fixture.expand

    def test_disjoint_questions_reuse_same_number_not_an_independent_source(self):
        specs=self.compact();shared=copy.deepcopy(specs[0]);shared['id']='reused_amount'
        shared['question_ids']=['q_other'];shared['review']['question_ids']=['q_other']
        specs.append(shared)
        expanded=self.expand(specs)
        self.assertEqual(expanded[0]['artifact_id'],expanded[-1]['artifact_id'])
        self.assertEqual(expanded[0]['record_locator'],expanded[-1]['record_locator'])
        self.assertEqual(expanded[0]['value'],expanded[-1]['value'])
        self.assertEqual(expanded[-1]['question_ids'],['q_other'])

    def test_overlapping_questions_remain_rejected_even_with_a_new_id(self):
        specs=self.compact();shared=copy.deepcopy(specs[0]);shared['id']='reused_amount';specs.append(shared)
        with self.assertRaises(ContractError):self.expand(specs)

    def test_different_context_cannot_reuse_the_same_amount(self):
        specs=self.compact();shared=copy.deepcopy(specs[0]);shared['id']='reused_amount'
        shared['question_ids']=['q_other'];shared['calculation_context']['currency']='USD';specs.append(shared)
        with self.assertRaises(ContractError):self.expand(specs)

    def test_date_candidates_only_normalize_explicit_read_dates(self):
        view={'parts':[{'locator':'p1','text':'截至2026 年6 月30 日。取得日不在正文。2026年2月30日。'}]}
        self.assertEqual(original_date_anchors(view),[{'date':'2026-06-30','part':'p1','quote':'2026 年6 月30 日'}])

    def test_date_candidates_use_the_same_literal_grammar_as_final_verification(self):
        view={'parts':[{'locator':'p1','text':'2026 年 6 月\n30 日。2026-06-30。'}]}
        self.assertEqual(original_date_anchors(view),[{'date':'2026-06-30','part':'p1','quote':'2026-06-30'}])


del Fixture
