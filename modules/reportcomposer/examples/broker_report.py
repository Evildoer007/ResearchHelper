"""Create a compact research paper from the user's frozen broker-sector case.

Only public content from the supplied PDF is used. No market data, model calls,
precise selling-flow estimates or forward returns are manufactured here.
"""
from __future__ import annotations

import hashlib
import json
import re
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from core.bootstrap import configure_imports
configure_imports()
from pypdf import PdfReader
from researchhelper.visual import inspection_link, inspection_surface, INSPECTION_SCRIPT

SOURCE = Path('/Users/haoranxu/Desktop/光大金创实习/材料/场外衍生品投资策略 — 券商板块投资机会0720.pdf')
OUTPUT = ROOT / 'result/reports/broker-2026-07'
PALETTE = 'research'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_case():
    text = ' '.join(PdfReader(SOURCE).pages[0].extract_text().split())
    normalized = text.replace('−', '-').replace('–', '-')
    names = ['证券Ⅱ', '通信设备', '商贸零售', '电力', '电子', '房地产']
    rows = []
    for name in names:
        match = re.search(re.escape(name) + r'\s+([+-]?\d+\.\d+)%\s+([+-]?\d+\.\d+)%', normalized)
        if match is None:
            raise ValueError('原材料缺少该板块的两项原值：' + name)
        rows.append(dict(name=name, price=float(match[1]), revenue=float(match[2]), quote=match[0]))
    heads = re.search(r'中信\s*\+([\d.]+)%、华泰\s*\+([\d.]+)%、广发\s*\+([\d.]+)%', text)
    assert heads
    for token in ['1.5万亿元', '735 亿元', '同比增长32%', '33%', '同比提升4pct',
                  '1.8%-1.9%', '3.8%-4.0%', '1.37x', '27%分位', '10%-13%', '69.59%', '股价普遍下跌10%-20%']:
        assert token in normalized, token
    observations = [
        dict(name='非银', observation='持有规模最大，减持最重'),
        dict(name='医药', observation='减持明显'),
        dict(name='食品饮料', observation='减持较多'),
        dict(name='地产', observation='减持明显'),
        dict(name='银行', observation='减持温和'),
    ]
    characteristics = ['量价背离最显著', '同样背离', '股价超跌', '营收强但未涨', '量价同步（正面）', '量价双杀']
    for observation in observations:
        assert observation['name'] in text
    for characteristic in characteristics:
        assert characteristic in text, characteristic
    return dict(
        title='券商板块投资机会', date='2026年7月', cutoff='2026年6至7月',
        source_type='user_provided_strategy_pdf', source_sha256=digest(SOURCE),
        source_label='券商板块投资机会，2026年7月', palette=PALETTE,
        figure_source_status='原材料列示，未新增联网复核', authoring_mode='offline_strategy_case_reconstruction',
        sectors=rows,
        heads=[dict(name=name, revenue=float(value)) for name, value in zip(['中信证券', '华泰证券', '广发证券'], heads.groups())],
        reduction=dict(trillion=1.5, completion='九成以上', status='原材料估计'),
        valuation=dict(pb=1.37, percentile=27, percentile_qualifier='约', window='2016年以来', roe=[10, 13]),
        profit=dict(name='中信证券', period='2026H1', growth=69.59, status='材料业绩预告口径'),
        dividends=dict(year=2025, cash=735, cash_unit='亿元', cash_growth=32, payout=33, payout_change_pp=4, a=[1.8, 1.9], h=[3.8, 4.0]),
        instrument=dict(name='券商ETF', code='512000.SH'),
        selling_observations=observations, sector_characteristics=characteristics,
        head_price_observation=dict(decline_pct=[10, 20], period='原材料所称同期，未给出逐只价格与期间明细'),
        dividend_observations=['A股低于银行，但边际改善', 'H股券商已接近银行的红利水位'],
        model_calls=0, data_requests=0,
    )


def visual_evidence(data):
    """Deterministic display analytics; raw evidence is never overwritten."""
    sectors=data['sectors'];values=[row['revenue'] for row in data['heads']]
    quadrants=dict(down_up=0,up_up=0,down_down=0,up_down=0,flat=0)
    for row in sectors:
        x,y=row['price'],row['revenue']
        key=('down_' if x<0 else 'up_')+('up' if y>0 else 'down') if x!=0 and y!=0 else 'flat'
        quadrants[key]+=1
    average=sum(values)/len(values)
    dividends=data['dividends']
    return dict(schema='researchhelper.visual-evidence.v1',
        sector=dict(sample_count=len(sectors),quadrants=quadrants),
        earnings=dict(sample_count=len(values),mean=average,deviations_pp=[round(value-average,8) for value in values],spread_pp=max(values)-min(values)),
        valuation=dict(reference_percentile=50,distance_percentile_points=50-data['valuation']['percentile']),
        payout=dict(current=dividends['payout'],change_pp=dividends['payout_change_pp'],prior_inferred=dividends['payout']-dividends['payout_change_pp'],prior_is_inferred=True),
        yields=dict(low_spread_pp=round(dividends['h'][0]-dividends['a'][1],8),high_spread_pp=round(dividends['h'][1]-dividends['a'][0],8)),
        methods=[dict(id='sign_quadrants',inputs=['sectors.price','sectors.revenue'],unit='listed_sector_count'),
                 dict(id='arithmetic_mean_and_difference',inputs=['heads.revenue'],unit='percentage_points'),
                 dict(id='distance_to_percentile_scale_midpoint',inputs=['valuation.percentile'],unit='percentile_points'),
                 dict(id='subtract_reported_change',inputs=['dividends.payout','dividends.payout_change_pp'],unit='percent_inferred'),
                 dict(id='interval_difference',inputs=['dividends.a','dividends.h'],unit='percentage_points')])


def figures(data):
    cash = data['dividends']
    facts = visual_evidence(data)
    return [
        dict(target='valuation-chart', scheme='scatter', title='股价与营收背离', data=dict(points=[dict(name=row['name'], x=row['price'], y=row['revenue'], feature=feature) for row, feature in zip(data['sectors'], data['sector_characteristics'])], x_unit='%', y_unit='%', context=facts['sector'])),
        dict(target='earnings-chart', scheme='ranked_bar', title='头部券商Q1营收', data=dict(labels=[row['name'] for row in data['heads']], values=[row['revenue'] for row in data['heads']], unit='%', context=facts['earnings'])),
        dict(target='pb-chart', scheme='percentile', title='2016年以来PB历史位置', data=dict(**data['valuation'], context=facts['valuation'])),
        dict(target='cash-chart', scheme='cash_index', title='现金分红规模扩张', data=dict(cash=cash['cash'], growth=cash['cash_growth'], baseIndex=100, currentIndex=100*(1+cash['cash_growth']/100), increment=cash['cash_growth'], unit='base_index_2024_100', transformation='reported_yoy_to_base_index')),
        dict(target='payout-chart', scheme='payout_units', title='2025平均分红比例', data=dict(value=cash['payout'], change=cash['payout_change_pp'], total=100, unit='percentage_point_not_company', context=facts['payout'])),
        dict(target='dividend-chart', scheme='range', title='H股股息率更高', data=dict(ranges=[dict(name='A股券商', min=cash['a'][0], max=cash['a'][1]), dict(name='H股券商', min=cash['h'][0], max=cash['h'][1])], unit='%', context=facts['yields'])),
    ]


def evidence(data, specs):
    rows=''.join('<tr><th>'+row['name']+'</th><td>'+f"{row['price']:+.2f}%"+'</td><td>'+f"{row['revenue']:+.2f}%"+'</td><td>'+feature+'</td></tr>' for row,feature in zip(data['sectors'],data['sector_characteristics']))
    bodies={
        'valuation-chart':'<p>股价窗口为2026年1月14日至5月31日，营收为2026年Q1同比。散点的横纵坐标分别使用这两项原值，铅垂线只辅助读数，不代表经济因果。点大小只突出证券Ⅱ，不编码持仓规模。材料列示的6个板块中，4个为股价下跌、营收增长，1个同步增长、1个同步下跌；这不是全市场占比或未来概率。</p><div class="table-wrap" tabindex="0"><table><thead><tr><th>板块</th><th>股价</th><th>Q1营收</th><th>材料观察</th></tr></thead><tbody>'+rows+'</tbody></table></div>',
        'earnings-chart':'<p>Q1营收同比：中信证券69%、华泰证券52%、广发证券41%；每道刻线为1个百分点，不代表公司样本数。材料所列三家的算术均值为54%，中信、华泰、广发分别较该均值高15、低2、低13个百分点；这不是全行业平均。中信证券2026H1归母净利润同比69.59%按材料业绩预告口径列示，未与Q1营收混用。</p><p>原材料称同期头部券商股价普遍下跌10%至20%，未给出逐只价格与期间明细。</p>',
        'pb-chart':'<p>材料给出行业PB1.37倍，自2016年以来约27%分位；头部券商ROE10%至13%。分位尺定位材料列示的约27%，距分位尺度中点50约23个分位点；50只是刻度参照，不代表估值中枢或合理PB。ROE另用0%至15%的独立轴表示10%至13%的材料区间，没有虚构历史估值分布、合理估值阈值或未来上涨概率。</p><p>原材料未提供完整估值样本，本图按原时点复现，不表示独立验证通过。</p>',
        'cash-chart':'<p>2025年上市券商现金分红含中期为735亿元，同比增长32%。图中将2024年设为基准指数100，依据材料同比32%得到2025年指数132。</p><p>100和132仅为分红规模指数，不是现金金额；没有倒推2024年现金分红，也没有补造历史序列。</p>',
        'payout-chart':'<p>2025年平均分红比例33%，同比提高4个百分点。100点阵每点表示1个百分点，其中29点以黑色表示由33%-4个百分点推算的上年约29%，4点红色表示本期增量；推算值不是新增独立观测。33点合计对应本期33%，这些点不是公司数量、客户样本或收益概率。</p><p>灰色点仅构成100%的参考尺度，不用于推算资金留存金额。平均比例及其增量可能经过四舍五入，上年29%仅作同口径近似推算。</p>',
        'dividend-chart':'<p>A股券商平均股息率1.8%至1.9%，H股3.8%至4.0%。线段只表示所列区间，不是预测或置信区间，没有添加中点。H减A的区间差按[3.8-1.9,4.0-1.8]计算，得到1.9至2.2个百分点；它不是同一公司A/H股息差，也不是可套利收益。</p><p>原材料认为A股股息率低于银行，H股已接近银行红利水位；未提供银行横向明细，故保留为材料判断。实际回报仍受价格、汇率及派息持续性影响。</p>',
    }
    selling_rows=''.join('<tr><td>'+row['name']+'</td><td>'+row['observation']+'</td></tr>' for row in data['selling_observations'])
    selling='<section id="basis-selling" data-inspection-title="卖压数据口径"><h3>卖压数据口径</h3><p>来源：'+data['source_label']+'，第1页。累计减持约1.5万亿元、完成九成以上，均为原材料估计，未新增联网复核。</p><table><thead><tr><th>板块</th><th>材料观察</th></tr></thead><tbody>'+selling_rows+'</tbody></table><p>本项没有逐笔减持或连续资金流原值，不绘制卖压时间序列。减持趋缓对定价压制的缓解属于条件性研究逻辑，不等于未来收益已经确认。</p><p>验证条件：关注后续减持是否继续收缩，以及成交、自营及投行业务是否同步支持盈利兑现；减持反复会削弱原判断。</p></section>'
    records=[selling]
    for spec in specs:
        body='<p>来源：用户提供的2026年7月券商板块研究材料，第1页。按原时点离线复现，未新增联网复核。</p>'+bodies[spec['target']]
        records.append('<section id="basis-'+spec['target']+'" data-inspection-title="'+spec['title']+'"><h3>'+spec['title']+'</h3>'+body+'</section>')
    return inspection_surface(records)


CSS = Path(__file__).with_name('broker-paper.css').read_text()
RENDERER = ROOT / 'modules/visualrenderer/assets/evidence-charts.js'

JS = r'''
window.mountResearchPaper=()=>RHEvidenceCharts.mount({data:__DATA__,specs:__FIGURES__,onSource:({target})=>{
 const link=document.querySelector('a[href="#basis-'+target+'"]');
 if(link){link.focus({preventScroll:true});link.click()}
}});
window.RHStrategyPaper=window.mountResearchPaper();
const links=[...document.querySelectorAll('nav a')];
function activateContents(hash){const active=links.find(link=>link.hash===hash);if(active)for(const link of links)link.setAttribute('aria-current',String(link===active))}
activateContents(location.hash||links[0].hash);addEventListener('hashchange',()=>activateContents(location.hash));
'''



def render(data,profile="standard"):
    specs=figures(data)
    def figure(index):
        spec=specs[index]
        return '<figure class="chart-figure"><figcaption>'+inspection_link('basis-'+spec['target'],spec['title'])+'</figcaption><div class="chart" id="'+spec['target']+'" role="img" aria-label="'+spec['title']+'"></div></figure>'
    body='''
<main>
<header class="report-header"><div class="report-head__copy"><h1>券商板块投资机会</h1></div><dl class="report-meta"><div><dt>资料时点</dt><dd>2026年6至7月</dd></div></dl></header>
<section id="conclusion"><h2>核心结论</h2><p class="conclusion-band"><strong>卖压趋缓、盈利改善与分红提升</strong>共同支撑券商板块修复。<span class="core-direction">低位估值能否转化为收益，取决于盈利兑现与减持延续收缩。</span></p></section>
<section class="strategy" id="logic-1"><h2>策略逻辑一：卖压衰减，交易性压制有望缓解</h2>
<p>2026年初以来，材料估计特定机构累计减持<strong class="anchor-number">约1.5万亿元</strong>，占减持前规模<strong class="anchor-number">九成以上</strong>。若基本面未同步恶化，卖盘收缩有望减轻交易性定价压制。</p>
<p>非银持有规模最大、减持最重；医药与地产减持明显，食品饮料减持较多，银行减持温和。</p></section>
<section class="strategy" id="logic-2"><h2>策略逻辑二：盈利改善与低位估值，形成重估基础</h2>
<p>证券Ⅱ、通信设备股价与营收背离；商贸零售超跌、电力营收强而股价弱，电子同步增长，房地产同步回落。中信证券H1净利预告增长<strong>69.59%</strong>，头部券商同期股价普遍下跌10%至20%，估值修复仍需盈利兑现。</p>
__SCATTER____EARNINGS____PB__</section>
<section class="strategy" id="logic-3"><h2>策略逻辑三：分红提升，增强持有期现金回报</h2>
<p>现金分红与支付比例同步提升，为等待估值修复提供现金流补偿。原材料认为A股股息率低于银行，H股已接近银行红利水位；仍需考虑价格、汇率与派息持续性。</p>
__CASH____PAYOUT____YIELD__</section>
<p class="instrument-line" id="instrument"><strong>研究载体：券商ETF512000.SH。</strong>覆盖头部上市券商，关注卖压缓解、盈利修复与分红改善。</p>
<section id="risk"><h2>风险提示</h2><p>减持反复、成交走弱、自营波动或投行业务恢复不及预期，均可能削弱修复逻辑。盈利与分红可能不持续；股息不等于保本。</p></section>
<footer class="report-source-line">资料：2026年7月券商板块研究材料。历史案例，未更新行情。</footer>
</main>'''
    for marker,index in [('__SCATTER__',0),('__EARNINGS__',1),('__PB__',2),('__CASH__',3),('__PAYOUT__',4),('__YIELD__',5)]:body=body.replace(marker,figure(index))
    body=body.replace('策略逻辑一：卖压衰减，交易性压制有望缓解</h2>',inspection_link('basis-selling','策略逻辑一：卖压衰减，交易性压制有望缓解')+'</h2>')
    encode=lambda value:json.dumps(value,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
    scripts='<script>'+(ROOT/'assets/vendor/echarts.min.js').read_text()+'</script><script>'+RENDERER.read_text()+'</script><script>'+JS.replace('__DATA__',encode(data)).replace('__FIGURES__',encode(specs))+INSPECTION_SCRIPT+'</script>'
    from researchhelper.report_designer import compose_strategy_document
    from lxml import etree
    source='<html><head><title>'+data['title']+'</title></head><body>'+body+evidence(data,specs)+scripts+'</body></html>'
    document=compose_strategy_document(source,ROOT,figures={spec['target']:spec for spec in specs},profile=profile)
    return '<!doctype html>\n'+etree.tostring(document,encoding='unicode',method='html')


def build(output=OUTPUT):
    previous=socket.socket.connect
    def denied(*_args,**_kwargs):raise RuntimeError('本报告只读取本地材料，不允许外部调用')
    socket.socket.connect=denied
    try:
        before=digest(SOURCE);data=load_case();output=Path(output);output.mkdir(parents=True,exist_ok=True)
        report=output/'券商板块策略报告.html';report.write_text(render(data));(output/'report-data.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
        (output/'visual-evidence.json').write_text(json.dumps(visual_evidence(data),ensure_ascii=False,indent=2))
        assert digest(SOURCE)==before
        manifest=dict(status='offline_case_report_written',source_sha256=before,source_unchanged=True,model_calls=0,data_requests=0,figures=6,numeric_figures=6,strategy_logics=3,palette=PALETTE,
            presentation_palette=['#C8102E','#252525','#936719','#6B6B6B','#C9C9C9'],visual_system='detailed_report_examples',presentation='six_evidence_compact_paper',
            paper_width_mm=210,paper_height_mm=297,theme_sha256=digest(Path(__file__).with_name('broker-paper.css')),html_sha256=digest(report),data_sha256=digest(output/'report-data.json'),generator_sha256=digest(Path(__file__)),
            renderer_sha256=digest(RENDERER),chart_methods=[spec['scheme'] for spec in figures(data)],visual_references=['F8 Plumb Scatter','F5 Tick Rows','F11 percentile ruler adaptation','F9 indexed waterfall','L14 percentage-unit field','F12 observed endpoints'],
            source_scope='user_provided_case_material_not_new_provider_verification',removed=['institution_identifiers','derivative_recommendations','quotes','repeated_method_notes'],independent_financial_quality_approved=False)
        (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2));print(json.dumps(dict(report=str(report),logics=3,figures=6,model_calls=0,data_requests=0),ensure_ascii=False));return report
    finally:socket.socket.connect=previous


if __name__=='__main__':build()
