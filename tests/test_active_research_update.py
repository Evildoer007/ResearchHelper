"""Active updates refresh required query state without mixing old and new snapshots."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tests import ROOT
from tests import test_draft_first_workflow as financial
from tests import test_draft_identity_scope as identity
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError,canonical,charge,fingerprint,now
from researchhelper.draft_sources import observations,source_context,source_issues,writing_context
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import snapshot_record,_record_current_acquisition,identity_resolutions,current_query_reference
from researchhelper.reports import build_version_changes,render,validate_version_changes


class ActiveFinancialUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):financial.DraftFirstWorkflowTests.setUpClass()
    @classmethod
    def tearDownClass(cls):financial.DraftFirstWorkflowTests.tearDownClass()
    def setUp(self):
        self.control=financial.DraftFirstWorkflowTests();self.control.setUp();self.addCleanup(self.control.doCleanups)
        self.control.responses=deepcopy(self.control.responses);self.agent=self.control.agent;self.calls=[];self.messages=[]
        transport=self.control.transport;reason=self.control.model.reason
        def tracked(task,source,operation,params,credentials):
            self.calls.append(dict(version=task['version'],operation=operation,parameters=deepcopy(params)))
            return transport(task,source,operation,params,credentials)
        def model(task,messages,**options):
            self.messages.append(dict(version=task['version'],phase=options['phase'],body=json.loads(messages[-1]['content'])))
            return reason(task,messages,**options)
        self.agent.collector.external=tracked;self.control.model.reason=model
    def first(self):
        task,paper=self.control.run_task();self.frozen=deepcopy(paper);return task
    def check_frozen(self,task):self.assertEqual(self.agent.archive.report(task['id'],1),self.frozen)
    def test_updates_refresh_account_policy_and_both_financial_periods(self):
        first=self.first();done=self.agent.update(first['id'])
        self.assertEqual([row['operation'] for row in self.calls if row['version']==2],['get_limit_policy','basic_data_service','basic_data_service'])
        self.assertTrue(done.get('delivery'));self.assertEqual(done['ledger']['used']['data_requests'],3)
        self.assertEqual(len(done['draft_report']['facts']),8);self.assertEqual(len(done['draft_analysis']['results']),len(first['draft_analysis']['results']))
        self.assertTrue(any(row['operation']=='get_limit_policy' and current_query_reference(done,row) for row in done['source_records']))
        comparison = done['changes']['draft_observations']
        self.assertEqual(comparison['counts']['refreshed'], 8)
        self.assertEqual(comparison['rows'], [])
        self.assertEqual(len(done['source_records']),6);self.check_frozen(done)
    def test_revised_response_replaces_only_its_old_query_in_analysis_and_prose(self):
        first=self.first();old=deepcopy(first['source_records'])
        self.control.responses['basic_data_service:20260630']['tables'][0]['table']['ths_revenue_stock']=[123456789.125]
        done=self.agent.update(first['id']);facts=done['draft_report']['facts']
        revised=[row for row in facts if row['field']=='ths_revenue_stock' and row['raw_value']==123456789.125]
        self.assertEqual(len(revised),1);self.assertEqual(len(facts),8)
        fresh={row['id'] for row in done['source_records'] if current_query_reference(done,row)}
        self.assertTrue(all(row['artifact_id'] in fresh for row in facts))
        for row in old:
            retained=next(item for item in done['source_records'] if item['id']==row['id'])
            self.assertEqual(retained['snapshot'],row['snapshot']);self.assertEqual(retained['hash'],row['hash'])
        context=source_context(done);replaced=[row for row in context if 'superseded_by' in row]
        self.assertEqual(len(replaced),2);self.assertTrue(all(not row['content'] for row in replaced))
        writing=writing_context(done,facts);self.assertIn('123456789.125',canonical(writing))

        comparison = done['changes']['draft_observations']
        self.assertEqual(comparison['counts']['changed'], 1)
        self.assertEqual(comparison['counts']['refreshed'], 7)
        row, = comparison['rows']
        self.assertNotEqual(row['prior_source_ref'], row['current_source_ref'])
        self.assertIn(row['current_source_ref'], fresh)
        self.assertIn('123456789.125', row['cells'][3])
        self.assertNotIn('未取得新时点证据', ''.join(done['gaps']))
        paper = self.agent.archive.report(done['id'], 2)
        self.assertEqual(paper['content']['changes']['draft_observations'], comparison)
        self.assertNotIn('id="changes"', render(paper['content'], self.agent.resources))
        detail = render(paper['content'], self.agent.resources, report_profile='detailed')
        self.assertIn('id="changes"', detail)
        self.assertIn(comparison['summary'], detail)
        self.assertIn(row['cells'][3], detail)
        corrupted = deepcopy(done)
        corrupted['changes']['draft_observations']['rows'][0]['cells'][3] = '未返回'
        with self.assertRaises(ContractError):
            validate_version_changes(corrupted, self.frozen)
        self.check_frozen(done)
    def test_missing_values_in_a_successful_refresh_are_not_filled_from_old_values(self):
        first=self.first()
        self.control.responses['basic_data_service:20260630']['tables'][0]['table']['ths_revenue_stock']=[None]
        done=self.agent.update(first['id']);current=[row for row in done['draft_report']['facts'] if row['field']=='ths_revenue_stock']
        self.assertEqual(len(current),2);self.assertEqual(sum(row['raw_value'] is None for row in current),1)
        comparison = done['changes']['draft_observations']
        self.assertEqual(comparison['counts']['missing'], 1)
        self.assertEqual(comparison['rows'][0]['cells'][3], '未返回')
        self.assertEqual(len(done['draft_report']['facts']),8);self.assertTrue(done.get('delivery'));self.check_frozen(done)
    def test_failed_refresh_keeps_usable_old_values_with_their_previous_version(self):
        first=self.first();self.control.failed_financial_period='20260630'
        done=self.agent.update(first['id']);self.assertTrue(done.get('delivery'))
        old_ids={row['id'] for row in first['source_records'] if financial.period(row['parameters'])=='20260630'}
        self.assertTrue(any(row['artifact_id'] in old_ids for row in done['draft_report']['facts']))
        contexts=[row for row in source_context(done) if row['id'] in old_ids]
        counts = done['changes']['draft_observations']['counts']
        self.assertEqual(counts['retained'], 4)
        self.assertEqual(counts['refreshed'], 4)
        self.assertTrue(contexts);self.assertTrue(all(row.get('retained_from_version')==1 and row['content'] for row in contexts));self.check_frozen(done)
    def test_cancelled_update_resumes_completed_sources_without_requesting_them_twice(self):
        first=self.first();from researchhelper import draft_workflow
        checkpoint=draft_workflow._checkpoint;stopped=[]
        def save_then_stop(engine,task):
            checkpoint(engine,task)
            if not stopped and task['version']==2 and any(row['operation']=='basic_data_service' and row.get('acquisition',{}).get('research_version')==2 for row in task['source_records']):
                stopped.append(True);engine.cancel(task['id'])
        with patch.object(draft_workflow,'_checkpoint',side_effect=save_then_stop):cancelled=self.agent.update(first['id'])
        self.assertEqual(cancelled['status'],'已取消');before=deepcopy(cancelled['ledger']['used'])
        done=self.agent.start(first['id']);self.assertTrue(done.get('delivery'))
        self.assertEqual([row['operation'] for row in self.calls if row['version']==2],['get_limit_policy','basic_data_service','basic_data_service'])
        self.assertEqual(done['ledger']['used']['model_calls'],2);self.assertGreaterEqual(done['ledger']['used']['data_requests'],before['data_requests'])
        self.assertEqual(len(done['draft_report']['facts']),8);self.check_frozen(done)
    def test_saving_after_composition_failure_compares_the_retained_observations(self):
        first = self.first()
        self.control.responses['basic_data_service:20260630']['tables'][0]['table']['ths_revenue_stock'] = [123456789.125]
        with patch('researchhelper.draft_workflow.normalize_report', side_effect=OSError('Controlled save failure')):
            failed = self.agent.update(first['id'])
        self.assertEqual(failed['status'], '失败')
        requests = deepcopy(self.calls)
        messages = deepcopy(self.messages)
        done = self.agent.retry_save(first['id'])
        self.assertEqual(self.calls, requests)
        self.assertEqual(self.messages, messages)
        self.assertEqual(done['ledger']['used']['data_requests'], failed['ledger']['used']['data_requests'])
        self.assertEqual(done['ledger']['used']['model_calls'], failed['ledger']['used']['model_calls'])
        self.assertEqual(done['changes']['draft_observations']['counts']['changed'], 1)
        self.assertTrue(done.get('delivery'))
        self.check_frozen(done)
    def test_older_reader_and_frozen_reports_keep_their_original_selection(self):
        first=self.first();done=self.agent.update(first['id']);legacy=deepcopy(done);legacy['draft_source_reader_version']=11
        self.assertEqual(len(observations(legacy)),16);self.assertEqual(len(observations(done)),8);self.check_frozen(done)
    def test_planning_and_writing_know_that_old_snapshots_are_not_new_acquisitions(self):
        first=self.first();done=self.agent.update(first['id'])
        for phase in ('draft_plan','draft_report'):
            row=next(row for row in self.messages if row['version']==2 and row['phase']==phase)
            self.assertEqual(row['body']['active_update']['prior_version'],1)
        plan=next(row['body'] for row in self.messages if row['version']==2 and row['phase']=='draft_plan')
        self.assertTrue(any(row.get('retained_from_version')==1 for row in plan['already_received']));self.check_frozen(done)
    def test_conflicting_same_version_responses_are_not_silently_ordered(self):
        first=self.first();done=self.agent.update(first['id']);task=deepcopy(done)
        self.control.responses['basic_data_service:20260630']['tables'][0]['table']['ths_revenue_stock']=[123456789.125]
        params=next(row['parameters'] for row in task['source_records'] if row['operation']=='basic_data_service' and financial.period(row['parameters'])=='20260630')
        extra=self.control.transport(task,'ifind','basic_data_service',params,{})
        task['source_records'].append(extra)
        facts = observations(task)
        self.assertEqual(len(facts), 7)
        self.assertEqual([row['period'] for row in facts if row['field']=='ths_revenue_stock'], ['20250630'])
        conflicts = [issue for issue in source_issues(task) if '同版财务原值存在冲突' in issue['reason']]
        self.assertEqual(len(conflicts), 2)
        self.assertIn(extra['id'], {issue['artifact_id'] for issue in conflicts})
        self.assertEqual(fingerprint(extra['snapshot']), extra['hash'])
        task['draft_report']['facts'] = facts
        counts = build_version_changes(task, self.frozen)['draft_observations']['counts']
        self.assertEqual((counts['missing'], counts['unmatched'], counts['changed']), (1, 0, 0))
        self.assertFalse({row['artifact_id'] for row in facts} & {row['id'] for row in first['source_records']})
        self.check_frozen(done)


class ActiveHongKongUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        identity.DraftIdentityScopeTests.setUpClass()
        import gzip
        with gzip.open(ROOT/'result/acceptance/source-matrix-20261031/followup/sources.json.gz','rt') as stream:cls.sources=json.load(stream)
        if isinstance(cls.sources,dict):cls.sources=cls.sources['source_records']
    def setUp(self):
        self.control=identity.DraftIdentityScopeTests();self.control.setUp();self.addCleanup(self.control.doCleanups)
        self.control.sources=deepcopy(self.control.sources);self.agent=self.control.agent;self.calls=[]
        base=self.control.transport
        def transport(task,source,operation,params,credentials):
            self.calls.append(dict(version=task['version'],operation=operation))
            if operation=='get_thscode':return base(task,source,operation,params,credentials)
            payload=deepcopy(next(row['snapshot'] for row in self.sources if row['operation']==operation))
            charge(task['ledger'],'data_requests',1);record=snapshot_record(task,source,operation,params,payload)
            event=dict(kind='data_requests',source=source,operation=operation,batch=task['batch'],fingerprint=record['request_hash'],status='成功',time=now(),transport_isolated=True,network_request_started=True,network_worker_reaped=True,synthetic_test_transport=True)
            task['ledger']['dispatches'].append(event);_record_current_acquisition(task,record,event);return record
        self.agent.collector.external=transport
    def model(self,task,messages,*,phase,delivery=False,**options):
        charge(task['ledger'],'model_calls',1,delivery=delivery)
        if phase=='draft_plan':
            params=deepcopy(next(row['parameters'] for row in self.sources if row['operation']=='cmd_history_quotation'))
            return canonical(dict(data_requests=[dict(id='hk_quote',source='ifind',operation='cmd_history_quotation',parameters=params,question_ids=[task['questions'][0]['id']],selection_reason='按实际映射核对两日行情。')]))
        return canonical(dict(title='港股行情观察',conclusion='只讨论已取得交易日的原始行情。',logics=[dict(title='价格窗口',analysis='价格变化不等于经营改善。')]))
    def test_model_updates_refresh_mapping_before_the_quote_and_keep_eight_observations(self):
        self.agent.model=SimpleNamespace(reason=self.model)
        first=self.agent.start(self.control.task['id']);self.assertTrue(first.get('delivery'));frozen=deepcopy(self.agent.archive.report(first['id'],1))
        done=self.agent.update(first['id']);self.assertTrue(done.get('delivery'))
        self.assertEqual([row['operation'] for row in self.calls if row['version']==2],
                         ['get_thscode', 'cmd_history_quotation'])
        self.assertEqual(len(done['draft_report']['facts']),8)
        ready=[row for row in identity_resolutions(done) if row['status']=='代码映射已核验']
        self.assertEqual([row['codes'] for row in ready],[['0700.HK']]);self.assertEqual(ready[0]['binding']['research_version'],2)
        self.assertEqual(self.agent.archive.report(first['id'],1),frozen)
    def test_exact_host_query_refreshes_an_inherited_mapping(self):
        from datetime import datetime,timedelta
        first=self.control.acquire('seccode','00700')
        paper=dict(title='证券代码核对',conclusion='使用实际供应商映射。',logics=[])
        first=self.agent.start(first['id'],dict(draft_report=paper,data_requests=[]))
        params=next(row['parameters'] for row in first['source_records'] if row['operation']=='get_thscode')
        need=dict(id='mapping',source='ifind',operation='get_thscode',parameters=params,question_ids=[first['questions'][0]['id']],selection_reason='主动更新时重新核对代码映射。')
        done=self.agent.update(first['id'],cutoff=(datetime.fromisoformat(first['cutoff'])+timedelta(days=1)).isoformat(),host_result=dict(draft_report=paper,data_requests=[need]))
        self.assertEqual(sum(row['operation']=='get_thscode' for row in self.calls),2)
        self.assertEqual(sum(row['status']=='代码映射已核验' for row in identity_resolutions(done)),1)
    def test_current_no_match_is_reused_without_guessing_or_repeating_requests(self):
        for source in self.control.sources:
            if source['parameters']['functionpara']['mode']=='seccode':source['snapshot']['tables']=[]
        first=self.control.acquire('seccode','00700');second=self.control.acquire('seccode','00700')
        self.assertEqual(len(self.calls),1);self.assertEqual(identity_resolutions(second)[0]['status'],'无匹配')
        self.assertEqual(first['ledger']['used']['data_requests'],second['ledger']['used']['data_requests'])


class RollingMarketUpdateTests(unittest.TestCase):
    def setUp(self):
        self.code = '510300.SH'
        self.nav, self.shares = 'ths_unit_nvnf_fund', 'ths_fund_shares_fund'
        self.table = dict(thscode=self.code, time=[f'2026-10-{day}' for day in range(12, 17)],
            table={self.nav: [4.25, 4.32, 4.27, 4.23, 4.24], self.shares: [115, 120, 125, 125, 120]})
        params = dict(codes=self.code, startdate=self.table['time'][0], enddate=self.table['time'][-1],
            functionpara=dict(Fill='Blank'),
            indipara=[dict(indicator=field, indiparams=['']) for field in (self.nav, self.shares)])
        self.original_days = self.table['time'][:4]
        self.original_params = dict(params, enddate=self.original_days[-1])
        self.refresh_params = dict(params, startdate=self.original_days[2], indipara=params['indipara'][:1])
        self.revised = self.table['table'][self.nav][2] + 0.125
        self.responses = {
            1: (self.original_params, self.payload(self.original_days, {
                field: self.table['table'][field][:4] for field in (self.nav, self.shares)})),
            2: (self.refresh_params, self.payload(['2026/10/14', *self.table['time'][3:]], {
                self.nav: [self.revised, None, self.table['table'][self.nav][-1]]})),
        }
        self.dispatched = []
        self.latest_windows = {}
        store = TemporaryDirectory()
        self.addCleanup(store.cleanup)
        self.archive = ResearchArchive(Path(store.name))
        self.agent = ResearchAgent(self.archive, ROOT, offline=False, model=SimpleNamespace(reason=self.reply),
                                   source_credentials={}, require_argument_support=True, draft_first=True)
        self.addCleanup(self.agent.close_runtime)
        self.agent.collector.external = self.transport
        self.created = self.agent.create('比较指定窗口内的ETF净值与基金份额变化。',
            entry='ETF', objects=[self.code], research_mode='current',
            limits=dict(model_calls=2, data_requests=8, seconds=240))

    def payload(self, dates, fields):
        return dict(errorcode=0, tables=[dict(thscode=self.code, time=dates, table=fields)])

    def transport(self, task, source, operation, params, credentials):
        self.assertEqual((source, operation, params), ('ifind', 'date_sequence', self.responses[task['version']][0]))
        self.dispatched.append(task['version'])
        return self.acquired(task, params, self.responses[task['version']][1])

    def acquired(self, task, params, payload):
        charge(task['ledger'], 'data_requests', 1)
        record = snapshot_record(task, 'ifind', 'date_sequence', params, deepcopy(payload))
        event = dict(kind='data_requests', source='ifind', operation='date_sequence', batch=task['batch'],
                     fingerprint=record['request_hash'], status='成功', time=now(), transport_isolated=True,
                     network_request_started=True, network_worker_reaped=True, synthetic_test_transport=True)
        task['ledger']['dispatches'].append(event)
        _record_current_acquisition(task, record, event)
        self.assertIsNotNone(current_query_reference(task, record))
        return record

    def reply(self, task, messages, *, phase, delivery=False, **options):
        charge(task['ledger'], 'model_calls', 1, delivery=delivery)
        if phase == 'draft_plan':
            requests = []
            if task['version'] in self.responses:
                params, _ = self.responses[task['version']]
                if (window := self.latest_windows.get(task['version'])) is not None:
                    params = {key: value for key, value in params.items() if key not in ('startdate', 'enddate')}
                    params['observation_window'] = window
                requests.append(dict(id='etf_history', source='ifind', operation='date_sequence',
                    parameters=params, question_ids=[task['questions'][0]['id']],
                    selection_reason='核对同一ETF的原始读数。'))
            return canonical(dict(data_requests=requests))
        return canonical(dict(title='ETF净值观察', conclusion='同口径比较以实际观测日为准。',
            logics=[dict(title='净值与规模', analysis='净值与基金份额按各自原始观测日读取。')]))

    def start(self):
        task = self.agent.start(self.created['id'])
        self.assertTrue(task.get('delivery'), task.get('error'))
        return task

    def update(self):
        task = self.agent.update(self.created['id'])
        self.assertTrue(task.get('delivery'), task.get('error'))
        return task

    def latest_fixture(self, readings):
        from researchhelper.market_series import compile_observation_window, observation_selection

        dates = self.table['time']
        with patch('researchhelper.engine.now', return_value=dates[3]+'T22:00:00+08:00'):
            self.created = self.agent.create('核对最近可用的ETF净值。', entry='ETF', objects=[self.code],
                research_mode='current', limits=dict(model_calls=2, data_requests=8, seconds=240))
        for version, cutoff in enumerate(dates[3:], 1):
            task = dict(self.created, cutoff=cutoff+'T22:00:00+08:00')
            window = self.latest_windows[version] = dict(mode='latest_available', as_of=cutoff, lookback_calendar_days=7)
            request = compile_observation_window(dict(source='ifind', operation='date_sequence',
                parameters=dict(codes=self.code, functionpara=dict(Fill='Blank'),
                                indipara=self.refresh_params['indipara'], observation_window=window)),
                observation_selection(task))
            values = readings if version == 2 else self.table['table'][self.nav][2:4]
            self.responses[version] = (request['parameters'], self.payload(dates[2:2+len(values)], {self.nav: values}))

    def test_latest_refresh_records_an_explicit_blank_without_expanding_unseen_dates(self):
        self.latest_fixture([self.revised, None, None])
        first = self.start()
        original = self.archive.report(first['id'], 1)
        with patch('researchhelper.engine.now', return_value='2026-10-16T22:00:00+08:00'):
            second = self.update()
        recent, = [row for row in second['source_records'] if current_query_reference(second, row)]
        facts = second['draft_report']['facts']
        self.assertEqual([(row['period'], row['raw_value']) for row in facts],
                         [('2026-10-14', self.revised), ('2026-10-15', None)])
        self.assertTrue(all(row['artifact_id'] == recent['id'] for row in facts))
        change = next(row for row in second['changes']['draft_observations']['rows'] if row['kind'] == 'missing')
        self.assertEqual((change['period'], change['current_source_ref']), ('2026-10-15', recent['id']))
        self.assertNotIn('2026-10-16', canonical(writing_context(second, facts)['observations']))
        self.assertEqual(source_context(second)[0]['superseded_by'], [recent['id']])
        legacy = dict(second, draft_source_reader_version=19)
        self.assertIn(self.table['table'][self.nav][3], [row['raw_value'] for row in observations(legacy)])
        self.assertEqual(self.update()['draft_report']['facts'], facts)
        self.assertEqual(self.dispatched, [1, 2])
        self.assertEqual(self.archive.report(first['id'], 1), original)

    def test_later_refresh_keeps_an_older_correction_when_its_latest_point_is_replaced(self):
        self.latest_fixture([self.table['table'][self.nav][2], self.revised, 4.50])
        self.start()
        with patch('researchhelper.engine.now', return_value='2026-10-16T22:00:00+08:00'):
            second = self.update()
        days = self.table['time']
        corrected = next(row for row in second['draft_report']['facts'] if row['period'] == days[3])
        self.assertEqual(corrected['raw_value'], self.revised)
        self.responses[3] = (dict(self.refresh_params, startdate=days[4]), self.payload([days[4]], {self.nav:[4.60]}))
        third = self.update()
        facts = third['draft_report']['facts']
        self.assertEqual([(row['period'], row['raw_value']) for row in facts], [(days[3],self.revised),(days[4],4.60)])
        self.assertEqual(facts[0]['artifact_id'], corrected['artifact_id'])

    def test_latest_refresh_does_not_treat_an_omitted_date_as_an_explicit_blank(self):
        self.latest_fixture([self.revised])
        first = self.start()
        with patch('researchhelper.engine.now', return_value='2026-10-16T22:00:00+08:00'):
            second = self.update()
        facts = second['draft_report']['facts']
        self.assertEqual([(row['period'], row['raw_value']) for row in facts],
            [('2026-10-15', self.table['table'][self.nav][3]), ('2026-10-14', self.revised)])
        self.assertEqual(facts[0]['artifact_id'], first['source_records'][0]['id'])
        self.assertEqual(second['changes']['draft_observations']['counts']['missing'], 0)

    def test_rolling_refresh_keeps_nonoverlap_and_carries_authority_into_third_version(self):
        first = self.start()
        first_report = self.archive.report(first['id'], 1)
        second = self.update()
        changes = second['changes']['draft_observations']
        self.assertEqual({key:changes['counts'][key] for key in ('changed','missing','added','retained','unmatched')},
                         dict(changed=1, missing=1, added=1, retained=6, unmatched=0))
        self.assertEqual({row['period']: row['kind'] for row in changes['rows']},
                         {'2026-10-14':'changed', '2026-10-15':'missing', '2026-10-16':'added'})
        prior_id = first['source_records'][0]['id']
        recent, = [row for row in second['source_records'] if current_query_reference(second, row)]
        retained = json.loads(next(row['content'] for row in source_context(second) if row['id'] == prior_id))
        nav, = [table for table in retained['tables'] if self.nav in table['table']]
        self.assertEqual(nav['time'], self.original_days[:2])
        shares, = [table for table in retained['tables'] if self.shares in table['table']]
        self.assertEqual(shares['time'], self.original_days)
        written = writing_context(second, observations(second))
        self.assertIn(str(self.revised), canonical(written))
        self.assertNotIn(str(self.table['table'][self.nav][2]), canonical(written))
        self.assertIn('2026/10/14', canonical(written))
        self.assertTrue(all(row['current_source_ref'] == recent['id'] for row in changes['rows']))
        prior_hashes = {row['id']: row['hash'] for row in second['source_records']}
        second_report = self.archive.report(second['id'], 2)

        third = self.update()
        self.assertEqual(self.dispatched, [1, 2])
        self.assertEqual(third['draft_report']['facts'], second['draft_report']['facts'])
        self.assertEqual(third['changes']['draft_observations']['counts']['unmatched'], 0)
        self.assertEqual(third['update_from']['market_refreshes'][recent['id']]['version'], 2)
        self.assertEqual({row['id']: fingerprint(row['snapshot']) for row in third['source_records']}, prior_hashes)
        self.assertEqual(self.archive.report(first['id'], 1), first_report)
        self.assertEqual(self.archive.report(second['id'], 2), second_report)

    def test_report_orders_literal_market_date_formats_across_updates(self):
        from researchhelper.report_designer import design

        _, refresh = self.responses[2]
        refresh['tables'][0]['time'][-1] = '20261016'
        first = self.start()
        frozen_first = self.archive.report(first['id'], 1)
        second = self.update()
        third = self.update()
        self.assertEqual(second['changes']['draft_observations']['counts']['unmatched'], 0)
        self.assertEqual([row['period'] for row in second['changes']['draft_observations']['rows']],
                         ['2026-10-14', '2026-10-15', '2026-10-16'])
        days = self.table['time']
        for task in (second, third):
            content = self.archive.report(task['id'], task['version'])['content']
            before = canonical(content)
            facts = {row['id']: row for row in content['draft']['facts'] if row['field']==self.nav}
            for profile in ('standard', 'detailed'):
                document = design(content, ROOT, profile=profile)['document']
                chart, = [row for row in document['visual_sources'].values() if row.get('field')==self.nav]
                self.assertEqual(chart['auto_visual']['scheme'], 'line')
                self.assertEqual(chart['dates'], days)
                self.assertEqual(chart['values'], [4.25, 4.32, self.revised, None, 4.24])
                self.assertEqual([facts[ref]['period'] for ref in chart['data_refs']],
                                 [*days[:2], '2026/10/14', days[3], '20261016'])
                raw_periods = {row[2] for row in document['dataset']['rows']}
                self.assertTrue({'2026/10/14', '20261016'} <= raw_periods)
            self.assertEqual(canonical(content), before)
        self.assertEqual(self.archive.report(first['id'],1), frozen_first)

    def test_exact_query_keeps_earlier_values_for_omitted_cells(self):
        day = self.original_days[2]
        self.responses[2] = (self.original_params, self.payload([day], {self.nav: [self.revised]}))
        self.start()
        done = self.update()
        self.assertEqual(self.dispatched, [1, 2])
        self.assertEqual(len(observations(done)), 8)
        counts = done['changes']['draft_observations']['counts']
        self.assertEqual((counts['changed'], counts['retained'], counts['missing']), (1, 7, 0))

    def test_same_version_conflicts_and_unverified_refresh_keep_their_originals(self):
        self.start()
        done = self.update()
        fresh = next(row for row in done['source_records'] if current_query_reference(done, row))
        prior = next(row for row in done['source_records'] if row.get('retained_from_version') == 1)
        unverified = deepcopy(done)
        unverified['source_records'][-1].pop('acquisition')
        values = [row['raw_value'] for row in observations(unverified) if row['field'] == self.nav
                  and row['period'].replace('/', '-') == self.original_days[2]]
        self.assertEqual(values, [self.table['table'][self.nav][2], self.revised])
        legacy = dict(done, draft_source_reader_version=18)
        values = [row['raw_value'] for row in observations(legacy) if row['field'] == self.nav
                  and row['period'].replace('/', '-') == self.original_days[2]]
        self.assertEqual(values, [self.table['table'][self.nav][2], self.revised])

        duplicate = deepcopy(fresh['snapshot'])
        duplicate['tables'][0]['table'][self.nav][0] = self.revised + 0.125
        done['source_records'].append(self.acquired(done, fresh['parameters'], duplicate))
        facts = observations(done)
        matching = [row for row in facts if row['field'] == self.nav
                    and row['period'].replace('/', '-') == self.original_days[2]]
        self.assertEqual({row['raw_value'] for row in matching}, {self.revised, self.revised + 0.125})
        self.assertNotIn(prior['id'], {row['artifact_id'] for row in matching})
        done['draft_report']['facts'] = facts
        counts = build_version_changes(done, self.archive.report(done['id'], 1))['draft_observations']['counts']
        self.assertEqual((counts['unmatched'], counts['changed']), (1, 0))

    def test_different_field_arguments_cannot_replace_retained_cells(self):
        self.start()
        done = self.update()
        older = done['source_records'][0]
        older['parameters']['indipara'][0]['indiparams'] = ['other-basis']
        older['request_hash'] = fingerprint(dict(source=older['source'], operation=older['operation'],
                                                 parameters=older['parameters']))
        facts = observations(done)
        self.assertTrue(any(row['artifact_id'] == older['id'] and row['period'] == self.original_days[2]
                            and row['field'] == self.nav for row in facts))


if __name__=='__main__':unittest.main()
