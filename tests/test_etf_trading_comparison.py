import json
from decimal import Decimal

import pytest

from researchhelper import etf_trading
from researchhelper.contracts import ContractError, fingerprint, now
from researchhelper.draft_analysis import analyze
from researchhelper.draft_readout import etf_product_readout
from researchhelper.draft_sources import observations
from researchhelper.draft_workflow import direct_readout
from researchhelper.etf_comparison import comparison_scope, filing_requests
from researchhelper.etf_sources import parse_snapshot as parse_pcf
from researchhelper.evidence import _record_current_acquisition
from researchhelper.source_catalogue import official_record_label

SH_CODE = '510300.SH'
SZ_CODE = '159925.SZ'
QUESTION = f'比较沪深300ETF {SH_CODE}和{SZ_CODE}最近2个共同交易日的日均成交额和收盘折溢价。'


def research(question=QUESTION):
    return dict(id='ETF-window', version=1, batch=1, created_at=now(), cutoff='2026-11-23T10:00:00+08:00',
                question=question, questions=[dict(id='q1', required=True, question=question)], objects=[],
                research_mode='current', entry='混合', intent='综合研究', draft_first_version=1,
                official_source_expansion_version=1, official_etf_listing_version=1, official_etf_pcf_version=2,
                official_etf_trading_version=1, ledger={'dispatches': []}, source_records=[])


def retain(state, operation, code, snapshot):
    request = dict(source='official', operation=operation, parameters={'ts_code': code})
    record = dict(request, kind='api', status='成功待核验', task_id=state['id'], research_version=state['version'],
                  id=f'{code}-{operation}', obtained_at=now(), snapshot=snapshot, hash=fingerprint(snapshot),
                  request_hash=fingerprint(request))
    event = dict(kind='data_requests', status='成功', fingerprint=record['request_hash'], batch=1,
                 transport_isolated=True, network_request_started=True, network_worker_reaped=True)
    state['ledger']['dispatches'].append(event)
    state['source_records'].append(_record_current_acquisition(state, record, event))
    return record


def retained_market(state, monkeypatch, *, nav_date='2026-11-19', extra_latest=True):
    sh = [[20261118, 4.12, 800000000], [20261119, 4.1, 1000000000], [20261120, 4.07, 1400000000]]
    sz = [['2026-11-19', 4.0, 3.9, 3.9, 4.1, 0, 0, 1, 100000000],
          ['2026-11-20', 4.0, 3.8, 3.8, 4.1, 0, 0, 1, 200000000]]
    if extra_latest:
        sh.append([20261123, 9, 999])
        sz.append(['2026-11-23', 9, 9, 9, 9, 0, 0, 1, 999])
    for code, data in [(SH_CODE, dict(code='510300', kline=sh)),
                       (SZ_CODE, dict(code='0', data=dict(code='159925', picupdata=sz)))]:
        retain(state, etf_trading.DAILY, code, etf_trading.parse_snapshot(
            etf_trading.DAILY, code, json.dumps(data).encode()))
    payload = {'result': [dict(TRADE_CODE='510300', FUND_NAME='沪深300ETF甲方', TRADING_DAY='20261120',
                               PRE_TRADING_DAY='20261119', NAV='￥4.0')]}
    retain(state, 'etf_pcf', SH_CODE, parse_pcf(json.dumps(payload).encode(), SH_CODE, state['cutoff']))
    monkeypatch.setattr(etf_trading, '_nav_records', lambda code, raw: [(nav_date, 4.0)])
    return retain(state, etf_trading.NAV_HISTORY, SZ_CODE,
                  etf_trading.parse_snapshot(etf_trading.NAV_HISTORY, SZ_CODE, b'synthetic NAV response'))


def readout(state):
    facts = observations(state)
    calculations = analyze(state, facts)['results']
    return facts, calculations, etf_product_readout(state, facts, calculations)


def test_trading_uses_four_public_operations_and_no_pdf():
    state = research()
    kind, scope = direct_readout(state)
    assert kind == 'etf_comparison' and scope['codes'] == [SZ_CODE, SH_CODE]
    assert filing_requests(state, scope) == []
    operations = {(item['operation'], item['parameters']['ts_code'])
                  for item in etf_trading.trading_requests(state, scope)}
    assert operations == {(etf_trading.DAILY, SH_CODE), (etf_trading.DAILY, SZ_CODE),
                          ('etf_pcf', SH_CODE), (etf_trading.NAV_HISTORY, SZ_CODE)}
    with pytest.raises(ContractError):
        etf_trading.validate_request(state, etf_trading.DAILY, {'ts_code': '510330.SH'})


def test_daily_rows_mean_and_same_day_price_nav_use_actual_evidence(monkeypatch):
    state = research()
    nav = retained_market(state, monkeypatch)
    facts, results, report = readout(state)
    assert official_record_label(nav) == '深交所ETF单位净值'
    assert {fact['period'] for fact in facts} == {'2026-11-19', '2026-11-20'}
    means = {row['code']: Decimal(row['exact']) for row in results if row.get('comparison_kind')=='etf_turnover_mean'}
    assert means == {SH_CODE: 1200000000, SZ_CODE: 150000000}
    premiums = {row['code']: Decimal(row['display']) for row in results
                if row.get('comparison_kind')=='etf_exchange_close_premium'}
    assert premiums == {SH_CODE: Decimal('2.5'), SZ_CODE: Decimal('-2.5')}
    assert report['coverage']['trading'] == dict(turnover_days=['2026-11-19','2026-11-20'],
                                                valuation_day='2026-11-19')
    assert [part['title'] for part in report['sections']] == ['二级市场成交活跃度', '同日收盘折溢价']


@pytest.mark.parametrize('defect', ['wrong_valuation_day', 'changed_retained_cell'])
def test_missing_comparable_nav_preserves_turnover_and_reports_partial(monkeypatch, defect):
    state = research()
    nav = retained_market(state, monkeypatch, nav_date='2026-11-20' if defect=='wrong_valuation_day' else '2026-11-19')
    if defect=='changed_retained_cell':
        nav['snapshot']['rows'][0]['nav'] = '100'
    facts, calculations, report = readout(state)
    assert not any(row['field']==etf_trading.NAV for row in facts)
    assert sum(row.get('comparison_kind')=='etf_turnover_mean' for row in calculations) == 2
    assert report['coverage'] is None and '配对覆盖不足' in report['sections'][1]['analysis']


def test_deterministic_path_keeps_missing_execution_depth_unanswered(monkeypatch):
    state = research(QUESTION+'评估1000万元买单的执行条件。')
    assert direct_readout(state)[1]['trading']['ticket'] == '10000000'
    retained_market(state, monkeypatch)
    _, results, report = readout(state)
    ratios = {row['code']: row['display'] for row in results if row.get('comparison_kind')=='etf_ticket_participation'}
    assert ratios == {SH_CODE:'0.83', SZ_CODE:'6.67'}
    assert report['coverage'] is None
    assert '日均成交额的比例' in report['sections'][0]['analysis']
    assert '同步盘口核验' in report['conclusion']


def test_incomplete_time_window_and_special_requested_statistics_are_not_marked_complete():
    state = research(QUESTION.replace('最近2个共同交易日', '最近一个月'))
    assert comparison_scope(state, direct=True) is None
    assert comparison_scope(state)['trading']['unresolved_window']
    state = research(QUESTION.replace('的日均成交额', '的最低成交额'))
    assert comparison_scope(state, direct=True) is None
