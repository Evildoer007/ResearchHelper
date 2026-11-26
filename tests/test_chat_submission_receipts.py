"""An acknowledged or disconnected first send owns one task and one first batch."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.client import HTTPConnection
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

from researchhelper.contracts import context
from researchhelper.service import make_server

ROOT = Path(__file__).resolve().parents[1]
NONCE = '1'*32


class ChatSubmissionReceiptTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='rh-submission-receipt-')
        self.addCleanup(directory.cleanup)
        self.store = Path(directory.name)/'store'
        self.open_server()
        self.addCleanup(self.close_server)

    def open_server(self, *, configurable=False):
        self.server = make_server(self.store,ROOT,offline=True,authentication=True,preset_accounts=True,
                                  runtime_configurable=configurable)
        self.worker = Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True)
        self.worker.start()
        self.token = self.request('/api/auth/status')[1]['token']
        self.admin = self.login('admin','66666666')
        self.sales = self.login('sales','88888888')

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(5)
        self.assertTrue(self.server.runtime_settled.wait(5))

    def request(self, route, payload=None, cookie=None):
        headers = {'X-ResearchHelper-View':'workspace-v1'}
        if payload is not None:
            headers.update({'Content-Type':'application/json','X-ResearchHelper-Token':self.token})
        if cookie:
            headers['Cookie'] = cookie
        with closing(HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)) as connection:
            connection.request('GET' if payload is None else 'POST',route,
                               body=None if payload is None else json.dumps(payload),headers=headers)
            response = connection.getresponse()
            return response.status,json.loads(response.read()),response.getheader('Set-Cookie')

    def login(self, account, password):
        status, _, cookie = self.request('/api/auth/login',dict(account=account,password=password,remember=False))
        self.assertEqual(status,200)
        return cookie.split(';',1)[0]

    def create(self, nonce=NONCE, **changes):
        return self.request('/api/tasks',dict(question='合成验收：新建研究',submission_id=nonce,**changes),self.admin)

    def lookup(self, nonce=NONCE, cookie=None):
        return self.request('/api/chat-submissions/'+nonce,cookie=cookie or self.admin)[:2]

    def enable_without_model(self):
        self.close_server()
        self.open_server(configurable=True)
        route = '/api/settings/execution'
        change = dict(enabled=True,confirm=True)
        self.assertEqual(self.request(route,change,self.sales)[0],403)
        self.assertEqual(self.request(route,dict(change,confirm=False),self.admin)[0],400)
        status, state, _ = self.request(route,change,self.admin)
        self.assertEqual(status,200,state)
        self.assertEqual(state['mode'],'live')
        self.assertFalse(self.request('/api/bootstrap',cookie=self.sales)[1]['research_readiness']['model_configured'])
        self.assertFalse((self.store/'model-settings.json').exists())
        return {key:state[key] for key in ('mode','policy_version')}

    def test_data_only_questions_reach_their_registered_routes_without_a_model(self):
        mode = self.enable_without_model()
        agent = self.server.agent
        cases = [
            ('仅用上交所申购赎回清单核对510050.SH的基金份额净值和所属日期，生成报告。', 'official', 'etf_pcf'),
            ('仅用美联储官网原文核对最近两次FOMC声明的联邦基金目标利率区间，生成报告。', 'official', 'release_catalog'),
            ('比较贵州茅台600519.SH和比亚迪002594.SZ在2025年和2026年上半年合并报表的营业收入和归母净利润，生成报告。',
             'ifind', 'get_limit_policy'),
            ('核对0700.HK最近收盘价。', 'ifind', 'get_thscode'),
            ('解释ETF的市场价格与单位净值为什么不同。', None, None),
            ('用已取得的材料分析600519.SH的估值风险。请勿联网。', None, None),
        ]
        requests = []

        def observe(_, task, rows):
            requests.extend((item['source'],item['operation']) for item in rows)

        with (patch('researchhelper.draft_workflow._acquire',side_effect=observe),
              patch('researchhelper.draft_workflow._acquire_planned',
                    side_effect=AssertionError('direct readout called the model planner')) as planner,
              patch.object(agent.model,'reason',side_effect=AssertionError('unexpected model call')) as model):
            for index,(question,source,operation) in enumerate(cases):
                with self.subTest(question=question):
                    requests.clear()
                    nonce = f'{index+1:032x}'
                    payload = dict(question=question, research_mode='current',submission_id=nonce,
                                   expected_execution=mode,has_attachments=False)
                    status, task, _ = self.request('/api/tasks',payload,self.admin)
                    self.assertEqual(status,200,task)
                    self.assertEqual(self.request('/api/tasks',dict(payload,has_attachments=True),self.admin)[:2],
                                     (200,task))
                    route = '/api/tasks/'+task['id']+'/start'
                    status, result, _ = self.request(route,dict(submission_id=nonce,expected_execution=mode,
                                                               expected_task=context(task)),self.admin)
                    self.assertEqual(status,200,result)
                    worker = agent.workers.get(task['id'])
                    if worker is not None:
                        worker.join(8)
                    saved = agent.archive.get(task['id'])
                    self.assertTrue(saved.get('delivery'),saved.get('error'))
                    self.assertEqual(saved['ledger']['used']['model_calls'],0)
                    self.assertEqual(requests,[] if source is None else [(source,operation)])
            planner.assert_not_called()
            model.assert_not_called()

    def test_missing_model_rejects_before_create_or_dispatch(self):
        mode = self.enable_without_model()
        archive = self.server.agent.archive
        direct = '核对600519.SH最近收盘价。'
        analysis = '比较贵州茅台与五粮液的渠道、盈利与较长期估值，形成投资判断。'
        error = (400,'research_model_required')

        def assert_requires_model(response):
            status, body, _ = response
            self.assertEqual((status,body.get('failure_code')),error,body.get('error',body.get('status')))

        for role in (self.admin,self.sales):
            assert_requires_model(self.request('/api/tasks',dict(question=analysis,research_mode='current',
                submission_id=NONCE,expected_execution=mode,has_attachments=False),role))
            self.assertEqual(self.lookup(cookie=role),(200,None))
        assert_requires_model(self.request('/api/tasks',dict(question=direct,research_mode='current',
            submission_id=NONCE,expected_execution=mode,has_attachments=True),self.admin))
        self.assertEqual(self.lookup(),(200,None))
        stale = dict(mode='offline',policy_version=1)
        self.assertEqual(self.request('/api/tasks',dict(question=direct,research_mode='current',
            submission_id=NONCE,expected_execution=stale),self.admin)[0],400)
        self.assertFalse(archive.list())

        # A staged, non-Chat task can still be saved explicitly. Starting it
        # without the needed model leaves the archive record and ledger intact.
        status, task, _ = self.request('/api/tasks',dict(question=analysis,research_mode='current'),self.admin)
        self.assertEqual(status,200,task)
        original = archive.get(task['id'])
        route = '/api/tasks/'+task['id']
        assert_requires_model(self.request(route+'/start',dict(expected_execution=mode,expected_task=context(task)),self.admin))
        assert_requires_model(self.request(route+'/messages',dict(message=analysis,start=False,
            expected_revision=task['revision'],submission_id='2'*32,expected_execution=mode),self.admin))
        self.assertEqual(self.lookup('2'*32),(200,None))
        self.assertEqual(archive.get(task['id']),original)
        self.assertEqual(len(archive.list()),1)

        # An explicit host can still compose a report. A later ordinary update
        # must check its new input before replacing the saved version.
        host = dict(data_requests=[],draft_report=dict(title='留存的研究报告',
            conclusion='这里没有足够的原件支持投资判断。',logics=[]))
        status, result, _ = self.request(route+'/start',dict(expected_execution=mode,expected_task=context(task),
                                                             host_result=host),self.admin)
        self.assertEqual(status,200,result)
        worker = self.server.agent.workers.get(task['id'])
        if worker is not None:
            worker.join(8)
        frozen = archive.get(task['id'])
        self.assertEqual(archive.delivery_versions(task['id']),[1])
        assert_requires_model(self.request(route+'/update',dict(expected_execution=mode,
                                                               expected_task=context(frozen)),self.admin))
        self.assertEqual(archive.get(task['id']),frozen)

    def test_concurrent_first_send_commits_one_owned_task_and_reopens_after_restart(self):
        with ThreadPoolExecutor(max_workers=3) as workers:
            responses = list(workers.map(lambda _:self.create(),range(3)))
        self.assertTrue(all(row[0]==200 for row in responses))
        task = responses[0][1]
        self.assertTrue(all(row[1]==task for row in responses))
        self.assertEqual(task['owner_account'],'admin')
        self.assertNotIn('chat_submission_hash',task)
        self.assertEqual(len(self.server.agent.archive.list()),1)
        self.assertEqual(self.lookup(),(200,task))
        self.close_server()
        self.open_server()
        self.assertEqual(self.lookup(),(200,task))
        self.assertEqual(self.create()[1],task)
        self.assertEqual(len(self.server.agent.archive.list()),1)

    def test_same_key_cannot_overwrite_scope_or_cross_account(self):
        _, task, _ = self.create()
        self.assertEqual(self.create(objects=['600519.SH'])[0],400)
        self.assertEqual(self.lookup(cookie=self.sales),(200,None))
        self.assertEqual(self.request('/api/chat-submissions/'+NONCE)[0],401)
        self.assertEqual(self.lookup('x'*32)[0],400)
        self.assertEqual(self.request('/api/tasks/'+task['id'],cookie=self.sales)[0],403)
        status, other, _ = self.request('/api/tasks',dict(question=task['question'],submission_id=NONCE),self.sales)
        self.assertEqual(status,200)
        self.assertNotEqual(other['id'],task['id'])
        self.assertEqual(other['owner_account'],'sales')
        self.assertEqual(self.lookup(),(200,task))
        self.assertEqual(self.lookup(cookie=self.sales),(200,other))

    def test_question_cutoff_is_saved_once_and_controls_the_source_contract(self):
        question = '以北京时间2026年10月16日14:00为研究截止时点，核对600519.SH当时已知的收盘价和财务披露。'
        payload = dict(question=question,research_mode='current',submission_id=NONCE)
        status, task, _ = self.request('/api/tasks',payload,self.admin)
        self.assertEqual(status,200)
        self.assertEqual((task['question'],task['research_mode'],task['cutoff']),
                         (question,'historical','2026-10-16T14:00:00+08:00'))
        from researchhelper.evidence import data_request_contract
        stored = self.server.agent.archive.get(task['id'])
        operations = {tool['operation'] for tool in data_request_contract(stored,first_report=True)['tools']}
        self.assertIn('cmd_history_quotation',operations)
        self.assertNotIn('basic_data_service',operations)
        self.assertNotIn('edb_service',operations)
        self.assertEqual(self.request('/api/tasks',payload,self.admin)[:2],(200,task))
        self.assertEqual(self.lookup(),(200,task))
        self.assertEqual(len(self.server.agent.archive.list()),1)

    def test_observation_dates_keep_current_mode_and_conflicting_cutoffs_create_nothing(self):
        status, task, _ = self.request('/api/tasks',dict(question='比较截至2026年10月16日的收盘价。',
            research_mode='current',submission_id=NONCE),self.admin)
        self.assertEqual(status,200)
        self.assertEqual(task['research_mode'],'current')
        self.assertNotEqual(task['cutoff'][:10],'2026-10-16')

        for question, options in (
            ('研究截止时点：2026-02-30，核对公告。',dict(research_mode='current')),
            ('研究截止时点：2026-10-16，核对公告。',dict(research_mode='historical',cutoff='2026-10-15')),
            ('研究截止日期：2026-10-16。研究截止日期：2026-10-15。',dict(research_mode='current')),
            ('核对公告。',dict(research_mode='historical')),
        ):
            with self.subTest(question=question):
                rejected = dict(question=question,submission_id='3'*32,**options)
                self.assertEqual(self.request('/api/tasks',rejected,self.admin)[0],400)
                self.assertEqual(self.lookup('3'*32),(200,None))
        self.assertEqual(len(self.server.agent.archive.list()),1)

    def test_failed_creation_leaves_no_unowned_task_or_partial_receipt(self):
        with patch.object(self.server.agent.archive,'save',side_effect=OSError('Controlled transaction fault')) as save:
            self.assertEqual(self.create()[0],500)
            self.assertEqual(save.call_args.args[0]['owner_account'],'admin')
        self.assertEqual(self.lookup(),(200,None))
        self.assertFalse(self.server.agent.archive.list())
        self.assertEqual(self.create()[0],200)
        self.assertEqual(len(self.server.agent.archive.list()),1)

    def test_follow_up_reuses_exact_child_and_parent_context(self):
        _, parent, _ = self.create()
        nonce = '2'*32
        route = '/api/tasks/'+parent['id']+'/messages'
        payload = dict(message='合成验收：接着看上述对象',start=False,submission_id=nonce)
        _, child, _ = self.request(route,payload,self.admin)
        self.assertEqual(child['parent'],parent['id'])
        self.assertEqual(child['conversation_parent']['version'],parent['version'])
        self.assertEqual(self.request(route,payload,self.admin)[:2],(200,child))
        self.assertEqual(self.lookup(nonce),(200,child))
        self.assertEqual(self.request(route,dict(payload,message='不同的问题'),self.admin)[0],400)
        self.assertEqual(self.request(route,dict(payload,start=True),self.admin)[0],400)
        self.assertEqual(len(self.server.agent.archive.list()),2)
        self.assertEqual(self.lookup(),(200,parent))

    def test_follow_up_rejects_stale_parent_and_replays_an_existing_child_after_update(self):
        _, parent, _ = self.create(fixture='etf')
        agent = self.server.agent
        first = agent.start(parent['id'])
        self.assertEqual(first['delivery']['version'], 1)
        route = '/api/tasks/'+parent['id']+'/messages'
        payload = dict(message='继续核对ETF原判断',start=False,submission_id='2'*32,
                       expected_revision=first['revision'])
        status, child, _ = self.request(route,payload,self.admin)
        self.assertEqual(status,200)
        self.assertEqual(child['conversation_parent']['revision'],first['revision'])
        self.assertEqual(child['conversation_parent']['delivery']['digest'],first['delivery']['digest'])

        updated = agent.update(parent['id'])
        self.assertEqual(updated['delivery']['version'], 2)
        self.assertEqual(self.request(route,payload,self.admin)[:2],(200,child))
        self.assertEqual(self.lookup('2'*32),(200,child))
        stale = dict(payload,submission_id='3'*32)
        status, error, _ = self.request(route,stale,self.admin)
        self.assertEqual((status,error['failure_code']),(409,'followup_parent_changed'))
        self.assertEqual(self.lookup('3'*32),(200,None))
        self.assertEqual(len(agent.archive.list()),2)
        self.assertEqual(self.request(route,dict(payload,expected_revision=updated['revision']),self.admin)[0],400)

        status, related, _ = self.request(route,dict(stale,expected_revision=updated['revision']),self.admin)
        self.assertEqual(status,200)
        self.assertEqual(related['conversation_parent']['version'],2)
        self.assertEqual(related['conversation_parent']['delivery']['digest'],updated['delivery']['digest'])
        self.assertEqual(related['status'],'待开始')
        self.assertEqual(related['ledger']['used']['model_calls'],0)
        self.assertEqual(agent.archive.report(parent['id'],1)['digest'],first['delivery']['digest'])

    def test_lost_first_start_receipt_cannot_trigger_a_new_batch_after_failure(self):
        _, task, _ = self.create(fixture='etf')
        ident = task['id']
        route = '/api/tasks/'+ident
        submission = dict(submission_id=NONCE,expected_task=context(task))
        agent = self.server.agent
        self.assertEqual(self.request(route+'/start',dict(submission,submission_id='3'*32),self.admin)[0],400)
        with patch.object(agent.archive,'deliver',side_effect=OSError('Controlled delivery failure')):
            self.assertEqual(self.request(route+'/start',submission,self.admin)[0],200)
            agent.workers[ident].join(8)
        failed = agent.archive.get(ident)
        self.assertEqual((failed['status'],failed['batch']),('失败',1))
        for expectation in (submission,dict(submission,expected_task=context(failed))):
            self.assertEqual(self.request(route+'/start',expectation,self.admin)[1]['batch'],1)
        self.assertEqual(agent.archive.get(ident),failed)
        self.assertEqual(self.request(route+'/resume',submission,self.admin)[0],400)
        self.assertEqual(self.request(route+'/retry-save',{},self.admin)[0],200)
        self.assertEqual(agent.archive.delivery_versions(ident),[1])
