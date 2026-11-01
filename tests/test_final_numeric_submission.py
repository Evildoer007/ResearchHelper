"""A single budgeted final submission, never rewriting rejected client prose."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from researchhelper.contracts import charge
from researchhelper.engine import ResearchAgent
from researchhelper.model import ModelCompletionError

ROOT = Path(__file__).resolve().parents[1]
FAILED = ROOT/'result/acceptance/one-sentence-research-7s8j8_l8/task.json'
PASSED = ROOT/'result/acceptance/one-sentence-research-jrfhac2u/task.json'


class FinalNumericSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.hashes = {path:hashlib.sha256(path.read_bytes()).hexdigest() for path in (FAILED,PASSED)}
        self.task = json.loads(FAILED.read_text())
        self.proposal = copy.deepcopy(self.task['host_result'])
        self.agent = ResearchAgent.__new__(ResearchAgent)
        self.agent.model = SimpleNamespace(supports_final_delivery=lambda:True, reason_final_analysis=lambda:None)
        self.agent._save_model_checkpoint = Mock()
        self.final = {'answers':{},'theses':[]}
        def submit(task, selected, *, final_delivery):
            self.assertTrue(final_delivery)
            self.assertEqual(task['reasoning_state']['final_delivery']['status'],'已派发')
            charge(task['ledger'],'model_calls',delivery=True)
            task['reasoning_state']['final_delivery']['status']='已提交'
            return copy.deepcopy(self.final)
        self.agent._reason_on_verified_material = Mock(side_effect=submit)

    def tearDown(self):
        for path,value in self.hashes.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),value)

    def test_valid_actual_analysis_does_not_spend_another_call(self):
        task = json.loads(PASSED.read_text()); proposal = task['host_result']
        self.assertIs(self.agent._finalize_numeric_submission(task,proposal),proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_invalid_actual_prose_gets_one_budgeted_submission_and_quarantine(self):
        before = copy.deepcopy((self.task['evidence'],self.task['calculations'],self.proposal))
        result = self.agent._finalize_numeric_submission(self.task,self.proposal)
        self.assertEqual(result,self.final)
        self.assertEqual(self.task['ledger']['used']['model_calls'],8)
        rejected = self.task['reasoning_state']['rejected_analysis_submissions'][0]
        self.assertEqual(rejected['proposal'],self.proposal)
        self.assertFalse(rejected['evidence_approved']); self.assertFalse(rejected['automatic_repair'])
        self.assertFalse(self.task['reasoning_state']['final_delivery']['further_research_allowed'])
        self.assertEqual((self.task['evidence'],self.task['calculations'],self.proposal),before)

    def test_ninth_call_cannot_be_dispatched(self):
        self.task['ledger']['used']['model_calls']=8
        self.assertIs(self.agent._finalize_numeric_submission(self.task,self.proposal),self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_no_time_for_durable_save_means_no_final_dispatch(self):
        self.task['ledger']['used']['seconds']=598
        self.assertIs(self.agent._finalize_numeric_submission(self.task,self.proposal),self.proposal)
        self.agent._reason_on_verified_material.assert_not_called()

    def test_completed_final_submission_is_never_repeated(self):
        self.agent._finalize_numeric_submission(self.task,self.proposal)
        self.agent._finalize_numeric_submission(self.task,self.proposal)
        self.agent._reason_on_verified_material.assert_called_once()

    def test_provider_rejection_is_recorded_without_repair_or_retry(self):
        self.agent._reason_on_verified_material.side_effect=ModelCompletionError('Synthetic completion failure')
        self.assertIs(self.agent._finalize_numeric_submission(self.task,self.proposal),self.proposal)
        self.assertEqual(self.task['reasoning_state']['final_delivery']['status'],'未采用')
        self.agent._finalize_numeric_submission(self.task,self.proposal)
        self.agent._reason_on_verified_material.assert_called_once()
