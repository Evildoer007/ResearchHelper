"""Qualified report counts are not fund shares; financial quantities stay bound."""
import copy
import hashlib
import json
import unittest

from tests import ROOT
from researchhelper.contracts import ContractError
from researchhelper.numeric_integrity import _quantities, _statement_quantities, _segments, _legacy_bindings
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import analysis_numeric_review

RETAINED = ROOT/'result/acceptance/one-sentence-research-fvj_gq15/task.json'


class QualifiedDocumentCountTests(unittest.TestCase):
    def setUp(self):
        self.task = dict(nonfinancial_count_grammar_version=1)

    def test_explicit_qualified_document_nouns_are_not_financial_units(self):
        for text in ('两份最近可得正式报告','三份最新披露的年度报告','两份完整招股说明书',
                     '两份2026年中期报告','两份原始接口响应','三份已核验的原件'):
            with self.subTest(text=text):
                self.assertEqual(_statement_quantities(text,self.task),[])

    def test_bare_fund_shares_and_ambiguous_nouns_remain_quantities(self):
        for text in ('持有两份基金份额','买入100份ETF','两份最新净值','两份最近可得基金',
                     '100份正式报告基金','100份已核验文件股票'):
            with self.subTest(text=text):
                self.assertTrue(any(q['unit']=='份' for q in _statement_quantities(text,self.task)))

    def test_document_counts_do_not_hide_nearby_fees_money_or_shares(self):
        text='两份最近可得正式报告披露费率0.15%，买入100份基金需500元。'
        values=_statement_quantities(text,self.task)
        self.assertEqual([q['unit'] for q in values],['%','份','元'])
        self.assertEqual([str(q['value']) for q in values],['0.15','100','500'])

    def test_text_segments_and_legacy_prose_use_the_same_grammar(self):
        text='本次核对的两份最近可得正式报告均为中期报告。'
        self.assertEqual(_segments([dict(text=text)],{},self.task),(text,[]))
        self.assertEqual(_legacy_bindings(text,{},self.task),[])
        with self.assertRaises(ContractError):
            _segments([dict(text='两份正式报告披露费率0.15%。')],{},self.task)
        with self.assertRaises(ContractError):
            _segments([dict(text='费率0.15'),dict(text='%')],{},self.task)

    def test_old_policy_and_boolean_value_do_not_change_classification(self):
        text='两份最近可得正式报告'
        self.assertEqual(_statement_quantities(text,{}),_quantities(text))
        self.assertTrue(_quantities(text))
        self.assertEqual(_statement_quantities(text,dict(nonfinancial_count_grammar_version=True)),_quantities(text))


class RetainedQualifiedDocumentCountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with RETAINED.open('rb') as stream:
            cls.digest=hashlib.file_digest(stream,'sha256').hexdigest()
        cls.old=json.loads(RETAINED.read_text())

    @classmethod
    def tearDownClass(cls):
        with RETAINED.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=cls.digest:
                raise AssertionError('Closed research changed')

    def test_same_retained_proposal_changes_only_numeric_classification_on_memory_copy(self):
        task=dict(self.old,nonfinancial_count_grammar_version=1,numeric_lookup_isolation_version=1)
        task['input_hash']=input_fingerprint(task)
        old_review=analysis_numeric_review(self.old,self.old['host_result'])
        self.assertFalse(old_review['accepted'])
        self.assertEqual(old_review['question_failures'][0]['code'],'unbound_numeric_prose')
        new_review=analysis_numeric_review(task,self.old['host_result'])
        self.assertTrue(new_review['accepted'],new_review)
        self.assertFalse(new_review['approval'])
        self.assertEqual(self.old['status'],'部分完成')
        self.assertNotEqual(input_fingerprint(self.old),input_fingerprint(task))
