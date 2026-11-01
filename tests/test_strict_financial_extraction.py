"""Financial strict output is a container, never a substitute for source gates."""
import copy
import importlib
import json
import unittest

from researchhelper.contracts import ContractError
from researchhelper.output_schema import (financial_extraction_v2_schema as financial_extraction_output_schema,
    decode_financial_extraction_output, structured_completion,
    FINANCIAL_EXTRACTION_FUNCTION_NAME,FINANCIAL_EXTRACTION_PROTOCOL)
from researchhelper.financial_sources import expand_financial_value_references

Fixture=importlib.import_module('tests.test_financial_value_reference' if __package__
                              else 'test_financial_value_reference').FinancialValueReferenceTests


class StrictFinancialExtractionTests(unittest.TestCase):
    setUpClass=classmethod(Fixture.setUpClass.__func__)
    tearDownClass=classmethod(Fixture.tearDownClass.__func__)
    candidates_for=Fixture.candidates_for
    compact=Fixture.compact
    _set_up=Fixture.setUp

    def setUp(self):
        self._set_up()
        self.schema=financial_extraction_output_schema(self.task,[self.view])
        self.wire={'version':2,'financial_cells':self.compact(),'text_quotes':[]}
        for spec in self.wire['financial_cells']:
            spec.pop('published_at');spec.pop('publication')
            spec['publication_mode']='unknown_current_original'
            needs=spec['review'].pop('evidence_need_texts')
            spec['review']['evidence_need_texts']=[{'question_id':key,'texts':texts} for key,texts in needs.items()]

    def decode(self):return decode_financial_extraction_output(self.wire,self.schema)

    def test_actual_short_values_expand_to_unchanged_original_specifications(self):
        decoded=self.decode()
        expanded=expand_financial_value_references(self.task,decoded['evidence_specs'],[self.view])
        expected=copy.deepcopy(self.original_specs)
        for spec in expected:
            spec['publication']['basis']='首次公开时间未知，当前可得性另由实际取数账本核验'
        self.assertEqual(expanded,expected)
        self.assertEqual(self.task['questions'][0]['method_needs'],self.original_task['questions'][0]['method_needs'])

    def test_a_second_numeric_value_cannot_override_the_selected_cell(self):
        self.wire['financial_cells'][0]['value']=1
        with self.assertRaises(ContractError):self.decode()

    def test_a_fabricated_candidate_id_is_not_repaired(self):
        self.wire['financial_cells'][0]['financial_value_reference']='rh_fin_fake'
        with self.assertRaises(ContractError):self.decode()

    def test_missing_accounting_context_basis_is_rejected(self):
        del self.wire['financial_cells'][0]['calculation_context_basis']['accounting_scope']
        with self.assertRaises(ContractError):self.decode()

    def test_other_method_obligations_are_not_removed_to_fit_financial_protocol(self):
        task=copy.deepcopy(self.task);task['questions'][0]['method_needs'].append('fee_difference_v1')
        before=copy.deepcopy(task)
        self.assertIsNone(financial_extraction_output_schema(task,[self.view]))
        self.assertEqual(task,before)

    def test_api_and_original_mixed_batch_does_not_select_financial_container(self):
        self.assertIsNone(financial_extraction_output_schema(self.task,[self.view,{'kind':'api'}]))

    def test_protocol_accepts_one_named_submission_and_never_executes_tools(self):
        message={'role':'assistant','content':None,'tool_calls':[{'id':'submission','type':'function',
            'function':{'name':FINANCIAL_EXTRACTION_FUNCTION_NAME,'arguments':json.dumps(self.wire)}}]}
        result=structured_completion(message,'tool_calls',self.schema,protocol=FINANCIAL_EXTRACTION_PROTOCOL)
        self.assertEqual(json.loads(result),self.decode())
        message['tool_calls'][0]['function']['name']='execute_external_request'
        with self.assertRaises(ContractError):
            structured_completion(message,'tool_calls',self.schema,protocol=FINANCIAL_EXTRACTION_PROTOCOL)


# Keep only this cohort in discovery; the retained fixture still verifies hashes.
del Fixture
