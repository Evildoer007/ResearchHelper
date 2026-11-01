"""Exact DAG schema transport; no source trimming or local gate removal."""
import copy
import hashlib
import json
import unittest

from tests import ROOT
from researchhelper.contracts import canonical
from researchhelper.output_schema import (provider_output_schema, provider_request_schema,
    original_span_output_schema, original_span_prompt, ORIGINAL_SPAN_PROTOCOL)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS
from tests.probe_etf_extraction_context import payload_for

POLICY = 'structured_schema_sharing_version'
RETAINED = ROOT/'result/acceptance/autonomous-data-phase-yr9yq5lu/task.json'


def expanded(schema):
    definitions = schema.get('$defs',{})

    def resolve(node,stack=()):
        if isinstance(node,dict):
            if '$ref' in node:
                if set(node)-{'$ref','type'} or not node['$ref'].startswith('#/$defs/'):
                    raise AssertionError('Only internal exact references are allowed')
                name = node['$ref'].split('/')[-1]
                if name in stack:raise AssertionError('Recursive reference')
                def allowed_types(value):
                    if isinstance(value.get('type'),str):return {value['type']}
                    return set().union(*(allowed_types(branch) for branch in value.get('anyOf',[])))
                if 'type' in node and allowed_types(resolve(definitions[name],stack+(name,)))!={node['type']}:
                    raise AssertionError('Reference type changed a constraint')
                return resolve(definitions[name],stack+(name,))
            return {key:resolve(value,stack) for key,value in node.items() if key!='$defs'}
        if isinstance(node,list):return [resolve(value,stack) for value in node]
        return node
    return resolve(schema)


class SharedSchemaTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.digest = hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        cls.task = json.loads(RETAINED.read_text())
        cls.task[POLICY]=1
        cls.task['input_hash']=input_fingerprint(cls.task)
        cls.payload = payload_for(cls.task)
        cls.schema = original_span_output_schema(cls.task,cls.payload['untrusted_sources'])

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(RETAINED.read_bytes()).hexdigest()==cls.digest

    def test_every_constraint_expands_to_the_same_provider_schema(self):
        before = canonical(self.schema)
        compact = provider_request_schema(self.task,self.schema,ORIGINAL_SPAN_PROTOCOL)
        self.assertIn('$defs',compact)
        self.assertEqual(expanded(compact),provider_output_schema(self.schema))
        self.assertEqual(canonical(self.schema),before)
        self.assertLess(len(canonical(compact)),len(canonical(self.schema)))

    def test_all_four_actual_originals_and_all_needs_fit_without_new_budget(self):
        projected = original_span_prompt(self.payload,self.task)
        wire = provider_request_schema(self.task,self.schema,ORIGINAL_SPAN_PROTOCOL)
        self.assertEqual(len(projected['untrusted_sources']),4)
        self.assertEqual(projected['questions'],self.payload['questions'])
        self.assertEqual(projected['calculations'],self.payload['calculations'])
        self.assertEqual(len(projected['evidence_contract']['sources']),4)
        self.assertGreater(len(canonical(projected))+len(canonical(self.schema)),MAX_CONTEXT_CHARACTERS)
        self.assertLessEqual(len(canonical(projected))+len(canonical(wire)),MAX_CONTEXT_CHARACTERS)
        self.assertEqual(self.task['ledger']['limits']['model_calls'],8)

    def test_old_boolean_or_other_protocol_never_changes_its_transport(self):
        expected = provider_output_schema(self.schema)
        for value in (None,True,False,0,2,1.0):
            task = dict(self.task,**{POLICY:value})
            self.assertEqual(provider_request_schema(task,self.schema,ORIGINAL_SPAN_PROTOCOL),expected)
        self.assertEqual(provider_request_schema(self.task,self.schema,'strict_plan_v2'),expected)

    def test_factoring_is_deterministic_and_does_not_mutate_payload(self):
        before = canonical(self.payload)
        first = provider_request_schema(self.task,self.schema,ORIGINAL_SPAN_PROTOCOL)
        second = provider_request_schema(self.task,copy.deepcopy(self.schema),ORIGINAL_SPAN_PROTOCOL)
        self.assertEqual(first,second)
        self.assertEqual(canonical(self.payload),before)

    def test_standard_validator_accepts_the_definition_graph(self):
        from jsonschema import Draft202012Validator
        compact = provider_request_schema(self.task,self.schema,ORIGINAL_SPAN_PROTOCOL)
        Draft202012Validator.check_schema(compact)

    def test_union_branches_are_inline_and_references_have_no_type_siblings(self):
        compact = provider_request_schema(self.task,self.schema,ORIGINAL_SPAN_PROTOCOL)
        references = []
        def inspect(node):
            if isinstance(node,dict):
                if '$ref' in node:
                    self.assertEqual(set(node),{'$ref'})
                    references.append(node['$ref'])
                for branch in node.get('anyOf',[]):
                    self.assertNotIn('$ref',branch)
                    if branch.get('type')=='object':self.assertTrue(branch['properties'])
                for value in node.values():inspect(value)
            elif isinstance(node,list):
                for value in node:inspect(value)
        inspect(compact)
        self.assertTrue(references)

    def test_policy_changes_research_input_identity(self):
        task = dict(self.task);task.pop(POLICY)
        self.assertNotEqual(input_fingerprint(task),self.task['input_hash'])
