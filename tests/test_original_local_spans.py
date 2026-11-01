"""Page-local addresses preserve source bytes and cannot bridge read windows."""
import copy
import json
import unittest

from tests import ROOT
from tests import test_method_excerpt_selection as previous
from tests.probe_etf_extraction_context import payload_for
from researchhelper.contracts import ContractError,canonical
from researchhelper.output_schema import (_original_span_choices,_original_span_schema,
    _local_span_address,_expand_local_span,_span_fragments,original_span_output_schema,
    original_span_prompt,decode_original_spans,provider_request_schema,ORIGINAL_SPAN_PROTOCOL)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS

POLICY='original_local_span_version'


class OriginalLocalSpanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        previous.MethodExcerptSelectionTests.setUpClass()
        cls.base=previous.MethodExcerptSelectionTests('test_explicit_excerpt_keeps_selected_lines_and_complete_fee_context')
        cls.task=copy.deepcopy(cls.base.task);cls.task[POLICY]=1
        cls.task['input_hash']=input_fingerprint(cls.task)
        cls.views=cls.base.views
        cls.choices=_original_span_choices(cls.task,cls.views)
        cls.schema=original_span_output_schema(cls.task,cls.views)

    @classmethod
    def tearDownClass(cls):
        previous.MethodExcerptSelectionTests.tearDownClass()

    def wire(self,index=0):
        wire=self.base.wire(index)
        def local(value):
            if isinstance(value,dict):
                if set(value)=={'start','end'}:return _local_span_address(self.choices[index],value)
                return {key:local(item) for key,item in value.items()}
            if isinstance(value,list):return [local(item) for item in value]
            return value
        wire=local(wire)
        wire['binding_ref']=self.schema['properties']['binding_ref']['enum'][0]
        return wire

    def decode(self,wire):
        return decode_original_spans(wire,self.schema,(self.task,self.views))['evidence_specs'][0]

    def test_same_explicit_excerpt_restores_identical_complete_evidence(self):
        for index in (0,1):
            self.assertEqual(self.decode(self.wire(index)),self.base.decode(self.base.wire(index)))

    def test_each_actual_read_window_roundtrips_its_own_line_addresses(self):
        for choice in self.choices:
            for part,lines in _span_fragments(choice).items():
                for offset,line in enumerate(lines):
                    point=dict(start=line['index'],end=line['index'])
                    local=_local_span_address(choice,point)
                    self.assertEqual(local,dict(part=part,start=offset,end=offset))
                    self.assertEqual(_expand_local_span(choice,local),point)

    def test_out_of_window_reversed_unknown_and_boolean_addresses_are_not_repaired(self):
        choice=self.choices[0];part,lines=next(iter(_span_fragments(choice).items()))
        for span in (dict(part=part,start=0,end=len(lines)),dict(part=part,start=1,end=0),
                     dict(part=99999,start=0,end=0),dict(part=True,start=0,end=0),
                     dict(start=0,end=0)):
            with self.assertRaises(ContractError):_expand_local_span(choice,span)

    def test_two_windows_from_the_same_physical_page_still_cannot_be_joined(self):
        choice=dict(lines=[dict(index=0,fragment_index=0,part='第1页',text='甲\n'),
                           dict(index=1,fragment_index=1,part='第1页',text='乙\n')])
        with self.assertRaises(ContractError):_expand_local_span(choice,dict(part=0,start=0,end=1))
        with self.assertRaises(ContractError):_local_span_address(choice,dict(start=0,end=1))
        self.assertEqual(_expand_local_span(choice,dict(part=1,start=0,end=0)),dict(start=1,end=1))

    def test_numeric_reference_cannot_borrow_another_part_or_drop_part(self):
        wire=self.wire();span=wire['selections'][0]['number_reference']['text_span']
        span['part']=next(part for part,lines in _span_fragments(self.choices[0]).items()
                          if part!=span['part'] and len(lines)>span['end'])
        with self.assertRaises(ContractError):self.decode(wire)
        wire=self.wire();wire['selections'][0]['number_reference']['text_span'].pop('part')
        with self.assertRaises(ContractError):self.decode(wire)

    def test_presentation_preserves_every_character_and_character_based_publication_offsets(self):
        payload=payload_for(self.task);before=canonical(payload)
        local=original_span_prompt(payload,self.task)
        old_task=copy.deepcopy(self.task);old_task.pop(POLICY);old_task['input_hash']=input_fingerprint(old_task)
        old=original_span_prompt(payload_for(old_task),old_task)
        self.assertEqual(local['questions'],old['questions'])
        self.assertEqual(local['calculations'],old['calculations'])
        for new_view,old_view in zip(local['untrusted_sources'],old['untrusted_sources']):
            for new_part,old_part in zip(new_view['parts'],old_view['parts']):
                self.assertEqual([row[1:] for row in new_part['indexed_lines']],
                                 [row[1:] for row in old_part['indexed_lines']])
                self.assertEqual([row[0] for row in new_part['indexed_lines']],list(range(len(new_part['indexed_lines']))))
        for new_source,old_source in zip(local['evidence_contract']['sources'],old['evidence_contract']['sources']):
            self.assertEqual(new_source.get('publication_candidates'),old_source.get('publication_candidates'))
        self.assertEqual(canonical(payload),before)

    def test_old_tasks_and_invalid_policy_values_keep_the_original_grammar(self):
        self.assertNotEqual(input_fingerprint(self.base.task),self.task['input_hash'])
        for version in (True,False,0,2,1.0):
            task=dict(self.base.task,**{POLICY:version});task['input_hash']=input_fingerprint(task)
            self.assertEqual(original_span_output_schema(task,self.views)['properties']['selections'],
                             self.base.schema['properties']['selections'])

    def test_four_originals_and_failed_packaged_input_fit_without_trimming_or_larger_budget(self):
        for name in ('autonomous-data-phase-yr9yq5lu','one-sentence-research-a0io_uh2'):
            path=ROOT/'result/acceptance'/name/'task.json';original=path.read_bytes()
            task=json.loads(original);task.update({POLICY:1,'structured_schema_sharing_version':1,
                'method_excerpt_selection_version':1,'method_literal_address_version':1})
            task['input_hash']=input_fingerprint(task);payload=payload_for(task)
            schema=original_span_output_schema(task,payload['untrusted_sources'])
            projected=original_span_prompt(payload,task)
            self.assertEqual(projected['questions'],payload['questions'])
            self.assertEqual(len(projected['untrusted_sources']),len(payload['untrusted_sources']))
            self.assertLessEqual(len(canonical(projected))+len(canonical(provider_request_schema(
                task,schema,ORIGINAL_SPAN_PROTOCOL))),MAX_CONTEXT_CHARACTERS)
            self.assertEqual(task['ledger']['limits'],dict(data_requests=100,model_calls=8,seconds=600))
            self.assertEqual(path.read_bytes(),original)
