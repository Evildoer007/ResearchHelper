"""Lossless private API-container codec; closed exports are read-only fixtures."""
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.api_context_wire import POLICY, project, restore, enabled
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelService, ModelSettings
from researchhelper.output_schema import (ORIGINAL_SPAN_PROTOCOL, ORIGINAL_SPAN_FUNCTION_NAME,
    original_span_output_schema, structured_json_completion, structured_completion,
    provider_request_schema, _matches, object_schema, array_schema)
from researchhelper.planning import input_fingerprint, question_specs
from researchhelper.reasoning import evidence_contract, model_object
from researchhelper.reports import compose

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT/'result/acceptance/one-sentence-research-1fpj77sl'


def prepare_fixture():
    return dict(task=json.loads((STUDY/'task.json').read_text()),
        hashes={name:hashlib.sha256((STUDY/name).read_bytes()).hexdigest()
            for name in ('receipt.json','task.json','report.json','report.html')})


def literal(schema):
    if 'enum' in schema: return schema['enum'][0]
    if 'anyOf' in schema: return literal(schema['anyOf'][0])
    kind = schema['type']
    if kind == 'object': return {key:literal(value) for key,value in schema['properties'].items()}
    if kind == 'array': return []
    if kind == 'string': return 'control'
    if kind == 'integer': return schema.get('minimum', 0)
    if kind == 'boolean': return True
    if kind == 'null': return None
    raise AssertionError('Unsupported control type')


class APIContextWireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get('RESEARCHHELPER_API_CONTEXT_FIXTURE')
        cls.fixture = json.loads(gzip.decompress(Path(path).read_bytes())) if path else prepare_fixture()

    def setUp(self):
        network = patch('socket.socket.connect', side_effect=AssertionError('No model or data calls'))
        network.start(); self.addCleanup(network.stop)
        self.old = copy.deepcopy(self.fixture['task'])
        self.task = copy.deepcopy(self.old)
        self.task[POLICY] = 1
        self.task['input_hash'] = input_fingerprint(self.task)
        self.views = self.task['reasoning_state']['reading']['bundle']['views']
        self.schema = original_span_output_schema(self.task, self.views)
        self.variant = next(item for item in self.schema['properties']['selections']['items']['anyOf']
            if item['properties']['kind']['enum'] == ['api'])
        self.canonical = literal(self.schema)
        self.canonical['selections'] = [literal(self.variant)]
        self.wire = copy.deepcopy(self.canonical)
        self.wire['version'] = 14
        self.wire['selections'][0].pop('additional_contexts')

    def decode(self, wire):
        return json.loads(structured_json_completion(json.dumps(wire), self.schema,
            protocol=ORIGINAL_SPAN_PROTOCOL, context=(self.task,self.views)))

    def test_actual_two_failures_have_distinct_causes_and_old_schema_is_unchanged(self):
        events = [event for event in self.old['ledger']['dispatches']
            if event.get('research_phase') == 'extract']
        self.assertEqual(events[0]['structured_output_diagnostic']['json_error'], 'duplicate_key')
        self.assertEqual(events[1]['structured_failure_category'], 'invalid_original_spans')
        self.assertEqual(events[1]['structured_output_diagnostic']['schema_issues'],
            [dict(code='missing_field',path='$.selections[*].additional_contexts')])
        old_schema = original_span_output_schema(self.old, self.views)
        self.assertEqual(fingerprint(old_schema), events[1]['output_schema_hash'])
        self.assertEqual(project(self.old, old_schema), old_schema)
        self.assertNotIn(POLICY, self.old)

    def test_projection_offers_strict_full_and_compact_api_objects(self):
        schema = project(self.task, self.schema)
        self.assertEqual(schema['properties']['version']['enum'], [14])
        api = [item for item in schema['properties']['selections']['items']['anyOf']
            if item['properties']['kind']['enum'] == ['api']]
        self.assertEqual(len(api), 2)
        self.assertEqual({('additional_contexts' in item['properties']) for item in api}, {False,True})
        self.assertTrue(all(set(item['required']) == set(item['properties']) for item in api))
        self.assertTrue(_matches(self.wire, schema))

    def test_declared_omission_restores_only_the_empty_array_without_mutation(self):
        before = copy.deepcopy((self.task,self.schema,self.wire))
        self.assertEqual(restore(self.task,self.schema,self.wire), self.canonical)
        self.assertEqual((self.task,self.schema,self.wire), before)
        self.assertEqual(self.decode(self.wire), self.decode(self.canonical))
        self.assertEqual(self.decode(self.wire)['evidence_specs'], [])

    def test_existing_full_canonical_submission_has_no_new_defaults(self):
        value = copy.deepcopy(self.canonical)
        value['selections'][0].pop('additional_contexts')
        self.assertEqual(restore(self.task,self.schema,value), value)
        with self.assertRaises(ContractError): self.decode(value)

    def test_nonempty_chosen_context_is_preserved_exactly(self):
        wire = copy.deepcopy(self.wire)
        shape = self.variant['properties']['additional_contexts']['items']['anyOf'][0]
        addition = literal(shape)
        wire['selections'][0]['additional_contexts'] = [addition]
        expected = copy.deepcopy(wire); expected['version'] = 12
        self.assertEqual(restore(self.task,self.schema,wire), expected)

    def test_missing_selection_or_financial_fields_are_not_repaired(self):
        wire = copy.deepcopy(self.wire); wire['selections'][0]['selection'].pop('version')
        restored = restore(self.task,self.schema,wire)
        self.assertNotIn('version', restored['selections'][0]['selection'])
        with self.assertRaises(ContractError): self.decode(wire)

    def test_unknown_fields_are_never_deleted_to_make_a_submission_pass(self):
        wire = copy.deepcopy(self.wire); wire['selections'][0]['approved'] = True
        restored = restore(self.task,self.schema,wire)
        self.assertTrue(restored['selections'][0]['approved'])
        self.assertNotIn('additional_contexts',restored['selections'][0])
        with self.assertRaises(ContractError): self.decode(wire)

    def test_null_or_wrong_type_is_not_an_omission(self):
        for value in (None,False,{},''):
            wire = copy.deepcopy(self.wire); wire['selections'][0]['additional_contexts'] = value
            self.assertEqual(restore(self.task,self.schema,wire)['selections'][0]['additional_contexts'],value)
            with self.subTest(value=value), self.assertRaises(ContractError): self.decode(wire)

    def test_required_nonempty_context_array_is_not_made_optional(self):
        schema = copy.deepcopy(self.schema)
        api = next(item for item in schema['properties']['selections']['items']['anyOf']
            if item['properties']['kind']['enum'] == ['api'])
        api['properties']['additional_contexts']['minItems'] = 1
        self.assertEqual(project(self.task,schema),schema)

    def test_wrong_binding_version_and_extra_root_fields_are_rejected(self):
        for key,value in (('binding_ref','foreign'),('version',True),('version',13),('review',{})):
            wire = copy.deepcopy(self.wire); wire[key] = value
            with self.subTest(key=key,value=value), self.assertRaises(ContractError): self.decode(wire)

    def test_legacy_historical_and_document_tasks_do_not_gain_the_wire(self):
        old = copy.deepcopy(self.task); old.pop(POLICY)
        self.assertEqual(project(old,self.schema),self.schema)
        with self.assertRaises(ContractError):
            structured_json_completion(json.dumps(self.wire),self.schema,
                protocol=ORIGINAL_SPAN_PROTOCOL,context=(old,self.views))
        for changes in (dict(research_mode='historical'),dict(intent='原件内容核对')):
            task = dict(self.task,**changes)
            self.assertEqual(project(task,self.schema),self.schema)
        for policy in (True,0,2,'1',1.0):
            with self.subTest(policy=policy),self.assertRaises(ContractError): enabled({POLICY:policy})

    def test_duplicate_json_keys_remain_rejected_even_if_equal(self):
        raw = json.dumps(self.wire).replace('"version": 14','"version": 14,"version":14',1)
        with self.assertRaises(ContractError):
            structured_json_completion(raw,self.schema,protocol=ORIGINAL_SPAN_PROTOCOL,
                context=(self.task,self.views))

    def test_tool_and_responses_channels_restore_identical_canonical_results(self):
        message = dict(role='assistant',content=None,tool_calls=[dict(id='control',type='function',
            function=dict(name=ORIGINAL_SPAN_FUNCTION_NAME,arguments=json.dumps(self.wire)))])
        tool = json.loads(structured_completion(message,'tool_calls',self.schema,
            protocol=ORIGINAL_SPAN_PROTOCOL,context=(self.task,self.views)))
        self.assertEqual(tool,self.decode(self.wire))

    def test_provider_schema_uses_version14_without_changing_public_schema(self):
        before = copy.deepcopy(self.schema)
        schema = provider_request_schema(self.task,self.schema,ORIGINAL_SPAN_PROTOCOL)
        self.assertEqual(schema['properties']['version']['enum'],[14])
        self.assertEqual(self.schema,before)
        self.assertEqual(self.schema['properties']['version']['enum'],[12])

    def test_new_task_policy_is_frozen_without_resetting_budget(self):
        resources = Path(os.environ.get('RESEARCHHELPER_API_CONTEXT_RESOURCES',str(ROOT)))
        with tempfile.TemporaryDirectory(prefix='rh-api-context-wire-') as folder:
            agent = ResearchAgent(ResearchArchive(folder),resources,offline=True,require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create('解释ETF日终净值',entry='ETF',objects=['510300.SH'])
            self.assertEqual(task[POLICY],1)
            old = copy.deepcopy(task);old.pop(POLICY)
            self.assertNotEqual(input_fingerprint(old),input_fingerprint(task))
            agent._adopt_method_knowledge(task);task['status']='部分完成'
            agent._gap(task,'传输格式控制，不是实际研究。',category='question_coverage')
            self.assertEqual(compose(task)[POLICY],1)
            self.assertEqual(task['ledger']['used']['model_calls'],0)
            self.assertEqual(task['ledger']['limits'],dict(data_requests=100,model_calls=8,seconds=600))

    def test_model_instruction_advertises_version14_and_explicit_empty_semantics(self):
        class Captured(Exception): pass
        class Observer:
            def supports_structured_analysis(self):return True
            def input_budget(self):
                return ModelService.documented_input_budget(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash'))
            def reason_structured_original_spans(self,task,messages,views):
                self.messages=messages
                raise Captured()
        observer=Observer()
        payload=dict(question=self.task['question'],intent=self.task['intent'],objects=self.task['objects'],
            cutoff=self.task['cutoff'],questions=question_specs(self.task),calculations=self.task['calculation_specs'],
            evidence_contract=evidence_contract(self.task,self.views),field_registry={},untrusted_sources=self.views,
            source_reading=None)
        with self.assertRaises(Captured):model_object(observer,self.task,'extract',payload,{'evidence_specs'})
        instruction=observer.messages[0]['content']
        self.assertIn('version=14',instruction)
        self.assertIn('省略additional_contexts',instruction)

    def test_actual_model_adapter_projects_and_restores_without_a_network_call(self):
        response = json.dumps(dict(object='response',status='completed',error=None,incomplete_details=None,
            output=[dict(type='message',role='assistant',status='completed',
                content=[dict(type='output_text',text=json.dumps(self.wire))])],usage={})).encode()
        before = copy.deepcopy(self.task['ledger'])
        with tempfile.TemporaryDirectory(prefix='rh-api-wire-response-') as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',
                api_key='SYNTHETIC_TEST_ONLY',extraction_mode='direct'))
            service = ModelService(settings,offline=False)
            def transport(task, request, started, timeout, record):
                body = json.loads(request.data)
                self.assertEqual(body['text']['format']['schema']['properties']['version']['enum'],[14])
                record.update(response_body_complete=True,received_response_bytes=len(response))
                return response
            with patch.object(service,'_request_bytes',side_effect=transport) as call:
                result = json.loads(service.reason_structured_original_spans(self.task,[],self.views))
            self.assertEqual(call.call_count,1)
        self.assertEqual(result['evidence_specs'],[])
        self.assertEqual(self.task['ledger']['used']['model_calls'],before['used']['model_calls']+1)
        self.assertEqual(self.task['ledger']['used']['data_requests'],before['used']['data_requests'])
        self.assertEqual(self.task['ledger']['limits'],before['limits'])
