"""First-report account checks follow the requested business data."""
import json
import socket
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, canonical, charge, now
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (
    _record_current_acquisition, data_request_contract, snapshot_record, validate_source_request,
)


def quote_request():
    return dict(id='close', source='ifind', operation='cmd_history_quotation', question_ids=['q1'],
                parameters=dict(codes='600519.SH', indicators='close', startdate='2026-10-15', enddate='2026-10-15',
                                functionpara=dict(Currency='RMB', CPS='1', Fill='Omit')))


def financial_request(period='20251231', *, code='600519.SH'):
    return dict(id='financial_'+period, source='ifind', operation='basic_data_service', question_ids=['q1'],
                parameters=dict(codes=code, report_period=period, statement_type='1',
                                indicators=['ths_revenue_stock','ths_np_stock']))


def run_case(folder, requests, *, question='核对600519.SH的收盘价和经营数据', research_mode='current',
             data_requests=5, policy_limit=1, policy_fails=False, refresh_configured=False):
    timeline = []
    source_credentials = {'ifind': {'refresh_token': 'fixture-only'}} if refresh_configured else {}

    def reason(task, messages, *, phase, **options):
        timeline.append(phase)
        if phase == 'draft_plan':
            return canonical(dict(data_requests=requests if timeline.count(phase)==1 else []))
        return canonical(dict(title='研究核对', conclusion='按实际取得的观测核对。',
                              logics=[dict(title='实际读数', analysis='保留可用观测。', data_refs=['D1'])]))

    def transport(task, source, operation, params, credentials):
        timeline.append(operation)
        assert source=='ifind' and credentials==source_credentials.get('ifind', {})
        validate_source_request(task, source, operation, params)
        charge(task['ledger'], 'data_requests', 1)
        if operation=='get_limit_policy':
            if policy_fails:
                raise ContractError('模拟账号策略未返回')
            payload=dict(errorcode=0, qps_limit=dict(unit='requests_per_second', default_per_function=4, account_total=8),
                request_data_limit=dict(unit='cells', rules=[dict(data_types=['basicData'], max_per_request=policy_limit)]))
        elif operation=='cmd_history_quotation':
            payload=dict(errorcode=0, tables=[dict(thscode='600519.SH', time=['2026-10-15'], table=dict(close=[112]))])
        else:
            raise AssertionError('Unexpected dependent transport: '+operation)
        record=snapshot_record(task, source, operation, params, payload)
        event=dict(kind='data_requests', source=source, operation=operation, status='成功', time=now(),
            fingerprint=record['request_hash'], batch=task['batch'], transport_isolated=True,
            network_request_started=True, network_worker_reaped=True)
        task['ledger']['dispatches'].append(event)
        return _record_current_acquisition(task, record, event)

    with patch.object(socket.socket, 'connect', side_effect=AssertionError('No external requests')):
        archive=ResearchArchive(folder)
        agent=ResearchAgent(archive, ROOT, offline=False, model=SimpleNamespace(reason=reason),
                            source_credentials=source_credentials, require_argument_support=True, draft_first=True)
        try:
            options=dict(research_mode=research_mode, limits=dict(data_requests=data_requests, model_calls=2))
            if research_mode=='historical':
                options['cutoff']='2026-10-17T14:00:00+08:00'
            task=agent.create(question, objects=['600519.SH'] if requests else [], **options)
            task['synthetic']=True
            archive.save(task)
            agent.collector.external=transport
            done=agent.start(task['id'])
            assert done['delivery']
            return done, timeline
        finally:
            agent.close_runtime()


@pytest.mark.parametrize('mode', ['current', 'historical'])
def test_concept_or_independent_price_does_not_query_account_policy(tmp_path, mode):
    concept, timeline = run_case(tmp_path/'concept', [], question='什么是债券久期？', research_mode=mode)
    assert timeline == ['draft_plan','draft_report']
    assert not concept.get('draft_source_attempts')
    quote, timeline = run_case(tmp_path/'quote', [quote_request()], research_mode=mode)
    assert timeline == ['draft_plan','cmd_history_quotation','draft_report']
    assert [fact['value'] for fact in quote['draft_report']['facts']] == [112]


@pytest.mark.parametrize('case, budget, message, policy_calls', [
    ('invalid', 5, '实际取数范围', 0),
    ('low_budget', 1, '剩余取数预算不足', 0),
    ('over_cap', 5, '基础财务单次单元格', 1),
    ('unavailable', 5, '账号策略', 1),
])
def test_unavailable_prerequisite_does_not_dispatch_financial_call_or_block_quote(
        tmp_path, case, budget, message, policy_calls):
    request=financial_request(code='600036.SH' if case=='invalid' else '600519.SH')
    requests=[request, financial_request('20241231')] if case=='unavailable' else [request]
    done, timeline=run_case(tmp_path, [*requests,quote_request()], data_requests=budget, policy_fails=case=='unavailable')
    assert timeline == ['draft_plan']+['get_limit_policy']*policy_calls+['cmd_history_quotation','draft_report']
    assert [fact['value'] for fact in done['draft_report']['facts']]==[112]
    assert all(message in row['error'] for row in done['draft_source_attempts'] if row['proposed']['id']!='close')


def test_first_report_plans_fixed_fields_before_policy_but_transport_cannot_skip_it(tmp_path):
    agent=ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True, require_argument_support=True, draft_first=True)
    try:
        task=agent.create('核对600519.SH固定财务指标', objects=['600519.SH'], research_mode='current')
        general={row['operation'] for row in data_request_contract(task)['tools']}
        draft={row['operation']:row for row in data_request_contract(task, first_report=True)['tools']}
        for op in ('basic_data_service','get_indicators','search_edb'):
            assert op not in general and draft[op]['account_policy_prerequisite']=='resolved_before_dispatch'
        with pytest.raises(ContractError, match='先取得本任务实际账号策略'):
            validate_source_request(task, 'ifind', 'basic_data_service', financial_request()['parameters'])
    finally:
        agent.close_runtime()


def test_budget_also_accounts_for_required_refresh_authentication(tmp_path):
    done,timeline=run_case(tmp_path,[financial_request()],data_requests=2,refresh_configured=True)
    assert timeline==['draft_plan','draft_report']
    assert done['ledger']['used']['data_requests']==0
    assert '剩余取数预算不足' in done['draft_source_attempts'][0]['error']
