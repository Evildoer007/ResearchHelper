"""Version-bound corrections preserve frozen reports, task state and access rules."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from tests import ROOT
from core.session import Session
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError,fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.service import make_server

REPORT=dict(title='口径观察',conclusion='仅使用已有资料，判断仍需对应依据。',
            logics=[dict(title='区分事实与推断',analysis='原值变化不直接证明原因。')])


class ReportCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance');self.addCleanup(self.temp.cleanup)
        self.archive=ResearchArchive(self.temp.name)
        self.agent=ResearchAgent(self.archive,ROOT,offline=True,draft_first=True)
        self.addCleanup(self.agent.close_runtime)
        task=self.agent.create('核对已有数据的解释范围')
        self.task=self.agent.start(task['id'],dict(data_requests=[],draft_report=REPORT))
        self.report=self.archive.report(self.task['id'],1)

    def test_append_is_visible_after_restart_without_changing_the_report_or_task(self):
        before=fingerprint(self.task);frozen=deepcopy(self.report)
        notes=self.archive.correction(self.task['id'],1,'现金流与归母净利润的范围不同。',expected_digest=self.report['digest'])
        self.assertEqual(notes[0]['task_id'],self.task['id']);self.assertEqual(notes[0]['version'],1)
        restarted=ResearchArchive(self.temp.name)
        self.assertEqual(restarted.correction_records(self.task['id'],1),notes)
        after=restarted.report(self.task['id'],1)
        for key in ('content','html','digest'):self.assertEqual(after[key],frozen[key])
        self.assertEqual(fingerprint(restarted.get(self.task['id'])),before)

    def test_retries_record_the_same_reason_once(self):
        first=self.archive.correction(self.task['id'],1,'需要区分口径。')
        second=self.archive.correction(self.task['id'],1,' 需要区分口径。 ')
        self.assertEqual(first,second);self.assertEqual(len(second),1)
        self.assertEqual(self.archive.get(self.task['id'])['ledger'],self.task['ledger'])

    def test_invalid_version_reason_and_digest_do_not_leave_partial_notes(self):
        cases=[(True,'说明',None),(0,'说明',None),(2,'说明',None),('1','说明',None),
               (1,None,None),(1,{},None),(1,'  ',None),(1,'字'*4001,None),(1,'说明','wrong-digest')]
        for version,reason,digest in cases:
            with self.subTest(version=version,reason_type=type(reason).__name__),self.assertRaises(ContractError):
                self.archive.correction(self.task['id'],version,reason,expected_digest=digest)
        self.assertEqual(self.archive.correction_records(self.task['id'],1),[])

    def test_another_task_cannot_receive_the_first_reports_note_or_digest(self):
        task=self.agent.create('另一个独立问题');other=self.agent.start(task['id'],dict(data_requests=[],draft_report=REPORT))
        with self.assertRaises(ContractError):
            self.archive.correction(other['id'],1,'不应绑定其他任务。',expected_digest=self.report['digest'])
        self.assertEqual(self.archive.correction_records(other['id'],1),[])
        self.archive.correction(self.task['id'],1,'仅属于原报告。')
        self.assertEqual(self.archive.correction_records(other['id'],1),[])

    def test_host_has_a_shared_zero_call_entry(self):
        with tempfile.TemporaryDirectory() as project:
            session=Session(project)
            try:
                task=session.create('按已取得资料出稿')
                task=session.draft(task['id'],report=REPORT,data_requests=[])
                before=fingerprint(task)
                notes=session.correct_report(task['id'],'预期变化尚无对应依据。',expected_digest=task['delivery']['digest'])
                self.assertEqual(session.report_corrections(task['id']),notes)
                self.assertFalse(session.tools(task['id'])['report_corrections']['local_model_calls'])
                self.assertEqual(fingerprint(session.status(task['id'])),before)
            finally:session.agent.close_runtime()


class ReportCorrectionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance');self.addCleanup(self.temp.cleanup)
        self.server=make_server(self.temp.name,ROOT,authentication=True)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.token=self.call('/api/auth/status')[1]['token']
        status,_,_=self.call('/api/auth/setup',dict(admin_password='test-admin-only',sales_password='test-sales-only'))
        self.assertEqual(status,200)
        self.cookies={}
        for account in ('admin','sales'):
            status,_,cookie=self.call('/api/auth/login',dict(account=account,password='test-'+account+'-only'))
            self.assertEqual(status,200);self.cookies[account]=cookie.split(';')[0]
        status,created,_=self.call('/api/tasks',dict(question='更正入口验收'),self.cookies['admin'])
        self.assertEqual(status,200)
        self.task=self.server.agent.start(created['id'],dict(data_requests=[],draft_report=REPORT))
        self.payload=dict(version=1,reason='此处只能描述观察，不能确认原因。',expected_digest=self.task['delivery']['digest'])

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=10)

    def call(self,path,payload=None,cookie=None,*,token=True):
        headers={'Content-Type':'application/json','X-ResearchHelper-View':'workspace-v1'}
        if token and hasattr(self,'token'):headers['X-ResearchHelper-Token']=self.token
        if cookie:headers['Cookie']=cookie
        request=Request(self.base+path,data=json.dumps(payload).encode() if payload is not None else None,headers=headers)
        try:response=urlopen(request,timeout=10)
        except HTTPError as error:response=error
        with response:return response.status,json.loads(response.read()),response.headers.get('Set-Cookie')

    def test_record_readback_and_bootstrap_preserve_the_frozen_report(self):
        route='/api/tasks/'+self.task['id'];before=self.server.agent.archive.report(self.task['id'],1)
        for _ in range(2):
            status,result,_=self.call(route+'/report-correction',self.payload,self.cookies['admin'])
            self.assertEqual(status,200);self.assertEqual(len(result['corrections']),1)
        status,task,_=self.call(route,cookie=self.cookies['admin']);self.assertEqual(status,200)
        self.assertEqual(task['report_corrections'],result['corrections'])
        _,boot,_=self.call('/api/bootstrap',cookie=self.cookies['admin'])
        self.assertEqual(boot['tasks'][0]['report_corrections'],result['corrections'])
        after=self.server.agent.archive.report(self.task['id'],1)
        self.assertEqual(after['html'],before['html']);self.assertEqual(after['digest'],before['digest'])
        self.assertEqual(self.server.agent.archive.get(self.task['id']),self.task)

    def test_other_account_and_missing_session_cannot_read_or_write_notes(self):
        route='/api/tasks/'+self.task['id']
        self.assertEqual(self.call(route+'/report-correction',self.payload,self.cookies['sales'])[0],403)
        self.assertEqual(self.call(route,cookie=self.cookies['sales'])[0],403)
        self.assertEqual(self.call(route+'/report-correction',self.payload)[0],401)
        self.assertEqual(self.server.agent.archive.correction_records(self.task['id'],1),[])

    def test_stale_digest_extra_fields_and_missing_csrf_are_rejected(self):
        route='/api/tasks/'+self.task['id']+'/report-correction';cookie=self.cookies['admin']
        for change in (dict(expected_digest='stale'),dict(version=True),dict(extra='unsupported')):
            with self.subTest(change=change):self.assertEqual(self.call(route,{**self.payload,**change},cookie)[0],400)
        self.assertEqual(self.call(route,self.payload,cookie,token=False)[0],400)
        self.assertEqual(self.server.agent.archive.correction_records(self.task['id'],1),[])


if __name__=='__main__':unittest.main()
