from decimal import Decimal
from urllib.parse import urlsplit

import pytest

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.draft_filing_amounts import peer_filing_cells
from researchhelper.draft_workflow import direct_readout
from researchhelper.engine import ResearchAgent
from researchhelper.financial_data import half_year_filing_title
from researchhelper.financial_sources import BANK_CREDIT_IMPAIRMENT, BANK_INTEREST_INCOME, peer_financial_scope, stated_peer_financial_scope
from researchhelper.index_sources import CSI_FILES, CSI_PREFIX, industry_sample_requests, operating_report_question, validate_csi_url
from tests import ROOT


PERIODS = ['20250630', '20260630']
QUESTION = ('研究券商行业样本的经营分化。比较中信证券（600030.SH）、华泰证券（601688.SH）'
            '和招商证券（600999.SH）在2026年上半年合并报表营业收入、归母净利润的同比变化，给我一份研究报告。')


def official_statement(text, *, kind='pdf'):
    origin = dict(code='601688.SH', title='甲股份2026年半年度报告', issuer={'name': '甲股份'}, format=kind)
    snapshot = dict(official_origin=origin, status='已解析', parts=[dict(locator='第8页', text=text)])
    return dict(source='official', operation='release_original', kind='material', snapshot=snapshot)


def test_peer_plan_admits_the_named_same_period_cohort(tmp_path):
    engine = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=False, require_argument_support=True, draft_first=True)
    try:
        task = engine.create(QUESTION, entry='行业', research_mode='current')
        route, scope = direct_readout(task)
        assert route == 'peer_financial'
        assert scope['codes'] == ['600030.SH', '600999.SH', '601688.SH']
        assert scope['periods'] == PERIODS
        broad = engine.create(QUESTION + '再分析当前估值。', entry='行业', research_mode='current')
        assert direct_readout(broad) is None
        assert stated_peer_financial_scope(broad) == scope
    finally:
        engine.close_runtime()


@pytest.mark.parametrize(('question', 'index', 'count'), [
    ('研究2026年上半年A股券商行业的经营分化，写一份投研报告。', '399975.SZ', 3),
    ('看看银行板块', '399986.SZ', 3),
    ('研究A股白酒板块2026年上半年的经营分化，写一份研究报告。', '399997.SZ', 3),
    ('研究半导体今年上半年的经营分化', 'H30184.SH', 3),
    ('研究白酒和银行板块今年上半年经营分化', None, None),
    ('研究A股券商行业2026年上半年业绩，选前4家。', '399975.SZ', 4),
    ('对比2026年上半年券商和银行板块的经营情况。', None, None),
    ('研究中国银行业绩', None, None),
    ('研究券商行业2024年上半年财务。', None, None),
    ('研究2026年上半年A股券商行业的估值和业绩。', None, None),
    ('只用iFinD，研究2026年上半年券商行业的经营。', None, None),
])
def test_industry_scope_respects_the_question_without_assuming_company_codes(tmp_path, monkeypatch, question, index, count):
    monkeypatch.setattr('researchhelper.engine.now', lambda: '2026-11-22T08:00:00+00:00')
    engine = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=False, require_argument_support=True, draft_first=True)
    try:
        task = engine.create(question, research_mode='current')
        route = direct_readout(task, policy_without_model=True)
        if index is None:
            assert route is None
        else:
            name, scope = route
            assert name == 'industry_financial' and scope['index_code'] == index and scope['count'] == count
            assert scope['periods'] == PERIODS
            assert peer_financial_scope(task) is None
            assert [request['parameters'] for request in industry_sample_requests(task, scope)] == [dict(index_code=index)]
    finally:
        engine.close_runtime()


def test_current_index_weights_allow_the_new_publisher_path_only():
    base = CSI_FILES + CSI_PREFIX
    url = base + 'closeweight/399975closeweight.xls?20261103160000'
    validate_csi_url(url, '399975.SZ', 'weights')
    validate_csi_url(base + 'closeweightlintiao/20261030/399975closeweight.xls', '399975.SZ', 'weights')
    with pytest.raises(ContractError):
        validate_csi_url(url, '399986.SZ', 'weights')
    with pytest.raises(ContractError):
        validate_csi_url(url.replace(urlsplit(url).netloc, 'example.invalid'), '399975.SZ', 'weights')


def test_mixed_columns_require_the_group_periods_and_currency():
    text = ('合并及母公司利润表\n2026 年 1 月 1 日至 6 月 30 日止期间\n本集团 本公司\n'
            '2026 年 1-6 月\n人民币元\n2025 年 1-6 月\n人民币元\n'
            '2026 年 1-6 月\n人民币元\n2025 年 1-6 月\n人民币元\n'
            '一、营业总收入 1,200.00 1,000.00 900.00 800.00\n'
            '归属于母公司股东的净利润 160.00 130.00 100.00 80.00')
    cells = peer_filing_cells(official_statement(text), PERIODS)
    assert [(r['period'], r['value']) for r in cells] == [
        ('2026-06-30', Decimal('1200')), ('2025-06-30', Decimal('1000')),
        ('2026-06-30', Decimal('160')), ('2025-06-30', Decimal('130'))]
    assert all(row['locator']['statement'] == '合并报表' for row in cells)
    for wrong in (text.replace('本集团 本公司', '本公司 本集团'),
                  text.replace('2025 年 1-6 月', '2024 年 1-6 月'),
                  text.replace('人民币元', '港币元', 1)):
        assert not peer_filing_cells(official_statement(wrong), PERIODS)


def test_relative_columns_require_an_explicit_half_year_and_preserve_missing_cells():
    span = '本报告期、报告期、本期 指 2026年1月1日至2026年6月30日'
    statement = ('合并利润表 - 续\n编制单位：甲股份 单位：人民币元\n'
                 '项目 附注 本期发生额\n（未经审计）\n上期发生额\n（未经审计）\n'
                 '1．归属于母公司所有者的净利润 （160.00） 130.00\n一、营业总收入 - 900.00')
    cells = peer_filing_cells(official_statement(span + '\n' + statement), PERIODS)
    assert [cell['value'] for cell in cells] == [Decimal('-160.00'), Decimal('130.00')]
    assert all(cell['locator']['period_basis']['quote'] == span for cell in cells)
    assert not peer_filing_cells(official_statement(statement), PERIODS)


def test_peer_revenue_components_keep_parent_and_nested_investment_rows_out():
    text = ('本报告期 指 2026年1月1日至2026年6月30日\n合并利润表\n单位：人民币元\n'
            '项目 附注 本期发生额 上期发生额\n一、营业总收入 1,000.00 800.00\n'
            '手续费及佣金净收入 九、43 170.00 160.00\n其中：经纪业务手续费净收入 65.00 70.00\n'
            '利息净收入 20.00 10.00\n投资收益 350.00 400.00\n'
            '其中：对联营公司和合营公司的\n投资收益 20.00 18.00\n'
            '公允价值变动收益（损失以“-”号填列） 400.00 -20.00\n'
            '二、营业总支出 202.00 200.00\n归属于母公司股东的净利润 300.00 200.00\n')
    baseline = peer_filing_cells(official_statement(text), PERIODS)
    result = peer_filing_cells(official_statement(text), PERIODS, revenue_bridge=True)
    assert len(baseline) == 4
    fields = {key: [cell['value'] for cell in result if cell['field'] == key]
              for key in ('filing.peer_fee_income', 'filing.peer_net_interest', 'filing.peer_investment_income', 'filing.peer_fair_value_income')}
    assert fields == {'filing.peer_fee_income': [Decimal('170'), Decimal('160')],
                      'filing.peer_net_interest': [Decimal('20'), Decimal('10')],
                      'filing.peer_investment_income': [Decimal('350'), Decimal('400')],
                      'filing.peer_fair_value_income': [Decimal('400'), Decimal('-20')]}


def test_group_statement_can_continue_to_the_next_page_with_a_wrapped_profit_row():
    source = official_statement('')
    pages = [
        ('第5页', '报告期 指 2026 年 1 月 1 日-2026 年 6 月 30 日\n元、万元、亿元 指 人民币元、人民币万元、人民币亿元'),
        ('第7页', '四、主要会计数据和财务指标\n本报告期 上年同期 本报告期比上年同期增减\n'
                 '归属于上市公司股东的净利润（元） 320.00 220.00 45.45%\n'
                 '归属于上市公司股东的扣除非经常性\n损益的净利润（元） 310.00 200.00 55.0%\n'
                 '注：公司营业总收入不包括证券业务产生的投资收益及公允价值变动损益。'),
        ('第32页', '3、合并利润表\n单位：元\n项目 2026 年半年度 2025 年半年度\n'
                  '一、营业总收入 1,100.00 800.00\n其中：营业收入 240.00 200.00\n'
                  '手续费及佣金净收入 700.00 480.00\n利息净收入 160.00 120.00\n二、营业总成本 400.00 380.00'),
        ('第33页', '加：其他收益 20.00 15.00\n投资收益 40.00 44.00\n'
                  '公允价值变动收益 29.00 30.00\n减：所得税费用 100.00 90.00\n（二）按所有权归属分类\n'
                  '1.归属于母公司股东的净利润\n（净亏损以“—”号填列） 320.00 220.00'),
    ]
    source['snapshot']['parts'] = [dict(locator=where, text=text) for where, text in pages]
    read = peer_filing_cells(source, PERIODS, revenue_bridge=True)
    current = {row['field']: row for row in read if row['period'] == '2026-06-30'}
    assert [current[key]['value'] for key in ('ths_revenue_stock', 'ths_np_atoopc_stock', 'filing.peer_adjusted_profit')] == [
        Decimal('1100'), Decimal('320'), Decimal('310')]
    assert current['ths_np_atoopc_stock']['locator']['part'] == '第33页'
    assert current['ths_np_atoopc_stock']['locator']['currency_basis']['part'] == '第5页'
    assert current['ths_revenue_stock']['locator']['comparison_note']['kind'] == 'revenue_excludes_investment'
    assert 'filing.peer_investment_income' not in current
    source['snapshot']['parts'][-1]['locator'] = '第34页'
    assert not {'ths_np_atoopc_stock', 'filing.peer_adjusted_profit'}.intersection(
        row['field'] for row in peer_filing_cells(source, PERIODS, revenue_bridge=True))


def test_group_revenue_total_can_follow_its_contributing_rows():
    text = ('本报告期 指 2026年1月1日至2026年6月30日\n合并利润表\n'
            '截至 2026 年 6 月 30 日止六个月期间\n( 金额单位：人民币元 )\n'
            '2026 年度\n( 未经审计 )\n2025 年度\n( 未经审计 )\n营业收入\n'
            '手续费及佣金净收入 47 360 100\n利息净收入 48 50 40\n'
            '投资 ( 损失 ) / 收益 49 (20) 60\n公允价值变动收益 / ( 损失 ) 50 430 (10)\n'
            '营业总收入 820 190\n营业总支出 377 70\n归属于母公司所有者的净利润 300 80')
    current = {row['field']: row['value'] for row in peer_filing_cells(official_statement(text), PERIODS, revenue_bridge=True)
               if row['period'] == '2026-06-30'}
    assert current == dict(zip(
        ('ths_revenue_stock','ths_np_atoopc_stock','filing.peer_fee_income','filing.peer_net_interest',
         'filing.peer_investment_income','filing.peer_fair_value_income'),
        map(Decimal, ('820', '300', '360', '50', '-20', '430')), strict=True))


def test_bank_parent_profit_uses_only_group_columns_under_profit_allocation():
    text = ('合并及公司利润表\n截至2026年6月30日止六个月\n'
            '（除特别注明外，金额单位均为人民币百万元）\n本集团 本行\n'
            '附注 2026年 2025年 2026年 2025年\n'
            '利息净收入 30 340 310 325 300\n营业收入 460 420 410 380\n'
            '信用减值损失 38 (127) (104) (121) (100)\n'
            '净利润归属于：\n母公司股东 173 168\n少数股东 2 1')
    source = official_statement(text)
    rows = peer_filing_cells(source, PERIODS, bank_profit_bridge=True)
    current = {row['field']: row for row in rows if row['period'] == '2026-06-30'}
    assert {field: row['value'] for field,row in current.items()} == {
        'ths_revenue_stock': Decimal('460'), 'ths_np_atoopc_stock': Decimal('173'),
        BANK_INTEREST_INCOME: Decimal('340'), BANK_CREDIT_IMPAIRMENT: Decimal('-127')}
    assert all(row['unit'] == '百万元' for row in rows)
    source['snapshot']['parts'][0]['text'] = text.replace('本集团 本行', '本行 本集团')
    assert not peer_filing_cells(source, PERIODS, bank_profit_bridge=True)


def test_operating_filing_uses_paired_group_revenue_cost_and_cash_with_currency_evidence():
    from researchhelper.draft_analysis import peer_gross_profit_bridge
    from researchhelper.financial_sources import PEER_SALES_RECEIPTS

    source = official_statement('')
    source['snapshot']['parts'] = [dict(locator=page, text=text) for page, text in (
        ('第8页', '合并利润表\n2026年1—6月\n单位：元\n项目 2026年半年度 2025年半年度\n'
                  '一、营业总收入 1,300 1,100\n其中：营业收入 注释 61 1,200 1,000\n'
                  '其中：营业成本 注释 61 480 450\n归属于母公司股东的净利润 120 100'),
        ('第10页', '合并现金流量表\n单位：元\n项目 2026年半年度 2025年半年度\n'
                   '一、经营活动产生的现金流量：\n销售商品、提供劳务收到的现金 980 1,020'),
        ('第11页', '经营活动产生的现金流量净额 -20 70\n二、投资活动产生的现金流量：\n'
                   '投资活动产生的现金流量净额 120 130\n母公司现金流量表\n'
                   '一、经营活动产生的现金流量：\n经营活动产生的现金流量净额 9,999 9,999'),
        ('第24页', '4、记账本位币\n本公司采用人民币作为记账本位币。'),
    )]
    cells = peer_filing_cells(source, PERIODS, operating_drivers=True)
    current = {row['field']: row for row in cells if row['period'] == '2026-06-30'}
    assert {key: row['value'] for key, row in current.items()} == {
        'ths_revenue_stock': Decimal('1200'), 'ths_operating_cost_stock': Decimal('480'),
        'ths_np_atoopc_stock': Decimal('120'), 'ths_ncf_from_oa_stock': Decimal('-20'),
        PEER_SALES_RECEIPTS: Decimal('980'),
    }
    assert current['ths_ncf_from_oa_stock']['locator']['part'] == '第11页'
    assert current['ths_operating_cost_stock']['locator']['currency_basis']['part'] == '第24页'
    facts = {row['field'] + row['period']: dict(row, source='official', source_operation='release_original',
        currency='RMB', artifact_id='original', raw_value=str(row['value'])) for row in cells}
    sales, cost = (dict(data_refs=[field + period for period in ('2025-06-30', '2026-06-30')])
                   for field in ('ths_revenue_stock', 'ths_operating_cost_stock'))
    result = peer_gross_profit_bridge(sales, cost, facts)
    assert result['margin'] == 60 and result['prior_margin'] == 55
    assert result['revenue_effect'] == 110 and result['margin_effect'] == 60 and result['gross_change'] == 170
    facts[sales['data_refs'][1]]['locator']['quote'] = '营业总收入 1,300 1,100'
    assert peer_gross_profit_bridge(sales, cost, facts) is None
    source['snapshot']['parts'][-1]['text'] = '4、记账本位币\n本公司采用美元作为记账本位币。'
    assert not peer_filing_cells(source, PERIODS, operating_drivers=True)


def test_group_statement_continues_across_pages_with_literal_notes_and_same_column_order():
    from researchhelper.financial_sources import PEER_PURCHASE_PAYMENTS, PEER_SALES_RECEIPTS

    source = official_statement('')
    header = '项目 附注 2026 年半年度 2025 年半年度'
    pages = source['snapshot']['parts'] = [dict(locator=page, text=text) for page, text in (
        ('第8页', '3、合并利润表\n单位：元\n'+header),
        ('第9页', header+'\n一、营业总收入 七、61 1,310 1,100\n其中：营业收入 七、61 1,200 1,000\n'
                  '其中：营业成本 七、61 480 450\n三、营业利润 / 210 150'),
        ('第10页', header+'\n减：所得税费用 / 30 25\n（二）按所有权归属分类\n'
                   '1.归属于母公司股东的净利润（净亏损\n以“-”号填列）\n / 160 110\n'
                   '4、母公司利润表\n其中：营业收入 9,999 9,999'),
        ('第12页', '5、合并现金流量表\n单位：元\n'+header+'\n一、经营活动产生的现金流量：\n'
                   '销售商品、提供劳务收到的现金 / 980 1,020'),
        ('第13页', header+'\n购买商品、接受劳务支付的现金 / 880 700\n'
                   '经营活动产生的现金流量净额 七、79(1) -20 70\n二、投资活动产生的现金流量：\n'
                   '6、母公司现金流量表\n经营活动产生的现金流量净额 9,999 9,999'),
        ('第24页', '4、记账本位币\n本公司以人民币为记账本位币。本财务报\n表以人民币列示。'),
    )]

    def current():
        return {row['field']: row for row in peer_filing_cells(source, PERIODS, operating_drivers=True)
                if row['period']=='2026-06-30'}

    cells = current()
    assert {key: (row['value'], row['locator']['part']) for key, row in cells.items()} == {
        'ths_revenue_stock': (Decimal('1200'), '第9页'),
        'ths_operating_cost_stock': (Decimal('480'), '第9页'),
        'ths_np_atoopc_stock': (Decimal('160'), '第10页'),
        'ths_ncf_from_oa_stock': (Decimal('-20'), '第13页'),
        PEER_SALES_RECEIPTS: (Decimal('980'), '第12页'),
        PEER_PURCHASE_PAYMENTS: (Decimal('880'), '第13页'),
    }
    assert cells['ths_np_atoopc_stock']['locator']['statement_basis']['part']=='第8页'
    assert len(cells['ths_np_atoopc_stock']['locator']['quote'].splitlines())==3
    assert cells['ths_operating_cost_stock']['locator']['currency_basis']['part']=='第24页'
    pages[2]['text'] = pages[2]['text'].replace('2025 年半年度','2024 年半年度')
    assert set(current()) == {'ths_ncf_from_oa_stock', PEER_SALES_RECEIPTS, PEER_PURCHASE_PAYMENTS}
    pages[4]['text'] = '单位：元 币种：美元\n'+pages[4]['text']
    assert not current()


def test_profit_details_keep_signed_group_lines_and_skip_unpaired_amounts():
    source = official_statement('')
    source['snapshot']['parts'] = [
        dict(locator='第8页', text='合并利润表\n2026年1—6月\n单位：人民币元\n项目 2026年半年度 2025年半年度\n'
             '营业收入 1,200 1,000\n营业成本 480 450\n研发费用 100 70\n'
             '投资收益（损失以“—”号填\n列） 25 -7\n其中：对联营企业的投资收益 21 -5\n'
             '公允价值变动收益（损失以“—”号填列） -8'),
        dict(locator='第9页', text='减：所得税费用 30 25\n'
             '1.归属于母公司股东的净利润 160 110\n2.少数股东损益（净亏损以“—”号填列） -12 8\n'
             '六、其他综合收益的税后净额 8 6\n研发费用 9,999 9,999'),
    ]
    older = peer_filing_cells(source, PERIODS, operating_drivers=True)
    upgraded = peer_filing_cells(source, PERIODS, operating_drivers=True, profit_details=True)
    core = {row['field'] for row in older}
    added = {row['field']: row for row in upgraded if row['field'] not in core and row['period'] == '2026-06-30'}
    assert {key: row['value'] for key, row in added.items()} == {
        'filing.peer_research_expense': Decimal(100),
        'filing.peer_profit_investment_income': Decimal(25),
        'filing.peer_income_tax_expense': Decimal(30),
        'filing.peer_minority_profit': Decimal(-12),
    }
    assert added['filing.peer_income_tax_expense']['locator']['part'] == '第9页'
    assert added['filing.peer_income_tax_expense']['locator']['statement_basis']['part'] == '第8页'
    assert len(added['filing.peer_profit_investment_income']['locator']['quote'].splitlines()) == 2


@pytest.mark.parametrize(('title', 'accepted'), [
    ('甲银行2026半年度报告', True), ('甲银行：2026年半年度报告（A股）', True),
    ('甲银行2026半年度报告摘要', False), ('甲银行2026半年度报告（H股）', False),
    ('甲银行2025半年度报告', False),
])
def test_half_year_title_matches_only_the_requested_full_filing(title, accepted):
    assert half_year_filing_title(title, '20260630') is accepted


def test_operating_completion_is_limited_to_the_actual_standalone_question():
    task = dict(question_origin='default',question='研究2026年上半年A股银行业的盈利分化，写一份投研报告。')
    assert operating_report_question(task)
    assert not operating_report_question(dict(task, question=task['question']+'判断下一季度估值。'))
    assert not operating_report_question(dict(task, question_origin='explicit'))


def test_broker_income_driver_question_uses_the_registered_operating_study():
    from researchhelper.draft_workflow import direct_readout
    from researchhelper.index_sources import BROKER_READING_POLICY, INDUSTRY_SAMPLE_POLICY, INDUSTRY_SAMPLE_VERSION

    phrase = '今年上半年券商业绩怎么样？营收增长主要靠手续费和利息还是投资收益？归母利润跟上了吗？给我写一份投研报告。'

    def task(question):
        return dict(question_origin='default', question=question, questions=[dict(id='q1', question=question)],
                    cutoff='2026-11-23T00:00:00+08:00', research_mode='current', intent='综合研究', draft_first_version=1,
                    **{INDUSTRY_SAMPLE_POLICY:INDUSTRY_SAMPLE_VERSION, BROKER_READING_POLICY:1})

    assert operating_report_question(task(phrase))
    name, scope = direct_readout(task(phrase))
    assert name == 'industry_financial'
    assert scope['periods'] == ['20250630', '20260630'] and scope['revenue_bridge']
    for extra in ('请拆分投行业务承销金额。', '预测明年归母净利润。'):
        extended = task(phrase+extra)
        assert not operating_report_question(extended)
        assert direct_readout(extended) is None


def test_natural_operating_prompt_can_name_covered_metrics_without_narrowing_other_questions():
    from researchhelper.index_sources import INDUSTRY_SAMPLE_POLICY, INDUSTRY_SAMPLE_VERSION, industry_operating_scope

    phrase = '半导体板块今年上半年的经营情况怎么样？重点看营收、毛利率和经营现金流，给我一份研究报告。'

    def task(question):
        return dict(question_origin='default', question=question, questions=[dict(id='Q1', question=question)],
                    cutoff='2026-11-23T00:00:00+08:00', research_mode='current', intent='综合研究',
                    **{INDUSTRY_SAMPLE_POLICY:INDUSTRY_SAMPLE_VERSION})

    selected = task(phrase)
    assert operating_report_question(selected)
    scope = industry_operating_scope(selected, direct=True)
    assert (scope['index_code'], scope['periods']) == ('H30184.SH', ['20250630', '20260630'])
    for extra in ('和存货周转率', '和估值', '，再判断政策影响'):
        extended = task(phrase.replace('和经营现金流', '和经营现金流'+extra))
        assert not operating_report_question(extended)
        assert industry_operating_scope(extended, direct=True) is None
    bank = task(phrase.replace('半导体板块', '银行板块'))
    assert not operating_report_question(bank)
    assert industry_operating_scope(bank, direct=True) is None
