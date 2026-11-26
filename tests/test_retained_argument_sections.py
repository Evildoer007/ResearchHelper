"""An empty first draft explains observed changes and keeps each figure in its own section."""
from copy import deepcopy

from lxml import html

from researchhelper.draft_analysis import analyze
from researchhelper.draft_sources import observations
from researchhelper.draft_visuals import logic_figure_plan
from researchhelper.drafts import compose, normalize_report, source_binding
from researchhelper.evidence import snapshot_record
from researchhelper.report_designer import design
from researchhelper.reports import render
from tests import ROOT
from tests.test_draft_financial_period import add_record, rehash, sample
from tests.test_financial_visual_cohorts import report_fixture


def prepare(task, proposal=None):
    facts = observations(task)
    task['draft_analysis'] = analyze(task, facts)
    draft = normalize_report(proposal or {}, task, facts, retained_data_only=True)
    task['draft_report'] = dict(draft, facts=facts, calculations=task['draft_analysis']['results'],
                                source_binding=source_binding(task))
    return compose(task)


def financial_task():
    task = sample()
    task.update(questions=[], evidence=[], knowledge={}, gaps=[], tracking=[], theses=[], calculations=[],
                source_records=[])
    fields = ('ths_revenue_stock', 'ths_operating_cost_stock', 'ths_np_atoopc_stock', 'ths_np_stock',
              'ths_ncf_from_oa_stock')
    for period, values in [('20241231', (100, 70, 9, 10, 18)), ('20251231', (90, 60, 5, 6, 24))]:
        record = add_record(task, period, values, fields=fields)
        record['snapshot']['synthetic_test_response'] = True
        rehash(record)
    return task


def test_financial_sections_compare_actual_directions_and_use_owned_figures():
    task = financial_task()
    original = deepcopy(task['source_records'])
    content = prepare(task)
    sections = content['draft']['logics']
    assert [row['title'] for row in sections] == ['收入、成本与利润', '收入成本差与净利润率', '经营现金流与利润']
    assert '归母净利润降幅大于收入降幅' in sections[0]['analysis']
    assert '收入成本差占比上升，报表净利润率下降' in sections[1]['analysis']
    assert '现金流与利润变动方向相反' in sections[2]['analysis']
    assert '经营现金流/净利润倍数上升' in sections[2]['analysis']
    assert all('若所用原值单位一致' in section['analysis'] for section in sections)
    ids = [ident for section in sections for ident in section['calculation_refs']]
    assert sorted(ids) == sorted(row['id'] for row in content['draft']['calculations'])
    assert task['source_records'] == original
    for profile in ('standard', 'detailed'):
        figures = logic_figure_plan(content, report_profile=profile)
        assert all(figure.get('paper_logic') for figure in figures)
        assert {figure['paper_logic'] for figure in figures} == (
            {'logic-2'} if profile == 'standard' else {'logic-1', 'logic-2'})
        for figure in figures:
            section = sections[int(figure['paper_logic'].split('-')[-1]) - 1]
            assert set(figure.get('calculation_refs', ())) <= set(section['calculation_refs'])
            assert set(figure['data_refs']) <= set(section['data_refs'])
        delivery = design(content, ROOT, profile=profile)
        coverage = delivery['document']['figure_coverage']
        for key, section in zip(coverage, sections, strict=True):
            assert coverage[key]['status'] in {'numeric', 'numeric_table', 'numeric_text'}
            assert coverage[key]['data_refs']
            assert set(coverage[key]['data_refs']) <= set(section['data_refs'])
        page = html.document_fromstring(delivery['html'])
        report = page.xpath('//main')[0].text_content()
        assert '对象关系与影响路径' not in report and '可比结果与相反解释' not in report
        for section in sections:
            assert report.count(section['risk']) == 1


def test_financial_scopes_get_own_names_and_no_cross_scope_change():
    task, _ = report_fixture()
    altered = task['source_records'][-1]
    altered['snapshot']['tables'][0]['table']['ths_revenue_stock'] = [60]
    rehash(altered)
    content = prepare(task)
    finance = content['draft']['logics'][0]['analysis']
    assert '合并报表2025-12-31' in finance and '母公司报表2025-12-31' in finance
    assert all('归母净利润降幅' not in item['analysis'] for item in content['draft']['logics'])
    assert any('营业收入下降' in line and '母公司' in line for line in finance.splitlines())


def market_task(prices, *, invalid_basis=False):
    task = financial_task()
    task['source_records'] = []
    start, end = '2026-10-26', '2026-10-29'
    days = [start, '2026-10-27', '2026-10-28', end]
    params = dict(codes='600519.SH', indicators='close', startdate=start, enddate=end,
                  functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
    replies = [(params, prices)]
    if invalid_basis:
        other = deepcopy(params)
        other['functionpara']['CPS'] = '2'
        replies.append((other, [104, 107, 108, 110]))
    for request, values in replies:
        payload = dict(errorcode=0, tables=[dict(thscode='600519.SH', time=days, table=dict(close=values))])
        task['source_records'].append(snapshot_record(task, 'ifind', 'cmd_history_quotation', request, payload))
    return task


def test_market_section_uses_recovery_only_when_the_same_window_has_a_lower_reading():
    for observations_read, expectation in (([104, 98, 100, 101], '样本低点回升'),
                                           ([104, 103, 102, 101], '已取得读数中的最低值')):
        content = prepare(market_task(observations_read))
        section, = content['draft']['logics']
        assert section['title'] == '收盘走势与样本区间'
        assert '期末读数较起点下降' in section['analysis']
        assert expectation in section['analysis']
        assert '归因仍需对照同期基准' in section['risk']
    for task in (market_task([104, 98, 100, None]), market_task([104, 98, 100, 101], invalid_basis=True)):
        draft = prepare(task)['draft']
        assert all('期末读数较起点' not in section['analysis'] for section in draft['logics'])


def test_authored_analysis_and_direct_checks_keep_their_own_structure():
    proposal = dict(title='已有判断', conclusion='结论保留。', logics=[dict(title='自有逻辑', analysis='原分析。')])
    authored = prepare(financial_task(), proposal)['draft']
    assert authored['logics'][0]['title'] == '自有逻辑'
    assert 'observed_argument_sections' not in authored
    lookup = market_task([104, 98, 100, 101])
    lookup.update(draft_direct_scope_version=1, draft_question_scope_version=2,
                  questions=[dict(id='q1', kind='fact', required=True, question=lookup['question'])])
    draft = prepare(lookup)['draft']
    assert draft['organization'] == 'fact'
    assert 'observed_argument_sections' not in draft


def test_data_only_fallback_keeps_comparisons_without_claiming_an_investment_answer():
    from researchhelper.planning import QUESTION_ARGUMENT_POLICY

    task = financial_task()
    task.update(draft_question_scope_version=2)
    task[QUESTION_ARGUMENT_POLICY] = 1
    task['question'] = '研究公司是否值得投资，说明主要驱动。'
    task['questions'] = [dict(id='q1', kind='investment', required=True, question=task['question'])]
    content = prepare(task)
    draft = content['draft']
    assert draft['observed_argument_sections']
    assert '不足以形成' in draft['conclusion']
    assert all('策略逻辑' not in section['display_heading'] for section in draft['logics'])
    assert any(section['calculation_refs'] for section in draft['logics'])


def test_boilerplate_does_not_take_the_place_of_an_evidence_backed_body():
    task = financial_task()
    task['draft_report_editorial_version'] = 1
    reply = dict(title='经营质量', conclusion='综上所述，建议谨慎关注。', logics=[dict(
        title='后续观察', argument=dict(claim='建议保持谨慎关注。', reasoning='后续表现仍有待进一步观察。'))])
    original = deepcopy(reply)
    content = prepare(task, reply)
    assert reply == original
    draft = content['draft']
    assert draft['observed_argument_sections']
    assert len(draft['logics']) == 3
    assert any('现金流与利润变动方向相反' in item['analysis'] for item in draft['logics'])
    assert all(item['calculation_refs'] for item in draft['logics'])
    assert '%' in draft['conclusion']
    assert not any('谨慎关注' in item['analysis'] for item in draft['logics'])


def test_incomplete_reply_recovers_only_the_missing_part_and_cited_data():
    task = financial_task()
    task['draft_report_editorial_version'] = 1
    declared = '现金流与利润变动方向不同。'
    reply = dict(title='经营变化核对', conclusion=declared, logics=[dict(
        title='一般说明', analysis='整体来看，建议谨慎关注。')])
    draft = prepare(task, reply)['draft']
    assert draft['conclusion'] == declared and draft['observed_argument_sections']

    rates = analyze(task, observations(task))['results']
    cash, = [item for item in rates if item['method'] == 'raw_change_v1'
             and '现金流' in item['label']]
    reply = dict(conclusion='本报告仅供参考。', logics=[dict(title='经营现金流',
        analysis='经营现金流的变化应结合回款进度观察。后续表现仍有待进一步观察。',
        calculation_refs=[cash['id']], risk='若回款放缓，经营现金净流入将承压。')],
        risks=['若行业需求下滑，营业收入将承压。'])
    content = prepare(task, reply)
    draft = content['draft']
    assert '经营现金流' in draft['conclusion'] and '营业收入' not in draft['conclusion']
    logic, = draft['logics']
    assert logic['analysis'] == '经营现金流的变化应结合回款进度观察。'
    assert draft['quantity_links']['conclusion']['links'][0]['calculation_refs'] == [cash['id']]
    for profile in ('standard', 'detailed'):
        page = html.document_fromstring(render(content, ROOT, report_profile=profile))
        assert '若回款放缓' not in page.get_element_by_id('logic-1').text_content()
        risk = page.get_element_by_id('risk').text_content()
        assert '若回款放缓' in risk and '若行业需求下滑' in risk


def test_strategy_paragraphs_preserve_deliberate_breaks_in_both_reports():
    content = prepare(financial_task(), dict(title='已有判断', conclusion='结论保留。', logics=[dict(
        title='盈利质量', analysis='收入承压。\n成本收缩。\n\n利润弹性取决于费用。', implication='复核期间费用。')]))
    for profile in ('standard', 'detailed'):
        page = html.document_fromstring(render(content, ROOT, report_profile=profile))
        section = page.get_element_by_id('logic-1')
        assert [node.text_content() for node in section.xpath('.//p[not(ancestor::figure) and not(ancestor::div[@data-logic-detail])]')] == [
            '收入承压。成本收缩。', '利润弹性取决于费用。复核期间费用。']


def test_strategy_count_respects_a_preference_without_creating_arguments():
    from researchhelper.publication_content import requested_strategy_count

    assert requested_strategy_count('分析比亚迪，形成3条有数据支撑的投资逻辑') == 3
    assert requested_strategy_count('策略逻辑数量设为两条') == 2
    assert requested_strategy_count('给出4条相互独立的策略逻辑，生成报告') == 4
    assert requested_strategy_count('参考策略逻辑二，补充证据') is None
    assert requested_strategy_count('不要写三条策略逻辑') is None
    for preference, expected in (('只写2条策略逻辑', 2), ('生成50条策略逻辑', 3)):
        task = financial_task()
        task['question'] += '，' + preference
        content = prepare(task)
        assert len(content['draft']['logics']) == expected
        assert len(content['draft']['facts']) == 10


def test_both_report_profiles_preserve_the_institution_masthead():
    from researchhelper.report_designer import design

    content = prepare(financial_task())
    expected = '光大证券 金融创新业务总部'
    for profile in ('standard', 'detailed'):
        report = design(content, ROOT, profile=profile)
        tree = html.document_fromstring(report['html'])
        assert tree.xpath('//header//p[@class="report-head__brand"]/text()') == [expected]
        assert [block['text'] for block in report['document']['blocks'] if block['kind'] == 'masthead'] == [expected]


def test_conclusion_without_a_report_body_keeps_the_available_financial_analysis():
    report = prepare(financial_task(), dict(title='经营对照', conclusion='经营与现金回收分开判断。'))['draft']
    assert report['conclusion'] == '经营与现金回收分开判断。'
    assert len(report['logics']) == 3
    assert '成本占收入的比重已下降' in report['logics'][0]['analysis']
    assert all(logic['calculation_refs'] for logic in report['logics'])


def test_research_body_uses_one_complete_argument_and_keeps_the_fallback_for_legacy_prose():
    from researchhelper.publication_content import DRAFT_EDITORIAL_POLICY, DRAFT_EDITORIAL_VERSION

    task = financial_task()
    task[DRAFT_EDITORIAL_POLICY] = DRAFT_EDITORIAL_VERSION
    proposal = dict(title='盈利修复的定价机会', conclusion='盈利兑现后可能获得估值修复机会。',
                    report_scope='research', logics=[dict(title='盈利修复推动估值重估',
        analysis='重复拟稿内容',
        argument=dict(claim='基准情形下盈利预期有上修机会。', evidence='近期利润和现金流走势出现分化。',
                      reasoning='若现金流回升带动盈利预测上修，股价可能重新定价。',
                      condition='利润回升须能由主业收入和回款改善解释。'),
        implication='潜在收益来自盈利预测上修。',
        detail_analysis='如果收现只是回款时点前移，盈利的上调幅度需要回看。',
        risk='利润回升局限在非经常项目时，这条策略失效。')])
    archived = deepcopy(proposal)
    content = prepare(task, proposal)
    logical = content['draft']['logics'][0]
    assert '重复拟稿内容' not in logical['analysis']
    assert '现金流走势出现分化' in logical['analysis']
    assert '重新定价' in logical['analysis']
    assert logical['detail_analysis'] == archived['logics'][0]['detail_analysis']
    assert proposal == archived
    incomplete = deepcopy(proposal)
    incomplete['logics'][0]['argument'].pop('evidence')
    assert '重复拟稿内容' in prepare(task, incomplete)['draft']['logics'][0]['analysis']
