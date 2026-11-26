"""Resume a paid draft plan after cancellation without buying it again."""
from unittest.mock import patch
import copy
import unittest

from tests import test_draft_first_workflow as financial
from researchhelper import draft_workflow
from researchhelper.contracts import charge
from researchhelper.evidence import snapshot_record


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

    def cancel_after_first_financial(self,limits=None,*,with_prior_quote=False):
        task=self.agent.create(financial.QUESTION,research_mode='current',limits=limits)
        if with_prior_quote:
            params=dict(codes='002594.SZ',indicators='close',startdate='2026-10-15',enddate='2026-10-15',
                        functionpara=dict(Currency='RMB',CPS='1',Fill='Omit'))
            snapshot=dict(errorcode=0,tables=[dict(thscode='002594.SZ',time=['2026-10-15'],table=dict(close=[112]))])
            task['source_records'].append(snapshot_record(task,'ifind','cmd_history_quotation',params,snapshot))
            self.agent.archive.save(task)
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

    def test_policy_checkpoint_without_dispatch_continues_after_cancellation(self):
        task=self.agent.create(financial.QUESTION,research_mode='current',limits=dict(model_calls=2))
        checkpoint=self.agent._save_model_checkpoint

        def persist_then_cancel(active):
            checkpoint(active)
            attempts=active.get('source_attempts', {}).values()
            if any(row['operation']=='get_limit_policy' and row['status']=='待响应' for row in attempts):
                self.agent.cancel(active['id'])
                self.agent._check_run_control(active)

        with patch.object(self.agent,'_save_model_checkpoint',side_effect=persist_then_cancel):
            cancelled=self.agent.start(task['id'])
        self.assertEqual(cancelled['status'],'已取消')
        self.assertEqual(cancelled['ledger']['used']['data_requests'],0)
        self.assertEqual(self.model.calls,['draft_plan'])
        self.assertIsNotNone(draft_workflow._retained_plan(cancelled))
        resumed=self.check_resumed(cancelled)
        self.assertEqual(sum(row.get('operation')=='get_limit_policy' for row in resumed['ledger']['dispatches']),1)

    def stop_before_financial_request(self, *, reserve=False, unknown_auth=False):
        task = self.agent.create(financial.QUESTION, research_mode='current', limits=dict(model_calls=2))
        checkpoint = self.agent._save_model_checkpoint
        stopped = []

        def stop(active):
            checkpoint(active)
            pending = next(((digest, row) for digest, row in active.get('source_attempts', {}).items()
                            if row['operation'] == 'basic_data_service' and row['status'] == '待响应'), None)
            if stopped or pending is None:
                return
            digest, row = pending
            stopped.append(digest)
            if reserve or unknown_auth:
                charge(active['ledger'], 'data_requests')
                active['ledger']['dispatches'].append(dict(kind='data_requests', source='ifind', batch=active['batch'],
                    operation=row['operation'] if reserve else 'get_access_token',
                    fingerprint=digest if reserve else 'unknown-auth-fixture', status='已派发'))
            if unknown_auth:
                self.agent._journal(active)
            self.agent.cancel(active['id'])
            if reserve:
                self.agent._journal(active)
            self.agent._check_run_control(active)

        with patch.object(self.agent, '_save_model_checkpoint', side_effect=stop):
            cancelled = self.agent.start(task['id'])
        digest, = stopped
        self.assertEqual(cancelled['status'], '已取消')
        self.assertFalse(cancelled['delivery'])
        self.assertEqual(self.model.calls, ['draft_plan'])
        return cancelled, digest

    def test_resume_dispatches_a_financial_request_that_was_only_planned(self):
        cancelled, digest = self.stop_before_financial_request()
        resumed = self.check_resumed(cancelled)
        self.assertEqual(sum(row.get('fingerprint') == digest for row in resumed['ledger']['dispatches']), 1)

    def test_cancel_before_journal_does_not_charge_or_strand_the_financial_request(self):
        cancelled, digest = self.stop_before_financial_request(reserve=True)
        self.assertEqual(cancelled['ledger']['used']['data_requests'], 1)
        self.assertFalse(any(row.get('fingerprint') == digest for row in cancelled['ledger']['dispatches']))
        self.check_resumed(cancelled)

    def test_resume_cannot_replay_a_request_after_its_authentication_result_is_unknown(self):
        cancelled, digest = self.stop_before_financial_request(unknown_auth=True)
        self.assertEqual(cancelled['ledger']['used']['data_requests'], 2)
        resumed = self.agent.start(cancelled['id'])
        self.assertTrue(resumed['delivery'])
        self.assertFalse(any(row.get('fingerprint') == digest for row in resumed['ledger']['dispatches']))
        self.assertEqual(sum(row.get('fingerprint') == 'unknown-auth-fixture' for row in resumed['ledger']['dispatches']), 1)
        self.assertEqual(len(resumed['draft_report']['facts']), 4)


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
        cancelled=self.cancel_after_first_financial(with_prior_quote=True)
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
