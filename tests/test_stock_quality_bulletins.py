from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from unittest.mock import patch

from lxml import html
import pytest

from researchhelper.announcement_sources import parse_catalogue, request_issuer, request_spec
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, charge, now
from researchhelper.draft_analysis import GROSS_MARGIN_EFFECT, GROSS_SCALE_EFFECT
from researchhelper.draft_sources import observations
from researchhelper.draft_visuals import logic_figure_plan
from researchhelper.draft_workflow import direct_readout
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import _record_current_acquisition, snapshot_record, validate_source_request
from researchhelper.official_sources import validate_request
from researchhelper.report_designer import design
from researchhelper.stock_quality import OPEN_COMPANY_POLICY, catalogue_request, resolved_study_scope, study_scope
from researchhelper.stock_quality_readout import verified_stock_quality_answer

ROOT = Path(__file__).resolve().parents[1]
CUTOFF = '2026-11-22T10:00:00+08:00'
QUESTION = '研究示例制造2026年上半年增长质量，看看营收增长有没有同步转化为利润和现金，按A股半年报写一份研究报告。'
SPOKEN_QUESTION = '示例制造2026年上半年卖得更多，利润和回款有没有跟上？按半年报写份报告。'
OPEN_QUESTION = '我想知道示例制造的营收增长有没有带来更多利润和现金。'
DATED_QUESTION = '示例制造今年上半年销售多了，有没有赚得更多，现金有没有跟上？'
ISSUERS = [
    dict(code='00300', zwjc='示例制造', category='港股', orgId='stock-company', delisted=False),
    dict(code='000333', zwjc='示例制造', category='A股', orgId='stock-company', delisted='false'),
    dict(code='000337', zwjc='示例制造', category='A股', orgId='inactive', delisted='true'),
    dict(code='002999', zwjc='示例制造股份', category='A股', orgId='other', delisted=False),
]
PDF_BYTES = b'%PDF-1.7 Test content; extraction mocked'


def _json(value):
    return json.dumps(value, ensure_ascii=False).encode()


def _catalogue(*, summary_only=False, prior=False, revision=False):
    records = []
    for code, column, doc, title_suffix in (
            ('00300', 'HKE', 1, ''),
            ('000333', 'SZZB', 2, '摘要'),
            ('000333', 'SZZB', 3, ''),
            ('000333', 'SZJJ', 4, ''),
    ):
        if doc == 3 and summary_only:
            continue
        records.append(dict(secCode=code, orgId='stock-company', pageColumn=column,
            announcementTitle='示例制造：2026年半年度报告'+title_suffix,
            announcementTime=int(datetime.fromisoformat('2026-08-29T00:00:00+08:00').timestamp())*1000,
            adjunctUrl=f'finalpage/2026-08-29/{doc}.PDF', announcementId=str(doc)))
    if prior:
        records.append(dict(records[-1], pageColumn='SZZB', announcementTitle='示例制造：2025年半年度报告',
            announcementTime=int(datetime.fromisoformat('2025-08-29T00:00:00+08:00').timestamp())*1000,
            announcementId='5', adjunctUrl='finalpage/2025-08-29/5.PDF'))
    if revision:
        records.append(dict(records[-1], pageColumn='SZZB', announcementTitle='示例制造：2026年半年度报告（更正版）',
            announcementTime=int(datetime.fromisoformat('2026-09-01T00:00:00+08:00').timestamp())*1000,
            announcementId='6', adjunctUrl='finalpage/2026-09-01/6.PDF'))
    return _json(dict(totalAnnouncement=len(records), totalpages=1, hasMore=False, announcements=records))


def _network(task, parameters, phase, started, timeout, event, *, selected=None, **kwargs):
    event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
    if phase == 'lookup':
        assert parameters['name'] == '示例制造'
        return _json(ISSUERS)
    if phase == 'catalogue':
        assert 'scope' not in parameters
        spec = request_spec(parameters, phase, request_issuer(kwargs['issuer_raw'], parameters))
        assert b'column=&stock=000333%2Cstock-company' in spec['body']
        return _catalogue(prior=parameters['start_date'] < '2026-01-01')
    assert selected['code'] == '000333.SZ' and selected['provider_id'] == '3'
    return PDF_BYTES


def _parse(omit_adjusted, *, shared_currency=None):
    summary = (
        '四、主要会计数据和财务指标\n公司是否需追溯调整或重述以前年度会计数据\n'
        '□ 是 √ 否\n本报告期 上年同期\n本报告期比上年同期增减\n'
        '营业收入（千元） 1,200,000 1,000,000 20.00%\n'
        '归属于上市公司股东的净利润（千元） 120,000 110,000 9.09%\n'
        '归属于上市公司股东的扣除非经常性损益的\n净利润（千元）\n90,000 100,000 -10.00%\n'
        '经营活动产生的现金流量净额（千元） 165,000 160,000 3.13%\n'
        '本报告期末 上年度末\n总资产（千元） 900 800 12.50%\n'
        '备注：归属于上市公司股东的扣除非经常性损益的净利润同比下降主要由于汇率变动。'
        '汇率损失归属于经常性损益，而用于管理外汇风险所持有的衍生金融工具产生的收益归属于非经常性损益所致。'
    )
    if omit_adjusted:
        summary = summary.replace('90,000 100,000 -10.00%', '90,000 100,000 -9.00%')
    parts = [dict(locator='第7页', text=summary),
             dict(locator='第98页', text='2026年半年度合并及公司利润表\n（除特别注明外，金额单位为人民币千元）\n'
                  '项 目 附注 2026 年半年度 2025 年半年度 2026 年半年度 2025 年半年度\n合并 合并 公司 公司\n'
                  '其中：营业收入 四(46),十七(3) 1,200,000 1,000,000 14,000 13,000\n'
                  '其中：营业成本 四(46) (900,000) (700,000) (12,000) (11,000)'),
             dict(locator='第99页', text='2026年半年度合并及公司现金流量表\n（除特别注明外，金额单位为人民币千元）\n'
                  '项 目 附注 2026 年半年度 2025 年半年度 2026 年半年度 2025 年半年度\n合并 合并 公司 公司\n'
                  '一、经营活动产生的现金流量\n销售商品、提供劳务收到的现金 950,000 900,000 - -\n'
                  '经营活动现金流入小计 983,000 928,000 - -\n经营活动现金流出小计 (818,000) (768,000) - -\n'
                  '经营活动产生的现金流量净额 四(62)(c) 165,000 160,000 - -')]
    if shared_currency:
        units = '单位：千元 币种：'+shared_currency
        parts[0]['text'] = (summary.replace('本报告期 上年同期\n本报告期比上年同期增减\n',
            units+'\n主要会计数据 本报告期（1－6月） 上年同期 本报告期比上年同\n期增减(%)\n')
            .replace('（千元）', '').replace('%\n', '\n'))
        for page in parts[1:]:
            page['text'] = (page['text'].replace('（除特别注明外，金额单位为人民币千元）', units)
                            .replace('合并及公司', '合并')
                            .replace(' 2026 年半年度 2025 年半年度\n合并 合并 公司 公司', ''))
            page['text'] = re.sub(r' (?:14,000 13,000|\(12,000\) \(11,000\)|- -)$', '', page['text'], flags=re.M)
    def parse(task, name, payload, timeout, event, **kwargs):
        assert payload == PDF_BYTES
        return dict(name=name, status='已解析', hash=sha256(payload).hexdigest(), format='PDF',
                    obtained_at=now(), parts=deepcopy(parts), extract_version='pdf-tables-7')
    return parse


@pytest.mark.parametrize('reconciled', [True, False])
def test_stock_strategy_attributes_earnings_only_to_reconciled_consolidated_cells(tmp_path, reconciled):
    def parse(*args, **kwargs):
        material = _parse(False)(*args, **kwargs)
        summary, income, cash = material['parts']
        summary['text'] = summary['text'].replace('120,000 110,000 9.09%', '90,000 110,000 -18.18%')
        income['text'] = (income['text'].replace('(900,000)', '(825,000)')
                          + '\n资产减值损失 (90,000) (1,000) - -')
        parent_bridge = '125,000' if reconciled else '124,000'
        material['parts'].insert(2, dict(locator='第99页',
            text=f'三、利润总额 {parent_bridge} 150,000 - -\n减：所得税费用 32,000 30,000 - -\n'
                 '少数股东损益 3,000 10,000 - -'))
        cash['locator'] = '第100页'
        cash['text'] = cash['text'].replace('经营活动现金流出小计',
            '购买商品、接受劳务支付的现金 700,000 730,000 - -\n经营活动现金流出小计')
        return material

    archive = ResearchArchive(tmp_path)
    agent = ResearchAgent(archive, ROOT, offline=False, draft_first=True, require_argument_support=True, isolate_sources=True)
    try:
        with (patch('researchhelper.engine.now', return_value=CUTOFF),
              patch('researchhelper.source_transport.official_announcement', side_effect=_network),
              patch('researchhelper.source_transport.IsolatedEvidenceCollector._parse_official_material', side_effect=parse),
              patch('socket.socket.connect', side_effect=AssertionError('No external calls'))):
            task = agent.start(agent.create(QUESTION, research_mode='current')['id'])
        assert task['status'] == '已完成' and task['ledger']['used']['model_calls'] == 0
        report = archive.report(task['id'], 1)['content']
        logics = report['draft']['logics']
        assert '采购付款贡献' in logics[2]['analysis']
        assert '采购及劳务付款按上年同期金额测算' in logics[2]['detail_analysis']
        assert logics[1].get('stock_profit_chart', False) == reconciled
        bridge_figures = [spec for spec in logic_figure_plan(report) if spec['kind'] == 'stock_profit_bridge']
        assert bool(bridge_figures) == reconciled
        if reconciled:
            assert '资产减值拖累' in logics[1]['analysis'] and '减值是否收敛' in logics[1]['analysis']
            assert any(row['locator']['part'] == '第99页' for row in report['draft']['facts']
                       if row['label'] == '利润总额')
        else:
            assert all(row['label'] != '利润总额' for row in report['draft']['facts'])
    finally:
        agent.close_runtime()


@pytest.mark.parametrize(('topic', 'headings'), (
    ('增长质量', ['收入规模与毛利率变化相互抵消', '税费减少支撑归母利润', '采购付款拖累经营现金流']),
    ('归母净利润为什么增长', ['税费减少支撑归母利润']),
    ('经营现金流为什么下降', ['采购付款拖累经营现金流']),
))
def test_same_filing_chooses_sections_and_drivers_for_the_question(tmp_path, topic, headings):
    def parse(*args, **kwargs):
        material = _parse(False)(*args, **kwargs)
        summary, income, cash = material['parts']
        summary['text'] = summary['text'].replace('165,000 160,000 3.13%', '150,000 160,000 -6.25%')
        income['text'] += ('\n资产减值损失 (8,000) (6,000) - -\n'
                           '三、利润总额 168,000 175,000 - -\n减：所得税费用 30,000 45,000 - -\n'
                           '少数股东损益 18,000 20,000 - -')
        cash['text'] = (cash['text'].replace('经营活动现金流出小计 (818,000)', '经营活动现金流出小计 (833,000)')
                        .replace('165,000 160,000', '150,000 160,000')
                        .replace('经营活动现金流出小计',
                                 '购买商品、接受劳务支付的现金 830,000 730,000 - -\n经营活动现金流出小计'))
        return material

    archive = ResearchArchive(tmp_path)
    agent = ResearchAgent(archive, ROOT, offline=False, draft_first=True, require_argument_support=True, isolate_sources=True)
    question = f'研究示例制造2026年上半年{topic}，按A股半年报写一份研究报告。'
    try:
        with (patch('researchhelper.engine.now', return_value=CUTOFF),
              patch('researchhelper.source_transport.official_announcement', side_effect=_network),
              patch('researchhelper.source_transport.IsolatedEvidenceCollector._parse_official_material', side_effect=parse),
              patch('socket.socket.connect', side_effect=AssertionError('No external calls'))):
            task = agent.start(agent.create(question, research_mode='current')['id'])
        assert task['status'] == '已完成' and task['ledger']['used']['model_calls'] == 0
        content = archive.report(task['id'], 1)['content']
        draft = content['draft']
        assert [item['title'] for item in draft['logics']] == headings
        if topic != '增长质量':
            assert draft['organization'] == 'mechanism'
            assert headings[0] in draft['conclusion']
            forbidden = '经营现金流' if topic.startswith('归母') else '归母净利润'
            assert forbidden not in draft['logics'][0]['analysis']
        if '采购付款拖累经营现金流' in headings:
            assert '采购付款拖累1亿元' in draft['logics'][-1]['analysis']
        if '税费减少支撑归母利润' in headings:
            assert any('所得税费用贡献1,500万元' in item['analysis'] for item in draft['logics'])
        assert verified_stock_quality_answer(task, draft)
    finally:
        agent.close_runtime()


@pytest.mark.parametrize('currency', ['人民币', '美元'])
def test_summary_amounts_share_the_header_unit_and_percent_column(tmp_path, currency):
    archive = ResearchArchive(tmp_path)
    agent = ResearchAgent(archive, ROOT, offline=False, draft_first=True, require_argument_support=True, isolate_sources=True)
    try:
        with (patch('researchhelper.engine.now', return_value=CUTOFF),
              patch('researchhelper.source_transport.official_announcement', side_effect=_network),
              patch('researchhelper.source_transport.IsolatedEvidenceCollector._parse_official_material',
                    side_effect=_parse(False, shared_currency=currency)),
              patch('socket.socket.connect', side_effect=AssertionError('No external calls'))):
            task = agent.create(QUESTION, research_mode='current')
            completed = agent.start(task['id'])
        assert completed['ledger']['used']['model_calls'] == 0
        if currency == '美元':
            assert completed['status'] == '部分完成'
            assert not observations(completed)
            return
        assert completed['status'] == '已完成'
        assert verified_stock_quality_answer(completed, completed['draft_report'])
        facts = observations(completed)
        assert len(facts) == 16
        revenue = next(row for row in facts if row['label']=='营业收入' and row['period']=='2026-06-30')
        assert revenue['locator']['reported_yoy'] == '20.00%'
        assert revenue['locator']['unit_basis']['quote'] == '单位：千元 币种：人民币'
        assert revenue['raw_value'] == '1200000'
    finally:
        agent.close_runtime()


@pytest.mark.parametrize(('question', 'omit_adjusted'), (
    (QUESTION, False), (QUESTION, True), (SPOKEN_QUESTION, False), (OPEN_QUESTION, False), (DATED_QUESTION, False)))
def test_plain_question_acquires_own_a_share_filing_and_tests_conversion(tmp_path, question, omit_adjusted):
    archive = ResearchArchive(tmp_path)
    agent = ResearchAgent(archive, ROOT, offline=False, draft_first=True, require_argument_support=True, isolate_sources=True)
    try:
        with (patch('researchhelper.engine.now', return_value=CUTOFF),
              patch('researchhelper.source_transport.official_announcement', side_effect=_network),
              patch('researchhelper.source_transport.IsolatedEvidenceCollector._parse_official_material',
                    side_effect=_parse(omit_adjusted)),
              patch('socket.socket.connect', side_effect=AssertionError('No external calls'))):
            task = agent.create(question, research_mode='current')
            assert direct_readout(task)[0] == 'stock_quality'
            scope = study_scope(task)
            parameters = catalogue_request(task, scope)['parameters']
            with pytest.raises(ContractError):
                validate_request(task, 'release_catalog', dict(parameters, name='其他发行人'))
            with pytest.raises(ContractError):
                request_issuer(_json(ISSUERS + [dict(ISSUERS[1], code='000334')]), parameters)
            assert not any(row['title'].endswith('报告') for row in
                           parse_catalogue(_catalogue(summary_only=True), _json(ISSUERS), parameters, CUTOFF)['entries'])
            stale = '数据需求stock_quality_catalogue：测试前次目录查询失败'
            agent._gap(task, stale, [scope['question_id']])
            archive.save(task)
            result = agent.start(task['id'])
        assert result['delivery'], (result['status'], result.get('error'), result.get('gaps'))
        assert result['ledger']['used']['data_requests'] == 3
        assert result['ledger']['used']['model_calls'] == 0
        if question == OPEN_QUESTION:
            assert scope['year'] is None and scope['start'] == '2025-07-01'
        assert resolved_study_scope(result, scope)['periods'] == ['2025-06-30', '2026-06-30']
        assert {row['code'] for row in observations(result)} == {'000333.SZ'}
        draft = result['draft_report']
        report = archive.report(task['id'], 1)['content']
        from researchhelper.draft_visuals import _calculation_dependencies

        assert set(_calculation_dependencies(report)) == {row['id'] for row in draft['calculations']}
        mismatched = deepcopy(report)
        changed = next(row for row in mismatched['source_records'] if row['operation'] == 'release_original')
        changed['snapshot']['parts'][0]['text'] += '\n改写'
        assert not _calculation_dependencies(mismatched)
        standard = html.document_fromstring(design(report, ROOT, profile='standard')['html'])
        detailed = html.document_fromstring(design(report, ROOT, profile='detailed')['html'])
        standard_text = standard.find('.//main').text_content()
        detailed_text = detailed.text_content()
        assert 'https://static.cninfo.com.cn/finalpage/2026-08-29/3.PDF' in standard.xpath('//footer//a/@href')
        assert '90,000 100,000' not in standard_text
        assert any('毛利率为25%，同比下降5个百分点' in row['analysis'] for row in draft['logics'])
        assert any('销售收现贡献5,000万元' in row['analysis'] and '经营现金流出拖累5,000万元' in row['analysis']
                   for row in draft['logics'])
        assert '第98页' in detailed_text and '第99页' in detailed_text and '950,000 900,000' in detailed_text
        bridge = {row['method']: row for row in draft['calculations']
                  if row['method'] in (GROSS_SCALE_EFFECT, GROSS_MARGIN_EFFECT)}
        assert {method: float(row['exact']) for method, row in bridge.items()} == {
            GROSS_SCALE_EFFECT: 60_000.0, GROSS_MARGIN_EFFECT: -60_000.0}
        assert all(row['inputs'] == dict(a='1000000', b='700000', c='1200000', d='900000')
                   for row in bridge.values())
        assert {row['unit'] for row in bridge.values()} == {'人民币千元'}
        assert all(set(row['data_refs']) <= set(draft['logics'][0]['data_refs']) for row in bridge.values())
        formulas = detailed.get_element_by_id('logic-1').xpath(
            './/*[local-name()="math"][.//*[local-name()="mtext" and text()="同期成本"]]')
        assert len(formulas) == 2 and all(node.xpath('.//*[local-name()="mo" and text()="×"]') for node in formulas)
        if not omit_adjusted:
            assert not detailed.xpath('//main/section[@id="data"]')
            visible = set(detailed.xpath('//main//table//*[@data-observation-id]/@data-observation-id'))
            assert visible == {row['id'] for row in draft['facts']}
            proofs = {row['id']: row['data_refs'] for row in draft['calculations']}
            groups = detailed.xpath('//main//table[.//th[text()="计算项"]]')
            assert groups and any(len(group.xpath('.//tr[@data-calculation-ref]')) >= 3 for group in groups)
            for row in detailed.xpath('//main//table[.//th[text()="计算项"]]//tr[@data-calculation-ref]'):
                assert row.get('data-calculation-inputs').split() == proofs[row.get('data-calculation-ref')]
        for index, phrase in enumerate(('毛利率每变动0.1个百分点', '外汇净损益', '按同一合并现金流量表'), 1):
            section = detailed.get_element_by_id(f'logic-{index}')
            if omit_adjusted and index == 2:
                assert phrase not in section.text_content()
            else:
                assert phrase in section.text_content()
            assert phrase not in standard_text
        if omit_adjusted:
            assert result['status'] == '部分完成'
            assert '依据不足' not in [row.get('title') for row in draft['logics']]
            assert verified_stock_quality_answer(result, draft) is None
            assert not logic_figure_plan(report)
        else:
            assert result['status'] == '已完成' and not result['gaps'], result['gaps']
            assert any(row['message'] == stale for row in result['resolved_gap_records'])
            assert stale not in report['limitations'] and stale not in standard_text
            answer = verified_stock_quality_answer(result, draft)
            assert result['questions'][0]['stock_quality_answer'] == answer and answer['judgment'] == '收入增长未同步兑现'
            assert '扣非归母净利润同比下降' in standard_text and '这一列报差异扩大了两种利润口径的分化' in standard_text
            assert [figure['field'] for figure in logic_figure_plan(report)] == ['stock_growth']
            amended = deepcopy(result)
            raw = next(row for row in amended['source_records'] if row['operation'] == 'release_original')
            raw['snapshot']['parts'][0]['text'] += '\n改写'
            assert verified_stock_quality_answer(amended, draft) is None
            altered = deepcopy(draft)
            altered['logics'][0]['detail_analysis'] += '毛利已经由原材料价格解释。'
            assert verified_stock_quality_answer(result, altered) is None
    finally:
        agent.close_runtime()


@pytest.mark.parametrize('suffix', (' 不要下载PDF。', ' 只用iFinD。', ' 另外评估市盈率。', ' 比较港股。'))
def test_expanded_questions_use_the_general_planner(tmp_path, suffix):
    agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True, draft_first=True)
    try:
        with patch('researchhelper.engine.now', return_value=CUTOFF):
            task = agent.create(QUESTION + suffix, research_mode='current')
        assert study_scope(task) is None
    finally:
        agent.close_runtime()


def test_sector_cash_question_stays_with_the_general_planner(tmp_path):
    agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True, draft_first=True)
    try:
        task = agent.create('研究白酒行业经营现金流为什么下滑。', entry='混合', research_mode='current')
        assert study_scope(task) is None
    finally:
        agent.close_runtime()


def test_open_question_keeps_supported_financial_analysis_when_peers_are_not_covered(tmp_path):
    agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=False, draft_first=True, isolate_sources=True)
    try:
        with (patch('researchhelper.engine.now', return_value=CUTOFF),
              patch('researchhelper.source_transport.official_announcement', side_effect=_network),
              patch('researchhelper.source_transport.IsolatedEvidenceCollector._parse_official_material',
                    side_effect=_parse(False))):
            task = agent.create(OPEN_QUESTION + '另外和同行比怎么样？', research_mode='current')
            assert study_scope(task)['whole_question'] is False
            assert direct_readout(task) is None
            assert direct_readout(task, policy_without_model=True)[0] == 'stock_quality'
            result = agent.start(task['id'])
        assert result['status'] == '部分完成' and result['delivery']
        assert len(result['draft_report']['logics']) == 3
        assert 'stock_quality_answer' not in result['questions'][0]
        assert {row['code'] for row in observations(result)} == {'000333.SZ'}
    finally:
        agent.close_runtime()


def test_latest_filing_does_not_silently_fall_back_to_prior_year_on_revision(tmp_path):
    def network(task, parameters, phase, started, timeout, event, **kwargs):
        if phase == 'catalogue':
            event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
            return _catalogue(prior=True, revision=True)
        assert phase == 'lookup'
        return _network(task, parameters, phase, started, timeout, event, **kwargs)

    agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=False, draft_first=True, isolate_sources=True)
    try:
        with (patch('researchhelper.engine.now', return_value=CUTOFF),
              patch('researchhelper.source_transport.official_announcement', side_effect=network)):
            task = agent.create(OPEN_QUESTION, research_mode='current')
            result = agent.start(task['id'])
        assert any(row['operation'] == 'release_catalog' for row in result['source_records'])
        assert resolved_study_scope(result, study_scope(result)) is None
        assert all(row['operation'] != 'release_original' for row in result['source_records'])
    finally:
        agent.close_runtime()


def _standard_statement_layout(include_translation):
    """Keep only the relevant public table layout and comparative rows."""
    parts = [
        dict(locator='第7页', text='四、主要会计数据和财务指标\n项目 本报告期 上年同期 本报告期比上年同期增减\n'
             '营业收入（元） 89,397,522,501.21 97,324,925,988.20 -8.15%\n'
             '归属于上市公司股东的净利润（元） 13,277,590,882.57 14,412,407,113.84 -7.87%\n'
             '归属于上市公司股东的扣除非经常性\n损益的净利润（元） 12,706,672,720.90 13,946,409,161.52 -8.89%\n'
             '经营活动产生的现金流量净额（元） 18,808,832,392.46 28,328,562,187.20 -33.60%'),
        dict(locator='第57页', text='3.合并利润表\n单位：元\n项目 2026年半年度 2025年半年度\n'
             '其中：营业收入 89,397,522,501.21 97,324,925,988.20\n'
             '其中：营业成本 63,639,640,359.69 69,597,138,607.41'),
        dict(locator='第61页', text='5.合并现金流量表\n单位：元\n项目 2026年半年度 2025年半年度\n'
             '销售商品、提供劳务收到的现金 77,830,304,463.72 94,018,299,765.07\n'
             '经营活动现金流入小计 80,011,516,724.49 103,599,563,230.53\n'
             '经营活动现金流出小计 61,202,684,332.03 75,271,001,043.33\n'
             '经营活动产生的现金流量净额 18,808,832,392.46 28,328,562,187.20'),
        dict(locator='第76页', text='本公司以人民币为记账本位币，本公司的个别子公司采用人民币以外的货币作为记账本位币。'),
    ]
    if include_translation:
        parts.append(dict(locator='第155页', text='合并财务报表中包含的香港子公司、巴西子公司、\n'
            '泰国子公司、美国子公司等大陆地区以外的国家和地区子公司的财务报表均已折算为人民币列示。'))

    def parse(task, name, payload, timeout, event, **kwargs):
        assert payload == PDF_BYTES
        return dict(name=name, status='已解析', hash=sha256(payload).hexdigest(), format='PDF', obtained_at=now(),
                    parts=deepcopy(parts), extract_version='pdf-tables-7')
    return parse


@pytest.mark.parametrize(('translation', 'legacy'), ((True, False), (False, False), (True, True)))
def test_numbered_consolidated_tables_require_a_reporting_currency_basis(tmp_path, translation, legacy):
    agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=False, draft_first=True,
                          require_argument_support=True, isolate_sources=True)
    try:
        with (patch('researchhelper.engine.now', return_value=CUTOFF),
              patch('researchhelper.source_transport.official_announcement', side_effect=_network),
              patch('researchhelper.source_transport.IsolatedEvidenceCollector._parse_official_material',
                    side_effect=_standard_statement_layout(translation))):
            task = agent.create(OPEN_QUESTION, research_mode='current')
            if legacy:
                task[OPEN_COMPANY_POLICY] = 1
                agent.archive.save(task)
            result = agent.start(task['id'])
        facts = observations(result)
        if legacy or not translation:
            assert not facts and result['status'] == '部分完成'
            return
        assert {row['unit'] for row in facts} == {'人民币元'} and len(facts) == 16
        assert all({ref['part'] for ref in row['locator']['unit_basis']['references']} == {'第76页', '第155页'}
                   for row in facts)
        assert result['status'] == '已完成'
        report = result['draft_report']
        assert '营收同比下降8.15%' in report['conclusion'] and '经营现金流净额下降33.6%' in report['conclusion']
        assert report['logics'][0]['title'] == '营收回落拖累毛利'
        assert any('营收继续回落' in row for row in report['risks'])
        for row, phrase in zip(report['logics'], ('毛利率回升缓和了收入收缩对毛利的影响', '非经常性损益净贡献增加',
                               '销售收现拖累'), strict=True):
            assert phrase in row['analysis'] and len(row['analysis']) >= 85
        assert verified_stock_quality_answer(result, report)['judgment'] == '收入增长前提未成立'
        frozen = agent.archive.report(task['id'], 1)['content']
        assert [figure['kind'] for figure in logic_figure_plan(frozen)] == ['stock_growth']
        detailed = html.document_fromstring(design(frozen, ROOT, profile='detailed')['html'])
        assert {'第7页', '第57页', '第61页'} <= set(re.findall(r'第\d+页', detailed.text_content()))
        assert detailed.xpath('//main//table//*[@data-observation-id]')
    finally:
        agent.close_runtime()


@pytest.mark.parametrize(('source_request', 'calibrated'), (('', True), ('不下载PDF。只用iFinD。', False)))
def test_open_question_uses_same_issuer_structured_data(tmp_path, source_request, calibrated):
    from researchhelper.stock_quality import CASH, COST, PROFIT, REVENUE

    calls = []
    available = {REVENUE: (1_000_000_000, 1_200_000_000), COST: (700_000_000, 900_000_000),
                 PROFIT: (110_000_000, 120_000_000), CASH: (160_000_000, 165_000_000)}

    def transport(task, source, operation, params, credentials):
        assert source == 'ifind' and credentials == {'access_token': 'fixture-only'}
        validate_source_request(task, source, operation, params)
        calls.append(operation)
        if operation == 'get_thscode':
            assert params['secname'] == '示例制造'
            payload = dict(errorcode=0, inputParams=dict(mode='secname', isexact=1),
                           tables=[dict(secname='示例制造', table=dict(thscode=['000333.SZ']))])
        elif operation == 'get_limit_policy':
            payload = dict(errorcode=0, qps_limit=dict(unit='requests_per_second', default_per_function=4, account_total=8),
                           request_data_limit=dict(unit='cells', rules=[dict(data_types=['basicData'], max_per_request=8)]))
        else:
            assert operation == 'basic_data_service' and params['codes'] == '000333.SZ'
            assert {item['indicator'] for item in params['indipara']} == available.keys()
            (period,) = {tuple(item['indiparams']) for item in params['indipara']}
            assert period in (('20250630', '1'), ('20260630', '1'))
            offset = int(period[0] == '20260630')
            payload = dict(errorcode=0, tables=[dict(thscode='000333.SZ', time=['2026-11-22'],
                table={field: [pair[offset]] for field, pair in available.items()})],
                inputParams=dict(params=[dict(function=field, params=[dict(name='FDREPORT', value=period[0]),
                    dict(name='FL', value='1')]) for field in available]))
        charge(task['ledger'], 'data_requests')
        record = snapshot_record(task, source, operation, params, payload)
        event = dict(kind='data_requests', source=source, operation=operation, status='成功', time=now(),
                     fingerprint=record['request_hash'], batch=task['batch'], transport_isolated=True,
                     network_request_started=True, network_worker_reaped=True)
        task['ledger']['dispatches'].append(event)
        return _record_current_acquisition(task, record, event)

    agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=False, draft_first=True, isolate_sources=True,
                          require_argument_support=True, source_credentials={'ifind': {'access_token': 'fixture-only'}})
    agent.collector.external = transport
    definitions = {field: dict(claims=dict(indicator=field, applicable_codes=['000333.SZ'],
        applicable_periods=['20250630', '20260630'], report_type='1', unit='元'),
        basis=dict(source='模拟口径', locator='合并报表单位', hash='a'*64, obtained_at='2026-09-01'))
        for field in available} if calibrated else {}
    try:
        with (patch('researchhelper.engine.now', return_value=CUTOFF),
              patch('researchhelper.draft_sources.evidence_definitions', return_value=definitions),
              patch('researchhelper.source_transport.official_announcement',
                    side_effect=AssertionError('The selected route must not fetch a filing'))):
            task = agent.create(OPEN_QUESTION + source_request, research_mode='current')
            result = agent.start(task['id'])
            rows = observations(result)
        assert calls == ['get_thscode', 'get_limit_policy', 'basic_data_service', 'basic_data_service']
        assert all(row['source'] == 'ifind' for row in result['source_records'])
        assert result['delivery'] and result['ledger']['used']['model_calls'] == 0
        assert {row['field'] for row in rows} == available.keys() and len(rows) == 8
        assert {row['unit'] for row in rows} == ({'元'} if calibrated else {'原始单位待核对'})
        report = result['draft_report']
        assert report['calculations'] and len(report['logics']) >= 2
    finally:
        agent.close_runtime()
