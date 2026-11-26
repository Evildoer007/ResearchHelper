"""Exercise the ordinary draft path, including its no-model fallback."""
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from lxml import html

from core.session import Session
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.draft_readout import fomc_readout, policy_transmission_sections
from researchhelper.draft_sources import observations, retained_public_excerpts
from researchhelper.draft_workflow import direct_readout
from researchhelper.evidence import _record_current_acquisition, snapshot_record
from researchhelper.engine import ResearchAgent
from researchhelper.event_market import close_history_url, market_scope, parse_close_history
from researchhelper.model import ModelService, ModelSettings
from researchhelper.official_sources import (catalogue_url, direct_fomc_rate_request, fomc_rate_request,
                                             fomc_statement_release_clock, release_choices, research_fomc_rate_request)
from researchhelper.report_designer import design
from researchhelper.reports import render
from tests import ROOT


JULY = '/newsevents/pressreleases/monetary20260729a.htm'
SEPTEMBER = '/newsevents/pressreleases/monetary20260916a.htm'
NOVEMBER = '/newsevents/pressreleases/monetary20261104a.htm'
FOMC = '核对美联储2026年9月16日FOMC联邦基金目标利率区间，比较前次声明的变化。'
FOMC_RESEARCH = FOMC + '再分析对港股科技估值的影响，并参考2026年9月17日和2026年9月18日的行情。'
INDUSTRY = '核对证券行业2026年半年报营业收入及归母净利润的样本覆盖和完整名单。'
REVENUE = '营业收入[20260630]'
PROFIT = '归属于母公司所有者的净利润[20260630]'


@pytest.fixture
def session(tmp_path):
    with patch('socket.socket.connect', side_effect=AssertionError('No external data or model')):
        app = Session(tmp_path)
        try:
            yield app
        finally:
            app.agent.close_runtime()


def statement(day, decision):
    return (f'<html><head><title>Federal Reserve issues FOMC statement</title></head>'
            f'<body><p>{day}</p><p>{decision}</p><p>Last Update: {day}</p></body></html>').encode()


def test_statement_clock_uses_the_dated_header_and_matching_eastern_offset():
    record = dict(source='official', operation='release_original', snapshot={'official_origin': {
        'url': 'https://www.federalreserve.gov'+SEPTEMBER}})
    parts = [dict(locator='段落1', text='September 16, 2026'),
             dict(locator='段落2', text='For release at 2:00 p.m. EDT'),
             dict(locator='段落3', text='The Committee decided to raise the target range')]
    decision = dict(date='2026-09-16', part_index=2)
    assert fomc_statement_release_clock(record, decision, parts)['scheduled_at'] == '2026-09-16T14:00:00-04:00'
    parts[1]['text'] = 'For release at 2:00 p.m. EST'
    assert fomc_statement_release_clock(record, decision, parts) is None


def test_policy_question_gets_both_aligned_index_closes_and_draws_no_causal_conclusion(tmp_path):
    question = ('研究2026年9月16日美联储议息会议对港股科技估值的影响，'
                '核对政策并比较同一交易窗口的市场表现。')
    history = [
        dict(indexCode=code, indexName=name, valueHistory=[dict(date=day, close=value)
             for day, value in zip(('2026-09-16', '2026-09-17', '2026-09-18'), values, strict=True)])
        for code, name, values in [('02083.00', 'Hang Seng TECH Index', ('4325.45', '4310.74', '4405.50')),
                                   ('00001.00', 'Hang Seng Index', ('24713.78', '24604.29', '24750.78'))]
    ]
    payload = json.dumps(dict(code='0', data=history))
    fetched = []

    def confirm_send(event):
        event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)

    def fed(task, url, started, timeout, event, **options):
        confirm_send(event)
        output = fomc_transport()(task, url)
        if url.endswith(SEPTEMBER):
            output = output.replace(b'</p><p>The Committee', b'</p><p>For release at 2:00 p.m. EDT</p><p>The Committee')
        return output

    def hong_kong(task, codes, started, timeout, event, **options):
        confirm_send(event)
        fetched.append((codes, market_scope(task)))
        return dict(url=close_history_url(codes), original_text=payload, original_sha256=sha256(payload.encode()).hexdigest())

    archive = ResearchArchive(tmp_path)
    agent = ResearchAgent(archive, ROOT, offline=False, draft_first=True, require_argument_support=True,
                          model=ModelService(ModelSettings(tmp_path), offline=False))
    try:
        with (patch('researchhelper.source_transport.official_html', side_effect=fed),
              patch('researchhelper.source_transport.official_hsi_history', side_effect=hong_kong),
              patch('socket.socket.connect', side_effect=AssertionError('No external sources in unit test'))):
            with patch('researchhelper.engine.now', return_value='2026-09-19T05:10:00+08:00'):
                task = agent.create(question, research_mode='current', entry='事件')
            final = agent.start(task['id'])
        assert final['delivery'] and final['ledger']['used']['model_calls'] == 0, final.get('error')
        assert fetched == [(['HSTECH.HI', 'HSI.HI'], dict(codes=['HSTECH.HI', 'HSI.HI'], session_day='2026-09-17',
                                                            start='2026-09-13', end='2026-09-18'))]
        report = archive.report(task['id'], task['version'])
        for page in (report['html'], render(report['content'], ROOT, report_profile='detailed')):
            visible = html.document_fromstring(page).find('.//main').text_content()
            assert '恒生科技指数下跌0.34%' in visible and '恒生指数下跌0.44%' in visible
            assert '0.10个百分点' in visible and '会前利率预期' in visible
        standard = html.document_fromstring(design(report['content'], ROOT, profile='standard')['html'])
        assert standard.xpath('//*[contains(@class,"paper-dateline")]//time/@datetime') == ['2026-09-17']
        assert not standard.get_element_by_id('logic-2').xpath('.//table')
        changed = deepcopy(history)
        changed[0]['indexName'] += ' Net Total Return'
        with pytest.raises(ContractError, match='指数身份不匹配'):
            parse_close_history(json.dumps(dict(code='0',data=changed)),
                                ['HSTECH.HI','HSI.HI'], '2026-09-16', '2026-09-17')
        changed_final = deepcopy(final)
        record = next(row for row in changed_final['source_records'] if row['operation']=='hsi_close_history')
        record['snapshot']['original_text'] = payload.replace('4310.74','5010.74')
        assert all(row['artifact_id'] != record['id'] for row in observations(changed_final))
    finally:
        agent.close_runtime()


def test_policy_study_does_not_present_unrelated_closes_as_event_observations(session):
    question = ('研究2026年9月16日美联储议息会议对港股科技估值的影响。'
                '对照513130.SH在2026年9月16日至2026年9月17日的收盘价变化。')
    task = session.create(question, research_mode='current', entry='事件')
    assert direct_readout(task, policy_without_model=True)[0] == 'policy_context'
    event = dict(links=[dict(document={'source_ref': 'official-ref'})], policy_direction='tightening',
                 release_clock=dict(scheduled_at='2026-09-16T14:00:00-04:00', part='段落2',
                                    quote='For release at 2:00 p.m. EDT'))
    rows = [dict(id=ref, field='close', code='513130.SH', object_label='港股科技ETF', unit='元', period=period)
            for ref, period in zip(('D1', 'D2'), ('2026-09-16', '2026-09-17'))]
    calcs = [dict(id='A1', method='raw_change_v1', data_refs=['D1', 'D2'], exact='2.4')]
    for first, last in (('2026-09-14', '2026-09-15'), ('2026-09-17', '2026-09-18'), ('2026-09-16', '2026-09-17')):
        left, right = (dict(row, period=day) for row, day in zip(rows, (first, last)))
        mechanism, observation = policy_transmission_sections(task, event, [left, right], calcs)
        assert '2026-09-17 02:00' in mechanism['analysis']
        assert ('2.4%' in observation['analysis']) == (first == '2026-09-16')
        assert '会前利率预期' in observation['risk']


def test_effective_event_session_uses_calendar_without_inventing_a_missing_close(session):
    from researchhelper.event_market import matched_sessions
    from researchhelper.market_calendar import FUNCTION, MARKETS
    from tests.test_market_calendar_coverage import append_calendar_fixture_record

    task = session.create('核对上次美联储议息后000300.SH首个交易日收盘表现。', research_mode='current')
    scope = dict(codes=['000300.SH'], start='2026-09-13', end='2026-09-18', session_day='2026-09-17')
    rows = [dict(code='000300.SH', field='close', unit='点', source='official', artifact_id='prices',
                 period=day, value=value, raw_value=str(value))
            for day, value in [('2026-09-16', 4200), ('2026-09-18', 4242)]]
    assert not matched_sessions(scope, rows, task=task)
    params = dict(marketcode=MARKETS['SH'], functionpara=FUNCTION,
                  startdate=scope['start'], enddate=scope['end'])
    original = dict(errorcode=0, tables=dict(time=['2026-09-16', '2026-09-18']))
    append_calendar_fixture_record(task, 'get_trade_dates', params, original)
    assert matched_sessions(scope, rows, task=task)['000300.SH'][:2] == ('2026-09-16', '2026-09-18')
    assert not matched_sessions(scope, rows[:1], task=task)


@pytest.mark.parametrize('missing_short_yield', (False, True))
def test_fomc_treasury_curve_reads_both_tenors_on_the_us_date_and_replays_exact_bps(tmp_path, missing_short_yield):
    from researchhelper.draft_readout import verified_event_market_answer
    from researchhelper.evidence import validate_source_request
    from researchhelper.treasury_rates import OPERATION, requested_series, source_url
    from researchhelper.draft_visuals import _calculation_dependencies, logic_figure_plan

    question = '美联储最近一次议息后，两年期和十年期美债收益率怎么走？结合期限利差写一份研究报告。'
    calls = []
    csv = ('observation_date,DGS2,DGS10\n2026-09-14,4.55,4.82\n2026-09-15,4.70,4.99\n'
           f'2026-09-16,{"" if missing_short_yield else "4.67"},5.05\n2026-09-17,9.99,9.99\n')

    def sent(event):
        event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)

    def fed(task, url, started, timeout, event, **options):
        sent(event)
        calls.append(url)
        return fomc_transport()(task, url).replace(
            b'</p><p>The Committee', b'</p><p>For release at 2:00 p.m. EDT</p><p>The Committee')

    def h15(task, parameters, started, timeout, event, **options):
        sent(event)
        calls.append(parameters)
        assert validate_source_request(task, 'official', OPERATION, parameters) == parameters
        with pytest.raises(ContractError):
            validate_source_request(task, 'official', OPERATION, dict(parameters, end='2026-09-17'))
        return dict(url=source_url(parameters), original_text=csv, original_sha256=sha256(csv.encode()).hexdigest())

    archive = ResearchArchive(tmp_path)
    agent = ResearchAgent(archive, ROOT, offline=False, draft_first=True, require_argument_support=True)
    try:
        with (patch('researchhelper.source_transport.official_html', side_effect=fed),
              patch('researchhelper.source_transport.official_h15_treasury_yields', side_effect=h15),
              patch('researchhelper.engine.now', return_value='2026-09-19T12:00:00+08:00'),
              patch('socket.socket.connect', side_effect=AssertionError('No external requests'))):
            task = agent.create(question, research_mode='current', entry='事件')
            assert requested_series(task) == ('DGS2', 'DGS10')
            assert direct_readout(task)[0] == 'policy_context'
            result = agent.start(task['id'])
        assert calls[-1] == dict(series=['DGS2', 'DGS10'], start='2026-09-12', end='2026-09-16')
        assert result.get('delivery') and result['ledger']['used']['model_calls'] == 0
        frozen = archive.report(task['id'], 1)['content']
        draft = result['draft_report']
        facts = [row for row in draft['facts'] if row['field'] == 'treasury_yield']
        assert {row['period'] for row in facts} == {'2026-09-15', '2026-09-16'}
        assert {row['unit'] for row in facts} == {'%'}
        deltas = {row['code']: float(row['exact']) for row in draft['calculations'] if row.get('comparison_kind') == 'treasury_event_move'}
        if missing_short_yield:
            assert result['status'] == '部分完成' and deltas == {'DGS10': 6.0}
            assert 'event_market_answer' not in result['questions'][0]
            return
        assert result['status'] == '已完成', result.get('gaps')
        answer = verified_event_market_answer(result, draft)
        assert result['questions'][0]['event_market_answer'] == answer
        assert answer['windows']['us_treasury'] == dict(codes=['DGS2', 'DGS10'], start='2026-09-15', end='2026-09-16')
        assert deltas == {'DGS2': -3.0, 'DGS10': 6.0}
        assert [float(row['exact']) for row in draft['calculations'] if row.get('comparison_kind') == 'treasury_term_spread'] == [29, 38]
        assert [float(row['exact']) for row in draft['calculations'] if row.get('comparison_kind') == 'treasury_curve_shift'] == [9]
        assert {calc['id'] for calc in draft['calculations']} <= _calculation_dependencies(frozen).keys()
        revised = deepcopy(frozen)
        next(row['snapshot'] for row in revised['source_records'] if row['operation'] == OPERATION)['original_text'] += '\n'
        assert not set(_calculation_dependencies(revised)) & {calc['id'] for calc in draft['calculations']}
        assert not logic_figure_plan(frozen)
        for profile in ('standard', 'detailed'):
            page = html.document_fromstring(design(frozen, ROOT, profile=profile)['html'])
            main = page.find('.//main').text_content()
            assert '9个基点' in main and '9.99%' not in main
            assert '10年期与2年期利差' in main or '10年期减2年期的利差' in main
            addresses = page.xpath('//footer[contains(@class,"report-source-line")]//a/@href')
            assert {"https://www.federalreserve.gov"+SEPTEMBER, source_url(calls[-1])} <= set(addresses)
    finally:
        agent.close_runtime()


@pytest.mark.parametrize('suffix', ('取30年期美债收益率。', '只用iFinD。', '看盘中行情。', '用实际收益率。'))
def test_unavailable_or_excluded_treasury_series_are_not_substituted(session, suffix):
    from researchhelper.treasury_rates import requested_series

    task = session.create('最近一次美联储议息后，美债收益率怎么走？'+suffix, research_mode='current')
    assert not requested_series(task)


@pytest.mark.parametrize(('missing_sector', 'missing_hk_day', 'hk_only'),
                         [(False, False, False), (True, False, False), (False, True, False), (False, False, True)])
def test_natural_mixed_policy_study_keeps_local_market_benchmarks(tmp_path, missing_sector, missing_hk_day, hk_only):
    from researchhelper.draft_readout import verified_event_market_answer
    from researchhelper.draft_workflow import _resolve_policy_market_gaps
    from researchhelper.event_market import (MAINLAND_OPERATION, ifind_parameters, mainland_endpoint, market_scopes,
                                             parse_mainland_history, requested_markets)
    from researchhelper.evidence import validate_source_request

    question = ('这次美联储议息后，港股科技和A股券商开市第一天收盘表现怎么样？'
                '分别跟恒生指数和沪深300指数比较收盘表现，写一份研究报告。')
    if hk_only:
        question = '这次美联储议息后，港股科技开市第一天收盘表现怎么样？对照恒生指数，写一份研究报告。'
    days = ['2026-09-16', '2026-09-17', '2026-09-18']
    hk_json = json.dumps(dict(code='0', data=[
        dict(indexCode=code, indexName=name, valueHistory=[dict(date=day, close=value)
             for day, value in zip(days, values, strict=True) if not (missing_hk_day and day == '2026-09-17')])
        for code, name, values in [('02083.00', 'Hang Seng TECH Index', ['4000', '3960', '3970']),
                                   ('00001.00', 'Hang Seng Index', ['20000', '19600', '19700'])]]))
    equity = {'000300.SH': dict(code='000300',kline=[[20260916,4200],[20260917,4242],[20260918,4300]]),
              '399975.SZ': dict(code='0',data=dict(code='399975',picupdata=[
                  [day,'100',close,'99','130','0','0','100','200']
                  for day,close in zip(days,('100','98','110'),strict=True)]))}
    calls=[]

    def sent(event):
        event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)

    def fed(task, url, started, timeout, event, **options):
        sent(event)
        payload=fomc_transport()(task,url)
        return payload.replace(b'</p><p>The Committee',b'</p><p>For release at 2:00 p.m. EDT</p><p>The Committee')

    def hong_kong(task, codes, started, timeout, event, **options):
        sent(event)
        return dict(url=close_history_url(codes),original_text=hk_json,original_sha256=sha256(hk_json.encode()).hexdigest())

    def mainland(task, parameters, started, timeout, event, **options):
        sent(event)
        code=parameters['ts_code']
        calls.append(code)
        if missing_sector and code=='399975.SZ':
            raise ContractError('验收样例缺少券商指数窗口')
        payload=json.dumps(equity[code])
        return dict(url=mainland_endpoint(code)[0],original_sha256=sha256(payload.encode()).hexdigest(),
                    original_text=payload,rows=parse_mainland_history(payload,**parameters))

    archive=ResearchArchive(tmp_path)
    agent=ResearchAgent(archive,ROOT,offline=False,draft_first=True,require_argument_support=True,
                        model=ModelService(ModelSettings(tmp_path),offline=False))
    agent.source_credentials={'ifind':{'access_token':'','refresh_token':''}}
    try:
        with (patch('researchhelper.source_transport.official_html',side_effect=fed),
              patch('researchhelper.source_transport.official_hsi_history',side_effect=hong_kong),
              patch('researchhelper.source_transport.official_mainland_index_history',side_effect=mainland),
              patch('socket.socket.connect',side_effect=AssertionError('验收不联网'))):
            with patch('researchhelper.engine.now',return_value='2026-09-19T05:10:00+08:00'):
                task=agent.create(question,research_mode='current')
            expected = dict(hong_kong=['HSTECH.HI','HSI.HI'])
            if not hk_only:
                expected['mainland'] = ['399975.SZ','000300.SH']
            assert requested_markets(task) == expected
            assert direct_readout(task,policy_without_model=True)[0]=='policy_context'
            final=agent.start(task['id'])
        assert final['ledger']['used']['model_calls']==0 and final['delivery'],final.get('error')
        assert calls == ([] if hk_only else ['399975.SZ','000300.SH'])
        assert not any(row.get('proposed',{}).get('source')=='ifind' for row in final['draft_source_attempts'])
        scopes=market_scopes(final)
        assert {scope['session_day'] for scope in scopes.values()}=={'2026-09-17'}
        page=html.document_fromstring(archive.report(final['id'],1)['html']).find('.//main').text_content()
        assert '会前利率预期' in page
        receipt=final['questions'][0].get('event_market_answer')
        close_windows = [(row['code'], row['window_end']) for row in final['draft_report']['calculations']
                         if row.get('comparison_kind') == 'event_close_return']
        assert all(day == '2026-09-17' for _, day in close_windows)
        if missing_hk_day:
            assert not receipt and final['status'] == '部分完成'
            assert '补充区间收盘表现' in page and '2026-09-16至2026-09-18收盘区间' in page
            assert '港股首个收盘窗口' not in page
            assert [('HSTECH.HI', '2026-09-18'), ('HSI.HI', '2026-09-18')] == [
                (row['code'], row['window_end']) for row in final['draft_report']['calculations']
                if row.get('comparison_kind') == 'event_observed_return']
            return
        assert all(term in page for term in ('恒生科技指数下跌1.00%','恒生指数下跌2.00%'))
        if hk_only:
            assert final['status'] == '已完成' and receipt == verified_event_market_answer(final, final['draft_report'])
            assert list(receipt['windows']) == ['hong_kong']
            return
        assert all(term in page for term in ('沪深300指数上涨1.00%', '沪深与香港收盘时刻不同'))
        if missing_sector:
            assert not receipt and final['status']=='部分完成'
            assert '中证全指证券公司指数缺少该窗口可配对的收盘读数' in page
            assert '中证全指证券公司指数较沪深300指数低3.00个百分点' not in page
        else:
            assert '中证全指证券公司指数较沪深300指数低3.00个百分点' in page.replace(' ','')
            spread = {row['code']: row['display'] for row in final['draft_report']['calculations']
                      if row.get('comparison_kind') == 'event_market_spread'}
            assert spread == {'HSTECH.HI': '1.00', '399975.SZ': '-3.00'}
            assert final['status']=='已完成' and receipt==verified_event_market_answer(final,final['draft_report'])
            assert receipt['windows']['mainland']['end']=='2026-09-17'
            changed=deepcopy(final['draft_report'])
            changed['logics'][-1]['analysis']+='券商上涨。'
            assert verified_event_market_answer(final,changed) is None
            changed = deepcopy(final['draft_report'])
            changed['logics'][1]['detail_analysis'] += '同期成分股业绩已确认。'
            assert verified_event_market_answer(final, changed) is None
            frozen = archive.report(final['id'], 1)['content']
            detailed = html.document_fromstring(design(frozen, ROOT, profile='detailed')['html'])
            assert not detailed.xpath('//main/section[@id="data"]')
            assert detailed.xpath('//footer//a[@href="#complete-report-data"]')
            assert '少数权重股' in detailed.find('.//main').text_content()
            assert '少数权重股' not in page
        with pytest.raises(ContractError,match='窗口和研究指数'):
            validate_source_request(final,'official',MAINLAND_OPERATION,
                                    dict(ts_code='000001.SH',start='2026-09-13',end='2026-09-18'))
        staged=deepcopy(final)
        binding=dict(task_id=final['id'],research_version=final['version'],input_hash=final['input_hash'])
        read_gap={}
        for market, scope in scopes.items():
            ident='policy_ifind_'+market+'_closes'
            structured=dict(id=ident,source='ifind',operation='cmd_history_quotation',parameters=ifind_parameters(scope),
                            question_ids=[final['questions'][0]['id']])
            staged['draft_source_attempts'].append(dict(proposed=structured,encoded=structured,binding=binding))
            read_gap[market]='数据需求'+ident+'：请配置ResearchHelper独立Access Token或Refresh Token'
            agent._gap(staged,read_gap[market],structured['question_ids'])
        unrelated='另一个时间窗仍缺收盘读数。'
        denied='数据需求policy_ifind_hong_kong_closes：来源已遇到授权权限失败'
        agent._gap(staged,unrelated)
        agent._gap(staged,denied)
        _resolve_policy_market_gaps(agent,staged,observations(staged))
        assert unrelated in staged['gaps'] and denied in staged['gaps']
        assert read_gap['hong_kong'] not in staged['gaps']
        assert (read_gap['mainland'] in staged['gaps'])==missing_sector
        assert all(item['resolution']['source_refs'] for item in staged['resolved_gap_records'])
    finally:
        agent.close_runtime()


def fomc_transport(*, earlier=True, broken_header=False, directory_fails=False):
    from researchhelper.contracts import ContractError

    entries = ''.join(f'<a href="{path}">Statement</a>' for path in (JULY, SEPTEMBER, NOVEMBER)).encode()
    pages = {
        SEPTEMBER: statement('September 17, 2026' if broken_header else 'September 16, 2026',
            'The Committee decided to raise the target range for the federal funds rate '
            'by 1/4 percentage point to 3-3/4 to 4 percent. The Committee will assess incoming data.'),
        JULY: statement('July 29, 2026', 'The Committee decided to maintain the target range '
                       'for the federal funds rate at 3-1/2 to 3-3/4 percent.'),
        NOVEMBER: statement('November 4, 2026', 'The Committee decided to lower the target range '
                            'for the federal funds rate to 3.625 to 3.875 percent.'),
    }

    def transport(_task, url, *_args, **_kwargs):
        if url == catalogue_url('0', channel='fed_fomc'):
            if directory_fails:
                raise ContractError('离线夹具：官网目录暂不可用')
            return entries
        path = url.removeprefix('https://www.federalreserve.gov')
        if not earlier and path == JULY:
            raise ContractError('离线夹具：7月声明暂不可用')
        return pages[path]

    return transport


def retain_fomc(session, task, *, earlier=True, broken_header=False):
    from researchhelper.evidence import EvidenceCollector

    collector = EvidenceCollector(offline=False)
    with patch('researchhelper.source_transport.official_html', side_effect=fomc_transport(earlier=earlier, broken_header=broken_header)):
        catalogue = collector.external(task, 'official', 'release_catalog', {'channel': 'fed_fomc', 'page': '0'}, {})
        task['source_records'].append(catalogue)
        for choice in release_choices(task, with_details=True):
            if choice['path_date_hint'] == '2026-07-29' and not earlier:
                continue
            parameters = {key: choice[key] for key in ('catalog_id', 'release_id')}
            task['source_records'].append(collector.external(task, 'official', 'release_original', parameters, {}))
    session.archive.save(task)


def retain_industry(session, task, *, numeric=True):
    parameters = dict(searchtype='stock', searchstring='证券行业股票，2026年半年报营业收入，2026年半年报归属于母公司所有者的净利润')
    payload = dict(errorcode=0, tables=[dict(table={
        '股票代码': ['600030.SH', '600999.SH'], '股票简称': ['合成甲', '合成乙'],
        '所属同花顺行业': ['供方分类甲', '供方分类乙'],
        REVENUE: [100, '--'] if numeric else ['--', None],
        PROFIT: [0, -4] if numeric else [None, 'N/A'], '市值[20260630]': [12, 14]})])
    source = snapshot_record(task, 'ifind', 'smart_stock_picking', parameters, payload)
    event = dict(kind='data_requests', batch=task['batch'], status='成功', fingerprint=source['request_hash'],
                 transport_isolated=True, network_request_started=True, network_worker_reaped=True)
    task['ledger']['dispatches'].append(event)
    task['source_records'].append(_record_current_acquisition(task, source, event))
    session.archive.save(task)


def papers(session, task):
    final = session.draft(task['id'])
    assert final['delivery'] and final['ledger']['used']['model_calls'] == 0, final.get('error')
    report = session.archive.report(final['id'], final['version'])
    return final, [html.document_fromstring(page) for page in (report['html'], render(report['content'], ROOT, report_profile='detailed'))]


def test_report_reads_only_the_requested_statements_and_links_both_originals(session):
    task = session.create(FOMC, research_mode='current', entry='事件')
    retain_fomc(session, task)
    final, views = papers(session, task)
    assert final['ledger']['used']['data_requests'] == task['ledger']['used']['data_requests']
    assert final['draft_report']['title'] == 'FOMC目标利率区间核对'
    for paper in views:
        summary = paper.get_element_by_id('summary')
        assert '上下限均上调0.25个百分点' in summary.text_content()
        links = summary.xpath('.//a[@class="quantity-link"]')
        assert [link.text_content() for link in links] == ['3.5%至3.75%', '3.75%至4%']
        for link in links:
            source = paper.get_element_by_id(link.get('href')[1:]).text_content()
            assert '美联储' in source and 'The Committee decided' in source and 'Last Update' not in source
        decisions = paper.xpath('//main//q/text()')
        assert len(decisions) == 2 and all(value.startswith('The Committee decided') for value in decisions)
        assert all('assess incoming data' not in value for value in decisions)


@pytest.mark.parametrize(('has_previous', 'bad_current_date', 'available', 'missing'), [
    (False, False, '3.75%至4%', '2026-07-29的声明目标利率区间尚未核对'),
    (True, True, '3.5%至3.75%', '2026-09-16的声明目标利率区间尚未核对'),
])
def test_incomplete_statement_comparison_keeps_the_verified_other_side(session, has_previous, bad_current_date, available, missing):
    task = session.create(FOMC, research_mode='current', entry='事件')
    retain_fomc(session, task, earlier=has_previous, broken_header=bad_current_date)
    final, views = papers(session, task)
    assert available in final['draft_report']['conclusion']
    assert any(missing in note for note in final['draft_report']['readout_gaps'])
    for paper in views:
        assert missing in paper.find('.//main').text_content()
        assert '上下限均上调' not in paper.find('.//main').text_content()


def test_fomc_readout_refuses_cross_task_or_tampered_excerpt(session):
    task = session.create(FOMC, research_mode='current', entry='事件')
    retain_fomc(session, task)
    excerpts = retained_public_excerpts(task, verify_text=True)
    assert len(excerpts) == 2
    excerpts[0]['excerpt_hash'] = 'tampered'
    assert len(fomc_readout(task, excerpts)['links']) == 1
    foreign = deepcopy(task)
    foreign['id'] += '-foreign'
    assert retained_public_excerpts(foreign, verify_text=True) == []
    assert fomc_readout(foreign, excerpts)['links'] == []


def test_selection_keeps_historical_cutoffs_and_exclusion_requests_outside_current_readout():
    assert fomc_rate_request(dict(question=FOMC, research_mode='historical')) is None
    assert fomc_rate_request(dict(question=FOMC + '不比较上次。', research_mode='current')) is None
    assert fomc_rate_request(dict(question='核对美联储最近两次FOMC目标利率区间。', research_mode='current'))['previous']


@pytest.mark.parametrize(('options', 'expected'), [
    ({}, '上下限均上调0.25个百分点'),
    ({'earlier': False}, '两次声明的区间变化暂缺'),
    ({'directory_fails': True}, '前次声明日期尚未核实'),
])
def test_app_direct_request_acquires_only_bound_statements_and_does_not_call_model(tmp_path, options, expected):
    def unexpected_model(*_args, **_kwargs):
        raise AssertionError('Explicit statement comparison requires no model')

    with patch('researchhelper.source_transport.official_html', side_effect=fomc_transport(**options)) as transport:
        agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=False,
                              draft_first=True, model=SimpleNamespace(reason=unexpected_model))
        try:
            task = agent.create(FOMC + '生成报告。', research_mode='current', entry='事件')
            assert direct_fomc_rate_request(task)['previous']
            final = agent.start(task['id'])
            assert final['delivery'] and final['ledger']['used']['model_calls'] == 0, final.get('error')
            urls = [call.args[1] for call in transport.call_args_list]
            expected_urls = [catalogue_url('0', channel='fed_fomc')]
            if not options.get('directory_fails'):
                expected_urls.extend('https://www.federalreserve.gov' + url for url in (JULY, SEPTEMBER))
            assert urls == expected_urls
            assert final['ledger']['used']['data_requests'] == len(expected_urls)
            report = agent.archive.report(task['id'], 1)
            assert expected in html.document_fromstring(report['html']).find('.//main').text_content()
            if options:
                assert '上下限均上调' not in final['draft_report']['conclusion']
        finally:
            agent.close_runtime()


@pytest.mark.parametrize('question', [
    FOMC + '解释对美股估值的影响。', FOMC + '只用iFinD。', FOMC + '只用附件。',
    '请核对美联储最近两次FOMC声明的目标利率区间，并比较前次变化，再分析原因。',
])
def test_automatic_statement_fetch_does_not_capture_other_research_or_source_instructions(session, question):
    task = session.create(question, research_mode='current', entry='事件')
    assert direct_fomc_rate_request(task) is None


@pytest.mark.parametrize('writer_available', [True, False])
def test_event_research_collects_bound_decisions_before_inference(tmp_path, writer_available):
    phases = []

    def reason(task, messages, *, phase, **_):
        phases.append(phase)
        assert [row['operation'] for row in task['source_records']] == ['release_catalog', 'release_original', 'release_original']
        if phase == 'draft_plan':
            return '{"data_requests":[]}'
        if not writer_available:
            raise ContractError('Test writer unavailable')
        documents = json.loads(messages[1]['content'])['data']['documents']
        assert len(documents) == 2 and all(doc['parts'] for doc in documents)
        assert '会议声明可核对的政策利率区间及前次变化' in messages[0]['content']
        return json.dumps(dict(title='美联储政策与港股科技估值', report_scope='mechanism',
            conclusion='政策变化提供了折现率分析的起点，市场定价影响仍须结合利率预期验证。',
            logics=[dict(title='折现率渠道', argument=dict(claim='折现率变化对远期现金流估值的影响更大。',
                evidence='两份会议声明用于确定政策区间的调整方向。',
                reasoning='政策利率调整只有在实际利率与风险溢价发生相应变化时才会改变折现率。',
                condition='同期盈利预期与汇率波动也可能影响估值，需对照同一窗口的数据。'),
                source_refs=[doc['source_ref'] for doc in documents])]))

    agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=False,
                          draft_first=True, model=SimpleNamespace(reason=reason))
    try:
        task = agent.create(FOMC_RESEARCH, research_mode='current', entry='事件')
        assert direct_readout(task) is None
        assert research_fomc_rate_request(task) == dict(dates=('2026-09-16',), previous=True, compare=True)
        with patch('researchhelper.source_transport.official_html', side_effect=fomc_transport()) as wire:
            final = agent.start(task['id'])
        assert final['delivery'] and final['run_mode'] == 'model' and final['status'] == '部分完成'
        assert phases == ['draft_plan', 'draft_report']
        assert [call.args[1] for call in wire.call_args_list] == [catalogue_url('0', channel='fed_fomc'),
            'https://www.federalreserve.gov' + JULY, 'https://www.federalreserve.gov' + SEPTEMBER]
        conclusion = final['draft_report']['conclusion']
        assert '上下限均上调0.25个百分点' in conclusion
        if writer_available:
            assert '利率预期' in conclusion
            logic, = final['draft_report']['logics']
            assert len(logic['source_refs']) == 2 and '风险溢价' in logic['analysis']
            report = agent.archive.report(task['id'], 1)
            for paper in (report['html'], render(report['content'], ROOT, report_profile='detailed')):
                doc = html.document_fromstring(paper)
                links = doc.get_element_by_id('summary').xpath('.//a[@class="quantity-link"]')
                assert [link.text_content() for link in links] == ['3.5%至3.75%', '3.75%至4%']
                assert 'FOMC声明原文' not in doc.find('.//main').text_content()
        else:
            assert '具体对象的实际影响需结合相应证据判断' in conclusion
    finally:
        agent.close_runtime()


def test_skill_offers_event_originals_to_host_without_claiming_a_direct_answer(tmp_path):
    api = Session(tmp_path, allow_network=True)
    try:
        task = api.create(FOMC_RESEARCH)
        tools = api.tools(task['id'], scope='acquisition')
        assert 'direct_report' not in tools
        catalogue, = tools['stated_official_requests']
        assert catalogue['parameters'] == dict(channel='fed_fomc', page='0')
        for directive in ('只用iFinD。', '只用附件。', '不要查询美联储。'):
            restricted = api.create(FOMC_RESEARCH + directive)
            assert 'stated_official_requests' not in api.tools(restricted['id'], scope='acquisition')
    finally:
        api.agent.close_runtime()


def test_natural_fomc_question_retrieves_the_meeting_and_qualifies_transmission(tmp_path):
    question = '2026年9月16日美联储议息会议对港股科技估值有什么影响？请研究。'

    def reason(task, messages, *, phase, **kwargs):
        if phase == 'draft_plan':
            return '{"data_requests":[]}'
        raise ContractError('Use the evidence-based report without a live model')

    agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=False,
                          draft_first=True, model=SimpleNamespace(reason=reason))
    try:
        task = agent.create(question, research_mode='current')
        assert research_fomc_rate_request(task) == dict(dates=('2026-09-16',), previous=True, compare=True)
        for alternative in ('如果' + question, question + '只用iFinD。'):
            assert research_fomc_rate_request(agent.create(alternative, research_mode='current')) is None
        with patch('researchhelper.source_transport.official_html', side_effect=fomc_transport()) as wire:
            final = agent.start(task['id'])
        assert wire.call_count == 3 and final['delivery']
        draft = final['draft_report']
        assert '上下限均上调0.25个百分点' in draft['conclusion']
        assert '远期现金流' in draft['logics'][0]['analysis']
        assert '下一可交易时段' in draft['logics'][0]['analysis']
        assert '会前利率预期' in draft['logics'][1]['analysis']
    finally:
        agent.close_runtime()


def test_skill_exposes_catalogue_then_uses_the_same_bound_original_requests(tmp_path):
    api = Session(tmp_path, allow_network=True)
    question = '仅用美联储官网原文核对最近两次FOMC声明的联邦基金目标利率区间，生成报告。'
    try:
        task = api.create(question)
        ident = task['id']
        with patch('researchhelper.source_transport.official_html', side_effect=fomc_transport()) as wire:
            tools = api.tools(ident, scope='acquisition')
            assert tools['direct_report'] == 'draft(task_id)'
            catalogue, = tools['stated_official_requests']
            assert catalogue['operation'] == 'release_catalog'
            api.draft(ident, data_requests=[catalogue], defer_delivery=True)
            originals = api.tools(ident, scope='acquisition')['stated_official_requests']
            assert [row['id'] for row in originals] == ['stated_fomc_20260916', 'stated_fomc_20261104']
            api.draft(ident, data_requests=originals, defer_delivery=True)
            tools = api.tools(ident, scope='acquisition')
            assert not tools['stated_official_requests'] and 'direct_report' not in tools
            final = api.draft(ident)
        assert wire.call_count == final['ledger']['used']['data_requests'] == 3
        assert final['ledger']['used']['model_calls'] == 0
        assert '上下限均下调0.125个百分点' in final['draft_report']['conclusion']
    finally:
        api.agent.close_runtime()


def test_industry_coverage_counts_valid_cells_without_plotting_unverified_amounts(session):
    task = session.create(INDUSTRY, research_mode='current', entry='行业')
    retain_industry(session, task)
    final, views = papers(session, task)
    draft = final['draft_report']
    assert draft['organization'] == 'fact' and draft['title'] == '证券行业财务样本核对'
    assert set(row['field'] for row in draft['facts']) == {REVENUE, PROFIT}
    assert '无法计算行业覆盖率' in draft['conclusion'] and '2个证券代码' in draft['conclusion']
    for paper in views:
        coverage = paper.get_element_by_id('sample-coverage')
        assert [row.xpath('./td/text()')[-2:] for row in coverage.xpath('.//tbody/tr')][:2] == [['1', '1'], ['2', '0']]
        assert not paper.xpath('//main//div[@role="img"]')
        assert [text for text in paper.xpath('//th[@scope="row"]/text()') if text == '600030.SH']
    assert '市值[20260630]' in final['source_records'][0]['snapshot']['tables'][0]['table']


def test_newly_received_columns_never_repair_a_changed_copy(session):
    task = session.create(INDUSTRY, research_mode='current', entry='行业')
    retain_industry(session, task)
    before = fingerprint(task)
    assert len(observations(task)) == 4 and fingerprint(task) == before
    task['source_records'][0]['snapshot']['tables'][0]['table'][REVENUE][0] = 99
    assert observations(task) == []


def test_local_delivery_still_opens_with_a_sample_and_all_requested_values_missing(session):
    task = session.create(INDUSTRY, research_mode='current', entry='行业')
    retain_industry(session, task, numeric=False)
    task.update(status='已取消', batch=1)
    session.archive.save(task)
    final = session.finish_draft(task['id'])
    assert final['delivery'] and final['ledger']['used']['data_requests'] == 0, final.get('error')
    assert all(row['value'] is None for row in final['draft_report']['facts'])
    assert '营业收入0条，缺2条' in final['draft_report']['conclusion']
