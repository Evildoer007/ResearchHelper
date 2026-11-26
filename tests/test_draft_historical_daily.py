"""Date-only market observations must respect a historical cutoff within the day."""
from copy import deepcopy
import socket
from unittest.mock import patch

from lxml import html
import pytest

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.draft_sources import historical_restricted_sources, observations, source_context, writing_context
from researchhelper.draft_workflow import _serialize_request
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import data_request_contract, snapshot_record, validate_source_request
from researchhelper.market_series import HISTORICAL_DAILY_POLICY, observation_selection, validate_observation_scope
from researchhelper.planning import input_fingerprint
from researchhelper.draft_visuals import _frozen_observations


QUESTION = '核对截至研究时点600519.SH的收盘价'
DAYS = ['2026-10-14', '2026-10-15', '2026-10-16']
CUTOFF = DAYS[-1] + 'T14:00:00+08:00'
FUTURE_CLOSE = 98765


def price_request(start=DAYS[0], end=DAYS[-1], ident='historical_quotes'):
    return dict(id=ident, source='ifind', operation='cmd_history_quotation', question_ids=['q1'],
                parameters=dict(codes='600519.SH', indicators='close', startdate=start, enddate=end,
                                functionpara=dict(Currency='RMB', CPS='1', Fill='Omit')))


def price_snapshot(task, end=DAYS[-1]):
    record = snapshot_record(task, 'ifind', 'cmd_history_quotation', price_request(end=end)['parameters'],
        dict(errorcode=0, tables=[dict(thscode='600519.SH', time=DAYS, table=dict(close=[111, 112, FUTURE_CLOSE]))]))
    task['source_records'].append(record)
    return record


def historical_proposal():
    return dict(title='历史收盘观测', conclusion='比较已取得的同一标的收盘观测。',
        logics=[dict(title='历史价格', analysis='2026年10月15日收盘价为112元。', data_refs=['D1', 'D2']),
                dict(title='同日价格', analysis='2026年10月16日收盘价为'+str(FUTURE_CLOSE)+'元。', data_refs=['D3'])])


def check_delivery(task, report_text, *, intraday):
    expected = [111, 112] if intraday else [111, 112, FUTURE_CLOSE]
    assert task.get('delivery') and [row['raw_value'] for row in task['draft_report']['facts']] == expected
    assert task['ledger']['used']['model_calls'] == task['ledger']['used']['data_requests'] == 0
    for profile in ('standard', 'detailed'):
        text = report_text(profile)
        paper = html.document_fromstring(text).xpath('//main')[0].text_content()
        assert '112元' in paper
        assert (str(FUTURE_CLOSE) in text) is not intraday
    return dict(report_saved=True, profiles=['standard', 'detailed'], facts=expected)


@pytest.fixture
def scenario(tmp_path):
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('No external calls')):
        archive = ResearchArchive(tmp_path)
        agent = ResearchAgent(archive, ROOT, offline=True, draft_first=True)
        try:
            task = agent.create(QUESTION, entry='个股', objects=['600519.SH'], cutoff=CUTOFF, research_mode='historical')
            yield agent, archive, task
        finally:
            agent.close_runtime()


def test_quote_is_filtered_before_planning_writing_and_report(scenario):
    agent, archive, task = scenario
    original = deepcopy(price_snapshot(task))
    assert task[HISTORICAL_DAILY_POLICY] == 1
    assert [row['raw_value'] for row in observations(task)] == [111, 112]
    assert str(FUTURE_CLOSE) not in str(source_context(task))
    assert str(FUTURE_CLOSE) not in str(writing_context(task, observations(task)))
    archive.save(task)
    finished = agent.start(task['id'], dict(draft_report=historical_proposal(), data_requests=[]))
    assert finished['source_records'] == [original]
    saved = archive.report(task['id'], task['version'])
    assert saved['content'][HISTORICAL_DAILY_POLICY] == 1
    assert [row['raw_value'] for row in _frozen_observations(saved['content'], [original])] == [111, 112]
    from researchhelper.report_designer import design
    check_delivery(finished, lambda profile: design(saved['content'], ROOT, profile=profile)['html'], intraday=True)


@pytest.mark.parametrize('question,cutoff,latest', [
    ('截至2026年10月16日， 只用当时已公开资料核对600519.SH收盘价。', '2026-10-16', '2026-10-16'),
    ('核对600519.SH收盘价。研究截止时点：北京时间2026年10月16日14时30分。', '2026-10-16T14:30:00+08:00', '2026-10-15'),
])
def test_skill_accepts_explicit_question_cutoff(tmp_path,question,cutoff,latest):
    from core.session import Session
    session = Session(tmp_path)
    try:
        task = session.create(question)
        assert (task['research_mode'],task['cutoff']) == ('historical',cutoff)
        assert observation_selection(task)['as_of'] == latest
    finally:
        session.agent.close_runtime()


def test_boundary_uses_local_market_date_and_preserves_existing_versions(scenario):
    _, _, task = scenario
    price_snapshot(task)
    prior = dict(task)
    prior.pop(HISTORICAL_DAILY_POLICY)
    assert input_fingerprint(prior) != task['input_hash']
    assert [row['raw_value'] for row in observations(prior)] == [111, 112, FUTURE_CLOSE]
    assert [row['raw_value'] for row in observations(dict(task, research_mode='current'))] == [111, 112, FUTURE_CLOSE]
    for cutoff in ('2026-10-15T18:00:00Z', '2026-10-16T15:59:59.999998Z'):
        actual = dict(task, cutoff=cutoff)
        assert observation_selection(actual)['as_of'] == DAYS[1]
        assert [row['raw_value'] for row in observations(actual)] == [111, 112]
    for cutoff in (DAYS[-1], '2026-10-16T15:59:59.999999Z'):
        actual = dict(task, cutoff=cutoff)
        assert observation_selection(actual)['as_of'] == DAYS[-1]
        assert [row['raw_value'] for row in observations(actual)] == [111, 112, FUTURE_CLOSE]


@pytest.mark.parametrize('source,operation', [
    ('ifind','basic_data_service'), ('ifind','edb_service'), ('ifind','smart_stock_picking'),
    ('ifind','data_pool'), ('official','index_file'), ('official','etf_pcf'), ('tinyshare','income'),
])
def test_current_version_routes_are_unavailable_in_historical_research(scenario, source, operation):
    _, _, task = scenario
    tools = {(row['source'],row['operation']) for row in data_request_contract(task,first_report=True)['tools']}
    assert (source,operation) not in tools
    assert {('ifind','cmd_history_quotation'), ('ifind','report_query')} <= tools
    with pytest.raises(ContractError,match='历史截止时点的可得版本待核对'):
        validate_source_request(task,source,operation,{})


def test_explicit_update_of_an_unlabelled_legacy_task_applies_historical_rules(scenario):
    agent, archive, _ = scenario
    first = agent.create(QUESTION,objects=['600519.SH'],cutoff=DAYS[1]+'T23:59:59+08:00')
    assert 'research_mode' not in first
    first = agent.start(first['id'],dict(draft_report=None,data_requests=[]))
    frozen = archive.report(first['id'],1)
    second = agent.prepare_update(first['id'],CUTOFF)
    assert second['research_mode']=='historical' and second[HISTORICAL_DAILY_POLICY]==1
    assert archive.report(first['id'],1)==frozen


def test_historical_model_context_and_export_keep_current_edb_only_in_the_workpaper(scenario):
    agent, archive, task = scenario
    task['objects'].append('M002826730')
    task['input_hash'] = input_fingerprint(task)
    params = dict(indicators='M002826730',startdate='2026-09-01',enddate='2026-09-30')
    payload = dict(errorcode=0,tables=[dict(id=['M002826730'],time=['2026-09-30'],value=[9876.54321])])
    current_record = snapshot_record(task,'ifind','edb_service',params,payload)
    task['source_records'].append(current_record)
    price_snapshot(task)
    assert current_record['id'] in historical_restricted_sources(task)
    assert [row['raw_value'] for row in observations(task)]==[111,112]
    assert any(row['raw_value']==9876.54321 for row in observations(dict(task,draft_source_reader_version=22)))
    assert '9876.54321' not in str(source_context(task))
    assert '9876.54321' not in str(writing_context(task,observations(task)))
    archive.save(task)
    result = agent.start(task['id'],dict(data_requests=[],draft_report=None))
    assert result.get('delivery') and result['ledger']['used']['data_requests']==0
    assert result['source_records'][0]==current_record
    saved = archive.report(task['id'],1)
    assert '9876.54321' not in saved['html']
    assert [row['raw_value'] for row in _frozen_observations(saved['content'],result['source_records'])]==[111,112]


@pytest.mark.parametrize('source,operation,code,field', [
    ('ifind', 'date_sequence', '600519.SH', 'ths_pe_ttm_stock'),
    ('tinyshare', 'daily', '600519.SH', 'close'),
    ('tinyshare', 'daily_basic', '600519.SH', 'pe_ttm'),
    ('tinyshare', 'fund_daily', '510300.SH', 'close'),
])
def test_other_date_only_market_fields_obey_the_same_cutoff(scenario, source, operation, code, field):
    agent, _, task = scenario
    if code == '510300.SH':
        task = agent.create('核对510300.SH的已取得收盘价', entry='ETF', objects=[code],
                            cutoff=CUTOFF, research_mode='historical')
    if source == 'ifind':
        parameters = dict(codes=code, startdate=DAYS[0], enddate=DAYS[-1], functionpara={'Fill': 'Blank'},
                          indipara=[dict(indicator=field, indiparams=['', '100'])])
        payload = dict(errorcode=0, tables=[dict(thscode=code, time=DAYS, table={field: [111, 112, FUTURE_CLOSE]})])
        end_key, expected_end = 'enddate', DAYS[1]
    else:
        parameters = dict(ts_code=code, start_date='20261014', end_date='20261016', fields='ts_code,trade_date,'+field)
        payload = dict(code=0, data=[dict(ts_code=code, trade_date=day.replace('-', ''), **{field: value})
                                   for day, value in zip(DAYS, [111, 112, FUTURE_CLOSE])])
        end_key, expected_end = 'end_date', '20261015'
    task['source_records'].append(snapshot_record(task, source, operation, parameters, payload))
    assert [row['raw_value'] for row in observations(task)] == [111, 112]
    request = dict(source=source, operation=operation, parameters=parameters)
    encoded = _serialize_request(request, task)
    assert encoded['parameters'][end_key] == expected_end
    assert validate_source_request(task, source, operation, encoded['parameters']) == encoded['parameters']
    with pytest.raises(ContractError, match='历史截止时点位于日内'):
        validate_source_request(task, source, operation, parameters)


def test_latest_crossing_and_exact_day_have_distinct_request_semantics(scenario):
    agent, archive, task = scenario
    raw = price_request()
    crossed = _serialize_request(raw, task)
    assert raw['parameters']['enddate'] == DAYS[-1]
    assert crossed['parameters']['enddate'] == DAYS[1]
    agent._validated_data_needs(task, [crossed])
    exact = price_request(DAYS[-1], DAYS[-1], ident='exact_day')
    assert _serialize_request(exact, task) == exact
    with pytest.raises(ContractError, match='历史截止时点位于日内'):
        agent._validated_data_needs(task, [exact])
    tools = data_request_contract(task)['tools']
    quote = next(row for row in tools if row['operation'] == raw['operation'])
    day = quote['observation_selection']['as_of']
    assert day == DAYS[1] == quote['example']['parameters']['enddate']
    latest = price_request()
    for key in ('startdate', 'enddate'):
        latest['parameters'].pop(key)
    latest['parameters']['observation_window'] = dict(mode='latest_available', as_of=day, lookback_calendar_days=7)
    compiled = _serialize_request(latest, task)
    assert compiled['parameters']['startdate'] == '2026-10-09' and compiled['parameters']['enddate'] == DAYS[1]
    validate_observation_scope(task, compiled)
    latest['parameters']['observation_window']['as_of'] = DAYS[-1]
    with pytest.raises(ContractError, match='最近观测窗口未绑定'):
        _serialize_request(latest, task)
    price_snapshot(task)
    archive.save(task)
    finished = agent.start(task['id'], dict(draft_report=historical_proposal(), data_requests=[exact]))
    assert finished.get('delivery')
    assert finished['ledger']['used']['data_requests'] == 0
    assert finished['draft_source_attempts'][0]['status'] == '参数未通过'
    assert '历史截止时点位于日内' in finished['draft_source_attempts'][0]['error']
