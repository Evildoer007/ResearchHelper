"""Complete offline research paper using RH's shared chart grammar and retained data.

No App pipeline, installed component or original research artifact is modified.
The editorial prose is new; every numeric chart input remains traceable to the
retained iFinD observations and registered percentage-point calculations.
"""
from __future__ import annotations

import hashlib
import html
import json
import socket
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from core.bootstrap import configure_imports
configure_imports()
from researchhelper.draft_analysis import execute, formula_mathml
from researchhelper.visual import inspection_link, inspection_surface, INSPECTION_SCRIPT
from researchhelper.visual_catalog import theme

SOURCE = ROOT / 'result/acceptance/one-sentence-research-lfu94vrd/report.json'
OUTPUT = ROOT / 'result/reports/cpi-2026-09'
SERIES = [('M002826730', '总体'), ('M002826732', '食品'), ('M002840897', '消费品')]
PALETTE = 'teal'


def offline_only(*_args, **_kwargs):
    raise RuntimeError('本报告仅使用留存文件，不允许外部调用')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_data():
    content = json.loads(SOURCE.read_text())
    owned = {row['id']: row for row in content['source_records']
             if row.get('task_id') == content['task_id']
             and row.get('research_version') == content['research_version']
             and row.get('source') == 'ifind' and row.get('operation') == 'edb_service'}
    facts = content['draft']['facts']
    assert len(facts) == 27 and len({row['id'] for row in facts}) == 27
    assert all(row['artifact_id'] in owned and row['unit'] == '%' and row['field'] == 'value' for row in facts)
    assert '国家统计局' in json.dumps(content['source_records'], ensure_ascii=False)
    lookup = {(row['code'], row['period']): row for row in facts}
    dates = sorted({row['period'] for row in facts})
    assert len(lookup) == 27 and len(dates) == 9
    matrix = [[lookup[code, day]['value'] for day in dates] for code, _ in SERIES]
    calculations = content['draft']['calculations']
    assert len(calculations) == 6
    for row in calculations:
        result = execute(row['method'], row['inputs'])
        assert result['value'] == row['value'] and result['display'] == row['display']
    retrieved = min(row['obtained_at'] for row in owned.values())
    return dict(
        title='CPI回升中的结构分化', period='2026年1至9月', issued='2026-10-27',
        palette=PALETTE, palette_name=theme(PALETTE)['name'],
        origin='国家统计局', channel='iFinD',
        retrieved_at=datetime.fromisoformat(retrieved).astimezone(ZoneInfo('Asia/Shanghai')).isoformat(),
        authoring_mode='offline_editorial_report_from_retained_observations',
        real_model_calls=0, real_data_requests=0,
        dates=dates, series=[dict(code=code, name=name) for code, name in SERIES],
        matrix=matrix, facts=facts, calculations=calculations,
        original_report_sha256=digest(SOURCE),
    )


def chart_specs(data):
    labels = [day[:7] for day in data['dates']]
    names = [row['name'] for row in data['series']]
    values = data['matrix']
    current = {row['id']: row for row in data['calculations']}
    return [
        dict(target='trend-chart', scheme='multi_line',
             title='总体回升与分项分化并存',
             data=dict(labels=labels, series=[dict(name=name, values=rows) for name, rows in zip(names, values)], unit='%'),
             data_refs=[row['id'] for row in data['facts']]),
        dict(target='marginal-chart', scheme='diverging_bar',
             title='9月的同比变化方向仍不一致',
             data=dict(labels=names, values=[current[key]['value'] for key in ('A2', 'A4', 'A6')], unit='个百分点'),
             calculation_refs=['A2', 'A4', 'A6'],
             data_refs=[ref for key in ('A2', 'A4', 'A6') for ref in current[key]['data_refs']]),
        dict(target='matrix-chart', scheme='heatmap',
             title='食品同比自4月起连续六个月为负',
             data=dict(labels=labels, rows=names, matrix=values, unit='%'),
             data_refs=[row['id'] for row in data['facts']]),
        dict(target='endpoint-chart', scheme='dumbbell',
             title='首尾读数改善没有覆盖全部分项',
             data=dict(labels=names, periods=['2026年1月', '2026年9月'],
                       pairs=[[row[0], row[-1]] for row in values], unit='%'),
             calculation_refs=['A1', 'A3', 'A5'],
             data_refs=[ref for key in ('A1', 'A3', 'A5') for ref in current[key]['data_refs']]),
    ]


def data_table(data, *, raw_attributes=False):
    lookup = {(row['code'], row['period']): row for row in data['facts']}
    body = []
    for day in data['dates']:
        cells = []
        for code, _ in SERIES:
            row = lookup[code, day]
            attribute = (' data-observation="' + row['id'] + '" data-raw-value="'
                         + html.escape(str(row['raw_value']), quote=True) + '"') if raw_attributes else ''
            cells.append('<td' + attribute + '>' + f"{row['value']:.2f}" + '</td>')
        body.append('<tr><th scope="row">' + day[:7] + '</th>' + ''.join(cells) + '</tr>')
    return ('<div class="table-wrap" tabindex="0" aria-label="9个月完整同比数据"><table>'
            '<thead><tr><th scope="col">月份</th><th scope="col">总体</th><th scope="col">食品</th>'
            '<th scope="col">消费品</th></tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>')


def evidence_records(data, specs):
    records = []
    facts = {row['id']: row for row in data['facts']}
    calculations = {row['id']: row for row in data['calculations']}
    for spec in specs:
        rows = [facts[key] for key in spec['data_refs']]
        header = '<section id="basis-' + spec['target'] + '" data-inspection-title="' + spec['title'] + '">'
        text = ('<h3>' + spec['title'] + '</h3><p>来源：国家统计局；取数渠道：iFinD。'
                '观测为月度同比，单位为%。数据于2026年10月27日留存。</p>')
        if spec.get('calculation_refs'):
            text += '<h4>读数差的计算</h4>' + formula_mathml('raw_percentage_points_v1')
            text += '<p>两期同比相减，单位为个百分点，不是价格环比或指数涨跌幅。</p>'
            text += '<div class="table-wrap" tabindex="0"><table><thead><tr><th>指标与期间</th><th>起点</th><th>终点</th><th>差额</th></tr></thead><tbody>'
            for key in spec['calculation_refs']:
                row = calculations[key];a, b = (facts[ref] for ref in row['data_refs'])
                text += '<tr><th>' + html.escape(a['label']) + '<br>' + a['period'][:7] + '至' + b['period'][:7] + '</th><td>' + f"{a['value']:.2f}%" + '</td><td>' + f"{b['value']:.2f}%" + '</td><td>' + row['display'] + '个百分点</td></tr>'
            text += '</tbody></table></div>'
        text += '<h4>原始观测</h4><div class="table-wrap" tabindex="0"><table><thead><tr><th>指标</th><th>月份</th><th>接口原值</th></tr></thead><tbody>'
        text += ''.join('<tr><th>' + html.escape(row['label']) + '</th><td>' + row['period'][:7] + '</td><td>'
                        + html.escape(str(row['raw_value'])) + '%</td></tr>' for row in sorted(rows, key=lambda row: (row['code'], row['period'])))
        text += '</tbody></table></div><p>食品与消费品存在分类交叉，不能将三条序列相加或按同比读数计算贡献。</p></section>'
        records.append(header + text)
    return records


CSS = r'''
/* THESIS: Read the inflation evidence before attributing its cause.
   OWN-WORLD: ResearchHelper paper and its single teal palette; no dashboard cards.
   STORY: Answer, dated evidence, three research arguments, full data, next checks.
   FIRST VIEWPORT: Compact title, conclusion and actual three-series chart.
   FORM: Shared offline ECharts; every figure opens its frozen supporting record. */
:root{--content:1120px;--paper:var(--PAPER);--ink:var(--INK);--muted:var(--TEXT_SECONDARY);--rule:var(--GRID_LINE);font-size:16px}
body{background:var(--CANVAS);color:var(--INK);line-height:1.85}
.reader-layout{display:grid;grid-template-columns:140px minmax(0,1fr);gap:28px;max-width:1320px;padding:0 28px;margin:28px auto}
.reader-nav{position:sticky;top:38px;align-self:start;padding-top:44px}
.reader-nav a{display:flex;align-items:center;min-height:44px;color:var(--TEXT_SECONDARY);text-decoration:none;font-size:14px;padding:8px 12px;border-left:1px solid var(--GRID_LINE)}
.reader-nav a:hover,.reader-nav a[aria-current=true]{color:var(--SERIES_BENCH);border-left-color:var(--SERIES_BENCH);font-weight:600}
main{max-width:none;width:100%;margin:0;padding:38px 46px 30px;border-top:3px solid var(--SERIES_BENCH);background:var(--PAPER)}
h1,h2,h3,h4{color:var(--INK)}h1{font-size:33px;line-height:1.35;max-width:none;letter-spacing:0;margin-bottom:10px}
h2{font-size:21px;margin-bottom:15px}h3{font-size:18px;line-height:1.55;margin:0 0 12px;font-weight:700}
p{max-width:75ch;line-height:1.85;margin:10px 0}strong{font-weight:700;color:var(--INK)}
.report-header{padding-bottom:18px;margin-bottom:23px}.report-meta{font-size:14px;gap:16px;color:var(--TEXT_SECONDARY)}
.report-meta span{color:var(--TEXT_SECONDARY)}.lead{font-size:19px;line-height:1.75;font-weight:600;margin:10px 0 13px}
section{padding:28px 0}#summary{padding-bottom:25px}.story-block{padding:23px 0;border-top:1px solid var(--GRID_LINE)}
.story-block:first-of-type{padding-top:2px;border-top:0}.story-block:last-child{padding-bottom:0}
.story-pair{display:grid;grid-template-columns:minmax(0,.92fr) minmax(0,1.08fr);gap:30px;align-items:start}
.story-pair>div{min-width:0}.logic-followup{font-size:15px;color:var(--TEXT_SECONDARY);margin-top:17px}.logic-followup strong{color:var(--SERIES_BENCH)}
figure{margin:23px 0 0;padding:0;border:0;background:transparent;min-width:0}figcaption{font-size:16px;font-weight:600;line-height:1.6;margin-bottom:8px;color:var(--INK)}
figcaption a{min-height:36px;color:var(--INK);text-decoration:underline dotted;text-underline-offset:5px;text-decoration-color:var(--GRID_LINE)}
figcaption a:hover{color:var(--SERIES_BENCH);text-decoration-color:currentColor}.story-pair figure{margin-top:0}
.chart{width:100%;min-width:0}.chart-note{font-size:13px;line-height:1.7;color:var(--TEXT_SECONDARY);margin:8px 0 0;max-width:none}
#trend-chart{height:300px}#marginal-chart{height:246px}#matrix-chart{height:268px}#endpoint-chart{height:260px}
.section-heading{display:flex;align-items:baseline;justify-content:space-between;gap:16px;margin-bottom:12px}.section-heading h2{margin:0}.section-heading span{font-size:14px;color:var(--TEXT_SECONDARY)}
.table-wrap{max-width:100%;overflow-x:auto;min-width:0}table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}thead{background:transparent}
th,td{padding:9px 10px;border-bottom:1px solid var(--GRID_LINE);text-align:right;font-weight:400;white-space:nowrap}thead th{font-weight:600;border-top:1px solid var(--TEXT_SECONDARY);border-bottom:1px solid var(--TEXT_SECONDARY)}th:first-child{text-align:left}tbody tr:last-child td,tbody tr:last-child th{border-bottom:1px solid var(--TEXT_SECONDARY)}
#data table{min-width:320px}#data tbody th{font-weight:400}.inspection-content table{font-size:14px}.inspection-content h3{margin-top:8px}.inspection-content h4{font-size:16px}
.tracking-table th,.tracking-table td{white-space:normal;text-align:left;vertical-align:top;padding-top:12px;padding-bottom:12px}.tracking-table th:first-child{width:20%;font-weight:600}.tracking-table td{width:40%}
.risk-list{padding-left:20px;font-size:15px;margin-top:16px}.risk-list li{margin:7px 0}
.report-source-line{font-size:13px;line-height:1.8;margin-top:24px;padding-top:16px;color:var(--TEXT_SECONDARY)}.report-source-line p{max-width:none;margin:4px 0}
a{color:var(--SERIES_BENCH)}:focus-visible{outline:2px solid var(--SERIES_BENCH);outline-offset:3px}
@media(max-width:1100px){.reader-layout{grid-template-columns:110px minmax(0,1fr);gap:18px;padding:0 18px}main{padding:32px}.story-pair{grid-template-columns:1fr;gap:12px}.story-pair figure{margin-top:0}}
@media(max-width:700px){.reader-layout{display:block;padding:0;margin:0}.reader-nav{z-index:2;top:0;position:sticky;display:flex;justify-content:space-around;padding:0 12px;background:var(--PAPER);border-bottom:1px solid var(--GRID_LINE)}.reader-nav a{border:0;padding:7px 10px;font-size:14px}.reader-nav a[aria-current=true]{box-shadow:inset 0 -2px var(--SERIES_BENCH)}main{padding:26px 19px 22px;border-top:0}h1{font-size:27px}h2{font-size:20px}h3{font-size:18px}.lead{font-size:18px}.report-meta{gap:8px 15px;font-size:14px}section{scroll-margin-top:58px}.story-block{scroll-margin-top:58px}.section-heading{gap:8px}#trend-chart{height:280px}#matrix-chart{height:240px}#marginal-chart{height:240px}#endpoint-chart{height:270px}.tracking-table{font-size:13px}.tracking-table th,.tracking-table td{padding:10px 6px}.report-header{margin-bottom:19px}}
@media print{@page{size:A4;margin:13mm 12mm}body{background:white;color:#1C2926}.reader-layout{display:block;margin:0;padding:0}.reader-nav{display:none}main{max-width:none;width:100%;margin:0;padding:0;border-top:2px solid var(--SERIES_BENCH);background:white}h1{font-size:25px;margin-top:12px}h2{font-size:18px}h3{font-size:16px}p{font-size:14px;line-height:1.7}.lead{font-size:17px}section{padding:20px 0}.story-block{padding:17px 0}.story-pair{display:block}.story-pair figure{margin-top:14px}figure{break-inside:avoid;page-break-inside:avoid}thead{display:table-header-group}tr{break-inside:avoid}h2,h3,figcaption{break-after:avoid}#report-records,#report-inspector{display:none!important}a{color:inherit;text-decoration:none}#trend-chart{height:250px}#matrix-chart{height:230px}#marginal-chart{height:205px}#endpoint-chart{height:215px}.report-source-line{font-size:13px}.chart-note{font-size:13px}}
'''

SCRIPT = r'''
const PAPER = __DATA__;
const SPECS = __SPECS__;
const charts = [];
const palette = RHVisuals.theme(PAPER.palette);
const rendererState = {charts:[], errors:[], theme:PAPER.palette};
const signed=value=>(value>0?'+':'')+Number(value).toFixed(2);
function draw(spec,chart){
 const option=RHVisuals.option(spec.scheme,spec.data,PAPER.palette,{width:chart.getWidth()});
 option.animation=false;
 if(spec.scheme==='multi_line'){
  option.grid={...option.grid,top:58,right:22,bottom:24};
  option.yAxis.name='同比%';
  option.series.forEach(s=>{s.lineStyle.width=2.8;s.symbolSize=6});
 }
 if(spec.scheme==='diverging_bar'){
  option.grid={...option.grid,right:62,top:25,bottom:24};
  option.series[0].label.formatter=v=>signed(v.value);
  option.series[0].itemStyle.color=v=>v.value>=0?palette.positive:palette.negative;
  option.xAxis.name='个百分点';option.xAxis.nameLocation='middle';option.xAxis.nameGap=30;
  option.tooltip={trigger:'item',renderMode:'richText',formatter:v=>v.name+'\n'+signed(v.value)+'个百分点'};
 }
 if(spec.scheme==='dumbbell'){
  option.grid={...option.grid,top:48,right:26,bottom:25};
  option.xAxis.name='同比%';option.xAxis.nameLocation='middle';option.xAxis.nameGap=26;option.grid.bottom=40;
  option.tooltip={trigger:'item',renderMode:'richText',formatter:v=>{
    const i=v.value[1],name=spec.data.labels[i];return name+'\n'+v.seriesName+' '+Number(v.value[0]).toFixed(2)+'%';}};
 }
 chart.setOption(option,true);
}
for(const spec of SPECS){
 const node=document.getElementById(spec.target),chart=echarts.init(node,null,{renderer:'svg'});
 draw(spec,chart);charts.push(chart);rendererState.charts.push({target:spec.target,scheme:spec.scheme});
}
addEventListener('resize',()=>{charts.forEach((chart,index)=>{chart.resize();draw(SPECS[index],chart)})});
addEventListener('beforeprint',()=>{charts.forEach((chart,index)=>{chart.resize();draw(SPECS[index],chart)})});
addEventListener('afterprint',()=>{charts.forEach((chart,index)=>{chart.resize();draw(SPECS[index],chart)})});
const navigation=[...document.querySelectorAll('.reader-nav a')];
const observer=new IntersectionObserver(entries=>{
 for(const entry of entries)if(entry.isIntersecting)navigation.forEach(a=>a.setAttribute('aria-current',String(a.hash==='#'+entry.target.id)));
},{rootMargin:'-15% 0px -65% 0px'});
for(const link of navigation){observer.observe(document.querySelector(link.hash));link.addEventListener('click',()=>navigation.forEach(a=>a.setAttribute('aria-current',String(a===link))))}
window.RHReport = {data:PAPER,specs:SPECS,charts,state:rendererState};
'''


def render(data):
    specs = chart_specs(data)
    p = theme(PALETTE)
    tokens = {'PAPER':p['paper'],'CANVAS':p['canvas'],'INK':p['ink'],'TEXT_SECONDARY':p['muted'],
              'GRID_LINE':p['grid'],'SERIES_MAIN':p['accent'],'SERIES_BENCH':p['primary'],
              'PANEL':p['paper'],'NOTE_BG':p['paper'],'FORMULA_BG':p['ramp'][0]}
    roles = ':root{' + ';'.join('--' + key + ':' + value for key,value in tokens.items()) + '}'
    frontend = ROOT/'products/app/frontend'
    stylesheet = (frontend/'report.css').read_text() + CSS + roles
    esc = html.escape
    def figure(index, note):
        spec = specs[index]
        caption = inspection_link('basis-' + spec['target'], spec['title'], '核对' + spec['title'] + '的原值和计算')
        return ('<figure><figcaption>' + caption + '</figcaption><div id="' + spec['target']
                + '" class="chart" role="img" aria-label="' + spec['title'] + '图"></div>'
                + '<p class="chart-note">' + note + '</p></figure>')
    body = '''
<a class="skip" href="#summary">跳到正文</a>
<div class="reader-layout">
<nav class="reader-nav" aria-label="报告目录"><a href="#summary" aria-current="true">核心结论</a><a href="#strategy">策略逻辑</a><a href="#data">关键数据</a><a href="#risk">跟踪与风险</a></nav>
<main>
<header class="report-header"><h1>CPI回升中的结构分化</h1><p class="report-meta"><span>2026年1至9月</span><time datetime="2026-10-27">2026年10月27日</time></p></header>
<section id="summary"><h2>一、核心结论</h2>
<p class="lead">同比读数温和回升，分项方向尚未同步。</p>
<p>9月总体CPI同比为<strong>0.90%</strong>，较8月提高<strong>0.10个百分点</strong>；消费品同比升至<strong>1.00%</strong>，食品同比降至<strong>-1.70%</strong>。数据支持总体回升与结构分化并存，尚不足以确认全面提价或需求全面修复。</p>
<p>研究上宜把价格、销量和成本分开验证。仅凭这三条同比序列，不能推算分项贡献、企业利润弹性或降息概率。</p>
__TREND__
</section>
<section id="strategy"><h2>二、策略逻辑</h2>
<div class="story-block" id="logic-1"><h3>2.1 同比回升，持续性仍需检验</h3>
<div class="story-pair"><div>
<p>总体同比由1月的0.20%升至9月的0.90%，首尾提高0.70个百分点。但期间并非单边上行：2月为1.30%，7月回落至0.50%，随后两个月再度抬升。</p>
<p>9月总体与消费品同比较8月分别提高0.10和0.20个百分点，食品则回落0.30个百分点。总体读数的改善，没有对应到全部已观察分项。</p>
<p class="logic-followup"><strong>判断含义：</strong>对名义增长敏感的研究，应先验证后续多期读数与分项是否同步。同比读数之差不等于价格环比，现有数据无法区分当前涨价与上年基数的影响。</p>
</div>__MARGINAL__</div></div>
<div class="story-block" id="logic-2"><h3>2.2 食品负同比，归因仍需供需与基数资料</h3>
<p>食品同比自4月至9月连续为负，9月为-1.70%；同期总体和消费品同比均保持正值。当前样本呈现出食品与其他已观察指标的方向差异。</p>
<p>食品负同比表示相应价格水平低于上年同月，不能据此认定价格连续六个月环比下跌，也不能单凭同比确定需求走弱。缺少食品分项权重、环比、供给和销量资料，不能把食品认定为总体变化的主要贡献项。</p>
__MATRIX__
<p class="logic-followup"><strong>判断含义：</strong>食品及相关消费行业要进一步核对销售数量、成交价格与成本。同比转正也需区分基数变化与真实经营改善，不能直接外推利润率。</p>
</div>
<div class="story-block" id="logic-3"><h3>2.3 消费品回升，需要企业经营数据承接</h3>
<div class="story-pair"><div>
<p>消费品同比由1月的0.30%升至9月的1.00%，提高0.70个百分点；食品同期由-0.70%降至-1.70%，回落1.00个百分点。首尾对照显示，三个指标的阶段变化仍有明显差异。</p>
<p>消费品同比高于总体，并不直接证明服务价格弱于总体。本报告没有服务项与完整分类口径，不能通过两条同比的简单相减得到服务项读数。</p>
<p class="logic-followup"><strong>判断含义：</strong>消费板块的研究可优先拆分收入中的销量、均价与产品结构，再用公司报表核对。债券方向及权益估值还需增长、利率与政策证据，本表不足以单独给出仓位或目标价。</p>
</div>__ENDPOINT__</div></div>
</section>
<section id="data"><div class="section-heading"><h2>三、关键数据</h2><span>单位：%</span></div>
__TABLE__
<p class="chart-note">均为当月同比。食品与消费品存在分类交叉，三条序列不可相加。显示保留两位小数，完整原值可点击图题核对。</p>
</section>
<section id="risk"><h2>四、跟踪与风险</h2>
<div class="table-wrap" tabindex="0"><table class="tracking-table"><thead><tr><th scope="col">跟踪问题</th><th scope="col">下一步核对</th><th scope="col">判断如何变化</th></tr></thead><tbody>
<tr><th scope="row">回升能否持续</th><td>后续月份的总体、食品与消费品同比，以及对应环比</td><td>若多期、多分项同步改善，广谱回升的依据才会增强；若再度分化，继续按分项研究。</td></tr>
<tr><th scope="row">变化由什么驱动</th><td>同比基数、食品权重、产量、销量与价格分项</td><td>取得权重与贡献拆分后，才能判断哪一分项主导；供需归因仍需独立证据。</td></tr>
<tr><th scope="row">能否映射到盈利</th><td>公司销量、均价、产品结构、投入成本与财务报表</td><td>只有经营证据与价格读数一致，才能进一步讨论收入与利润弹性。</td></tr>
</tbody></table></div>
<ul class="risk-list"><li>资料仅含三条月度同比序列，未包含核心CPI、服务项、PPI、权重及环比，不据此作完整通胀贡献或因果分解。</li><li>使用2026年10月27日留存版本，后续修订可能改变读数；本报告不验证历史时点的可得性。</li><li>企业表现取决于行业竞争、销量、成本与财务口径，CPI消费品同比不能直接替代公司售价或盈利增速。</li></ul>
</section>
<footer class="report-source-line"><p>来源：国家统计局；取数渠道：iFinD。观测期：2026年1至9月。</p><p>ResearchHelper　基于留存数据编制，不作实时更新。</p></footer>
</main></div>
'''
    for key, value in {
        '__TREND__':figure(0,'三条曲线均为当月同比，颜色对应指标；直线连接实际月度观测，不作平滑拟合。'),
        '__MARGINAL__':figure(1,'8月至9月的同比读数差。赤金色为同比抬升，墨青色为同比回落。'),
        '__MATRIX__':figure(2,'颜色深浅对应同比读数，空缺不填补；完整数值见下方数据表。'),
        '__ENDPOINT__':figure(3,'空心点为1月，实心点为9月；横轴是同比读数，连接线不是期间价格收益。'),
        '__TABLE__':data_table(data,raw_attributes=True),
    }.items():
        body = body.replace(key,value)
    encode = lambda value: json.dumps(value,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
    script = SCRIPT.replace('__DATA__',encode(data)).replace('__SPECS__',encode(specs))
    libraries = [(ROOT/'assets/vendor/echarts.min.js').read_text(),
                 (frontend/'visual-catalog-data.js').read_text(),
                 (frontend/'visual-library.js').read_text()]
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="color-scheme" content="light"><title>'+esc(data['title'])+'</title>'
            '<style>'+stylesheet+'</style></head><body>'+body+inspection_surface(evidence_records(data,specs))
            + ''.join('<script>'+library+'</script>' for library in libraries)
            + '<script>'+script+'\n'+INSPECTION_SCRIPT+'</script></body></html>')


def _write_report(output):
    source_hash = digest(SOURCE)
    data = load_data()
    output = Path(output);output.mkdir(parents=True,exist_ok=True)
    report = output/'中国CPI研究报告.html'
    report.write_text(render(data),encoding='utf-8')
    content = output/'report-data.json';content.write_text(json.dumps(data,ensure_ascii=False,indent=2))
    assert digest(SOURCE)==source_hash
    manifest = dict(status='generated_from_retained_real_data',html=report.name,data=content.name,
                    palette=PALETTE,palette_name=theme(PALETTE)['name'],observations=27,registered_calculations=6,
                    figures=4,source_sha256=source_hash,source_unchanged=True,
                    html_sha256=digest(report),data_sha256=digest(content),generator_sha256=digest(Path(__file__)),
                    shared_resource_hashes={name:digest(ROOT/name) for name in ('assets/vendor/echarts.min.js','products/app/frontend/report.css','products/app/frontend/visual-catalog-data.js','products/app/frontend/visual-library.js')},
                    real_model_calls=0,real_data_requests=0,
                    financial_research_quality_approved=False,
                    scope='new_editorial_report_from_retained_data_not_a_new_autonomous_research_run')
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps({'report':str(report),'figures':4,'palette':manifest['palette_name'],'real_model_calls':0,'real_data_requests':0},ensure_ascii=False))
    return report

def build(output=OUTPUT):
    previous=socket.socket.connect
    socket.socket.connect=offline_only
    try:return _write_report(output)
    finally:socket.socket.connect=previous


if __name__=='__main__':build()
