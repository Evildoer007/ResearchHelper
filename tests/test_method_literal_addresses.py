"""Finite literal addresses exclude impossible shorthand, not source numbers."""
import copy
import hashlib
import json
import unittest

from tests import ROOT
from tests.probe_etf_extraction_context import payload_for
from researchhelper.contracts import canonical
from researchhelper.output_schema import (_original_span_choices,_adjacent_quantity_units,
    original_span_output_schema,original_span_prompt,provider_request_schema,ORIGINAL_SPAN_PROTOCOL)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS

POLICY='method_literal_address_version'


class MethodLiteralAddressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.retained=ROOT/'result/acceptance/autonomous-data-phase-9xcpusbz/task.json'
        cls.digest=hashlib.sha256(cls.retained.read_bytes()).hexdigest()
        cls.task=json.loads(cls.retained.read_text());cls.task[POLICY]=1
        cls.task['input_hash']=input_fingerprint(cls.task)
        cls.views=cls.task['reasoning_state']['reading']['bundle']['views']
        cls.schema=original_span_output_schema(cls.task,cls.views)

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(cls.retained.read_bytes()).hexdigest()==cls.digest

    def test_short_form_enumerates_only_actual_same_source_unit_addresses(self):
        choices={c['source_ref']:c for c in _original_span_choices(self.task,self.views)}
        tested=0
        for variant in self.schema['properties']['selections']['items']['anyOf']:
            properties=variant['properties']
            if 'number_reference' not in properties:continue
            choice=choices[properties['source_ref']['enum'][0]]
            for branch in properties['number_reference']['anyOf']:
                if 'literal_ref' in branch['properties']:
                    addresses=branch['properties']['literal_ref']
                    self.assertEqual(addresses,dict(type='string',enum=list(_adjacent_quantity_units(choice))))
                    self.assertLess(len(addresses['enum']),len(choice['number_tokens']))
                    self.assertIn('text_span',branch['properties'])
                    tested+=1
        self.assertEqual(tested,2)

    def test_non_adjacent_numbers_keep_the_complete_unit_span_branch(self):
        for variant in self.schema['properties']['selections']['items']['anyOf']:
            properties=variant['properties']
            if 'number_reference' not in properties:continue
            explicit=properties['number_reference']['anyOf'][1]['properties']
            self.assertEqual(set(explicit),{'number_ref','unit','unit_span','text_span'})
            self.assertIn('contexts',properties)

    def test_old_policy_and_noninteger_flags_do_not_change_selection_grammar(self):
        old=copy.deepcopy(self.task);old.pop(POLICY);old['input_hash']=input_fingerprint(old)
        self.assertNotEqual(old['input_hash'],self.task['input_hash'])
        expected=original_span_output_schema(old,self.views)['properties']['selections']
        self.assertNotEqual(expected,self.schema['properties']['selections'])
        for version in (True,False,0,2,1.0):
            task=dict(old,**{POLICY:version});task['input_hash']=input_fingerprint(task)
            self.assertEqual(expected,original_span_output_schema(task,self.views)['properties']['selections'])

    def test_all_four_originals_and_obligations_fit_the_same_budget(self):
        task=json.loads((ROOT/'result/acceptance/autonomous-data-phase-yr9yq5lu/task.json').read_text())
        task.update({POLICY:1,'method_excerpt_selection_version':1,'structured_schema_sharing_version':1})
        task['input_hash']=input_fingerprint(task)
        payload=payload_for(task);projected=original_span_prompt(payload,task)
        schema=original_span_output_schema(task,payload['untrusted_sources'])
        self.assertEqual(len(projected['untrusted_sources']),4)
        self.assertEqual(projected['questions'],payload['questions'])
        self.assertEqual(projected['calculations'],payload['calculations'])
        self.assertLessEqual(len(canonical(projected))+len(canonical(provider_request_schema(
            task,schema,ORIGINAL_SPAN_PROTOCOL))),MAX_CONTEXT_CHARACTERS)
        self.assertEqual(task['ledger']['limits']['model_calls'],8)
