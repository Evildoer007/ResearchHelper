"""Exercise the actual model adapter and SQLite around an interrupted result."""
import copy
import json
import socket
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, ExecutionTimeExhausted, charge, now
from researchhelper.draft_workflow import _retained_plan, retained_submission
from researchhelper.drafts import report_proposal
from researchhelper.evidence import _record_current_acquisition, snapshot_record
from researchhelper.engine import ResearchAgent
from researchhelper.market_calendar import FUNCTION, QUOTE_ENDPOINT_POLICY
from researchhelper.model import ModelService
from researchhelper.planning import draft_research_scope
from researchhelper.service import workspace_task_view


PAPER = dict(title='合成研究回复', conclusion='机制判断取决于价格变化及其传导条件。', logics=[])
QUOTE_REQUEST = dict(id='close', source='ifind', operation='cmd_history_quotation', question_ids=['q1'],
    selection_reason='核对指定交易日收盘价。',
    parameters=dict(codes='600519.SH', indicators='close', startdate='2026-10-15', enddate='2026-10-15',
                    functionpara=dict(Currency='RMB', CPS='1', Fill='Omit')))
INTERVAL_DAYS = ['2026-10-15', '2026-10-16']
INTERVAL_REQUEST = dict(QUOTE_REQUEST, parameters=dict(QUOTE_REQUEST['parameters'], enddate=INTERVAL_DAYS[-1]))
LEGACY_INTERVAL_RULE = ('收盘变化按各自查询区间解读。window_start与window_end是查询边界，period是实际读数的日期；'
                        '读数变化只比较所列日期。quote_interval_gaps中的区间不写完整涨跌幅，也不拼接分开的区间。')
CURRENT_INTERVAL_RULE = ('收盘变化按各自区间解读。window_start与window_end是区间边界，period是实际读数日期。'
                         '首末日有读数时可比较端点。中间有缺项时照实说明，不能据端点描述连续走势。'
                         'quote_interval_gaps中的区间不写完整涨跌幅，也不拼接分开的区间。')


class InterruptedProcess(BaseException):
    """Exit the worker before its caller can create a checkpoint."""


class ReplayModel(ModelService):
    def __init__(self, agent, calls, *, interruption=None, cancel=False, loss_stage=None, requests=(), structured=False):
        self.structured = structured
        settings = SimpleNamespace(read=lambda private=False: {
            'endpoint':'https://api.deepseek.com/responses' if structured else 'https://fixture.invalid',
            'model':'deepseek-flash' if structured else 'offline-fixture',
            **({'api_key':'RH_FIXTURE_NOT_A_KEY'} if private else {'has_key':True})})
        self.agent, self.calls, self.requests = agent, calls, requests
        self.interruption, self.loss_stage, self.cancel = interruption, loss_stage, cancel
        super().__init__(settings, offline=False, journal=self.persist)

    def persist(self, task):
        self.agent._journal(task)
        record = task['ledger']['dispatches'][-1]
        if record['research_phase']==self.loss_stage and record['status']=='成功':
            self.loss_stage = None
            raise InterruptedProcess

    def _request_bytes(self, task, request, started, timeout, record):
        phase = record['research_phase']
        self.calls.append(phase)
        record['network_request_started'] = True
        if phase==self.interruption:
            self.interruption = None
            raise InterruptedProcess
        if phase=='draft_report' and self.cancel:
            self.cancel = False
            self.agent.cancel(task['id'])
        if phase=='acquire':
            schema = json.loads(request.data)['text']['format']['schema']
            reference = schema['properties']['binding_ref']['enum'][0]
            answer = dict(version=1,binding_ref=reference,data_requests=list(self.requests),
                          stop_reason='' if self.requests else '无需外部数据。')
        else:
            answer = {'data_requests':list(self.requests)} if phase=='draft_plan' else PAPER
        content = json.dumps(answer,ensure_ascii=False)
        if self.structured:
            payload = dict(object='response',status='completed',error=None,incomplete_details=None,
                output=[dict(type='message',role='assistant',status='completed',
                             content=[dict(type='output_text',text=content)])])
        else:
            payload = {'choices':[{'message':{'role':'assistant','content':content},'finish_reason':'stop'}]}
        return json.dumps(payload,ensure_ascii=False).encode()


def seed_interrupted_quote(store, *, policy=3, reply_saved=True):
    """Persist a genuine dispatch using the report prompt shipped for this policy."""
    from researchhelper import draft_workflow

    agent = ResearchAgent(ResearchArchive(store),ROOT,offline=False,require_argument_support=True,draft_first=True)
    calls, acquired = [], []
    interruption = {'loss_stage' if reply_saved else 'interruption': 'draft_report'}
    agent.model = ReplayModel(agent,calls,requests=[INTERVAL_REQUEST],**interruption)
    writing_messages = draft_workflow._writing_messages

    def shipped_messages(task, facts, **options):
        messages = writing_messages(task,facts,**options)
        system = messages[0]['content']
        expected = LEGACY_INTERVAL_RULE if policy == 3 else CURRENT_INTERVAL_RULE
        other = CURRENT_INTERVAL_RULE if policy == 3 else LEGACY_INTERVAL_RULE
        assert system.count(expected) + system.count(other) == 1
        messages[0]['content'] = system.replace(other,expected)
        return messages

    def transport(task, source, operation, parameters, credentials):
        assert source == 'ifind'
        if operation == 'cmd_history_quotation':
            assert parameters == INTERVAL_REQUEST['parameters']
            payload = dict(errorcode=0,tables=[dict(thscode='600519.SH',time=INTERVAL_DAYS,table=dict(close=[112,116]))])
        else:
            assert operation == 'get_trade_dates'
            assert parameters == dict(marketcode='212001',functionpara=FUNCTION,
                                      startdate=INTERVAL_DAYS[0],enddate=INTERVAL_DAYS[-1])
            payload = dict(errorcode=0,tables=dict(time=INTERVAL_DAYS))
        charge(task['ledger'],'data_requests')
        record = snapshot_record(task,source,operation,parameters,payload)
        receipt = dict(kind='data_requests',source=source,operation=operation,status='成功',time=now(),
            batch=task['batch'],fingerprint=record['request_hash'],transport_isolated=True,
            network_request_started=True,network_worker_reaped=True)
        task['ledger']['dispatches'].append(receipt)
        acquired.append(operation)
        return _record_current_acquisition(task,record,receipt)

    agent.collector.external = transport
    try:
        with patch('researchhelper.market_calendar.QUOTE_ENDPOINT_VERSION',policy):
            task = agent.create('核对600519.SH在2026-10-15至2026-10-16的收盘涨跌幅。',objects=['600519.SH'],
                                research_mode='current',owner_account='admin')
        try:
            with patch.object(draft_workflow,'_writing_messages',shipped_messages):
                agent.start(task['id'])
        except InterruptedProcess:
            pass
        else:
            raise AssertionError('The fixture did not interrupt the worker')
        checkpoint = agent.archive.get(task['id'])
        assert checkpoint[QUOTE_ENDPOINT_POLICY] == policy and checkpoint['status'] == '运行中'
        assert calls == ['draft_plan','draft_report'] and set(acquired) == {'cmd_history_quotation','get_trade_dates'}
        return dict(task_id=task['id'],cutoff=task['cutoff'],title=PAPER['title'],conclusion=PAPER['conclusion'],
                    model_calls=len(calls),data_requests=len(acquired))
    finally:
        agent.close_runtime()


class PaidDraftCheckpointTests(unittest.TestCase):
    def setUp(self):
        store = tempfile.TemporaryDirectory(prefix='rh-paid-checkpoint-')
        self.addCleanup(store.cleanup)
        self.store = store.name
        guard = patch.object(socket.socket,'connect',side_effect=AssertionError('Real connection forbidden'))
        guard.start()
        self.addCleanup(guard.stop)
        self.calls = []

    def open(self, *, offline=False, **fixture):
        archive = ResearchArchive(self.store)
        agent = ResearchAgent(archive,ROOT,offline=offline,require_argument_support=True,draft_first=True)
        self.addCleanup(agent.close_runtime)
        if not offline:
            agent.model = ReplayModel(agent,self.calls,**fixture)
        return agent

    def test_report_argument_boundary_recovery_uses_the_same_paid_response(self):
        from researchhelper.output_schema import DRAFT_REPORT_SCHEMA_NAME

        raw = ('{"title":"价格传导","conclusion":"样本内价格波动。","logics":['
               '{"argument":{"claim":"需核对传导条件。","reasoning":"判断依赖预期及实际利率。",'
               '"title":"利率渠道","tracking":{"indicator":"实际利率"}},'
               '{"argument":{"claim":"竞争解释仍成立。","evidence":"融资需求需要额外对照。",'
               '"title":"信用渠道"}],"risks":[]}')
        formats = []

        class DraftReply(ReplayModel):
            def _request_bytes(self, task, request, started, timeout, record):
                self.calls.append(record['research_phase'])
                formats.append(json.loads(request.data)['text']['format'])
                record['network_request_started'] = True
                return json.dumps(dict(object='response', status='completed', error=None,
                    incomplete_details=None, output=[dict(type='message', role='assistant', status='completed',
                        content=[dict(type='output_text', text=raw)])]), ensure_ascii=False).encode()

        agent = self.open()
        agent.model = DraftReply(agent, self.calls, structured=True)
        task = agent.create('研究价格波动对利率和信用需求的可能影响。', research_mode='current',
                            limits={'model_calls': 1})
        finished = agent.start(task['id'])
        self.assertEqual(self.calls, ['draft_report'])
        self.assertEqual(finished['ledger']['used']['model_calls'], 1)
        self.assertEqual(finished['draft_submissions'][0]['payload'], raw)
        self.assertEqual(finished['draft_report']['origin'], 'model')
        self.assertEqual([row['title'] for row in finished['draft_report']['logics']], ['利率渠道', '信用渠道'])
        self.assertEqual(formats[0]['type'], 'json_schema')
        self.assertEqual(formats[0]['name'], DRAFT_REPORT_SCHEMA_NAME)
        self.assertTrue(formats[0]['strict'])
        model_events = [event for event in finished['ledger']['dispatches'] if event.get('kind') == 'model_calls']
        self.assertEqual([event['submission_transport'] for event in model_events], ['responses_json_schema_v1'])
        self.assertTrue(agent.archive.report(task['id'], 1)['html_integrity_verified'])
        self.assertEqual(report_proposal(raw[:-8]), {})
        self.assertEqual(report_proposal(raw.replace(
            '"title":"信用渠道"', '"title":"信用渠道","title":"重复标题"')), {})

    def interrupt(self, *, question='解释本次供需变化的传导路径。', model_limit=4, **fixture):
        first = self.open(**fixture)
        task = first.create(question,research_mode='current',
                            limits=dict(model_calls=model_limit,data_requests=12,seconds=120))
        with self.assertRaises(InterruptedProcess):
            first.start(task['id'])
        self.assertEqual(first.archive.get(task['id'])['status'],'运行中')
        first.close_runtime()
        return task

    def restart(self, original, **options):
        agent = self.open(**options)
        agent.recover_interrupted()
        failed = agent.archive.get(original['id'])
        self.assertEqual(failed['status'],'失败')
        self.assertEqual((failed['version'],failed['batch'],failed['cutoff']),(1,1,original['cutoff']))
        return agent,failed

    def assert_draft(self, agent, task, original, *, batch, model_calls, origin, data_requests=0):
        self.assertEqual((task['id'],task['version'],task['batch'],task['cutoff']),
                         (original['id'],1,batch,original['cutoff']))
        self.assertEqual(task['draft_report']['origin'],origin)
        self.assertEqual(task['delivery']['kind'],'research_draft')
        self.assertEqual(task['status'],'部分完成')
        self.assertEqual(task['ledger']['used']['model_calls'],model_calls)
        self.assertEqual(task['ledger']['used']['data_requests'],data_requests)
        self.assertEqual(agent.archive.delivery_versions(original['id']),[1])

    def quote_transport(self, task, source, operation, parameters, credentials):
        self.assertEqual((source, operation), ('ifind', 'cmd_history_quotation'))
        charge(task['ledger'], 'data_requests')
        value = dict(errorcode=0,tables=[dict(thscode='600519.SH',time=['2026-10-15'],table=dict(close=[112]))])
        record = snapshot_record(task,source,operation,parameters,value)
        receipt = dict(kind='data_requests',source=source,operation=operation,status='成功',time=now(),
            batch=task['batch'],fingerprint=record['request_hash'],transport_isolated=True,
            network_request_started=True,network_worker_reaped=True)
        task['ledger']['dispatches'].append(receipt)
        return _record_current_acquisition(task,record,receipt)

    def resume_completed_quote_plan(self, *, phase, structured=False, legacy=False):
        # Older releases used a paid plan for this literal date. Resume that
        # stored dispatch even though new tasks can use the fixed query.
        with patch('researchhelper.market_calendar.QUOTE_ENDPOINT_VERSION', 7):
            original = self.interrupt(loss_stage=phase, structured=structured, requests=[QUOTE_REQUEST], model_limit=2,
                                      question='核对600519.SH在2026年10月15日的收盘价。')
        restarted,failed = self.restart(original, structured=structured)
        self.assertEqual(len(failed['draft_acquisition_checkpoint']['requests']),1)
        self.assertEqual(failed['source_records'],[])
        self.assertEqual(failed['ledger']['dispatches'][-1]['status'],'成功')
        if legacy:
            failed['draft_acquisition_checkpoint']['scope'] = draft_research_scope(failed)
            missing_receipt = copy.deepcopy(failed)
            missing_receipt['ledger']['dispatches'][-1].pop('draft_call_binding')
            self.assertIsNone(_retained_plan(missing_receipt))
            changed_scope = copy.deepcopy(failed)
            changed_scope['plan_version'] += 1
            self.assertIsNone(_retained_plan(changed_scope))
            restarted.archive.save(failed)
        restarted.collector.external = self.quote_transport
        done = restarted.start(original['id'])
        self.assertEqual(self.calls,[phase,'draft_report'])
        self.assertEqual([fact['value'] for fact in done['draft_report']['facts']],[112])
        self.assert_draft(restarted,done,original,batch=2,model_calls=2,origin='model',data_requests=1)

    def test_completed_plan_is_durable_when_adapter_never_returns(self):
        self.resume_completed_quote_plan(phase='draft_plan')

    def test_completed_strict_plan_uses_the_same_durable_checkpoint(self):
        self.resume_completed_quote_plan(phase='acquire',structured=True)

    def test_legacy_plan_with_wrong_scope_recovers_with_the_original_dispatch(self):
        self.resume_completed_quote_plan(phase='draft_plan',legacy=True)

    def test_completed_report_survives_adapter_exit_and_saves_offline(self):
        original = self.interrupt(loss_stage='draft_report')
        restarted,failed = self.restart(original,offline=True)
        self.assertTrue(workspace_task_view(failed)['has_retained_submission'])
        self.assertEqual(retained_submission(failed)['payload'],json.dumps(PAPER,ensure_ascii=False))
        before = copy.deepcopy(failed['ledger']['dispatches'])
        done = restarted.retry_save(original['id'])
        self.assertEqual(done['ledger']['dispatches'],before)
        self.assertEqual(done['draft_report']['conclusion'],PAPER['conclusion'])
        self.assertEqual(self.calls,['draft_plan','draft_report'])
        self.assert_draft(restarted,done,original,batch=1,model_calls=2,origin='model')

    def test_unknown_plan_reuses_available_material_and_keeps_the_writing_call(self):
        original = self.interrupt(interruption='draft_plan')
        restarted,failed = self.restart(original)
        self.assertEqual(failed['ledger']['dispatches'][-1]['status'],'已派发')
        done = restarted.start(original['id'])
        self.assertEqual(self.calls,['draft_plan','draft_report'])
        self.assert_draft(restarted,done,original,batch=2,model_calls=2,origin='model')
        self.assertTrue(any(row['stage']=='取数规划' and '已派发' in row['message'] for row in done['draft_notices']))

    def test_unknown_report_uses_the_available_draft_without_a_second_call(self):
        original = self.interrupt(interruption='draft_report')
        restarted,failed = self.restart(original)
        self.assertEqual(failed['ledger']['dispatches'][-1]['status'],'已派发')
        done = restarted.start(original['id'])
        self.assertEqual(self.calls,['draft_plan','draft_report'])
        self.assert_draft(restarted,done,original,batch=2,model_calls=2,origin='data_outline')

    def test_changed_material_is_a_new_input_with_the_original_budget(self):
        original = self.interrupt(interruption='draft_report')
        restarted,failed = self.restart(original)
        restarted.add_material(original['id'],'补充材料.txt','新的资料需要重新纳入研究。'.encode())
        done = restarted.start(original['id'])
        self.assertEqual(self.calls,['draft_plan','draft_report','draft_plan','draft_report'])
        self.assertEqual(done['ledger']['limits'],failed['ledger']['limits'])
        self.assert_draft(restarted,done,original,batch=2,model_calls=4,origin='model')

    def test_reply_after_cancellation_is_not_used_or_resent_on_resume(self):
        agent = self.open(cancel=True)
        original = agent.create('解释本次供需变化的传导路径。',research_mode='current')
        cancelled = agent.start(original['id'])
        self.assertEqual(cancelled['status'],'已取消')
        self.assertEqual(cancelled['ledger']['used']['model_calls'],2)
        self.assertIsNone(retained_submission(cancelled))
        self.assertEqual(agent.archive.delivery_versions(original['id']),[])
        done = agent.start(original['id'])
        self.assertEqual(self.calls,['draft_plan','draft_report'])
        self.assert_draft(agent,done,original,batch=2,model_calls=2,origin='data_outline')

    def test_recovery_uses_the_saved_quote_without_redispatch(self):
        used = []
        def transport(task, source, operation, parameters, credentials):
            used.append(operation)
            return self.quote_transport(task, source, operation, parameters, credentials)

        first = self.open(interruption='draft_report',requests=[QUOTE_REQUEST])
        first.collector.external = transport
        with patch('researchhelper.market_calendar.QUOTE_ENDPOINT_VERSION', 7):
            original = first.create('核对600519.SH在2026年10月15日的收盘价。', objects=['600519.SH'], research_mode='current')
        with self.assertRaises(InterruptedProcess):
            first.start(original['id'])
        first.close_runtime()
        restarted,failed = self.restart(original,requests=[QUOTE_REQUEST])
        restarted.collector.external = transport
        done = restarted.start(original['id'])
        self.assertEqual(self.calls,['draft_plan','draft_report'])
        self.assertEqual(used,['cmd_history_quotation'])
        self.assertEqual(done['ledger']['used']['data_requests'],failed['ledger']['used']['data_requests'])
        self.assertEqual(done['draft_report']['origin'],'data_outline')
        self.assertEqual([fact['value'] for fact in done['draft_report']['facts']],[112])
        self.assertEqual(restarted.archive.delivery_versions(original['id']),[1])

    def test_saved_interval_reply_from_an_earlier_release_is_reused_offline(self):
        fixture = seed_interrupted_quote(self.store)
        original = dict(id=fixture['task_id'],cutoff=fixture['cutoff'])
        restarted,failed = self.restart(original,offline=True)
        self.assertTrue(retained_submission(failed))
        saved = restarted.retry_save(original['id'])
        self.assert_draft(restarted,saved,original,batch=1,model_calls=2,origin='model',data_requests=2)
        self.assertEqual(saved['draft_report']['conclusion'],fixture['conclusion'])
        self.assertEqual(saved['ledger']['dispatches'],failed['ledger']['dispatches'])
        self.assertIn('3.57',[row['display'] for row in saved['draft_report']['calculations']])

    def test_unknown_interval_reply_from_an_earlier_release_is_not_resent(self):
        fixture = seed_interrupted_quote(self.store,reply_saved=False)
        original = dict(id=fixture['task_id'],cutoff=fixture['cutoff'])
        restarted,failed = self.restart(original,requests=[INTERVAL_REQUEST])
        with patch.object(restarted.collector,'external',side_effect=AssertionError('Source request repeated')):
            saved = restarted.start(original['id'])
        self.assertEqual(self.calls,[])
        self.assert_draft(restarted,saved,original,batch=2,model_calls=2,origin='data_outline',data_requests=2)
        self.assertEqual(saved['ledger']['dispatches'],failed['ledger']['dispatches'])
        self.assertIn('3.57',[row['display'] for row in saved['draft_report']['calculations']])

    def test_finish_with_saved_prices_works_offline_and_with_live_configuration(self):
        from lxml import html
        from researchhelper.report_designer import render_html

        for offline in (True, False):
            with self.subTest(offline=offline):
                fixture = seed_interrupted_quote(self.store,reply_saved=False)
                original = dict(id=fixture['task_id'],cutoff=fixture['cutoff'])
                agent,before = self.restart(original,offline=offline,requests=[INTERVAL_REQUEST])
                self.assertTrue(workspace_task_view(before)['can_finish_from_data'])
                with patch.object(agent.collector,'external',side_effect=AssertionError('Data dispatch repeated')):
                    result = agent.start(original['id'],retained_data_only=not offline)
                self.assertEqual(self.calls,[])
                self.assert_draft(agent,result,original,batch=2,model_calls=2,origin='data_outline',data_requests=2)
                self.assertEqual(result['run_mode'],'local_draft')
                self.assertEqual(result['input_hash'],before['input_hash'])
                self.assertEqual(result['source_records'],before['source_records'])
                self.assertEqual(result['ledger']['dispatches'],before['ledger']['dispatches'])
                self.assertEqual([row['value'] for row in result['draft_report']['facts']],[112,116])
                changes = [row for row in result['draft_report']['calculations'] if row['method']=='raw_change_v1'
                           and not row.get('comparison_kind')]
                self.assertEqual([(row.get('window_start'),row.get('window_end'),row['display']) for row in changes],
                                 [(INTERVAL_DAYS[0],INTERVAL_DAYS[-1],'3.57')])
                report = agent.export(result['id'],result['version'])
                for profile in ('standard','detailed'):
                    body = html.fromstring(render_html(report['content'],ROOT,profile=profile)).find('.//main')
                    content = body.text_content()
                    for value in ('收盘走势与样本区间','3.57%','112','116',*INTERVAL_DAYS):
                        self.assertIn(value,content)

    def test_local_finish_rejects_absent_data_pending_prose_and_exhausted_budget(self):
        agent = self.open(offline=True)
        blank = agent.create('比较贵州茅台的收盘价。')
        with self.assertRaisesRegex(ContractError,'没有可用于出稿'):
            agent.start(blank['id'],retained_data_only=True)
        self.assertEqual(agent.archive.get(blank['id']),blank)
        for saved_reply in (True,False):
            fixture = seed_interrupted_quote(self.store,reply_saved=saved_reply)
            agent.recover_interrupted()
            task = agent.archive.get(fixture['task_id'])
            if saved_reply:
                with self.assertRaisesRegex(ContractError,'重试保存'):
                    agent.start(task['id'],retained_data_only=True)
            else:
                task['ledger']['used']['seconds'] = task['ledger']['limits']['seconds']
                task = agent.archive.save(task)
                with self.assertRaises(ExecutionTimeExhausted):
                    agent.start(task['id'],retained_data_only=True)
            self.assertFalse(workspace_task_view(task).get('can_finish_from_data'))
            self.assertEqual(agent.archive.get(task['id']),task)

    def test_local_render_failure_saves_a_checkpoint_without_using_more_data(self):
        fixture = seed_interrupted_quote(self.store,reply_saved=False)
        agent,before = self.restart(dict(id=fixture['task_id'],cutoff=fixture['cutoff']),offline=True)
        with patch('researchhelper.reports.render',side_effect=OSError('Offline render failure')):
            failed = agent.start(before['id'],retained_data_only=True)
        self.assertEqual(failed['status'],'失败')
        self.assertTrue(workspace_task_view(failed)['has_pending_content'])
        self.assertFalse(workspace_task_view(failed).get('can_finish_from_data'))
        saved = agent.retry_save(failed['id'])
        self.assertEqual((saved['batch'],saved['input_hash']),(failed['batch'],before['input_hash']))
        self.assertEqual(saved['ledger']['dispatches'],before['ledger']['dispatches'])
        self.assertEqual(agent.export(saved['id'])['content'],failed['pending_content'])

    def test_local_draft_ignores_cached_host_dispatches_and_checks_source_binding(self):
        fixture = seed_interrupted_quote(self.store,reply_saved=False)
        agent,task = self.restart(dict(id=fixture['task_id'],cutoff=fixture['cutoff']),offline=False)
        task['host_result'] = dict(draft_report=PAPER,data_requests=[INTERVAL_REQUEST])
        bad = copy.deepcopy(task)
        for record in bad['source_records']:
            if record['operation']=='cmd_history_quotation':
                record['input_hash']='another-input'
        agent.archive.save(bad)
        rejected = agent.archive.get(task['id'])
        self.assertFalse(workspace_task_view(rejected).get('can_finish_from_data'))
        with self.assertRaisesRegex(ContractError,'没有可用于出稿'):
            agent.start(task['id'],retained_data_only=True)
        task['revision']=rejected['revision']
        task=agent.archive.save(task)
        with patch.object(agent.collector,'external',side_effect=AssertionError('Cached source request repeated')):
            saved=agent.start(task['id'],retained_data_only=True)
        self.assertEqual(self.calls,[])
        self.assertEqual(saved['draft_report']['origin'],'data_outline')
        self.assertNotEqual(saved['draft_report']['conclusion'],PAPER['conclusion'])
        self.assertEqual(saved['ledger']['dispatches'],task['ledger']['dispatches'])


def seed_recoverable_report(store):
    """Prepare an isolated App store before its bundled service starts."""
    agent = ResearchAgent(ResearchArchive(store),ROOT,offline=False,require_argument_support=True,draft_first=True)
    calls = []
    agent.model = ReplayModel(agent,calls,loss_stage='draft_report')
    task = agent.create('解释本次供需变化的传导路径。',research_mode='current',owner_account='admin')
    try:
        try:
            agent.start(task['id'])
        except InterruptedProcess:
            pass
        else:
            raise AssertionError('The fixture did not interrupt the worker')
        checkpoint = agent.archive.get(task['id'])
        assert calls==['draft_plan','draft_report'] and retained_submission(checkpoint)
        return dict(task_id=task['id'],cutoff=task['cutoff'],title=PAPER['title'],conclusion=PAPER['conclusion'],
                    model_calls=checkpoint['ledger']['used']['model_calls'])
    finally:
        agent.close_runtime()
