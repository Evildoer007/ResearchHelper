"""One correction plus a fresh independent review within the original8 calls."""
import copy
import unittest
from types import SimpleNamespace

from tests import test_reviewed_writing_revision as legacy
from researchhelper.contracts import ContractError, charge, canonical
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelCompletionError


class FinalReviewRevisionBudgetTests(unittest.TestCase):
    def setUp(self):
        self.base=legacy.ReviewedWritingRevisionTests('test_one_new_analysis_and_review_keep_original_reserve')
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.addCleanup(self.base.tearDown)
        self.task=self.base.task;self.agent=self.base.agent
        self.task['review_delivery_call_version']=1
        self.task['ledger']['used']['model_calls']=6
        self.agent._reasoning_available=ResearchAgent._reasoning_available

    def run_revision(self):
        return self.agent._revise_reviewed_writing(self.task,self.base.proposal,['q1'])

    def test_seventh_call_revises_and_eighth_is_actual_independent_review(self):
        ledger=copy.deepcopy(self.task['ledger']);seen=[]
        def reject_review(task,proposal,*,delivery=False):
            seen.append(delivery)
            charge(task['ledger'],'model_calls',delivery=delivery)
            raise ModelCompletionError('Isolated failed review, never approval')
        self.agent.model=SimpleNamespace(reason_argument_support=reject_review)
        self.agent._prepare_argument_review=lambda *args:ResearchAgent._prepare_argument_review(self.agent,*args)
        self.run_revision()
        self.assertEqual(seen,[True])
        self.assertEqual(self.task['ledger']['used']['model_calls'],8)
        self.assertEqual(self.task['ledger']['limits'],ledger['limits'])
        self.assertEqual(self.task['ledger']['reserve'],ledger['reserve'])
        self.assertEqual(self.task['questions'][0]['status'],'未回答')
        revision=self.task['reasoning_state']['reviewed_writing_revision']
        self.assertEqual(revision['status'],'复核未完成')
        self.assertFalse(revision['automatic_repair'])
        self.assertFalse(revision['further_acquisition_allowed'])
        with self.assertRaises(ContractError):charge(self.task['ledger'],'model_calls',delivery=True)

    def test_last_single_call_is_not_spent_on_prose(self):
        self.task['ledger']['used']['model_calls']=7
        before=canonical(self.task)
        self.assertIs(self.run_revision(),self.base.proposal)
        self.agent._reason_on_verified_material.assert_not_called()
        self.assertEqual(canonical(self.task),before)

    def test_the_full_delivery_time_reserve_is_preserved(self):
        self.task['ledger']['used']['seconds']=550
        self.assertIs(self.run_revision(),self.base.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_old_revision_policy_keeps_the_original_call_reserve_behavior(self):
        self.task.pop('review_delivery_call_version')
        self.assertIs(self.run_revision(),self.base.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_missing_required_evidence_cannot_trigger_an_in_place_rewrite(self):
        self.task['argument_support_reviews'][-1]['result']['question_assessments'][0].update(
            status='requires_evidence',missing_evidence=['待核验必要资料'])
        self.assertIs(self.run_revision(),self.base.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_unknown_or_boolean_policy_is_not_accepted(self):
        for value in (True,False,0,2,1.0):
            self.task['review_delivery_call_version']=value
            with self.subTest(value=value):self.assertIs(self.run_revision(),self.base.proposal)
        self.agent._reason_on_verified_material.assert_not_called()
