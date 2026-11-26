"""A paid, completed reply survives local composition failure with no paid replay."""
from copy import deepcopy
import json,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from tests import ROOT
from tests import test_delivery_recovery as delivery
PAPER=delivery.PAPER
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError,fingerprint
from researchhelper.draft_workflow import retained_submission
from researchhelper.engine import ResearchAgent
from researchhelper.service import workspace_task_view


class DraftWritingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.control=delivery.DeliveryRecoveryTests();self.control.setUp();self.addCleanup(self.control.doCleanups)
        self.agent=self.control.agent;self.archive=self.control.archive;self.model=self.control.model
    def fail_after_reply(self,limit=8):
        task=self.agent.create('合成检查：原文恢复不重新研究。',research_mode='current',
            limits=dict(model_calls=limit,data_requests=100,seconds=600))
        task['synthetic']=True;self.archive.save(task)
        with patch('researchhelper.draft_workflow.normalize_report',side_effect=OSError('Controlled local composition failure')):
            failed=self.agent.start(task['id'])
        self.assertEqual(failed['status'],'失败');self.assertEqual(len(failed['draft_submissions']),1)
        self.assertEqual(failed['draft_submissions'][0]['version'],2)
        self.assertIsNotNone(retained_submission(failed));self.assertFalse(failed.get('pending_content'))
        return failed
    def assert_local_save(self,before,done):
        self.assertTrue(done.get('delivery'),done.get('error'));self.assertIsNone(done.get('error'))
        self.assertEqual(done['draft_report']['origin'],'model')
        self.assertEqual(done['draft_report']['conclusion'],PAPER['conclusion'])
        self.assertEqual(done['draft_submissions'],before['draft_submissions'])
        self.assertEqual(self.model.calls,['draft_plan','draft_report'])
        for field in ('id','version','batch','cutoff','input_hash'):self.assertEqual(done[field],before[field])
        for field in ('model_calls','data_requests'):self.assertEqual(done['ledger']['used'][field],before['ledger']['used'][field])
        self.assertEqual(sum(row['action']=='复用已保存原文' for row in done['revisions']),1)
    def test_completed_reply_selects_local_save_instead_of_a_third_model_call(self):
        failed=self.fail_after_reply()
        view=workspace_task_view(failed)
        self.assertFalse(view['has_pending_content']);self.assertTrue(view['has_retained_submission'])
        with self.assertRaisesRegex(ContractError,'原文已保留'):self.agent.start(failed['id'])
        self.control.transport.reset_mock()
        with patch.object(self.model,'reason',side_effect=AssertionError('No paid replay')):
            done=self.agent.retry_save(failed['id'])
        self.assert_local_save(failed,done);self.control.transport.assert_not_called()
        self.assertFalse(workspace_task_view(done)['has_retained_submission'])
    def test_exhausted_two_call_budget_does_not_replace_completed_prose_with_an_outline(self):
        failed=self.fail_after_reply(2);done=self.agent.retry_save(failed['id']);self.assert_local_save(failed,done)
        self.assertEqual(done['ledger']['used']['model_calls'],done['ledger']['limits']['model_calls'])
    def test_offline_restart_can_finish_the_same_reply_without_model_configuration(self):
        failed=self.fail_after_reply(2);self.agent.close_runtime()
        restarted=ResearchAgent(ResearchArchive(self.control.directory),ROOT,offline=True,require_argument_support=True,draft_first=True)
        self.addCleanup(restarted.close_runtime)
        done=restarted.retry_save(failed['id']);self.assert_local_save(failed,done)
        self.assertIsNone(restarted.model)
    def test_repeated_local_composition_failure_keeps_original_reply_and_budget(self):
        failed=self.fail_after_reply(2)
        clock = [100.0]

        def fail_composition(*args, **kwargs):
            clock[0] += 2
            raise OSError('Still local')

        with patch('researchhelper.engine.time', SimpleNamespace(monotonic=lambda: clock[0])), \
                patch('researchhelper.draft_workflow.normalize_report', side_effect=fail_composition):
            with self.assertRaises(OSError):self.agent.retry_save(failed['id'])
        current=self.archive.get(failed['id']);self.assertEqual(current['draft_submissions'],failed['draft_submissions'])
        self.assertFalse(current.get('pending_content'));self.assertIn('正文整理失败',current['error'])
        self.assertAlmostEqual(current['ledger']['used']['seconds'],failed['ledger']['used']['seconds']+2)
        self.assert_local_save(failed,self.agent.retry_save(failed['id']))
    def test_content_composition_failure_still_keeps_the_complete_reply(self):
        failed=self.fail_after_reply(2)
        with patch('researchhelper.reports.compose',side_effect=OSError('Controlled content composition failure')):
            with self.assertRaises(OSError):self.agent.retry_save(failed['id'])
        current=self.archive.get(failed['id']);self.assertFalse(current.get('pending_content'))
        self.assertEqual(current['draft_submissions'],failed['draft_submissions']);self.assertIsNotNone(retained_submission(current))
        done=self.agent.retry_save(failed['id']);self.assertTrue(done.get('delivery'))
        self.assertEqual(done['draft_report']['conclusion'],PAPER['conclusion'])
        self.assertEqual(self.model.calls,['draft_plan','draft_report']);self.assertEqual(done['draft_submissions'],failed['draft_submissions'])
    def test_reply_restoration_checkpoints_content_before_a_later_render_failure(self):
        failed=self.fail_after_reply(2)
        with patch('researchhelper.reports.render',side_effect=OSError('Controlled render failure')):
            with self.assertRaises(OSError):self.agent.retry_save(failed['id'])
        current=self.archive.get(failed['id']);self.assertTrue(current['pending_content']);self.assertFalse(current.get('pending_html'))
        with patch('researchhelper.draft_workflow.normalize_report',side_effect=AssertionError('Reuse composed content')):
            done=self.agent.retry_save(failed['id'])
        self.assert_local_save(failed,done)
    def test_reply_restoration_reuses_completed_html_after_a_later_storage_failure(self):
        failed=self.fail_after_reply(2)
        with patch.object(self.archive,'deliver',side_effect=OSError('Controlled storage failure')):
            with self.assertRaises(OSError):self.agent.retry_save(failed['id'])
        current=self.archive.get(failed['id']);page=current['pending_html'];self.assertTrue(page)
        with patch('researchhelper.draft_workflow.normalize_report',side_effect=AssertionError('No new normalization')),patch('researchhelper.reports.render',side_effect=AssertionError('No new rendering')):
            done=self.agent.retry_save(failed['id'])
        self.assert_local_save(failed,done);self.assertEqual(self.archive.report(done['id'],1)['html'],page)
    def test_cancel_after_reply_checkpoint_can_be_locally_saved(self):
        from researchhelper import draft_workflow
        checkpoint=draft_workflow._checkpoint;stopped=[]
        def cancel(engine,current):
            checkpoint(engine,current)
            if current.get('draft_submissions') and not current.get('pending_content') and not stopped:
                stopped.append(True);self.agent.cancel(current['id'])
        task=self.control.create()
        with patch.object(draft_workflow,'_checkpoint',side_effect=cancel):failed=self.agent.start(task['id'])
        self.assertTrue(stopped);self.assertEqual(failed['status'],'已取消')
        self.assert_local_save(failed,self.agent.retry_save(failed['id']))
    def test_modified_foreign_or_legacy_records_do_not_gain_local_save_or_charge(self):
        failed=self.fail_after_reply();original=deepcopy(failed['draft_submissions'][0])
        cases=[dict(version=1),dict(version=True),dict(task_id='foreign'),dict(research_version=True),
            dict(research_version=2),dict(batch=True),dict(batch=failed['batch']+1),dict(input_hash='stale'),
            dict(source_binding='stale'),dict(facts_hash='stale'),dict(knowledge_hash='stale'),
            dict(writing_hash='stale'),dict(model_call_count=True),dict(model_call_count=3),dict(payload=None)]
        for change in cases:
            with self.subTest(change=change):
                task=self.archive.get(failed['id']);row=deepcopy(original);row.update(change)
                row['payload_hash']=fingerprint(row['payload']);row['id']=fingerprint({key:value for key,value in row.items() if key not in ('id','received_at')})
                task['draft_submissions']=[row];self.archive.save(task);before=deepcopy(self.archive.get(task['id']))
                self.assertIsNone(retained_submission(before));self.assertFalse(workspace_task_view(before)['has_retained_submission'])
                with self.assertRaises(ContractError):self.agent.retry_save(task['id'])
                self.assertEqual(self.archive.get(task['id']),before)
    def test_added_material_allows_research_to_continue_instead_of_reusing_old_prose(self):
        failed=self.fail_after_reply();self.agent.add_material(failed['id'],'changed.txt','新增研究材料。'.encode())
        current=self.archive.get(failed['id']);self.assertIsNone(retained_submission(current))
        done=self.agent.start(failed['id']);self.assertTrue(done.get('delivery'),done.get('error'))
        self.assertEqual(self.model.calls,['draft_plan','draft_report','draft_plan','draft_report'])
    def test_current_host_submission_can_explicitly_replace_the_unpublished_model_reply(self):
        failed=self.fail_after_reply();replacement=dict(title='用户修订',conclusion='显式采用新正文。',logics=[])
        done=self.agent.start(failed['id'],host_result=dict(data_requests=[],draft_report=replacement))
        self.assertTrue(done.get('delivery'));self.assertEqual(done['draft_report']['conclusion'],replacement['conclusion'])
        self.assertEqual(self.model.calls,['draft_plan','draft_report']);self.assertEqual(done['draft_submissions'][-1]['origin'],'host')
    def test_changed_writing_instructions_reject_reuse_without_spending_budget(self):
        failed=self.fail_after_reply();before=deepcopy(self.archive.get(failed['id']))
        with patch('researchhelper.draft_workflow._writing_messages',return_value=[dict(role='system',content='Changed writing contract')]):
            self.assertIsNone(retained_submission(before))
            with self.assertRaises(ContractError):self.agent.retry_save(failed['id'])
        self.assertEqual(self.archive.get(failed['id']),before)
    def test_changed_question_obligation_cannot_reuse_a_reply_with_a_stale_input_hash(self):
        failed=self.fail_after_reply();task=self.archive.get(failed['id'])
        task['questions'][0]['question']='改为核对不同的研究义务。'
        self.archive.save(task);before=deepcopy(self.archive.get(task['id']))
        self.assertIsNone(retained_submission(before))
        with self.assertRaises(ContractError):self.agent.retry_save(task['id'])
        self.assertEqual(self.archive.get(task['id']),before)
    def test_retained_financial_reply_keeps_all_facts_and_registered_arithmetic(self):
        from tests import test_draft_first_workflow as financial
        financial.DraftFirstWorkflowTests.setUpClass();self.addCleanup(financial.DraftFirstWorkflowTests.tearDownClass)
        control=financial.DraftFirstWorkflowTests();control.setUp();self.addCleanup(control.doCleanups)
        task=control.agent.create(financial.QUESTION,research_mode='current',limits=dict(model_calls=2,data_requests=100,seconds=600))
        with patch('researchhelper.draft_workflow.normalize_report',side_effect=OSError('Local normalization after financial reading')):
            failed=control.agent.start(task['id'])
        self.assertIsNotNone(retained_submission(failed))
        facts=failed['draft_submissions'][-1]['facts_hash'];analysis=deepcopy(failed['draft_analysis']['results'])
        calls=control.model.calls.copy()
        with patch.object(control.model,'reason',side_effect=AssertionError('No new paid financial analysis')):
            done=control.agent.retry_save(task['id'])
        self.assertTrue(done.get('delivery'));self.assertEqual(done['draft_report']['origin'],'model')
        self.assertEqual(len(done['draft_report']['facts']),8);self.assertEqual(fingerprint(done['draft_report']['facts']),facts)
        self.assertEqual(done['draft_report']['calculations'],analysis);self.assertEqual(control.model.calls,calls)
        self.assertEqual(done['ledger']['used']['data_requests'],failed['ledger']['used']['data_requests'])
    def test_public_skill_can_discover_and_finish_a_model_reply_without_its_own_model(self):
        from core.session import Session
        with tempfile.TemporaryDirectory(prefix='researchhelper-writing-skill-') as project:
            session=Session(project);writer=ResearchAgent(session.archive,ROOT,offline=False,model=self.model,
                require_argument_support=True,draft_first=True,source_credentials={})
            try:
                writer.collector.external=Mock(side_effect=ContractError('Synthetic transport has no data'))
                task=session.create('合成检查：宿主恢复已保存模型原文。')
                with patch('researchhelper.draft_workflow.normalize_report',side_effect=OSError('Local normalization')):
                    failed=writer.start(task['id'])
                descriptor=session.tools(task['id'])['delivery_recovery']
                self.assertTrue(descriptor['retained_submission']);self.assertFalse(descriptor['retained_content'])
                done=session.retry_save(task['id']);self.assert_local_save(failed,done)
                self.assertIsNone(session.agent.model)
            finally:writer.close_runtime();session.agent.close_runtime()

if __name__=='__main__':unittest.main()
