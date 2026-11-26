from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from lxml import html
import pytest

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.draft_sources import observations
from researchhelper.draft_visuals import _calculation_dependencies, logic_figure_plan
from researchhelper.draft_workflow import direct_readout
from researchhelper.engine import ResearchAgent
from researchhelper.macro_readout import verified_macro_answer
from researchhelper.monthly_statistics import frozen_retail_refs, study_scope
from researchhelper.official_sources import catalogue_url
from researchhelper.report_designer import design

ROOT = Path(__file__).resolve().parents[1]
CUTOFF = '2026-11-23T10:00:00+08:00'
QUESTION = '验证2026年8月至10月国内物价上涨是否反映内需普遍改善，用统计局公布的资料写一份研究报告。'
RETAIL_QUESTION = '最近国内消费回暖了吗？看社零、商品零售和餐饮，分析哪里在拖累，写一份研究报告。'


def mocked_bulletins(*, omit=None, support=False, conflicting=False, composition=False, paginated=False):
    def article(title, date, paragraphs):
        body = ''.join('<p>'+p+'</p>' for p in paragraphs)
        return (f'<html><head><meta name="ArticleTitle" content="{title}"><meta name="PubDate" '
                f'content="{date.replace("-", "/")} 10:00"></head><body>{body}{body}</body></html>').encode()
    responses, links = {}, []
    for n, rates in enumerate((('0.8', '1.0', '3.8', '0.4', '2.5'), ('0.9', '1.0', '5.0', '1.1', '3.9'),
                               ('1.5', '1.4', '5.7', '2.0', '4.5') if support else ('0.7', '0.8', '5.7', '0.4', '2.5')), 8):
        cpi, core, ppi, retail, auto = rates
        price_day, retail_day = f'2026-{n+1:02d}-09', f'2026-{n+1:02d}-15'
        price_title = f'国家统计局城市司首席统计师解读2026年{n}月份CPI和PPI数据'
        retail_title = f'2026年1—{n}月份社会消费品零售总额增长7.8%'
        paragraphs = [f'{n}月份，居民消费价格指数（CPI）环比持平；同比上涨{cpi}%，'
                      f'涨幅比上月回落0.2个百分点；扣除食品和能源价格的核心CPI同比上涨{core}%。'
                      f'工业生产者出厂价格指数（PPI）环比上涨0.2%，同比上涨{ppi}%。',
                      f'从同比看，全国PPI上涨{ppi}%，上述9个行业合计影响PPI同比上涨约6.14个百分点。'
                      '5个行业合计影响PPI同比下降约0.60个百分点。']
        if conflicting and n == 10:
            paragraphs += [paragraphs[0].replace(f'核心CPI同比上涨{core}', '核心CPI同比上涨5.0')]
        total, non_auto, goods, dining, growth, dining_growth = {
            8: ('39,824', '36,545', '35,280', '4,544', '0.3', '1.1'),
            9: ('42,433', '38,792', '37,830', '4,603', '1.0', '2.1'),
            10: ('46,463', '43,162', '41,228', '5,235', '0.3', '0.7'),
        }[n]
        retail_body = [f'1—{n}月份，社会消费品零售总额270001亿元，同比增长7.8%。'
                       f'{n}月份，社会消费品零售总额{total}亿元，同比增长{retail}%。'
                       f'其中，除汽车以外的消费品零售额{non_auto}亿元，增长{auto}%。']
        if composition and not (conflicting and n == 10):
            retail_body.append(f'按消费类型分，1—{n}月份，商品零售额800000亿元，同比增长8.9%；餐饮收入600000亿元，增长8.1%。'
                               f'{n}月份，商品零售额{goods}亿元，同比增长{growth}%；餐饮收入{dining}亿元，增长{dining_growth}%。')
        for kind, date, title, body in (
                ('price', price_day, price_title, paragraphs),
                ('retail', retail_day, retail_title, retail_body)):
            path = f'/sj/zxfb/{date[:7].replace("-", "")}/t{date.replace("-", "")}_{n*10+(kind=="retail")}.html'
            url = 'https://www.stats.gov.cn' + path
            links.append(f'<a href="{path}">{title}</a>')
            responses[url] = None if omit == (kind, n) else article(title, date, body)
    responses[catalogue_url('0')] = ('<html><body>'+''.join(links)+'</body></html>').encode()
    if paginated:
        pages = [links[2:], links[:1], links[:1], links[:1], links[1:2]]
        for page, entries in enumerate(pages):
            navigation = '<script>\ncreatePageHTML(5,0,"index","html");\n</script>' if page == 0 else ''
            responses[catalogue_url(str(page), maximum_page=4)] = (
                '<html><body>' + navigation + ''.join(entries) + '</body></html>').encode()

    def send(task, url, started, seconds, event, **kwargs):
        event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
        if responses[url] is None:
            raise ContractError('本次公报响应缺失')
        return responses[url]
    return send


@pytest.mark.parametrize(('missing', 'support', 'conflicting'), [
    (None, False, False), (None, True, False), (('retail', 9), False, False), (None, False, True),
])
def test_macro_claim_is_evaluated_only_against_the_same_months(tmp_path, missing, support, conflicting):
    archive = ResearchArchive(tmp_path)
    runtime = ResearchAgent(archive, ROOT, offline=False, draft_first=True, require_argument_support=True)
    try:
        with (patch('researchhelper.engine.now', return_value=CUTOFF),
              patch('researchhelper.source_transport.official_html', side_effect=mocked_bulletins(
                  omit=missing, support=support, conflicting=conflicting)),
              patch('socket.socket.connect', side_effect=AssertionError('No external calls'))):
            task = runtime.create(QUESTION, entry='宏观', intent='验证观点', research_mode='current')
            assert direct_readout(task)[0] == 'macro_demand'
            result = runtime.start(task['id'])
        assert result['delivery'], result.get('error')
        assert result['ledger']['used']['data_requests'] == 7 and result['ledger']['used']['model_calls'] == 0
        report = archive.report(task['id'], 1)
        page = html.document_fromstring(design(report['content'], ROOT, profile='standard')['html'])
        shown = page.find('.//main').text_content()
        assert page.xpath('//dl[@class="paper-dateline"]/time/@datetime | //dl[@class="paper-dateline"]/dd/time/@datetime') == ['2026-08','2026-10']
        assert '7.8%' not in shown and '0.60个百分点' not in shown
        if missing or conflicting:
            assert result['status'] == '部分完成'
            assert '关键指标尚未取齐' in shown
        else:
            assert result['status'] == '已完成', (result['error'], result['gaps'])
            answer = result['questions'][0]['macro_demand_answer']
            assert answer == verified_macro_answer(result, result['draft_report'])
            assert answer['judgment'] == ('初步支持' if support else '缺乏同期支持')
            assert '6.14个百分点' in shown and '0.6个百分点' in shown
            assert [chart['field'] for chart in logic_figure_plan(report['content'])] == ['cpi_core','retail_ex_auto']
            copied = deepcopy(result)
            one = next(row for row in copied['source_records'] if row['operation'] == 'release_original')
            one['snapshot']['parts'][0]['text'] += '篡改'
            assert not verified_macro_answer(copied, result['draft_report'])
            assert all(row['artifact_id'] != one['id'] for row in observations(copied))
    finally:
        runtime.close_runtime()


@pytest.mark.parametrize('question', [QUESTION + ' 对比美国真实销量。', QUESTION + ' 追加利率收益率。',
                                       QUESTION + ' 只用iFinD。', QUESTION.replace('8月至10月', '1月至11月'),
                                       QUESTION.replace('国内物价', '德国物价'), QUESTION.replace('改善', '走弱')])
def test_uncovered_requirements_do_not_enter_the_deterministic_study(tmp_path, question):
    agent = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True, draft_first=True)
    try:
        with patch('researchhelper.engine.now', return_value=CUTOFF):
            task = agent.create(question, research_mode='current')
        assert study_scope(task) is None
    finally:
        agent.close_runtime()


@pytest.mark.parametrize(('missing_types', 'paginated'), [(False, True), (True, False)])
def test_open_retail_from_latest_publications_and_attribution(tmp_path, missing_types, paginated):
    archive = ResearchArchive(tmp_path)
    runtime = ResearchAgent(archive, ROOT, offline=False, draft_first=True, require_argument_support=True)
    try:
        with (patch('researchhelper.engine.now', return_value=CUTOFF),
              patch('researchhelper.source_transport.official_html', side_effect=mocked_bulletins(
                  composition=True, conflicting=missing_types, paginated=paginated)) as calls,
              patch('socket.socket.connect', side_effect=AssertionError('No external calls'))):
            task = runtime.create(RETAIL_QUESTION, research_mode='current')
            assert direct_readout(task)[1]['periods'] == []
            result = runtime.start(task['id'])
        assert study_scope(result)['periods'] == ['2026-08', '2026-09', '2026-10']
        assert result['ledger']['used']['data_requests'] == calls.call_count == (8 if paginated else 4)
        assert result['ledger']['used']['model_calls'] == 0
        assert result['status'] == ('部分完成' if missing_types else '已完成')
        assert result['delivery']
        content = archive.report(task['id'], 1)['content']
        facts = content['draft']['facts']
        assert frozen_retail_refs(content, facts) == {row['id'] for row in facts}
        if paginated:
            changed = deepcopy(content)
            changed['monthly_catalogue_dispatch']['response_snapshot_hash'] = 'different'
            assert len(frozen_retail_refs(changed, facts)) == 16
        page = html.document_fromstring(design(content, ROOT, profile='standard')['html'])
        shown = page.find('.//main').text_content()
        assert all(word in shown for word in ('光大证券', '核心结论', '社零总量与回暖节奏', '汽车拖累与社零降温的来源', '风险提示'))
        assert '数据期间' in shown
        assert '非汽车消费放缓主导本月回落' in shown
        assert all(word not in shown for word in ('7.8%', '8.9%', '8.1%', '缺少', '尚未取齐', '研究草稿', '独立复核'))
        assert [chart['field'] for chart in logic_figure_plan(content)] == ['retail_ex_auto', 'retail_change']
        replay = _calculation_dependencies(content)
        points = {(row['component'], row['period']): row['display'] for row in replay.values()
                  if row.get('comparison_kind') == 'nbs_retail_contribution'}
        assert points['汽车', '2026-10'] == '-1.9'
        if missing_types:
            assert '商品零售与餐饮的贡献' not in shown
            assert 'macro_demand_answer' not in result['questions'][0]
        else:
            answer = result['questions'][0]['macro_demand_answer']
            assert answer == verified_macro_answer(result, result['draft_report'])
            assert '商品零售与餐饮的贡献' in shown and len(facts) == 24
            assert answer['judgment'] == '阶段性回升后回落'
            changed = deepcopy(content)
            fact = next(row for row in changed['draft']['facts'] if row['field'] == 'nbs_retail' and row['period'] == '2026-10')
            fact.update(raw_value='75', value=75.0)
            assert fact['id'] not in frozen_retail_refs(changed, changed['draft']['facts'])
            assert not logic_figure_plan(changed)
    finally:
        runtime.close_runtime()
