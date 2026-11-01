"""Independent UI metadata acceptance. No providers, models or production DB."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.service import make_server


class PresentationStoreTests(unittest.TestCase):
    def setUp(self):
        output = Path(__file__).resolve().parents[2] / 'result/acceptance'
        output.mkdir(parents=True,exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='rh-presentations-',dir=output)
        self.addCleanup(self.temp.cleanup)
        self.store = ResearchArchive(self.temp.name)
        self.task = {'id':'root', 'thread_id':'root', 'version':1, 'question':'原始问题',
                     'status':'已完成', 'owner_account':'admin', 'input_hash':'immutable',
                     'ledger':{'requests':3, 'models':1}}
        self.store.save(self.task)
        with self.store.connect() as db:
            db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?)',
                       ('root',1,'冻结研究正文','<h1>原始报告</h1>','frozen-digest','2026-10-12'))

    def update(self, revision, **changes):
        return self.store.update_presentation('root', {'expected_revision':revision,'changes':changes})

    def snapshot(self):
        with self.store.connect() as db:
            return {name:db.execute('SELECT * FROM '+name).fetchall()
                    for name in ('tasks','versions','deliveries','originals')}

    def test_rename_pin_archive_restore_do_not_mutate_research(self):
        before = self.snapshot()
        for revision, changes in enumerate([{'title':'  展示名称  '},{'pinned':True},{'archived':True},{'archived':False}]):
            self.update(revision, **changes)
        value = self.store.presentations([self.task])['root']
        self.assertEqual(value, {'title':'展示名称','pinned':True,'archived':False,'revision':4,'thread_id':'root'})
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(ResearchArchive(self.temp.name).presentations([self.task]),self.store.presentations([self.task]))

    def test_stale_write_is_not_replayed(self):
        self.update(0,title='首次修改')
        with self.assertRaises(ContractError): self.update(0,pinned=True)
        self.assertEqual(self.store.presentations([self.task])['root']['revision'],1)
        self.assertFalse(self.store.presentations([self.task])['root']['pinned'])

    def test_concurrent_writers_share_one_revision(self):
        barrier = threading.Barrier(2)
        outcomes = []
        def write(title):
            barrier.wait()
            try:
                ResearchArchive(self.temp.name).update_presentation('root',{'expected_revision':0,'changes':{'title':title}})
                outcomes.append('saved')
            except ContractError: outcomes.append('stale')
        threads = [threading.Thread(target=write,args=(title,)) for title in ('甲','乙')]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertCountEqual(outcomes,['saved','stale'])
        self.assertEqual(self.store.presentations([self.task])['root']['revision'],1)

    def test_thread_shared_metadata_and_owner_isolation(self):
        child = dict(self.task,id='child',revision=0)
        self.store.save(child)
        self.store.update_presentation('child',{'expected_revision':0,'changes':{'title':'关联任务'}})
        self.assertEqual(self.store.presentations([self.task])['root']['title'],'关联任务')
        self.assertEqual(self.store.presentations([dict(self.task,owner_account='sales')]),{})
        foreign = dict(child,id='foreign',owner_account='sales',revision=0)
        self.store.save(foreign)
        with self.assertRaises(ContractError):
            self.store.update_presentation('foreign',{'expected_revision':1,'changes':{'pinned':True}})

    def test_running_member_blocks_archive_without_cancelling(self):
        child = dict(self.task,id='child',revision=0,status='运行中')
        self.store.save(child)
        before = self.snapshot()
        with self.assertRaises(ContractError): self.update(0,archived=True)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.store.presentations([self.task]),{})

    def test_invalid_values_never_write(self):
        before = self.snapshot()
        bad = [None,{}, {'expected_revision':False,'changes':{'pinned':True}},
               {'expected_revision':0,'changes':{}}, {'expected_revision':0,'changes':{'pinned':1}},
               {'expected_revision':0,'changes':{'archived':'true'}},
               {'expected_revision':0,'changes':{'question':'改研究'}},
               {'expected_revision':0,'changes':{'title':' ' }},
               {'expected_revision':0,'changes':{'title':'a'*121}},
               {'expected_revision':0,'changes':{'title':'a\nb'}}]
        for payload in bad:
            with self.subTest(payload=payload),self.assertRaises(ContractError):
                self.store.update_presentation('root',payload)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.store.presentations([self.task]),{})

    def test_transaction_failure_does_not_commit_metadata(self):
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER reject_ui BEFORE INSERT ON task_presentations BEGIN SELECT RAISE(ABORT,'fixture save failure'); END")
        before = self.snapshot()
        with self.assertRaises(Exception): self.update(0,pinned=True)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.store.presentations([self.task]),{})


class PresentationPermissionTests(unittest.TestCase):
    def setUp(self):
        output = Path(__file__).resolve().parents[2] / 'result/acceptance'
        self.temp = tempfile.TemporaryDirectory(prefix='rh-presentations-api-',dir=output)
        self.addCleanup(self.temp.cleanup)
        self.server = make_server(self.temp.name,authentication=True,preset_accounts=True)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.url = 'http://127.0.0.1:'+str(self.server.server_port)
        self.admin = self.login('admin','66666666')
        self.sales = self.login('sales','88888888')

    def shutdown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=5)

    def request(self, path, payload=None, cookie=None, token=None):
        headers = {'Content-Type':'application/json'}
        if cookie: headers['Cookie']=cookie
        if token: headers['X-ResearchHelper-Token']=token
        request = Request(self.url+path,data=json.dumps(payload).encode() if payload is not None else None,headers=headers)
        try:
            with urlopen(request,timeout=5) as response:
                return response.status,json.loads(response.read()),response.headers
        except HTTPError as error:
            return error.code,json.loads(error.read()),error.headers

    def login(self,name,password):
        _,boot,_ = self.request('/api/auth/status')
        status,_,headers=self.request('/api/auth/login',{'account':name,'password':password},token=boot['token'])
        self.assertEqual(status,200)
        cookie=headers['Set-Cookie'].split(';')[0]
        _,boot,_=self.request('/api/bootstrap',cookie=cookie)
        return cookie,boot['token']

    def test_ownership_csrf_and_bootstrap_filter(self):
        cookie,token=self.admin
        status,task,_=self.request('/api/tasks',{'question':'离线权限检查'},cookie,token)
        self.assertEqual(status,200)
        route='/api/tasks/'+task['id']+'/presentation'
        change={'expected_revision':0,'changes':{'title':'私有展示名称','pinned':True}}
        denied,reason,_ = self.request(route,change,cookie)
        self.assertEqual(denied,400)
        self.assertEqual(reason['error'],'本地操作令牌无效')
        self.assertEqual(self.request(route,change,*self.sales)[0],403)
        self.assertEqual(self.request(route,cookie=self.sales[0])[0],403)
        self.assertEqual(self.request(route,change,cookie,token)[0],200)
        _,view,_=self.request(route,cookie=cookie)
        self.assertEqual(view['title'],'私有展示名称')
        _,sales_boot,_=self.request('/api/bootstrap',cookie=self.sales[0])
        self.assertEqual(sales_boot['task_presentations'],{})
        self.assertNotIn(task['id'],[t['id'] for t in sales_boot['tasks']])
        _,unchanged,_=self.request('/api/tasks/'+task['id'],cookie=cookie)
        self.assertEqual(unchanged['question'],'离线权限检查')
        self.assertEqual(unchanged['revision'],task['revision'])
