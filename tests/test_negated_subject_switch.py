"""Follow-up subject selection and real request admission."""

from unittest.mock import patch

import pytest

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.draft_sources import stated_window_requests
from researchhelper.economic_sources import available_economic_indicators
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import available_security_codes, validate_source_request
from researchhelper.market_calendar import stated_direct_market_day
from researchhelper.planning import draft_research_scope, input_fingerprint
from tests import ROOT


@pytest.fixture
def agent(tmp_path):
    with patch('socket.socket.connect', side_effect=AssertionError('Offline acceptance')):
        runtime = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True,
                                draft_first=True, require_argument_support=True)
        try:
            yield runtime
        finally:
            runtime.close_runtime()


STOCK = '核对贵州茅台600519.SH在2026-10-27的收盘价。'
ETF = '比较159915.SZ和510300.SH在2026-10-21的单位净值。'
CPI = '核对中国CPI当月同比M002826730在2026年9月的读数。'


@pytest.mark.parametrize('question, rejected', [
    ('别换看510300.SH，还是核对600519.SH在2026-10-27的收盘价。', '510300.SH'),
    ('不要改看600036.SH，按上一轮的日期核对收盘价。', '600036.SH'),
    ('先别换看600036.SH，而是核对这个标的在2026-10-27的收盘价。', '600036.SH'),
    ('核对600519.SH在2026-10-27的收盘价。“换看510300.SH”只作备注。', '510300.SH'),
])
def test_rejected_and_quoted_switches_cannot_authorize_a_stock(agent, question, rejected):
    parent = agent.create(STOCK, research_mode='current')
    child = agent.follow_up(parent['id'], question, start=False)
    assert child['question'] == question and child['input_hash'] == input_fingerprint(child)
    assert child['objects'] == available_security_codes(child) == ['600519.SH']
    assert child['conversation_context']['scope_relation'] == 'continue'
    assert rejected not in draft_research_scope(child)['active_question']
    assert stated_direct_market_day(child, for_report=True)
    request, = stated_window_requests(child)
    parameters = request['parameters']
    assert (parameters['codes'], parameters['startdate'], parameters['enddate']) == (
        '600519.SH', '2026-10-27', '2026-10-27')
    with pytest.raises(ContractError):
        validate_source_request(child, 'ifind', 'cmd_history_quotation', dict(parameters, codes=rejected))


@pytest.mark.parametrize('question', [
    '不用换成食品CPI，核对这个指标8月的读数。',
    '不用换成食品CPI而是核对这个指标8月的读数。',
    '不是要换看食品CPI，是核对2026年8月的读数。',
    '不要改看M002826732在2026年9月的读数，核对中国CPI当月同比M002826730在2026年8月的读数。',
])
def test_negative_indicators_and_dates_are_outside_macro_requests(agent, question):
    parent = agent.create(CPI, research_mode='current')
    child = agent.follow_up(parent['id'], question, start=False)
    assert child['objects'] == available_economic_indicators(child) == ['M002826730']
    request, = stated_window_requests(child)
    assert request['parameters'] == dict(indicators='M002826730',
                                         startdate='2026-08-01', enddate='2026-08-31')
    with pytest.raises(ContractError):
        validate_source_request(child, 'ifind', 'edb_service',
                                dict(request['parameters'], indicators='M002826732'))


def test_rejecting_a_switch_and_excluding_a_fund_have_different_scope(agent):
    parent = agent.create(ETF, research_mode='current')
    retain_pair = agent.follow_up(parent['id'], '不要换成后者，继续比较两只基金的单位净值。', start=False)
    assert retain_pair['objects'] == ['159915.SZ', '510300.SH']
    assert not retain_pair['subject_scope']['excluded']

    for question in ('别查前者。', '前者先不看了。'):
        exclude = agent.follow_up(parent['id'], question, start=False)
        assert exclude['objects'] == ['510300.SH']
        assert exclude['subject_scope']['excluded'] == ['159915.SZ']

    dated = agent.follow_up(parent['id'], '不要换成第三只ETF，按上一轮的日期核对后者的单位净值。', start=False)
    request, = stated_window_requests(dated)
    assert dated['objects'] == ['510300.SH']
    assert stated_direct_market_day(dated, for_report=True)
    assert (request['parameters']['codes'], request['parameters']['startdate']) == ('510300.SH', '2026-10-21')


def test_a_fresh_affirmative_switch_and_an_older_round_stay_available(agent):
    first = agent.create(STOCK, research_mode='current')
    second = agent.follow_up(first['id'], '换看510300.SH在2026-10-21的单位净值。', start=False)
    assert second['objects'] == ['510300.SH']
    returned = agent.follow_up(second['id'],
        '切回第1轮的标的，别换成600036.SH，按上一轮的日期核对收盘价。', start=False)
    assert returned['objects'] == ['600519.SH']
    assert returned['conversation_context']['subject_reference']['source']['task_id'] == first['id']
    affirmative = agent.follow_up(returned['id'], '现在换看600036.SH。', start=False)
    assert affirmative['objects'] == ['600036.SH']


def test_quoted_literal_identifier_in_a_plain_question_is_still_admitted(agent):
    task = agent.create('核对“510300.SH”在2026-10-21的单位净值。', research_mode='current')
    assert available_security_codes(task) == ['510300.SH']
    assert 'subject_scope' not in task


@pytest.mark.parametrize('question', [
    '我不想研究600036.SH，只核对600519.SH在2026-10-27的收盘价。',
    '我暂时不打算研究600036.SH而是核对600519.SH在2026-10-27的收盘价。',
    '核对600519.SH在2026-10-27的收盘价。「我不想研究600036.SH」只作备注。',
])
def test_first_question_only_dispatches_the_active_subject(agent, question):
    task = agent.create(question, research_mode='current')
    assert available_security_codes(task) == ['600519.SH']
    assert stated_direct_market_day(task)
    request, = stated_window_requests(task)
    assert request['parameters']['codes'] == '600519.SH'
    with pytest.raises(ContractError):
        validate_source_request(task, 'ifind', request['operation'],
                                dict(request['parameters'], codes='600036.SH'))


def test_metric_exclusion_and_affirmative_selection_keep_the_right_subject(agent):
    stock = '600519.SH'
    task = agent.create(f'我不想核对{stock}的市盈率，只核对{stock}在2026-10-27的收盘价。',
                        research_mode='current')
    assert available_security_codes(task) == [stock]
    excluded = agent.follow_up(task['id'], '我不想研究600519.SH，只核对600036.SH。', start=False)
    assert available_security_codes(excluded) == ['600036.SH']
    restored = agent.follow_up(excluded['id'], '改看600519.SH。', start=False)
    assert available_security_codes(restored) == [stock]


def test_contextual_readout_keeps_requested_analysis(agent):
    parent = agent.create(STOCK, research_mode='current')
    child = agent.follow_up(parent['id'],
        '不要改看600036.SH，按上一轮的日期核对收盘价，并分析该股的投资价值。', start=False)
    assert child['objects'] == ['600519.SH']
    assert not stated_direct_market_day(child, for_report=True)
