"""Stop after the provider response commit and resume from the actual archive."""
from contextlib import ExitStack
import json
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit

import pytest

from core.session import Session
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import LocalStateError
from researchhelper.draft_workflow import direct_readout
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import current_query_reference
from tests import ROOT
from tests.market_observation_fixtures import scoped_etf_nav_reply
from tests.test_compound_market_calculations import retained_calculation_inputs
from tests.test_financial_market_readout import (
    DATED_QUESTION, ETF_CUTOFF, ETF_DIRECTORY_QUERY, retained_inputs, scoped_retained_reply,
)
from tests.test_latest_session_calculation import BEFORE_CLOSE, COMBINED_QUESTION, replay_public_nav


class InterruptedAfterCommit(BaseException):
    """Leave the Store in the state seen after unexpected process exit."""


CASES = {
    'calendar': (COMBINED_QUESTION, 'ETF', BEFORE_CLOSE, 'get_trade_dates', '-0.0143'),
    'price': (COMBINED_QUESTION, 'ETF', BEFORE_CLOSE, 'cmd_history_quotation', '-0.83'),
    'nav': (COMBINED_QUESTION, 'ETF', BEFORE_CLOSE, 'etf_pcf', '-0.0143'),
    'financial': (DATED_QUESTION, '个股', ETF_CUTOFF, 'basic_data_service', '经营现金流'),
    'account_policy': (DATED_QUESTION, '个股', ETF_CUTOFF, 'get_limit_policy', '经营现金流'),
    'catalogue': (ETF_DIRECTORY_QUERY, 'ETF', ETF_CUTOFF, 'report_query', '公告目录'),
}


def install_retained_transport(stack, agent, case, calls):
    """Keep production query admission and journal on both sides of the restart."""
    is_financial = case in ('financial', 'account_policy')
    archive = retained_inputs() if is_financial else retained_calculation_inputs()

    def business_response(task, request, timeout, event):
        url = urlsplit(request.full_url)
        operation = url.path.rsplit('/', 1)[-1]
        parameters = json.loads(request.data) if request.data is not None else dict(parse_qsl(url.query))
        calls.append(event['fingerprint'])
        event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
        if is_financial:
            return scoped_retained_reply(archive, operation, parameters)
        if operation == 'report_query':
            assert parameters == archive['directory']['request']
            return archive['directory']['response']
        if operation == 'date_sequence':
            return scoped_etf_nav_reply(archive['nav'], parameters)
        return scoped_retained_reply(({}, archive['prices'], archive['calendar']), operation, parameters)

    def official_response(task, code, started, timeout, event, **options):
        calls.append(event['fingerprint'])
        return replay_public_nav(archive, task, code, started, timeout, event, **options)

    stack.enter_context(patch.object(agent.collector, '_http_json', side_effect=business_response))
    stack.enter_context(patch.object(agent.collector, '_wait_ifind_metadata'))
    if not is_financial:
        stack.enter_context(patch('researchhelper.source_transport.official_etf', side_effect=official_response))


def open_research(stack, store, case, calls):
    agent = ResearchAgent(ResearchArchive(store), ROOT, offline=False, isolate_sources=True,
        require_argument_support=True, draft_first=True, source_credentials={'ifind': {'access_token': 'fixture-access'}})
    stack.callback(agent.close_runtime)
    install_retained_transport(stack, agent, case, calls)
    return agent


@pytest.mark.parametrize('case', CASES)
def test_completed_query_and_budget_survive_restart(tmp_path, case):
    question, entry, cutoff, operation, expected_text = CASES[case]
    calls = []
    with ExitStack() as stack:
        stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('Use retained data only')))
        with ExitStack() as first:
            agent = open_research(first, tmp_path, case, calls)
            with patch('researchhelper.engine.now', return_value=cutoff):
                task = agent.create(question, entry=entry, research_mode='current', owner_account='admin')
            route = direct_readout(task)
            assert route is not None
            save = agent.archive.save

            def commit_before_exit(current, *args, **kwargs):
                result = save(current, *args, **kwargs)
                event = next((row for row in current['ledger']['dispatches']
                              if row.get('operation') == operation and row.get('response_snapshot_hash')), None)
                if current['status'] == '运行中' and event is not None:
                    raise InterruptedAfterCommit
                return result

            with patch.object(agent.archive, 'save', side_effect=commit_before_exit), pytest.raises(InterruptedAfterCommit):
                agent.start(task['id'])
            checkpoint = agent.archive.get(task['id'])
            assert checkpoint['status'] == '运行中' and not checkpoint['delivery']
            completed = next(row for row in checkpoint['ledger']['dispatches'] if row['operation'] == operation)
            responses = [row for row in checkpoint['source_records'] if row['operation'] == operation]
            assert len(responses) == 1, (operation, completed['status'])
            retained, = responses
            assert current_query_reference(checkpoint, retained)
            assert retained['hash'] == completed['response_snapshot_hash']
            assert retained['question_ids'] and retained['request_purposes']

        agent = open_research(stack, tmp_path, case, calls)
        agent.recover_interrupted()
        assert agent.archive.get(task['id'])['status'] == '失败'
        result = agent.start(task['id'])
        assert result.get('delivery'), result.get('error')
        assert result['version'] == task['version'] and result['cutoff'] == task['cutoff']
        assert result['batch'] == checkpoint['batch'] + 1
        assert calls.count(completed['fingerprint']) == 1
        assert result['ledger']['used']['data_requests'] == len(calls)
        assert result['ledger']['used']['model_calls'] == 0
        assert retained['id'] in {row['id'] for row in result['source_records']}
        saved = agent.export(task['id'])
        assert saved['html_integrity_verified'] and expected_text in saved['html']


def test_unconfirmed_request_is_not_resent_on_resume(tmp_path):
    question, entry, cutoff, operation, _ = CASES['catalogue']
    calls = []
    with ExitStack() as stack:
        stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('Use retained data only')))
        with ExitStack() as first:
            agent = open_research(first, tmp_path, 'catalogue', calls)
            with patch('researchhelper.engine.now', return_value=cutoff):
                task = agent.create(question, entry=entry, research_mode='current')
            with patch.object(agent.collector, '_http_json', side_effect=InterruptedAfterCommit), pytest.raises(InterruptedAfterCommit):
                agent.start(task['id'])
            checkpoint = agent.archive.get(task['id'])
            event, = checkpoint['ledger']['dispatches']
            assert event['operation'] == operation and event['status'] == '已派发'
            assert not checkpoint['source_records'] and checkpoint['ledger']['used']['data_requests'] == 1
        agent = open_research(stack, tmp_path, 'catalogue', calls)
        agent.recover_interrupted()
        result = agent.start(task['id'])
        assert result.get('delivery') and not calls
        assert not result['source_records'] and result['ledger']['used']['data_requests'] == 1
        assert '未取得' in agent.export(task['id'])['html']


def test_cancel_before_completed_commit_keeps_ledger_without_adopting_response(tmp_path):
    question, entry, cutoff, operation, _ = CASES['catalogue']
    calls = []
    with ExitStack() as stack:
        stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('Use retained data only')))
        agent = open_research(stack, tmp_path, 'catalogue', calls)
        with patch('researchhelper.engine.now', return_value=cutoff):
            task = agent.create(question, entry=entry, research_mode='current')
        transport = agent.collector._http_json

        def cancelled_before_return(*args, **kwargs):
            reply = transport(*args, **kwargs)
            agent.cancel(task['id'])
            return reply

        with patch.object(agent.collector, '_http_json', side_effect=cancelled_before_return):
            result = agent.start(task['id'])
        event, = result['ledger']['dispatches']
        assert event['operation'] == operation and event['fingerprint'] in calls
        assert len(calls) == result['ledger']['used']['data_requests'] == 1
        assert result['status'] == '已取消' and not result['source_records'] and not result['delivery']


def test_a_new_skill_session_resumes_from_the_committed_response(tmp_path):
    question, entry, cutoff, operation, _ = CASES['catalogue']
    calls = []
    with ExitStack() as stack:
        stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('Use retained data only')))
        original = Session(tmp_path, allow_network=True, source_credentials={'ifind': {'access_token': 'fixture-access'}})
        stack.callback(original.agent.close_runtime)
        with patch('researchhelper.engine.now', return_value=cutoff):
            task = original.create(question, entry=entry, research_mode='current')
        save = original.archive.save

        def interrupted_after_snapshot(current, *args, **kwargs):
            result = save(current, *args, **kwargs)
            if current['status'] == '运行中' and any(row['operation'] == operation for row in current['source_records']):
                raise InterruptedAfterCommit
            return result

        with ExitStack() as first:
            install_retained_transport(first, original.agent, 'catalogue', calls)
            first.enter_context(patch.object(original.archive, 'save', side_effect=interrupted_after_snapshot))
            with pytest.raises(InterruptedAfterCommit):
                original.draft(task['id'])
        assert original.status(task['id'])['status'] == '运行中'
        original.agent.close_runtime()
        resumed = Session(tmp_path)
        stack.callback(resumed.agent.close_runtime)
        result = resumed.resume(task['id'])
        assert result.get('delivery') and result['batch'] == 2
        assert result['version'] == task['version'] and result['cutoff'] == task['cutoff']
        assert result['ledger']['used']['data_requests'] == len(calls) == 1
        assert result['ledger']['used']['model_calls'] == 0
        assert '公告目录' in resumed.archive.report(task['id'], result['version'])['html']


def test_a_live_skill_session_keeps_its_task_while_another_task_can_deliver(tmp_path):
    question, entry, cutoff, _, _ = CASES['catalogue']
    calls = []
    with ExitStack() as stack:
        stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('Use retained data only')))
        current = Session(tmp_path, allow_network=True, source_credentials={'ifind': {'access_token': 'fixture-access'}})
        other = Session(tmp_path)
        stack.callback(current.agent.close_runtime)
        stack.callback(other.agent.close_runtime)
        install_retained_transport(stack, current.agent, 'catalogue', calls)
        with patch('researchhelper.engine.now', return_value=cutoff):
            task = current.create(question, entry=entry, research_mode='current')
        different = other.create('解释现金流与会计利润的关系。')
        transport = current.agent.collector._http_json

        def ensure_exclusive_execution(*args, **kwargs):
            saved = other.status(task['id'])
            assert saved['status'] == '运行中'
            with pytest.raises(LocalStateError, match='正在其他会话执行'):
                other.resume(task['id'])
            assert other.status(task['id']) == saved
            result = other.draft(different['id'], report={
                'title': '现金流与会计利润', 'conclusion': '收付时点与权责发生时点可能不同。', 'logics': []})
            assert result.get('delivery') and result['ledger']['used']['data_requests'] == 0
            return transport(*args, **kwargs)

        with patch.object(current.agent.collector, '_http_json', side_effect=ensure_exclusive_execution):
            result = current.draft(task['id'])
        assert result.get('delivery') and result['batch'] == 1
        assert result['ledger']['used']['data_requests'] == len(calls) == 1
