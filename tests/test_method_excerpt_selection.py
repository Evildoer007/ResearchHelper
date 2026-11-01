"""Explicit multi-line numeric excerpts, with unchanged financial checks."""
import copy
import hashlib
import json
import unittest

from tests import ROOT
from tests import test_original_interval_context as interval
from researchhelper.contracts import ContractError,canonical
from researchhelper.evidence import candidate_evidence,validate_calculation_context_basis
from researchhelper.output_schema import (_original_span_choices,original_span_output_schema,
    decode_original_spans,original_span_prompt,provider_request_schema,ORIGINAL_SPAN_PROTOCOL)
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS
from tests.probe_etf_extraction_context import payload_for

POLICY='method_excerpt_selection_version'


class MethodExcerptSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.retained=ROOT/'result/acceptance/autonomous-data-phase-h7v8ptz5/task.json'
        cls.digest=hashlib.sha256(cls.retained.read_bytes()).hexdigest()
        cls.task=json.loads(cls.retained.read_text())
        cls.task[POLICY]=1
        cls.task['input_hash']=input_fingerprint(cls.task)
        cls.views=cls.task['reasoning_state']['reading']['bundle']['views']
        cls.choices=_original_span_choices(cls.task,cls.views)
        cls.schema=original_span_output_schema(cls.task,cls.views)

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(cls.retained.read_bytes()).hexdigest()==cls.digest

    def wire(self,index=0):
        wire=interval.OriginalIntervalContextTests.wire(self,index)
        choice=self.choices[index]
        item=wire['selections'][0]
        line=next(row['line'] for row in choice['number_tokens']
                  if row['ref']==item['number_reference']['literal_ref'])
        span=dict(start=line,end=line+1)
        self.assertEqual(choice['lines'][line]['part'],choice['lines'][line+1]['part'])
        item['number_reference']['text_span']=span
        for entry in item['contexts']:
            if entry['field'] in ('metric','rate_period'):
                entry['span']=copy.deepcopy(span)
        return wire

    def decode(self,wire):
        return decode_original_spans(wire,self.schema,(self.task,self.views))['evidence_specs'][0]

    def test_explicit_excerpt_keeps_selected_lines_and_complete_fee_context(self):
        for index in (0,1):
            wire=self.wire(index);spec=self.decode(wire)
            point=wire['selections'][0]['number_reference']['text_span']
            from researchhelper.financial_sources import selected_original_span
            self.assertEqual(spec['text'],selected_original_span(self.choices[index]['lines'],point)['quote'])
            self.assertIn(spec['calculation_context_basis']['metric']['quote'],spec['text'])
            source=next(row for row in self.task['source_records'] if row['id']==spec['artifact_id'])
            item=candidate_evidence(self.task,source,spec)
            validate_calculation_context_basis(item,self.task['source_records'])
            self.assertEqual((item['value'],item['unit']),(0.15,'%'))

    def test_truncated_excerpt_does_not_borrow_context_from_another_line(self):
        wire=self.wire();span=wire['selections'][0]['number_reference']['text_span'];span['end']=span['start']
        spec=self.decode(wire)
        source=next(row for row in self.task['source_records'] if row['id']==spec['artifact_id'])
        with self.assertRaisesRegex(ContractError,'费用类别依据须位于'):
            candidate_evidence(self.task,source,spec)

    def test_number_outside_explicit_excerpt_is_not_moved_into_it(self):
        wire=self.wire();span=wire['selections'][0]['number_reference']['text_span'];span['start']+=1
        with self.assertRaises(ContractError):self.decode(wire)

    def test_omitted_or_invalid_excerpt_is_not_repaired(self):
        for replacement in (None,dict(start=True,end=0),dict(start=99999,end=99999)):
            wire=self.wire();reference=wire['selections'][0]['number_reference']
            if replacement is None:reference.pop('text_span')
            else:reference['text_span']=replacement
            with self.assertRaises(ContractError):self.decode(wire)

    def test_old_policy_keeps_its_original_schema_and_input_identity(self):
        old=copy.deepcopy(self.task);old.pop(POLICY);old['input_hash']=input_fingerprint(old)
        schema=original_span_output_schema(old,self.views)
        self.assertNotEqual(old['input_hash'],self.task['input_hash'])
        for version in (True,False,0,2,1.0):
            other=dict(old,**{POLICY:version});other['input_hash']=input_fingerprint(other)
            actual=original_span_output_schema(other,self.views)
            # The request binding includes input identity; compare the
            # complete selection grammar, not another input's binding hash.
            self.assertEqual(schema['properties']['selections'],actual['properties']['selections'])

    def test_four_actual_originals_still_fit_without_trimming_or_extra_budget(self):
        task=json.loads((ROOT/'result/acceptance/autonomous-data-phase-yr9yq5lu/task.json').read_text())
        task.update({POLICY:1,'structured_schema_sharing_version':1})
        task['input_hash']=input_fingerprint(task)
        payload=payload_for(task);schema=original_span_output_schema(task,payload['untrusted_sources'])
        projected=original_span_prompt(payload,task)
        wire=provider_request_schema(task,schema,ORIGINAL_SPAN_PROTOCOL)
        self.assertEqual(len(projected['untrusted_sources']),4)
        self.assertEqual(projected['questions'],payload['questions'])
        self.assertLessEqual(len(canonical(projected))+len(canonical(wire)),MAX_CONTEXT_CHARACTERS)
        self.assertEqual(task['ledger']['limits']['model_calls'],8)
