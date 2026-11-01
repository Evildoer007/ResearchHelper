"""A bound literal percent is not an invented annual rate or method input."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT,test_method_span_projection as fixtures
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import canonical
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelService,ModelSettings
from researchhelper.output_schema import (original_span_output_schema,original_span_prompt,decode_original_spans,
    _original_span_choices,_adjacent_quantity_units,_original_number_tokens,StructuredOutputError)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS,model_object,_method_literal_format


class MethodLiteralUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.MethodSpanProjectionTests.setUpClass()
        cls.base=fixtures.MethodSpanProjectionTests()
        cls.task=dict(copy.deepcopy(cls.base.task),method_literal_quantity_version=1)
        cls.task['input_hash']=input_fingerprint(cls.task)
        cls.views,cls.payload=cls.base.views,copy.deepcopy(cls.base.payload)
        from tests.probe_etf_extraction_context import payload_for
        cls.payload=payload_for(cls.task)
        cls.schema=original_span_output_schema(cls.task,cls.views)
        cls.prompt=original_span_prompt(cls.payload,cls.task)

    @classmethod
    def tearDownClass(cls):
        fixtures.MethodSpanProjectionTests.tearDownClass()

    def wire(self,*,manual=False):
        wire=self.base.wire();wire['binding_ref']=self.prompt['span_binding_ref']
        item=wire['selections'][0]
        numeric={key:item.pop(key) for key in ('number_ref','unit','unit_span','text_span','contexts')}
        item['number_reference']=numeric if manual else dict(literal_ref=numeric['number_ref'])
        return wire

    def decode(self,wire=None):
        return decode_original_spans(wire or self.wire(),self.schema,(self.task,self.views))

    def test_literal_and_explicit_units_recover_the_same_exact_source_spec(self):
        actual=self.decode()['evidence_specs'][0]
        self.assertEqual(self.decode(),self.decode(self.wire(manual=True)))
        self.assertEqual((actual['raw_value'],actual['unit'],actual['raw_unit']),(.15,'%','%'))
        self.assertNotIn('calculation_context',actual)
        self.assertNotIn('source_check',actual)
        self.assertEqual(actual['conversion_factor'],1)

    def test_unknown_ref_and_real_number_without_adjacent_unit_are_not_repaired(self):
        choices=_original_span_choices(self.task,self.views)
        no_unit=next(t['ref'] for t in choices[0]['number_tokens']
            if t['ref'] not in _adjacent_quantity_units(choices[0]))
        for ref in ('n1_9999','n2_1',no_unit):
            wire=self.wire();wire['selections'][0]['number_reference']['literal_ref']=ref
            with self.subTest(ref=ref),self.assertRaises(StructuredOutputError):self.decode(wire)

    def test_cannot_mix_literal_binding_with_manual_unit_or_outer_fields(self):
        for outside in (True,False):
            wire=self.wire()
            target=wire['selections'][0] if outside else wire['selections'][0]['number_reference']
            target['unit']='%/年'
            with self.subTest(outside=outside),self.assertRaises(StructuredOutputError):self.decode(wire)

    def test_compound_units_are_not_truncated_to_percent(self):
        lines=[dict(index=0,text='0.15%/年，1个百分点和2%。\n')]
        choice=dict(lines=lines,number_tokens=_original_number_tokens(lines,1,compact=True))
        bound=_adjacent_quantity_units(choice)
        self.assertNotIn('n1_1',bound)
        self.assertEqual(bound['n1_2']['unit'],'个百分点')
        self.assertEqual(bound['n1_3']['unit'],'%')

    def test_complete_two_originals_and_all_registered_obligations_still_fit(self):
        size=len(canonical(self.prompt))+len(canonical(self.schema))
        self.assertLessEqual(size,MAX_CONTEXT_CHARACTERS,size)
        self.assertEqual(self.task['questions'],self.base.task['questions'])
        self.assertTrue(_method_literal_format(self.schema))

    def test_context_bound_method_keeps_its_full_context_selection(self):
        task=copy.deepcopy(self.task)
        task['questions'][0]['method_needs']=['observed_price_return_v1']
        task['input_hash']=input_fingerprint(task)
        schema=original_span_output_schema(task,self.views)
        self.assertFalse(_method_literal_format(schema))
        numbers=[v['properties'] for v in schema['properties']['selections']['items']['anyOf']
                 if v['properties']['kind']['enum']==['number']]
        self.assertTrue(all('contexts' in p and 'unit_span' in p for p in numbers))

    def test_actual_model_adapter_uses_one_selection_and_preserves_unit(self):
        reply=dict(object='response',status='completed',error=None,incomplete_details=None,output=[dict(
            type='message',role='assistant',status='completed',content=[dict(type='output_text',text=json.dumps(self.wire()))])],usage={})
        task=copy.deepcopy(self.task)
        with tempfile.TemporaryDirectory(prefix='method-literal-',dir=ROOT/'result/acceptance') as folder:
            settings=ModelSettings(folder);settings.save(dict(endpoint='https://api.deepseek.com/responses',
                model='deepseek-flash',api_key='SYNTHETIC_UNIT_KEY',extraction_mode='direct'))
            with patch.object(ModelService,'_request_bytes',return_value=json.dumps(reply).encode()) as transport:
                result,receipt=model_object(ModelService(settings,offline=False),task,'extract',self.payload,{'evidence_specs'})
        self.assertEqual(result,self.decode());self.assertEqual(transport.call_count,1)
        self.assertEqual(receipt['method_literal_quantity_version'],1)

    def test_flag_is_input_bound_and_old_payload_keeps_its_original_unit_fields(self):
        self.assertNotEqual(input_fingerprint(self.task),input_fingerprint(self.base.task))
        old=original_span_output_schema(self.base.task,self.views)
        self.assertFalse(_method_literal_format(old))
        with tempfile.TemporaryDirectory() as folder:
            agent=ResearchAgent(ResearchArchive(folder),ROOT,offline=True,require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            self.assertEqual(agent.create('核对费用')['method_literal_quantity_version'],1)
            self.assertNotIn('method_literal_quantity_version',agent.create_case('mechanism'))
