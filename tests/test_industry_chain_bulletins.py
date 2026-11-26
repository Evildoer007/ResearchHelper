from copy import deepcopy
from pathlib import Path
import json
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from lxml import html
import pytest

from researchhelper.archive import ResearchArchive
from researchhelper.chain_readout import verified_chain_answer
from researchhelper.contracts import ContractError
from researchhelper.draft_sources import observations
from researchhelper.draft_workflow import direct_readout
from researchhelper.draft_visuals import logic_figure_plan
from researchhelper.engine import ResearchAgent
from researchhelper.miit_releases import CHANNELS, ORIGIN
from researchhelper.official_sources import article_url, catalogue_url, parse_catalogue
from researchhelper.report_designer import design


ROOT = Path(__file__).resolve().parents[1]
QUESTION = '研究2026年上半年锂电产业链的供需、价格传导和主要风险，写一份研究报告。'
MATERIAL = '2026年上半年全国锂离子电池行业运行情况'
VEHICLES = '{year}年6月汽车工业经济运行情况'
CUTOFF = '2026-11-10T18:00:00+08:00'
BATTERY = (
    '上半年全国锂电池总产量超过1500GWh，同比增长30%。',
    '电池环节，上半年消费型、储能型和动力型锂电池产量分别为45GWh、455GWh、1005GWh。'
    '上半年，全国锂电池出口总额达到3500亿元，同比增长25%。',
    '二阶材料环节，上半年电池级碳酸锂产量60万吨，同比增长35%，电池级氢氧化锂产量20万吨，同比增长6%。'
    '上半年电池级碳酸锂和氢氧化锂（微粉级）均价分别为17.3万元/吨和14.2万元/吨。',
)


def vehicle_paragraphs(sales, exports, share):
    return ('6月，新能源汽车产销分别完成110万辆和120万辆，同比增长30%和25%；'
            '新能源汽车新车销量达到汽车新车总销量的60%。',
            f'1-6月，新能源汽车产销分别完成901万辆和{sales}万辆，同比增长5%和6%；'
            f'新能源汽车新车销量达到汽车新车总销量的{share}%。',
            f'1-6月，汽车整车出口400万辆，同比增长15%。其中，新能源汽车出口{exports}万辆，同比增长20%。',
            '（注：上述数据来自中国汽车工业协会。）')


def bulletin(year, name, department, day, paragraphs):
    body = ''.join('<p>' + value + '</p>' for value in paragraphs)
    return (f'<html><head><title>{name}</title><meta name="ArticleTitle" content="{name}">'
            f'<meta name="PubDate" content="{day} 11:20"><meta name="ContentSource" content="{department}">'
            f'</head><body><nav>{name}</nav><div id="con_con">{body}</div></body></html>').encode()


def path(channel, year, suffix):
    return CHANNELS[channel]['path'] + f'/art/{year}/art_{suffix:032x}.html'


def catalogue(*rows):
    fragment = ''.join(f'<li class="cf"><a class="fl" title="{title}" href="{url}">{title}</a>'
                       f'<span class="fr">{day}</span></li>' for url, title, day in rows)
    return json.dumps(dict(success=True, code='200', data={'html': fragment})).encode()


def provider(*, missing_prior=False):
    materials = path('miit_electronics', 2026, 1)
    current = path('miit_vehicles', 2026, 2)
    previous = path('miit_vehicles', 2025, 3)
    new_title, old_title = (VEHICLES.format(year=year) for year in (2026, 2025))
    materials_date, current_date, prior_date = '2026-09-20', '2026-07-12', '2025-07-12'
    current_list = (current, new_title, current_date)
    addresses = {catalogue_url('0', channel='miit_electronics'): catalogue((materials, MATERIAL, materials_date)),
         catalogue_url('0', channel='miit_vehicles'): catalogue(current_list),
         catalogue_url('1', channel='miit_vehicles'): catalogue((previous, old_title, prior_date)),
         ORIGIN + materials: bulletin(2026, MATERIAL, '电子信息司', materials_date, BATTERY),
         ORIGIN + current: bulletin(2026, new_title, '装备工业一司', current_date, vehicle_paragraphs('900', '300', '52')),
         ORIGIN + previous: bulletin(2025, old_title, '装备工业一司', prior_date, vehicle_paragraphs('850', '200', '48'))}
    requests = []

    def fetch(task, url, started, timeout, event, **kwargs):
        requests.append(url)
        event.update(transport_isolated=True, network_request_started=True, network_worker_reaped=True)
        if missing_prior and url == ORIGIN + previous:
            raise ContractError('原文暂缺')
        return addresses[url]
    return fetch, requests


@pytest.mark.parametrize('missing_prior', [False, True])
def test_natural_chain_study_fetches_dated_origins_and_composes_useful_research(tmp_path, missing_prior):
    archive = ResearchArchive(tmp_path)
    runtime = ResearchAgent(archive, ROOT, offline=False, draft_first=True, require_argument_support=True)
    fetch, requests = provider(missing_prior=missing_prior)
    try:
        with (patch('researchhelper.engine.now', return_value=CUTOFF),
              patch('researchhelper.source_transport.official_html', side_effect=fetch),
              patch('socket.socket.connect', side_effect=AssertionError('No external network in regression tests'))):
            task = runtime.create(QUESTION, research_mode='current')
            assert direct_readout(task)[0] == 'industry_chain'
            result = runtime.start(task['id'])
        assert len(requests) == result['ledger']['used']['data_requests'] == 6
        assert result['ledger']['used']['model_calls'] == 0
        assert result['delivery'], result.get('error')
        report = archive.report(task['id'], result['version'])
        shown = html.document_fromstring(design(report['content'], ROOT, profile='standard')['html'])
        visible = shown.find('.//main').text_content()
        assert '超过1,500GWh' in visible and '库存计价与采购合同重定价频率' in visible
        assert '碳酸锂产量增长快于氢氧化锂' in visible and '扩产' not in visible
        assert '120万辆' not in visible
        assert len(shown.xpath('//*[@id="logic-1"]')) == 1
        if missing_prior:
            assert result['status'] == '部分完成'
            assert '同比贡献暂无法拆分' in visible
        else:
            assert result['status'] == '已完成', (result['error'], result['gaps'])
            assert '外销扩张、内销收缩' in visible and '下降50万辆' in visible
            charts = logic_figure_plan(report['content'])
            assert [chart['field'] for chart in charts] == ['battery_mix', 'vehicle_channels']
            assert [len(chart['data_refs']) for chart in charts] == [3, 4]
            detailed = design(report['content'], ROOT, profile='detailed')['html']
            assert '两期分别从同口径原件取值' in detailed and '上述数据来自中国汽车工业协会' not in visible
            answer = result['questions'][0]['industry_chain_answer']
            assert answer == verified_chain_answer(result, result['draft_report'])
            damaged = deepcopy(result)
            raw = next(row for row in damaged['source_records'] if row['operation'] == 'release_original')
            raw['snapshot']['parts'][0]['text'] += ' 修改'
            assert not verified_chain_answer(damaged, result['draft_report'])
            assert all(row['artifact_id'] != raw['id'] for row in observations(damaged))
    finally:
        runtime.close_runtime()


def test_registered_miit_directory_rejects_unscoped_links_and_future_publications():
    channel = 'miit_vehicles'
    address = path(channel, 2026, 1)
    data = catalogue((address, '六月报告', '2026-07-01'), (path(channel, 2026, 2), '未来报告', '2026-11-12'),
                     ('https://other.example' + address, '非登记来源', '2026-01-01'))
    rows = parse_catalogue(data, CUTOFF, channel=channel)
    assert [row['url'] for row in rows['entries']] == [ORIGIN + address]
    assert rows['entries'][0]['published_at'] is None and rows['coverage']['skipped_future'] == 1
    assert json.loads(parse_qs(urlsplit(catalogue_url('2', channel=channel)).query)['paramJson'][0])['pageNo'] == 3
    for invalid in ('https://other.example' + address, ORIGIN + address + '?download=1'):
        with pytest.raises(ContractError):
            article_url(invalid, channel=channel)


@pytest.mark.parametrize('question', [QUESTION + ' 预测个股目标价。', '研究2026年下半年锂电产业链及价格。',
                                      QUESTION + ' 只用iFinD。', QUESTION + ' 统计美国的销量。'])
def test_chain_route_does_not_capture_tasks_it_cannot_answer(tmp_path, question):
    online = ResearchAgent(ResearchArchive(tmp_path), ROOT, offline=True, draft_first=True)
    try:
        with patch('researchhelper.engine.now', return_value=CUTOFF):
            task = online.create(question, research_mode='current')
        merely = direct_readout(task)
        assert not merely or merely[0] != 'industry_chain'
    finally:
        online.close_runtime()
