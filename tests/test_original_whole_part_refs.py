"""Explicit complete-window aliases equal existing valid precise ranges."""
import copy
import json
import unittest

from tests import ROOT
from tests import test_method_excerpt_selection as previous
from tests.probe_etf_extraction_context import payload_for
from researchhelper.contracts import ContractError,canonical
from researchhelper.evidence import candidate_evidence,validate_calculation_context_basis
from researchhelper.output_schema import (_original_span_choices,_original_span_schema,_span_fragments,
    _local_span_address,_expand_local_span,original_span_output_schema,original_span_prompt,
    decode_original_spans,provider_request_schema,ORIGINAL_SPAN_PROTOCOL)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS

POLICY='original_whole_part_selection_version'


class OriginalWholePartReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        previous.MethodExcerptSelectionTests.setUpClass()
        cls.base=previous.MethodExcerptSelectionTests('test_explicit_excerpt_keeps_selected_lines_and_complete_fee_context')
        cls.task=copy.deepcopy(cls.base.task);cls.task.update(original_local_span_version=1,**{POLICY:1})
        cls.task['input_hash']=input_fingerprint(cls.task);cls.views=cls.base.views
        cls.choices=_original_span_choices(cls.task,cls.views)
        cls.schema=original_span_output_schema(cls.task,cls.views)

    @classmethod
    def tearDownClass(cls):previous.MethodExcerptSelectionTests.tearDownClass()

    def wire(self,index=0,whole=True):
        choice=self.choices[index]
        def address(value):
            if isinstance(value,dict):
                if set(value)=={'start','end'}:
                    point=_local_span_address(choice,value)
                    if whole:return dict(part_ref=point['part'])
                    lines=_span_fragments(choice)[point['part']]
                    return dict(part=point['part'],start=0,end=len(lines)-1)
                return {key:address(item) for key,item in value.items()}
            if isinstance(value,list):return [address(item) for item in value]
            return value
        wire=address(self.base.wire(index));wire['binding_ref']=self.schema['properties']['binding_ref']['enum'][0]
        return wire

    def decode(self,wire):
        return decode_original_spans(wire,self.schema,(self.task,self.views))['evidence_specs'][0]

    def test_alias_restores_exactly_the_same_evidence_as_explicit_full_window_range(self):
        for index in (0,1):
            spec=self.decode(self.wire(index))
            self.assertEqual(spec,self.decode(self.wire(index,whole=False)))
            source=next(s for s in self.task['source_records'] if s['id']==spec['artifact_id'])
            item=candidate_evidence(self.task,source,spec)
            validate_calculation_context_basis(item,self.task['source_records'])
            self.assertEqual((item['value'],item['unit']),(0.15,'%'))

    def test_every_presented_part_has_a_visible_stable_reference_without_counting(self):
        payload=payload_for(self.task);before=canonical(payload)
        shown=original_span_prompt(payload,self.task)
        for view in shown['untrusted_sources']:
            self.assertEqual([p['part_ref'] for p in view['parts']],list(range(len(view['parts']))))
            for part in view['parts']:
                self.assertEqual([row[0] for row in part['indexed_lines']],list(range(len(part['indexed_lines']))))
        self.assertEqual(canonical(payload),before)

    def test_alias_does_not_expand_the_two_hundred_line_limit(self):
        lines=[dict(index=i,part='段落1',fragment_index=0,text='实际读取行\n') for i in range(201)]
        choice=dict(lines=lines)
        with self.assertRaises(ContractError):_expand_local_span(choice,dict(part_ref=0))
        schema=_original_span_schema(self.task,choice)
        self.assertNotIn('anyOf',schema)
        self.assertEqual(_expand_local_span(choice,dict(part=0,start=0,end=199)),dict(start=0,end=199))

    def test_unknown_boolean_or_cross_part_number_reference_is_not_repaired(self):
        for value in (99999,True,'0'):
            with self.assertRaises(ContractError):_expand_local_span(self.choices[0],dict(part_ref=value))
        wire=self.wire();span=wire['selections'][0]['number_reference']['text_span']
        span['part_ref']=next(p for p,lines in _span_fragments(self.choices[0]).items()
                              if p!=span['part_ref'] and len(lines)<=200)
        with self.assertRaises(ContractError):self.decode(wire)

    def test_old_local_tasks_and_invalid_flags_keep_their_original_grammar(self):
        task=copy.deepcopy(self.task);task.pop(POLICY);task['input_hash']=input_fingerprint(task)
        expected=original_span_output_schema(task,self.views)['properties']['selections']
        self.assertNotEqual(task['input_hash'],self.task['input_hash'])
        self.assertNotEqual(expected,self.schema['properties']['selections'])
        for value in (True,False,0,2,1.0):
            old=dict(task,**{POLICY:value});old['input_hash']=input_fingerprint(old)
            self.assertEqual(expected,original_span_output_schema(old,self.views)['properties']['selections'])

    def test_originals_obligations_and_default_budget_survive_actual_large_inputs(self):
        for name in ('autonomous-data-phase-yr9yq5lu','one-sentence-research-a0io_uh2',
                     'one-sentence-research-fnr64tej'):
            path=ROOT/'result/acceptance'/name/'task.json';raw=path.read_bytes();task=json.loads(raw)
            task.update(original_local_span_version=1,structured_schema_sharing_version=1,
                method_excerpt_selection_version=1,method_literal_address_version=1,**{POLICY:1})
            task['input_hash']=input_fingerprint(task);payload=payload_for(task)
            shown=original_span_prompt(payload,task);schema=original_span_output_schema(task,payload['untrusted_sources'])
            self.assertEqual(shown['questions'],payload['questions'])
            self.assertEqual(len(shown['untrusted_sources']),len(payload['untrusted_sources']))
            self.assertLessEqual(len(canonical(shown))+len(canonical(provider_request_schema(
                task,schema,ORIGINAL_SPAN_PROTOCOL))),MAX_CONTEXT_CHARACTERS)
            self.assertEqual(task['ledger']['limits'],dict(data_requests=100,model_calls=8,seconds=600))
            self.assertEqual(path.read_bytes(),raw)
