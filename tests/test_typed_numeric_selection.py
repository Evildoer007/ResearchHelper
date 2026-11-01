"""Required numeric slots preserve literal values and every source gate."""
import copy
import gzip
import hashlib
import json
import unittest

from tests import ROOT
from researchhelper.contracts import ContractError
from researchhelper.data_requirements import numeric_slots
from researchhelper.planning import input_fingerprint
from researchhelper.source_reader import read,binding
from researchhelper.output_schema import (original_span_output_schema,_original_span_choices,
    decode_original_spans,_matches,provider_request_schema,ORIGINAL_SPAN_PROTOCOL)


class TypedNumericSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path=ROOT/'result/acceptance/autonomous-data-phase-8fimxhs6/task.json.gz'
        cls.original_hash=hashlib.sha256(cls.path.read_bytes()).hexdigest()
        with gzip.open(cls.path,'rt',encoding='utf-8') as stream:cls.old=json.load(stream)
        cls.task=copy.deepcopy(cls.old);cls.task['typed_numeric_selection_version']=2
        cls.task['knowledge_reading_navigation_version']=2
        cls.task['input_hash']=input_fingerprint(cls.task)
        original=cls.task['reasoning_state']['reading']['bundle']
        from unittest.mock import patch
        with patch('socket.socket.connect',side_effect=AssertionError('Offline original reading only')):
            bundle=read(cls.task,original['selection'],binding(cls.task),original['artifact_ids'])
        cls.task['reasoning_state']['reading']['bundle']=bundle
        cls.views=bundle['views']
        cls.schema=original_span_output_schema(cls.task,cls.views)
        cls.choices={c['source_ref']:c for c in _original_span_choices(cls.task,cls.views)}

    def wire(self):
        return dict(version=12,binding_ref=self.schema['properties']['binding_ref']['enum'][0],selections=[],
            numeric_requirements={slot['key']:dict(status='not_found',reason='离线格式测试，不宣称原件缺失')
                                  for slot in numeric_slots(self.task)})

    def number(self,slot):
        branches=self.schema['properties']['numeric_requirements']['properties'][slot['key']]['anyOf'][0]
        props=branches['properties']['items']['items']['anyOf'][0]['properties']
        choice=self.choices[props['source_ref']['enum'][0]]
        token=next(n for n in choice['number_tokens'] if n['token']=='0.15')
        unit=next(u for u in choice['unit_references'] if u['unit']=='%')
        period=next(p for p in choice['period_anchors'] if p['period']=='2026-01-01/2026-06-30')
        return dict(id='slot_number_test',kind='number',source_ref=choice['source_ref'],
            object=props['object']['enum'][0],nature='合同政策条款',
            number_reference=dict(number_ref=token['ref'],unit_ref=unit['ref']),
            temporal_reference=dict(period_ref=period['ref']),review=dict(scope='current_original_descriptive_only'))

    def test_every_numeric_need_has_a_required_slot_not_a_text_substitute(self):
        slots=numeric_slots(self.task)
        self.assertEqual(len(slots),4)
        self.assertEqual(set(self.schema['properties']['numeric_requirements']['required']),{s['key'] for s in slots})
        self.assertTrue(all(v['properties']['kind']['enum']==['text'] for v in
            self.schema['properties']['selections']['items']['anyOf']))
        wire=self.wire();wire.pop('numeric_requirements')
        self.assertFalse(_matches(wire,self.schema))
        wire=self.wire();wire['numeric_requirements'].pop(slots[0]['key'])
        self.assertFalse(_matches(wire,self.schema))

    def test_explicit_missing_preserves_no_evidence_and_does_not_approve_absence(self):
        result=decode_original_spans(self.wire(),self.schema,(self.task,self.views))
        self.assertEqual(result,{'evidence_specs':[]})
        wire=self.wire();wire['numeric_requirements']['numeric_1']['reason']=' '
        with self.assertRaises(ContractError):decode_original_spans(wire,self.schema,(self.task,self.views))

    def test_found_numeric_round_trips_literal_value_unit_and_period(self):
        slot=numeric_slots(self.task)[0];wire=self.wire()
        wire['numeric_requirements'][slot['key']]=dict(status='found',items=[self.number(slot)])
        spec=decode_original_spans(wire,self.schema,(self.task,self.views))['evidence_specs'][0]
        self.assertEqual((spec['value'],spec['unit'],spec['period']),(0.15,'%','2026-01-01/2026-06-30'))
        self.assertEqual(spec['review']['evidence_need_texts'],{slot['question_id']:[slot['text']]})
        self.assertNotIn('source_check',spec)
        self.assertNotIn('applicability_check',spec)

    def test_empty_found_text_kind_and_other_need_link_are_rejected(self):
        slot=numeric_slots(self.task)[0]
        for change in ('empty','text','other_need'):
            wire=self.wire();item=self.number(slot)
            if change=='text':item['kind']='text'
            if change=='other_need':item['review']['need_links']=[dict(question_id=slot['question_id'],need_ids=[slot['need_ref']])]
            wire['numeric_requirements'][slot['key']]=dict(status='found',items=[] if change=='empty' else [item])
            with self.assertRaises(ContractError):decode_original_spans(wire,self.schema,(self.task,self.views))

    def test_old_schema_and_actual_original_export_remain_unchanged(self):
        old=original_span_output_schema(self.old,self.views)
        self.assertNotIn('numeric_requirements',old['properties'])
        encoded=provider_request_schema(self.task,self.schema,ORIGINAL_SPAN_PROTOCOL)
        self.assertIn('numeric_requirements',encoded['properties'])
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),self.original_hash)

    def test_literal_fee_navigation_reads_actual_missing_rates_without_adding_methods(self):
        old=self.old['reasoning_state']['reading']['bundle']['views']
        self.assertFalse(any('0.15%' in p['text'] for view in old for p in view['parts']))
        for view in self.views:
            text='\n'.join(p['text'] for p in view['parts'])
            self.assertIn('0.15%',text)
            self.assertIn('0.05%',text)
        self.assertEqual(self.task['questions'],self.old['questions'])
        self.assertEqual(self.task['calculation_specs'],self.old['calculation_specs'])
        rule=self.task['reasoning_state']['reading']['bundle']['concept_navigation']['rule']
        self.assertFalse(rule['financial_approval'] or rule['automatic_need_coverage'])
        self.assertIn('年费率',rule['terms'])

    def test_numeric_owner_is_declared_once_and_explicit_codes_cannot_cross_funds(self):
        for slot in numeric_slots(self.task):
            field=self.schema['properties']['numeric_requirements']['properties'][slot['key']]
            expected=next(code for code in ('510300.SH','510500.SH') if code in slot['text'])
            for branch in field['anyOf'][0]['properties']['items']['items']['anyOf']:
                properties=branch['properties']
                self.assertEqual(properties['object']['enum'],[expected])
                self.assertNotIn('need_links',properties['review']['properties'])
        legacy=copy.deepcopy(self.task);legacy['typed_numeric_selection_version']=1
        legacy['input_hash']=input_fingerprint(legacy)
        shape=original_span_output_schema(legacy,self.views)
        choice=shape['properties']['numeric_requirements']['properties']['numeric_1']['anyOf'][0]
        self.assertIn('need_links',choice['properties']['items']['items']['anyOf'][0]['properties']['review']['properties'])


if __name__=='__main__':unittest.main()
