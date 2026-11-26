"""Replay a completed session, including partial inputs and calendar failures."""
from copy import deepcopy
from decimal import Decimal
import socket
from unittest.mock import patch

from lxml import html
import pytest

from core.session import Session
from researchhelper.contracts import ContractError, charge, fingerprint
from researchhelper.draft_analysis import DAILY_NAV_PREMIUM_METHOD
from researchhelper.draft_workflow import direct_readout
from researchhelper.market_calendar import (LATEST_SESSION_CALCULATION_POLICY, latest_session_calendar_requests,
                                             latest_session_calculation_sessions, stated_latest_session_calculation)
from researchhelper.report_designer import render_html
from tests import ROOT
from tests.market_observation_fixtures import scoped_etf_nav_reply, simulated_ifind_response
from tests.test_compound_market_calculations import retained_calculation_inputs
from tests.test_financial_market_readout import scoped_retained_reply


BEFORE_CLOSE = '2026-10-22T10:30:00+08:00'
AFTER_CLOSE = '2026-10-22T20:00:00+08:00'
PREMIUM_QUESTION = '核对510300.SH最近一个交易日的收盘价、单位净值和折溢价。'
RETURN_QUESTION = '核对510300.SH最近一个交易日的收盘价及当日涨跌幅。'
COMBINED_QUESTION = '核对510300.SH最近一个交易日的收盘价、当日涨跌幅、单位净值及收盘折溢价率。'


def latest_calculation_transport(agent, inputs, calls, *, disturbance=None):
    """Project captured responses for the requested windows in the replay."""
    external = agent.collector.external

    def transport(task, source, operation, params, credentials):
        calls.append((source, operation, deepcopy(params)))
        if operation == disturbance:
            charge(task['ledger'], 'data_requests')
            raise ContractError('该来源在本次回放中不可用。')
        if (source, operation) == ('official', 'etf_pcf'):
            assert params == {'ts_code': '510300.SH'}
            return external(task, source, operation, params, credentials)
        assert source == 'ifind'
        if operation == 'get_trade_dates' and disturbance == 'empty_calendar':
            response = dict(errorcode=0, tables=dict(time=[]))
        elif operation == 'date_sequence':
            response = scoped_etf_nav_reply(inputs['nav'], params)
        else:
            response = scoped_retained_reply(({}, inputs['prices'], inputs['calendar']), operation, params)
        if disturbance == 'previous_missing' and operation == 'cmd_history_quotation':
            table, = response['tables']
            index = table['time'].index('2026-10-20')
            table['time'].pop(index)
            for values in table['table'].values():
                values.pop(index)
        return simulated_ifind_response(task, operation, params, response)

    return transport


def replay_public_nav(inputs, task, code, started, timeout, event, **options):
    assert code == '510300.SH'
    event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
    return inputs['exchange'].encode()


@pytest.mark.parametrize('question,cutoff,disturbance,expected_day,calculation,gap', [
    (PREMIUM_QUESTION, BEFORE_CLOSE, None, '2026-10-21', (DAILY_NAV_PREMIUM_METHOD,), ''),
    (PREMIUM_QUESTION, AFTER_CLOSE, None, '2026-10-22', None, '同日可比收盘价或官方单位净值缺失'),
    (PREMIUM_QUESTION+'只用iFinD。', BEFORE_CLOSE, None, '2026-10-21', None, '收盘折溢价率暂缺'),
    (PREMIUM_QUESTION, BEFORE_CLOSE, 'etf_pcf', '2026-10-21', None, '收盘折溢价率暂缺'),
    (PREMIUM_QUESTION, BEFORE_CLOSE, 'cmd_history_quotation', '2026-10-21', None, '收盘折溢价率暂缺'),
    (PREMIUM_QUESTION, BEFORE_CLOSE, 'get_trade_dates', None, None, '最近交易日尚未核实'),
    (PREMIUM_QUESTION, BEFORE_CLOSE, 'empty_calendar', None, None, '回看窗口内无已结束的交易日'),
    (RETURN_QUESTION, BEFORE_CLOSE, None, '2026-10-21', ('raw_change_v1',), ''),
    (COMBINED_QUESTION, BEFORE_CLOSE, None, '2026-10-21', ('raw_change_v1', DAILY_NAV_PREMIUM_METHOD), ''),
    (RETURN_QUESTION, BEFORE_CLOSE, 'previous_missing', '2026-10-21', None, '日涨跌幅暂缺'),
    (RETURN_QUESTION, BEFORE_CLOSE, 'get_trade_dates', None, None, '日涨跌幅暂缺'),
])
def test_latest_trade_date_calculation_preserves_available_observations(
        tmp_path, question, cutoff, disturbance, expected_day, calculation, gap):
    session = Session(tmp_path, allow_network=True, source_credentials={})
    inputs, calls = retained_calculation_inputs(), []
    try:
        with patch('researchhelper.engine.now', return_value=cutoff):
            task = session.create(question, entry='ETF', research_mode='current')
        route, scope = direct_readout(task)
        assert route == 'latest_session_calculation'
        assert session.tools(task['id'], scope='acquisition')['direct_report'] == 'draft(task_id)'
        task['synthetic'] = True
        session.archive.save(task)
        with (patch.object(socket.socket, 'connect', side_effect=AssertionError('Use retained data only')),
              patch.object(session.agent.collector, 'external', latest_calculation_transport(
                  session.agent, inputs, calls, disturbance=disturbance)),
              patch('researchhelper.source_transport.official_etf',
                    side_effect=lambda *args, **kwargs: replay_public_nav(inputs, *args, **kwargs))):
            result = session.draft(task['id'])
        assert result.get('delivery'), result.get('error')
        assert result['run_mode'] == 'direct' and result['ledger']['used']['model_calls'] == 0
        assert calls[0][1] == 'get_trade_dates'
        assert calls[0][2]['enddate'] == ('2026-10-21' if cutoff == BEFORE_CLOSE else '2026-10-22')
        assert len({fingerprint(row) for row in calls}) == len(calls)
        assert [operation for _, operation, _ in calls].count('get_trade_dates') == 1
        assert (any(operation=='etf_pcf' for _, operation, _ in calls)
                == ('折溢价' in question and '只用iFinD' not in question and expected_day is not None))
        sessions = latest_session_calculation_sessions(result, scope)
        assert (sessions[-1].isoformat() if sessions else None) == expected_day
        assert disturbance != 'empty_calendar' or (sessions == () and len(calls) == 1)
        if sessions is not None:
            assert latest_session_calendar_requests(result, scope) == []
        saved = session.agent.archive.report(task['id'], result['version'])
        assert saved['html_integrity_verified']
        draft = saved['content']['draft']
        facts = {row['id']:row for row in draft['facts']}
        if disturbance == 'empty_calendar':
            assert not facts
        else:
            assert facts
        assert all(row['period'] <= calls[0][2]['enddate'] for row in facts.values())
        if expected_day is None:
            assert not any(row.get('previous_close_for') for row in facts.values())
        summary = draft['conclusion'] + ''.join(draft.get('readout_gaps', ()))
        assert (gap in summary) if gap else not draft.get('readout_gaps')
        found = draft['calculations']
        assert tuple(row['method'] for row in found) == (calculation or ())
        for row in found:
            first, second = (facts[ident] for ident in row['data_refs'])
            if row['method'] == DAILY_NAV_PREMIUM_METHOD:
                assert first['period'] == second['period'] == expected_day
                assert (first['source'], second['source']) == ('ifind','official')
                expected = (Decimal('4.185') / Decimal('4.1856') - 1) * 100
            else:
                assert (first['period'], second['period']) == ('2026-10-20', expected_day)
                assert row['session_basis']['kind'] == 'exchange_calendar'
                expected = (Decimal('4.185') / Decimal('4.22') - 1) * 100
            assert row['value'] == pytest.approx(float(expected))
            assert sum(row['id'] in link['calculation_refs'] for link in draft['quantity_links']['conclusion']['links']) == 1
        for profile in ('standard','detailed'):
            page = html.document_fromstring(render_html(saved['content'], ROOT, profile=profile))
            visible = page.find('.//main').text_content()
            assert not gap or gap in visible
            for row in found:
                links = page.xpath('//*[@id="summary"]//a[@class="quantity-link" and text()=$value]',
                                   value=row['display'])
                assert len(links) == 1, (row['method'], row['display'], visible)
                target = page.get_element_by_id(links[0].attrib['href'][1:])
                details = target.text_content()
                assert all(facts[ident]['period'] in details for ident in row['data_refs'])
                for ident in row['data_refs']:
                    assert str(facts[ident]['raw_value']) in details
            if disturbance == 'get_trade_dates':
                assert '4.185' in visible and '2026-10-21' in visible
        if question == COMBINED_QUESTION:
            damaged = deepcopy(saved['content'])
            for ident, calendar in damaged['calendar_replay']['events'].items():
                damaged['daily_nav_premium_sources']['events'][ident] = dict(calendar['event'])
                calendar['event']['response_snapshot_hash'] = 'invalid'
            damaged.pop('content_hash')
            damaged['content_hash'] = fingerprint(damaged)
            page = html.document_fromstring(render_html(damaged, ROOT, profile='standard'))
            assert '-0.0143%' in page.get_element_by_id('summary').text_content()
            assert not page.xpath('//*[@id="summary"]//a[text()="-0.0143"]')
            assert page.xpath('//*[@id="summary"]//a[text()="4.185"]')
    finally:
        session.agent.close_runtime()


@pytest.mark.parametrize('text', [
    '分析510300.SH最近一个交易日的收盘折溢价率及形成原因。',
    '核对510300.SH最近交易日盘中估算净值和收盘折溢价率。',
    '核对510300.SH最近一个交易日与2026-10-20的区间涨跌幅。',
    '核对600519.SH最近交易日收盘折溢价率。',
    PREMIUM_QUESTION+'本次不联网。',
])
def test_latest_session_calculation_does_not_change_the_requested_measure(tmp_path, text):
    session = Session(tmp_path)
    try:
        with patch('researchhelper.engine.now', return_value=BEFORE_CLOSE):
            task = session.create(text, research_mode='current')
        assert stated_latest_session_calculation(task) is None
    finally:
        session.agent.close_runtime()


def test_legacy_task_keeps_its_old_latest_scope(tmp_path):
    session = Session(tmp_path)
    try:
        task = session.create(PREMIUM_QUESTION, research_mode='current')
        assert task.pop(LATEST_SESSION_CALCULATION_POLICY) == 1
        assert direct_readout(task) is None
    finally:
        session.agent.close_runtime()
