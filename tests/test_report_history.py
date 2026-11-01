"""Historical corrections remain visible without changing any saved report."""
from copy import deepcopy
from unittest.mock import patch
import tempfile,unittest
from core.session import Session
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import fingerprint
from tests import test_report_corrections as correction_tests
REPORT=correction_tests.REPORT


class SavedHistoryTests(unittest.TestCase):
    setUp=correction_tests.ReportCorrectionTests.setUp

    def test_update_retains_the_original_note_beside_only_its_report(self):
        before=deepcopy(self.report);ledger=deepcopy(self.task['ledger'])
        self.archive.correction(self.task['id'],1,'原稿不能据此确认因果。')
        current=self.agent.update(self.task['id'],host_result=dict(data_requests=[],draft_report=REPORT))
        history=self.archive.delivery_history(current['id'])
        self.assertEqual([row['version'] for row in history],[2,1])
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
        self.assertEqual(set(history[0]),{'task_id','version','digest','created_at','corrections'})
        with self.archive.connect() as db:
            recorded=db.execute('SELECT created_at FROM deliveries WHERE task_id=? AND version=1',(self.task['id'],)).fetchone()[0]
        self.assertEqual(history[0]['created_at'],recorded)
        self.assertEqual(fingerprint(self.archive.get(self.task['id'])),before)

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


if __name__=='__main__':unittest.main()
