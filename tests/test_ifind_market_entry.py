"""Direct market Q&A through the shared App/Skill acquisition and export path."""
from collections import Counter
from copy import deepcopy
import socket
from unittest.mock import patch

import pytest

from core.session import Session
from researchhelper.contracts import ContractError
from researchhelper.draft_sources import observations, stated_window_requests
from researchhelper.draft_workflow import direct_readout
from researchhelper.evidence import validate_source_request
from researchhelper.intraday_quotes import MINUTE, REALTIME, question_request
from researchhelper.market_calendar import FUNCTION
from tests.market_observation_fixtures import simulated_ifind_response


CODE = '600519.SH'
DAYS = ['2026-10-20', '2026-10-21', '2026-10-22']
TIMES = ['2026-10-20 09:30', '2026-10-20 09:31', '2026-10-20 09:35']
DAILY = 'cmd_history_quotation'


@pytest.fixture
def session(tmp_path):
    value = Session(tmp_path, allow_network=True, source_credentials={})
    try:
        yield value
    finally:
        value.agent.close_runtime()


def create(session, question):
    task = session.create(question, research_mode='current')
    task['synthetic'] = True
    session.archive.save(task)
    return task


@pytest.mark.parametrize('operation', [DAILY, REALTIME, MINUTE])
def test_direct_market_request_saves_both_reports(session, operation):
    questions = {
        DAILY: f'查询{CODE}在{DAYS[0]}至{DAYS[-1]}的收盘价、成交量、成交额。',
        REALTIME: f'查询{CODE}的最新报价。',
        MINUTE: f'查询{CODE}在2026年10月20日9:30至9:35的1分钟收盘价。',
    }
    task = create(session, questions[operation])
    calls = []

    def transport(task, source, selected, params, credentials):
        assert source == 'ifind'
        calls.append(selected)
        if selected == 'get_trade_dates':
            assert params['functionpara'] == FUNCTION
            snapshot = dict(errorcode=0, tables=dict(time=DAYS))
        else:
            assert selected == operation
            if selected == DAILY:
                assert params['indicators'] == 'close,volume,amount'
                times, columns = DAYS, dict(close=[1364, 1371, 1355], volume=[0, None, 93280])
            elif selected == REALTIME:
                assert params == {'codes': CODE, 'indicators': 'latest'}
                times, columns = [TIMES[0]+':52'], dict(latest=[1350.32])
            else:
                assert params['functionpara']['Fill'] == 'Original'
                assert params['starttime'] == TIMES[0]+':00' and params['endtime'] == TIMES[-1]+':00'
                times, columns = TIMES, dict(close=[1350.66, 1351, 1350.72])
            snapshot = dict(errorcode=0, tables=[dict(thscode=CODE, time=times, table=columns)])
        return simulated_ifind_response(task, selected, params, snapshot)

    with (patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline only')),
          patch.object(session.agent.collector, 'external', transport)):
        done = session.draft(task['id'])
    assert calls == ([DAILY, 'get_trade_dates'] if operation == DAILY else [operation])
    assert done['delivery']
    assert done['ledger']['used']['model_calls'] == 0
    report = done['draft_report']
    rows = report['facts']
    if operation == DAILY:
        assert Counter(row['field'] for row in rows) == {'close': 3, 'volume': 3}
        assert [row['raw_value'] for row in rows if row['field'] == 'volume'] == [0, None, 93280]
        assert any('成交额' in gap for gap in report['readout_gaps'])
        assert [row['unit'] for row in rows if row['field'] == 'volume'] == ['原始单位待核对'] * 3
        assert not stated_window_requests(done)
    else:
        field = 'latest' if operation == REALTIME else 'minute_close'
        assert Counter(row['field'] for row in rows) == {field: 1 if operation == REALTIME else 3}
        assert all(row['locator']['field'] == ('close' if operation == MINUTE else 'latest') for row in rows)
        assert rows[0]['source_timestamp'] == (TIMES[0] if operation == MINUTE else TIMES[0]+':52')
        assert all('2026-10-20' in row['period'] for row in rows)
    for profile in ('standard', 'detailed'):
        rendered = session.export(task['id'], profile=profile).read_text()
        assert report['title'] in rendered
        assert '2026-10-20' in rendered


def test_inferred_intraday_request_respects_frequency_window_and_security(session):
    wrong = [
        f'查{CODE}在2026年10月20日9:30至9:35的5分钟收盘价。',
        f'查{CODE}在2026年10月20日9:30至9:35和10月21日的1分钟行情。',
        f'核对{CODE}最新收盘价。',
        f'不要查询{CODE}的最新价。',
        f'不用联网，查{CODE}的最新价。',
        f'查{CODE}和000001.SZ的实时行情。',
    ]
    for phrase in wrong:
        task = create(session, phrase)
        assert question_request(task) is None, phrase
    task = create(session, f'查询{CODE}在2026年10月20日9:30至9:35的1分钟收盘价。')
    params = direct_readout(task)[1]['parameters']
    for changed in (dict(params, codes='000001.SZ'), dict(params, endtime='2026-10-20 09:40:00')):
        with pytest.raises(ContractError):
            validate_source_request(task, 'ifind', MINUTE, changed)

    live = create(session, f'查询{CODE}的最新价。')
    params = question_request(live)['parameters']
    record = simulated_ifind_response(live, REALTIME, params, dict(errorcode=0,
        tables=[dict(thscode=CODE, time=['2099-12-30 09:33:00'], table=dict(latest=[1350.66]))]))
    live['source_records'] = [dict(record, question_ids=['q1'])]
    assert observations(live) == []
    forged = deepcopy(record)
    forged['snapshot']['tables'][0]['time'] = [TIMES[0]]
    live['source_records'] = [dict(forged, question_ids=['q1'])]
    assert observations(live) == []


def test_daily_requests_keep_aligned_scope_when_one_field_is_already_held(session):
    task = create(session, f'查{CODE}在{DAYS[0]}至{DAYS[-1]}的收盘价和成交额。')
    request, = stated_window_requests(task)
    original = dict(request['parameters'], indicators='close')
    record = simulated_ifind_response(task, DAILY, original,
        dict(errorcode=0, tables=[dict(thscode=CODE, time=DAYS, table=dict(close=[20, 21, 22]))]))
    task['source_records'] = [dict(record, question_ids=['q1'])]
    request, = stated_window_requests(task)
    assert request['parameters']['indicators'] == 'amount'

    task = create(session, f'查询{CODE}在{DAYS[0]}至{DAYS[-1]}的收盘价，不要成交量和成交额。')
    requests = stated_window_requests(task)
    assert all(row['parameters']['indicators'] == 'close' for row in requests)
