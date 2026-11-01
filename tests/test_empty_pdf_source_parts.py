"""A verified empty physical page is absence of text, not a forged excerpt."""
import copy
import json
import unittest

from tests import ROOT
from researchhelper.contracts import ContractError
from researchhelper.financial_sources import financial_table_candidates
from researchhelper.reasoning import source_views

RETAINED = ROOT / 'result/acceptance/autonomous-data-phase-lmbz1p9w/task.json'


class EmptyPDFSourcePartsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = RETAINED.read_bytes()
        cls.task = json.loads(cls.original)
        cls.record = next(s for s in cls.task['source_records'] if s['operation']=='filing_original'
            and any(not p['text'] for p in s['snapshot']['material']['parts']))
        cls.view = next(v for v in source_views(cls.task) if v['id']==cls.record['id'])

    @classmethod
    def tearDownClass(cls):
        assert RETAINED.read_bytes()==cls.original

    def test_real_blank_pages_do_not_block_the_remaining_report(self):
        self.assertTrue(any(not p['text'] for p in self.view['parts']))
        result = financial_table_candidates(self.task,self.record,self.view)
        self.assertIsInstance(result['cells'],list)
        self.assertTrue(all(c['proposal_fields']['text'] for c in result['cells']))

    def test_clearing_a_nonempty_original_part_is_still_rejected(self):
        view = copy.deepcopy(self.view)
        next(p for p in view['parts'] if p['text'])['text']=''
        with self.assertRaises(ContractError):
            financial_table_candidates(self.task,self.record,view)

    def test_inventing_text_on_a_blank_page_is_still_rejected(self):
        view = copy.deepcopy(self.view)
        next(p for p in view['parts'] if not p['text'])['text']='伪造财务数字'
        with self.assertRaises(ContractError):
            financial_table_candidates(self.task,self.record,view)
