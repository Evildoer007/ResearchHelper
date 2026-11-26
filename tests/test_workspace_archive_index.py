"""Transactional UI headers, full research integrity and lazy legacy fallback."""
from copy import deepcopy
import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.archive_codec import encode_body
from researchhelper.contracts import ContractError,canonical
from researchhelper.engine import ResearchAgent
from researchhelper.workspace_views import workspace_task_summary


class WorkspaceArchiveIndexTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='rh-workspace-index-');self.addCleanup(folder.cleanup)
        self.archive=ResearchArchive(folder.name)
        self.agent=ResearchAgent(self.archive,ROOT,offline=True);self.addCleanup(self.agent.close_runtime)
        self.task=self.agent.create('任务摘要隔离样本')
        self.task.update(owner_account='admin',pending_content={'synthetic_text':'隔离数据。'*40000})
        self.archive.save(self.task)
        guard=patch('socket.socket.connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)

    def erase_index(self):
        with self.archive.connect() as db:db.execute('DELETE FROM workspace_summaries')

    def index(self):
        with self.archive.connect() as db:
            return db.execute('SELECT summary FROM workspace_summaries WHERE task_id=?',(self.task['id'],)).fetchone()

    def test_current_headers_need_no_original_decompression_or_task_decoding(self):
        expected=workspace_task_summary(self.task)
        before=hashlib.sha256(self.archive.path.read_bytes()).hexdigest()
        with patch('researchhelper.archive.decode_body',side_effect=AssertionError('Details only')), \
             patch('researchhelper.archive_codec._get',side_effect=AssertionError('Originals only')):
            self.assertEqual(self.archive.list_workspace(),[expected])
        self.assertEqual(hashlib.sha256(self.archive.path.read_bytes()).hexdigest(),before)
        self.assertEqual(self.archive.get(self.task['id']),self.task)

    def test_restart_reads_the_same_saved_headers(self):
        reopened=ResearchArchive(self.archive.root)
        with patch('researchhelper.archive.decode_body',side_effect=AssertionError('No eager details')):
            self.assertEqual(reopened.list_workspace(),[workspace_task_summary(self.task)])

    def test_save_atomically_updates_revision_owner_title_nodes_and_message_count(self):
        original_creation=self.task['created_at']
        self.task.update(question='新任务名称',owner_account='sales',messages=[{'role':'user','content':'原问题'}],
                         nodes=[{'id':'evidence','title':'取证','status':'运行中','private_source':'not copied'}])
        updated='2028-05-20T04:00:00+00:00'
        with patch('researchhelper.archive.now',return_value=updated):
            self.archive.save(self.task)
        summary=self.archive.list_workspace()[0]
        self.assertEqual(summary,workspace_task_summary(self.task))
        self.assertEqual(summary['revision'],self.task['revision'])
        self.assertEqual(summary['created_at'],original_creation)
        self.assertEqual(summary['updated_at'],updated)
        self.assertEqual(self.archive.get(self.task['id'])['updated_at'],updated)
        self.assertEqual(summary['owner_account'],'sales')
        self.assertNotIn('pending_content',summary)
        self.assertNotIn('private_source',summary['nodes'][0])

    def test_missing_index_fully_verifies_once_without_rewriting_task_bytes(self):
        self.erase_index()
        with self.archive.connect() as db:before=db.execute('SELECT body FROM tasks WHERE id=?',(self.task['id'],)).fetchone()[0]
        from researchhelper.archive import decode_body
        with patch('researchhelper.archive.decode_body',wraps=decode_body) as decode:
            self.assertEqual(self.archive.list_workspace(),[workspace_task_summary(self.task)])
            self.assertEqual(decode.call_count,1)
        self.assertIsNotNone(self.index())
        with self.archive.connect() as db:after=db.execute('SELECT body FROM tasks WHERE id=?',(self.task['id'],)).fetchone()[0]
        self.assertEqual(after,before)
        self.assertEqual(self.archive.get(self.task['id']),self.task)

    def test_pre_index_database_opens_without_rewriting_legacy_research(self):
        with self.archive.connect() as db:
            db.execute('DROP TABLE workspace_summaries')
            before=db.execute('SELECT body FROM tasks WHERE id=?',(self.task['id'],)).fetchone()[0]
        reopened=ResearchArchive(self.archive.root)
        self.assertEqual(reopened.list_workspace(),[workspace_task_summary(self.task)])
        with reopened.connect() as db:after=db.execute('SELECT body FROM tasks WHERE id=?',(self.task['id'],)).fetchone()[0]
        self.assertEqual(before,after)

    def test_corrupt_stale_or_private_field_cache_falls_back_to_full_task(self):
        changes=('json','checksum','revision','version','task_id','private_field')
        from researchhelper.archive import decode_body
        for change in changes:
            with self.subTest(change=change):
                summary=workspace_task_summary(self.task)
                if change=='task_id':summary['id']='another-task'
                if change=='private_field':summary['source_records']=[{'private':'must not leak'}]
                raw='not JSON' if change=='json' else canonical(summary)
                digest=hashlib.sha256(raw.encode()).hexdigest()
                with self.archive.connect() as db:
                    db.execute('UPDATE workspace_summaries SET task_revision=?,view_version=?,summary=?,summary_sha256=? WHERE task_id=?',
                        (self.task['revision']+1 if change=='revision' else self.task['revision'],
                         99 if change=='version' else 1,raw,'bad' if change=='checksum' else digest,self.task['id']))
                with patch('researchhelper.archive.decode_body',wraps=decode_body) as decode:
                    result=self.archive.list_workspace()
                    self.assertEqual(decode.call_count,1)
                self.assertEqual(result,[workspace_task_summary(self.task)])
                self.assertNotIn('source_records',result[0])

    def test_encoded_body_change_invalidates_old_owner_even_at_same_revision(self):
        altered=deepcopy(self.task);altered.update(owner_account='sales',question='外部已保存的新标题')
        with self.archive.connect() as db:
            body=encode_body(db,altered);db.execute('UPDATE tasks SET body=? WHERE id=?',(body,self.task['id']))
        self.assertEqual(self.archive.list_workspace(),[workspace_task_summary(altered)])

    def test_cached_header_is_not_full_source_integrity_approval(self):
        with self.archive.connect() as db:db.execute("UPDATE archive_values SET compressed=?",(b'corrupt original',))
        self.assertEqual(self.archive.list_workspace(),[workspace_task_summary(self.task)])
        self.agent.recover_interrupted()
        for read in (lambda:self.archive.get(self.task['id']),self.archive.list):
            with self.subTest(reader=read):
                with self.assertRaises(ContractError):read()

    def test_startup_skips_stopped_studies_but_still_checks_interrupted_inputs(self):
        with patch.object(self.archive,'list',side_effect=AssertionError('Startup uses headers')), \
             patch('researchhelper.archive.decode_body',side_effect=AssertionError('Stopped sources stay deferred')):
            self.agent.recover_interrupted()
        self.task.update(status='运行中')
        self.task['ledger']['used'].update(model_calls=2,data_requests=7,seconds=13)
        self.archive.save(self.task)
        before=deepcopy(self.task)
        self.agent.recover_interrupted()
        current=self.archive.get(self.task['id'])
        self.assertEqual(current['status'],'失败')
        self.assertEqual(current['ledger'],before['ledger'])
        self.assertEqual(current['version'],before['version'])
        self.assertEqual(current['cutoff'],before['cutoff'])
        self.assertEqual(self.archive.list_workspace()[0],workspace_task_summary(current))

    def test_service_can_start_with_deferred_headers_in_a_populated_store(self):
        from researchhelper.service import make_server
        with patch('researchhelper.archive.decode_body',side_effect=AssertionError('No eager source decode')):
            server=make_server(self.archive.root,ROOT,offline=True,authentication=False)
        server.server_close()

    def test_index_failure_rolls_back_both_task_and_summary(self):
        before=self.archive.get(self.task['id']);cached=self.index()
        with self.archive.connect() as db:
            db.execute("CREATE TRIGGER reject_workspace_index BEFORE INSERT ON workspace_summaries BEGIN SELECT RAISE(ABORT,'fixture index write failure'); END")
        changed=deepcopy(self.task);changed['question']='must roll back'
        with self.assertRaises(sqlite3.IntegrityError):self.archive.save(changed)
        self.assertEqual(changed['revision'],before['revision'])
        self.assertEqual(self.archive.get(self.task['id']),before)
        self.assertEqual(self.index(),cached)

    def test_busy_optional_legacy_backfill_does_not_block_reading(self):
        self.erase_index();first=self.archive.connect()
        try:
            with patch.object(self.archive,'connect',side_effect=[first,sqlite3.OperationalError('database is locked')]):
                self.assertEqual(self.archive.list_workspace(),[workspace_task_summary(self.task)])
        finally:first.close()
        self.assertIsNone(self.index())
        self.assertEqual(self.archive.get(self.task['id']),self.task)

    def test_late_legacy_backfill_cannot_overwrite_a_newer_save(self):
        self.erase_index();other=ResearchArchive(self.archive.root);original=self.archive.connect;calls=[]
        def connect():
            calls.append(True)
            if len(calls)==2:
                current=other.get(self.task['id']);current['question']='并发保存的新标题';other.save(current)
            return original()
        with patch.object(self.archive,'connect',side_effect=connect):
            old=self.archive.list_workspace()
        self.assertEqual(old[0]['question'],self.task['question'])
        self.assertEqual(self.archive.list_workspace()[0]['question'],'并发保存的新标题')

    def test_frozen_delivery_updates_headers_without_changing_report_or_ledger(self):
        task=self.agent.create_case('mechanism');done=self.agent.start(task['id']);frozen=self.archive.report(done['id'],1)
        summaries={row['id']:row for row in self.archive.list_workspace()}
        self.assertEqual(summaries[done['id']],workspace_task_summary(done))
        self.assertEqual(summaries[done['id']]['delivery']['digest'],done['delivery']['digest'])
        self.assertEqual(summaries[done['id']]['updated_at'],done['delivery']['saved_at'])
        self.assertEqual(self.archive.report(done['id'],1),frozen)
        self.assertEqual(self.archive.get(done['id'])['ledger'],done['ledger'])

    def test_related_task_and_its_header_commit_together(self):
        child=dict(self.task,id='related-index-child',revision=0,status='待开始',parent=self.task['id'],materials=[])
        created='2028-05-21T04:00:00+00:00'
        with patch('researchhelper.archive.now',return_value=created):
            done=self.archive.create_related(child,self.task)
        self.assertEqual({row['id']:row for row in self.archive.list_workspace()}[done['id']],workspace_task_summary(done))
        self.assertEqual(done['updated_at'],created)
