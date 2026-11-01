"""Disjoint read fragments remain separate literal positions, not a new page."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from researchhelper.contracts import ContractError
from researchhelper.reasoning import evidence_contract
from researchhelper.financial_sources import (financial_table_candidates,financial_quote_choices,
    original_line_index,selected_original_span)
from researchhelper.output_schema import financial_selector_prompt,financial_extraction_output_schema

ROOT=Path(__file__).resolve().parents[1]
RETAINED=ROOT/'result/acceptance/one-sentence-research-jsr3c5n7/task.json'


class FinancialReadingFragmentsTests(unittest.TestCase):
    def setUp(self):
        self.digest=hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        self.task=json.loads(RETAINED.read_text())
        self.view=self.task['reasoning_state']['reading']['bundle']['views'][0]
        self.record=next(record for record in self.task['source_records'] if record['kind']=='filing')
        grouped={}
        for index,part in enumerate(self.view['parts']):grouped.setdefault(part['locator'],[]).append(index)
        self.fragments=next(indices for indices in grouped.values() if len(indices)>1)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(RETAINED.read_bytes()).hexdigest(),self.digest)

    def test_disjoint_prose_fragments_do_not_block_real_financial_table_cells(self):
        cells=financial_table_candidates(self.task,self.record,self.view)['cells']
        self.assertTrue({'revenue','net_profit','operating_cash_flow'}.issubset({cell['metric'] for cell in cells}))
        self.assertEqual(financial_extraction_output_schema(self.task,[self.view])['properties']['version']['enum'],[7])

    def test_prompt_and_quote_choices_preserve_each_fragment_once(self):
        choices=financial_quote_choices(self.task,self.view)
        payload={'untrusted_sources':[self.view],'evidence_contract':evidence_contract(self.task,[self.view])}
        presented=financial_selector_prompt(payload,task=self.task)
        for index,part in enumerate(self.view['parts']):
            selected=[choice for choice in choices if choice['fragment_index']==index]
            self.assertEqual(''.join(choice['quote'] for choice in selected),part['text'])
            self.assertTrue(all(choice['quote'] in part['text'] for choice in selected))
            actual=presented['untrusted_sources'][0]['parts'][index]
            self.assertEqual(''.join(item[1] for item in actual['quote_choices']),part['text'])

    def test_duplicate_or_overlapping_fragments_remain_rejected(self):
        view=copy.deepcopy(self.view);view['parts'].append(copy.deepcopy(view['parts'][self.fragments[0]]))
        with self.assertRaises(ContractError):financial_table_candidates(self.task,self.record,view)
        view=copy.deepcopy(self.view)
        a,b=(view['parts'][index] for index in self.fragments[:2])
        a['reading_end']=b['reading_end']
        original=next(part['text'] for part in self.record['snapshot']['material']['parts'] if part['locator']==a['locator'])
        a['text']=original[a['reading_start']:a['reading_end']]
        with self.assertRaises(ContractError):financial_table_candidates(self.task,self.record,view)

    def test_same_page_does_not_authorize_a_cross_fragment_quote(self):
        lines=original_line_index(self.view)
        first=[line for line in lines if line['fragment_index']==self.fragments[0]][-1]['index']
        second=[line for line in lines if line['fragment_index']==self.fragments[1]][0]['index']
        with self.assertRaises(ContractError):selected_original_span(lines,{'start':first,'end':second})
