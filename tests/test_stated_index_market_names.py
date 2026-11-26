"""Verify named index quotes against the same catalogue as index research."""
from copy import deepcopy
import socket
from unittest.mock import patch

from lxml import html
import pytest

from core.session import Session
from researchhelper.contracts import ContractError, charge
from researchhelper.draft_sources import stated_window_requests
from researchhelper.draft_workflow import direct_readout
from researchhelper.evidence import declared_security_codes
from researchhelper.market_calendar import NAMED_INDEX_MARKET_POLICY
from researchhelper.report_designer import render_html
from tests import ROOT
from tests.market_observation_fixtures import INDEX_OHLC, read_captures, simulated_ifind_response


CUTOFF = '2026-11-16T21:00:00+08:00'
SH300 = '000300.SH'
SH50 = '000016.SH'
DAY = '2026-10-30'
PRIOR = '2026-10-29'
READING = '核对沪深300指数2026年10月30日收盘点位。'


def index_quote_replay_transport(capture, calls, *, missing=()):
    def transport(task, source, operation, params, credentials):
        assert source == 'ifind'
        calls.append((operation, deepcopy(params)))
        if operation == 'get_trade_dates':
            charge(task['ledger'], 'data_requests')
            raise ContractError('本次回放不含该日交易所日历。')
        assert operation == 'cmd_history_quotation'
        assert params['startdate'] == params['enddate']
        assert params['functionpara'] == capture['parameters']['functionpara']
        assert params['indicators'] == 'close'
        codes = set(params['codes'].split(','))
        assert codes <= {table['thscode'] for table in capture['snapshot']['tables']}
        day = params['startdate']
        tables = []
        for table in capture['snapshot']['tables']:
            code = table['thscode']
            if code in codes and code not in missing:
                value = table['table']['close'][table['time'].index(day)]
                tables.append(dict(thscode=code, time=[day], table=dict(close=[value])))
        snapshot = dict(errorcode=0, tables=tables)
        return simulated_ifind_response(task, operation, params, snapshot)
    return transport


@pytest.mark.parametrize('question,requested,missing', [
    (READING, [(SH300, DAY, 3985.501)], ()),
    ('核对沪深3002026年10月30日收盘点位，给出报告。', [(SH300, DAY, 3985.501)], ()),
    ('核对沪深300指数（000300.SH）在2026年10月30日收盘点位。', [(SH300, DAY, 3985.501)], ()),
    ('对比上证50和沪深300指数在2026年10月30日的收盘点位。',
     [(SH50, DAY, 2742.1108), (SH300, DAY, 3985.501)], ()),
    ('核对沪深300指数2026年10月30日收盘点位；核对上证502026年10月29日收盘点位。',
     [(SH50, PRIOR, 2762.7907), (SH300, DAY, 3985.501)], ()),
    ('核对上证50和沪深300指数2026年10月30日收盘点位。',
     [(SH50, DAY, 2742.1108), (SH300, DAY, 3985.501)], (SH50,)),
])
def test_named_index_points_share_the_ordinary_quote_and_report_paths(tmp_path, question, requested, missing):
    capture = read_captures(INDEX_OHLC.read_bytes(), grouped=False)
    session = Session(tmp_path, allow_network=True, source_credentials={})
    agent = session.agent
    calls = []
    try:
        with patch('researchhelper.engine.now', return_value=CUTOFF):
            task = session.create(question, research_mode='current')
        assert direct_readout(task)[0] == 'market_day'
        assert declared_security_codes(task) == sorted({code for code, _, _ in requested})
        assert session.tools(task['id'], scope='acquisition')['direct_report'] == 'draft(task_id)'
        with (patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline market replay')),
              patch.object(agent.collector, 'external', index_quote_replay_transport(capture, calls, missing=missing))):
            done = session.draft(task['id'])
        assert done.get('delivery'), done.get('error')
        assert done['run_mode'] == 'direct' and done['ledger']['used']['model_calls'] == 0
        draft = done['draft_report']
        expected = [row for row in requested if row[0] not in missing]
        assert sorted((row['code'], row['period'], row['raw_value']) for row in draft['facts']) == expected
        observed = {(code, params['startdate']) for op, params in calls
                    if op == 'cmd_history_quotation' for code in params['codes'].split(',')}
        assert observed == {(code, day) for code, day, _ in requested}
        assert len([op for op, _ in calls if op == 'cmd_history_quotation']) == len({row[1] for row in requested})
        assert all(row['unit'] == '点' for row in draft['facts'])
        assert '沪深300' in draft['title']
        if missing:
            assert any(SH50 in gap and DAY in gap for gap in draft['readout_gaps'])
        saved = agent.archive.report(done['id'], done['version'])
        assert saved['html_integrity_verified']
        for profile in ('standard', 'detailed'):
            page = html.document_fromstring(render_html(saved['content'], ROOT, profile=profile))
            body = page.xpath('//main')[0].text_content()
            assert '沪深300' in body and '3,985.501' in body
            assert bool(page.xpath('//*[@id="summary"]//a[contains(@class,"quantity-link")]'))
    finally:
        agent.close_runtime()


@pytest.mark.parametrize('question', [
    '核对沪深300指数（000905.SH）在2026年10月30日收盘点位。',
    '核对沪深300指数（000905.SH）在2026年10月29日和30日的收盘点位及区间涨跌幅。',
    '核对华泰柏瑞沪深300ETF在2026年10月30日的收盘价。',
    '核对沪深300全收益指数2026年10月30日的收盘点位。',
    '核对沪深300指数2026年10月30日的收盘点位并分析政策影响。',
    '不要联网。核对沪深300指数2026年10月30日的收盘点位。',
])
def test_similar_names_conflicts_or_restricted_questions_do_not_launch_a_direct_quote(tmp_path, question):
    session = Session(tmp_path)
    try:
        task = session.create(question, research_mode='current')
        assert direct_readout(task) is None
        assert not stated_window_requests(task)
    finally:
        session.agent.close_runtime()


def test_literal_comparison_accepts_natural_report_request_without_capturing_an_investment_question(tmp_path):
    session = Session(tmp_path, allow_network=True)
    comparison = '对比510300.SH和510500.SH在2026年10月30日的收盘价和当日收盘涨跌幅'
    try:
        for tail in ('，给一份研究报告。', '，写一份完整的研究报告。'):
            task = session.create(comparison + tail, research_mode='current')
            assert direct_readout(task) == ('market_day', None)
        task = session.create(comparison + '，写一份完整的研究报告并分析风格切换。', research_mode='current')
        assert direct_readout(task) is None
    finally:
        session.agent.close_runtime()


def test_archived_task_without_the_rule_keeps_its_original_subject_scope(tmp_path):
    session = Session(tmp_path)
    try:
        task = session.create(READING, research_mode='current')
        assert task.pop(NAMED_INDEX_MARKET_POLICY) == 1
        assert declared_security_codes(task) == []
        assert direct_readout(task) is None
    finally:
        session.agent.close_runtime()
