"""Resume a paid draft plan after cancellation without buying it again."""
from unittest.mock import patch
import copy
import unittest

from tests import test_draft_first_workflow as financial
from researchhelper import draft_workflow


class DraftResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        financial.DraftFirstWorkflowTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        financial.DraftFirstWorkflowTests.tearDownClass()

    def setUp(self):
        self.control=financial.DraftFirstWorkflowTests()
        self.addCleanup(self.control.doCleanups)
        self.control.setUp()
        self.agent=self.control.agent
        self.model=self.control.model

    def cancel_after_first_financial(self,limits=None):
        task=self.agent.create(financial.QUESTION,research_mode='current',limits=limits)
        checkpoint=draft_workflow._checkpoint
        stopped=[]

        def persist_then_cancel(engine,current):
            checkpoint(engine,current)
            if not stopped and any(row['operation']=='basic_data_service' for row in current['source_records']):
                stopped.append(True)
                self.agent.cancel(current['id'])

        with patch.object(draft_workflow,'_checkpoint',side_effect=persist_then_cancel):
            cancelled=self.agent.start(task['id'])
        self.assertTrue(stopped)
        self.assertEqual(cancelled['status'],'已取消')
        self.assertEqual(self.model.calls,['draft_plan'])
        self.assertEqual(cancelled['ledger']['used']['model_calls'],1)
        self.assertEqual(sum(row['operation']=='basic_data_service' for row in cancelled['source_records']),1)
        return cancelled

    def check_resumed(self,cancelled):
        result=self.agent.start(cancelled['id'])
        self.assertTrue(result.get('delivery'),result.get('gaps'))
        self.assertEqual(len(result['draft_report']['facts']),8)
        self.assertEqual(self.model.calls,['draft_plan','draft_report'])
        self.assertEqual(result['ledger']['used']['model_calls'],2)
        self.assertEqual(result['ledger']['used']['data_requests'],3)
        self.assertEqual(result['batch'],cancelled['batch']+1)
        self.assertEqual(result['version'],cancelled['version'])
        self.assertEqual(result['cutoff'],cancelled['cutoff'])
        return result

    def test_resume_reuses_the_completed_plan_without_an_extra_model_call(self):
        self.check_resumed(self.cancel_after_first_financial())

    def test_two_call_budget_still_fetches_the_unfinished_period_after_resume(self):
        self.check_resumed(self.cancel_after_first_financial(
            limits=dict(model_calls=2,data_requests=100,seconds=600)))


    def test_restart_reuses_the_saved_plan_with_the_original_budget_and_cutoff(self):
        from researchhelper.archive import ResearchArchive
        from researchhelper.engine import ResearchAgent
        cancelled=self.cancel_after_first_financial(limits=dict(model_calls=2,data_requests=100,seconds=600))
        self.agent.close_runtime()
        self.agent=ResearchAgent(ResearchArchive(self.control.directory.name),financial.ROOT,
            offline=False,model=self.model,require_argument_support=True,draft_first=True)
        self.addCleanup(self.agent.close_runtime)
        self.agent.collector.external=self.control.transport
        self.check_resumed(cancelled)

    def test_a_new_material_invalidates_the_previous_selection_without_resetting_cost(self):
        cancelled=self.cancel_after_first_financial()
        self.agent.add_material(cancelled['id'],'new-disclosure.txt','新增材料，规划须重新读取。'.encode())
        result=self.agent.start(cancelled['id'])
        self.assertTrue(result.get('delivery'),result.get('gaps'))
        self.assertEqual(self.model.calls,['draft_plan','draft_plan','draft_report'])
        self.assertEqual(result['ledger']['used']['model_calls'],3)
        self.assertEqual(len(result['draft_report']['facts']),8)

    def test_changed_or_foreign_checkpoint_is_not_reused_and_does_not_charge(self):
        cancelled=self.cancel_after_first_financial()
        before=copy.deepcopy(cancelled)
        mutations=(
            lambda t:t['draft_acquisition_checkpoint']['scope'].update(task_id='foreign'),
            lambda t:t['draft_acquisition_checkpoint']['scope'].update(research_version=True),
            lambda t:t['draft_acquisition_checkpoint'].update(attempt=True),
            lambda t:t['draft_acquisition_checkpoint']['requests'][0]['parameters'].update(codes='600519.SH'),
            lambda t:t['source_records'][0]['snapshot'].update(changed=True),
        )
        for mutate in mutations:
            changed=copy.deepcopy(cancelled);mutate(changed)
            with self.subTest(mutate=mutate):
                self.assertIsNone(draft_workflow._retained_plan(changed))
                self.assertEqual(changed['ledger'],cancelled['ledger'])
        self.assertEqual(cancelled,before)

    def test_an_invalid_checkpoint_falls_back_to_a_new_plan_instead_of_blocking(self):
        cancelled=self.cancel_after_first_financial()
        changed=self.agent.archive.get(cancelled['id'])
        changed['draft_acquisition_checkpoint']['scope']['task_id']='foreign'
        self.agent.archive.save(changed)
        result=self.agent.start(cancelled['id'])
        self.assertTrue(result.get('delivery'),result.get('gaps'))
        self.assertEqual(self.model.calls,['draft_plan','draft_plan','draft_report'])
        self.assertEqual(result['ledger']['used']['model_calls'],3)
        self.assertEqual(len(result['draft_report']['facts']),8)


if __name__=='__main__':
    unittest.main()
