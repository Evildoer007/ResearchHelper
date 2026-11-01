"""Whole-field numeric composition and cache boundaries, no real calls."""

import copy
import unittest
from unittest.mock import patch

from researchhelper.reasoning import analysis_contract, analysis_numeric_review
from tests import test_management_custody_fee_offline as fixtures


class NumericCompositionCacheTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ManagementCustodyFeeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        task, host = self.fixture.prepare()
        self.task = self.fixture.agent.start(task['id'], host)
        self.assertEqual(self.task['status'], '已完成')
        self.answer = copy.deepcopy(host['answers']['q1'])
        self.segments = [
            {'text': '同口径年度费用比较：'},
            {'source': 'calculation', 'ref': 'fee_compare', 'path': 'value',
             'format': 'comparison', 'digits': 2},
            {'text': '。不代表实际收益差。'},
        ]

    def proposal(self, mismatched=False):
        answer = dict(self.answer, numeric_segments={'answer': copy.deepcopy(self.segments)})
        if not mismatched:
            answer.pop('answer')
        return {'answers': {'q1': answer}, 'theses': []}

    def execution_task(self):
        task = copy.deepcopy(self.task)
        task['run_mode'] = 'model'
        task['questions'][0]['kind'] = 'fact'
        task['questions'][0]['status'] = '待回答'
        task['nodes'] = [{'id': 'answer_q1', 'kind': 'answer', 'questions': ['q1']}]
        task['reasoning_state'] = {}
        return task

    def step(self, task, proposal):
        with patch.object(self.fixture.agent, '_reason_on_verified_material', return_value=proposal) as model, \
                patch.object(self.fixture.agent, '_save_model_checkpoint'):
            result = self.fixture.agent._step(task, 'answer_q1', None)
        return result, model.call_count

    def test_contract_describes_complete_prose_not_number_appendix(self):
        contract = analysis_contract(self.task)
        self.assertEqual(contract['answer_item_structure']['version'], 2)
        composition = contract['numeric_statement']['composition']
        self.assertEqual(composition['scope'], 'complete_field_text')
        self.assertFalse(composition['separate_numeric_appendix'])
        self.assertFalse(composition['automatic_repair'])

    def test_complete_segments_accepted_without_inventing_duplicate_text(self):
        before = copy.deepcopy(self.task)
        review = analysis_numeric_review(self.task, self.proposal())
        self.assertTrue(review['accepted'])
        self.assertFalse(review['approval'])
        self.assertEqual(self.task, before)

    def test_mismatch_is_rejected_without_rewriting_the_original_proposal(self):
        proposal = self.proposal(True)
        before = copy.deepcopy(proposal)
        review = analysis_numeric_review(self.task, proposal)
        self.assertFalse(review['accepted'])
        self.assertEqual(review['invalid_fields'], ['answers.*.answer'])
        self.assertEqual(proposal, before)

    def test_invalid_response_delivers_gap_but_is_not_a_reusable_checkpoint(self):
        task, calls = self.step(self.execution_task(), self.proposal(True))
        self.assertEqual(calls, 1)
        self.assertEqual(task['questions'][0]['status'], '未回答')
        self.assertNotIn('input_digest', task['reasoning_state']['analysis'])
        self.assertFalse(task['reasoning_state']['analysis']['numeric_review']['accepted'])
        again, calls = self.step(task, self.proposal())
        self.assertEqual(calls, 0, 'No automatic per-node repair call')
        self.assertEqual(again['host_result'], task['host_result'])

    def test_explicit_new_batch_can_replace_failed_proposal_without_resetting_budget(self):
        task, _ = self.step(self.execution_task(), self.proposal(True))
        used = copy.deepcopy(task['ledger']['used'])
        task['batch'] += 1
        recovered, calls = self.step(task, self.proposal())
        self.assertEqual(calls, 1)
        self.assertEqual(recovered['questions'][0]['status'], '已回答')
        self.assertIn('input_digest', recovered['reasoning_state']['analysis'])
        self.assertEqual(recovered['ledger']['used'], used, 'Mock call does not forge spending')

    def test_valid_response_is_reused_without_extra_model_calls(self):
        task, calls = self.step(self.execution_task(), self.proposal())
        self.assertEqual(calls, 1)
        self.assertTrue(task['reasoning_state']['analysis']['numeric_review']['accepted'])
        _, calls = self.step(task, self.proposal(True))
        self.assertEqual(calls, 0)

    def test_rejected_thesis_does_not_change_valid_independent_answer(self):
        proposal = self.proposal()
        original_answer = copy.deepcopy(proposal['answers']['q1'])
        proposal['theses'] = [{'question_ids': ['q1'], 'title': '错误费用判断', 'judgment': '支持',
            'investment_meaning': '费用差为40个百分点。', 'limitation': '不能推断收益。',
            'support': self.answer['refs'], 'counter': [], 'calculation_refs': ['fee_compare']}]
        review = analysis_numeric_review(self.task, proposal)
        self.assertEqual(review['invalid_fields'], ['theses.*.numeric_statement'])
        self.assertEqual(proposal['answers']['q1'], original_answer)

    def test_same_batch_proposal_tampering_cannot_reuse_failed_attempt(self):
        task, _ = self.step(self.execution_task(), self.proposal(True))
        task['host_result']['answers']['q1']['answer'] = '篡改后的文字。'
        recovered, calls = self.step(task, self.proposal())
        self.assertEqual(calls, 1)
        self.assertEqual(recovered['questions'][0]['status'], '已回答')


if __name__ == '__main__':
    unittest.main()
