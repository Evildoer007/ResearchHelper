"""Locked conceptual search terms point to real read text, never approve facts."""
import copy
import json
import re
import unittest
from unittest.mock import patch

from tests import ROOT
from tests.probe_etf_extraction_context import payload_for
from researchhelper.contracts import ContractError,canonical
from researchhelper.knowledge import literal_navigation_rule
from researchhelper.output_schema import (_original_span_choices,_expand_local_span,_present_concept_navigation,
    original_span_prompt,original_span_output_schema,provider_request_schema,ORIGINAL_SPAN_PROTOCOL)
from researchhelper.financial_sources import selected_original_span
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS

POLICY='original_concept_navigation_version'


class OriginalConceptNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path=ROOT/'result/acceptance/one-sentence-research-onlmhtfa/task.json'
        cls.raw=cls.path.read_bytes();cls.task=json.loads(cls.raw)
        cls.task[POLICY]=1;cls.task['input_hash']=input_fingerprint(cls.task)
        cls.views=cls.task['reasoning_state']['reading']['bundle']['views']

    @classmethod
    def tearDownClass(cls):assert cls.path.read_bytes()==cls.raw

    def test_keyword_addresses_reconstruct_actual_same_window_text_for_both_funds(self):
        payload=payload_for(self.task);before=canonical(payload)
        shown=original_span_prompt(payload,self.task);rule=shown['concept_navigation']
        self.assertFalse(rule['financial_approval'])
        choices={c['source_ref']:c for c in _original_span_choices(self.task,self.views)}
        for view in shown['untrusted_sources']:
            self.assertTrue(view['concept_hits'])
            for term,part,start,end in view['concept_hits']:
                span=_expand_local_span(choices[view['source_ref']],dict(part=part,start=start,end=end))
                quote=selected_original_span(choices[view['source_ref']]['lines'],span)['quote']
                self.assertIn(rule['terms'][term],re.sub(r'\s+','',quote))
        self.assertEqual(canonical(payload),before)

    def test_untrusted_text_negation_is_preserved_and_nothing_is_inferred_from_absence(self):
        rule=dict(version=1,terms=['本基金跟踪'],financial_approval=False,selection='first_hit_per_term')
        choice=dict(source_ref='s',kind='original',lines=[
            dict(index=0,fragment_index=0,part='段1',text='该表述未生效。\n'),
            dict(index=1,fragment_index=0,part='段1',text='并非本基金跟踪任何已确认标的。\n')])
        with patch('researchhelper.knowledge.literal_navigation_rule',return_value=rule):
            prompt=_present_concept_navigation(dict(untrusted_sources=[dict(source_ref='s')]),{},[choice])
            self.assertEqual(prompt['untrusted_sources'][0]['concept_hits'],[[0,0,1]])
            quote=selected_original_span(choice['lines'],dict(start=0,end=1))['quote']
            self.assertIn('未生效',quote);self.assertIn('并非',quote)
            choice['lines']=choice['lines'][:1]
            absent=_present_concept_navigation(dict(untrusted_sources=[dict(source_ref='s')]),{},[choice])
            self.assertNotIn('concept_navigation',absent)

    def test_old_policy_and_missing_knowledge_do_not_get_new_navigation(self):
        for version in (None,True,False,0,2,1.0):
            task=dict(self.task);task.pop(POLICY,None)
            if version is not None:task[POLICY]=version
            task['input_hash']=input_fingerprint(task)
            shown=original_span_prompt(payload_for(task),task)
            self.assertNotIn('concept_navigation',shown)
        self.assertIsNone(literal_navigation_rule({POLICY:1}))

    def test_navigation_changes_only_derived_addresses_not_originals_or_obligations(self):
        old=dict(self.task);old.pop(POLICY);old['input_hash']=input_fingerprint(old)
        first=original_span_prompt(payload_for(old),old)
        second=original_span_prompt(payload_for(self.task),self.task)
        self.assertEqual(first['questions'],second['questions'])
        self.assertEqual(first['calculations'],second['calculations'])
        for a,b in zip(first['untrusted_sources'],second['untrusted_sources']):
            self.assertEqual(a['parts'],b['parts'])
            self.assertEqual(a['snapshot_hash'],b['snapshot_hash'])
        self.assertNotEqual(old['input_hash'],self.task['input_hash'])

    def test_stale_task_input_cannot_reuse_a_locked_knowledge_navigation_rule(self):
        bad=dict(self.task,input_hash='stale')
        with self.assertRaises(ContractError):literal_navigation_rule(bad)

    def test_four_actual_originals_fit_without_removing_source_characters_or_methods(self):
        path=ROOT/'result/acceptance/autonomous-data-phase-yr9yq5lu/task.json';raw=path.read_bytes()
        task=json.loads(raw);task.update({POLICY:1,'original_local_span_version':1,
            'original_whole_part_selection_version':1,'method_excerpt_selection_version':1,
            'method_literal_address_version':1,'structured_schema_sharing_version':1})
        task['input_hash']=input_fingerprint(task);payload=payload_for(task)
        shown=original_span_prompt(payload,task);schema=original_span_output_schema(task,payload['untrusted_sources'])
        self.assertEqual(len(shown['untrusted_sources']),4)
        self.assertEqual(shown['questions'],payload['questions'])
        self.assertLessEqual(len(canonical(shown))+len(canonical(provider_request_schema(task,schema,ORIGINAL_SPAN_PROTOCOL))),MAX_CONTEXT_CHARACTERS)
        self.assertEqual(task['ledger']['limits'],dict(data_requests=100,model_calls=8,seconds=600))
        self.assertEqual(path.read_bytes(),raw)
