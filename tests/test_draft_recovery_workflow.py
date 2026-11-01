"""Completed bad plan text may use the reserved writer; transport failures do not retry."""
from unittest.mock import patch
from tests import test_draft_first_workflow as legacy
from researchhelper.contracts import ContractError,charge


class DraftRecoveryWorkflowTests(legacy.DraftFirstWorkflowTests):
    def test_bad_planning_format_does_not_block_data_outline(self):
        self.model.malformed_plan=True
        task,paper=self.run_task()
        self.assertEqual(self.model.calls,['draft_plan','draft_report'])
        self.assertEqual(task['draft_report']['origin'],'model')
        self.assertTrue(paper['content']['draft']['logics'])
        self.assertIn(legacy.REPORT['conclusion'],paper['html'])
        self.assertEqual(task['ledger']['used']['model_calls'],2)

    def test_transport_failure_does_not_trigger_a_second_paid_attempt(self):
        original=self.model.reason
        def failed(task,messages,*,phase,delivery=False,**options):
            if phase=='draft_plan':
                self.model.calls.append(phase);charge(task['ledger'],'model_calls',1,delivery=delivery)
                raise ContractError('模拟服务连接失败')
            return original(task,messages,phase=phase,delivery=delivery,**options)
        with patch.object(self.model,'reason',side_effect=failed):task,paper=self.run_task()
        self.assertEqual(self.model.calls,['draft_plan'])
        self.assertEqual(task['ledger']['used']['model_calls'],1)
        self.assertEqual(task['draft_report']['origin'],'data_outline')
        self.assertTrue(paper['html'])

    def test_bad_plan_and_failed_writer_still_save_without_a_third_call(self):
        self.model.malformed_plan=True;self.model.fail_report=True
        task,paper=self.run_task()
        self.assertEqual(self.model.calls,['draft_plan','draft_report'])
        self.assertEqual(task['ledger']['used']['model_calls'],2)
        self.assertEqual(task['draft_report']['origin'],'data_outline')
        self.assertTrue(paper['html']);self.assertIsNotNone(task['delivery'])
