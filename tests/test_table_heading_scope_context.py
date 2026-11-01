"""Same-page table geometry prevents table rows from replacing note headings."""
import copy
import json
from pathlib import Path
import unittest

from researchhelper.contracts import ContractError
from researchhelper.financial_sources import passage_context,evidence_passage_context
from researchhelper.reasoning import evidence_contract

ROOT=Path(__file__).resolve().parents[1]
RETAINED=ROOT/'result/acceptance/one-sentence-research-xj6t95ng/task.json'


class TableHeadingScopeContextTests(unittest.TestCase):
    def setUp(self):
        self.original=RETAINED.read_bytes()
        self.task=json.loads(self.original)
        self.source=next(item for item in self.task['source_records'] if item['kind']=='filing')

    def tearDown(self):self.assertEqual(RETAINED.read_bytes(),self.original)

    def test_table_numerals_do_not_end_consolidated_notes(self):
        context=passage_context(self.source,'第81页',version=2)
        self.assertEqual(context['accounting_scope_hint'],'consolidated')
        self.assertEqual(context['chapter']['part'],'第53页')
        self.assertIn('合并财务报表项目注释',context['chapter']['quote'])
        self.assertFalse(context['financial_approval'])

    def test_table_part_keeps_full_actual_page_and_same_consolidated_section(self):
        context=passage_context(self.source,'第81页表1',include_text=True,version=2)
        page=next(p for p in self.source['snapshot']['material']['parts'] if p['locator']=='第81页')
        self.assertEqual(context['page_text'],page['text'])
        self.assertEqual(context['accounting_scope_hint'],'consolidated')

    def test_actual_next_heading_closes_scope_while_parent_scope_remains_distinct(self):
        next_chapter=passage_context(self.source,'第84页',version=2)
        self.assertEqual(next_chapter['accounting_scope_hint'],'unspecified')
        self.assertIn('研发支出',next_chapter['chapter']['quote'])
        parent=passage_context(self.source,'第108页',version=2)
        self.assertEqual(parent['accounting_scope_hint'],'parent')

    def test_existing_tasks_keep_version_one_context_without_automatic_upgrade(self):
        context=passage_context(self.source,'第81页')
        self.assertEqual(context['version'],1)
        self.assertEqual(context['chapter']['quote'],'二、将重分类')
        self.assertNotIn('passage_context_version',self.task)

    def test_new_version_reaches_extraction_and_evidence_context(self):
        task=copy.deepcopy(self.task);task['passage_context_version']=2
        item=next(item for item in task['evidence'] if item['id']=='tq_np_adj_2026h1')
        self.assertEqual(evidence_passage_context(task,item)['accounting_scope_hint'],'consolidated')
        views=task['reasoning_state']['reading']['bundle']['views']
        sources=evidence_contract(task,views)['sources']
        index=next(source['passage_context_index'] for source in sources if source['artifact_id']==self.source['id'])
        part=next(part for part in index['parts'] if part['part']=='第81页')
        self.assertEqual(index['version'],2)
        self.assertEqual(index['sections'][part['section_ref']]['accounting_scope_hint'],'consolidated')

    def test_unknown_context_version_is_rejected(self):
        with self.assertRaises(ContractError):passage_context(self.source,'第81页',version=3)
