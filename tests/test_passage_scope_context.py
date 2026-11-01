"""Original chapter context reaches extraction, analysis and independent review."""
import copy
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from researchhelper.argument_support import review_request,review_messages
from researchhelper.contracts import ContractError,fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.financial_sources import passage_context,passage_contexts,evidence_passage_context
from researchhelper.output_schema import original_batch_prompt
from researchhelper.reasoning import evidence_contract

ROOT=Path(__file__).resolve().parents[1]
RETAINED=ROOT/'result/acceptance/one-sentence-research-9_qa5f41/task.json'


class PassageScopeContextTests(unittest.TestCase):
    def setUp(self):
        self.original=RETAINED.read_bytes()
        self.task=json.loads(self.original)
        self.item=next(e for e in self.task['evidence'] if e['id']=='s1_q_revenue_composition')
        self.source=next(r for r in self.task['source_records'] if r['id']==self.item['artifact_id'])
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))
        guard.start();self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(),self.original)

    def test_actual_parent_section_is_not_relabelled_consolidated(self):
        context=evidence_passage_context(self.task,self.item,include_text=True)
        self.assertEqual(context['accounting_scope_hint'],'parent')
        self.assertEqual(context['chapter']['part'],'第103页')
        self.assertIn('母公司财务报表主要项目注释',context['chapter']['quote'])
        self.assertIn('49,660,601,803.96',context['page_text'])
        self.assertFalse(context['financial_approval'])

    def test_entire_original_page_is_preserved_without_an_invented_summary(self):
        context=passage_context(self.source,'第108页',include_text=True)
        page=next(p for p in self.source['snapshot']['material']['parts'] if p['locator']=='第108页')
        self.assertEqual(context['page_text'],page['text'])
        self.assertEqual(context['snapshot_hash'],self.source['hash'])

    def test_extraction_only_gets_locator_context_not_a_second_full_copy(self):
        context=passage_context(self.source,'第108页')
        self.assertNotIn('page_text',context)
        self.assertEqual(context['accounting_scope_hint'],'parent')
        with patch('researchhelper.financial_sources.fingerprint',wraps=fingerprint) as validation:
            values=passage_contexts(self.source,['第103页','第108页'])
        self.assertEqual(validation.call_count,1)
        self.assertEqual(values[1],context)

    def test_next_major_section_does_not_inherit_parent_scope(self):
        context=passage_context(self.source,'第109页')
        self.assertEqual(context['accounting_scope_hint'],'unspecified')
        self.assertIn('补充资料',context['chapter']['quote'])

    def test_unknown_and_duplicate_parts_are_rejected(self):
        with self.assertRaises(ContractError):passage_context(self.source,'不存在')
        source=copy.deepcopy(self.source)
        page=next(p for p in source['snapshot']['material']['parts'] if p['locator']=='第108页')
        source['snapshot']['material']['parts'].append(copy.deepcopy(page))
        source['hash']=fingerprint(source['snapshot'])
        with self.assertRaises(ContractError):passage_context(source,'第108页')

    def test_changed_original_cannot_reuse_its_old_context(self):
        source=copy.deepcopy(self.source);source['snapshot']['material']['parts'][0]['text']+='changed'
        with self.assertRaises(ContractError):passage_context(source,'第108页')

    def test_foreign_task_version_or_item_snapshot_is_rejected(self):
        for field,value in (('task_id','foreign'),('research_version',99),('snapshot_hash','changed')):
            item=copy.deepcopy(self.item);item[field]=value
            with self.subTest(field=field),self.assertRaises(ContractError):
                evidence_passage_context(self.task,item)

    def test_table_part_resolves_to_its_actual_physical_page(self):
        part=next(p for p in self.source['snapshot']['material']['parts']
            if p.get('pdf_table',{}).get('page')==108)
        context=passage_context(self.source,part['locator'])
        self.assertEqual(context['physical_part'],'第108页')
        self.assertEqual(context['accounting_scope_hint'],'parent')

    def test_non_material_api_does_not_receive_financial_scope(self):
        self.assertIsNone(passage_context(dict(kind='api'),None))

    def test_analyzer_payload_contains_the_full_page_and_chapter(self):
        payload=ResearchAgent._reasoning_evidence_payload(self.task)
        item=next(e for e in payload['evidence'] if e['id']==self.item['id'])
        self.assertEqual(item['passage_context'],evidence_passage_context(self.task,self.item,include_text=True))
        self.assertNotIn('original_base64',json.dumps(item,ensure_ascii=False))

    def test_independent_review_receives_the_original_locator_and_scope(self):
        request=review_request(self.task,self.task['host_result'])
        item=next(e for e in request['evidence'] if e['id']==self.item['id'])
        self.assertEqual(item['record_locator'],self.item['record_locator'])
        self.assertEqual(item['passage_context']['accounting_scope_hint'],'parent')
        self.assertIn('母公司',review_messages(request,use_reference_units=True)[0]['content'])
        self.assertEqual(request['binding_version'],4)

    def test_extraction_and_multi_original_projection_keep_literal_chapter_context(self):
        views=self.task['reasoning_state']['reading']['bundle']['views']
        contract=evidence_contract(self.task,views)
        payload=original_batch_prompt(dict(untrusted_sources=views,evidence_contract=contract),self.task)
        source=next(s for s in payload['evidence_contract']['sources'] if s['artifact_id']==self.source['id'])
        index=source['passage_context_index']
        context=next(c for c in index['parts'] if c['part']=='第108页')
        self.assertEqual(index['sections'][context['section_ref']]['accounting_scope_hint'],'parent')
        self.assertIn('不批准',index['rule'])


if __name__=='__main__':unittest.main()
