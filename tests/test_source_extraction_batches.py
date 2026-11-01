"""Independent orchestration tests; mock results never approve financial facts."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ContractError


class SourceExtractionBatchTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.agent=ResearchAgent(ResearchArchive(Path(temporary.name)),ROOT,offline=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.task=self.agent.create('比较两份原件')
        self.task['status']='运行中';self.agent.archive.save(self.task)
        self.views=[dict(id='source_a',kind='filing'),dict(id='source_b',kind='filing')]
        self.payload=dict(untrusted_sources=self.views,questions=self.task['questions'],evidence_contract={},calculations=[])
        self.calls=[]

    def call(self,model,task,phase,payload,fields):
        self.calls.append(copy.deepcopy(payload['untrusted_sources']))
        batch=task['reasoning_state']['source_extraction_batches']
        self.assertEqual(batch['items'][payload['untrusted_sources'][0]['id']]['status'],'已派发')
        task['ledger']['used']['model_calls']+=1
        return dict(evidence_specs=[]),dict(phase='extract',task_id=task['id'])

    def run_batches(self,side_effect=None):
        with patch.object(self.agent,'_save_model_checkpoint'), \
                patch('researchhelper.engine.evidence_contract',return_value={
                    'calculation_input_requirements':{'proposal_requirements':[]}}), \
                patch('researchhelper.engine.model_object',side_effect=side_effect or self.call):
            return self.agent._extract_source_batches(self.task,self.payload,'stable_test_input',None)

    def test_every_complete_source_is_processed_once_and_known_results_are_reused(self):
        result,receipt=self.run_batches()
        self.assertEqual(self.calls,[[self.views[0]],[self.views[1]]])
        self.assertTrue(receipt['all_source_views_processed'])
        self.assertEqual(result,{'evidence_specs':[]})
        self.run_batches();self.assertEqual(len(self.calls),2)
        self.assertEqual(self.task['ledger']['used']['model_calls'],2)
        self.assertEqual(self.task['evidence'],[])

    def test_a_failed_or_unknown_dispatch_is_not_retried_on_resume(self):
        def fail_second(*args):
            if args[3]['untrusted_sources'][0]['id']=='source_b':
                self.calls.append(copy.deepcopy(args[3]['untrusted_sources']))
                raise ContractError('synthetic offline completed rejection')
            return self.call(*args)
        _,receipt=self.run_batches(fail_second)
        self.assertFalse(receipt['all_source_views_processed'])
        self.assertTrue(self.task['gaps'])
        self.run_batches(fail_second);self.assertEqual(len(self.calls),2)

    def test_budget_reserves_analysis_review_and_delivery_before_dispatch(self):
        self.task['ledger']['used']['model_calls']=5
        with self.assertRaises(ContractError):self.run_batches()
        self.assertEqual(self.calls,[])

    def test_scope_and_checkpoint_changes_are_not_silently_reused(self):
        self.run_batches();self.task['input_hash']='changed'
        with self.assertRaises(ContractError):self.run_batches()
        self.assertEqual(len(self.calls),2)


if __name__=='__main__':unittest.main()
