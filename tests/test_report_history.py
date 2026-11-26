"""Historical corrections remain visible without changing any saved report."""
from copy import deepcopy
from unittest.mock import patch
import json
import tempfile,unittest
from core.session import Session
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import fingerprint
from tests import test_report_corrections as correction_tests
REPORT=correction_tests.REPORT
REVISED_REPORT=dict(REPORT,title='口径复核')


class SavedHistoryTests(unittest.TestCase):
    setUp=correction_tests.ReportCorrectionTests.setUp

    def test_update_retains_the_original_note_beside_only_its_report(self):
        before=deepcopy(self.report);ledger=deepcopy(self.task['ledger'])
        self.archive.correction(self.task['id'],1,'原稿不能据此确认因果。')
        current=self.agent.update(self.task['id'],host_result=dict(data_requests=[],draft_report=REVISED_REPORT))
        history=self.archive.delivery_history(current['id'])
        self.assertEqual([row['version'] for row in history],[2,1])
        self.assertEqual([row['title'] for row in history],[REVISED_REPORT['title'],REPORT['title']])
        self.assertEqual(history[0]['corrections'],[])
        self.assertEqual(history[1]['corrections'],self.archive.correction_records(current['id'],1))
        self.assertEqual(history[1]['digest'],before['digest'])
        self.assertEqual(self.archive.report(current['id'],1)['html'],before['html'])
        self.assertEqual(self.archive.get(current['id'],1)['ledger'],ledger)
        self.assertEqual(current['ledger']['used']['model_calls'],0)
        self.assertEqual(current['ledger']['used']['data_requests'],0)
        self.assertEqual(ResearchArchive(self.temp.name).delivery_history(current['id']),history)

    def test_unpublished_checkpoints_and_another_tasks_notes_do_not_enter_history(self):
        self.archive.correction(self.task['id'],1,'仅属于原问题。')
        other=self.agent.create('独立问题')
        self.assertEqual(self.archive.delivery_history(other['id']),[])
        self.agent.prepare_update(self.task['id'])
        history=self.archive.delivery_history(self.task['id'])
        self.assertEqual([row['version'] for row in history],[1])
        self.assertEqual(self.archive.delivery_versions(self.task['id']),[1])
        self.assertEqual(history[0]['corrections'][0]['task_id'],self.task['id'])

    def test_listing_is_read_only_and_does_not_decode_large_report_bodies(self):
        self.archive.correction(self.task['id'],1,'核对归属范围。')
        before=fingerprint(self.archive.get(self.task['id']))
        connect=self.archive.connect
        def read_only():
            db=connect();db.execute('PRAGMA query_only=ON');return db
        with patch.object(self.archive,'connect',read_only),patch('researchhelper.archive.decode_body',side_effect=AssertionError('Full body decoding is unnecessary')):
            history=self.archive.delivery_history(self.task['id'])
        self.assertEqual(set(history[0]),{'task_id','version','digest','created_at','title','corrections'})
        with self.archive.connect() as db:
            recorded=db.execute('SELECT created_at FROM deliveries WHERE task_id=? AND version=1',(self.task['id'],)).fetchone()[0]
        self.assertEqual(history[0]['created_at'],recorded)
        self.assertEqual(fingerprint(self.archive.get(self.task['id'])),before)

    def test_legacy_report_without_title_metadata_uses_its_saved_page(self):
        ident=self.task['id']
        with self.archive.connect() as db:
            db.execute('DELETE FROM delivery_titles WHERE task_id=?',(ident,))
            body=json.loads(db.execute('SELECT body FROM versions WHERE task_id=? AND version=1',(ident,)).fetchone()[0])
            self.assertEqual(body['delivery'].pop('title'),REPORT['title'])
            db.execute('UPDATE versions SET body=? WHERE task_id=? AND version=1',(json.dumps(body),ident))
        with patch('researchhelper.archive.decode_body',side_effect=AssertionError('Do not inflate research to read its name')):
            history=self.archive.delivery_history(ident,version=1)
        self.assertEqual(history[0]['title'],REPORT['title'])
        self.assertEqual(self.archive.report(ident,1)['html'],self.report['html'])
        with self.archive.connect() as db:
            db.execute('UPDATE deliveries SET html=? WHERE task_id=? AND version=1',
                (self.report['html'].replace('<title>'+REPORT['title']+'</title>','<title>其他标题</title>'),ident))
        self.assertNotIn('title',self.archive.delivery_history(ident,version=1)[0])

    def test_unreadable_or_unbound_legacy_snapshots_cannot_supply_a_title(self):
        ident=self.task['id']
        with self.archive.connect() as db:
            db.execute('DELETE FROM delivery_titles WHERE task_id=?',(ident,))
        foreign=dict(self.task,id='another-task')
        wrong_digest=dict(self.task,delivery=dict(self.task['delivery'],digest='0'*64))
        for kind,invalid in (('malformed','{invalid'),('foreign task',json.dumps(foreign)),('wrong digest',json.dumps(wrong_digest))):
            with self.subTest(kind=kind):
                with self.archive.connect() as db:
                    db.execute('UPDATE versions SET body=? WHERE task_id=? AND version=1',(invalid,ident))
                history=self.archive.delivery_history(ident,version=1)
                self.assertEqual(history[0]['digest'],self.report['digest'])
                self.assertNotIn('title',history[0])

    def test_indexed_title_reads_without_loading_the_frozen_snapshot(self):
        ident=self.task['id']
        with patch.object(self.archive,'_legacy_delivery_title',side_effect=AssertionError('Title already indexed')):
            self.assertEqual(self.archive.delivery_history(ident)[0]['title'],REPORT['title'])

    def test_host_reads_the_same_history_with_zero_local_calls(self):
        with tempfile.TemporaryDirectory() as project:
            session=Session(project)
            try:
                task=session.create('保留原稿更正');task=session.draft(task['id'],report=REPORT)
                session.correct_report(task['id'],'原稿的判断依据不足。')
                session.update(task['id']);current=session.draft(task['id'],report=REPORT)
                self.assertEqual(session.report_history(task['id']),session.archive.delivery_history(task['id']))
                self.assertEqual(session.tools(task['id'])['report_corrections']['history'],'report_history(task_id)')
                self.assertEqual(current['ledger']['used']['model_calls'],0)
                self.assertEqual(current['ledger']['used']['data_requests'],0)
            finally:session.agent.close_runtime()


class SavedHistoryHTTPTests(unittest.TestCase):
    setUp=correction_tests.ReportCorrectionHTTPTests.setUp
    close=correction_tests.ReportCorrectionHTTPTests.close
    call=correction_tests.ReportCorrectionHTTPTests.call

    def test_history_exposes_bound_notes_but_the_legacy_version_list_stays_compatible(self):
        self.server.agent.archive.correction(self.task['id'],1,'<svg onload="x()">保留为文字。')
        updated=self.server.agent.update(self.task['id'],host_result=dict(data_requests=[],draft_report=REPORT))
        before=fingerprint(updated)
        route='/api/tasks/'+updated['id']
        status,history,_=self.call(route+'/report-history',cookie=self.cookies['admin'])
        self.assertEqual(status,200);self.assertEqual(history['task_id'],updated['id'])
        self.assertEqual(history['reports'],self.server.agent.archive.delivery_history(updated['id']))
        self.assertEqual([row['version'] for row in history['reports']],[2,1])
        self.assertEqual(len(history['reports'][1]['corrections']),1)
        self.assertEqual(history['reports'][0]['corrections'],[])
        self.assertEqual(self.call(route+'/versions',cookie=self.cookies['admin'])[:2],(200,[1,2]))
        self.assertEqual(fingerprint(self.server.agent.archive.get(updated['id'])),before)

    def test_history_respects_account_and_session_ownership(self):
        self.server.agent.archive.correction(self.task['id'],1,'原稿更正。')
        route='/api/tasks/'+self.task['id']+'/report-history'
        self.assertEqual(self.call(route,cookie=self.cookies['sales'])[0],403)
        self.assertEqual(self.call(route)[0],401)
        self.assertEqual(self.call(route,cookie=self.cookies['admin'])[0],200)

    def test_pending_revision_keeps_the_previous_frozen_title(self):
        self.server.agent.prepare_update(self.task['id'])
        status,task,_=self.call('/api/tasks/'+self.task['id'],cookie=self.cookies['admin'])
        self.assertEqual(status,200)
        self.assertEqual(task['version'],2)
        self.assertIsNone(task['delivery'])
        self.assertEqual(task['previous_delivery']['title'],REPORT['title'])
        self.assertEqual(task['previous_delivery']['digest'],self.task['delivery']['digest'])

    def test_legacy_current_report_projects_its_title_without_rewriting_the_task(self):
        archive=self.server.agent.archive
        ident=self.task['id']
        canonical_task=archive.get(ident)
        canonical_task['delivery'].pop('title')
        archive.save(canonical_task)
        with archive.connect() as db:
            db.execute('DELETE FROM delivery_titles WHERE task_id=?',(ident,))
            body=json.loads(db.execute('SELECT body FROM versions WHERE task_id=? AND version=1',(ident,)).fetchone()[0])
            body['delivery'].pop('title')
            db.execute('UPDATE versions SET body=? WHERE task_id=? AND version=1',(json.dumps(body),ident))
        before=fingerprint(archive.get(ident))
        status,task,_=self.call('/api/tasks/'+ident,cookie=self.cookies['admin'])
        self.assertEqual(status,200)
        self.assertEqual(task['delivery']['title'],REPORT['title'])
        self.assertEqual(fingerprint(archive.get(ident)),before)
        self.assertNotIn('title',archive.get(ident)['delivery'])


if __name__=='__main__':unittest.main()
