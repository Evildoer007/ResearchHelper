"""Synthetic quote and calendar replies with isolated dispatch receipts."""
import copy
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests import ROOT
from researchhelper import market_calendar as calendar
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, charge, fingerprint, now
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (EvidenceCollector, snapshot_record, current_query_reference,
    current_snapshot_reference, calendar_query_reference, _record_current_acquisition, data_request_contract)
from researchhelper.reasoning import extractable_source_views, analysis_contract
from researchhelper.output_schema import planning_output_schema
from researchhelper.planning import input_fingerprint
from researchhelper.reports import compose
from researchhelper.argument_support import review_request


def append_calendar_fixture_record(task, operation, params, payload):
    """Record a simulated provider reply with the same acquisition binding as a live call."""
    record = snapshot_record(task, 'ifind', operation, params, payload)
    event = dict(kind='data_requests', source='ifind', operation=operation,
        fingerprint=record['request_hash'], batch=task['batch'], status='成功',
        transport_isolated=True, network_request_started=True, network_worker_reaped=True,
        time=now(), synthetic_test_double=True)
    charge(task['ledger'], 'data_requests')
    task['ledger']['dispatches'].append(event)
    _record_current_acquisition(task, record, event)
    record.update(question_ids=['q1'], data_need_ids=['synthetic_calendar_fixture'])
    task['source_records'].append(record)
    return record


class MarketCalendarTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='calendar-coverage-')
        self.addCleanup(folder.cleanup)
        self.engine = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True, require_argument_support=True)
        self.addCleanup(self.engine.close_runtime)
        self.task = self.engine.create('比较600519.SH收盘价和交易日覆盖', entry='个股', objects=['600519.SH'],
            research_mode='current')
        self.task['synthetic'] = True
        self.task['knowledge'] = dict(entries=[])
        self.task['host_result'] = dict(answers={'q1': dict(answer='交易日覆盖以本任务实际取得的日历为准。', refs=[])},
                                        theses=[])
        self.days = ['2026-08-03','2026-08-04','2026-08-05','2026-08-06','2026-08-07','2026-08-10']
        self.price = self.append_record('cmd_history_quotation',
            dict(codes='600519.SH', indicators='close', startdate=self.days[0], enddate=self.days[-1],
                 functionpara=dict(Currency='RMB', CPS='1', Fill='Omit')),
            dict(errorcode=0, tables=[dict(thscode='600519.SH', time=self.days,
                                           table=dict(close=[100,105,110,115,120,117]))]))
        self.original = fingerprint(self.price['snapshot'])
        self.params = dict(marketcode='212001', functionpara=copy.deepcopy(calendar.FUNCTION),
                           startdate=self.days[0], enddate=self.days[-1])

    def tearDown(self):
        self.assertEqual(fingerprint(self.price['snapshot']), self.original)

    def append_record(self, operation, params, payload):
        return append_calendar_fixture_record(self.task, operation, params, payload)

    def test_missing_literal_readouts_deliver_the_requested_scope_without_strategy_sections(self):
        from lxml import html
        from researchhelper.draft_workflow import direct_readout
        from researchhelper.planning import draft_research_scope
        from researchhelper.report_designer import render_html

        self.engine.draft_first = True
        permitted = self.engine.create(
            '核对510300.SH在2026-10-21的单位净值。保留缺项。生成一份研究报告。', research_mode='current')
        self.assertEqual(direct_readout(permitted), ('market_day', None))
        scenarios = [
            ('核对贵州茅台600519.SH在2026-10-21的收盘价。', '600519.SH收盘价核对', ['2026-10-21']),
            ('列出000300.SH在2026-10-21的收盘点位。', '000300.SH收盘点位核对', ['2026-10-21']),
            ('核对510300.SH在2026-10-20和2026-10-21的单位净值。保留缺项。生成一份简短报告。',
             '510300.SH单位净值核对', ['2026-10-20', '2026-10-21']),
        ]
        for question, title, days in scenarios:
            with self.subTest(question=question):
                task = self.engine.create('本轮不联网，'+question, research_mode='current')
                self.assertFalse(calendar.stated_direct_market_day(task))
                self.assertTrue(calendar.stated_direct_market_day(task, for_report=True))
                self.assertIsNone(direct_readout(task))
                scope = draft_research_scope(task)
                self.assertEqual(scope['mode'], 'fact')
                self.assertEqual(scope['allowed_report_scopes'], ['fact', 'comparison'])
                result = self.engine.start(task['id'])
                self.assertIsNotNone(result['delivery'])
                self.assertEqual(result['draft_report']['logics'], [])
                self.assertEqual(result['draft_report']['title'], title)
                self.assertEqual(result['ledger']['used']['model_calls'], 0)
                self.assertEqual(result['ledger']['used']['data_requests'], 0)
                content = self.engine.export(task['id'])['content']
                for profile in ('standard', 'detailed'):
                    document = html.document_fromstring(render_html(content, ROOT, profile=profile))
                    main = document.find('.//main').text_content()
                    self.assertNotIn('策略逻辑', main)
                    self.assertTrue(all(day in main for day in days), main)
                    self.assertEqual(document.xpath('string(//main//h1)'), title)

    def test_investment_clause_preserves_investment_report_scope(self):
        from researchhelper.planning import draft_research_scope

        self.engine.draft_first = True
        task = self.engine.create(
            '本轮不联网，核对510300.SH在2026-10-21的单位净值，同时分析未来上下行的投资逻辑。',
            research_mode='current')
        self.assertFalse(calendar.stated_direct_market_day(task, for_report=True))
        self.assertIn('research', draft_research_scope(task)['allowed_report_scopes'])
        result = self.engine.start(task['id'])
        self.assertIsNotNone(result['delivery'])
        self.assertEqual(result['draft_report']['logics'], [])
        self.assertIn('资料不足以形成研究判断', result['draft_report']['conclusion'])

    def append_calendar(self, days=None, params=None):
        return self.append_record('get_trade_dates', params or self.params,
            dict(errorcode=0, tables=dict(time=self.days if days is None else days)))

    def price_need(self):
        return dict(id='price_scope', source='ifind', operation='cmd_history_quotation',
            parameters=copy.deepcopy(self.price['parameters']), question_ids=['q1'])

    def test_separate_calendar_matches_price_dates_without_financial_approval(self):
        source = self.append_calendar()
        result = calendar.coverage(self.task)
        self.assertEqual(len(result['panels']), 1)
        panel = result['panels'][0]
        self.assertEqual(panel['status'], '日历覆盖一致')
        self.assertEqual(panel['calendar_dates'], self.days)
        self.assertFalse(panel['financial_approval'])
        self.assertFalse(panel['historical_availability_approved'])
        self.assertEqual(panel['calendar_ref']['snapshot_hash'], source['hash'])

    def test_prices_alone_never_become_the_calendar(self):
        result = calendar.coverage(self.task)['panels'][0]
        self.assertEqual(result['status'], '日历未核验')
        self.assertIsNone(result['calendar_ref'])
        with self.assertRaises(ContractError):
            calendar.sessions(self.params, self.price['snapshot'])

    def test_cross_weekend_session_calculation_uses_a_verified_calendar_and_exact_prices(self):
        from researchhelper.draft_analysis import analyze
        from researchhelper.draft_sources import observations

        def daily():
            return [row for row in analyze(self.task, observations(self.task))['results']
                    if row.get('comparison_kind') == 'previous_session']

        self.assertEqual(daily(), [])

        record = self.append_calendar()
        self.assertEqual(len(daily()), 1)
        result = daily()[0]
        self.assertEqual((result['window_start'], result['window_end'], result['display']),
                         ('2026-08-07', '2026-08-10', '-2.50'))
        self.assertEqual(result['session_basis']['source_refs'], [record['id']])
        conflict = self.append_calendar(['2026-08-07','2026-08-09'],
                                        dict(self.params, startdate='2026-08-07', enddate='2026-08-09'))
        self.assertEqual(daily(), [])
        self.task['source_records'].remove(conflict)
        payload = copy.deepcopy(self.price['snapshot'])
        payload['tables'][0]['table']['close'][-2] = None
        self.task['source_records'].remove(self.price)
        self.append_record('cmd_history_quotation', self.price['parameters'], payload)
        self.assertEqual(daily(), [])

    def test_omitted_window_endpoints_do_not_move_the_requested_interval(self):
        from researchhelper.draft_analysis import analyze
        from researchhelper.draft_sources import observations, client_writing_context
        from researchhelper.drafts import normalize_report

        for omit in (0, -1):
            with self.subTest(omit=omit):
                task = copy.deepcopy(self.task)
                task['draft_quote_interval_endpoints_version'] = 1
                task['draft_client_copy_version'] = 3
                task['input_hash'] = input_fingerprint(task)
                task['source_records'] = []
                quote = copy.deepcopy(self.price['snapshot'])
                quote['tables'][0]['time'].pop(omit)
                quote['tables'][0]['table']['close'].pop(omit)
                append_calendar_fixture_record(task,'cmd_history_quotation',self.price['parameters'],quote)
                append_calendar_fixture_record(task,'get_trade_dates',self.params,dict(errorcode=0,tables=dict(time=self.days)))
                facts = observations(task)
                result = analyze(task,facts)
                intervals = [row for row in result['results'] if row['method']=='raw_change_v1' and not row.get('comparison_kind')]
                self.assertEqual(intervals, [])
                self.assertTrue(any(row['method']=='raw_range_v1' for row in result['results']))
                task['draft_analysis'] = result
                gap, = client_writing_context(task,facts)['quote_interval_gaps']
                self.assertEqual(gap['missing_close_dates'], [self.days[omit]])
                self.assertIn(self.days[omit], '\n'.join(normalize_report(None,task,facts)['reader_notes']))

    def test_filling_a_middle_day_does_not_change_the_interval_endpoints(self):
        from researchhelper.draft_analysis import analyze
        from researchhelper.draft_sources import observations
        from researchhelper.drafts import normalize_report

        for start_value in ([], [None]):
            with self.subTest(explicit_blank=bool(start_value)):
                task = copy.deepcopy(self.task)
                self.engine._apply_draft_policies(task)
                task['input_hash'] = input_fingerprint(task)
                task['source_records'] = []
                days = self.days

                def quote(start, end, dates, values):
                    return append_calendar_fixture_record(task, 'cmd_history_quotation',
                        dict(self.price['parameters'], startdate=start, enddate=end),
                        dict(errorcode=0, tables=[dict(thscode='600519.SH', time=dates, table=dict(close=values))]))

                def current():
                    facts = observations(task)
                    result = analyze(task, facts)
                    task['draft_analysis'] = result
                    changes = [row for row in result['results']
                               if row['method'] == 'raw_change_v1' and row.get('comparison_kind') != 'previous_session']
                    return changes, result, normalize_report(None, task, facts)

                quote(days[0], days[-1], days[:len(start_value)] + days[1:2] + days[3:],
                      start_value + [105,115,120,117])
                append_calendar_fixture_record(task, 'get_trade_dates', self.params,
                                               dict(errorcode=0, tables=dict(time=days)))
                quote(days[2], days[2], [days[2]], [110])
                changes, result, report = current()
                self.assertEqual(changes, [])
                gap, = result['quote_interval_gaps']
                self.assertEqual((gap['window_start'], gap['window_end'], gap['missing_close_dates']),
                                 (days[0], days[-1], days[:1]))
                self.assertTrue(any(row['method'] == 'raw_range_v1' for row in result['results']))
                self.assertTrue(any(days[0] in note for note in report['reader_notes']))

                endpoint = quote(days[0], days[0], [days[0]], [100])
                endpoint['question_ids'] = ['other']
                self.assertEqual(current()[0], [])
                endpoint['question_ids'] = ['q1']
                changes, result, report = current()
                change, = changes
                self.assertEqual((change['period'], change['display']), (days[0]+'至'+days[-1], '17.00'))
                self.assertNotIn('quote_interval_gaps', result)
                self.assertFalse(any(days[0] in note for note in report['reader_notes']))

    def test_separate_requested_windows_remain_separate_in_the_frozen_report(self):
        from researchhelper.draft_analysis import analyze
        from researchhelper.draft_sources import observations
        from researchhelper.draft_visuals import _calculation_dependencies

        days = self.days
        windows = (days[:2], days[3:])
        task = self.engine.create(f'分别核对600519.SH在{windows[0][0]}至{windows[0][-1]}和'
                                  f'{windows[1][0]}至{windows[1][-1]}的收盘价变化。',
                                  entry='个股', objects=['600519.SH'], research_mode='current')
        task['synthetic'] = True
        self.engine._apply_draft_policies(task)
        task['input_hash'] = input_fingerprint(task)
        for window, values in zip(windows, ([100,105], [115,120,117])):
            params = dict(self.price['parameters'], startdate=window[0], enddate=window[-1])
            append_calendar_fixture_record(task, 'cmd_history_quotation', params,
                dict(errorcode=0, tables=[dict(thscode='600519.SH', time=window, table=dict(close=values))]))
        append_calendar_fixture_record(task, 'get_trade_dates', self.params,
                                       dict(errorcode=0, tables=dict(time=days)))
        facts = observations(task)
        result = analyze(task, facts)
        changes = [row for row in result['results']
                   if row['method'] == 'raw_change_v1' and row.get('comparison_kind') != 'previous_session']
        expected = [(window[0]+'至'+window[-1], value) for window, value in zip(windows, ('5.00','1.74'))]
        self.assertEqual([(row['period'], row['display']) for row in changes], expected)
        self.assertEqual([row['period'] for row in result['results'] if row['method'] == 'raw_range_v1'],
                         [period for period, _ in expected])
        self.engine.archive.save(task)
        done = self.engine.start(task['id'], dict(data_requests=[], draft_report=dict(title='分段行情比较',
            conclusion='逐段对照同口径收盘读数。', logics=[dict(title='收盘价变化', analysis='对照指定区间。',
                calculation_refs=[row['id'] for row in changes])])))
        self.assertTrue(done.get('delivery'), done.get('error'))
        content = self.engine.export(task['id'])['content']
        dependencies = _calculation_dependencies(content)
        self.assertEqual([(dependencies[row['id']]['period'], dependencies[row['id']]['display'])
                          for row in changes], expected)
        self.assertEqual(result['disconnected_closes'], ['600519.SH'])
        from researchhelper.publication_content import publication_dataset
        public = [row for row in publication_dataset(content)['calculations']
                  if row['method']=='raw_change_v1' and 'window_start' in row]
        self.assertEqual([(row['window_start'],row['window_end']) for row in public],
                         [(window[0],window[-1]) for window in windows])
        from researchhelper.draft_visuals import planned_figures
        close_figures = [figure for figure in planned_figures(content) if figure['field']=='close']
        self.assertEqual(close_figures, [])
        self.assertEqual([row['period'] for row in content['draft']['facts']], windows[0] + windows[1])

    def test_short_literal_close_sequence_shares_a_calendar_and_uses_a_trend(self):
        from researchhelper.draft_visuals import planned_figures

        self.engine.draft_first = True
        days = ['2026-08-07', '2026-08-10', '2026-08-11', '2026-08-12']
        task = self.engine.create('核对600519.SH在' + '、'.join(days) + '的收盘价，生成报告。',
                                  entry='个股', research_mode='current')
        prepared = []
        for index, day in enumerate(days):
            params = dict(self.price['parameters'], startdate=day, enddate=day)
            append_calendar_fixture_record(task, 'cmd_history_quotation', params,
                dict(errorcode=0, tables=[dict(thscode='600519.SH', time=[day], table=dict(close=[100-index]))]))
            prepared.append((dict(question_ids=['q1']), 'ifind', 'cmd_history_quotation', params))
        request, = calendar.dependency_requests(task, prepared[:1])
        self.assertEqual((request['parameters']['startdate'], request['parameters']['enddate']),
                         (days[0], days[-1]))
        append_calendar_fixture_record(task, 'get_trade_dates', request['parameters'],
                                       dict(errorcode=0, tables=dict(time=days)))
        self.assertEqual(calendar.dependency_requests(task, prepared[1:]), [])
        self.engine.archive.save(task)
        final = self.engine.start(task['id'], dict(data_requests=[], draft_report={}))
        self.assertTrue(final.get('delivery'))
        content = self.engine.export(task['id'])['content']
        close, = [figure for figure in planned_figures(content) if figure['field'] == 'close']
        self.assertEqual(close['auto_visual']['scheme'], 'line')
        self.assertEqual(close['dates'], days)
        self.assertEqual(close['values'], [100, 99, 98, 97])

    def test_stated_close_window_survives_a_short_query_and_endpoint_supplement(self):
        from researchhelper.draft_analysis import analyze
        from researchhelper.draft_sources import acquisition_coverage, observations
        from researchhelper.draft_visuals import _calculation_dependencies, planned_figures
        from researchhelper.drafts import normalize_report

        for syntax in ('2026-08-03至2026-08-10', '2026年8月3日至10日'):
            with self.subTest(syntax=syntax):
                days = self.days
                task = self.engine.create(f'计算600519.SH在{syntax}的区间收盘涨跌幅。', research_mode='current')
                self.engine._apply_draft_policies(task)
                task['input_hash'] = input_fingerprint(task)
                params = dict(self.price['parameters'], startdate=days[2])
                quote = append_calendar_fixture_record(task, 'cmd_history_quotation', params,
                    dict(errorcode=0, tables=[dict(thscode='600519.SH',time=days[2:],
                                                  table=dict(close=[110,115,120,117]))]))
                request = dict(self.price_need(), parameters=params)
                need, = calendar.dependency_requests(task, [(request,'ifind','cmd_history_quotation',params)])
                self.assertEqual(need['parameters'], self.params)
                before = analyze(task, observations(task))
                gap, = before['quote_interval_gaps']
                self.assertTrue(gap['calendar_unverified'])
                self.assertEqual(gap['window_start'], days[0])
                self.assertFalse(any(row['method']=='raw_change_v1' and not row.get('comparison_kind')
                                     for row in before['results']))
                append_calendar_fixture_record(task, 'get_trade_dates', self.params,
                    dict(errorcode=0, tables=dict(time=days)))
                self.assertEqual(calendar.dependency_requests(task, [(request,'ifind','cmd_history_quotation',params)]), [])
                self.assertEqual(acquisition_coverage(task,[request])['stated_close_windows'], [
                    dict(code='600519.SH',question_id='q1',startdate=days[0],enddate=days[-1],
                         unqueried_windows=[dict(startdate=days[0],enddate=days[1])],unqueried_session_dates=days[:2],
                         missing_session_dates=days[:2],missing_session_count=2)])
                facts = observations(task)
                missing = task['draft_analysis'] = analyze(task,facts)
                self.assertEqual(missing['quote_interval_gaps'], [
                    dict(code='600519.SH',window_start=days[0],window_end=days[-1],missing_close_dates=days[:1])])
                draft = normalize_report(None,task,facts)
                self.assertTrue(any(days[0] in note for note in draft['reader_notes']))
                self.assertEqual(draft['incomplete_quote_series'], [dict(code='600519.SH',field='close')])

                append_calendar_fixture_record(task, 'cmd_history_quotation',
                    dict(self.price['parameters'],enddate=days[1]),
                    dict(errorcode=0,tables=[dict(thscode='600519.SH',time=days[:2],table=dict(close=[100,105]))]))
                self.assertEqual(acquisition_coverage(task,[request])['stated_close_windows'][0]['unqueried_windows'], [])
                repaired = analyze(task, observations(task))
                changes = [row for row in repaired['results']
                           if row['method']=='raw_change_v1' and not row.get('comparison_kind')]
                change, = changes
                self.assertEqual((change['window_start'],change['window_end'],change['display']),
                                 (days[0],days[-1],'17.00'))
                self.assertNotIn('quote_interval_gaps',repaired)
                self.engine.archive.save(task)
                done = self.engine.start(task['id'],dict(data_requests=[],draft_report=dict(title='收盘涨跌幅核对',
                    conclusion='对照原问题日期的收盘价。',logics=[dict(title='收盘变化',
                    analysis='按实际取得的区间端点复算。',calculation_refs=[change['id']])])))
                self.assertTrue(done.get('delivery'), done.get('error'))
                content = self.engine.export(task['id'])['content']
                self.assertEqual(_calculation_dependencies(content)[change['id']]['display'],'17.00')
                self.assertTrue(any(figure['type']=='line' for figure in planned_figures(content) if figure['field']=='close'))
                self.assertEqual(quote['parameters'], params)

    def test_retained_quote_window_uses_the_question_dates(self):
        from researchhelper.draft_analysis import analyze
        from researchhelper.draft_sources import observations

        first, last = self.days[1], self.days[3]
        for missing in (False, True):
            with self.subTest(missing_endpoint=missing):
                task = self.engine.create(f'本轮不联网，核对600519.SH在{first}至{last}的收盘价和区间涨跌幅。',
                                          research_mode='current')
                self.engine._apply_draft_policies(task)
                prices = copy.deepcopy(self.price['snapshot'])
                if missing:
                    table, = prices['tables']
                    table['time'].pop(3)
                    table['table']['close'].pop(3)
                quote = snapshot_record(task, 'ifind', 'cmd_history_quotation', self.price['parameters'], prices)
                dates = snapshot_record(task, 'ifind', 'get_trade_dates', self.params,
                                        dict(errorcode=0, tables=dict(time=self.days)))
                task['source_records'].extend([quote, dates])
                facts = observations(task)
                self.assertEqual([row['period'] for row in facts], self.days[1:3 if missing else 4])
                result = analyze(task, facts)
                intervals = [row for row in result['results'] if row['method']=='raw_change_v1' and row.get('window_start')]
                if missing:
                    self.assertEqual(intervals, [])
                    gap, = result['quote_interval_gaps']
                    self.assertEqual((gap['window_start'],gap['window_end']), (first,last))
                    self.assertTrue(gap['calendar_unverified'])
                else:
                    self.assertEqual([(row['window_start'],row['window_end'],row['display']) for row in intervals],
                                     [(first,last,'9.52')])
                self.assertEqual(quote['snapshot'], prices)
                self.assertEqual(task['ledger']['used']['data_requests'], 0)

    def test_linked_market_response_uses_question_dates_in_planning_writing_and_coverage(self):
        from researchhelper.draft_sources import acquisition_coverage, observations, source_context, writing_context

        first, last = self.days[1], self.days[3]
        task = self.engine.create(f'核对600519.SH在{first}至{last}的收盘价和区间变化。', research_mode='current')
        self.engine._apply_draft_policies(task)
        task['input_hash'] = input_fingerprint(task)
        quote = append_calendar_fixture_record(task, 'cmd_history_quotation', self.price['parameters'], self.price['snapshot'])
        append_calendar_fixture_record(task, 'get_trade_dates', self.params, dict(errorcode=0, tables=dict(time=self.days)))
        request = dict(id='synthetic_calendar_fixture', source='ifind', operation=quote['operation'],
                       parameters=quote['parameters'], question_ids=['q1'])
        before = fingerprint(task)
        picked = self.days[1:4]
        self.assertEqual([row['period'] for row in observations(task)], picked)
        self.assertEqual(list(calendar.verified_session_window('600519.SH',
            *map(calendar.date.fromisoformat, (self.days[0],self.days[-1])), calendar.retained_calendars(task))),
            list(map(calendar.date.fromisoformat,self.days)))
        source = next(row for row in source_context(task) if row['id']==quote['id'])
        self.assertEqual(json.loads(source['content'])['tables'][0]['time'], picked)
        self.assertEqual(source['parameters']['startdate'], self.days[0])
        previous = dict(task, **{calendar.QUOTE_ENDPOINT_POLICY:5})
        old_rows = observations(previous)
        self.assertEqual([row['period'] for row in old_rows], self.days)
        writer = writing_context(task, old_rows)
        series, = writer['observations']
        self.assertEqual([row[series['columns'].index('period')] for row in series['rows']], picked)
        self.assertFalse(any(row['id']==quote['id'] for row in writer['other_data']))
        coverage = acquisition_coverage(task, [request])
        self.assertIn(request['id'], coverage['complete_request_ids'])
        self.assertFalse(coverage['unresolved'])
        self.assertFalse(coverage['stated_close_windows'][0]['missing_session_dates'])
        self.assertEqual(fingerprint(task),before)

    def test_linked_quote_records_keep_separate_question_windows(self):
        from researchhelper.draft_sources import observations

        question = '核对600519.SH从2026年8月3至4日的收盘价。'
        task = self.engine.create(question, research_mode='current')
        self.engine._apply_draft_policies(task)
        task['questions'] += [dict(id='q2', question='核对600519.SH从2026年8月7至10日收盘价。'),
                              dict(id='q3', question='核对600519.SH近期收盘价。')]
        row = snapshot_record(task, 'ifind', 'cmd_history_quotation', self.price['parameters'], self.price['snapshot'])
        task['source_records'].append(row)
        for links, days in ((['q1'], self.days[:2]), (['q2'],self.days[-2:]),
                            (['q1','q2'],self.days[:2]+self.days[-2:]), (['q1','q3'],self.days)):
            with self.subTest(links=links):
                row['question_ids'] = links
                self.assertEqual([point['period'] for point in observations(task)], days)

    def test_linked_nav_uses_only_its_dated_cells_and_registries_keep_raw_values(self):
        from researchhelper.draft_sources import acquisition_coverage, observations, source_context
        from researchhelper.market_series import NAV_INDICATOR

        task = self.engine.create('核对510300.SH从2026年8月4至6日的单位净值。', research_mode='current')
        self.engine._apply_draft_policies(task)
        field = 'ths_fund_shares_fund'
        dates = self.days[:5]
        params = dict(codes='510300.SH', startdate=dates[0], enddate=dates[-1], functionpara={'Fill':'Blank'},
                      indipara=[dict(indicator=key,indiparams=['']) for key in (NAV_INDICATOR,field)])
        payload = dict(errorcode=0, tables=[dict(thscode='510300.SH', time=dates,
            table={NAV_INDICATOR:[4.39,4.26,4.20,None,4.11],field:[8000,8100,8200,8300,8400]})])
        linked = snapshot_record(task, 'ifind', 'date_sequence', params, payload)
        linked.update(question_ids=['q1'], data_need_ids=['nav'])
        task['source_records'].append(linked)
        request = dict(id='nav', source='ifind', operation='date_sequence', parameters=params, question_ids=['q1'])
        expected = list(zip(dates[1:4], [4.26,4.20,None]))
        self.assertEqual([(row['period'],row['value']) for row in observations(task)], expected)
        table, = json.loads(source_context(task)[0]['content'])['tables']
        self.assertEqual(table['time'], dates[1:4])
        self.assertEqual(table['table'], {NAV_INDICATOR:[4.26,4.20,None]})
        self.assertIn('nav', acquisition_coverage(task,[request])['complete_request_ids'])
        self.assertEqual(linked['snapshot'],payload)

    def test_explicit_ranges_do_not_mix_different_securities_or_non_quote_questions(self):
        task = self.engine.create('核对600519.SH在2026-08-03至2026-08-07的收盘价及002594.SZ在'
                                  '2026年8月4日至10日的收盘价。另查2025-01-01至2025-12-31的财务数据。',
                                  research_mode='current')
        self.engine._apply_draft_policies(task)
        bounds = calendar.stated_close_intervals(task)
        self.assertEqual({code: [(a.isoformat(),b.isoformat()) for a,b,_ in sorted(windows)]
                          for code,windows in bounds.items()},
                         {'600519.SH':[('2026-08-03','2026-08-07')], '002594.SZ':[('2026-08-04','2026-08-10')]})
        task[calendar.QUOTE_ENDPOINT_POLICY] = 2
        self.assertEqual(calendar.stated_close_intervals(task), {})

    def test_abbreviated_daily_periods_bind_the_requested_metric_and_subject(self):
        from researchhelper.market_series import stated_nav_intervals

        task = self.engine.create('核对600519.SH在2026年8月3至7日的区间收盘涨跌幅，'
                                  '再查510300.SH在2026-08-04至08-10的单位净值。', research_mode='current')
        self.engine._apply_draft_policies(task)
        close, = calendar.stated_close_intervals(task)['600519.SH']
        nav, = stated_nav_intervals(task)['510300.SH']
        self.assertEqual(tuple(day.isoformat() for day in close[:2]), ('2026-08-03','2026-08-07'))
        self.assertEqual(tuple(day.isoformat() for day in nav[:2]), ('2026-08-04','2026-08-10'))
        self.assertNotIn('510300.SH', calendar.stated_close_intervals(task))
        self.assertNotIn('600519.SH', stated_nav_intervals(task))

    def test_date_first_clauses_keep_each_subject_and_measure_window(self):
        from researchhelper.draft_sources import stated_window_requests
        from researchhelper.market_series import stated_nav_intervals

        task = self.engine.create('核对2026年8月3日至7日510300.SH的单位净值，'
                                  '以及2026年8月10日至12日600519.SH的收盘价。', research_mode='current')
        self.engine._apply_draft_policies(task)
        quote_intervals = calendar.stated_close_intervals(task, explicit=True)
        nav_intervals = stated_nav_intervals(task)
        self.assertEqual(set(quote_intervals), {'600519.SH'})
        self.assertEqual(set(nav_intervals), {'510300.SH'})
        quote, = quote_intervals['600519.SH']
        nav, = nav_intervals['510300.SH']
        self.assertEqual(tuple(map(str, quote[:2])), ('2026-08-10', '2026-08-12'))
        self.assertEqual(tuple(map(str, nav[:2])), ('2026-08-03', '2026-08-07'))
        requested = {(p['operation'], p['parameters']['codes'],
                      p['parameters']['startdate'], p['parameters']['enddate'])
                     for p in stated_window_requests(task)}
        self.assertEqual(requested, {
            ('cmd_history_quotation', '600519.SH', '2026-08-10', '2026-08-12'),
            ('date_sequence', '510300.SH', '2026-08-03', '2026-08-07'),
        })
        previous = dict(task, **{calendar.QUOTE_ENDPOINT_POLICY: 10})
        prior, = stated_nav_intervals(previous)['510300.SH']
        self.assertEqual(tuple(map(str, prior[:2])), ('2026-08-10', '2026-08-12'))

    def test_date_before_adjacent_code_resolves_two_independent_readouts(self):
        task = self.engine.create('核对2026年8月7日510300.SH的单位净值、'
                                  '2026年8月10日600519.SH的收盘价。', research_mode='current')
        self.engine._apply_draft_policies(task)
        points = {(code, str(day), field) for code, day, field, _ in calendar.stated_market_points(task)}
        self.assertEqual(points, {('510300.SH', '2026-08-07', 'unit_nav'),
                                  ('600519.SH', '2026-08-10', 'close')})
        self.assertTrue(calendar.stated_direct_market_day(task))

    def test_short_market_date_lists_share_explicit_calendar_context(self):
        cases = (
            ('在2026年8月3日、5日和7日的收盘价与当日涨跌幅',
             {'2026-08-03', '2026-08-05', '2026-08-07'}, True),
            ('在2026年8月31日，以及9月2日和3日的收盘价',
             {'2026-08-31', '2026-09-02', '2026-09-03'}, False),
            ('在2026年8月3日、10日均线的收盘价',
             {'2026-08-03'}, False),
            ('的2026年8月3日收盘价和2026年8月7日',
             {'2026-08-03', '2026-08-07'}, False),
        )
        for tail, expected, with_return in cases:
            with self.subTest(tail=tail):
                task = self.engine.create('核对600519.SH'+tail+'。', research_mode='current')
                self.engine._apply_draft_policies(task)
                actual = {str(day) for _, day, field, _ in calendar.stated_market_points(task) if field=='close'}
                self.assertEqual(actual, expected)
                returns = {str(day) for _, day, _ in calendar.stated_close_returns(task)}
                self.assertEqual(returns, expected if with_return else set())

    def test_daily_periods_keep_complete_spans_and_validate_both_endpoints(self):
        from researchhelper.numeric_integrity import _draft_periods

        cases = {
            '2026年8月3-7日': ('2026-08-03','2026-08-07'),
            '2026年8月31号到9月2号': ('2026-08-31','2026-09-02'),
            '2026/08/31～09/02': ('2026-08-31','2026-09-02'),
            '2026年12月30日至2027年1月5日': ('2026-12-30','2027-01-05'),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                text = '核对'+value+'的收盘价'
                start,end,first,last = calendar.stated_day_intervals(text)[0]
                self.assertEqual(text[start:end], value)
                self.assertEqual((first.isoformat(), last.isoformat()), expected)
                begin,end,period = _draft_periods(text)[0]
                self.assertEqual(text[begin:end], value)
                self.assertEqual(period.start, first.strftime('%Y%m%d'))
                self.assertEqual(period.year+period.end, last.strftime('%Y%m%d'))
        for text in ('2026年2月28至29日', '2026年12月30日至1月5日',
                     '2026-08-31至09-31', '2026-08-03至08-10%'):
            with self.subTest(text=text):
                self.assertEqual(calendar.stated_day_intervals(text), [])

    def test_mixed_close_and_nav_share_a_date_range_without_sharing_metric_bindings(self):
        from researchhelper.draft_sources import stated_window_requests
        from researchhelper.market_series import stated_nav_intervals

        cases = (
            ('贵州茅台600519.SH与沪深300ETF510300.SH在2026年8月3至7日分别核对收盘价和单位净值。',
             '600519.SH', '510300.SH'),
            ('核对2026年8月3至7日600519.SH的收盘价与510300.SH的单位净值。', '600519.SH', '510300.SH'),
            ('510300.SH与600519.SH在2026年8月3至7日分别核对单位净值和收盘价。', '600519.SH', '510300.SH'),
            ('核对510300.SH在2026-08-03至08-07的收盘价和单位净值。', '510300.SH', '510300.SH'),
        )
        for question, close, nav in cases:
            with self.subTest(question=question):
                task = self.engine.create(question, research_mode='current')
                self.engine._apply_draft_policies(task)
                self.assertEqual(list(calendar.stated_close_intervals(task, explicit=True)), [close])
                self.assertEqual(list(stated_nav_intervals(task)), [nav])
                plans = stated_window_requests(task)
                self.assertEqual({(item['operation'], item['parameters']['codes']) for item in plans},
                                 {('cmd_history_quotation',close), ('date_sequence',nav)})
                self.assertTrue(all((item['parameters']['startdate'],item['parameters']['enddate']) ==
                                    ('2026-08-03','2026-08-07') for item in plans))

    def test_mixed_ranges_keep_qualifiers_and_do_not_guess_missing_bindings(self):
        from researchhelper.market_series import stated_nav_intervals

        for question, close, nav in (
            ('核对510300.SH在2026年8月3至7日的后复权收盘价和单位净值。', [], ['510300.SH']),
            ('600519.SH与510300.SH在2026年8月3至7日分别核对收盘价和单位净值。不要单位净值。',
             ['600519.SH'], []),
            ('510300.SH与600519.SH在2026年8月3至7日分别核对收盘价和单位净值。', ['510300.SH'], []),
            ('600519.SH与510300.SH在2026年8月3至7日核对收盘价和单位净值。', [], []),
            ('600519.SH或510300.SH在2026年8月3至7日分别核对收盘价和单位净值。', [], []),
        ):
            with self.subTest(question=question):
                task = self.engine.create(question, research_mode='current')
                self.engine._apply_draft_policies(task)
                self.assertEqual(list(calendar.stated_close_intervals(task)), close)
                self.assertEqual(list(stated_nav_intervals(task)), nav)

    def test_stated_quote_scope_excludes_nav_and_other_explicit_metrics(self):
        for text in ('计算510300.SH在2026-08-03至2026-08-10的单位净值涨跌幅。',
                     '核对510300.SH的净值在2026年8月3日至10日的区间涨跌幅。',
                     '比较600519.SH在2026-08-03至2026-08-10的市盈率涨跌幅。'):
            with self.subTest(question=text):
                task = self.engine.create(text, research_mode='current')
                self.engine._apply_draft_policies(task)
                self.assertEqual(calendar.stated_close_intervals(task), {})
        task = self.engine.create('比较510300.SH在2026-08-03至2026-08-04的单位净值涨跌幅，'
                                  '再核对2026-08-05至2026-08-10的收盘价。', research_mode='current')
        self.engine._apply_draft_policies(task)
        self.assertEqual([(a.isoformat(),b.isoformat()) for a,b,_ in calendar.stated_close_intervals(task)['510300.SH']],
                         [('2026-08-05','2026-08-10')])
        task[calendar.QUOTE_ENDPOINT_POLICY] = 3
        self.assertEqual(len(calendar.stated_close_intervals(task)['510300.SH']), 2)

    def test_single_date_binding_leaves_cutoffs_announcement_dates_and_comparisons_distinct(self):
        from researchhelper.draft_sources import stated_window_requests

        for question, dates in (
            ('截至2026-08-10，核对600519.SH在2026年8月5日的收盘价。',['2026-08-05']),
            ('2026-08-05公司公告后，核对600519.SH的最新收盘价。',[]),
            ('核对600519.SH在2026-08-05之后的收盘价。',[]),
            ('请看「2026-08-05」的写法，以及600519.SH的最新收盘价。',[]),
            ('核对600519.SH在2026-08-05及2026-08-07的收盘价。',['2026-08-05','2026-08-07']),
            ('核对600519.SH在2099-12-31的收盘价。',['2099-12-31']),
        ):
            with self.subTest(question=question):
                task = self.engine.create(question,research_mode='current')
                self.engine._apply_draft_policies(task)
                slots = calendar.stated_close_intervals(task).get('600519.SH', ())
                self.assertEqual([(a.isoformat(),b.isoformat()) for a,b,_ in sorted(slots)],[(day,day) for day in dates])
                if '2099' in question:
                    self.assertFalse(stated_window_requests(task))
                task[calendar.QUOTE_ENDPOINT_POLICY] = 7
                self.assertFalse(calendar.stated_close_intervals(task))

    def test_gap_between_queries_keeps_the_endpoint_return_and_discloses_missing_path(self):
        from researchhelper.draft_analysis import analyze
        from researchhelper.draft_sources import acquisition_coverage, observations
        from researchhelper.draft_visuals import planned_figures

        days = self.days

        def quote(current, dates, prices):
            params = dict(self.price['parameters'],startdate=dates[0],enddate=dates[-1])
            return append_calendar_fixture_record(current,'cmd_history_quotation',params,
                dict(errorcode=0,tables=[dict(thscode='600519.SH',time=dates,table=dict(close=prices))]))

        for gap in (days[2:3], []):
            with self.subTest(gap=gap):
                current = self.engine.create(f'计算600519.SH在{days[0]}至{days[-1]}的区间收盘涨跌幅，并核对逐日收盘价。',
                                             research_mode='current')
                self.engine._apply_draft_policies(current)
                current['input_hash'] = input_fingerprint(current)
                quote(current, days[:2], [100,105])
                quote(current, days[3:], [115,120,117])
                append_calendar_fixture_record(current,'get_trade_dates',self.params,
                                               dict(errorcode=0,tables=dict(time=days)))
                if not gap:
                    supplement = quote(current, days[2:3], [110])
                    supplement['question_ids'] = ['other']
                    self.assertEqual(acquisition_coverage(current)['stated_close_windows'][0]['missing_session_dates'], days[2:3])
                    supplement['question_ids'] = ['q1']
                facts = observations(current)
                result = analyze(current, facts)
                change, = [row for row in result['results'] if row['method']=='raw_change_v1'
                           and not row.get('comparison_kind')]
                self.assertEqual(change['display'], '17.00')
                self.assertNotIn('quote_interval_gaps', result)
                scope, = acquisition_coverage(current)['stated_close_windows']
                self.assertEqual(scope['unqueried_session_dates'], gap)
                self.assertEqual(scope['missing_session_dates'], gap)
                self.engine.archive.save(current)
                done = self.engine.start(current['id'], dict(data_requests=[], draft_report=dict(title='区间收盘复核',
                    conclusion='按已取得的首末收盘价计算。', logics=[dict(title='区间收盘变化',
                    analysis='对照两个端点。',calculation_refs=[change['id']],data_refs=[row['id'] for row in facts])])))
                self.assertTrue(done.get('delivery'), done.get('error'))
                content = self.engine.export(current['id'])['content']
                self.assertEqual(content['draft']['reader_notes'],
                                 [f'600519.SH在{days[2]}的收盘价尚未取得。'] if gap else [])
                self.assertEqual(sorted(row['period'] for row in content['draft']['facts']),
                                 [day for day in days if day not in gap])
                close = [row for row in planned_figures(content) if row['field']=='close']
                if gap:
                    self.assertEqual(close, [])
                else:
                    chart, = close
                    self.assertEqual(chart['auto_visual']['scheme'], 'line')
                    self.assertEqual(len(chart['data_refs']), len(days))

    def test_sampled_close_is_labeled_by_its_actual_days_without_a_calendar(self):
        from researchhelper.draft_analysis import analyze
        from researchhelper.draft_sources import observations

        task = copy.deepcopy(self.task)
        self.engine._apply_draft_policies(task)
        task['input_hash'] = input_fingerprint(task)
        task['source_records'] = []
        days = self.days
        append_calendar_fixture_record(task, 'cmd_history_quotation', self.price['parameters'],
            dict(errorcode=0, tables=[dict(thscode='600519.SH', time=days[1:4], table=dict(close=[105,110,115]))]))
        result = analyze(task, observations(task))
        change, = [row for row in result['results'] if row['method']=='raw_change_v1']
        self.assertEqual(change['label'], '收盘价读数变化')
        self.assertEqual(change['period'], days[1]+'至'+days[3])
        self.assertEqual((change['window_start'],change['window_end']), (days[0],days[-1]))
        self.assertIn('交易日历未核对', change['condition'])
        self.assertNotIn('quote_interval_gaps', result)

    def test_draft_coverage_keeps_missing_days_and_uses_only_matching_supplements(self):
        from researchhelper.draft_analysis import analyze
        from researchhelper.draft_sources import acquisition_coverage, observations
        from researchhelper.drafts import normalize_report

        task = copy.deepcopy(self.task)
        self.engine._apply_draft_policies(task)
        task['input_hash'] = input_fingerprint(task)
        task['source_records'] = []
        need = self.price_need()
        need['parameters']['indicators'] = 'close,open'
        returned_days = self.days[:2] + self.days[3:]
        first = append_calendar_fixture_record(task, 'cmd_history_quotation', need['parameters'],
            dict(errorcode=0, tables=[dict(thscode='600519.SH', time=returned_days,
                                           table=dict(close=[100,105,115,120,117], open=[99,None,114,119,116]))]))
        first['data_need_ids'] = [need['id']]
        binding = dict(task_id=task['id'], research_version=task['version'], input_hash=task['input_hash'])
        task['draft_source_attempts'] = [dict(binding=binding, status='已提交', encoded=need)]

        # With no independent calendar, the response does not establish a gap in exchange dates.
        self.assertEqual(acquisition_coverage(task)['unresolved'], [])
        verified = append_calendar_fixture_record(task, 'get_trade_dates', self.params,
                                                   dict(errorcode=0, tables=dict(time=self.days)))

        def current():
            facts = observations(task)
            task['draft_analysis'] = analyze(task, facts)
            coverage = acquisition_coverage(task, facts=facts)
            report = normalize_report(None, task, facts)
            return coverage, ' '.join(report['reader_notes']), report.get('incomplete_quote_series', [])

        coverage, notes, incomplete = current()
        unresolved, = coverage['unresolved']
        self.assertEqual({row['field']:row['missing_dates'] for row in unresolved['missing']},
                         {'close':[self.days[2]], 'open':self.days[1:3]})
        self.assertTrue(coverage['needs_followup'])
        self.assertFalse(coverage['blocks_report'])
        self.assertIn('收盘价', notes)
        self.assertIn(self.days[2], notes)
        self.assertNotIn(self.days[-1], notes)
        self.assertEqual({row['field'] for row in incomplete}, {'open','close'})
        from researchhelper.drafts import draft_figures
        self.assertTrue(all(figure['type'] == 'bar' for figure in draft_figures(observations(task),
            incomplete_quote_series={(row['code'], row['field']) for row in incomplete})))

        conflict = append_calendar_fixture_record(task, 'get_trade_dates', self.params,
            dict(errorcode=0, tables=dict(time=returned_days)))
        self.assertEqual(current()[0]['unresolved'], [])
        task['source_records'].remove(conflict)
        self.assertIn(verified, task['source_records'])

        params = dict(need['parameters'], indicators='close', startdate=self.days[2], enddate=self.days[2])
        second = append_calendar_fixture_record(task, 'cmd_history_quotation', params,
            dict(errorcode=0, tables=[dict(thscode='600519.SH', time=[self.days[2]], table=dict(close=[110]))]))
        second['question_ids'] = ['other']
        self.assertEqual({row['field'] for row in current()[0]['unresolved'][0]['missing']}, {'open','close'})
        second['question_ids'] = ['q1']
        coverage, notes, incomplete = current()
        self.assertEqual([row['field'] for row in coverage['unresolved'][0]['missing']], ['open'])
        self.assertNotIn('收盘价', notes)
        params = dict(params, indicators='open', startdate=self.days[1])
        append_calendar_fixture_record(task, 'cmd_history_quotation', params,
            dict(errorcode=0, tables=[dict(thscode='600519.SH', time=self.days[1:3], table=dict(open=[104,109]))]))
        coverage, notes, incomplete = current()
        self.assertEqual(coverage['unresolved'], [])
        self.assertFalse(coverage['needs_followup'])
        self.assertEqual(notes, '')
        self.assertEqual(incomplete, [])

    def test_latest_quote_lookback_does_not_require_every_session(self):
        from researchhelper.draft_sources import acquisition_coverage
        from researchhelper.market_series import compile_observation_window, observation_selection

        task = copy.deepcopy(self.task)
        self.engine._apply_draft_policies(task)
        task['cutoff'] = self.days[-1] + 'T21:00:00+08:00'
        task['input_hash'] = input_fingerprint(task)
        task['source_records'] = []
        need = self.price_need()
        need['parameters'].pop('startdate')
        need['parameters'].pop('enddate')
        need['parameters']['observation_window'] = dict(mode='latest_available', as_of=self.days[-1], lookback_calendar_days=14)
        need = compile_observation_window(need, observation_selection(task))
        params = need['parameters']
        record = append_calendar_fixture_record(task, 'cmd_history_quotation', params,
            dict(errorcode=0, tables=[dict(thscode='600519.SH', time=[self.days[-1]], table=dict(close=[117]))]))
        record['data_need_ids'] = [need['id']]
        append_calendar_fixture_record(task, 'get_trade_dates',
            dict(self.params, startdate=params['startdate'], enddate=params['enddate']),
            dict(errorcode=0, tables=dict(time=self.days)))
        self.assertEqual(acquisition_coverage(task, [need])['complete_request_ids'], [need['id']])

    def test_historical_transport_binds_calendar_without_approving_point_in_time_data(self):
        from researchhelper.draft_analysis import analyze
        from researchhelper.draft_sources import observations

        history = self.engine.create('核对600519.SH跨周末收盘变化', objects=['600519.SH'],
            cutoff=self.days[-1]+'T22:00:00+08:00', research_mode='historical')
        calendar_payload = dict(errorcode=0, tables=dict(time=self.days))
        replies = {'get_trade_dates': calendar_payload, 'cmd_history_quotation': self.price['snapshot']}

        def wire(task, request, timeout, event):
            self.assertIs(task, history)
            operation = request.full_url.rsplit('/', 1)[-1]
            event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True,
                         synthetic_test_transport=True)
            return copy.deepcopy(replies[operation])

        collector = EvidenceCollector(offline=False)
        with patch('socket.socket.connect', side_effect=AssertionError('Offline test only')), \
                patch.object(collector, '_http_json', side_effect=wire) as transport:
            for operation, params in [('cmd_history_quotation', self.price['parameters']), ('get_trade_dates', self.params)]:
                record = collector.external(history, 'ifind', operation, params, dict(access_token='synthetic-calendar'))
                history['source_records'].append(record)
            with self.assertRaises(ContractError):
                collector.external(history, 'ifind', 'get_trade_dates', dict(self.params, enddate='2026-08-11'),
                                   dict(access_token='synthetic-calendar'))
        self.assertEqual(transport.call_count, 2)
        quote, record = history['source_records']
        self.assertEqual(record['snapshot'], calendar_payload)
        self.assertIsNotNone(calendar_query_reference(history, record))
        self.assertIsNone(calendar_query_reference(history, quote))
        for source in history['source_records']:
            self.assertIsNone(current_snapshot_reference(history, source))
            self.assertIsNone(current_query_reference(history, source))
        self.assertEqual(calendar.scope_basis(history)['panels'], [])
        self.assertNotIn(record['id'], [view['id'] for view in extractable_source_views(history)])
        daily, = [row for row in analyze(history, observations(history))['results']
                  if row.get('comparison_kind') == 'previous_session']
        self.assertEqual((daily['window_start'], daily['window_end'], daily['display']),
                         ('2026-08-07', '2026-08-10', '-2.50'))
        self.assertEqual(daily['session_basis']['source_refs'], [record['id']])
        self.assertFalse(daily['session_basis']['historical_availability_approved'])
        self.assertFalse(daily['financial_approval'])

        for problem in ('unbound', 'changed_response', 'wrong_operation', 'foreign_task', 'unreaped', 'later_version'):
            with self.subTest(problem=problem):
                modified = copy.deepcopy(history)
                source = modified['source_records'][-1]
                event = modified['ledger']['dispatches'][source['acquisition']['dispatch_index']]
                if problem == 'unbound':
                    source.pop('acquisition')
                elif problem == 'changed_response':
                    source['snapshot']['tables']['time'].pop(0)
                    source['hash'] = fingerprint(source['snapshot'])
                elif problem == 'wrong_operation':
                    event['operation'] = 'cmd_history_quotation'
                elif problem == 'foreign_task':
                    source['acquisition']['task_id'] = 'other-task'
                elif problem == 'unreaped':
                    event['network_worker_reaped'] = False
                else:
                    modified['version'] += 1
                self.assertEqual(calendar.retained_calendars(modified), [])

    def test_latest_selection_keeps_cross_weekend_previous_close_only_with_calendar(self):
        from researchhelper.draft_analysis import analyze
        from researchhelper.draft_sources import observations
        from researchhelper.evidence import record_request_purpose
        from researchhelper.market_series import compile_observation_window, observation_selection

        ResearchAgent._apply_draft_policies(self.task)
        self.task['cutoff'] = '2026-08-10T22:00:00+08:00'
        self.task['input_hash'] = input_fingerprint(self.task)
        need = self.price_need()
        params = need['parameters']
        params.pop('startdate')
        params.pop('enddate')
        params['observation_window'] = dict(mode='latest_available', as_of='2026-08-10', lookback_calendar_days=14)
        need = compile_observation_window(need, observation_selection(self.task))
        self.task['source_records'].clear()
        quote = self.append_record(need['operation'], need['parameters'], copy.deepcopy(self.price['snapshot']))
        quote['data_need_ids'] = [need['id']]
        record_request_purpose(quote, need, ['q1'])
        self.assertEqual([row['value'] for row in observations(self.task)], [117])
        calendar_record = self.append_calendar()
        facts = observations(self.task)
        self.assertEqual([row['value'] for row in facts], [117, 120])
        change, = analyze(self.task, facts)['results']
        self.assertEqual((change['display'], change['window_start'], change['window_end']),
                         ('-2.50', '2026-08-07', '2026-08-10'))
        self.assertEqual(change['session_basis']['source_refs'], [calendar_record['id']])
        self.append_calendar(['2026-08-07', '2026-08-09'],
            dict(self.params, startdate='2026-08-07', enddate='2026-08-09'))
        self.assertEqual([row['value'] for row in observations(self.task)], [117])
        self.assertEqual(analyze(self.task, observations(self.task))['results'], [])

    def test_missing_price_is_reported_without_guessing_suspension(self):
        payload = copy.deepcopy(self.price['snapshot'])
        missing = payload['tables'][0]['time'].pop(5)
        payload['tables'][0]['table']['close'].pop(5)
        self.task['source_records'] = [item for item in self.task['source_records'] if item['id'] != self.price['id']]
        self.append_record('cmd_history_quotation', self.price['parameters'], payload)
        self.append_calendar()
        panel = calendar.coverage(self.task)['panels'][0]
        self.assertEqual(panel['missing_dates'], [missing])
        self.assertEqual(panel['status'], '存在未观测交易日')
        self.assertNotIn('停牌', panel['status'])

    def test_calendar_coverage_uses_only_the_bound_close_cells(self):
        from researchhelper.draft_sources import observations
        from researchhelper.evidence import api_cell_choice_coverage, api_cell_choices

        ResearchAgent._apply_draft_policies(self.task)
        self.task['input_hash'] = input_fingerprint(self.task)
        self.task['source_records'].clear()
        payload = copy.deepcopy(self.price['snapshot'])
        payload['tables'].extend([
            dict(thscode='600519.SH',time=[self.days[0]],table=dict(close=[1234])),
            dict(thscode='600519.SH',time=[self.days[1]],table=dict(thscode=['600036.SH'],close=[5678])),
        ])
        quote = self.append_record('cmd_history_quotation', self.price['parameters'], payload)
        self.append_calendar()
        panel, = calendar.coverage(self.task)['panels']
        self.assertEqual(panel['observed_dates'], self.days[1:])
        self.assertEqual(panel['missing_dates'], self.days[:1])
        self.assertEqual(panel['status'], '存在未观测交易日')
        self.assertEqual([row['period'] for row in observations(self.task)], self.days[1:])
        choices = api_cell_choices(self.task, quote)
        self.assertEqual([row['period'] for row in choices], self.days[1:])
        coverage = api_cell_choice_coverage(quote, choices, task=self.task)
        self.assertTrue(coverage['selection_complete'])
        self.assertEqual(coverage['invalid_positions'], 3)

    def test_latest_refresh_with_a_later_blank_keeps_the_exact_session_pair(self):
        from researchhelper.contracts import budget
        from researchhelper.draft_analysis import analyze
        from researchhelper.draft_sources import observations, retained_market_refreshes
        from researchhelper.market_series import compile_observation_window, observation_selection, record_observation_scope

        def quote(task, days, values, latest):
            args = dict(self.price['parameters'], startdate=days[0], enddate=days[-1])
            request = dict(id='latest', source='ifind', operation='cmd_history_quotation', parameters=args)
            if latest:
                selector = observation_selection(task)
                args.pop('startdate')
                args.pop('enddate')
                args['observation_window'] = dict(mode='latest_available', as_of=selector['as_of'], lookback_calendar_days=14)
                request = compile_observation_window(request, selector)
            record = append_calendar_fixture_record(task, request['operation'], request['parameters'],
                dict(errorcode=0, tables=[dict(thscode='600519.SH', time=days, table=dict(close=values))]))
            record['data_need_ids'] = ['latest']
            record_observation_scope(record, request, ['q1'])
            return record

        days = self.days[-3:]
        for previous_visible in (False, True):
            with self.subTest(previous_visible=previous_visible):
                old = copy.deepcopy(self.task)
                old.update(cutoff=days[-1]+'T22:00:00+08:00', source_records=[], ledger=budget(old['ledger']['limits']))
                ResearchAgent._apply_draft_policies(old)
                quote(old, days, [98, 102, 110], not previous_visible)
                updated = copy.deepcopy(old)
                updated.update(version=2, cutoff='2026-08-11T22:00:00+08:00', ledger=budget(old['ledger']['limits']),
                    update_from=dict(version=1, market_refreshes=retained_market_refreshes(old, old)))
                updated['source_records'][0].update(research_version=2, retained_from_version=1)
                current = quote(updated, days, [100, 105, None], True)
                calendar_params = dict(self.params, enddate='2026-08-11')
                append_calendar_fixture_record(updated, 'get_trade_dates', calendar_params,
                    dict(errorcode=0, tables=dict(time=[*self.days, '2026-08-11'])))
                updated['input_hash'] = input_fingerprint(updated)
                facts = observations(updated)
                by_id = {row['id']:row for row in facts}
                self.assertTrue(all(row['artifact_id'] == current['id'] for row in facts))
                self.assertEqual(next(row['raw_value'] for row in facts if row['period'] == days[-1]), None)
                pair, = [result for result in analyze(updated, facts)['results'] if result.get('comparison_kind') == 'previous_session']
                self.assertEqual((pair['window_start'], pair['window_end'], pair['display']), (days[0], days[1], '5.00'))
                self.assertEqual([by_id[ref]['raw_value'] for ref in pair['data_refs']], [100, 105])
                self.assertEqual(bool(by_id[pair['data_refs'][0]].get('previous_close_for')), not previous_visible)

    def test_null_price_is_not_covered_or_filled(self):
        payload = copy.deepcopy(self.price['snapshot']); payload['tables'][0]['table']['close'][3] = None
        self.task['source_records'] = [item for item in self.task['source_records'] if item['id'] != self.price['id']]
        source = self.append_record('cmd_history_quotation', self.price['parameters'], payload)
        self.append_calendar()
        self.assertEqual(calendar.coverage(self.task)['panels'][0]['missing_dates'], [self.days[3]])
        self.assertIsNone(source['snapshot']['tables'][0]['table']['close'][3])

    def test_changed_cross_task_or_unreaped_calendar_is_not_reused(self):
        source = self.append_calendar()
        for change in ('hash', 'task', 'unreaped'):
            task = copy.deepcopy(self.task)
            record = next(item for item in task['source_records'] if item['id'] == source['id'])
            if change == 'hash': record['hash'] = 'changed'
            elif change == 'task': record['acquisition']['task_id'] = 'foreign_task'
            else: task['ledger']['dispatches'][record['acquisition']['dispatch_index']]['network_worker_reaped'] = False
            self.assertEqual(calendar.coverage(task)['panels'][0]['status'], '日历未核验')

    def test_empty_date_column_differs_from_absent_column(self):
        self.assertTrue(calendar.sessions(self.params, dict(errorcode=0, tables=dict(time=[])))['explicit_empty'])
        for tables in ([], {}, dict(table={})):
            with self.assertRaises(ContractError): calendar.sessions(self.params, dict(errorcode=0, tables=tables))

    def test_wrong_market_dates_order_duplicates_and_coercion_are_rejected(self):
        invalid = [list(reversed(self.days)), self.days + [self.days[-1]], ['2026-08-03T00:00:00'],
            ['2026/08/03'], [' 2026-08-03'], ['2026-09-01'], [None], [True]]
        for days in invalid:
            with self.subTest(days=days[:2]), self.assertRaises(ContractError):
                calendar.sessions(self.params, dict(errorcode=0, tables=dict(time=days)))
        with self.assertRaises(ContractError):
            calendar.sessions(self.params, dict(errorcode=0, tables=dict(time=self.days), inputParams=dict(marketcode='212100')))

    def test_conflicting_date_columns_are_not_silently_selected(self):
        with self.assertRaises(ContractError):
            calendar.sessions(self.params, dict(errorcode=0,
                tables=dict(time=self.days, table=dict(sequencedate=self.days[:-1]))))

    def test_imported_snapshot_is_not_an_independent_acquisition(self):
        source = snapshot_record(self.task, 'ifind', 'get_trade_dates', self.params,
            dict(errorcode=0, tables=dict(time=self.days)))
        self.task['source_records'].append(source)
        self.assertIsNone(current_query_reference(self.task, source))
        self.assertEqual(calendar.coverage(self.task)['panels'][0]['status'], '日历未核验')

    def test_dependency_only_for_acquired_multi_day_prices_and_same_task_market(self):
        need = self.price_need()
        prepared = [(need, 'ifind', need['operation'], need['parameters'])]
        requests = calendar.dependency_requests(self.task, prepared)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['parameters'], self.params)
        self.assertEqual(requests[0]['question_ids'], ['q1'])
        self.assertEqual(calendar.dependency_requests(self.task, []), [])
        self.assertEqual(calendar.dependency_requests(self.task,
            [(dict(need), 'ifind', 'date_sequence', need['parameters'])]), [])
        parameters = dict(need['parameters'], enddate=need['parameters']['startdate'])
        self.assertEqual(calendar.dependency_requests(self.task, [(need, 'ifind', need['operation'], parameters)]), [])
        self.append_calendar()
        self.assertEqual(calendar.dependency_requests(self.task, prepared), [])

    def test_scope_is_checked_before_credentials_or_request_charge(self):
        before = copy.deepcopy(self.task['ledger'])
        collector = EvidenceCollector(offline=False)
        with self.assertRaises(ContractError):
            collector.external(self.task, 'ifind', 'get_trade_dates', dict(self.params, marketcode='212100'), {})
        self.assertEqual(self.task['ledger'], before)

    def test_calendar_is_public_tool_but_not_numeric_extraction(self):
        source = self.append_calendar()
        tool = next(tool for tool in data_request_contract(self.task)['tools'] if tool['operation'] == 'get_trade_dates')
        self.assertTrue(tool['raw_only']); self.assertFalse(tool['numeric_fields'])
        self.assertNotIn(source['id'], [view['id'] for view in extractable_source_views(self.task)])
        self.assertIn('get_trade_dates', json.dumps(planning_output_schema(self.task)))

    def test_coverage_is_bound_to_analysis_and_review_but_not_legacy(self):
        before = analysis_contract(self.task)
        self.append_calendar()
        after = analysis_contract(self.task)
        self.assertNotEqual(before['execution']['market_coverage_hash'], after['execution']['market_coverage_hash'])
        self.assertEqual(after['market_coverage']['panels'][0]['status'], '日历覆盖一致')
        request = review_request(self.task, self.task['host_result'])
        self.assertEqual(request['scope']['market_coverage'], after['market_coverage'])
        legacy = dict(self.task); legacy.pop(calendar.POLICY)
        legacy['input_hash'] = input_fingerprint(legacy)
        self.assertIsNone(calendar.coverage(legacy))
        self.assertNotIn('market_coverage', analysis_contract(legacy))

    def test_optional_automatic_failure_does_not_invent_or_block_endpoint_evidence(self):
        agent = ResearchAgent.__new__(ResearchAgent)
        agent.source_credentials = {}; agent.collector = SimpleNamespace(external=Mock(side_effect=ContractError('日历权限未通过')))
        agent._active = Mock(); agent._save_model_checkpoint = Mock(); agent._journal = Mock()
        need = calendar.dependency_requests(self.task, [(self.price_need(), 'ifind',
            'cmd_history_quotation', self.price['parameters'])])[0]
        before = len(self.task['gaps'])
        agent._acquire_sources(self.task, [need], metadata_dependency=True)
        self.assertEqual(len(self.task['gaps']), before)
        self.assertTrue(self.task['calendar_dependency_failures'])
        self.assertEqual(calendar.coverage(self.task)['panels'][0]['status'], '日历未核验')
        agent._acquire_sources(self.task, [need], metadata_dependency=True)
        agent.collector.external.assert_called_once()  # Failed request is not silently retried.

    def test_new_policy_flags_are_frozen_without_upgrading_old_tasks(self):
        with tempfile.TemporaryDirectory(prefix='calendar-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            self.addCleanup(agent.close_runtime)
            task = agent.create('解释价格变化', questions=[dict(id='q1', kind='fact', question='解释价格变化',
                answer_scope='general_definition', knowledge_needs=['rh_k_price_return_measures'])])
            self.assertEqual(task[calendar.POLICY], 1)
            self.assertEqual(task['economic_identifier_namespace_version'], 2)
            legacy = dict(task); legacy.pop(calendar.POLICY)
            self.assertNotEqual(task['input_hash'], input_fingerprint(legacy))
            done = compose(agent.start(task['id']))
            self.assertEqual(done['market_coverage']['panels'], [])
            self.assertEqual(done[calendar.POLICY], 1)
            self.assertNotIn(calendar.POLICY, agent.create_case('mechanism'))


if __name__ == '__main__':
    unittest.main()
