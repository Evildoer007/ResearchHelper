"""Lossless source intervals and explicit method context; no financial approval."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tests.fee_disclosure_fixture import fee_task, read_fee_task
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.evidence import candidate_evidence, validate_calculation_context_basis, validate_reporting_period_basis
from researchhelper.financial_sources import literal_interval, original_period_anchors
from researchhelper.output_schema import (_original_span_choices, original_span_output_schema,
    original_span_prompt, decode_original_spans, StructuredOutputError)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS, original_context_selection_guidance

POLICY = 'original_interval_context_version'


class LiteralIntervalEncodingTests(unittest.TestCase):
    def test_equal_complete_dates_have_one_representation_and_keep_originals(self):
        texts = ['2026年1月1日起至2026年6 月30日','2026年01月01日至2026年06月30日',
                 '2026-01-01到2026-06-30']
        for quote in texts:
            with self.subTest(quote=quote):
                self.assertEqual(literal_interval(quote),'2026-01-01/2026-06-30')
                anchors = original_period_anchors(dict(parts=[dict(locator='段1',text=quote)]),representation_version=2)
                self.assertEqual(anchors[0]['quote'],quote)
                self.assertEqual(anchors[0]['period'],'2026-01-01/2026-06-30')

    def test_incomplete_reversed_invalid_or_extra_dates_never_become_intervals(self):
        for quote in ('2026年上半年','2026年1月1日至6月30日',
                      '2026-06-30至2026-01-01','2026-02-30至2026-06-30',
                      '报告期2026-01-01至2026-06-30','2026-01-01至2026-06-30或2026-09-30'):
            with self.subTest(quote=quote),self.assertRaises((ContractError,ValueError)):
                literal_interval(quote)
        for value in (True,False,0,3,2.0):
            with self.assertRaises(ContractError):
                original_period_anchors(dict(parts=[]),representation_version=value)

    def test_legacy_interval_label_is_unchanged(self):
        quote = '2026年1月1日起至2026年6 月30日'
        old = original_period_anchors(dict(parts=[dict(locator='段1',text=quote)]))[0]
        self.assertEqual(old['period'],'2026年1月1日起至2026年6月30日')
        self.assertNotIn('representation_version',old)


class OriginalIntervalContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.task = fee_task(has_trading_code=False)
        cls.task['questions'][0]['method_needs'] = ['fee_difference_v2']
        # Pin the earlier location and interval formats. The current fee
        # candidate protocol is exercised separately on the same disclosures.
        for flag in ('original_duration_anchor_version','original_fee_context_mapping_version',
                     'original_local_span_version','original_whole_part_selection_version',
                     'method_excerpt_selection_version','method_literal_address_version',
                     'original_observation_reference_version','original_scope_wire_version',
                     'original_explicit_unit_reference_version','independent_original_selections_version'):
            cls.task.pop(flag,None)
        cls.task.update(original_prompt_projection_version=1, **{POLICY:1})
        cls.task['input_hash'] = input_fingerprint(cls.task)
        bundle = read_fee_task(cls.task)
        cls.task.setdefault('reasoning_state',{})['reading'] = {'bundle':bundle}
        cls.views = bundle['views']
        cls.digest = fingerprint(cls.task['source_records'])
        cls.choices = _original_span_choices(cls.task,cls.views)
        cls.schema = original_span_output_schema(cls.task,cls.views)

    @classmethod
    def tearDownClass(cls):
        assert fingerprint(cls.task['source_records']) == cls.digest

    def wire(self,index=0):
        choice = self.choices[index]
        source = choice['description']['source']
        line = next(row for row in choice['lines'] if '0.15' in row['text'] and '年费率' in row['text'])
        number = next(row for row in choice['number_tokens'] if row['line']==line['index'] and row['token']=='0.15')
        point = dict(start=line['index'],end=line['index'])
        share = source['listed_etf_unit_candidates'][0]
        issuer = source['object_binding']['value']
        identity = next(row for row in choice['lines'] if issuer.split('.')[0] in row['text'] and '代码' in row['text'])
        variant = next(row for row in self.schema['properties']['selections']['items']['anyOf']
                       if row['properties']['source_ref']['enum']==[choice['source_ref']]
                       and row['properties']['kind']['enum']==['number'])
        props = variant['properties']
        need = props['review']['properties']['need_links']['items']['anyOf'][0]['properties']
        period_ref = choice['period_anchors'][0]['ref']
        contexts = [dict(field=field,value=value,span=copy.deepcopy(point)) for field,value in
                    (('metric','management_fee'),('fee_basis','net_assets'),('rate_period','annual'))]
        contexts += [dict(field='fund_id',value=issuer.split('.')[0],span=dict(start=identity['index'],end=identity['index'])),
                     dict(field='share_class',unit_ref=share['ref']),dict(field='period',period_ref=period_ref)]
        item = dict(id='fee_'+str(index),source_ref=choice['source_ref'],kind='number',object=issuer,
            nature='合同政策条款',number_reference=dict(literal_ref=number['ref']),
            contexts=contexts,temporal_reference=dict(period_ref=period_ref),
            review=dict(scope='current_original_descriptive_only',need_links=[dict(
                question_id=need['question_id']['enum'][0],need_ids=[need['need_ids']['items']['enum'][0]])]))
        return dict(version=12,binding_ref=self.schema['properties']['binding_ref']['enum'][0],selections=[item])

    def decode(self,wire=None):
        return decode_original_spans(wire or self.wire(),self.schema,(self.task,self.views))['evidence_specs'][0]

    def test_explicit_interval_reuses_exact_source_basis_without_typed_label(self):
        for index in (0,1):
            spec = self.decode(self.wire(index))
            self.assertEqual(spec['period'],'2026-01-01/2026-06-30')
            self.assertEqual(spec['calculation_context']['period'],spec['period'])
            self.assertEqual(spec['calculation_context_basis']['period'],spec['reporting_period_basis'])
            self.assertEqual(spec['reporting_period_basis']['version'],2)
            self.assertEqual((spec['raw_value'],spec['unit']),(0.15,'%'))
            self.assertEqual(set(spec['calculation_context']),{'fund_id','share_class','period','metric','fee_basis','rate_period'})
            record = next(row for row in self.task['source_records'] if row['id']==spec['artifact_id'])
            validate_reporting_period_basis(spec,record)
            item = candidate_evidence(self.task,record,spec)
            validate_calculation_context_basis(item,self.task['source_records'])
            self.assertNotIn('source_check',spec)

    def test_free_period_duplicate_cross_period_and_unknown_ref_are_rejected(self):
        wire = self.wire()
        for replacement in (dict(field='period',value='2026年上半年',span=dict(start=0,end=0)),
                            dict(field='period',period_ref='p9999')):
            bad = copy.deepcopy(wire); bad['selections'][0]['contexts'][-1]=replacement
            with self.assertRaises((ContractError,StructuredOutputError)):self.decode(bad)
        bad = copy.deepcopy(wire);bad['selections'][0]['contexts'].append(copy.deepcopy(bad['selections'][0]['contexts'][-1]))
        with self.assertRaises(ContractError):self.decode(bad)
        bad = copy.deepcopy(wire);choice=self.choices[0]
        bad['selections'][0]['temporal_reference']=dict(observation_anchor=choice['period_anchors'][0]['end_anchor'])
        with self.assertRaises(ContractError):self.decode(bad)

    def test_literal_and_explicit_number_forms_keep_the_same_complete_context(self):
        from researchhelper.output_schema import _adjacent_quantity_units
        wire = self.wire()
        item = wire['selections'][0]
        ref = item['number_reference']['literal_ref']
        quantity = _adjacent_quantity_units(self.choices[0])[ref]
        point = dict(start=quantity['line'],end=quantity['line'])
        manual = copy.deepcopy(wire)
        manual['selections'][0]['number_reference'] = dict(number_ref=ref,unit=quantity['unit'],
            text_span=point,unit_span=copy.deepcopy(point))
        self.assertEqual(self.decode(wire),self.decode(manual))
        self.assertEqual(len(self.decode(wire)['calculation_context']),6)

    def test_literal_quantity_cannot_borrow_unit_or_hide_context_inside_reference(self):
        for ref in ('n1_9999','n2_1'):
            wire = self.wire();wire['selections'][0]['number_reference']['literal_ref']=ref
            with self.assertRaises(StructuredOutputError):self.decode(wire)
        wire = self.wire()
        wire['selections'][0]['number_reference']['contexts']=wire['selections'][0].pop('contexts')
        with self.assertRaises(StructuredOutputError):self.decode(wire)

    def test_modified_basis_period_quote_or_unregistered_version_cannot_pass(self):
        original = self.decode()
        record = next(row for row in self.task['source_records'] if row['id']==original['artifact_id'])
        for changes in (dict(period='2026-01-01/2026-09-30'),
                        dict(reporting_period_basis=dict(original['reporting_period_basis'],version=True)),
                        dict(reporting_period_basis=dict(original['reporting_period_basis'],quote='2026年上半年'))):
            with self.assertRaises(ContractError):validate_reporting_period_basis(dict(original,**changes),record)

    def test_share_reference_cannot_mix_partial_span_or_unknown_candidate(self):
        wire = self.wire()
        for replacement in (dict(field='share_class',unit_ref='u9999'),
                            dict(field='share_class',value='listed_report_unit:510300.SH',span=dict(start=0,end=0))):
            bad = copy.deepcopy(wire);bad['selections'][0]['contexts'][-2]=replacement
            with self.assertRaises(StructuredOutputError):self.decode(bad)

    def test_legacy_policy_and_input_binding_are_preserved(self):
        old = dict(self.task);old.pop(POLICY);old['input_hash']=input_fingerprint(old)
        self.assertNotEqual(old['input_hash'],self.task['input_hash'])
        self.assertNotIn('representation_version',_original_span_choices(old,self.views)[0]['period_anchors'][0])
        for value in (True,False,0,2):
            task = dict(old,**{POLICY:value});task['input_hash']=input_fingerprint(task)
            self.assertNotIn('representation_version',_original_span_choices(task,self.views)[0]['period_anchors'][0])

    def test_sources_obligations_and_capacity_are_preserved(self):
        from tests.probe_etf_extraction_context import payload_for
        payload = payload_for(self.task)
        projected = original_span_prompt(payload,self.task)
        self.assertEqual(projected['questions'],payload['questions'])
        self.assertEqual(len(projected['untrusted_sources']),len(self.views))
        self.assertTrue(all(row.get('reporting_period_encoding')=='iso_interval_from_same_literal_complete_dates_v2'
                            for row in projected['untrusted_sources']))
        self.assertLessEqual(len(canonical(projected))+len(canonical(self.schema)),MAX_CONTEXT_CHARACTERS)

    def test_context_guidance_is_not_limited_to_mixed_batches(self):
        guide = original_context_selection_guidance(self.task)
        self.assertIn('share_class',guide)
        self.assertIn('period_ref',guide)
        self.assertIn('required_context',guide)
        self.assertEqual(original_context_selection_guidance(dict(questions=[],calculation_specs=[])),'')

    def test_source_intervals_allow_comparable_inputs_without_changing_fee_units(self):
        from researchhelper.contracts import validate_calculation_context,calculate
        inputs = {}
        for index,key in enumerate(('fee_a','fee_b')):
            spec = self.decode(self.wire(index))
            record = next(row for row in self.task['source_records'] if row['id']==spec['artifact_id'])
            inputs[key] = candidate_evidence(self.task,record,spec)
        parameters = dict(unit='%/年',period='2026-01-01/2026-06-30',fee_kind='management_fee')
        validate_calculation_context('fee_difference_v2',inputs,parameters)
        output = calculate('fee_difference_v2',{key:row['value'] for key,row in inputs.items()},parameters)
        self.assertEqual(output['value'],0)
        self.assertEqual(output['unit'],'百分点/年')
        self.assertEqual([row['unit'] for row in inputs.values()],['%','%'])

    def test_actual_adapter_carries_pure_original_context_guidance_once(self):
        from researchhelper.model import ModelService,ModelSettings
        from researchhelper.reasoning import model_object
        from tests.probe_etf_extraction_context import payload_for
        wire = self.wire()
        response = dict(object='response',status='completed',error=None,incomplete_details=None,
            output=[dict(type='message',role='assistant',status='completed',
                         content=[dict(type='output_text',text=json.dumps(wire))])],usage={})
        task = copy.deepcopy(self.task)
        before = task['ledger']['used']['model_calls']
        with tempfile.TemporaryDirectory() as folder:
            settings = ModelSettings(folder)
            settings.save(dict(endpoint='https://api.deepseek.com/responses',model='deepseek-flash',
                               api_key='LOCAL_ONLY_FIXTURE',extraction_mode='direct'))
            with patch.object(ModelService,'_request_bytes',return_value=json.dumps(response).encode()) as transport:
                result,_ = model_object(ModelService(settings,offline=False),task,'extract',payload_for(task),{'evidence_specs'})
        self.assertEqual(result['evidence_specs'][0],self.decode(wire))
        self.assertEqual(transport.call_count,1)
        self.assertEqual(task['ledger']['used']['model_calls'],before+1)
        sent = json.loads(transport.call_args.args[1].data)
        self.assertIn('share_class',json.dumps(sent,ensure_ascii=False))
        self.assertIn('contexts中的period',json.dumps(sent,ensure_ascii=False))
