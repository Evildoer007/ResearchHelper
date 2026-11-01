"""Current original selection keeps operating metrics and exact wire fields.

Retained downloads are read on isolated copies. No study is resumed, source
approved, response repaired, or external request made by these tests.
"""
import copy
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from tests.test_asset_data_acceptance_review import load_retained_task
from tests import test_current_official_data_protocols as text_helpers
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.planning import input_fingerprint, question_specs
from researchhelper.reports import compose
from researchhelper.reasoning import evidence_contract,model_object,MAX_CONTEXT_CHARACTERS
from researchhelper.output_schema import (_original_span_choices,original_span_output_schema,
    original_span_prompt,provider_request_schema,ORIGINAL_SPAN_PROTOCOL)


POLICY='original_number_catalogue_capacity_version'


def payload(task,views):
    return dict(question=task['question'],intent=task['intent'],objects=task['objects'],cutoff=task['cutoff'],
        questions=question_specs(task),calculations=task['calculation_specs'],untrusted_sources=views,
        evidence_contract=evidence_contract(task,views),field_registry={},source_reading=None)


class OperatingOriginalProtocolTests(unittest.TestCase):
    def setUp(self):
        self.original=load_retained_task(ROOT/'result/acceptance/autonomous-data-phase-fe8o_n2b')
        self.before=fingerprint(self.original)
        self.views=self.original['reasoning_state']['reading']['bundle']['views']
        self.task=copy.deepcopy(self.original)
        # Explicit-code offline fixture: changing a policy invalidates the old
        # name-discovery input hash. Do not forge a fresh name-query receipt.
        # Real re-runs remain name-only and must acquire their own receipts.
        self.task['objects']=['300750.SZ']
        self.task[POLICY]=1;self.task['input_hash']=input_fingerprint(self.task)

    def tearDown(self):
        self.assertEqual(fingerprint(load_retained_task(ROOT/'result/acceptance/autonomous-data-phase-fe8o_n2b')),self.before)

    def test_new_bounded_catalogue_keeps_all_2104_tokens_not_a_truncated_subset(self):
        self.assertIsNone(_original_span_choices(self.original,self.views))
        choices=_original_span_choices(self.task,self.views)
        self.assertIsNotNone(choices)
        self.assertEqual(len(choices[0]['number_tokens']),2104)
        self.assertIsNotNone(original_span_output_schema(self.task,self.views))

    def test_complete_operating_table_and_other_read_text_fit_the_same_context_limit(self):
        schema=original_span_output_schema(self.task,self.views)
        shown=original_span_prompt(payload(self.task,self.views),self.task)
        wire=provider_request_schema(self.task,schema,ORIGINAL_SPAN_PROTOCOL)
        self.assertLessEqual(len(canonical(shown))+len(canonical(wire)),MAX_CONTEXT_CHARACTERS)
        for original,projected in zip(self.views,shown['untrusted_sources']):
            parts=([dict(zip(projected['part_columns'],row)) for row in projected['parts']]
                   if 'part_columns' in projected else projected['parts'])
            self.assertEqual(len(parts),len(original['parts']))
            for old,new in zip(original['parts'],parts):
                text=''.join(line[1] for line in new['indexed_lines'])
                if projected.get('literal_encoding'):
                    text=text_helpers.CurrentOfficialDataProtocolTests.restore_marked_text(text)
                self.assertEqual(text,old['text'])
        self.assertTrue(any('电池系统（GWh）' in part['text'] and '产能利用率' in part['text']
                            for view in self.views for part in view['parts']))

    def test_over_limit_and_unregistered_policies_do_not_truncate_or_fall_back(self):
        with patch('researchhelper.output_schema._original_number_tokens',return_value=[{}]*8193):
            with self.assertRaisesRegex(ContractError,'8192'):_original_span_choices(self.task,self.views)
        for policy in (True,'1',2):
            task=dict(self.task);task[POLICY]=policy
            with self.assertRaises(ContractError):_original_span_choices(task,self.views)

    def test_version12_instructions_only_advertise_current_selection_fields(self):
        task=load_retained_task(ROOT/'result/acceptance/autonomous-data-phase-9rmxvnaf')
        views=task['reasoning_state']['reading']['bundle']['views']
        calls=[]
        def capture(task,messages,views):
            instructions='\n'.join(m['content'] for m in messages[:-1])
            calls.append(instructions)
            self.assertNotIn('question_ids、period、observation_anchor',instructions)
            self.assertIn('本次每种kind的选择项只允许以下字段',instructions)
            self.assertIn('temporal_reference',instructions)
            raise RuntimeError('offline_wire_capture_only')
        model=SimpleNamespace(supports_structured_analysis=lambda:True,supports_financial_references=lambda:True,
            reason_structured_original_spans=capture,reason_structured_extraction=capture)
        with self.assertRaisesRegex(RuntimeError,'offline_wire_capture_only'):
            model_object(model,task,'extract',payload(task,views),{'evidence_specs'})
        self.assertEqual(len(calls),1)

    def test_new_policy_is_input_bound_and_frozen_without_upgrading_old_tasks(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance') as directory:
            agent=ResearchAgent(ResearchArchive(Path(directory)),ROOT,offline=True,require_argument_support=True)
            try:
                task=agent.create('解释ETF折溢价',entry='ETF',questions=[dict(id='q1',kind='fact',
                    question='解释ETF折溢价',answer_scope='general_definition',knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task[POLICY],1)
                old=dict(task);old.pop(POLICY)
                self.assertNotEqual(input_fingerprint(old),task['input_hash'])
                self.assertEqual(compose(agent.start(task['id']))[POLICY],1)
            finally:agent.close_runtime()
