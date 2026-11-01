"""Literature counts are not fund shares; financial quantities stay bound."""
import hashlib
import json
import unittest

from tests import ROOT
from researchhelper.contracts import ContractError
from researchhelper.numeric_integrity import _statement_quantities,_segments
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import analysis_numeric_review


class DocumentExcerptCountTests(unittest.TestCase):
    def test_explicit_derived_document_nouns_are_not_financial_units(self):
        for text in ('两份摘录均给出基准','三份引文','两份实际读取的引用片段','两份中期报告'):
            self.assertEqual(_statement_quantities(text,dict(nonfinancial_count_grammar_version=2)),[])

    def test_financial_quantities_next_to_excerpts_and_ambiguous_fund_nouns_remain_bound(self):
        task=dict(nonfinancial_count_grammar_version=2)
        for text in ('持有两份基金','两份摘录基金','两份摘录 基金','买入100份ETF'):
            self.assertTrue(_statement_quantities(text,task))
        values=_statement_quantities('两份摘录记载费率0.15%，100份ETF价值500元。',task)
        self.assertEqual([v['unit'] for v in values],['%','份','元'])
        with self.assertRaises(ContractError):_segments([dict(text='两份摘录记载费率0.15%。')],{},task)

    def test_legacy_and_boolean_policies_keep_the_previous_excerpt_classification(self):
        for version in (None,0,1,True,False,3,2.0):
            task={} if version is None else dict(nonfinancial_count_grammar_version=version)
            self.assertTrue(_statement_quantities('两份摘录',task))

    def test_closed_proposal_reclassifies_only_text_without_reusing_old_approvals(self):
        path=ROOT/'result/acceptance/one-sentence-research-onlmhtfa/task.json'
        raw=path.read_bytes();before=hashlib.sha256(raw).hexdigest();old=json.loads(raw)
        self.assertFalse(analysis_numeric_review(old,old['host_result'])['accepted'])
        task=dict(old,nonfinancial_count_grammar_version=2);task['input_hash']=input_fingerprint(task)
        parts=old['host_result']['answers']['q1']['numeric_segments']['answer']
        plain=[p['text'] for p in parts if 'text' in p]
        self.assertTrue(any(_statement_quantities(text,old) for text in plain))
        self.assertTrue(all(not _statement_quantities(text,task) for text in plain))
        result=analysis_numeric_review(task,old['host_result'])
        # A different policy hash cannot reuse the old task's financial
        # approvals. Only the text classifier was independently corrected.
        self.assertFalse(result['accepted'])
        self.assertEqual(result['question_failures'][0]['code'],'numeric_or_reference_binding_rejected')
        self.assertFalse(result['approval'])
        self.assertEqual(old['status'],'部分完成')
        self.assertNotEqual(task['input_hash'],old['input_hash'])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
