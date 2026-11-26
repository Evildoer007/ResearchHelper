"""Explicit financial and market inputs remain available in open research."""
from lxml import html
import pytest

from researchhelper.archive import ResearchArchive
from researchhelper.draft_sources import acquisition_coverage, observations, stated_financial_supplements, stated_latest_requests
from researchhelper.draft_workflow import _serialize_request, direct_readout
from researchhelper.engine import ResearchAgent
from researchhelper.market_series import (
    PE_TTM_INDICATOR, STATED_LATEST_POLICY, VALUATION_EXCLUSIONS_VERSION, stated_mixed_latest_scopes,
)
from researchhelper.report_designer import design
from tests import ROOT
from tests.test_market_calendar_coverage import append_calendar_fixture_record


QUESTION = ('请研究贵州茅台600519.SH，对比2024年和2025年两个完整会计年度的营业收入、归母净利润、经营活动现金流，'
            '结合最新股价和市盈率分析基本面与估值是否匹配。不默认下载PDF。')


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr('researchhelper.engine.now', lambda: '2026-10-29T12:00:00+00:00')
    agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True, require_argument_support=True, draft_first=True)
    try:
        yield agent
    finally:
        agent.close_runtime()


def test_research_retains_both_annual_statements_and_recent_market_inputs(engine):
    task = engine.create(QUESTION, research_mode='current')
    coverage = acquisition_coverage(task)
    finance = stated_financial_supplements(task, coverage)
    assert [row['parameters']['report_period'] for row in finance] == ['20241231', '20251231']
    assert all(row['parameters']['statement_type'] == '1' and len(row['parameters']['indicators']) == 3 for row in finance)
    market = [_serialize_request(row, task) for row in stated_latest_requests(task, coverage)]
    assert [row['operation'] for row in market] == ['cmd_history_quotation', 'date_sequence']
    assert market[0]['parameters']['indicators'] == 'close'
    assert market[1]['parameters']['indipara'] == [{'indicator': PE_TTM_INDICATOR, 'indiparams': ['', '100']}]
    assert not direct_readout(task)
    route, scope = direct_readout(task, policy_without_model=True)
    assert route == 'valuation_research' and scope['code'] == '600519.SH'
    assert not stated_mixed_latest_scopes(dict(task, **{STATED_LATEST_POLICY: VALUATION_EXCLUSIONS_VERSION}))


@pytest.mark.parametrize('extra', ['本轮不联网。', '不用iFinD。', '不查市盈率。', '采用可比公司估值。'])
def test_no_model_valuation_route_respects_access_and_comparison_basis(engine, extra):
    task = engine.create(QUESTION+extra, research_mode='current')
    assert direct_readout(task, policy_without_model=True) is None


@pytest.mark.parametrize('question, fields', [
    ('研究600519.SH利润变化。当前收盘价和PE(TTM)处于什么水平？', ['close', PE_TTM_INDICATOR]),
    ('对比002594.SZ和300750.SZ的利润率。再比较两者最新股价和滚动市盈率。', ['close', PE_TTM_INDICATOR]),
    ('结合贵州茅台当前股价和市盈率分析盈利。', ['close', PE_TTM_INDICATOR]),
    ('分析600519.SH的利润，结合最新股价和市盈率分析估值。不查市盈率。', ['close']),
    ('分析600519.SH的利润，如果最新股价和市盈率假定不变。', []),
    ('研究600519.SH利润。引用“结合最新股价和市盈率分析”。', []),
    ('对比002594.SZ和300750.SZ利润，结合最新股价分析估值。', []),
    (QUESTION+'本轮不联网。', []),
    (QUESTION+'不用iFinD。', []),
])
def test_market_inputs_preserve_subject_and_access_scope(engine, question, fields):
    task = engine.create(question, research_mode='current')
    assert [row['indicator'] for row in stated_mixed_latest_scopes(task)] == fields


def test_readings_are_cited_in_prose_without_a_second_raw_value_line(engine):
    task = engine.create('研究600519.SH利润变化。结合最新股价和市盈率分析估值。', research_mode='current')
    day = '2026-10-29'
    for row in stated_latest_requests(task, acquisition_coverage(task)):
        wire = _serialize_request(row, task)
        field, value = ('close', 1331.25) if wire['operation'] == 'cmd_history_quotation' else (PE_TTM_INDICATOR, 20.53)
        append_calendar_fixture_record(task, wire['operation'], wire['parameters'], dict(errorcode=0, tables=[
            dict(thscode='600519.SH', time=[day], table={field: [value]})]))
    engine.archive.save(task)
    refs = [row['id'] for row in observations(task)]
    report = dict(title='贵州茅台估值观察', conclusion='估值支撑取决于滚动利润的变化。', logics=[
        dict(title='估值与盈利', analysis='2026年10月29日收盘价1331.25元，PE(TTM)读数20.53。', data_refs=refs)])
    result = engine.start(task['id'], dict(data_requests=[], draft_report=report))
    assert result.get('delivery')
    standard = html.document_fromstring(design(engine.archive.report(task['id'], 1)['content'], ROOT)['html'])
    logic = standard.get_element_by_id('logic-1')
    assert len(logic.xpath('.//a[@data-observation-id]')) == 2
    assert not logic.xpath('.//p[@class="measured-reading"]')
    assert '未确认' not in logic.text_content()

@pytest.mark.parametrize('question, permitted', [
    (QUESTION, True),
    ('600519.SH现在的估值贵不贵？只用iFinD。', True),
    ('600519.SH的估值处于什么水平？', True),
    ('只核对600519.SH最新的市盈率。', False),
    ('600519.SH估值是否合理，采用近三年市盈率。', False),
    ('600519.SH近三年估值分位是多少？', False),
    ('比较600519.SH与000858.SZ估值是否合理。', False),
    ('600519.SH估值是否合理。别查历史估值。', False),
    ('600519.SH估值是否合理。仅用本地已有资料。', False),
    ('600519.SH估值是否合理。不要用iFinD。', False),
    ('600519.SH估值是否合理。只按市净率分析。', False),
    ('600519.SH的估值是否合理？采用可比公司估值。', False),
    ('研究600519.SH。引用“估值是否合理”。', False),
    ('回看600519.SH截至2025年11月30日的估值是否合理。', False),
])
def test_valuation_benchmark_respects_requested_subject_metric_and_period(engine, question, permitted):
    from researchhelper.market_series import valuation_history_requests, valuation_history_scope
    task = engine.create(question, research_mode='current')
    scope = valuation_history_scope(task)
    requests = valuation_history_requests(task, scope) if scope else []
    assert bool(requests) == permitted
    if requests:
        from researchhelper.market_series import validate_series_request
        recent, older = [row['parameters'] for row in requests]
        assert recent['codes'] == older['codes'] == '600519.SH'
        assert recent['enddate'] == '2026-10-29'
        assert older['enddate'] < recent['startdate']
        assert recent['indipara'] == older['indipara'] == [{'indicator': PE_TTM_INDICATOR, 'indiparams': ['', '100']}]
        for params in (recent, older):
            validate_series_request(task, 'date_sequence', params)
    if scope:
        assert not valuation_history_scope(dict(task, research_mode='historical'))


def _valuation_history_record(task, mismatch=None):
    from datetime import date
    from researchhelper.market_series import valuation_history_requests, valuation_history_scope

    requests = valuation_history_requests(task, valuation_history_scope(task))
    months = [date(2025, 10, 24), date(2025, 11, 24), date(2025, 12, 24),
              *(date(2026, month, 24) for month in range(1, 10))]
    cells = [(d.replace(day=3).isoformat(), 100) for d in months]
    cells += [(d.isoformat(), 10 + i) for i, d in enumerate(months)]
    if mismatch == 'missing':
        cells = [pair for pair in cells if pair[0][:7] != '2026-02']
    elif mismatch == 'negative':
        cells[-1] = cells[-1][0], -1
    cells.extend([('2026-10-28', 15), ('2026-10-29', None)])
    records = []
    for request in requests:
        params = request['parameters']
        selected = [pair for pair in cells if params['startdate'] <= pair[0] <= params['enddate']]
        if params['enddate'] == '2026-10-29':
            selected.append(('2026-10-30', 200))  # Out of the request window.
        days, values = zip(*sorted(selected))
        payload = dict(errorcode=0, tables=[dict(thscode='600519.SH', time=list(days),
                       table={PE_TTM_INDICATOR: list(values)})])
        record = append_calendar_fixture_record(task, 'date_sequence', params, payload)
        record['data_need_ids'] = [request['id']]
        records.append(record)
    return records


def test_valuation_position_is_reproducible_and_report_uses_the_monthly_refs(engine):
    import json
    from researchhelper.draft_analysis import VALUATION_POSITION, analyze, current_analysis
    from researchhelper.draft_workflow import _writing_messages

    task = engine.create('600519.SH的估值是否合理？', research_mode='current')
    records = _valuation_history_record(task)
    facts = observations(task)
    assert len(facts) > 13
    task['draft_analysis'] = analyze(task, facts)
    checked = current_analysis(task, facts)
    position, = [row for row in checked['results'] if row.get('comparison_kind') == VALUATION_POSITION]
    assert position['value'] == 50
    assert position['period'] == '2026-10-28'
    assert position['window_start'] == '2025-10-24' and position['window_end'] == '2026-09-24'
    assert len(position['data_refs']) == 13 and position['sample_count'] == 12
    messages = _writing_messages(task, facts)
    payload = json.loads(messages[1]['content'])
    views = payload['data']['observations']
    assert all(view['frequency'] == '每月最后一个可用读数' for view in views)
    assert sum(len(view['rows']) for view in views) == 13
    assert 'valuation_history_position' in messages[0]['content']
    assert set(position['data_refs']) == {row[0] for view in views for row in view['rows']}

    engine.archive.save(task)
    proposal = dict(title='贵州茅台估值观察', conclusion='滚动估值位于本标的历史样本中段。', logics=[
        dict(title='估值位于历史样本中段', analysis='滚动市盈率处于近12个月历史样本的50%分位，盈利修复的幅度仍决定当前倍数能否维持。',
             calculation_refs=[position['id']], source_refs=[record['id'] for record in records])])
    result = engine.start(task['id'], dict(data_requests=[], draft_report=proposal))
    assert result['delivery']
    content = engine.archive.report(task['id'], 1)['content']
    paper = html.document_fromstring(design(content, ROOT)['html'])
    logic = paper.get_element_by_id('logic-1')
    assert '50%' in logic.text_content()
    assert logic.xpath('.//a[contains(text(), "50")]')
    from researchhelper.draft_visuals import logic_figure_plan
    figures = logic_figure_plan(content)
    assert any(set(figure['data_refs']) == set(position['data_refs']) for figure in figures), figures


@pytest.mark.parametrize('mismatch', ['missing', 'negative'])
def test_insufficient_comparables_never_become_a_historical_percentile(engine, mismatch):
    from researchhelper.draft_analysis import VALUATION_POSITION, analyze
    task = engine.create(QUESTION, research_mode='current')
    _valuation_history_record(task, mismatch)
    facts = observations(task)
    result = analyze(task, facts)
    assert not any(row.get('comparison_kind') == VALUATION_POSITION for row in result['results'])
    from researchhelper.draft_numeric_review import review_draft_quantities

    proposal = dict(logics=[dict(title='估值判断', analysis=(
        '当前滚动市盈率处于过去12个月历史样本的50%分位。盈利修复仍取决于需求变化。'))])
    review_draft_quantities(task, proposal, facts, result['results'])
    assert proposal['logics'][0]['analysis'] == '盈利修复仍取决于需求变化。'


def test_failed_history_still_allows_the_requested_latest_pe(engine):
    from researchhelper.market_series import valuation_history_requests, valuation_history_scope

    task = engine.create(QUESTION, research_mode='current')
    history = valuation_history_requests(task, valuation_history_scope(task))
    coverage = acquisition_coverage(task, history)
    latest = stated_latest_requests(task, coverage)
    assert any(request['operation'] == 'date_sequence' for request in latest)

def _recent_attribution_records(task, prices, multiples, *, dates=None):
    from researchhelper.market_series import valuation_close_request, valuation_history_requests, valuation_history_scope

    scope = valuation_history_scope(task)
    quote = valuation_close_request(task, scope)
    series = valuation_history_requests(task, scope)[0]
    days = ['2026-10-08', '2026-10-15', '2026-10-29']
    dates = dates or days
    for request, field, time, values in ((quote, 'close', days, prices), (series, PE_TTM_INDICATOR, dates, multiples)):
        payload = dict(errorcode=0, tables=[dict(thscode='600519.SH', time=time, table={field:list(values)})])
        record = append_calendar_fixture_record(task, request['operation'], request['parameters'], payload)
        record['data_need_ids'] = [request['id']]


def test_short_history_response_targets_missing_months_instead_of_the_outer_slice(engine, monkeypatch):
    from researchhelper.draft_workflow import _acquire_valuation_inputs
    from researchhelper.market_series import (VALUATION_HISTORY_REQUEST_PREFIX, validate_series_request,
                                              valuation_history_requests, valuation_history_scope)

    task = engine.create(QUESTION, research_mode='current')
    scope = valuation_history_scope(task)
    primary, older = valuation_history_requests(task, scope)
    _recent_attribution_records(task, [100, 120, 144], [10, 11, 12])
    task['draft_source_attempts'] = [dict(batch=task['batch'], proposed=primary)]
    monkeypatch.setattr(engine, 'offline', False)
    called = []

    def record_attempt(runtime, current, requests):
        for request in requests:
            called.append(request)
            current['draft_source_attempts'].append(dict(batch=current['batch'], proposed=request))

    monkeypatch.setattr('researchhelper.draft_workflow._acquire', record_attempt)
    _acquire_valuation_inputs(engine, task)
    _acquire_valuation_inputs(engine, task)
    actual, = [row for row in called if row['id'].startswith(VALUATION_HISTORY_REQUEST_PREFIX)]
    parameters = actual['parameters']
    assert (parameters['startdate'], parameters['enddate']) == ('2025-10-01', '2026-09-30')
    assert actual != older and actual['question_ids'] == primary['question_ids']
    validate_series_request(task, 'date_sequence', parameters)


@pytest.mark.parametrize('multiples, level, earnings', [([10, 12, 14.4], 44, 0), ([10, 11, 12], 22, 22)])
def test_valuation_attribution_sums_to_price_move_and_body_links(engine, multiples, level, earnings):
    from researchhelper.draft_analysis import VALUATION_ATTRIBUTION, VALUATION_SCENARIO, analyze
    from researchhelper.draft_visuals import logic_figure_plan

    task = engine.create(QUESTION, research_mode='current')
    _recent_attribution_records(task, [100, 120, 144], multiples)
    facts = observations(task)
    result = analyze(task, facts)
    components = [r for r in result['results'] if r.get('comparison_kind') == VALUATION_ATTRIBUTION]
    assert [r['value'] for r in components] == pytest.approx([level, earnings])
    assert all(r['period']=='2026-10-08至2026-10-29' and len(r['data_refs'])==4 for r in components)
    scenario, = [row for row in result['results'] if row.get('comparison_kind')==VALUATION_SCENARIO]
    assert scenario['value'] == pytest.approx(100*(multiples[-1]/multiples[0]-1))
    assert scenario['data_refs'] == components[0]['data_refs']
    market_refs = [row['id'] for row in facts if row['field'] in ('close', PE_TTM_INDICATOR)]
    engine.archive.save(task)
    report = dict(title='贵州茅台估值分析', conclusion='短窗价格上涨仍需经营改善支撑。', logics=[
        dict(title='价格变化分解', analysis=f'2026年10月8日至29日，估值倍数贡献约{level}个百分点。',
             data_refs=market_refs, calculation_refs=[r['id'] for r in components])])
    saved = engine.start(task['id'], dict(data_requests=[], draft_report=report))
    assert saved['delivery']
    content = engine.archive.report(task['id'],1)['content']
    html_standard = html.document_fromstring(design(content, ROOT)['html'])
    logic = html_standard.get_element_by_id('logic-1')
    assert logic.xpath(f'.//a[contains(text(), "{level}")]')
    assert logic.xpath(f'.//a[normalize-space(text())="{scenario["display"]}%"]')
    assert '若股价保持区间终点的收盘水平' in logic.text_content()
    assert not logic.xpath('.//div[contains(@class,"argument-observations")]')
    assert '未确认' not in logic.text_content()
    assert sorted(fig['field'] for fig in logic_figure_plan(content)) == (
        ['close'] if earnings==0 else ['close', PE_TTM_INDICATOR])
    assert sorted(fig['field'] for fig in logic_figure_plan(content, report_profile='detailed')) == [
        'close', PE_TTM_INDICATOR]


def test_valuation_attribution_skips_a_stale_last_pe_or_nonpositive_input(engine):
    from researchhelper.draft_analysis import PE_MULTIPLE_EFFECT, VALUATION_ATTRIBUTION, VALUATION_SCENARIO, analyze, execute
    from researchhelper.contracts import ContractError

    task = engine.create(QUESTION, research_mode='current')
    _recent_attribution_records(task, [100, 120, 144], [10, 11, 12],
                                dates=['2026-10-08', '2026-10-15', '2026-10-28'])
    result = analyze(task, observations(task))
    assert not any(r.get('comparison_kind') in (VALUATION_ATTRIBUTION, VALUATION_SCENARIO) for r in result['results'])
    with pytest.raises(ContractError):
        execute(PE_MULTIPLE_EFFECT, dict(start_price=100, end_price=120, start_pe=10, end_pe=-2))


@pytest.mark.parametrize('policy, expected', [(1, None), (2, '低约20%')])
def test_retained_valuation_can_write_a_negative_fixed_price_scenario(engine, policy, expected):
    from researchhelper.market_series import VALUATION_HISTORY_POLICY

    task = engine.create(QUESTION, research_mode='current')
    task[VALUATION_HISTORY_POLICY] = policy
    task.update(status='失败', batch=1)
    _recent_attribution_records(task, [100, 98, 88], [10, 9, 8])
    engine.archive.save(task)
    done = engine.start(task['id'], retained_data_only=True)
    assert done['delivery'] and done['ledger']['used']['model_calls'] == 0
    draft = engine.archive.report(task['id'], 1)['content']['draft']
    written = ''.join(item['analysis'] for item in draft['logics'])
    if expected:
        assert expected in written
    else:
        assert '回到起点市盈率' not in written
