"""Host-authored drafts finish locally without losing prose or recording a second author reply."""
from copy import deepcopy
from pathlib import Path
import socket,tempfile,threading,unittest
from types import SimpleNamespace
from unittest.mock import patch
from tests import ROOT
from core.session import Session
from researchhelper.contracts import ContractError,fingerprint
from researchhelper.draft_workflow import retained_submission,_writing_messages
from researchhelper.service import workspace_task_view

PAPER=dict(title='宿主原文恢复',conclusion='完整保留已经提交的研究正文。',
    logics=[dict(title='条件观察',analysis='在现有资料范围内说明成立条件与待验证部分。')])


class HostWritingRecoveryTests(unittest.TestCase):
    def setUp(self):
        project=tempfile.TemporaryDirectory(prefix='researchhelper-host-original-');self.addCleanup(project.cleanup)
        self.session=Session(project.name);self.addCleanup(self.session.agent.close_runtime)
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external requests'))
        guard.start();self.addCleanup(guard.stop)
    def fail_after_submission(self,report=None):
        task=self.session.create('合成检查：宿主已提交原文，不重新撰写。')
        with patch('researchhelper.draft_workflow.normalize_report',side_effect=OSError('Controlled local composition failure')):
            task=self.session.draft(task['id'],report=deepcopy(PAPER if report is None else report))
        self.assertEqual(task['status'],'失败');self.assertFalse(task.get('pending_content'))
        self.assertEqual(task['draft_submissions'][-1]['version'],2);self.assertIsNotNone(retained_submission(task))
        return task
    def saved(self,before,done):
        self.assertTrue(done.get('delivery'));self.assertIsNone(done.get('error'))
        self.assertEqual(done['draft_report']['conclusion'],PAPER['conclusion']);self.assertEqual(done['draft_report']['origin'],'host')
        self.assertEqual(done['draft_submissions'],before['draft_submissions'])
        for name in ('id','version','batch','input_hash','cutoff'):self.assertEqual(done[name],before[name])
        self.assertEqual(done['ledger']['used']['model_calls'],0);self.assertEqual(done['ledger']['used']['data_requests'],0)
        self.assertIn(PAPER['conclusion'],self.session.archive.report(done['id'],1)['html'])
    def test_tools_and_app_expose_the_same_local_composition_checkpoint(self):
        task=self.fail_after_submission();descriptor=self.session.tools(task['id'])['delivery_recovery']
        self.assertTrue(descriptor['retained_submission']);self.assertTrue(descriptor['local_composition'])
        self.assertFalse(descriptor['retained_content']);self.assertFalse(descriptor['local_presentation_only'])
        self.assertFalse(descriptor['local_model_calls']);self.assertFalse(descriptor['data_requests'])
        self.assertTrue(workspace_task_view(task)['has_retained_submission'])
        self.saved(task,self.session.retry_save(task['id']))
    def test_default_draft_finishes_original_prose_instead_of_an_outline(self):
        task=self.fail_after_submission()
        with patch('researchhelper.draft_workflow._acquire',side_effect=AssertionError('Do not reacquire while saving')):
            self.saved(task,self.session.draft(task['id']))
    def test_resume_finishes_the_same_batch_without_duplicate_author_records(self):
        task=self.fail_after_submission()
        with patch.object(self.session.agent,'start',side_effect=AssertionError('No research restart')):
            self.saved(task,self.session.resume(task['id']))
    def test_local_recovery_remains_available_after_reopening_the_project(self):
        task=self.fail_after_submission();project=self.session.project;self.session.agent.close_runtime()
        resumed=Session(project);self.addCleanup(resumed.agent.close_runtime)
        done=resumed.resume(task['id']);self.saved(task,done);self.assertIsNone(resumed.agent.model)

    def test_exhausted_budget_keeps_retained_host_prose_without_offering_a_retry(self):
        task = self.fail_after_submission()
        task['ledger']['used']['seconds'] = task['ledger']['limits']['seconds']
        self.session.archive.save(task)
        before = self.session.status(task['id'])
        view = workspace_task_view(before)
        for scope in ('all', 'first_report'):
            tools = self.session.tools(task['id'], scope=scope)
            self.assertTrue(tools['delivery_recovery']['retained_submission'])
            self.assertIsNone(tools['delivery_recovery']['action'])
            self.assertEqual(tools['recovery_block'], view['recovery_block'])
        with patch('researchhelper.draft_workflow.restore_report', side_effect=AssertionError('No new local composition')):
            for action in (self.session.draft, self.session.resume, self.session.retry_save):
                with self.assertRaisesRegex(ContractError, '执行时间.*正文已保留'):
                    action(task['id'])
        self.assertEqual(self.session.status(task['id']), before)

    def test_exhausted_time_cannot_dispatch_an_existing_acquisition_checkpoint(self):
        task = self.session.create('合成检查：取证检查点没有剩余执行时间。')
        task = self.session.draft(task['id'], defer_delivery=True)
        self.assertEqual(task['status'], '待开始')
        self.assertGreater(task['batch'], 0)
        task['ledger']['used']['seconds'] = task['ledger']['limits']['seconds']
        self.session.archive.save(task)
        before = self.session.status(task['id'])
        tools = self.session.tools(task['id'], scope='first_report')
        self.assertNotIn('delivery_recovery', tools)
        self.assertEqual(tools['recovery_block']['code'], 'execution_time_exhausted')
        with self.assertRaisesRegex(ContractError, '执行时间已用尽'):
            self.session.draft(task['id'], report=PAPER)
        self.assertEqual(self.session.status(task['id']), before)

    def test_data_only_checkpoint_carries_local_and_metered_seconds_without_counting_twice(self):
        from researchhelper import draft_analysis, draft_workflow

        task = self.session.create('合成检查：取证检查点继承执行时长。')
        task['ledger']['used']['seconds'] = 7
        self.session.archive.save(task)
        clock = [100.0]
        analyze = draft_analysis.analyze

        def timed_read(engine, active, requests):
            active['ledger']['used']['seconds'] += 2
            clock[0] += 2

        def timed_analysis(*args, **kwargs):
            clock[0] += 3
            return analyze(*args, **kwargs)

        with patch('researchhelper.engine.time', SimpleNamespace(monotonic=lambda: clock[0])), \
                patch.object(draft_workflow, '_acquire', side_effect=timed_read), \
                patch.object(draft_analysis, 'analyze', side_effect=timed_analysis):
            checkpoint = self.session.draft(task['id'], defer_delivery=True)
        self.assertEqual(checkpoint['status'], '待开始')
        self.assertEqual(checkpoint['ledger']['used']['seconds'], 12)
        self.assertEqual(self.session.status(task['id'])['ledger'], checkpoint['ledger'])
        done = self.session.draft(task['id'], report=PAPER)
        self.assertTrue(done['delivery'])
        self.assertGreaterEqual(done['ledger']['used']['seconds'], 12)
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertEqual(done['ledger']['used']['data_requests'], 0)

    def test_late_draft_checkpoint_preserves_a_material_attached_during_writing(self):
        from researchhelper import draft_workflow

        task = self.session.create('合成检查：撰写中收到新的原件。')
        entered, release = threading.Event(), threading.Event()
        normalize = draft_workflow.normalize_report
        returned = []

        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('Controlled writing barrier did not release')
            return normalize(*args, **kwargs)

        with patch.object(draft_workflow, 'normalize_report', side_effect=delayed):
            worker = threading.Thread(target=lambda: returned.append(self.session.draft(task['id'], report=PAPER)))
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                material = self.session.material(task['id'], '补充材料.txt', '保留本次写作期间上传的原件。'.encode())
            finally:
                release.set()
                worker.join(5)
                self.assertFalse(worker.is_alive())
        stopped, = returned
        self.assertIn(material['id'], [item['id'] for item in stopped['materials']])
        self.assertEqual(stopped['status'], '失败')
        self.assertFalse(stopped['delivery'])
        self.assertIsNone(retained_submission(stopped))

        done = self.session.draft(task['id'], report=PAPER)
        self.assertTrue(done['delivery'])
        self.assertIn(material['id'], [item['id'] for item in done['materials']])
        self.assertEqual((done['version'], done['batch'], done['cutoff']), (1, 2, task['cutoff']))
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertEqual(done['ledger']['used']['data_requests'], 0)

    def test_repeated_local_failure_keeps_one_original_reply(self):
        task=self.fail_after_submission()
        with patch('researchhelper.draft_workflow.normalize_report',side_effect=OSError('Still local')):
            with self.assertRaises(OSError):self.session.resume(task['id'])
        current=self.session.status(task['id']);self.assertEqual(current['draft_submissions'],task['draft_submissions'])
        self.saved(task,self.session.resume(task['id']))
    def test_explicit_new_text_wins_and_only_the_latest_reply_is_recovered(self):
        task=self.fail_after_submission();replacement=deepcopy(PAPER);replacement['conclusion']='显式提交的修订正文。'
        with patch('researchhelper.draft_workflow.normalize_report',side_effect=OSError('New text needs local recovery')):
            latest=self.session.draft(task['id'],report=replacement)
        self.assertEqual(len(latest['draft_submissions']),2);self.assertEqual(retained_submission(latest)['payload'],replacement)
        done=self.session.resume(task['id']);self.assertTrue(done.get('delivery'))
        self.assertEqual(done['draft_report']['conclusion'],replacement['conclusion']);self.assertEqual(done['draft_submissions'],latest['draft_submissions'])
    def test_a_legacy_latest_edit_does_not_fall_back_to_an_older_reply(self):
        task=self.fail_after_submission();row=deepcopy(task['draft_submissions'][-1]);row.update(version=1,payload=dict(PAPER,conclusion='后续旧格式修订。'))
        row.pop('writing_hash');row['payload_hash']=fingerprint(row['payload']);row['id']=fingerprint({k:v for k,v in row.items() if k not in ('id','received_at')})
        task['draft_submissions'].append(row);self.session.archive.save(task)
        self.assertIsNone(retained_submission(task));self.assertFalse(workspace_task_view(task)['has_retained_submission'])
    def test_changed_workpaper_does_not_silently_reuse_old_prose(self):
        task=self.fail_after_submission();task['questions'][0]['answer']='新的分析判断，须重新采用。';self.session.archive.save(task)
        before=deepcopy(self.session.status(task['id']));self.assertIsNone(retained_submission(before))
        with self.assertRaises(ContractError):self.session.retry_save(task['id'])
        self.assertEqual(self.session.status(task['id']),before)
    def test_invalid_arguments_are_not_bypassed_by_automatic_local_recovery(self):
        task=self.fail_after_submission();before=deepcopy(task)
        for options in (dict(defer_delivery=0),dict(data_requests={}),dict(data_requests='bad')):
            with self.subTest(options=options),self.assertRaises(ContractError):self.session.draft(task['id'],**options)
        self.assertEqual(self.session.status(task['id']),before)
    def test_optional_bad_requests_do_not_block_explicit_useful_prose(self):
        task=self.session.create('合成检查：可用正文不被取数格式问题阻断。')
        done=self.session.draft(task['id'],report=PAPER,data_requests='bad')
        self.assertTrue(done.get('delivery'));self.assertEqual(done['draft_report']['conclusion'],PAPER['conclusion'])
        self.assertEqual(done['ledger']['used']['data_requests'],0)
    def test_model_reply_binding_remains_compatible_with_the_existing_v2_format(self):
        from tests import test_delivery_recovery as delivery
        control=delivery.DeliveryRecoveryTests();control.setUp();self.addCleanup(control.doCleanups)
        task=control.create()
        with patch('researchhelper.draft_workflow.normalize_report',side_effect=OSError('Local model composition failure')):failed=control.agent.start(task['id'])
        from researchhelper.drafts import observations
        row=failed['draft_submissions'][-1];self.assertEqual(row['writing_hash'],fingerprint(_writing_messages(failed,observations(failed))))
        self.assertTrue(control.agent.retry_save(task['id']).get('delivery'))
    def test_unavailable_binding_keeps_a_private_legacy_record_without_blocking_first_delivery(self):
        task=self.session.create('合成检查：上下文记录失败仍可正常交付。')
        with patch('researchhelper.draft_workflow._submission_binding',side_effect=ContractError('Unavailable binding')):
            done=self.session.draft(task['id'],report=PAPER)
        self.assertTrue(done.get('delivery'));self.assertEqual(done['draft_submissions'][0]['version'],1)
        self.assertEqual(done['draft_report']['conclusion'],PAPER['conclusion'])
    def test_resuming_a_legacy_host_reply_does_not_relabel_it_as_a_new_submission(self):
        task=self.fail_after_submission();row=task['draft_submissions'][-1];row['version']=1;row.pop('writing_hash')
        row['id']=fingerprint({k:v for k,v in row.items() if k not in ('id','received_at')})
        for revision in task['revisions']:revision.pop('draft_report_supplied',None)
        self.session.archive.save(task);original=deepcopy(task['draft_submissions'])
        self.assertIsNone(retained_submission(task));done=self.session.resume(task['id'])
        self.assertTrue(done.get('delivery'));self.assertEqual(done['draft_submissions'],original)

if __name__=='__main__':unittest.main()
