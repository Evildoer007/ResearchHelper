"""Completed prose survives failed presentation, cancellation and save retries."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json,socket,tempfile,unittest
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock,patch
from tests import ROOT
from core.session import Session
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError,charge,fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper import draft_workflow

PAPER=dict(title='合成正文恢复检查',conclusion='仅用于检验交付恢复。',
    logics=[dict(title='已生成的正文',analysis='这段完整正文应保留，不重新撰写。')])


class RecordingModel:
    def __init__(self):self.calls=[]
    def reason(self,task,messages,**options):
        phase=options['phase'];self.calls.append(phase)
        charge(task['ledger'],'model_calls',1,delivery=options.get('delivery',False))
        return json.dumps(dict(data_requests=[])) if phase=='draft_plan' else json.dumps(PAPER)


class DeliveryRecoveryTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='delivery-recovery-',dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup);self.directory=folder.name;self.archive=ResearchArchive(folder.name)
        self.model=RecordingModel();self.agent=ResearchAgent(self.archive,ROOT,offline=False,model=self.model,
            source_credentials={},require_argument_support=True,draft_first=True)
        self.addCleanup(self.agent.close_runtime)
        self.transport=Mock(side_effect=ContractError('合成传输未取数'));self.agent.collector.external=self.transport
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external request'))
        guard.start();self.addCleanup(guard.stop)
    def create(self):
        task=self.agent.create('合成检查：保留已经生成的正文。',research_mode='current')
        task['synthetic']=True;self.archive.save(task);return task
    def render_failure(self):
        task=self.create()
        with patch('researchhelper.reports.render',side_effect=OSError('synthetic render failure')):
            failed=self.agent.start(task['id'])
        self.assertEqual(failed['status'],'失败');self.assertTrue(failed.get('pending_content'))
        self.assertFalse(failed.get('pending_html'));self.assertEqual(self.model.calls,['draft_plan','draft_report'])
        return failed
    def save_failure(self):
        task=self.create()
        with patch.object(self.archive,'deliver',side_effect=OSError('synthetic storage failure')):
            failed=self.agent.start(task['id'])
        self.assertTrue(failed.get('pending_content') and failed.get('pending_html'));return failed
    def assert_saved_without_research(self,before,done):
        self.assertTrue(done.get('delivery'),done.get('error'));self.assertIsNone(done.get('error'))
        self.assertEqual(done['status'],'部分完成')
        self.assertNotIn('研究草稿已生成，独立核验可在后续版本补充。',done['gaps'])
        self.assertEqual(self.model.calls,['draft_plan','draft_report'])
        self.assertEqual(done['draft_report']['origin'],'model')
        for key in ('version','batch','cutoff','input_hash'):self.assertEqual(done[key],before[key])
        for key in ('model_calls','data_requests'):self.assertEqual(done['ledger']['used'][key],before['ledger']['used'][key])
        self.assertIn(PAPER['logics'][0]['analysis'],self.archive.report(done['id'],done['version'])['html'])
        for key in ('pending_content', 'pending_html', 'pending_html_binding'):
            self.assertNotIn(key, done)
        render=next(row for row in done['nodes'] if row['id']=='render')
        self.assertEqual(render['status'],'成功');self.assertNotIn('error',render)
    def test_render_failure_keeps_content_and_cannot_trigger_a_third_model_call(self):
        failed=self.render_failure();content=deepcopy(failed['pending_content']);self.transport.reset_mock()
        with self.assertRaisesRegex(ContractError,'重试保存'):self.agent.start(failed['id'])
        with patch.object(self.model,'reason',side_effect=AssertionError('No model retry')):
            done=self.agent.retry_save(failed['id'])
        self.assert_saved_without_research(failed,done);self.transport.assert_not_called()
        self.assertEqual(self.archive.report(done['id'],done['version'])['content'],content)
    def test_restart_keeps_the_report_checkpoint_and_used_budget(self):
        failed=self.render_failure();self.agent.close_runtime()
        resumed=ResearchAgent(ResearchArchive(self.directory),ROOT,offline=False,model=self.model,
            source_credentials={},require_argument_support=True,draft_first=True)
        self.addCleanup(resumed.close_runtime)
        with patch.object(self.model,'reason',side_effect=AssertionError('No new model call')):
            done=resumed.retry_save(failed['id'])
        self.assert_saved_without_research(failed,done)
    def test_repeated_render_failure_keeps_the_same_content_for_a_later_retry(self):
        failed=self.render_failure();content=deepcopy(failed['pending_content']);used=deepcopy(failed['ledger']['used'])
        with patch('researchhelper.reports.render',side_effect=OSError('second render failure')):
            with self.assertRaises(OSError):self.agent.retry_save(failed['id'])
        current=self.archive.get(failed['id']);self.assertEqual(current['pending_content'],content)
        self.assertNotIn('pending_html',current);self.assertIn('页面生成失败',current['error'])
        self.assertGreaterEqual(current['ledger']['used']['seconds'],used['seconds']+.001)
        done=self.agent.retry_save(failed['id']);self.assert_saved_without_research(failed,done)

    def test_render_retry_does_not_block_other_tasks_and_keeps_its_own_slot(self):
        from researchhelper.reports import render

        failed = self.render_failure()
        ident = failed['id']
        other = self.agent.create_case('value')['id']
        rendering, release = Event(), Event()

        def held_render(content, *args, **kwargs):
            if content['task_id'] == ident:
                rendering.set()
                if not release.wait(10):
                    raise TimeoutError('Held renderer was not released')
            return render(content, *args, **kwargs)

        def other_status():
            # The service reads status and busy state under the same lock.
            with self.agent.lock:
                return self.archive.get(other)['status'], self.agent.run_pending(other)

        with patch('researchhelper.reports.render', side_effect=held_render), ThreadPoolExecutor(3) as pool:
            saving = pool.submit(self.agent.retry_save, ident)
            try:
                self.assertTrue(rendering.wait(3), 'Save retry never reached its renderer')
                self.assertEqual(pool.submit(other_status).result(timeout=1), ('待开始', False))
                self.assertTrue(self.agent.run_pending(ident))
                with self.assertRaisesRegex(ContractError, '稍后重试保存'):
                    pool.submit(self.agent.retry_save, ident).result(timeout=1)
                other_result = pool.submit(self.agent.start, other).result(timeout=3)
                self.assertEqual(other_result['status'], '已完成')
            finally:
                release.set()
            done = saving.result(timeout=5)
        self.assert_saved_without_research(failed, done)
        self.assertFalse(self.agent.run_pending(ident))
        self.assertEqual(len(self.archive.delivery_versions(ident)), 1)
        self.assertEqual(len(self.archive.delivery_versions(other)), 1)

    def test_render_retry_cannot_overwrite_a_newer_revision_of_the_same_task(self):
        from researchhelper.reports import render

        failed = self.render_failure()
        ident = failed['id']
        concurrent_archive = ResearchArchive(self.directory)

        def revise_during_render(content, *args, **kwargs):
            current = concurrent_archive.get(ident)
            current['revisions'].append({'action': '并发修订'})
            concurrent_archive.save(current)
            return render(content, *args, **kwargs)

        with patch('researchhelper.reports.render', side_effect=revise_during_render):
            with self.assertRaisesRegex(ContractError, '任务已更新'):
                self.agent.retry_save(ident)
        current = self.archive.get(ident)
        self.assertEqual(current['revisions'][-1]['action'], '并发修订')
        self.assertEqual(current['pending_content'], failed['pending_content'])
        self.assertFalse(self.archive.delivery_versions(ident))
        self.assertFalse(self.agent.run_pending(ident))
        self.assert_saved_without_research(failed, self.agent.retry_save(ident))
    def test_existing_html_is_reused_and_old_runtime_error_clears_only_on_success(self):
        failed=self.save_failure();page=failed['pending_html'];self.assertTrue(failed['error'])
        with patch('researchhelper.reports.render',side_effect=AssertionError('Do not render saved HTML again')):
            done=self.agent.retry_save(failed['id'])
        self.assert_saved_without_research(failed,done)
        self.assertEqual(self.archive.report(done['id'],done['version'])['html'],page)

    def test_save_retry_rebuilds_html_that_belongs_to_another_report(self):
        failed = self.save_failure()
        other = self.agent.create('合成检查：另一份报告。', research_mode='current')
        foreign = dict(title='另一份报告', conclusion='这是另一项研究。', logics=[])
        other = self.agent.start(other['id'], dict(draft_report=foreign, data_requests=[]))
        original = self.archive.report(other['id'], 1)
        failed['pending_html'] = original['html']
        self.archive.save(failed)
        self.agent.close_runtime()
        resumed = ResearchAgent(ResearchArchive(self.directory), ROOT, offline=True, draft_first=True)
        self.addCleanup(resumed.close_runtime)

        done = resumed.retry_save(failed['id'])

        self.assert_saved_without_research(failed, done)
        saved = resumed.archive.report(done['id'], 1)
        self.assertEqual(saved['content'], failed['pending_content'])
        self.assertNotEqual(done['delivery']['html_sha256'], other['delivery']['html_sha256'])
        self.assertEqual(resumed.archive.report(other['id'], 1), original)

    def test_save_retry_checks_bytes_even_when_embedded_report_identity_is_unchanged(self):
        failed = self.save_failure()
        edited = failed['pending_html'].replace(PAPER['logics'][0]['analysis'], '页面正文已发生变化。', 1)
        self.assertIn('页面正文已发生变化。', edited)
        failed['pending_html'] = edited
        self.archive.save(failed)

        done = self.agent.retry_save(failed['id'])

        self.assert_saved_without_research(failed, done)
        self.assertNotIn('页面正文已发生变化。', self.archive.report(done['id'], 1)['html'])

    def test_unbound_legacy_page_is_rendered_from_retained_content(self):
        failed = self.save_failure()
        expected = failed.pop('pending_html_binding')
        self.archive.save(failed)
        from researchhelper.reports import render

        with patch('researchhelper.reports.render', wraps=render) as renderer:
            done = self.agent.retry_save(failed['id'])

        renderer.assert_called_once()
        self.assert_saved_without_research(failed, done)
        self.assertEqual(done['delivery']['html_sha256'], expected['html_sha256'])

    def test_renderer_returning_another_profile_cannot_freeze_a_client_report(self):
        failed = self.render_failure()
        from researchhelper.reports import render
        detailed = render(failed['pending_content'], ROOT, report_profile='detailed')

        with patch('researchhelper.reports.render', return_value=detailed):
            with self.assertRaisesRegex(ContractError, '详略'):
                self.agent.retry_save(failed['id'])

        pending = self.archive.get(failed['id'])
        self.assertFalse(pending.get('delivery'))
        self.assertNotIn('pending_html', pending)
        self.assertNotIn('pending_html_binding', pending)
        done = self.agent.retry_save(failed['id'])
        self.assert_saved_without_research(failed, done)

    def test_successful_render_is_durable_even_if_the_following_save_fails(self):
        failed=self.render_failure()
        with patch.object(self.archive,'deliver',side_effect=OSError('storage after rendering failed')):
            with self.assertRaises(OSError):self.agent.retry_save(failed['id'])
        current=self.archive.get(failed['id']);self.assertTrue(current['pending_html'])
        self.assertEqual(current['pending_content'],failed['pending_content']);self.assertIn('尚未保存',current['error'])
        page=current['pending_html']
        with patch('researchhelper.reports.render',side_effect=AssertionError('Reuse durable HTML')):
            done=self.agent.retry_save(failed['id'])
        self.assert_saved_without_research(failed,done);self.assertEqual(self.archive.report(done['id'],1)['html'],page)
    def test_cancelled_content_checkpoint_can_be_explicitly_saved_after_work_stops(self):
        task=self.create();checkpoint=draft_workflow._checkpoint;cancelled=[]
        def persist_then_cancel(engine,current):
            checkpoint(engine,current)
            if current.get('pending_content') and not cancelled:
                cancelled.append(True);self.agent.cancel(current['id'])
        with patch.object(draft_workflow,'_checkpoint',side_effect=persist_then_cancel):failed=self.agent.start(task['id'])
        self.assertTrue(cancelled);self.assertEqual(failed['status'],'已取消');self.assertTrue(failed['pending_content'])
        done=self.agent.retry_save(task['id']);self.assert_saved_without_research(failed,done)
    def test_a_still_running_worker_cannot_overlap_the_save_retry(self):
        failed=self.render_failure();before=deepcopy(self.archive.get(failed['id']))
        with patch.dict(self.agent.workers,{failed['id']:SimpleNamespace(is_alive=lambda:True)}):
            with self.assertRaisesRegex(ContractError,'仍在停止'):self.agent.retry_save(failed['id'])
        self.assertEqual(self.archive.get(failed['id']),before)
    def test_changed_or_foreign_content_is_rejected_before_rendering_or_charging(self):
        failed=self.render_failure();original=deepcopy(failed['pending_content'])
        for field,value in [('title','changed title'),('task_id','foreign'),('research_version',2)]:
            task=self.archive.get(failed['id']);task['pending_content']=deepcopy(original);task['pending_content'][field]=value
            content=task['pending_content'];content['content_hash']=fingerprint({k:v for k,v in content.items() if k!='content_hash'})
            self.archive.save(task);before=deepcopy(self.archive.get(task['id']))
            with self.subTest(field=field),patch('researchhelper.reports.render',side_effect=AssertionError('Do not render stale content')):
                with self.assertRaises(ContractError):self.agent.retry_save(task['id'])
            self.assertEqual(self.archive.get(task['id']),before)
    def test_formal_report_can_finish_its_local_render_node_without_repeating_research(self):
        agent=ResearchAgent(self.archive,ROOT,offline=True);self.addCleanup(agent.close_runtime)
        task=agent.create_case('etf')
        with patch('researchhelper.reports.render',side_effect=OSError('formal render failure')):failed=agent.start(task['id'])
        self.assertEqual(failed['status'],'失败');self.assertTrue(failed.get('pending_content'));self.assertFalse(failed.get('pending_html'))
        before=deepcopy(failed['ledger']['used']);done=agent.retry_save(task['id'])
        self.assertTrue(done.get('delivery'));self.assertIsNone(done['error'])
        for key in ('model_calls','data_requests'):self.assertEqual(done['ledger']['used'][key],before[key])
        node=next(row for row in done['nodes'] if row['id']=='render');self.assertEqual(node['status'],'成功')
        self.assertEqual(node['result']['status'],'成功')
    def test_skill_discovers_and_uses_the_same_local_recovery(self):
        project=tempfile.TemporaryDirectory(prefix='researchhelper-skill-recovery-')
        self.addCleanup(project.cleanup)
        session=Session(project.name);session.agent.resources=ROOT;self.addCleanup(session.agent.close_runtime)
        task=session.create('合成宿主报告恢复。')
        with patch('researchhelper.reports.render',side_effect=OSError('skill render failure')):
            failed=session.draft(task['id'],report=PAPER,data_requests=[])
        self.assertTrue(failed.get('pending_content'));self.assertFalse(failed.get('pending_html'))
        self.assertEqual(session.tools(task['id'])['delivery_recovery']['action'],'retry_save(task_id)')
        done=session.retry_save(task['id']);self.assertTrue(done.get('delivery'));self.assertIsNone(done['error'])
        self.assertEqual(done['ledger']['used']['model_calls'],0);self.assertEqual(done['ledger']['used']['data_requests'],0)
    def test_no_report_checkpoint_does_not_create_a_save_or_charge(self):
        task=self.create();before=deepcopy(task)
        with self.assertRaises(ContractError):self.agent.retry_save(task['id'])
        self.assertEqual(self.archive.get(task['id']),before)

if __name__=='__main__':unittest.main()
