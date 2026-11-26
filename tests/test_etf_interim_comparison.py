import json

import pytest

from researchhelper.announcement_sources import parse_catalogue, request_spec
from researchhelper.contracts import fingerprint, now
from researchhelper.draft_readout import etf_product_readout
from researchhelper.draft_workflow import direct_readout
from researchhelper.etf_comparison import (ASSETS, BENCHMARK, HOLDING_POLICY, MANAGEMENT, PERFORMANCE, REQUIRED_FIELDS,
                                           TRACKING_ERROR, comparison_scope, filing_requests, named_targets, report_cells)
from researchhelper.etf_listing import POLICY as LISTING_POLICY, listing_requests, parse_listing
from researchhelper.etf_trading import POLICY as TRADING_POLICY
from researchhelper.evidence import _record_current_acquisition, available_security_codes
from researchhelper.parallel_study import planned_questions


QUESTION = '比较沪深300ETF 510330.SH和159919.SZ在2025年上半年的管理费、托管费与跟踪表现，写一份投研报告。'
CUTOFF = '2025-09-02T12:00:00+08:00'


def task(question=QUESTION):
    return dict(question=question, cutoff=CUTOFF, objects=[], entry='混合', intent='综合研究',
                official_source_expansion_version=1, research_mode='current', draft_first_version=1,
                questions=[dict(id='q1', required=True, question=question)])


def fixture_report(*, code='510330', basis='本基金业绩比较基准为沪深300指数。', row=None):
    period = '本报告期自 2025 年 1 月 1 日起至 6 月 30 日止。'
    identity = f'基金主代码 {code}\n交易代码 {code}\n基金运作方式 交易型开放式\n业绩比较基准 {basis}'
    assets = '金额单位：人民币元\n期末基金资产净值 2,001,220,001.30'
    header = ('3.2.1 基金份额净值增长率及其与同期业绩比较基准收益率的比较\n'
              '份额净值增\n长率① 份额净值增长率标准差② 业绩比较基\n准收益率③ '
              '业绩比较基准收益率标准差④\n①－③ ②－④\n')
    row = row or '过去六个\n月 8.44% 1.13% 7.55% 1.13% 0.89% 0.00%'
    fees = ('支付基金管理人的管理人报酬按前一日基金资产净值0.15%的年费率计提。\n'
            '支付基金托管人的托管费按前一日基金资产净值0.05%的年费率计提。')
    parts = [dict(locator=f'第{page}页', text=source) for page, source in
             ((2, period), (5, identity), (6, assets), (7, header+row), (32, fees))]
    return dict(source='official', operation='release_original',
                snapshot=dict(official_origin=dict(code='510330.SH', channel='sse_fund',
                    title='甲沪深300交易型开放式指数证券投资基金2025年中期报告'), parts=parts))


def test_scoped_etf_study_does_not_shortcut_liquidity_or_expanded_questions():
    kind, scope = direct_readout(task())
    assert kind == 'etf_comparison'
    assert scope['codes'] == ['159919.SZ', '510330.SH']
    expanded = task(QUESTION+'请分析流动性和买卖价差。')
    assert comparison_scope(expanded)
    assert comparison_scope(expanded, direct=True) is None
    assert comparison_scope(task(QUESTION.replace('2025年', '2026年'))) is None


def test_independent_policy_and_fee_questions_keep_user_order_and_do_not_drop_extra_work():
    policy = '核对2025年7月30日美联储会议的联邦基金目标利率区间，并比较前一次会议的变化'
    funds = '比较今年上半年沪深300ETF 510330.SH和159919.SZ的管理费和托管费'

    def plan(question):
        return planned_questions(dict(task(question), question_origin='default', **{LISTING_POLICY:1},
                                      official_channels_version=1, official_policy_channels_version=1))

    assert [row['group'] for row in plan(policy+'；另请'+funds)] == ['policy', 'funds']
    assert [(row['group'], row['question']) for row in plan(funds+'；'+policy)] == [
        ('funds', funds), ('policy', policy)]
    assert not plan(policy+'；'+funds+'。同时分析为什么这些ETF会跑赢指数。')
    assert not plan(policy+'；'+funds+'和跟踪表现。')


def test_long_holding_question_discovers_products_and_windows_without_metric_keywords():
    question = '我想长期跟踪沪深300，华泰柏瑞和南方的沪深300ETF怎么选？给我一份研究报告。'
    study = dict(task(question), cutoff='2026-11-23T11:00:00+08:00',
                 **{HOLDING_POLICY:1, LISTING_POLICY:1, TRADING_POLICY:1})
    kind, scope = direct_readout(study)
    assert kind == 'etf_comparison' and scope['holding'] is True
    assert scope['period'] == '2026-01-01至2026-06-30'
    assert scope['names'] == ['华泰柏瑞沪深300ETF','南方沪深300ETF']
    assert scope['trading']['turnover'] and scope['trading']['premium'] and scope['trading']['sessions']==10
    assert comparison_scope(dict(study, cutoff='2026-08-31T11:00:00+08:00'), direct=True)['year']=='2025'
    assert comparison_scope(dict(study, question=question.replace('怎么选','看2024年上半年怎么选')), direct=True)['year']=='2024'

    aliases = dict(study, question='想长期持有沪深300ETF，华泰柏瑞和南方的产品怎么选？')
    assert named_targets(aliases) == scope['names']
    assert named_targets(dict(study, question='想长期持有沪深300ETF，510300还是159925这两只怎么选？')) == ['510300','159925']
    for unsupported in (question.replace('怎么选','按近3年走势怎么选'), question+'顺便分析行业利润。'):
        assert direct_readout(dict(study, question=unsupported)) is None
    assert direct_readout(task(question)) is None


def test_etf_original_keeps_metric_positions_and_ignores_mismatched_report():
    record = fixture_report()
    cells = {cell['field']: cell for cell in report_cells(record, '510330.SH', '2025')}
    assert cells.keys() == REQUIRED_FIELDS | {ASSETS}
    assert cells[PERFORMANCE]['raw'] == '8.44'
    assert cells[BENCHMARK]['locator']['benchmark_name'] == '沪深300指数'
    assert cells[BENCHMARK]['locator']['part'] == '第7页'
    assert cells[MANAGEMENT]['locator']['quote'] in record['snapshot']['parts'][-1]['text']
    assert not report_cells(fixture_report(code='510300'), '510330.SH', '2025')


@pytest.mark.parametrize('changes', [
    dict(basis='沪深300指数收益率×95%＋银行存款利率×5%'),
    dict(row='过去六个月 8.44% 1.13% 7.55% 1.13% 8.89% 0.00%'),
])
def test_no_tracking_claim_when_benchmark_or_row_is_ambiguous(changes):
    fields = {cell['field'] for cell in report_cells(fixture_report(**changes), '510330.SH', '2025')}
    assert fields.isdisjoint({PERFORMANCE, BENCHMARK})


def test_fund_contract_target_is_not_a_realized_error():
    record = fixture_report()
    part = dict(locator='第10页', text='本基金力争将年化跟踪误差控制在2%以内。')
    record['snapshot']['parts'].insert(4, part)
    assert TRACKING_ERROR not in {cell['field'] for cell in report_cells(record, '510330.SH', '2025')}
    part['text'] += '\n本报告期内，本基金日均跟踪偏离为 0.01%，年化跟踪误差为 0.37%。'
    errors = [cell for cell in report_cells(record, '510330.SH', '2025') if cell['field'] == TRACKING_ERROR]
    assert len(errors) == 1 and errors[0]['raw'] == '0.37'


def test_exchange_directory_disallows_same_period_pdf_with_another_code():
    params = dict(channel='sse_fund', code='510330.SH', start_date='2025-07-01',
                  end_date='2025-09-02', keyword='中期报告', page='1')
    lookup = json.dumps(dict(sqlId='COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C', result=[
        dict(TRADE_CODE='510330', FUND_NAME='甲沪深300交易型开放式指数证券投资基金')])).encode()
    valid = dict(SSEDATE='2025-08-30', SECURITY_CODE='510330',
                 TITLE='甲沪深300交易型开放式指数证券投资基金2025年中期报告',
                 URL='/disclosure/fund/announcement/c/new/2025-08-30/510330_20250830_TEST.pdf')
    wrong = dict(valid, URL=valid['URL'].replace('510330_', '510300_'))
    raw = json.dumps(dict(sqlId='COMMON_PL_JJXX_JJGG_L', pageHelp=dict(total=2, pageCount=1,
                                                                     data=[valid, wrong]))).encode()
    selected = parse_catalogue(raw, lookup, params, CUTOFF)
    assert selected['coverage']['excluded']['unsupported_original'] == 1
    original, = selected['entries']
    assert original['code'] == '510330.SH'
    assert original['published_at'] is None
    assert request_spec(params, 'lookup')['url'].startswith('https://query.sse.com.cn/')


def named_task(question):
    return dict(task(question), official_etf_listing_version=1, source_records=[], ledger={'dispatches': []},
                id='test-ETF', version=1, batch=1, created_at=now())


def add_listing(state, market, names):
    from researchhelper.etf_listing import _URLS

    request = dict(source='official', operation='etf_listing', parameters={'market': market})
    snapshot = dict(market=market, url=_URLS[market], rows=[
        dict(code=code+'.'+market, name=name, manager=manager+'基金管理有限公司', short='')
        for code, name, manager in names])
    record = dict(request, snapshot=snapshot, hash=fingerprint(snapshot), request_hash=fingerprint(request),
                  id=market, kind='api', status='成功待核验', task_id=state['id'], research_version=1, obtained_at=now())
    event = dict(kind='data_requests', status='成功', fingerprint=record['request_hash'], batch=1,
                 transport_isolated=True, network_request_started=True, network_worker_reaped=True)
    state['ledger']['dispatches'].append(event)
    state['source_records'].append(_record_current_acquisition(state, record, event))


def test_named_comparison_resolves_from_both_directories_before_requesting_originals():
    state = named_task('比较今年上半年甲方和乙方的沪深300ETF，重点看管理费、托管费与跟踪表现，写一份报告。')
    kind, initial = direct_readout(state)
    assert kind == 'etf_comparison' and initial['year'] == '2025'
    assert initial['names'] == ['甲方沪深300ETF', '乙方沪深300ETF'] and initial['codes'] == []
    assert [r['parameters']['market'] for r in listing_requests(state, initial['names'])] == ['SH', 'SZ']
    add_listing(state, 'SH', [('510330', '沪深300ETF甲方', '甲方'), ('510300', '沪深300ETF丙方', '丙方')])
    assert comparison_scope(state)['codes'] == []
    add_listing(state, 'SZ', [('159919', '沪深300ETF乙方', '乙方')])
    resolved = comparison_scope(state)
    assert resolved['unresolved'] == [] and resolved['codes'] == ['159919.SZ', '510330.SH']
    assert [row['source_ref'] for row in resolved['resolved']] == ['SH', 'SZ']
    assert available_security_codes(state) == resolved['codes']
    assert [r['parameters']['code'] for r in filing_requests(state, resolved)] == resolved['codes']
    assert listing_requests(state, initial['names']) == []


def test_generic_alias_reserves_the_comparison_and_preserves_the_known_product():
    state = named_task('比较沪深300ETF和乙方沪深300ETF今年上半年的管理费、托管费与跟踪表现。')
    add_listing(state, 'SH', [('510330', '沪深300ETF甲方', '甲方')])
    add_listing(state, 'SZ', [('159919', '沪深300ETF乙方', '乙方')])
    resolved = comparison_scope(state)
    assert resolved['codes'] == ['159919.SZ']
    ambiguous, = resolved['unresolved']
    assert ambiguous['status'] == '简称有歧义' and ambiguous['count'] == 2
    assert available_security_codes(state) == ['159919.SZ']
    report = etf_product_readout(state, [], [])
    assert report['coverage'] is None and '尚未全部确认' in report['conclusion']
    section, = report['sections']
    assert '510330.SH' in section['analysis'] and '159919.SZ' in section['analysis']
    assert section['title'] == '标的核对' and not section['data_refs']


def test_named_scope_accepts_plain_questions_and_mixed_code_name():
    state = named_task('510330.SH和乙方沪深300ETF今年上半年的管理费、托管费和跟踪表现分别怎样？')
    scope = direct_readout(state)[1]
    assert scope['codes'] == ['510330.SH'] and scope['names'] == ['乙方沪深300ETF']
    assert comparison_scope(dict(state, question=state['question']+'顺便分析适合定投的时点'), direct=True) is None


def test_tampered_directory_does_not_authorize_a_code():
    state = named_task('比较甲方沪深300ETF和乙方沪深300ETF2025年上半年的管理费、托管费与跟踪表现。')
    add_listing(state, 'SH', [('510330', '沪深300ETF甲方', '甲方')])
    add_listing(state, 'SZ', [('159919', '沪深300ETF乙方', '乙方')])
    state['source_records'][1]['snapshot']['rows'][0]['code'] = '159888.SZ'
    assert available_security_codes(state) == []
    assert all(x['status'] == '名录未齐' for x in comparison_scope(state)['unresolved'])


def test_sh_directory_rejects_truncated_fund_list():
    row = dict(fundType='00', fundCode='510330', secNameFull='沪深300ETF甲方',
               companyName='甲方基金管理有限公司', fundAbbr='甲方300')
    data = dict(sqlId='FUND_LIST', result=[row], pageHelp=dict(total=2))
    with pytest.raises(ValueError, match='完整列表'):
        parse_listing('SH', json.dumps(data, ensure_ascii=False).encode())
