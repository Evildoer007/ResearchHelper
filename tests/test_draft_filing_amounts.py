"""Current-report cells keep the stated duration, unit, scope and original row."""
from copy import deepcopy
import json
from zipfile import ZipFile

from lxml import html
import pytest

from tests import ROOT
from tests.test_announcement_partial_delivery import ifind_query, ifind_row
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import fingerprint
from researchhelper.draft_sources import READER_POLICY, VERSION, observations, writing_context
from researchhelper.draft_visuals import logic_figure_plan
from researchhelper.drafts import compose, normalize_report, source_binding
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import filing_record, snapshot_record, validate_filing_original_request
from researchhelper.pdf_material import EXTRACT_VERSION, TEXT_TABLE_VERSION
from researchhelper.report_designer import design


CODE = '600519.SH'
PERIOD = '2026-01-01至2026-06-30'
PERIOD_TEXT = '2026 年 1 月 1 日至 6 月 30 日止期间'
CONSOLIDATED = '合并利润表'
REVENUE = '营业收入'
PROFIT = '归属于母公司股东的净利润'
UNIT = '（金额单位均为人民币元）'


def seed_original(task):
    record = snapshot_record(task, 'ifind', 'report_query', ifind_query(CODE),
                             {'errorcode': 0, 'data': [ifind_row(CODE, '甲股份')]})
    task['source_records'].append(record)
    directory = validate_filing_original_request(task, {'catalog_id':record['id'], 'source_id':'selected'})
    original = filing_record(task, directory, 'synthetic.txt', '合成报告'.encode())
    material = original['snapshot']['material']
    statement = [[CONSOLIDATED], [PERIOD_TEXT], [UNIT], ['项目', '2026年', '2025年'],
                 [REVENUE, '3,000.00', '2,000.00'], [PROFIT, '500.00', '400.00']]
    table = dict(page=9, version=TEXT_TABLE_VERSION, representation='aligned_words', geometry_only=True,
                 header_row=3, period_columns={'1':PERIOD_TEXT}, word_cells={
                     str(index):[[{'text': token}] for token in row] for index,row in enumerate(statement)})
    material.update(extract_version=EXTRACT_VERSION, parts=[
        dict(locator='第1页', text='合成报告封面\n'+'无关内容'*800),
        dict(locator='第9页', text='\n'.join(' '.join(row) for row in statement)),
        dict(locator='第9页列式文本', text='\n'.join('\t'.join(row) for row in statement), pdf_table=table),
        dict(locator='第15页', text='主营业务分行业情况\n单位：人民币元\n分行业 营业收入 营业成本\n'
             '经纪业务 1,200.00 350.00\n证券投资业务 (100.50) 40.00\n合计 1,099.50 390.00'),
        dict(locator='第82页', text='分部报告\n分部业绩\n'+PERIOD_TEXT+'\n'
             '经纪业务 证券投资业务 合计\n一、营业收入 1,200.00 (100.50) 1,099.50'),
    ])
    original['hash'] = fingerprint(original['snapshot'])
    task['source_records'].append(original)
    return original


@pytest.fixture
def scenario(tmp_path):
    agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True, draft_first=True)
    try:
        task = agent.create('核对甲股份（600519.SH）半年报的营业收入、归母净利润及分部收入',
                            objects=[CODE], research_mode='current')
        yield task, seed_original(task)
    finally:
        agent.close_runtime()


def proposal(analysis, *, title='甲股份', data_refs=(), source_refs=()):
    return dict(title='半年报核对', conclusion='核对本期经营读数。',
                logics=[dict(title=title, analysis=analysis, data_refs=list(data_refs), source_refs=list(source_refs))])


def test_source_values_preserve_period_sign_unit_and_location(scenario):
    task, original = scenario
    before = fingerprint(task)
    facts = observations(task)
    assert task[READER_POLICY] == VERSION
    assert [row['raw_value'] for row in facts] == ['3000.00','500.00','1200.00','-100.50']
    assert {row['period'] for row in facts} == {PERIOD}
    assert {row['unit'] for row in facts} == {'人民币元'}
    assert {row['code'] for row in facts} == {CODE}
    assert all(row['artifact_id'] == original['id'] for row in facts)
    assert [row['locator']['part'] for row in facts] == ['第9页列式文本']*2 + ['第15页']*2
    assert facts[-1]['locator']['raw_token'] == '(100.50)'
    parts = writing_context(task, facts, text_limit=600)['documents'][0]['parts']
    assert sum(len(part['text']) for part in parts) <= 600
    assert len(parts) > 1 and parts[0]['locator'] == '第1页'
    assert any('1,200.00' in part['text'] for part in parts[1:])
    assert fingerprint(task) == before


def test_retained_brokerage_reconciles_both_segment_tables_to_the_same_statement():
    from decimal import Decimal
    from researchhelper.draft_filing_amounts import segment_revenue_cells

    retained = ROOT/'result/acceptance/autonomous-data-phase-9rmxvnaf'
    if (retained/'task.json').is_file():
        task = json.loads((retained/'task.json').read_bytes())
    else:
        with ZipFile(retained/'raw-qa.zip') as package:
            task = json.loads(package.read('task.json'))
    original = next(row for row in task['source_records'] if row['kind']=='filing')
    scope = dict(code='600030.SH', periods=['20250630','20260630'])
    cells = segment_revenue_cells(original, scope)
    totals = [cell for cell in cells if cell['field']=='ths_revenue_stock']
    assert [cell['value'] for cell in totals] == [Decimal('33127176031.34'), Decimal('49691932907.10')]
    assert [cell['locator']['restated'] for cell in totals] == [True, False]
    assert len(cells)==12
    securities = [cell['value'] for cell in cells if cell['label']=='证券投资业务']
    assert securities[1]-securities[0]==Decimal('5357512747.85')
    altered = deepcopy(original)
    prior = next(part for part in altered['snapshot']['material']['parts'] if part['locator']=='第263页')
    prior['text'] = prior['text'].replace('14,496,552,056.40', '14,496,552,057.40', 1)
    assert segment_revenue_cells(altered, scope)==[]


def add_statement(material, page, title, rows, *, unit=UNIT):
    values = [[title], [PERIOD_TEXT], [unit], ['项目', '2026年', '2025年'], *rows]
    rendered = '\n'.join('\t'.join(row) for row in values)
    material['parts'].extend([
        dict(locator=f'第{page}页', text=rendered.replace('\t', ' ')),
        dict(locator=f'第{page}页列式文本', text=rendered, pdf_table=dict(
            page=page, version=TEXT_TABLE_VERSION, representation='aligned_words', geometry_only=True,
            header_row=3, period_columns={'1': PERIOD_TEXT}, word_cells={
                str(index): [[{'text': token}] for token in row] for index, row in enumerate(values)
            })),
    ])


@pytest.mark.parametrize('scope, expected', [
    ('核对甲股份2026年半年报合并报表营业成本、报表净利润、经营现金流。', [
        ('ths_operating_cost_stock', '2000.00', '第10页列式文本'),
        ('ths_np_stock', '800.00', '第10页列式文本'),
        ('ths_ncf_from_oa_stock', '-750.25', '第12页列式文本'),
    ]),
    ('核对甲股份2026年半年报母公司报表，营业成本、报表净利润、经营现金流。', []),
    ('核对甲股份2026年半年报的扣非净利润、净利润率和营业总成本。', []),
])
def test_additional_cells_require_the_requested_metric_and_own_consolidated_statement(scenario, scope, expected):
    task, original = scenario
    task['question'] = scope
    task['questions'] = []
    material = original['snapshot']['material']
    add_statement(material, 10, CONSOLIDATED, [
        ['二、营业总成本', '2,234.00', '1,134.00'],
        ['其中：营业成本', '2,000.00', '1,000.00'],
        ['五、净利润', '800.00', '600.00'],
        ['持续经营净利润', '900.00', '700.00'],
    ])
    add_statement(material, 11, '母公司现金流量表', [['经营活动产生的现金流量净额', '999.00', '700.00']])
    add_statement(material, 12, '合并现金流量表', [['经营活动产生的现金流量净额', '(750.25)', '600.00']])
    original['hash'] = fingerprint(original['snapshot'])
    before = fingerprint(task)
    facts = observations(task)
    assert [(row['field'], row['raw_value'], row['locator']['part']) for row in facts] == expected
    previous_fields = {row['field'] for row in observations(dict(task, **{READER_POLICY: 26}))}
    assert previous_fields <= {'ths_np_atoopc_stock'}
    assert fingerprint(task) == before


def test_retained_cash_flow_and_net_profit_summary_link_their_own_source(scenario):
    task, original = scenario
    task['question'] = '核对甲股份2026年半年报合并报表的报表净利润和经营现金流。'
    task['questions'] = []
    material = original['snapshot']['material']
    add_statement(material, 10, CONSOLIDATED, [['五、净利润', '800.00', '600.00']])
    add_statement(material, 12, '合并现金流量表', [['经营活动产生的现金流量净额', '(750.25)', '600.00']])
    original['hash'] = fingerprint(original['snapshot'])
    facts = observations(task)
    report = normalize_report({}, task, facts, retained_data_only=True)
    assert report['title'] == '甲股份2026年上半年财务核对'
    assert all(label in report['conclusion'] for label in ('2026-01-01至2026-06-30', '净利润', '经营现金流净额'))
    task['draft_report'] = dict(report, facts=facts, calculations=[], source_binding=source_binding(task))
    content = compose(task)
    for profile in ('standard', 'detailed'):
        assert not any(row.get('field') == 'filing_amount'
                       for row in logic_figure_plan(content, report_profile=profile))
        tree = html.document_fromstring(design(content, ROOT, profile=profile)['html'])
        assert tree.findtext('.//main//h1') == report['title']
        links = tree.get_element_by_id('summary').xpath('.//a[@class="quantity-link"]')
        assert len(links) == 2
        for link, page, amount in zip(links, ('第10页列式文本', '第12页列式文本'), ('800.00', '(750.25)')):
            pane = tree.get_element_by_id(link.get('href')[1:]).text_content()
            assert all(value in pane for value in (PERIOD, '人民币元', page, amount))


def test_legacy_sparse_filing_stays_readable_without_a_chart(scenario):
    task, _ = scenario
    facts = observations(task)
    report = normalize_report({}, task, facts, retained_data_only=True)
    report.pop('filing_caption_version')
    task['draft_report'] = dict(report, facts=facts, calculations=[], source_binding=source_binding(task))
    content = compose(task)
    for profile in ('standard', 'detailed'):
        assert not any(row.get('field') == 'filing_amount'
                       for row in logic_figure_plan(content, report_profile=profile))
        tree = html.document_fromstring(design(content, ROOT, profile=profile)['html'])
        main = tree.find('.//main').text_content()
        assert all(word in main for word in ('营业收入', '净利润', '经纪业务', '证券投资业务'))


@pytest.mark.parametrize('placement, expected_unit', [
    ('before_caption', None), ('after_statement', None), ('after_valid_statement', '元'),
])
def test_statement_uses_its_own_unit_without_losing_other_amounts(scenario, placement, expected_unit):
    task, original = scenario
    page = original['snapshot']['material']['parts'][1]
    foreign_unit = '其他明细表\n单位：人民币千元'
    if placement != 'after_valid_statement':
        page['text'] = page['text'].replace(UNIT+'\n', '')
    if placement == 'before_caption':
        page['text'] = foreign_unit+'\n'+page['text']
    else:
        page['text'] += '\n'+foreign_unit
    original['hash'] = fingerprint(original['snapshot'])

    facts = observations(task)
    statement = [row for row in facts if row['field'].startswith('ths_')]
    assert [row['raw_unit'] for row in statement] == ([expected_unit]*2 if expected_unit else [])
    assert [row['raw_value'] for row in facts if row['field'].startswith('filing.segment.revenue:')] == ['1200.00','-100.50']
    submitted = proposal('甲股份2026年上半年营业收入为3000千元。甲股份2026年上半年经纪业务营业收入为1200元。',
                         source_refs=[original['id']])
    logic, = normalize_report(submitted, task, facts)['logics']
    assert '3000千元' not in logic['analysis']
    assert '营业收入缺少可核对的读数' in logic['analysis'] and '1200元' in logic['analysis']
    assert [link['original'] for link in logic['quantity_links']['analysis']['links']] == ['1200元']
    if expected_unit:
        submitted['logics'][0]['analysis'] = '甲股份2026年上半年营业收入为3千元。'
        logic, = normalize_report(submitted, task, facts)['logics']
        link, = logic['quantity_links']['analysis']['links']
        assert link['original'] == '3千元' and link['data_refs'] == [statement[0]['id']]
        assert statement[0]['raw_value'] == '3000.00'


@pytest.mark.parametrize('statement', [
    '甲股份2026年第二季度营业收入为3000元。',
    '甲股份2026年1月1日营业收入为3000元。',
    '000001.SZ在2026年上半年营业收入为3000元。',
    '甲股份2026年上半年母公司营业收入为3000元。',
    '甲股份2026年上半年母公司经纪业务营业收入为1200元。',
    '甲股份2026年上半年营业收入为3000美元。',
    '甲股份2026年上半年证券投资业务收入为100.50元。',
    '全行业2026年上半年经纪业务收入为1200元。',
])
def test_wrong_claim_is_removed_without_stopping_delivery(scenario, statement):
    task, _ = scenario
    report = normalize_report(proposal(statement), task, observations(task))
    logic, = report['logics']
    assert '缺少可核对的读数' in logic['analysis']
    assert not logic.get('quantity_links')


def test_missing_column_basis_or_conflicting_segment_amount_stays_local(scenario):
    task, original = scenario
    material = original['snapshot']['material']
    material['parts'][2]['pdf_table']['period_columns'] = {}
    material['parts'][3]['text'] = material['parts'][3]['text'].replace('1,200.00', '1,300.00')
    original['hash'] = fingerprint(original['snapshot'])
    facts = observations(task)
    assert [row['raw_value'] for row in facts] == ['-100.50']
    assert writing_context(task, facts)['documents'][0]['parts']


@pytest.mark.parametrize('declaration', [
    '单位：人民币万元', '币种：美元', '单位：人民币元，币种：美元',
    '单位：港元', '单位：元/股', '单位：人民币元\n单位：人民币千元',
])
def test_same_printed_amount_cannot_confirm_a_conflicting_note(scenario, declaration):
    task, original = scenario
    note = original['snapshot']['material']['parts'][-1]
    note['text'] = note['text'].replace(PERIOD_TEXT, declaration+'\n'+PERIOD_TEXT)
    original['hash'] = fingerprint(original['snapshot'])
    before = fingerprint(task)

    facts = observations(task)
    assert [row['raw_value'] for row in facts] == ['3000.00', '500.00']
    text = ('甲股份2026年上半年营业收入为3000元。'
            '甲股份2026年上半年经纪业务营业收入为1200元。')
    logic, = normalize_report(proposal(text, source_refs=[original['id']]), task, facts)['logics']
    assert '3000元' in logic['analysis'] and '1200元' not in logic['analysis']
    assert [link['original'] for link in logic['quantity_links']['analysis']['links']] == ['3000元']
    assert fingerprint(task) == before
    legacy = observations(dict(task, **{READER_POLICY:15}))
    assert [row['raw_value'] for row in legacy[2:]] == ['1200.00', '-100.50']


@pytest.mark.parametrize('declaration, retained', [
    ('单位：人民币千元', True),
    ('单位：千元\n币种：人民币', True),
    ('币种：人民币 单位：千元', True),
    ('单位：千元', False),
])
def test_segment_cross_scale_requires_literal_rmb_and_exact_equivalence(scenario, declaration, retained):
    task, original = scenario
    note = original['snapshot']['material']['parts'][-1]
    note['text'] = note['text'].replace(PERIOD_TEXT, PERIOD_TEXT+'\n'+declaration)
    note['text'] = note['text'].replace('1,200.00 (100.50) 1,099.50', '1.20000 (0.10050) 1.09950')
    original['hash'] = fingerprint(original['snapshot'])

    facts = observations(task)
    segments = facts[2:]
    assert [row['raw_value'] for row in segments] == (['1200.00', '-100.50'] if retained else [])
    for row in segments:
        assert row['period'] == PERIOD and row['raw_unit'] == '元'
        assert row['locator']['statement_basis']['unit_quotes'] == declaration.splitlines()
    if not retained:
        return
    text = '甲股份2026年上半年经纪业务营业收入为1200元。'
    report = normalize_report(proposal(text, source_refs=[original['id']]), task, facts)
    task['draft_report'] = dict(report, facts=facts, calculations=[], source_binding=source_binding(task))
    content = compose(task)
    for profile in ('standard', 'detailed'):
        rendered = html.document_fromstring(design(content, ROOT, profile=profile)['html'])
        link, = rendered.get_element_by_id('logic-1').xpath('.//a[@class="quantity-link"]')
        pane = rendered.get_element_by_id(link.get('href')[1:]).text_content()
        assert PERIOD in pane and '人民币元' in pane and '1.20000' in pane
        assert all(line in pane for line in declaration.splitlines())


@pytest.mark.parametrize('title_and_unit, quote', [
    ('主营业务分行业情况\n单位：元\n币种：人民币', '单位：元\n币种：人民币'),
    ('主营业务分行业情况（单位：人民币元）', '主营业务分行业情况（单位：人民币元）'),
])
def test_segment_table_keeps_the_literal_unit_declaration(scenario, title_and_unit, quote):
    task, original = scenario
    table = original['snapshot']['material']['parts'][-2]
    table['text'] = table['text'].replace('主营业务分行业情况\n单位：人民币元', title_and_unit)
    original['hash'] = fingerprint(original['snapshot'])
    segments = observations(task)[2:]
    assert [row['raw_value'] for row in segments] == ['1200.00', '-100.50']
    assert all(row['locator']['unit_basis']['quote'] == quote for row in segments)


def test_conversion_does_not_round_a_nearby_amount_into_a_match(scenario):
    task, original = scenario
    note = original['snapshot']['material']['parts'][-1]
    note['text'] = note['text'].replace('经纪业务 证券投资业务 合计',
                                       '经纪业务 证券投资业务 合计\n单位：人民币千元')
    note['text'] = note['text'].replace('1,200.00 (100.50) 1,099.50',
                                       '1.20000000000000000000000000001 (0.10050) 1.09950')
    original['hash'] = fingerprint(original['snapshot'])
    assert [row['raw_value'] for row in observations(task)] == ['3000.00', '500.00', '-100.50']


def test_legacy_history_and_unverifiable_snapshot_do_not_gain_current_cells(scenario):
    task, original = scenario
    assert observations(dict(task, **{READER_POLICY:14})) == []
    assert observations(dict(task, research_mode='historical')) == []
    assert observations(dict(task, cutoff='2026-10-01')) == []
    original['snapshot']['material']['parts'][2]['text'] += '\n改写的表'
    assert observations(task) == []


def test_reports_link_to_the_same_original_and_plot_only_cited_cells(scenario):
    task, original = scenario
    facts = observations(task)
    value = proposal('甲股份2026年上半年经纪业务营业收入为1200元。'
                     '甲股份2026年上半年证券投资业务收入为-100.50元。',
                     source_refs=[original['id']])
    report = normalize_report(value, task, facts)
    logic, = report['logics']
    assert logic['data_refs'] == ['D3','D4']
    assert len(logic['quantity_links']['analysis']['links']) == 2
    task['draft_report'] = dict(report, facts=facts, calculations=[], source_binding=source_binding(task))
    content = compose(task)
    for profile in ('standard','detailed'):
        delivered = design(content, ROOT, profile=profile)
        result = html.document_fromstring(delivered['html'])
        links = result.get_element_by_id('logic-1').xpath('.//a[@class="quantity-link"]')
        assert len(links) == 2
        for link in links:
            pane = result.get_element_by_id(link.get('href')[1:]).text_content()
            assert PERIOD in pane and '人民币元' in pane and '第15页' in pane and '第82页' in pane
            assert '第1页' not in pane
        assert delivered['document']['figure_coverage']['logic-1']['data_refs'] == ['D3','D4']
    tampered = deepcopy(content)
    tampered['draft']['facts'][2]['locator']['quote'] = '替换后的摘录'
    tampered['content_hash'] = fingerprint({key:value for key,value in tampered.items() if key != 'content_hash'})
    delivery = design(tampered, ROOT)
    assert delivery['document']['figure_coverage']['logic-1']['figures'] == 0
    result = html.document_fromstring(delivery['html'])
    assert [item.text_content() for item in result.get_element_by_id('logic-1').xpath('.//a[@class="quantity-link"]')] == ['-100.50元']


def change_statement_fixture(original, case):
    material = original['snapshot']['material']
    physical, table = material['parts'][1:3]
    if case in ('split_rmb', 'inline_rmb', 'missing_currency', 'conflicting_currency', 'conflicting_scale'):
        units = {
            'split_rmb': '编制单位：甲股份\n单位：元\n币种：人民币',
            'inline_rmb': '编制单位：甲股份 单位：元 币种：人民币',
            'missing_currency': '单位：元',
            'conflicting_currency': UNIT+'\n币种：美元',
            'conflicting_scale': UNIT+'\n单位：人民币万元',
        }
        physical['text'] = physical['text'].replace(UNIT, units[case])
    else:
        own = physical['text']
        parent = own.replace(CONSOLIDATED, '母公司利润表')
        if case == 'parent_scope':
            own = own.replace('3,000.00', '9,000.00').replace('500.00', '800.00')
            table['text'] = table['text'].replace(CONSOLIDATED, '母 公司 利润 表')
        elif case == 'unknown_scope':
            table['text'] = table['text'].replace(CONSOLIDATED, '')
        elif case == 'borrowed_period':
            own = own.replace(PERIOD_TEXT, '截至2026年6月30日')
        elif case == 'late_period':
            own = own.replace(PERIOD_TEXT, '截至2026年6月30日')+'\n'+PERIOD_TEXT
        elif case == 'separate_parent':
            parent = parent.replace(UNIT, '单位：美元')
        else:
            raise AssertionError(case)
        physical['text'] = own+'\n'+parent
    original['hash'] = fingerprint(original['snapshot'])


@pytest.mark.parametrize('reader_version', [17, VERSION], ids=['frozen-v17', 'current-reader'])
@pytest.mark.parametrize('case, expected, unit_quote', [
    ('split_rmb', ['3000.00', '500.00'], '单位：元\n币种：人民币'),
    ('inline_rmb', ['3000.00', '500.00'], '编制单位：甲股份 单位：元 币种：人民币'),
    ('missing_currency', [], None),
    ('conflicting_currency', [], None),
    ('conflicting_scale', [], None),
    ('parent_scope', [], None),
    ('unknown_scope', [], None),
    ('borrowed_period', [], None),
    ('late_period', [], None),
    ('separate_parent', ['3000.00', '500.00'], UNIT),
])
def test_consolidated_filing_requires_same_statement_and_explicit_unit_currency(scenario, case, expected, unit_quote, reader_version):
    task, original = scenario
    assert task[READER_POLICY] == VERSION
    task[READER_POLICY] = reader_version
    change_statement_fixture(original, case)
    before = fingerprint(task)
    facts = observations(task)
    statement = [row for row in facts if row['field'].startswith('ths_')]
    segments = [row for row in facts if row['field'].startswith('filing.segment.')]
    assert task[READER_POLICY] == reader_version
    assert [row['raw_value'] for row in statement] == expected
    assert [row['raw_value'] for row in segments] == ['1200.00', '-100.50']
    assert all(row['unit']=='人民币元' and row['period']==PERIOD for row in statement)
    assert all(row['locator']['unit_basis']['quote']==unit_quote for row in statement)
    if case in ('split_rmb', 'conflicting_currency', 'parent_scope', 'borrowed_period', 'late_period'):
        legacy = observations(dict(task, **{READER_POLICY:16}))
        old = [row['raw_value'] for row in legacy if row['field'].startswith('ths_')]
        assert old == ([] if case == 'split_rmb' else ['3000.00', '500.00'])
    assert fingerprint(task) == before


def test_split_rmb_and_scope_conflict_keep_only_the_correct_report_links(scenario):
    task, original = scenario
    change_statement_fixture(original, 'split_rmb')
    text = '甲股份2026年上半年营业收入为3000元。甲股份2026年上半年经纪业务营业收入为1200元。'
    facts = observations(task)
    draft = normalize_report(proposal(text, source_refs=[original['id']]), task, facts)
    task['draft_report'] = dict(draft, facts=facts, calculations=[], source_binding=source_binding(task))
    content = compose(task)
    for profile in ('standard', 'detailed'):
        tree = html.document_fromstring(design(content, ROOT, profile=profile)['html'])
        links = tree.get_element_by_id('logic-1').xpath('.//a[@class="quantity-link"]')
        assert len(links) == 2
        scope = tree.get_element_by_id(links[0].get('href')[1:]).text_content()
        assert all(text in scope for text in (PERIOD, '合并利润表', '单位：元', '币种：人民币'))
    # Add a local conflict to this already-split unit declaration.
    page = original['snapshot']['material']['parts'][1]
    page['text'] = page['text'].replace('币种：人民币', '币种：人民币\n币种：美元')
    original['hash'] = fingerprint(original['snapshot'])
    logic, = normalize_report(proposal(text), task, observations(task))['logics']
    assert '3000元' not in logic['analysis'] and '1200元' in logic['analysis']
    assert [link['original'] for link in logic['quantity_links']['analysis']['links']] == ['1200元']
