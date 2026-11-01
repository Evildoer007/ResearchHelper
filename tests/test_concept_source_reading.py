"""Read locked concept locations without facts, network or old-task mutation."""
import copy
import json
import re
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import ContractError,canonical
from researchhelper.planning import input_fingerprint
from researchhelper.source_reader import (binding,concept_reader_queries,concept_reader_rule,
    read,reader_version_for,validate_reading,CONTEXT_LIMIT,MAX_QUERY_CANDIDATES)
from researchhelper.output_schema import original_span_prompt,original_span_output_schema,provider_request_schema,ORIGINAL_SPAN_PROTOCOL
from researchhelper.reasoning import MAX_CONTEXT_CHARACTERS
from tests.probe_etf_extraction_context import payload_for

POLICY='original_concept_reading_version'


class ConceptSourceReadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        network=patch('socket.socket.connect',side_effect=AssertionError('Offline reading replay'))
        network.start();cls.addClassCleanup(network.stop)
        cls.path=ROOT/'result/acceptance/one-sentence-research-x00mmulk/task.json'
        cls.raw=cls.path.read_bytes();cls.retained=json.loads(cls.raw)
        cls.task=copy.deepcopy(cls.retained);cls.task[POLICY]=1
        cls.task['input_hash']=input_fingerprint(cls.task)
        cls.old_bundle=cls.retained['reasoning_state']['reading']['bundle']
        cls.bundle=read(cls.task,cls.old_bundle['selection'],binding(cls.task),cls.old_bundle['artifact_ids'])

    @classmethod
    def tearDownClass(cls):assert cls.path.read_bytes()==cls.raw

    def test_same_actual_selection_now_reads_direct_tracking_clauses(self):
        self.assertEqual(self.bundle['reader_version'],6)
        self.assertEqual(self.bundle['selection'],self.old_bundle['selection'])
        self.assertEqual(self.bundle['artifact_ids'],self.old_bundle['artifact_ids'])
        self.assertLessEqual(len(canonical(self.bundle['views'])),CONTEXT_LIMIT)
        found=[]
        for view in self.bundle['views']:
            text=re.sub(r'\s+','',''.join(part['text'] for part in view.get('parts',[])))
            if '以标的指数沪深300' in text:found.append('510300')
            if '本基金跟踪沪深300' in text:found.append('159919')
        self.assertEqual(sorted(set(found)),['159919','510300'])
        navigation=self.bundle['concept_navigation']
        self.assertFalse(navigation['financial_approval'])
        self.assertFalse(navigation['completeness_approved'])
        self.assertEqual(navigation['shared_query_candidate_limit'],MAX_QUERY_CANDIDATES)
        for row in self.bundle['coverage']:
            if row.get('mode')=='full_api':continue
            query=next(q for q in navigation['queries'] if q['artifact_id']==row['artifact_id'])
            self.assertTrue(set(query['terms']).issubset(row['method_queries']['terms']))
            self.assertFalse(row['method_queries']['financial_approval'])
            self.assertGreater(row['provided_candidate_counts']['exception'],0)
        self.assertEqual(self.task['question'],self.retained['question'])
        self.assertEqual(self.task['ledger'],self.retained['ledger'])
        self.assertEqual(self.task['data_need_specs'],self.retained['data_need_specs'])

    def test_excerpts_are_exact_bounded_originals_and_rebuild_deterministically(self):
        records={s['id']:s for s in self.task['source_records']}
        for view in self.bundle['views']:
            source=records[view['id']]['snapshot']['material']
            originals={p['locator']:p['text'] for p in source['parts']}
            for part in view['parts']:
                self.assertEqual(part['text'],originals[part['locator']][part['reading_start']:part['reading_end']])
        validate_reading(self.task,self.bundle)
        # Old receipts still reproduce their original windows, not the new ones.
        validate_reading(self.retained,self.old_bundle)
        self.assertEqual(self.retained['status'],'部分完成')
        self.assertEqual(self.retained['questions'][0]['status'],'未回答')

    def test_registered_literals_preserve_original_whitespace_and_negation(self):
        rule=dict(terms=['本基金跟踪','以标的指数'])
        parts=[dict(text='并非本 基金\n跟踪某个已确认标的。以标的指数不代表已生效。')]
        self.assertEqual(concept_reader_queries(rule,parts),['本 基金\n跟踪','以标的指数'])
        self.assertEqual(concept_reader_queries(rule,[dict(text='没有这些词')]),[])
        self.assertIn('并非',parts[0]['text'])

    def test_old_invalid_or_missing_knowledge_does_not_gain_reader_six(self):
        for value in (None,True,False,0,2,1.0):
            task=copy.deepcopy(self.retained)
            if value is not None:task[POLICY]=value
            task['input_hash']=input_fingerprint(task)
            self.assertNotEqual(reader_version_for(task),6)
            with self.assertRaises(ContractError):read(task,[],binding(task),reader_version=6)
        task=copy.deepcopy(self.task);task['knowledge']=None
        self.assertIsNone(concept_reader_rule(task))
        task=copy.deepcopy(self.task);task['question']='不同研究'
        with self.assertRaises(ContractError):concept_reader_rule(task)

    def test_four_original_extraction_still_fits_default_context_and_budgets(self):
        from researchhelper.knowledge import ResearchKnowledge
        path=ROOT/'result/acceptance/autonomous-data-phase-yr9yq5lu/task.json';raw=path.read_bytes()
        task=json.loads(raw);task.update({POLICY:1,'extraction_knowledge_guidance_version':1,
            'original_concept_navigation_version':1,'original_local_span_version':1,
            'original_whole_part_selection_version':1,'method_excerpt_selection_version':1,
            'method_literal_address_version':1,'structured_schema_sharing_version':1})
        task['knowledge']=ResearchKnowledge().read(task['entry'],task['question'],
            context=dict(intent=task['intent'],questions=task['questions']))
        task['input_hash']=input_fingerprint(task)
        old=task['reasoning_state']['reading']['bundle']
        task['reasoning_state']['reading']['bundle']=read(task,old['selection'],binding(task),old['artifact_ids'])
        payload=payload_for(task);shown=original_span_prompt(payload,task)
        schema=original_span_output_schema(task,payload['untrusted_sources'])
        size=len(canonical(shown))+len(canonical(provider_request_schema(task,schema,ORIGINAL_SPAN_PROTOCOL)))
        self.assertLessEqual(size,MAX_CONTEXT_CHARACTERS)
        self.assertEqual(task['ledger']['limits'],dict(data_requests=100,model_calls=8,seconds=600))
        self.assertEqual(path.read_bytes(),raw)
