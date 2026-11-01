"""Rejected compact references remain inspectable but never become evidence."""
import copy
import importlib
import json
import unittest

from researchhelper.contracts import ContractError
from researchhelper.reasoning import model_object,material_prompt_projection,evidence_contract

Fixture=importlib.import_module('tests.test_financial_value_reference' if __package__
                              else 'test_financial_value_reference').FinancialValueReferenceTests


class FinancialReferenceDiagnosticsTests(unittest.TestCase):
    setUpClass=classmethod(Fixture.setUpClass.__func__)
    tearDownClass=classmethod(Fixture.tearDownClass.__func__)
    setUp=Fixture.setUp
    candidates_for=Fixture.candidates_for
    compact=Fixture.compact
    payload=Fixture.payload

    def test_invalid_selection_preserves_rejected_wire_not_approved_state(self):
        specs=self.compact();specs[0]['financial_value_reference']='rh_fin_invalid'
        before=copy.deepcopy((self.task,specs))
        class Model:
            def reason_phase(self,*args):return json.dumps({'evidence_specs':specs})
        with self.assertRaises(ContractError) as caught:
            model_object(Model(),self.task,'extract',self.payload(),{'evidence_specs'})
        self.assertEqual(caught.exception.rejected_extraction_specs,specs)
        self.assertFalse(caught.exception.diagnostic['evidence_approved'])
        self.assertIn('失效',caught.exception.diagnostic['reference_failure'])
        self.assertEqual((self.task,specs),before)

    def test_shortcut_prompt_lists_exact_conflicting_fields_without_filling_facts(self):
        payload=self.payload()
        payload['untrusted_sources']=[self.view]
        payload['evidence_contract']=evidence_contract(self.task,[self.view])
        before=copy.deepcopy(payload)
        presented,applied=material_prompt_projection(payload)
        self.assertTrue(applied)
        shape=presented['evidence_contract']['financial_short_reference_shape']
        self.assertEqual(shape['type'],'candidate_id字符串')
        self.assertEqual(set(shape['excluded_fields']),
            {'text','period','raw_token','raw_value','raw_unit','unit','value','conversion_factor'})
        self.assertFalse(shape['automatic_selection'])
        self.assertFalse(shape['evidence_approved'])
        self.assertEqual(payload,before)


del Fixture
