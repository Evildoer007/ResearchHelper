"""Harness-only checks; never counted as financial-engine acceptance."""
from pathlib import Path
import tempfile
import unittest
from tests import ROOT
from tests.accept_initial_acquisition_runtime import sharded_runner


class RuntimeCaseShardTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='case-shard-',dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup);self.output=Path(folder.name);self.calls=[]

    def runner(self,kind,location,root,output,modules,count):
        self.calls.append(list(modules))
        return dict(status='passed',tests_run=count,module_paths={},module_code_origins={})

    def test_every_actual_financial_reference_case_runs_once_in_small_shards(self):
        name='test_financial_reference_protocol'
        suite=unittest.defaultTestLoader.loadTestsFromName('tests.'+name)
        count=suite.countTestCases()
        result=sharded_runner(self.runner,isolated_modules=(name,),case_shard_modules=(name,))(
            'skill',self.output,ROOT,self.output,[name],count)
        expected=set(unittest.defaultTestLoader.getTestCaseNames(
            __import__('tests.'+name,fromlist=['FinancialReferenceProtocolTests']).FinancialReferenceProtocolTests))
        actual=[entry.rsplit('.',1)[-1] for group in self.calls for entry in group]
        self.assertEqual(set(actual),expected);self.assertEqual(len(actual),count)
        self.assertTrue(all(len(group)<=4 for group in self.calls))
        self.assertEqual(result['tests_run'],count)
        self.assertEqual(result['test_modules'],[name])

    def test_old_module_grouping_is_preserved_when_case_mode_is_not_selected(self):
        name='test_review_remaining_deadline'
        result=sharded_runner(self.runner)('skill',self.output,ROOT,self.output,[name],4)
        self.assertEqual(self.calls,[[name]]);self.assertEqual(result['tests_run'],4)

    def test_expected_total_cannot_be_reduced_to_hide_a_case(self):
        name='test_review_remaining_deadline'
        with self.assertRaises(AssertionError):
            sharded_runner(self.runner,isolated_modules=(name,),case_shard_modules=(name,))(
                'skill',self.output,ROOT,self.output,[name],3)

    def test_failed_case_count_does_not_become_successful_aggregation(self):
        def wrong(*args):return dict(status='passed',tests_run=0,module_paths={},module_code_origins={})
        with self.assertRaises(AssertionError):
            sharded_runner(wrong)('skill',self.output,ROOT,self.output,['test_review_remaining_deadline'],4)


if __name__=='__main__':unittest.main()
