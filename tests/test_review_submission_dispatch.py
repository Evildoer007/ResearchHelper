"""One automatic review per completed analysis, not per changing envelope."""
import copy
import json
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from researchhelper import argument_support
from researchhelper.contracts import ContractError,charge
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelCompletionError

ROOT=Path(__file__).resolve().parents[1]
RETAINED=ROOT/'result/acceptance/one-sentence-research-s89qizr5/task.json'


class ReviewSubmissionDispatchTests(unittest.TestCase):
    def setUp(self):
        self.original=RETAINED.read_bytes()
        self.task=json.loads(self.original)
        self.task.update(status='运行中',run_mode='model')
        self.task['ledger']['used'].update(model_calls=6,seconds=100)
        self.task['reasoning_state']['argument_support_attempts']={}
        self.task['reasoning_state'].pop('final_delivery',None)
        self.proposal=copy.deepcopy(self.task['host_result'])
        self.agent=ResearchAgent.__new__(ResearchAgent)
        self.agent._save_model_checkpoint=Mock()
        self.agent._check_run_control=Mock()
        def failure(task,proposal,*,delivery):
            charge(task['ledger'],'model_calls',delivery=delivery)
            raise ModelCompletionError('Isolated fixture: no adopted model response')
        self.method=Mock(side_effect=failure)
        self.agent.model=SimpleNamespace(reason_argument_support=self.method)
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))
        guard.start();self.addCleanup(guard.stop)

    def tearDown(self):self.assertEqual(RETAINED.read_bytes(),self.original)

    def prepare(self):
        self.agent._prepare_argument_review(self.task,self.proposal,[q['id'] for q in self.task['questions']])

    def test_changed_review_envelope_does_not_redispatch_a_failed_analysis_submission(self):
        first=argument_support.review_key(argument_support.review_request(self.task,self.proposal))
        self.prepare()
        self.task['knowledge']['entries'].reverse()
        second=argument_support.review_key(argument_support.review_request(self.task,self.proposal))
        self.assertNotEqual(first,second)
        self.prepare()
        self.assertEqual(self.method.call_count,1)
        self.assertEqual(self.task['ledger']['used']['model_calls'],7)

    def test_unknown_dispatch_survives_envelope_change_and_resume(self):
        self.agent._check_run_control.side_effect=ContractError('Isolated cancellation')
        with self.assertRaises(ContractError):self.prepare()
        self.assertEqual(next(iter(self.task['reasoning_state']['argument_support_attempts'].values()))['status'],'已派发')
        self.task['batch']+=1
        self.task['knowledge']['entries'].reverse()
        self.agent._check_run_control=Mock()
        self.prepare()
        self.assertEqual(self.method.call_count,1)

    def test_new_completed_analysis_input_may_receive_one_new_review(self):
        self.prepare()
        self.task['reasoning_state']['analysis']['input_digest']='explicit-new-analysis-input'
        self.task['knowledge']['entries'].reverse()
        self.prepare()
        self.assertEqual(self.method.call_count,2)
        self.assertEqual(self.task['ledger']['used']['model_calls'],8)
        self.assertEqual(len(self.task['reasoning_state']['argument_support_attempts']),2)

    def test_changed_proposal_without_a_new_analysis_checkpoint_cannot_spend_another_call(self):
        self.prepare()
        self.proposal['theses'][0]['numeric_segments']['title']=[dict(text='Changed unsubmitted title')]
        self.prepare()
        self.assertEqual(self.method.call_count,1)
        self.assertFalse(self.task.get('argument_support_reviews'))


if __name__=='__main__':unittest.main()
