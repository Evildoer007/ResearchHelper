"""Complete named fee contexts restore the same evidence, never fill omissions."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from tests import test_original_interval_context as interval_fixture
from researchhelper.contracts import ContractError, canonical, fingerprint, METHOD_REGISTRY
from researchhelper.evidence import candidate_evidence, validate_calculation_context_basis
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.output_schema import (original_span_output_schema, _original_span_choices,
    decode_original_spans, fee_context_mapping_enabled, StructuredOutputError, _local_span_address)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import original_context_selection_guidance

POLICY='original_fee_context_mapping_version'


class FeeContextMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Fixture=interval_fixture.OriginalIntervalContextTests
        Fixture.setUpClass()
        cls.base=Fixture()
        cls.task=copy.deepcopy(Fixture.task)
        cls.task.update(fee_method_catalogue_version=2,calculation_specs=[],**{POLICY:1})
        for question in cls.task['questions']:
            question['method_needs']=['fee_difference_v2']
        cls.task['knowledge']=ResearchKnowledge().read('ETF',cls.task['question'],methods=('fee_difference_v2',))
        cls.task['input_hash']=input_fingerprint(cls.task)
        cls.views=copy.deepcopy(Fixture.views)
        cls.schema=original_span_output_schema(cls.task,cls.views)
        cls.choices=_original_span_choices(cls.task,cls.views)
        cls.base.schema=cls.schema;cls.base.choices=cls.choices

    @classmethod
    def tearDownClass(cls):
        interval_fixture.OriginalIntervalContextTests.tearDownClass()

    def wire(self,index=0):
        wire=self.base.wire(index)
        wire['selections'][0]['contexts']={entry['field']:{key:value for key,value in entry.items() if key!='field'}
                                         for entry in wire['selections'][0]['contexts']}
        return wire

    def decode(self,wire=None):
        return decode_original_spans(wire or self.wire(),self.schema,(self.task,self.views))['evidence_specs'][0]

    def test_numeric_context_has_exactly_all_six_required_source_fields(self):
        expected=set(METHOD_REGISTRY['fee_difference_v2']['binding_context'])
        numbers=[entry['properties'] for entry in self.schema['properties']['selections']['items']['anyOf']
                 if entry['properties']['kind']['enum']==['number']]
        self.assertTrue(numbers)
        for entry in numbers:
            context=entry['contexts']
            self.assertEqual(context['type'],'object')
            self.assertEqual(set(context['properties']),expected)
            self.assertEqual(set(context['required']),expected)
            self.assertFalse(context['additionalProperties'])
            for field,shape in context['properties'].items():
                self.assertNotIn('default',shape)
                self.assertNotIn('field',shape.get('properties',{}))

    def test_omitting_any_context_key_is_rejected_before_adoption(self):
        for field in METHOD_REGISTRY['fee_difference_v2']['binding_context']:
            wire=self.wire();wire['selections'][0]['contexts'].pop(field)
            with self.subTest(field=field),self.assertRaises(StructuredOutputError):
                self.decode(wire)

    def test_complete_explicit_mapping_restores_original_values_and_all_anchors(self):
        before=canonical(self.task)
        for index in (0,1):
            spec=self.decode(self.wire(index))
            self.assertEqual(set(spec['calculation_context']),set(METHOD_REGISTRY['fee_difference_v2']['binding_context']))
            self.assertEqual((spec['raw_value'],spec['unit']),(0.15,'%'))
            record=next(item for item in self.task['source_records'] if item['id']==spec['artifact_id'])
            evidence=candidate_evidence(self.task,record,spec)
            validate_calculation_context_basis(evidence,self.task['source_records'])
            self.assertEqual(spec['calculation_context']['fund_id'],spec['object'].split('.')[0])
            self.assertEqual(spec['calculation_context_basis']['period'],spec['reporting_period_basis'])
            self.assertNotIn('source_check',spec)
        self.assertEqual(canonical(self.task),before)

    def test_wrong_fund_and_redundant_fields_are_not_fixed(self):
        wire=self.wire();wire['selections'][0]['contexts']['fund_id']['value']='000000'
        with self.assertRaises(StructuredOutputError):self.decode(wire)
        wire=self.wire();wire['selections'][0]['contexts']['fund_id']['field']='fund_id'
        with self.assertRaises(StructuredOutputError):self.decode(wire)

    def test_new_wire_cannot_silently_use_a_partial_legacy_list(self):
        wire=self.base.wire()
        with self.assertRaises(StructuredOutputError):self.decode(wire)

    def test_period_and_financial_checks_are_still_applied_after_decoding(self):
        wire=self.wire();wire['selections'][0]['contexts']['period']['period_ref']='absent'
        with self.assertRaises(StructuredOutputError):self.decode(wire)
        spec=self.decode();spec['calculation_context']['fund_id']='000000'
        record=next(item for item in self.task['source_records'] if item['id']==spec['artifact_id'])
        with self.assertRaises(ContractError):candidate_evidence(self.task,record,spec)

    def test_policy_is_explicit_input_bound_and_does_not_activate_for_other_methods(self):
        old=dict(self.task);old.pop(POLICY)
        self.assertNotEqual(input_fingerprint(old),self.task['input_hash'])
        self.assertFalse(fee_context_mapping_enabled(old))
        for value in (True,False,0,2,1.0):
            with self.subTest(value=value),self.assertRaises(ContractError):
                fee_context_mapping_enabled(dict(self.task,**{POLICY:value}))
        mixed=copy.deepcopy(self.task);mixed['questions'][0]['method_needs'].append('observed_price_return_v1')
        self.assertFalse(fee_context_mapping_enabled(mixed))
        self.assertFalse(fee_context_mapping_enabled(dict(self.task,intent='原件内容核对')))

    def test_textual_selection_and_retained_legacy_schema_remain_unchanged(self):
        legacy=copy.deepcopy(self.task);legacy.pop(POLICY);legacy['input_hash']=input_fingerprint(legacy)
        schema=original_span_output_schema(legacy,self.views)
        current=[row for row in self.schema['properties']['selections']['items']['anyOf']
                 if row['properties']['kind']['enum']==['text']]
        prior=[row for row in schema['properties']['selections']['items']['anyOf']
               if row['properties']['kind']['enum']==['text']]
        self.assertEqual(current,prior)
        legacy_shapes=[row['properties']['contexts'] for row in schema['properties']['selections']['items']['anyOf']
                       if row['properties']['kind']['enum']==['number']]
        self.assertTrue(all(shape['type']=='array' for shape in legacy_shapes))

    def test_loading_a_legacy_partial_archive_does_not_add_a_missing_fee_identity(self):
        from researchhelper.archive import ResearchArchive

        task=copy.deepcopy(self.task)
        task.pop(POLICY)
        proposed=self.decode()
        original=next(row for row in task['source_records'] if row['id']==proposed['artifact_id'])
        evidence=candidate_evidence(task,original,proposed)
        for key in ('calculation_context','calculation_context_basis'):
            evidence[key].pop('fund_id')
        task.update(status='部分完成',revision=0,evidence=[evidence])
        task['input_hash']=input_fingerprint(task)
        with tempfile.TemporaryDirectory(prefix='fee-legacy-partial-') as folder:
            archive=ResearchArchive(folder)
            saved=archive.save(task)
            restored=archive.get(task['id'])
            self.assertEqual(restored,saved)
            self.assertEqual(restored['status'],'部分完成')
            self.assertNotIn('fund_id',restored['evidence'][0]['calculation_context'])
            self.assertNotIn(POLICY,restored)

    def test_legacy_extraction_records_the_exact_dispatched_schema_hash(self):
        from researchhelper.model import ModelService, ModelSettings

        task=copy.deepcopy(self.task)
        task.pop(POLICY)
        task['input_hash']=input_fingerprint(task)
        schema=original_span_output_schema(task,self.views)
        wire=self.base.wire()
        wire['binding_ref']=schema['properties']['binding_ref']['enum'][0]
        body=dict(object='response',status='completed',error=None,incomplete_details=None,
            output=[dict(type='message',role='assistant',status='completed',
                         content=[dict(type='output_text',text=json.dumps(wire))])],usage={})
        before=fingerprint(task['source_records'])
        with tempfile.TemporaryDirectory(prefix='fee-legacy-schema-') as folder:
            settings=ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',
                               api_key='LOCAL_ONLY_FIXTURE',extraction_mode='direct'))
            service=ModelService(settings,offline=False)
            with patch.object(service,'_request_bytes',return_value=json.dumps(body).encode()) as transport:
                value=service.reason_structured_original_spans(task,[],self.views)
            transport.assert_called_once()
        self.assertTrue(json.loads(value)['evidence_specs'])
        self.assertEqual(task['ledger']['dispatches'][-1]['output_schema_hash'],fingerprint(schema))
        self.assertEqual(fingerprint(task['source_records']),before)

    def test_formal_task_and_frozen_definition_keep_the_policy(self):
        from researchhelper.archive import ResearchArchive
        from researchhelper.engine import ResearchAgent
        from researchhelper.reports import compose
        with tempfile.TemporaryDirectory(prefix='fee-context-policy-') as folder:
            agent=ResearchAgent(ResearchArchive(folder),ROOT,offline=True,require_argument_support=True)
            try:
                task=agent.create('解释ETF折溢价',entry='ETF',questions=[dict(id='q1',kind='fact',
                    question='解释ETF折溢价',answer_scope='general_definition',knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task[POLICY],1)
                self.assertEqual(task['review_delivery_call_version'],1)
                self.assertEqual(task['initial_fee_input_completion_version'],1)
                prior=dict(task);prior.pop(POLICY)
                self.assertNotEqual(input_fingerprint(prior),task['input_hash'])
                done=agent.start(task['id'])
                self.assertEqual(done['status'],'已完成')
                self.assertEqual(compose(done)[POLICY],1)
                self.assertEqual(compose(done)['review_delivery_call_version'],1)
                self.assertEqual(compose(done)['initial_fee_input_completion_version'],1)
                self.assertEqual(done['ledger']['used']['model_calls'],0)
                self.assertEqual(done['ledger']['used']['data_requests'],0)
            finally:
                agent.close_runtime()

    def test_current_local_span_and_excerpt_grammars_restore_complete_context(self):
        task=copy.deepcopy(self.task)
        task.update(original_local_span_version=1,original_whole_part_selection_version=1,
                    method_excerpt_selection_version=1,method_literal_address_version=1)
        task['input_hash']=input_fingerprint(task)
        schema=original_span_output_schema(task,self.views)
        choices=_original_span_choices(task,self.views)
        for index in (0,1):
            wire=self.wire(index)
            item=wire['selections'][0]
            item['number_reference']['text_span']=copy.deepcopy(item['contexts']['metric']['span'])
            def local(value):
                if isinstance(value,dict):
                    if set(value)=={'start','end'}:return _local_span_address(choices[index],value)
                    return {key:local(child) for key,child in value.items()}
                if isinstance(value,list):return [local(child) for child in value]
                return value
            wire=local(wire);wire['binding_ref']=schema['properties']['binding_ref']['enum'][0]
            spec=decode_original_spans(wire,schema,(task,self.views))['evidence_specs'][0]
            source=next(row for row in task['source_records'] if row['id']==spec['artifact_id'])
            evidence=candidate_evidence(task,source,spec)
            validate_calculation_context_basis(evidence,task['source_records'])
            self.assertEqual(set(evidence['calculation_context']),set(METHOD_REGISTRY['fee_difference_v2']['binding_context']))

    def test_prompt_uses_new_mapping_and_not_legacy_field_list_instructions(self):
        guidance=original_context_selection_guidance(self.task)
        self.assertIn('六字段对象',guidance)
        self.assertNotIn('{field:share_class',guidance)
        self.assertNotIn('{field:period',guidance)
