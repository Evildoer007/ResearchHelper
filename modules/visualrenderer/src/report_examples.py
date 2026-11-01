"""Single-page reading editions of public historical examples.

The supplied archived HTML is never rewritten. The reading edition preserves
public narrative/tables but excludes legacy pixel-reconstructed observations.
It does not validate a historical thesis or manufacture missing observations.
"""
import copy
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from .layout import resource_directory
from .visual import inspection_link, inspection_surface, INSPECTION_SCRIPT


class _MainBody(HTMLParser):
    """Locate the original main without normalizing quotations or SVG markup."""
    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.source, self.start, self.end = source, None, None
        self.offsets = [0]
        for line in source.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line))
        self.feed(source)
        if self.start is None or self.end is None:
            raise ValueError('Historical report has no complete main')

    def position(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag == 'main' and self.start is None:
            self.start = self.position() + len(self.get_starttag_text())

    def handle_endtag(self, tag):
        if tag == 'main' and self.start is not None:
            self.end = self.position()


class _LegacyChartGrids(HTMLParser):
    """Find whole legacy chart grids without rewriting adjacent public text."""
    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.offsets=[0]
        for line in source.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1]+len(line))
        self.depth=0;self.start=None;self.ranges=[]
        self.feed(source)
        if self.depth:
            raise ValueError('Incomplete archived chart grid')

    def position(self):
        line,column=self.getpos()
        return self.offsets[line-1]+column

    def handle_starttag(self, tag, attrs):
        if tag!='div':return
        if self.depth:
            self.depth+=1
        elif 'chart-grid' in dict(attrs).get('class','').split():
            self.depth=1;self.start=self.position()

    def handle_endtag(self, tag):
        if tag=='div' and self.depth:
            self.depth-=1
            if not self.depth:
                self.ranges.append((self.start,self.position()+len('</div>')))


def reading_appendix(key, complete):
    """Archived illustrations are not historical observations or evidence.

    These three legacy grids contain guessed paths/coordinates. Their disclosed
    values, periods, qualitative relations and narrative remain in the adjacent
    public tables and in the reader's explicitly sourced figures.
    """
    titles={'ipo':'超大IPO上市前后全A收益路径',
            'consumer':'科技与消费归一化路径',
            'broker':'特定机构各板块持仓与减持相对关系'}
    if key not in titles:return complete
    grids=_LegacyChartGrids(complete).ranges
    if len(grids)!=1 or titles[key] not in complete[grids[0][0]:grids[0][1]]:
        raise ValueError('Legacy illustration boundary does not match the declared report')
    notes={
        'ipo':'上市首日全A收益中位数-1.4%、随后约3个交易日基本填平是原件明确表述；中间点位不反推。',
        'consumer':'保留原件区间涨跌、起终值及历史轮动样本，不从原图反推中间逐日价格。',
        'broker':'原件横轴为持有规模、纵轴为减持力度，气泡大小表示相对规模；没有可计算坐标或规模值。板块及分红数据完整列示。',
    }
    replacement='<section class="archive-chart-boundary"><h3>'+html.escape(titles[key])+\
                '</h3><p class="chart-boundary">原件未披露的重绘数值不展示。'+html.escape(notes[key])+'</p></section>'
    start,end=grids[0]
    complete=complete[:start]+replacement+complete[end:]
    return complete.replace('完整保留现有Demo已整理的研究内容。',
                            '保留公开研究正文与表格；旧整理稿的估画点位不作原件数据展示。')


def example_figures(key, item):
    """Use catalog numbers; never use pixel-reconstructed paths or coordinates."""
    if key == 'semi':
        figures = copy.deepcopy(item['charts'][:2])
        # The PDF marks the latest annual expectation as 87%+, not exact 87%.
        figures[0]['series'][1]['labels'] = ['33', '68', '87+']
        figures[0]['orientation'] = 'vertical'
        compute = item['charts'][2]
        for index, unit in enumerate(('EOPS/千人', 'EOPS/十亿美元GDP')):
            figures.append({'title': ('人均AI算力' if index == 0 else '单位GDP的AI算力'),
                            'unit': unit, 'categories': copy.deepcopy(compute['categories']),
                            'series': [copy.deepcopy(compute['series'][index])],
                            'orientation': 'vertical', 'color_by': 'country'})
        figures[1]['presentation'] = 'endpoints'
        figures.append({'title': '历史顶部与当前AI样本FY3估值区间', 'unit': '倍',
                        'categories': ['历史顶部样本', '当前AI样本'],
                        'series': [{'name': '样本最低值', 'data': [20.5, 14.3]},
                                   {'name': '样本最高值', 'data': [55.8, 19.9]}],
                        'presentation': 'intervals',
                        'note': '原件列示样本的最低与最高估值，不是置信区间；样本及预测口径不同。'})
        if 'valuation_samples' in item:
            samples = copy.deepcopy(item['valuation_samples'])
            if len(samples) != 2:
                raise ValueError('Valuation chart requires the two declared sample groups')
            for index, sample in enumerate(samples):
                values = sample['values']
                if (not values or len(values) != len(sample['categories'])
                        or any(type(value) not in (int, float) for value in values)
                        or min(values) != figures[-1]['series'][0]['data'][index]
                        or max(values) != figures[-1]['series'][1]['data'][index]):
                    raise ValueError('Valuation samples must match their disclosed bounds')
            figures[-1]['samples'] = samples
            figures[-1]['title'] = '历史顶部与当前AI样本FY3估值'
            figures[-1]['note'] = '不同样本及预测口径，不作配对比较，也不是置信区间；名称按原件保留。'
    elif key == 'consumer':
        rows = item['evidence']['tables'][0]['rows']
        figures = [{'title': '2026年7月科技与消费表现', 'unit': '%',
                    'categories': [row[0] for row in rows],
                    'series': [{'name': '区间涨跌', 'data': [float(row[1].rstrip('%')) for row in rows]}]},
                   copy.deepcopy(item['charts'][1])]
    elif key == 'broker':
        figures = [copy.deepcopy(item['charts'][1])]
    elif key in ('medicine', 'internet'):
        figures = copy.deepcopy(item['charts'])
        figures[0]['orientation'] = 'vertical'
    elif key == 'ipo':
        rows = item['evidence']['tables'][0]['rows']
        figures = [{'kind': 'timeline', 'title': '长鑫科技IPO观察窗口',
                    'nodes': [{'date': row[0], 'name': row[1], 'detail': row[2]} for row in rows],
                    'note': '原件所列计划节点，含预计上市时点，不表示实际发行进度。'}]
    else:
        raise ValueError('Unknown historical report')
    for figure in figures:
        if figure.get('kind') != 'timeline':
            for series in figure['series']:
                if len(series['data']) != len(figure['categories']):
                    raise ValueError('Historical chart categories mismatch')
                if any(value is not None and (type(value) not in (int, float)) for value in series['data']):
                    raise ValueError('Historical chart requires disclosed numeric values')
    return figures


def render_example(key, resources):
    resources = Path(resources)
    data_directory = resource_directory(resources, 'data')
    frontend_directory = resource_directory(resources, 'frontend')
    catalog = json.loads((data_directory / 'report_catalog.json').read_text())['sources']
    if key not in catalog:
        raise ValueError('Unknown historical report')
    item = catalog[key]
    original = (data_directory / item['source_html']).read_text()
    # Already-rendered editions in a release remain byte-stable when served.
    if any('data-example-reader="' + version + '"' in original for version in ('single-page-1', 'single-page-2')):
        return original
    bounds = _MainBody(original)
    complete = reading_appendix(key, original[bounds.start:bounds.end])
    # Namespace all old anchors, SVG gradients and internal links. The literal
    # body text remains untouched and no duplicate identity enters the edition.
    complete = re.sub(r'\bid=("|\x27)([^"\x27]+)\1',
                      lambda match: 'id=' + match[1] + 'archive-' + match[2] + match[1], complete)
    complete = re.sub(r'(href=["\x27]#|url\(#)([^"\x27)]+)',
                      lambda match: match[1] + 'archive-' + match[2], complete)
    # Only the reading edition changes: retained chart tables become ordinary
    # blocks inside the inspector. The archived source and its text stay intact.
    complete = re.sub(r'<(/?)details\b', r'<\1div', complete)
    complete = re.sub(r'<(/?)summary\b', r'<\1h4', complete)
    escape = lambda value: html.escape(str(value), quote=True)
    figures = example_figures(key, item)
    diagrams, inspection_records = [], []
    for index, figure in enumerate(figures):
        numeric = figure.get('kind') != 'timeline'
        if numeric:
            rows = ''.join('<tr><th scope="row">'+escape(category)+'</th>' + ''.join(
                '<td class="num">'+escape('未披露' if series['data'][i] is None else
                    series.get('labels', series['data'])[i])+'</td>'
                for series in figure['series'])+'</tr>' for i, category in enumerate(figure['categories']))
            table = '<table aria-label="'+escape(figure['title'])+'"><thead><tr><th scope="col">观察对象</th>'+''.join(
                '<th scope="col">'+escape(s['name'])+'（'+escape(figure['unit'])+'）</th>'
                for s in figure['series'])+'</tr></thead><tbody>'+rows+'</tbody></table>'
            if figure.get('samples'):
                sample_rows = ''.join('<tr><td>'+escape(sample['name'])+'</td><th scope="row">'+escape(name)
                    +'</th><td class="num">'+format(value, '.1f')+'</td></tr>'
                    for sample in figure['samples'] for name, value in zip(sample['categories'], sample['values']))
                table += '<table aria-label="完整FY3估值样本"><thead><tr><th scope="col">样本组</th>' \
                         '<th scope="col">原件名称</th><th scope="col">FY3市盈率（倍）</th></tr></thead><tbody>' \
                         +sample_rows+'</tbody></table>'
        else:
            table = '<table><tbody>'+''.join('<tr>'+''.join('<td>'+escape(n[field])+'</td>' for field in ('date','name','detail'))+'</tr>' for n in figure['nodes'])+'</tbody></table>'
        note = figure.get('note', '')
        if key == 'consumer' and index == 1:
            note = '历史窗口沿用原件约数，不与本轮精确涨跌幅混用。'
        if key == 'broker':
            note = '股价区间1月14日至5月31日；营收为Q1同比，衡量维度不同。'
        data_notes = []
        if figure.get('samples'):
            data_notes.append(note)
            note = '两组样本分别列示，不作配对比较。'
        if any(value is None for series in figure.get('series', []) for value in series['data']):
            data_notes.append('未披露值不绘制，不按零值处理。')
        if key == 'semi' and index == 1:
            data_notes.append('原材料只明确标注2026E收入终值，未披露中间年份的精确点位。')
        dense = numeric and len(figure['categories']) > 3 and len(figure['series']) > 1 and figure.get('orientation') != 'vertical'
        figure_class = 'chart-dense' if dense else 'chart-intervals' if figure.get('presentation') == 'intervals' else ''
        if figure.get('samples'):
            figure_class += ' chart-samples'
        caption = '图' + str(index + 1) + ' ' + figure['title']
        detail_id = 'example-data-' + str(index)
        inspection_records.append('<section id="'+detail_id+'" data-inspection-title="'+escape(caption)+'"><h3>'+escape(caption)+'</h3>'
            + ('<p>'+escape(note)+'</p>' if note else '')
            + ''.join('<p>'+escape(text)+'</p>' for text in data_notes)
            + '<div class="scroll" tabindex="0">'+table+'</div><p>'+escape(item['sourceNote'])+'</p></section>')
        diagrams.append('<figure'+(' class="'+figure_class+'"' if figure_class else '')+'><figcaption><span>'
                        + inspection_link(detail_id, caption, '查看'+caption+'的数据与口径')+'</span></figcaption>'
                        '<div id="example-chart-'+str(index)+'" class="example-chart" role="img" aria-label="'+escape(figure['title']+('，单位：'+figure['unit'] if numeric else '，计划节点顺序，不是实际发行进度'))+'"></div>'
                        + ('<p class="chart-note">'+escape(note)+'</p>' if note else '')
                        + '</figure>')
    # Bind each disclosed figure to its actual investment argument. Readers
    # should see the evidence beside the argument, not after all arguments.
    figure_groups = {'semi': ((0, 1), (2, 3), (4,)),
                     'consumer': ((0,), (1,)), 'internet': ((0,), (1,)),
                     'medicine': ((0,), ()), 'broker': ((), (0,), ()),
                     'ipo': ((0,), (), ())}[key]
    assigned = [index for group in figure_groups for index in group]
    if len(figure_groups) != len(item['logics']) or sorted(assigned) != list(range(len(figures))):
        raise ValueError('Historical figures must belong to exactly one argument')
    blocks = []
    for index, (logic, group) in enumerate(zip(item['logics'], figure_groups)):
        paragraphs = list(logic['paragraphs'])
        if key == 'semi' and index == 0:
            # Data availability is a chart detail, not another investment thesis.
            # Keep it verbatim in the corresponding data disclosure above.
            paragraphs = [text.replace('原材料只明确标注2026E收入终值，未披露中间年份的精确点位。', '')
                          for text in paragraphs]
        argument = '<article class="example-logic"><h2>'+inspection_link('archive-reportLogic'+str(index+1), logic['title'], '查看'+logic['title']+'的研究依据')+'</h2><div class="argument-text"><p>'+escape(
            paragraphs[0])+'</p>'
        charts = '<div class="example-figures">'+''.join(diagrams[number] for number in group)+'</div>' if group else ''
        support = '<div class="argument-support">'+''.join('<p>'+escape(paragraph)+'</p>' for paragraph in paragraphs[1:])+'</div>' if len(paragraphs) > 1 else ''
        # Keep the complete narrative in one semantic article. Screen and print
        # can place it differently without splitting or shortening its content.
        argument += support+'</div></article>'
        blocks.append('<section class="example-block" data-argument="'+str(index)+'">'+argument+charts+'</section>')
    css = (frontend_directory / 'report.css').read_text() + '\n' + (frontend_directory / 'report-examples.css').read_text()
    vendor = (resource_directory(resources, 'vendor') / 'echarts.min.js').read_text()
    script = (frontend_directory / 'report-examples.js').read_text()
    data = json.dumps(figures, ensure_ascii=False).replace('<', '\\u003c')
    source_line = (item['sourceNote'].split('出品机构', 1)[0].strip() + '历史案例，不构成投资建议。'
                   if key == 'semi' else item['sourceNote'])
    inspection_records.append('<section id="history-source" data-inspection-title="'+escape(item['shortTitle'])+'"><p>'+escape(item['sourceNote'])+'</p><div class="archived-content">'+complete+'</div></section>')
    return ('<!doctype html><html lang="zh-CN" data-example-reader="single-page-2"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="rh-source-key" content="'+escape(key)+'"><meta name="rh-history-boundary" content="public-source-1"><title>'+escape(item['shortTitle'])+'</title><style>'+css+'</style></head><body>'
            '<main class="single-page-paper example-paper"><header class="report-header"><h1>'+escape(item['shortTitle'])+'</h1><p class="report-meta">'+escape(item['date'])+'<span>历史案例，非实时研究</span></p></header>'
            '<section class="example-conclusion"><h2>核心结论</h2><p>'+escape(item['conclusion'])+'</p></section>'
            +''.join(blocks)+
            '<footer class="example-risk"><p>'+escape(item['risk'])+'</p><p class="chart-note">'+inspection_link('history-source', source_line, '查看历史报告的来源与完整公开底稿')+'</p></footer></main>'
            + inspection_surface(inspection_records)
            + '<script>'+vendor+'</script><script>window.RH_EXAMPLE_FIGURES='+data+';</script><script>'+script+INSPECTION_SCRIPT+'</script></body></html>')
