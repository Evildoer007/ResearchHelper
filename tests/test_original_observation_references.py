"""Exact original-date short references; no financial evidence is approved."""
import copy
import gzip
import hashlib
import json
import unittest

from tests import ROOT
from tests.test_operating_original_protocol import payload
from researchhelper.contracts import ContractError
from researchhelper.planning import input_fingerprint
from researchhelper.output_schema import (_original_span_choices, original_observation_choices,
    original_span_output_schema, original_span_prompt, decode_original_spans)


class OriginalObservationReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path=ROOT/'result/acceptance/autonomous-data-phase-5m6zeru2/task.json.gz'
        cls.original_hash=hashlib.sha256(cls.path.read_bytes()).hexdigest()
        with gzip.open(cls.path,'rt',encoding='utf-8') as stream:cls.old=json.load(stream)
        cls.task=copy.deepcopy(cls.old);cls.task['original_observation_reference_version']=1
        cls.task['input_hash']=input_fingerprint(cls.task)
        cls.views=cls.task['reasoning_state']['reading']['bundle']['views']
        cls.choices=_original_span_choices(cls.task,cls.views)
        cls.schema=original_span_output_schema(cls.task,cls.views)

    def wire(self):
        choice=self.choices[0]
        props=next(v['properties'] for v in self.schema['properties']['selections']['items']['anyOf']
                   if v['properties']['kind']['enum']==['text'] and
                   v['properties']['source_ref']['enum']==[choice['source_ref']])
        link=props['review']['properties']['need_links']['items']['anyOf'][0]['properties']
        anchor=original_observation_choices(choice)[0]
        return dict(version=12,binding_ref=self.schema['properties']['binding_ref']['enum'][0],
            selections=[dict(id='date_reference_test',object=props['object']['enum'][0],nature='已实现事实',
                kind='text',source_ref=choice['source_ref'],text_reference={'line':0},
                temporal_reference={'observation_ref':anchor['reference']},review=dict(
                    scope='current_original_descriptive_only',need_links=[dict(question_id=link['question_id']['enum'][0],
                    need_ids=[link['need_ids']['items']['enum'][0]])]))])

    def test_references_cover_only_original_eligible_indices_and_are_source_bound(self):
        sets=[]
        for choice in self.choices:
            refs=original_observation_choices(choice)
            self.assertEqual([r['index'] for r in refs],choice['observation_indices'])
            self.assertEqual(len({r['reference'] for r in refs}),len(refs))
            for ref in refs:
                date=choice['description']['dates'][ref['index']]
                expected=date.get('observation_period',{}).get('value',date.get('date'))
                self.assertEqual(ref['period'],expected)
            sets.append({r['reference'] for r in refs})
        self.assertFalse(sets[0].intersection(sets[1]))

    def test_literal_period_round_trips_without_source_approval(self):
        result=decode_original_spans(self.wire(),self.schema,(self.task,self.views))
        item=result['evidence_specs'][0]
        self.assertEqual(item['period'],original_observation_choices(self.choices[0])[0]['period'])
        self.assertNotIn('source_check',item)
        self.assertNotIn('applicability_check',item)

    def test_integer_row_and_other_source_reference_are_rejected(self):
        for temporal in ({'observation_anchor':0}, {'observation_ref':'observation_unknown'},
                         {'observation_ref':original_observation_choices(self.choices[1])[0]['reference']}):
            wire=self.wire();wire['selections'][0]['temporal_reference']=temporal
            with self.assertRaises(ContractError):
                decode_original_spans(wire,self.schema,(self.task,self.views))

    def test_prompt_preserves_source_body_and_date_tables(self):
        before=original_span_prompt(payload(self.old,self.views),self.old)
        after=original_span_prompt(payload(self.task,self.views),self.task)
        for old,new,choice in zip(before['untrusted_sources'],after['untrusted_sources'],self.choices):
            self.assertEqual(new['observation_choices'],original_observation_choices(choice))
            for key in ('parts','date_anchor_tables','date_part_labels','unit_references','non_observation_dates'):
                self.assertEqual(old.get(key),new.get(key))
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),self.original_hash)


if __name__=='__main__':unittest.main()
