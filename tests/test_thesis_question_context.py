"""Full-question completeness and focused-claim support are separate gates."""
import copy
import json
from pathlib import Path
import unittest

from researchhelper import argument_support
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.reports import compose

ROOT=Path(__file__).resolve().parents[1]
RETAINED=ROOT/'result/acceptance/one-sentence-research-z9drr8h1/task.json'


class ThesisQuestionContextTests(unittest.TestCase):
    def setUp(self):
        self.original=RETAINED.read_bytes()
        self.task=json.loads(self.original)
        self.proposal=copy.deepcopy(self.task['host_result'])
        self.agent=ResearchAgent.__new__(ResearchAgent)
        self.agent._answer_questions(self.task,['q1'],self.proposal,{})
        self.assertEqual(self.task['questions'][0]['status'],'已回答')

    def tearDown(self):self.assertEqual(RETAINED.read_bytes(),self.original)

    def adopt(self):self.agent._accept_theses(self.task,['q1'],self.proposal,None)

    def test_actual_reviewed_answer_provides_context_without_adding_claim_citations(self):
        self.assertNotIn('fin_cost_2026h1',self.proposal['theses'][0]['support'])
        self.adopt()
        self.assertEqual(len(self.task['theses']),1)
        thesis=self.task['theses'][0]
        self.assertEqual(thesis['support'],self.proposal['theses'][0]['support'])
        self.assertEqual(thesis['counter'],self.proposal['theses'][0]['counter'])
        self.assertIn('fin_cost_2026h1',thesis['question_context_receipt']['q1']['refs'])
        self.assertEqual(self.task['questions'][0]['status'],'已回答')

    def test_context_and_report_recheck_keep_the_original_research_ledger(self):
        used=copy.deepcopy(self.task['ledger']['used'])
        self.adopt()
        self.agent._resolve_current_gaps(self.task)
        result=compose(self.task)
        self.assertEqual(len(result['theses']),1)
        self.assertEqual(self.task['ledger']['used'],used)
        self.assertFalse(self.task['gaps'])

    def test_incomplete_question_still_requires_full_claim_level_requirements(self):
        self.task['questions'][0]['status']='未回答'
        self.assertEqual(argument_support.thesis_question_contexts(self.task,['q1']),{})
        self.adopt()
        self.assertFalse(self.task['theses'])
        self.assertTrue(any('判断依据未覆盖' in gap for gap in self.task['gaps']))

    def test_unreviewed_answer_provides_no_context_or_claim_approval(self):
        self.task['argument_support_reviews']=[]
        self.assertEqual(argument_support.thesis_question_contexts(self.task,['q1']),{})
        self.adopt()
        self.assertFalse(self.task['theses'])

    def test_missing_answer_requirement_cannot_be_hidden_in_context(self):
        self.task['questions'][0]['refs'].remove('fin_cost_2026h1')
        self.assertEqual(argument_support.thesis_question_contexts(self.task,['q1']),{})
        self.adopt()
        self.assertFalse(self.task['theses'])

    def test_changed_required_question_or_method_invalidates_context(self):
        self.task['questions'][0]['method_needs'].append('gross_margin_v1')
        self.assertEqual(argument_support.thesis_question_contexts(self.task,['q1']),{})
        self.adopt()
        self.assertFalse(self.task['theses'])

    def test_changed_claim_cannot_borrow_an_existing_answer_review(self):
        self.proposal['theses'][0]['numeric_segments']['title'].append(dict(text='并保证未来投资获利。'))
        self.adopt()
        self.assertFalse(self.task['theses'])

    def test_report_rejects_a_forged_context_receipt(self):
        self.adopt()
        self.task['theses'][0]['question_context_receipt']['q1']['answer_hash']='forged'
        with self.assertRaises(ContractError):compose(self.task)

    def test_report_rejects_removed_receipt_when_claim_does_not_cover_every_need(self):
        self.adopt()
        self.task['theses'][0].pop('question_context_receipt')
        with self.assertRaises(ContractError):compose(self.task)

    def test_changed_adopted_answer_invalidates_the_context_at_save(self):
        self.adopt()
        self.task['questions'][0]['answer']+='新增未核验说明。'
        with self.assertRaises(ContractError):compose(self.task)

    def test_context_does_not_license_missing_numeric_claim_inputs(self):
        ratio=next(item for item in self.task['calculations'] if item['method']=='operating_cash_profit_ratio_v1')
        proposal=copy.deepcopy(self.proposal)
        proposal['theses'][0]['support']=[ref for ref in proposal['theses'][0]['support']
            if ref!=ratio['input_refs'][0]]
        self.agent._accept_theses(self.task,['q1'],proposal,None)
        self.assertFalse(self.task['theses'])

    def test_bad_numeric_host_output_is_not_a_fatal_context_fallback(self):
        self.task['host_result']['answers']['q1']['numeric_segments']['answer'].append(dict(text='未经引用的收益率99%。'))
        self.assertEqual(argument_support.thesis_question_contexts(self.task,['q1']),{})
        self.agent._accept_theses(self.task,['q1'],self.task['host_result'],None)
        self.assertFalse(self.task['theses'])

    def test_invalid_review_record_cannot_become_a_context_or_crash_adoption(self):
        self.task['argument_support_reviews'][-1]['result']['review_hash']='foreign'
        self.assertEqual(argument_support.thesis_question_contexts(self.task,['q1']),{})
        self.adopt()
        self.assertFalse(self.task['theses'])
