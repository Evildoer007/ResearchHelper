"""Use one source-linked financial matrix for a broad company comparison."""
from copy import deepcopy
from decimal import Decimal

from lxml import html
import pytest

from tests import ROOT
from tests.test_draft_financial_period import add_record, rehash, sample
from researchhelper.contracts import fingerprint
from researchhelper.draft_analysis import PEER_NET_AFTER_GROSS, PEER_PROFIT_FIELDS, PEER_PROFIT_POLICY, analyze
from researchhelper.draft_sources import observations
from researchhelper.draft_visuals import FIELDS, _validated, financial_peer_tables
from researchhelper.drafts import compose, normalize_report, source_binding
from researchhelper.publication_content import requested_financial_statement
from researchhelper.report_designer import design
from researchhelper.tinyshare_sources import analysis_field


def peer_content(*, missing=False, mixed=False, explicit_ratio=False, fields=tuple(FIELDS)):
    task = sample()
    task.update(draft_source_reader_version=14, source_records=[], questions=[], evidence=[],
                calculations=[], knowledge={}, gaps=[], tracking=[], theses=[], objects=['600519.SH','002594.SZ'])
    for statement in ('1','2') if mixed else ('1',):
        for index, period in enumerate(('20241231','20251231')):
            first = [2000+field*100+index*120 for field in range(len(fields))]
            second = [1400+field*80-index*50 for field in range(len(fields))]
            if missing and index:
                second[0] = None
            record = add_record(task, period, first, fields=fields)
            record['parameters']['codes'] = ','.join(task['objects'])
            record['snapshot']['tables'].append(dict(thscode='002594.SZ', time=['2026-10-31'],
                table={field: [value] for field, value in zip(fields, second)}))
            for wire, echo in zip(record['parameters']['indipara'], record['snapshot']['inputParams']['params']):
                wire['indiparams'][1] = statement
                echo['params'][1]['value'] = statement
            record['snapshot']['synthetic_test_response'] = True
            rehash(record)
    facts = observations(task)
    task['draft_analysis'] = analyze(task, facts)
    calculation_refs = [row['id'] for row in task['draft_analysis']['results'] if
                        row['method']=='raw_percent_v1' and row['code']=='002594.SZ'] if explicit_ratio else []
    proposal = dict(title='两公司财务核对', conclusion='逐项核对所列财务读数。',
        logics=[dict(title='报告期对照', analysis='比较原值和已登记的变化。', risk='单位差异影响解释。',
                     data_refs=[row['id'] for row in facts], calculation_refs=calculation_refs)])
    task['draft_report'] = dict(normalize_report(proposal,task,facts), facts=facts,
        calculations=task['draft_analysis']['results'], source_binding=source_binding(task))
    return compose(task)


def tables_in(paper):
    return paper.xpath('//main//table[contains(concat(" ",normalize-space(@class)," ")," financial-peer-table ")]')


def test_profit_bridge_requires_all_three_comparable_consolidated_lines():
    task = sample()
    task.update(source_records=[], **{PEER_PROFIT_POLICY: 1})
    for period, values in [('20240630', (1000, 600, 300)), ('20250630', (1200, 700, 360))]:
        add_record(task, period, values, fields=PEER_PROFIT_FIELDS)
    facts = observations(task)
    for row in facts:
        row.update(unit='人民币元', currency='RMB')
    result, = [row for row in analyze(task, facts)['results'] if row['method'] == PEER_NET_AFTER_GROSS]
    assert Decimal(result['exact']) == -40 and len(result['data_refs']) == 6
    assert set(result['data_refs']) == {row['id'] for row in facts}

    last_profit = next(row for row in facts if row['period'] == '20250630' and row['field'] == PEER_PROFIT_FIELDS[-1])
    for altered in (dict(last_profit, currency='USD'), dict(last_profit, unit='原始单位待核对')):
        substituted = [altered if row['id'] == last_profit['id'] else row for row in facts]
        assert all(row['method'] != PEER_NET_AFTER_GROSS for row in analyze(task, substituted)['results'])


@pytest.mark.parametrize('profile',('standard','detailed'))
def test_two_panels_preserve_all_values_and_exact_change_links(profile):
    content = peer_content()
    before = deepcopy(content)
    result = design(content,ROOT,profile=profile)
    paper = html.document_fromstring(result['html'])
    document = result['document']
    charts = list(document['visual_sources'].values())
    assert len(charts)==2 and {item['kind'] for item in charts}=={'peer_financial_changes'}
    table, = tables_in(paper)
    assert len(table.xpath('./tbody/tr'))==8 and len(table.xpath('./thead/tr/th'))==5
    facts = {row['id']:row for row in content['draft']['facts']}
    cells = table.xpath('.//td[@data-observation-id]')
    assert len(cells)==len(facts)==len(document['dataset']['rows'])==32
    assert {cell.get('data-observation-id') for cell in cells}==set(facts)
    for cell in cells:
        ref = cell.get('data-observation-id')
        assert cell.xpath('./a/@data-inspection-observation')==[ref]
        assert str(facts[ref]['raw_value'])==cell.text_content().replace(',','').removesuffix('*')
    assert not paper.xpath('//main/section[@id="data"]')
    changes = {tuple(calc['data_refs']):calc for calc in _validated(content) if calc['method']=='raw_change_v1'}
    for chart in charts:
        assert chart['context'].count('原值单位一致')==1
        data = chart['auto_visual']['data']
        for series, refs in zip(data['series'], chart['series_point_refs']):
            for label, value, point in zip(data['labels'],series['values'],refs):
                calc = changes[tuple(point)]
                assert calc['value']==value and calc['code']==series['name']
                assert FIELDS[analysis_field(calc['rows'][0])]==label
    assert document['figure_coverage']['logic-1']['tables']==1 and content==before


def test_missing_cell_remains_blank_in_change_series_and_present_in_values():
    content = peer_content(missing=True)
    result = design(content,ROOT)
    paper = html.document_fromstring(result['html'])
    assert len(tables_in(paper))==1 and '未返回' in tables_in(paper)[0].text_content()
    operations, = [spec for spec in result['document']['visual_sources'].values() if spec['name'].startswith('经营指标')]
    series, = [row for row in operations['auto_visual']['data']['series'] if row['name']=='002594.SZ']
    assert series['values'][0] is None and '空档表示缺少可比读数' in operations['context']
    assert all('002594.SZ'!=row[0] or row[3] is None or row[3]!=0 for row in result['document']['dataset']['rows'])


def test_distinct_statements_are_separate_and_frozen_version_one_reopens():
    content = peer_content(mixed=True)
    panels = financial_peer_tables(content,content['draft']['facts'])
    assert {row['statement'] for row in panels}=={'合并报表','母公司报表'}
    for panel in panels:
        rows = [row for row in content['draft']['facts'] if row['id'] in panel['data_refs']]
        assert requested_financial_statement(content,rows)==panel['statement']
    legacy = deepcopy(content)
    legacy['draft']['visual_layout_version'] = 1
    legacy['content_hash'] = fingerprint({key:value for key,value in legacy.items() if key!='content_hash'})
    old = design(legacy,ROOT)
    assert not tables_in(html.document_fromstring(old['html']))
    assert len(old['document']['dataset']['rows'])==len(content['draft']['facts'])


def test_two_cited_ratio_values_remain_readable_and_small_unknown_cohorts_use_a_table():
    content = peer_content(explicit_ratio=True)
    selected = design(content,ROOT)
    paper = html.document_fromstring(selected['html'])
    refs = set(content['draft']['logics'][0]['calculation_refs'])
    assert len(refs)==2 and refs <= set(paper.xpath('//main//@data-calculation-ref'))
    assert not any(spec.get('kind')=='paired' for spec in selected['document']['visual_sources'].values())
    small = design(peer_content(fields=('ths_revenue_stock',)),ROOT)
    paper = html.document_fromstring(small['html'])
    assert not any(spec.get('axis_role')=='cross_section' for spec in small['document']['visual_sources'].values())
    assert len(paper.xpath('//*[@id="complete-report-data"]//tbody/tr'))==4


def test_detailed_financial_change_methods_print_once_with_each_original_pair():
    content = peer_content()
    frozen = deepcopy(content)
    result = design(content,ROOT,profile='detailed')
    document = result['document']
    paper = html.document_fromstring(result['html'])
    expected = {calc['id']:calc for calc in _validated(content) if calc['method']=='raw_change_v1'}
    assert len(expected)==16
    assert len(paper.xpath('//main//*[local-name()="math"]'))==2
    assert len(paper.xpath('//main//figure'))==2
    assert not paper.xpath('//main//*[contains(concat(" ",normalize-space(@class)," ")," evidence-ledger ")]')
    sections = paper.xpath('//main//table[contains(concat(" ",normalize-space(@class)," ")," calculation-results-table ")]')
    assert len(sections)==2
    table_rows = {row.get('data-calculation-ref'):row for table in sections
                  for row in table.xpath('./tbody/tr[@data-calculation-ref]')}
    assert set(table_rows)==set(expected)
    for ref, row in table_rows.items():
        calc = expected[ref]
        link, = row.xpath('./td/a')
        assert link.text_content()==calc['display']+'%'
        assert link.get('href')=='#draft-basis-1'
        assert link.get('data-inspection-observation').split()==calc['data_refs']
    blocks = [block for block in document['blocks'] if block.get('calculation_rows')]
    assert {row['id']:row['data_refs'] for block in blocks for row in block['calculation_rows']}=={
        calc['id']:calc['data_refs'] for calc in expected.values()}
    assert content==frozen


def test_financial_change_methods_keep_distinct_statement_and_balance_conditions():
    content = peer_content(mixed=True,explicit_ratio=True)
    result = design(content,ROOT,profile='detailed')
    validated = {calc['id']:calc for calc in _validated(content)}
    rows = [block['calculation_rows'] for block in result['document']['blocks'] if block.get('calculation_rows')]
    assert len(rows)==4
    for batch in rows:
        calcs=[validated[row['id']] for row in batch]
        facts=[fact for calc in calcs for fact in calc['rows']]
        assert len({calc['condition'] for calc in calcs})==1
        assert requested_financial_statement(content,facts) in ('合并报表','母公司报表')
    assert any(block.get('calculation_ref') in {calc['id'] for calc in validated.values()
        if calc['method']=='raw_percent_v1'} for block in result['document']['blocks'])
