"""ResearchDesigner: one frozen research projection for every output format."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import html
import json
import re
from unicodedata import east_asian_width
from lxml import etree
from lxml import html as dom
from .contracts import ContractError, canonical, unique_json_object
from .visual_catalog import CATALOGUE_VERSION, default_report_theme, theme as visual_theme

from .publication_content import PROFILES, project_content, publication_dataset
from .layout import resource_directory


def read_presentation_document(page):
    """Read the report's unique embedded document from its actual HTML."""
    try:
        tree = dom.document_fromstring(page)
        nodes = tree.xpath('//script[@id="rh-publication-document"]')
        if len(nodes) != 1:
            raise ContractError('HTML缺少唯一的报告资料')
        document = json.loads(nodes[0].text, object_pairs_hook=unique_json_object)
    except (etree.LxmlError, json.JSONDecodeError, UnicodeError, TypeError, RecursionError) as error:
        raise ContractError('HTML报告资料无效') from error
    if not isinstance(document, dict) or document.get('schema') != 'researchhelper.presentation-document':
        raise ContractError('HTML报告资料无效')
    return tree, document


def _text(node):
    return ' '.join(''.join(node.itertext()).split())

def _quantity_runs(node, text):
    """Keep the exact projected paragraph with its validated numeric references."""
    if not node.xpath('./a[@data-quantity-basis]'):
        return []
    parts = [(node.text or '', None)]
    for child in node:
        target = child.get('data-quantity-basis') if child.tag == 'a' else None
        if target and not re.fullmatch(r'(?:calculation:A|observation:D)[1-9][0-9]*', target):
            target = None
        parts.extend(((''.join(child.itertext()), target), (child.tail or '', None)))
    runs, pending_space = [], False
    for value, target in parts:
        words = ' '.join(value.split())
        if not words:
            pending_space |= bool(value)
            continue
        if runs and (pending_space or value[0].isspace()):
            runs.append(dict(text=' ', target=None))
        runs.append(dict(text=words, target=target))
        pending_space = value[-1].isspace()
    return runs if any(run['target'] for run in runs) and ''.join(run['text'] for run in runs) == text else []

def document_reference_blocks(blocks, main):
    """Preserve the saved document's question-to-excerpt links in conversions.

    The HTML remains the authority. Match complete, unique headings and actual
    excerpt targets; never infer a missing source or replace research prose.
    Older frozen HTML can recover presentation links without being rewritten.
    """
    if main is None:
        return blocks
    indices = main.xpath('.//article[contains(concat(" ",normalize-space(@class)," ")," document-answer-index ")]')
    if not indices:
        return blocks
    result = deepcopy(blocks)
    targets = {}
    for node in main.xpath('.//*[@id]'):
        targets.setdefault(node.get('id'), []).append(node)

    def heading_block(node):
        headings = node.xpath('.//*[self::h2 or self::h3 or self::h4]')
        if len(headings) != 1:
            return None
        heading = headings[0]
        found = [block for block in result if block.get('kind') == 'heading'
            and block.get('level') == int(heading.tag[1:]) and block.get('text') == _text(heading)]
        return found[0] if len(found) == 1 else None

    for index in indices:
        question = heading_block(index)
        if question is None:
            continue
        links = []
        for link in index.xpath('.//div[contains(concat(" ",normalize-space(@class)," ")," report-references ")]/a'):
            href = link.get('href', '')
            if not href.startswith('#ev-') or not _text(link):
                continue
            target = href[1:]
            nodes = targets.get(target, [])
            if len(nodes) != 1 or not nodes[0].xpath('.//p[contains(concat(" ",normalize-space(@class)," ")," document-content-quote ")]'):
                continue
            excerpt = heading_block(nodes[0])
            if excerpt is None:
                continue
            excerpt['anchor'] = target
            links.append(dict(text=_text(link), target=target))
        if not links:
            continue
        text = '、'.join(link['text'] for link in links)
        position = result.index(question) + 1
        if position < len(result) and result[position].get('kind') == 'paragraph' and result[position].get('text') == text:
            result[position]['inline_links'] = links
        else:
            result.insert(position, dict(kind='paragraph', text=text, inline_links=links))
    return result


def _block_html(blocks):
    result=[];esc=lambda v:html.escape(str(v));active_calculation=None
    for block in blocks:
        calculation=block.get('calculation_ref')
        if calculation!=active_calculation:
            if active_calculation is not None:result.append('</div>')
            if calculation is not None:
                result.append('<div class="calculation-reading publication-detail-unit" data-calculation-ref="'+esc(calculation)+'">')
            active_calculation=calculation
        if block['kind']=='heading':result.append('<h'+str(block['level'])+'>'+esc(block['text'])+'</h'+str(block['level'])+'>')
        elif block['kind']=='paragraph':result.append('<p>'+esc(block['text'])+'</p>')
        elif block['kind']=='data_reference':result.append('<p data-observation-refs="'+html.escape(json.dumps(block['data_refs'],ensure_ascii=False),quote=True)+'"><a href="#data">'+esc(block['text'])+'</a></p>')
        elif block['kind']=='formula':result.append(block['mathml'])
        elif block['kind']=='table':
            rows=block['rows'];refs=block.get('data_refs',[]);rendered=[]
            linked=block.get('source_links') and len(refs)==len(rows)-1
            calculation_rows=block.get('calculation_rows',[])
            target=block.get('inspection_target')
            if linked or calculation_rows:
                from .visual import inspection_link
            for index,row in enumerate(rows):
                tag='th' if index==0 else 'td'
                reference=(' data-observation-id="'+esc(refs[index-1])+'"') if index and len(refs)==len(rows)-1 else ''
                cells=[esc(value) for value in row]
                if linked and index:
                    cells[-1]=inspection_link('ev-'+refs[index-1], row[-1], '查看'+row[0]+'的原文')
                if calculation_rows and index:
                    item=calculation_rows[index-1]
                    inputs=' '.join(item['data_refs'])
                    reference+=' data-calculation-ref="'+esc(item['id'])+'" data-calculation-inputs="'+esc(inputs)+'"'
                    if target:
                        cells[-1]=inspection_link(target,row[-1],'查看'+row[0]+row[1]+'的两期原值',observation_ref=inputs)
                rendered.append('<tr'+reference+'>'+''.join('<'+tag+'>'+cell+'</'+tag+'>' for cell in cells)+'</tr>')
            table_class=' class="calculation-results-table"' if calculation_rows else ''
            result.append('<div class="publication-table-scroll"><table'+table_class+'>'+''.join(rendered)+'</table></div>')
    if active_calculation is not None:result.append('</div>')
    return ''.join(result)


def _public_appendix(content):
    return _block_html(project_content(content,'detailed')['appendix'])


def _blocks(main,specs, *, detailed=False):
    result=[]
    def walk(node):
        tag=etree.QName(node).localname if isinstance(node.tag,str) else ''
        if tag in ('script','style','nav','button','dialog'):return
        if node.get('class') == 'report-head__brand':
            result.append(dict(kind='masthead',text=_text(node)))
            return
        if tag == 'dl' and node.get('class') == 'paper-dateline':
            result.append(dict(kind='paragraph', text=' '.join(_text(child) for child in node if _text(child))))
            return
        if tag in ('h1','h2','h3','h4'):
            result.append(dict(kind='title' if tag=='h1' else 'heading',level=int(tag[1]),text=_text(node)));return
        if tag=='table':
            for heading in node.xpath('./caption[@class="publication-table-title"]/*'):
                walk(heading)
            for caption in node.xpath('./caption[not(@class="publication-table-title")]'):
                if _text(caption):result.append(dict(kind='caption',text=_text(caption)))
            rows=[[_text(cell) for cell in row if isinstance(cell.tag,str) and etree.QName(cell).localname in ('td','th')] for row in node.xpath('./thead/tr|./tbody/tr|./tfoot/tr|./tr')]
            if rows:
                block=dict(kind='table',rows=rows)
                calculation_rows=node.xpath('./tbody/tr[@data-calculation-ref and @data-calculation-inputs]')
                if calculation_rows:
                    block['calculation_rows']=[dict(id=row.get('data-calculation-ref'),
                        data_refs=row.get('data-calculation-inputs').split()) for row in calculation_rows]
                if detailed:
                    observation_rows = [row.get('data-observation-refs', row.get('data-observation-id', '')).split()
                                        for row in node.xpath('./thead/tr|./tbody/tr|./tfoot/tr|./tr')]
                    if any(observation_rows):
                        block['observation_rows'] = observation_rows
                result.append(block)
            return
        if tag=='math':result.append(dict(kind='formula',mathml=etree.tostring(node,encoding='unicode')));return
        if tag=='figure':
            caption=node.find('figcaption')
            if caption is not None:result.append(dict(kind='caption',text=_text(caption)))
            for plot in node.xpath('.//*[@id]'):
                if plot.get('id') in specs:result.append(dict(kind='chart',spec=specs[plot.get('id')]))
            for table in node.xpath('.//table'):walk(table)
            for para in node.xpath('./p'):walk(para)
            return
        if tag in ('p','li','figcaption'):
            maths=node.xpath('.//*[local-name()="math"]')
            if maths:
                clone=deepcopy(node)
                for math in clone.xpath('.//*[local-name()="math"]'):math.getparent().remove(math)
                if _text(clone):result.append(dict(kind='paragraph',text=_text(clone)))
                for math in maths:walk(math)
            elif _text(node):
                block=dict(kind='paragraph',text=_text(node))
                if detailed and (runs := _quantity_runs(node, block['text'])):
                    block['inline_runs'] = runs
                if node.get('data-observation-refs'):
                    try:refs=json.loads(node.get('data-observation-refs'))
                    except (TypeError,ValueError):refs=[]
                    if isinstance(refs,list) and all(isinstance(ref,str) for ref in refs):block['data_refs']=refs
                result.append(block)
            return
        if 'calculation-reading' in (node.get('class') or '').split():
            ref=node.get('data-calculation-ref')
            scope=next((parent.get('data-logic-detail') for parent in node.iterancestors() if parent.get('data-logic-detail')),'appendix')
            first=len(result)
            for child in node:walk(child)
            if ref:
                for block in result[first:]:block.update(calculation_group=scope+':'+ref,calculation_ref=ref)
            return
        if 'logic-reading-grid' in (node.get('class') or '').split():
            # Retain the HTML's prose/figure columns for PDF and Word, rather
            # than flattening a compact argument into two full-width rows.
            group=(node.getparent().get('id') or 'logic')+'-reading'
            for column,child in enumerate(node):
                first=len(result);walk(child)
                for block in result[first:]:
                    block.update(reading_group=group,reading_column=column)
            return
        if 'paper-figure-group' in (node.get('class') or '').split():
            group=node.get('data-group-id');columns=int(node.get('data-columns','1'))
            panel_index=0
            for child in node:
                first=len(result);walk(child)
                if child.tag!='figure':continue
                for block in result[first:]:block.update(figure_group=group,figure_columns=columns,figure_index=panel_index)
                panel_index+=1
            return
        for child in node:walk(child)
    walk(main)
    return document_reference_blocks(result, main)



DEFAULT_TEMPLATE_ID='report-standard'
TEMPLATE_VERSION=1
RESEARCH_MASTHEAD='光大证券 金融创新业务总部'


def report_template(resources,profile='standard'):
    """Select a shipped report definition by its existing audience profile."""
    if not isinstance(profile,str) or profile not in PROFILES:raise ContractError('请选择标准报告或详细报告')
    identity='report-'+profile;directory=resource_directory(resources,'report_design')
    definition=json.loads((directory/'templates'/(identity+'.template.json')).read_text())
    expected=dict(schema='researchhelper.report-template',id=identity,version=TEMPLATE_VERSION,output_type='report',profile=profile,shell='report.html')
    if definition!=expected:raise ContractError('报告模板定义与所选类型不一致')
    return definition

STYLE='''
.rh-print-table-parts{display:none}
body.rh-publication{background:#f4f4f4}
body.rh-publication main{box-sizing:border-box;width:210mm;max-width:100%;min-height:0;margin:26px 0 48px;padding:14mm 16mm 16mm;background:#fff}
body.rh-publication :is(.argument-reading,.argument-introduction,.argument-visual,.argument-continuation){display:block;max-width:none}
body.rh-publication p,body.rh-publication li{font-size:12px;line-height:1.6;text-wrap:pretty}
body.rh-publication .paper-conclusion p{max-width:none}
body.rh-publication .paper-conclusion .conclusion-band{margin:0;padding:11px 16px 12px;border:0;border-block:1px solid #d8c9a7;background:#f7f3e9}
body.rh-publication .conclusion-band p{margin:0;font-size:12px;line-height:1.55}
body.rh-publication .conclusion-band p+p{margin-top:3px}
body.rh-publication .conclusion-band .conclusion-label{margin:0 0 5px;color:#8b5d13;font-size:12px;font-weight:750}
body.rh-publication .conclusion-band .conclusion-supporting{margin-top:6px}
body.rh-publication .conclusion-band ul{margin:2px 0 0;padding-left:16px}
body.rh-publication .conclusion-band a{color:var(--ink);text-decoration:none}
body.rh-publication .conclusion-band a:hover{color:var(--red)}
body.rh-publication .strategy>h2 a{color:inherit}
body.rh-publication .strategy>h2 a:hover{color:var(--SERIES_MAIN_DEEP)}
body.rh-publication .paper-conclusion{padding:6px 0 3px;border:0}
body.rh-publication #summary.paper-conclusion>h2{position:relative;display:flex;align-items:center;gap:10px;font-size:16px;font-weight:730;line-height:1.4;margin:0 0 9px;padding-left:10px;color:var(--ink)}
body.rh-publication #summary.paper-conclusion>h2::before{content:"";display:block;position:absolute;left:0;top:.23em;bottom:.23em;width:2px;background:var(--red)}
body.rh-publication #summary.paper-conclusion>h2::after{content:"";flex:1;height:1px;background:var(--rule-strong)}
body.rh-publication .paper-figure-group{display:grid;gap:12px;margin:5px 0 3px;align-items:start}
body.rh-publication .paper-figure-group>.shared-figure-context{grid-column:1/-1;margin:0}
body.rh-publication .paper-figure-group[data-columns="1"]{grid-template-columns:minmax(0,1fr)}
body.rh-publication .paper-figure-group[data-columns="2"]{grid-template-columns:repeat(2,minmax(0,1fr))}
body.rh-publication .paper-figure-group[data-columns="3"]{grid-template-columns:repeat(3,minmax(0,1fr))}
body.rh-publication .paper-figure-group .chart-figure{min-width:0;margin:0;padding:4px 0 0;border:0}
body.rh-publication .paper-figure-group .chart{height:var(--paper-chart-height,170px)!important;width:100%;min-width:0}
body.rh-publication .paper-figure-group[data-columns="1"] .chart{height:var(--paper-chart-height,210px)!important}
body.rh-publication .evidence-layout{display:block;min-width:0}body.rh-publication .evidence-ledger{margin-top:3px;max-width:100%;overflow-x:auto}
body.rh-publication .evidence-ledger table{font-size:11px;line-height:1.3}body.rh-publication .evidence-ledger :is(td,th){min-width:0;padding:3px 4px;white-space:normal}
body.rh-publication .evidence-layout[data-auto-scheme="candlestick"][data-ledger-columns="2"]{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,.85fr);align-items:center;gap:10px}
body.rh-publication .paper-figure-group[data-columns] .evidence-layout[data-auto-scheme="candlestick"][data-ledger-columns="2"] .chart{height:132px!important}
body.rh-publication .evidence-layout[data-auto-scheme="candlestick"][data-ledger-columns="2"] .evidence-ledger{margin:0}
body.rh-publication .evidence-ledger :is(th,td):has([data-chart-observation]){text-align:right;font-variant-numeric:tabular-nums}
body.rh-publication .chart svg text{font-size:11px}body.rh-publication .figure-context,body.rh-publication .figure-note,body.rh-publication .figure-source{font-size:11px;line-height:1.3;margin:3px 0}
body.rh-publication .report-meta{font-size:11px}body.rh-publication .argument-heading{margin:0}body.rh-publication .argument-heading h2{margin:0 0 5px}
body.rh-publication .paper-supporting h2{font-size:14.5px;margin:10px 0 5px}body.rh-publication .paper-supporting :is(th,td){font-size:11px;padding:3px 5px}
body.rh-publication .report-series-table{width:100%;min-width:var(--series-table-min-width);table-layout:fixed;margin:8px 0}
body.rh-publication .report-series-table caption{font-size:11px;line-height:1.4;font-weight:500;text-align:left;padding:4px 0}
body.rh-publication .report-series-table :is(th,td){font-size:11px;line-height:1.3;padding:2px 6px;white-space:nowrap;font-variant-numeric:tabular-nums}
body.rh-publication .report-series-table :is(th,td):nth-child(2n){text-align:right}
body.rh-publication .report-series-table :is(th,td):nth-child(2n+3){border-left:1px solid #dedede}
body.rh-publication .financial-peer-table{width:100%;margin:7px 0 2px}
body.rh-publication .financial-peer-table caption{font-weight:700;text-align:left;padding:4px 0}
body.rh-publication .financial-peer-table :is(th,td){font-size:11px;line-height:1.35;padding:3px 5px}
body.rh-publication .financial-peer-table :is(th,td):not(:first-child){text-align:right;font-variant-numeric:tabular-nums}
body.rh-publication .financial-peer-table th>span{display:block;font-weight:500}
body.rh-publication .financial-peer-table :is(th,td):nth-child(2n+4){border-left:1px solid #dedede}
body.rh-publication .financial-peer-table td>a{overflow-wrap:anywhere}
body.rh-publication main>.report-header{display:grid;grid-template-columns:minmax(0,1fr) minmax(144px,.42fr);align-items:end;gap:20px;margin-bottom:13px;padding:16px 0 13px}
body.rh-publication .report-header:not(:has(>dl)){grid-template-columns:minmax(0,1fr)}
body.rh-publication .report-header .report-head__brand{margin:0 0 3px;color:#902f3b;font-size:12px;font-weight:730;letter-spacing:0;line-height:1.4}
body.rh-publication main>.report-header h1{font-size:23px;font-weight:750}
body.rh-publication .report-header>:is(.paper-dateline,dl.report-meta){display:block;margin:0;padding:0 0 0 14px;border:0;border-left:1px solid var(--rule-strong);justify-self:stretch}
body.rh-publication .report-header dl.report-meta>div{display:block}
body.rh-publication .report-header dl dt{margin:0;color:var(--muted);font-size:11px;font-weight:700;line-height:1.4}
body.rh-publication .report-header dl dd{margin:3px 0 0;color:var(--ink);font-size:12px;line-height:1.4;font-variant-numeric:tabular-nums}
body.rh-publication .report-status{display:block;margin-top:3px;color:var(--muted)}
body.rh-publication main>.paper-conclusion{padding:6px 0 8px}
body.rh-publication main>.report-source-line{padding:5px 0 0;margin:5px 0 0}
body.rh-publication main>.report-source-line p{display:flex;flex-wrap:wrap;align-items:baseline;gap:0 12px;margin:0}
body.rh-publication main>.report-source-line a{color:var(--SERIES_BENCH_DEEP);font-weight:600;min-height:24px}
body.rh-publication main>.report-source-line a:hover{color:var(--SERIES_MAIN_DEEP)}
@media screen and (pointer:coarse){body.rh-publication main>.report-source-line a{min-height:44px}}
@media print{
  body.rh-publication main a{color:inherit}
  body.rh-publication main>.report-source-line a[href="#complete-report-data"]{display:none}
}
#report-inspector tr[data-chart-selected]{background:#F7EDF0;color:#252525}
body.rh-publication .chart:focus-visible{outline:2px solid #C8102E;outline-offset:3px}
body.rh-publication .reader-basis{font-size:11px;color:var(--color-muted);margin:5px 0 0}
body.rh-publication .logic-detail{border-top:1px solid #ddd;margin-top:8px;padding-top:5px}
body.rh-publication .logic-detail h3{font-size:13px;margin:8px 0 4px}
body.rh-publication .publication-table-scroll{max-width:100%;overflow-x:auto}body.rh-publication .logic-detail table{font-size:11px}
body.rh-publication .calculation-results-table :is(th,td):last-child{text-align:right;font-variant-numeric:tabular-nums}
body.rh-publication .argument-calculations table{margin:5px 0}
body.rh-publication .argument-calculations caption{padding:3px 0;text-align:left;font-size:11px;color:var(--color-muted)}
body.rh-publication .argument-calculations :is(th,td){padding:3px 6px;font-size:11px;line-height:1.4}
body.rh-publication .argument-calculations :is(.calculation-period,td:last-child){white-space:nowrap;font-variant-numeric:tabular-nums}
body.rh-publication .logic-reading-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,var(--logic-panel-min,270px)),1fr));gap:20px;align-items:start}
body.rh-publication .logic-copy{min-width:0}
body.rh-publication .logic-copy p{margin:0 0 5px}
body.rh-publication .strategy .argument-boundary{font-size:11px;line-height:1.5;color:var(--TEXT_SECONDARY)}
body.rh-publication .strategy .draft-tracking-rule{margin:6px 0 0;padding-top:5px}
body.rh-publication .strategy .draft-tracking-rule p{margin:0}
body.rh-publication .quantity-link{font-weight:600;font-variant-numeric:tabular-nums}
body.rh-publication .logic-reading-grid .paper-figure-group{min-width:0;margin:0}
body.rh-publication .chart-figure figcaption{font-size:11px;line-height:1.4;margin:0 0 3px}
body.rh-publication .chart-figure figcaption a{display:inline;min-height:0;padding:0;line-height:inherit}
body.rh-publication main>#summary{padding:6px 0 8px}
body.rh-publication main>#risk ul{margin:5px 0;padding-left:18px}
@media(max-width:700px){body.rh-publication .chart-figure figcaption a{display:flex;align-items:center;min-height:44px}}

@media(max-width:700px){body.rh-publication .logic-reading-grid{display:block}body.rh-publication .logic-reading-grid .paper-figure-group{margin-top:8px}}
body.rh-publication .report-toc nav{min-width:0}body.rh-publication .report-toc a{white-space:nowrap}
@media(max-width:700px){body.rh-publication main{width:100%;margin:0;padding:14px 16px}body.rh-publication main>.report-header h1{font-size:21px}body.rh-publication .paper-figure-group[data-columns]{grid-template-columns:minmax(0,1fr)}body.rh-publication .paper-figure-group .chart{height:var(--paper-mobile-height,220px)!important}body.rh-publication .report-toc nav{overflow-x:auto;justify-content:flex-start}body.rh-publication .report-toc a{padding:9px 8px}}
@media screen and (max-width:560px){
  body.rh-publication main>.report-header{grid-template-columns:minmax(0,1fr);gap:8px;padding:11px 0 9px}
  body.rh-publication .report-header>:is(.paper-dateline,dl.report-meta){padding:0;border:0}
  body.rh-publication .report-header dl dt,body.rh-publication .report-header dl dd{display:inline;margin:0}
  body.rh-publication .report-header dl dd{margin-left:7px}
}
@media print{body.rh-publication .rh-print-table-new-page{break-before:page;page-break-before:always}body.rh-publication .rh-print-table-original{display:none!important}body.rh-publication .rh-print-table-parts{display:block}body.rh-publication .rh-print-table-copy{break-inside:avoid;page-break-inside:avoid;width:100%}body.rh-publication .rh-print-series-caption{font-size:11px;line-height:1.4;font-weight:500;text-align:left!important;padding:4px 0!important;border:0}body.rh-publication :is(.report-data-scroll,.publication-table-scroll,.table-wrap,.evidence-ledger){overflow:visible;max-height:none}body.rh-publication thead{display:table-header-group}body.rh-publication tbody{display:table-row-group}body.rh-publication table{break-inside:auto}body.rh-publication table.publication-table--short{break-inside:avoid;page-break-inside:avoid}body.rh-publication .publication-detail-unit{break-inside:avoid;page-break-inside:avoid}body.rh-publication[data-report-variant="detailed"] h3{break-after:avoid;page-break-after:avoid}body.rh-publication #risk li{break-inside:avoid;page-break-inside:avoid}body.rh-publication tr{break-inside:avoid}
/* WebKit can split a grid row through an SVG's axes. Table layout keeps a
   paired paragraph and figure together, and permits oversized text to flow. */
body.rh-publication main>.report-header{padding:8px 0 7px;break-inside:avoid}
body.rh-publication main>.report-header h1{font-size:20px}
body.rh-publication .conclusion-band{break-inside:avoid}
body.rh-publication .strategy{break-inside:avoid;page-break-inside:avoid}
body.rh-publication .logic-reading-grid{display:table;width:100%;table-layout:fixed;break-inside:avoid;page-break-inside:avoid}
body.rh-publication .logic-reading-grid>.logic-copy{display:table-cell;width:51%;padding-right:9px;vertical-align:middle}
body.rh-publication .logic-reading-grid>.paper-figure-group{display:table-cell;width:49%;padding-left:9px;vertical-align:middle}
body.rh-publication .report-header{display:grid}body.rh-publication{print-color-adjust:exact;-webkit-print-color-adjust:exact}body.rh-publication main{width:196mm;max-width:none;margin:0;padding:0}.report-toc{display:none}body.rh-publication .paper-figure-group[data-columns="2"]{grid-template-columns:repeat(2,minmax(0,1fr))}body.rh-publication .paper-figure-group[data-columns="3"]{grid-template-columns:repeat(3,minmax(0,1fr))}body.rh-publication .paper-figure-group[data-columns] .chart{height:var(--paper-print-height,var(--paper-chart-height,160px))!important}.paper-figure-group{break-inside:avoid}body.rh-publication .logic-copy p{line-height:1.4}body.rh-publication main>.report-header{margin-bottom:4px}body.rh-publication main>.paper-conclusion{padding:4px 0 5px}body.rh-publication :is(.strategy,[id="risk"]){padding:4px 0 3px}body.rh-publication main>.report-source-line{margin:3px 0 0;padding:3px 0 0}body.rh-publication[data-report-variant="standard"] main>#risk ul{padding:0;margin:3px 0;list-style:none}body.rh-publication[data-report-variant="standard"] main>#risk li{display:inline}body.rh-publication[data-report-variant="standard"] main>#risk li:not(:last-child)::after{content:attr(data-print-separator)}}
'''

# WebKit paginates table rows reliably; a print grid with inline ledgers can
# repaint earlier headings and split long numeric cells. Keep detailed figures
# in ordinary table rows while preserving the screen grid and standard report.
DETAILED_PRINT_STYLE='''
body.rh-publication table>.publication-table-title{text-align:left;caption-side:top}
body.rh-publication[data-report-variant="detailed"] .paper-supporting #data .report-data-scroll :is(th,td){
 padding-block:var(--observation-cell-padding,3px)
}
body.rh-publication[data-report-variant="detailed"] .rh-print-table-measure{--observation-cell-padding:1px}
@media print{
body.rh-publication[data-report-variant="detailed"]{--observation-cell-padding:1px}
body.rh-publication[data-report-variant="detailed"] .paper-figure-group{display:table;width:100%;table-layout:fixed;border-collapse:separate;border-spacing:0;break-inside:avoid;page-break-inside:avoid}
body.rh-publication[data-report-variant="detailed"] .paper-figure-group>figure{display:table-cell;vertical-align:top}
body.rh-publication[data-report-variant="detailed"] .paper-figure-group>figure:not(:first-of-type){padding-left:12px}
body.rh-publication[data-report-variant="detailed"] .paper-figure-group>.shared-figure-context{display:table-caption;caption-side:top}
body.rh-publication[data-report-variant="detailed"] .evidence-ledger table{table-layout:auto}
body.rh-publication[data-report-variant="detailed"] .evidence-ledger :is(td,thead th){white-space:nowrap;word-break:normal;overflow-wrap:normal}
body.rh-publication[data-report-variant="detailed"] h2{break-after:avoid;page-break-after:avoid}
body.rh-publication[data-report-variant="detailed"] .calculation-reading>p{margin-block:2px}
}
'''

TEMPLATE_SCRIPT=r'''
(()=>{
 const links=[...document.querySelectorAll('.report-toc a')];
 const mark=hash=>{const active=links.find(link=>link.hash===hash);if(active)for(const link of links)link.setAttribute('aria-current',String(link===active))};
 mark(location.hash||links[0]?.hash);addEventListener('hashchange',()=>mark(location.hash));
 const specs=__PAPER_FIGURES__;
 if(window.RHVisuals&&window.echarts&&specs.length){
  const charts=specs.map(spec=>{const node=document.getElementById(spec.target);return node&&(echarts.getInstanceByDom(node)||echarts.init(node,null,{renderer:'svg'}))});
  const profile=document.getElementById('rh-visual-profile');
  RHVisuals.bindReports(specs,charts,profile?JSON.parse(profile.textContent):{});
 }
 // WebKit lays out print CSS after beforeprint. Size and redraw the SVGs
 // first, so their axes fit the same compact containers during pagination.
 const printHeights=new Map();
 const printRedraw=()=>{
  for(const node of document.querySelectorAll('main .chart'))window.echarts?.getInstanceByDom(node)?.resize();
  window.RHVisuals?.applyProfile();
 };
 addEventListener('beforeprint',()=>{
  for(const node of document.querySelectorAll('main .paper-figure-group .chart')){
   const group=node.closest('.paper-figure-group');
   const height=parseFloat(getComputedStyle(group).getPropertyValue('--paper-print-height'));
   if(!Number.isFinite(height)||height<=0)continue;
   if(!printHeights.has(node))printHeights.set(node,[node.style.getPropertyValue('height'),node.style.getPropertyPriority('height')]);
   node.style.setProperty('height',height+'px','important');
  }
  window.RHTablePrint?.prepare();printRedraw();
 });
 addEventListener('afterprint',()=>{
  for(const [node,[height,priority]] of printHeights){
   if(height)node.style.setProperty('height',height,priority);else node.style.removeProperty('height');
  }
  printHeights.clear();window.RHTablePrint?.restore();printRedraw();
 });
})();
'''


def _figure_reading_width(spec):
    """Reserve space for axes and legend before allocating a paper column."""
    choice = spec.get('auto_visual', {})
    scheme = choice.get('scheme', spec.get('scheme'))
    data = choice.get('data', spec.get('data', {}))

    def label_width(text):
        return sum(11 if east_asian_width(char) in ('W', 'F') else 7 for char in str(text))

    minimum = 220
    if scheme in ('lollipop', 'diverging_bar', 'ranked_bar', 'benchmark_dot'):
        axis = max((label_width(label) for label in data.get('labels', [])), default=0)
        minimum = max(minimum, axis + 152)
    if scheme in ('grouped_bar', 'multi_line', 'indexed_line', 'stacked_bar'):
        names = data.get('periods') or [row['name'] for row in data.get('series', [])]
        minimum = max(minimum, 20 + sum(28 + label_width(name) for name in names[:3]))
    return minimum


def _group_figures(section,specs,group_index,profile='standard'):
    figures=section.xpath('.//figure')
    if not figures:return
    by_figure = {figure: next((specs[key] for key in figure.xpath('.//*[@id]/@id') if key in specs), {})
                 for figure in figures}
    for figure in figures:
        spec=by_figure[figure]
        reused=bool(spec.get('reused_time_context'))
        # Paired endpoint values already appear in the detailed logic basis.
        # Keep that complete table once; the two dates remain in the legend.
        repeated_date=spec.get('axis_role')=='cross_section' and spec.get('context') and str(spec['context']) in spec.get('name','')
        # Scale context explains normalized values, beyond the dates and
        # originals in a ledger. Keep it with the plot in both report profiles.
        if not spec.get('plot_scale') and spec.get('kind')!='peer_financial_changes' and (
                reused or repeated_date or figure.xpath('.//*[contains(@class,"evidence-ledger")]')):
            for context in figure.xpath('./p[@class="figure-context"]'):context.getparent().remove(context)
        if profile=='detailed' and (reused or spec.get('axis_role')=='cross_section'):
            for ledger in figure.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," evidence-ledger ")]'):
                ledger.getparent().remove(ledger)
        figure.getparent().remove(figure);figure.set('class',(figure.get('class','')+' chart-figure').strip())
    # Remove empty layout furniture, retaining all prose, labels and source links.
    for node in reversed(section.xpath('.//div')):
        if not len(node) and not (node.text or '').strip():node.getparent().remove(node)
    groups=[]
    if len(figures)==3:
        first=figures[0];plot=first.xpath('.//*[@id]');spec=next((specs[n.get('id')] for n in plot if n.get('id') in specs),{})
        choice=spec.get('auto_visual',{});kind=choice.get('scheme',spec.get('scheme'))
        schemes=[specs[key].get('auto_visual',{}).get('scheme') for figure in figures
                 for key in figure.xpath('.//*[@id]/@id') if key in specs]
        if 'evidence' in schemes and any(scheme!='evidence' for scheme in schemes):
            groups=[figures[:2],figures[2:]]
        else:
            groups=[[figures[0]],figures[1:]] if kind in ('scatter','multi_line','heatmap','candlestick','evidence') else [figures]
    else:groups=[figures[i:i+2] for i in range(0,len(figures),2)]
    def category_count(figure):
        current=by_figure[figure]
        choice=current.get('auto_visual',{});kind=choice.get('scheme',current.get('scheme'))
        return len(choice.get('data',current.get('data',{})).get('labels',[])) if kind in ('lollipop','ranked_bar','diverging_bar','benchmark_dot') else 0
    widths = {figure: _figure_reading_width(spec) for figure, spec in by_figure.items()}
    reading_width = (210 - 2 * 16) * 96 / 25.4 - 2
    readable_groups=[]
    for items in groups:
        narrow = []
        for figure in items:
            full_width = category_count(figure) > 8 or by_figure[figure].get('full_width')
            if full_width:
                if narrow:readable_groups.append(narrow)
                readable_groups.append([figure]);narrow=[]
            else:
                # A4 content width after page insets. Test equal column
                # geometry because one long label can crowd an entire row.
                proposed = [*narrow, figure]
                if narrow and len(proposed) * max(widths[item] for item in proposed) + 12 * len(narrow) > reading_width:
                    readable_groups.append(narrow);narrow=[]
                narrow.append(figure)
        if narrow:readable_groups.append(narrow)
    groups=readable_groups
    if profile=='detailed':
        # Preserve established price/valuation and evidence groupings. Only
        # an existing triple of value tables needs another A4 row.
        groups=[part for items in groups for part in
            ([[items[0]],items[1:]] if len(items)==3 and any(figure.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," evidence-ledger ")]') for figure in items) else [items])]
    def minimum_height(items):
        heights=[]
        for figure in items:
            for key in figure.xpath('.//*[@id]/@id'):
                if key not in specs:continue
                height=specs[key].get('min_height',0)
                count=category_count(figure)
                if count>8:height=max(height,64+18*count)
                heights.append(height)
        return max(heights,default=0)
    shared_context=None
    contexts=[figure.xpath('./p[@class="figure-context"]') for figure in figures]
    if len(figures)>1 and all(len(nodes)==1 for nodes in contexts) and len({_text(nodes[0]) for nodes in contexts})==1:
        shared_context=contexts[0][0]
        for nodes in contexts:nodes[0].getparent().remove(nodes[0])
    for j,items in enumerate(groups,1):
        columns=len(items);wrapper=etree.SubElement(section,'div',{'class':'paper-figure-group '+('support-row' if columns==2 else 'dividend-grid' if columns==3 else 'paper-wide'),
          'data-columns':str(columns),'data-group-id':'paper-figures-'+str(group_index)+'-'+str(j)})
        if j==1 and shared_context is not None:
            shared_context.set('class','figure-context shared-figure-context');wrapper.append(shared_context)
        for figure in items:wrapper.append(figure)
        height=200 if columns==1 else 155 if columns==3 else 170
        spec=by_figure[items[0]]
        if columns==1:
            if spec.get('kind')=='changes':height=min(200,max(145,80+30*len(spec.get('values',[]))))
            if spec.get('kind')=='market_observed_range':height=150
        # Preserve screen geometry; reserve a compact print rhythm for paired panels.
        print_height=height-24 if profile=='standard' and columns==2 else height
        if profile == 'standard' and columns == 1 and spec.get('kind') in ('peer_growth', 'peer_operating_bridge'):
            print_height = min(print_height, 170)
        required_height=minimum_height(items)
        height=max(height,required_height)
        print_height=max(print_height,required_height)
        wrapper.set('style','--paper-chart-height:'+str(height)+'px;--paper-print-height:'+str(print_height)+'px'+(';--paper-mobile-height:'+str(max(220,required_height))+'px' if required_height else ''))
    body=section.xpath('./div[not(contains(@class,"paper-figure-group"))]|./p|./table')
    tabular=[node for node in body if node.tag=='table' or node.xpath('.//table')]
    prose=[node for node in body if node not in tabular]
    arguments=section.xpath('./article[contains(concat(" ",normalize-space(@class)," ")," research-argument ")]')
    owner=None
    if arguments:
        # Formal fact/comparison answers are articles, unlike draft strategy
        # paragraphs. Pair only the answer which actually cites this plot's
        # calculation. Shared or absent ownership stays full width.
        reference=figures[0].get('data-calculation-ref') if len(figures)==1 else None
        owners=[argument for argument in arguments if reference and
                any(link.get('href')=='#'+reference for link in argument.iter('a'))]
        owner=owners[0] if len(owners)==1 and not owners[0].xpath('.//table') else None
        prose=[owner] if owner is not None else []
    single=by_figure[figures[0]] if len(figures)==1 else {}
    compact_reading=profile=='standard' or bool(single.get('reused_time_context'))
    prose_length=sum(len(_text(node)) for node in prose)
    panel_width=max(270,widths[figures[0]])
    if (compact_reading and len(figures)==1 and not single.get('full_width') and category_count(figures[0])<=8
            and panel_width<=350 and 0<prose_length<=340):
        layout=etree.Element('div',{'class':'logic-reading-grid','style':f'--logic-panel-min:{panel_width}px'})
        if owner is not None:section.insert(section.index(owner),layout)
        else:section.append(layout)
        copy=etree.SubElement(layout,'div',{'class':'logic-copy'})
        for node in prose:copy.append(node)
        for group in section.xpath('./div[contains(@class,"paper-figure-group")]'):
            compact=(single.get('kind') in ('indexed_comparison','paired','changes')
                     and len(single.get('dates',[]))<=4)
            print_height=131 if compact else 155
            required_height=minimum_height(group.xpath('./figure'))
            group.set('style','--paper-chart-height:'+str(max(155,required_height))+'px;--paper-print-height:'+str(max(print_height,required_height))+'px'+(';--paper-mobile-height:'+str(max(220,required_height))+'px' if required_height else ''));layout.append(group)
        # Evidence matrices need the paper width, below the paired prose/chart.
        for node in tabular:section.append(node)
    elif profile=='standard':
        for node in tabular:
            if 'argument-calculations' in (node.get('class') or '').split():
                section.append(node)


def _table_sections(main, *, keep_detail_units=False):
    """Keep real header rows in a repeatable group without changing cells."""
    for table in main.xpath('.//table'):
        body_rows=table.xpath('./tr[td]|./tbody/tr[td]')
        classes=(table.get('class') or '').split()
        if 0<len(body_rows)<=12:
            if 'publication-table--short' not in classes:table.set('class',' '.join(classes+['publication-table--short']))
            container=table.getparent()
            if container.tag=='div' and len(container)==1:
                names=(container.get('class') or '').split()
                if 'publication-detail-unit' not in names:container.set('class',' '.join(names+['publication-detail-unit']))
            else:
                wrapper=etree.Element('div',{'class':'publication-detail-unit'})
                container.replace(table,wrapper);wrapper.append(table)
        if table.find('thead') is not None:continue
        rows=table.xpath('./tr|./tbody/tr')
        headers=[]
        for row in rows:
            cells=[cell for cell in row if isinstance(cell.tag,str)]
            if not cells or any(cell.tag!='th' for cell in cells):break
            headers.append(row)
        if not headers:continue
        head=etree.Element('thead')
        position=next((i for i,node in enumerate(table) if node.tag in ('tr','tbody','tfoot')),len(table))
        table.insert(position,head)
        for row in headers:head.append(row)
        body=None
        for node in list(table):
            if node.tag=='tr':
                if body is None:
                    body=etree.Element('tbody');table.insert(list(table).index(node),body)
                body.append(node)
            else:body=None

    if not keep_detail_units:return
    # Keep a short value/table with its heading. Longer tables keep the
    # heading in the first print fragment as a native caption.
    for heading in main.xpath('.//h2|.//h3'):
        parent=heading.getparent();following=heading.getnext()
        if following is None or 'publication-detail-unit' in (parent.get('class') or '').split():continue
        tables=[following] if following.tag=='table' else [following[0]] if len(following)==1 and following[0].tag=='table' else []
        short_table=bool(tables) and all('publication-table--short' in (table.get('class') or '').split() for table in tables)
        short_value=heading.tag=='h3' and following.tag=='p' and len(_text(following))<=240
        if short_table or short_value:
            wrapper=etree.Element('div',{'class':'publication-detail-unit'})
            parent.replace(heading,wrapper);wrapper.append(heading);wrapper.append(following)
        elif tables and tables[0].find('caption') is None:
            caption=etree.Element('caption',{'class':'publication-table-title'})
            caption.append(heading)
            tables[0].insert(0,caption)


def _report_data_day(dataset, cutoff, calculations=()):
    """Use the common date of the observations used in the report."""
    latest = {}
    event_endpoints = {row['code']: row['window_end'] for row in calculations
                       if row.get('comparison_kind') == 'event_close_return'}
    try:
        boundary = datetime.fromisoformat(cutoff)
        if boundary.utcoffset() is not None:
            boundary = boundary.astimezone(timezone(timedelta(hours=8)))
        for code, label, period, value, *_ in dataset['rows']:
            if type(value) not in (int, float) and not (isinstance(value, str) and
                    re.fullmatch(r'[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', value)):
                continue
            literal = str(period)
            duration = re.fullmatch(r'(\d{4}-\d{2}-\d{2})至(\d{4}-\d{2}-\d{2})', literal)
            if duration:
                start, day = map(date.fromisoformat, duration.groups())
                if start > day:
                    return None
            else:
                day = date.fromisoformat(literal)
            if label == '收盘点位' and code in event_endpoints and day.isoformat() > event_endpoints[code]:
                continue  # Nearby readings used to align sessions are not later report observations.
            if day > boundary.date():
                return None
            key = (code, label)
            latest[key] = max(day, latest.get(key, day))
    except (TypeError, ValueError):
        return None
    days = set(latest.values())
    return next(iter(days)) if len(days) == 1 else None


def _report_data_months(dataset, cutoff):
    """Show the precision actually published by a complete monthly dataset."""
    from .contracts import market_date

    months = {str(period) for _, _, period, value, *_ in dataset['rows'] if type(value) in (int, float)
              or isinstance(value, str) and re.fullmatch(r'[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', value)}
    if (not cutoff or not months
            or not all(re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])', month) for month in months)
            or max(months) >= market_date(cutoff)[:7]):
        return ()
    return (min(months), max(months)) if len(months) > 1 else (next(iter(months)),)


def _compose_paper_header(slot, header, cutoff, data_day, masthead, data_months=()):
    if header is not None:
        for child in list(header):
            slot.append(child)
    title = slot.find('.//h1')
    if title is None:
        title = etree.SubElement(slot, 'h1')
        title.text = '研究报告'
    copy = title.getparent()
    if copy is slot:
        copy = etree.Element('div', {'class': 'report-head__copy'})
        slot.replace(title, copy)
        copy.append(title)
    if copy.find('./p[@class="report-head__brand"]') is None:
        brand = etree.Element('p', {'class': 'report-head__brand'})
        brand.text = masthead
        copy.insert(list(copy).index(title), brand)
    if cutoff:
        from .reports import research_date_label
        previous = slot.find('./p[@class="report-meta"]')
        dateline = etree.SubElement(slot, 'dl', {'class': 'paper-dateline'})
        etree.SubElement(dateline, 'dt').text = ('数据期间' if data_months else '数据基准日' if data_day else '研究截止')
        date_value = etree.SubElement(dateline, 'dd')
        if data_months:
            date_value.set('title', '研究截止 ' + research_date_label(cutoff))
            start, end = data_months[0], data_months[-1]
            def render_month(month, include_year):
                time = etree.SubElement(date_value, 'time', datetime=month)
                time.text = (month[:4] + '年' if include_year else '') + str(int(month[-2:])) + '月'
                return time
            first = render_month(start, True)
            if start != end:
                first.tail = '至'
                render_month(end, end[:4] != start[:4])
        else:
            instant = data_day.isoformat() if data_day else cutoff
            time = etree.SubElement(date_value, 'time', datetime=instant)
            time.text = instant if data_day else research_date_label(cutoff, compact=True)
            if data_day:
                time.set('title', '研究截止 ' + research_date_label(cutoff))
        if previous is not None:
            for status in previous.findall('span'):
                status.set('class', 'report-status')
                date_value.append(status)
            slot.remove(previous)
    return title


def _compose_paper_conclusion(slot, specs, profile, *, research_judgment):
    statements = [node for node in slot if node.tag not in ('h2', 'header')
                  and not node.get('data-report-scope')]
    if not statements:
        return
    panel = etree.Element('div', {'class': 'conclusion-band'})
    slot.insert(list(slot).index(statements[0]), panel)
    if research_judgment:
        etree.SubElement(panel, 'p', {'class': 'conclusion-label'}).text = '研究判断'
    for node in statements:
        if node.get('class') == 'conclusion-band':
            node.attrib.pop('class')
        panel.append(node)
    bridges = [(key, spec['data']) for key, spec in specs.items() if spec.get('kind') == 'margin_bridge']
    if len(bridges) == 1:
        key, values = bridges[0]
        direction = '降至' if values['end'] < values['start'] else '升至'
        before, after = (f"{values[name]:.2f}%" for name in ('start', 'end'))
        if profile == 'standard':
            from .drafts import client_financial_value
            labels = [client_financial_value(label) for label in (before, after)]
            if labels[0] != labels[1]:
                before, after = labels
        last = statements[-1]
        if last.tag == 'p' and _text(last).endswith(('。', '！', '？')):
            item = last
        else:
            item = etree.SubElement(panel, 'p', {'class': 'conclusion-supporting'})
        link = etree.SubElement(item, 'a', href='#' + key)
        link.text = f'净利润率由{before}{direction}{after}'
        cost_direction = '上升' if values['steps'][0]['value'] > 0 else '下降'
        link.tail = f'，同期收入成本差占比{cost_direction}。'


def compose_strategy_document(source,resources,*,figures=None,profile='standard',details=None,
                              cutoff=None,data_day=None,data_months=(),masthead='ResearchHelper',research_judgment=True):
    """Compose any semantic research document with the actual broker-paper template."""
    tree=dom.document_fromstring(source) if isinstance(source,str) else source
    main=tree.find('.//main')
    if main is None:raise ContractError('报告正文不可用')
    body=tree.find('body')
    if body is None:body=etree.SubElement(tree,'body')
    source_head=tree.find('head')
    for child in list(source_head) if source_head is not None else []:
        if child.tag not in ('meta','title','style','link','base'):body.append(child)
    definition=report_template(resources,profile);design_dir=resource_directory(resources,'report_design');template=dom.document_fromstring((design_dir/definition['shell']).read_text())
    for name in ('data-renderer-version', 'data-reader-version'):
        if tree.get(name) is not None:template.set(name, tree.get(name))
    template.find('body').set('data-report-template',definition['id']);template.find('body').set('data-report-variant',profile)
    template.xpath('//meta[@name="rh-report-template"]')[0].set('content',definition['id']+'@'+str(definition['version']))
    slots={node.get('data-paper-slot'):node for node in template.xpath('//*[@data-paper-slot]')}
    for slot in slots.values():
        for child in list(slot):slot.remove(child)
        slot.text=None
    header=main.find('header')
    title_node = _compose_paper_header(slots['header'], header, cutoff, data_day, masthead, data_months)
    template.find('.//title').text=_text(title_node)
    logics=main.xpath('.//section[starts-with(@id,"logic-")]|.//article[@data-thesis-ref]')
    logic_groups={node.getparent() for node in logics}
    specs=figures or {}
    for index,node in enumerate(logics,1):
        node.set('class',(node.get('class','')+' strategy').strip())
        if node.tag=='article' and node.get('data-thesis-ref') is not None:
            headings=node.xpath('./h2|./h3|./header[contains(@class,"argument-summary")]/div[contains(@class,"argument-heading")]/*[self::h2 or self::h3]')
            if headings and headings[0].tag=='h3':headings[0].tag='h2'
        for heading in node.xpath('./div[contains(@class,"argument-heading")]/h2'):
            owner=heading.getparent();node.insert(0,heading)
            if not len(owner):owner.getparent().remove(owner)
        _group_figures(node,specs,index,profile);slots['logics'].append(node)
    for node in list(main):
        if node is header:continue
        identity=node.get('id','');tag=etree.QName(node).localname if isinstance(node.tag,str) else ''
        if identity in ('summary','conclusion'):
            for child in list(node):slots['summary'].append(child)
        elif identity == 'indicator-definitions':
            slots['summary'].addnext(node)
        elif identity in ('risk','risks','section-risk'):
            for child in list(node):slots['risk'].append(child)
        elif tag=='footer':
            for child in list(node):slots['source'].append(child)
            slots['source'].text=(slots['source'].text or '')+(node.text or '')
        elif tag not in ('script','style'):
            # Moving arguments can leave only their old group heading. Drop
            # that scaffold, not background prose, bare text, tables or images.
            if (node in logic_groups and not (node.text or '').strip()
                    and all(child.tag in ('h1','h2','h3') and not (child.tail or '').strip()
                            for child in node)):
                continue
            if not _text(node).strip():continue
            if identity==slots['logics'].get('id'):
                slots['logics'].attrib.pop('id')
            _group_figures(node,specs,len(logics)+1,profile);slots['supporting'].append(node)
    _compose_paper_conclusion(slots['summary'], specs, profile, research_judgment=research_judgment)
    for item in slots['risk'].xpath('.//li'):
        item.set('data-print-separator','' if _text(item).rstrip().endswith(('。','；','！','？','.',';','!','?')) else '；')
    for name in ('summary','risk'):
        if not len(slots[name]):slots[name].getparent().remove(slots[name])
    source = slots['source']
    if not _text(source).strip():
        source.getparent().remove(source)
    identifiers=set(template.xpath('//*[@id]/@id'))
    def anchor(node,default):
        if node.get('id'):return node.get('id')
        target=default;index=2
        while target in identifiers:
            target=default+'-'+str(index);index+=1
        identifiers.add(target);node.set('id',target)
        return target
    headings=template.xpath('//main//h2')
    for index,heading in enumerate(headings,1):
        section=next((parent for parent in heading.iterancestors() if parent in logics),heading.getparent())
        target=anchor(section,'paper-section-'+str(index))
        heading_text = _text(heading)
        label=re.sub(r'^[一二三四五六七八九十]+、','',heading_text)
        if label.startswith(('策略逻辑', '传导逻辑')):
            label = label.partition('：')[0]
        link=etree.SubElement(slots['contents'],'a',href='#'+target);number=etree.SubElement(link,'span');number.text=str(index)+'.';number.tail=label
        if label != heading_text:
            link.set('title', heading_text)
        if section.tag=='article' and section.get('data-thesis-ref') is not None:
            link.set('title',_text(heading))
            link.set('style','display:block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap')
    if not template.xpath('//*[@id="summary"]'):
        report_body=template.find('.//main');report_body.set('tabindex','-1')
        for link in template.xpath('//a[@class="skip-link"]'):
            link.set('href','#'+anchor(report_body,'report-body'))
    for name,node in slots.items():
        if node.getparent() is not None:node.attrib.pop('data-paper-slot',None)
    new_head=template.find('head');old_head=tree.find('head')
    for child in list(old_head) if old_head is not None else []:
        if child.tag=='title' or child.tag=='meta' and (child.get('charset') or child.get('name') in ('viewport','rh-report-template')):continue
        new_head.append(child)
    style=etree.SubElement(new_head,'style');style.text=(resource_directory(resources,'frontend')/'publication.css').read_text()+STYLE
    if profile=='detailed':style.text+='\n@media print{body.rh-publication .strategy{break-inside:auto;page-break-inside:auto}}'+DETAILED_PRINT_STYLE
    new_body=template.find('body');new_body.set('class',((body.get('class') or '')+' report-profile-a4 rh-publication').strip())
    for child in list(body):
        if child is main or main in child.iterdescendants():continue
        # The paper shell owns the keyboard entry; legacy anchors target
        # the source main, whose identifier need not survive composition.
        if child.tag=='a' and {'skip','skip-link'}.intersection((child.get('class') or '').split()):continue
        new_body.append(child)
    if not any('/* Offline evidence charts.' in (node.text or '') for node in template.xpath('//script')):
        asset=etree.SubElement(new_head,'script');asset.text=(design_dir/'evidence-charts.js').read_text()
    if profile=='detailed':
        if details is not None:
            for key,items in details.items():
                nodes=template.xpath('//main//*[@id=$target or @data-thesis-ref=$target]', target=key)
                parent=nodes[0] if nodes else template.find('.//main')
                parent.append(dom.fromstring('<div class="logic-detail" data-logic-detail="'+html.escape(key)+'">'+_block_html(items)+'</div>'))
        else:
            records={n.get('id'):n for n in template.xpath('//*[@data-inspection-title]')}
            for logic in template.xpath('//main//*[contains(concat(" ",normalize-space(@class)," ")," strategy ")]'):
                targets=list(dict.fromkeys(link[1:] for link in logic.xpath('.//h2//a/@href|.//figcaption//a/@href') if link.startswith('#') and link[1:] in records))
                if not targets:continue
                panel=etree.SubElement(logic,'div',{'class':'logic-detail','data-logic-detail':logic.get('id','')})
                for target in targets:
                    note=deepcopy(records[target])
                    for node in note.iter():
                        if node.get('id'):node.set('id','internal-'+logic.get('id','')+'-'+node.get('id'))
                    panel.append(note)
    _table_sections(template.find('.//main'),keep_detail_units=profile=='detailed')
    pagination=etree.SubElement(new_body,'script');pagination.text=(design_dir/'table-pagination.js').read_text()
    meta=etree.SubElement(new_head,'meta',name='rh-report-profile',content=profile)
    script=etree.SubElement(new_body,'script');script.text=TEMPLATE_SCRIPT.replace('__PAPER_FIGURES__',json.dumps(list(specs.values()),ensure_ascii=False,separators=(',',':')).replace('<','\\u003c'))
    return template



def _ordered_chart_periods(labels):
    if not isinstance(labels, list) or len(labels) < 2 or not isinstance(labels[0], str):
        return False
    precision = len(labels[0])
    previous = ''
    for label in labels:
        if (not isinstance(label, str) or len(label) != precision
                or not re.fullmatch(r'[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?', label)):
            return False
        day = label + {4: '-01-01', 7: '-01', 10: ''}[precision]
        try:
            date.fromisoformat(day)
        except ValueError:
            return False
        if day <= previous:
            return False
        previous = day
    return True


def compatible_schemes(spec, *, preserve_archived_choices=False):
    """The same lossless visual choices offered by the browser gallery."""
    from .visual_selection import _number

    choice=spec.get('auto_visual') or dict(scheme=spec.get('scheme'),data=spec.get('data',{}))
    kind,data=choice.get('scheme'),choice.get('data',{})
    if kind == 'small_multiples':return ['small_multiples','multi_line']
    if data.get('categoricalComparison'):return [kind]
    if kind == 'scatter' and not preserve_archived_choices:
        points = data.get('points')
        if (isinstance(points, list) and len(points) >= 6
                and all(isinstance(point, dict) and _number(point.get('x')) and _number(point.get('y'))
                        for point in points)
                and all(len({point[axis] for point in points}) >= 3 for axis in ('x', 'y'))):
            return [kind, 'scatter_rug']
    if kind in ('sample_range', 'beeswarm') and isinstance(data.get('samples'), list):
        values = [row.get('value') if isinstance(row, dict) else row for row in data['samples']]
        if len(values) >= 5 and all(_number(value) for value in values) and len(set(values)) >= 3:
            return [kind, 'ecdf']
    labels = data.get('labels')
    if kind == 'band' and isinstance(labels, list):
        count = len(labels)
        center = data.get('center')
        if 2 <= count <= 8 and isinstance(center, list) and len(center) == count:
            return [kind, 'interval_dot']
    if kind == 'scenario_matrix':
        settings = data.get('xValues')
        names = data.get('rows', [])
        axis = data.get('xName')
        matrix = data.get('matrix', [])
        if (isinstance(labels, list) and isinstance(settings, list) and len(settings) == len(labels) >= 3
                and all(_number(value) for value in settings)
                and all(first < second for first, second in zip(settings, settings[1:]))
                and isinstance(axis, str) and axis.strip() and isinstance(names, list) and 1 <= len(names) <= 4
                and all(isinstance(name, str) and name.strip() for name in names) and len(set(names)) == len(names)
                and isinstance(matrix, list) and len(matrix) == len(names)
                and all(isinstance(row, list) and len(row) == len(settings) and sum(map(_number, row)) >= 2 for row in matrix)
                and sum(any(_number(value) for value in column) for column in zip(*matrix, strict=True)) >= 3):
            return [kind, 'scenario_profile']
    if kind in ('line','area'):
        area=data.get('measure')=='scale' and all(value is None or value>=0 for value in data.get('values',[]))
        return ['line',*(['area'] if area else []),'grouped_bar','lollipop']
    if kind in ('ranked_bar','lollipop','diverging_bar'):return ['ranked_bar','lollipop','diverging_bar']
    if kind=='grouped_bar' and data.get('pairs'):return ['grouped_bar','slope']
    if kind == 'heatmap':
        if preserve_archived_choices:
            return ['heatmap', 'multi_line', 'grouped_bar'] if all(
                re.match(r'^\d{4}-\d{2}', str(value)) for value in data.get('labels', [])
            ) else ['heatmap']
        return ['heatmap', *(['multi_line'] if _ordered_chart_periods(labels) else []), 'grouped_bar']
    if kind in ('multi_line', 'grouped_bar') and len(data.get('series', [])) > 1:
        if kind == 'multi_line':
            return ['multi_line', 'grouped_bar', 'heatmap']
        if preserve_archived_choices or _ordered_chart_periods(labels):
            return ['grouped_bar', 'multi_line', 'heatmap']
        return ['grouped_bar', 'heatmap']
    return [kind]


def visual_profile_for(profile,specs,theme='research', *, preserve_archived_choices=False):
    if profile is None:profile={}
    if not isinstance(profile,dict) or set(profile)-{'theme','schemes'}:raise ContractError('图表样式设置无效')
    selected=profile.get('theme',theme);schemes=profile.get('schemes',{})
    if not isinstance(selected,str) or visual_theme(selected) is None:raise ContractError('配色未登记')
    if not isinstance(schemes,dict):raise ContractError('图形选择须对应当前报告')
    for target,scheme in schemes.items():
        if not isinstance(target,str) or target not in specs:raise ContractError('图表不属于当前报告，请刷新后重新选择')
        if not isinstance(scheme,str) or scheme not in compatible_schemes(
                specs[target], preserve_archived_choices=preserve_archived_choices):
            raise ContractError('当前数据不适用所选图形')
    return dict(theme=selected,schemes=deepcopy(schemes))


def styled_spec(spec,scheme):
    result=deepcopy(spec);choice=result.get('auto_visual') or dict(scheme=result.get('scheme'),data=result.get('data',{}))
    data=deepcopy(choice.get('data',{}))
    if scheme=='grouped_bar' and not data.get('series') and data.get('pairs'):
        data['series']=[dict(name=name,values=[pair[index] for pair in data['pairs']]) for index,name in enumerate(data['periods'])]
    if scheme in ('multi_line','grouped_bar') and not data.get('series') and data.get('matrix'):
        data['series']=[dict(name=name,values=data['matrix'][index]) for index,name in enumerate(data['rows'])]
    if scheme=='grouped_bar' and not data.get('series'):
        data['series']=[dict(name=data.get('name',''),values=data.get('values',[]))]
    if scheme=='heatmap' and data.get('series'):
        data['rows']=[row['name'] for row in data['series']];data['matrix']=[row['values'] for row in data['series']]
    result['auto_visual']=dict(choice,scheme=scheme,data=data)
    return result


def style_document(document,profile=None, *, preserve_archived_choices=False):
    """Resolve view choices over frozen source specs, never over financial data."""
    result=deepcopy(document)
    sources=result.get('visual_sources') or {block['spec']['target']:deepcopy(block['spec']) for block in result['blocks'] if block['kind']=='chart'}
    selected=visual_profile_for(profile,sources,result.get('theme','research'),
                                preserve_archived_choices=preserve_archived_choices)
    result.update(theme=selected['theme'],visual_profile=selected,visual_sources=sources)
    for block in result['blocks']:
        if block['kind']=='chart':
            target=block['spec']['target'];base=sources[target]
            block['spec']=styled_spec(base,selected['schemes'][target]) if target in selected['schemes'] else deepcopy(base)
    for key,coverage in result.get('figure_coverage',{}).items():
        items=[block['spec'] for block in result['blocks'] if block['kind']=='chart' and block['spec'].get('paper_logic')==key]
        if items:coverage['schemes']=list(dict.fromkeys(item.get('auto_visual',{}).get('scheme',item.get('scheme','')) for item in items))
    return result



def _compact_series_tables(main,facts):
    """Reflow repeated series metadata without discarding a source cell.

    Each band holds a literal period/value pair. Its caption retains identity,
    metric and unit. Other records keep their full original columns and order.
    """
    by_id={row['id']:row for row in facts}
    if len(by_id)!=len(facts):return
    for region in main.xpath('./section[@id="data"]/div[@aria-label="完整原始数据，可横向滚动"]'):
        table=region.find('table')
        if table is None or table.get('id') or table.find('caption') is not None or table.find('tfoot') is not None:continue
        header=table.find('thead/tr');body=table.find('tbody')
        if header is None or body is None:continue
        names=[_text(cell) for cell in header]
        if names not in (['标的','指标','期间','原值','单位'],['标的','名称','指标','期间','原值','单位']):continue
        period_column=names.index('期间');value_column=names.index('原值')
        rows=list(body);ids=[row.get('data-observation-id') for row in rows]
        if ids!=[row['id'] for row in facts] or any(len(row)!=len(names) for row in rows):continue
        groups=[]
        for row,ident in zip(rows,ids):
            fact=by_id[ident]
            key=(fact.get('code'),fact.get('field'),fact.get('unit'),fact.get('series_basis'),
                 fact.get('artifact_id'),fact.get('axis_role'),
                 tuple(_text(cell) for index,cell in enumerate(row) if index not in (period_column,value_column)))
            if groups and groups[-1][0]==key:groups[-1][1].append(row)
            else:groups.append((key,[row]))
        tables=[];plain=[];changed=False
        def ordinary_rows():
            if not plain:return
            retained=etree.Element(table.tag,dict(table.attrib));retained.text=table.text
            for child in table:
                if child is body:
                    target=etree.SubElement(retained,'tbody',dict(body.attrib));target.text=body.text
                    for row in plain:target.append(deepcopy(row))
                else:retained.append(deepcopy(child))
            tables.append(retained);plain.clear()
        def width(cell):
            # A conservative width at the existing 11px table type. Longer
            # numbers reduce the bands instead of reducing numeric precision.
            return max(34,sum(2 if ord(char)>255 else 1 for char in _text(cell))*7+18)
        for key,series in groups:
            periods=[_text(row[period_column]) for row in series]
            if len(series)<24 or key[5]!='time' or not all(periods) or len(set(periods))!=len(periods):
                plain.extend(series);continue
            period_width=max(width(row[period_column]) for row in series)
            value_width=max(width(row[value_column]) for row in series)
            pair_width=period_width+value_width;bands=min(4,700//pair_width)
            if bands<2:plain.extend(series);continue
            ordinary_rows();changed=True
            packed=etree.Element('table',{'class':'report-series-table','data-raw-observations':str(len(series)),
                'data-series-bands':str(bands),'style':'--series-table-min-width:'+str(bands*pair_width)+'px'})
            caption=etree.SubElement(packed,'caption')
            caption.text='，'.join(_text(series[0][index]) if name!='单位' else '单位：'+_text(series[0][index])
                for index,name in enumerate(names) if index not in (period_column,value_column))
            columns=etree.SubElement(packed,'colgroup')
            for _ in range(bands):
                for size in (period_width,value_width):
                    etree.SubElement(columns,'col',style='width:'+format(100*size/(bands*pair_width),'.4f')+'%')
            headings=etree.SubElement(etree.SubElement(packed,'thead'),'tr')
            for _ in range(bands):
                for name in ('期间','原值'):etree.SubElement(headings,'th',scope='col').text=name
            target=etree.SubElement(packed,'tbody')
            for offset in range(0,len(series),bands):
                source_rows=series[offset:offset+bands]
                line=etree.SubElement(target,'tr',{'data-observation-refs':' '.join(row.get('data-observation-id') for row in source_rows)})
                for source in source_rows:
                    line.append(deepcopy(source[period_column]))
                    value=deepcopy(source[value_column]);value.set('data-observation-id',source.get('data-observation-id'));line.append(value)
                for _ in range((bands-len(source_rows))*2):etree.SubElement(line,'td')
            tables.append(packed)
        ordinary_rows()
        if changed:
            tables[-1].tail=table.tail
            position=region.index(table);region.remove(table)
            for offset,replacement in enumerate(tables):region.insert(position+offset,replacement)


def _compact_internal_data(main,content,bindings,projection):
    """Keep one complete appendix when a reading or an appendix citation needs it."""
    facts = (content.get('draft') or {}).get('facts', [])
    if not facts:
        return
    owned = {ref for binding in bindings.values() for key in ('table_data_refs', 'reading_data_refs')
             for ref in binding.get(key, [])}
    appendix_link = bool(main.xpath('.//a[@href="#data"]'))
    for key, blocks in projection['logic_blocks'].items():
        if key not in bindings:
            continue
        for block in blocks:
            if block['kind'] == 'data_reference':
                appendix_link = True
            if block['kind'] == 'table' and len(block.get('data_refs', [])) == len(block['rows']) - 1:
                owned.update(block['data_refs'])
            if block.get('includes_inputs'):
                owned.update(ref for row in block['calculation_rows'] for ref in row['data_refs'])
    if appendix_link or any(row['id'] not in owned for row in facts):
        _compact_series_tables(main, facts)
        return
    for section in main.xpath('./section[@id="data"]'):
        for table in section.xpath('./div[@aria-label="完整原始数据，可横向滚动"]'):
            section.remove(table)
        has_visual=section.xpath('.//figure|.//*[contains(concat(" ",normalize-space(@class)," ")," chart ")]')
        if not has_visual and not any(_text(child) for child in section if child.tag!='h2'):
            section.getparent().remove(section)


def _remove_repeated_peer_ledgers(sections, specs, projection):
    """Keep a rate ledger unless the same logic already prints every result."""
    for key, blocks in projection['logic_blocks'].items():
        section = sections.get(key)
        printed = {row['id'] for block in blocks for row in block.get('calculation_rows', [])}
        if section is None or not printed:
            continue
        for plot in section.xpath('.//*[@id]'):
            spec = specs.get(plot.get('id'), {})
            rates = set(spec.get('calculation_refs', []))
            if spec.get('kind') == 'peer_financial_changes' and rates and rates <= printed:
                for ledger in plot.xpath('ancestor::figure[1]//*[contains(concat(" ",normalize-space(@class)," ")," evidence-ledger ")]'):
                    ledger.getparent().remove(ledger)


def design(content,resources,*,profile='standard',theme=None,visual_profile=None):
    if not isinstance(profile,str) or profile not in PROFILES:raise ContractError('请选择标准报告或详细报告')
    if theme is None:theme=default_report_theme(content)
    if visual_theme(theme) is None:raise ContractError('配色未登记')
    if content.get('report_kind') == 'research_draft':
        from .drafts import render as render_draft
        from .draft_visuals import logic_figure_plan
        figure_plan = logic_figure_plan(content, report_profile=profile)
        source = render_draft(content, resources, visual_profile=dict(theme=theme), report_profile=profile, figure_plan=figure_plan)
    else:
        from .visual import render_research
        source, figure_plan = render_research(content, resources, visual_profile=dict(theme=theme), report_profile=profile)
        figure_plan = [spec for spec in figure_plan if spec.get('auto_visual')]
    tree=dom.document_fromstring(source);main=tree.find('.//main')
    if main is None:raise ContractError('报告正文不可用')
    targets = set(main.xpath('.//*[@id]/@id'))
    specs = {spec['target']: spec for spec in figure_plan if spec['target'] in targets}
    sections = ({f'logic-{i}':node for i,node in enumerate(main.xpath('.//section[starts-with(@id,"logic-")]'),1)}
        if content.get('draft') else {node.get('data-thesis-ref') or node.get('id'):node
            for node in main.xpath('.//article[@data-thesis-ref or @data-question-ref]')})
    bindings, coverage = {}, {}
    for key, node in sections.items():
        plotted = [specs[ident] for ident in node.xpath('.//*[@id]/@id') if ident in specs]
        reused = [specs[ref] for ref in node.xpath('.//a[@data-reused-figure]/@data-reused-figure')
                  if ref in specs and key in specs[ref].get('reused_by', ())]
        visible = plotted + reused
        refs, calcs, context_refs = [], [], []
        for spec in visible:
            actual = spec.get('data_refs', spec.get('refs', []))
            context = spec.get('reused_context_data_refs', {}).get(key, spec.get('plot_context', {}).get('context_data_refs', []))
            refs.extend(ref for ref in actual if ref not in context)
            context_refs.extend(context)
            calcs.extend(spec.get('calculation_refs', []))

        data_tables = node.xpath('.//table[.//*[@data-observation-id or @data-calculation-inputs]]')
        table_refs, table_inputs = [], []
        for table in data_tables:
            table_refs.extend(table.xpath('.//*[@data-observation-id]/@data-observation-id'))
            for result in table.xpath('.//*[@data-calculation-ref and @data-calculation-inputs]'):
                calcs.append(result.get('data-calculation-ref'))
                table_inputs.extend(result.get('data-calculation-inputs').split())
        reading_refs = node.xpath('.//p//*[@data-observation-id]/@data-observation-id')
        reading_inputs = []
        for result in node.xpath('.//p//*[@data-calculation-ref and @data-calculation-inputs]'):
            calcs.append(result.get('data-calculation-ref'))
            reading_inputs.extend(result.get('data-calculation-inputs').split())
        calcs.extend(node.xpath('.//figure/@data-calculation-ref'))
        refs.extend(table_refs + table_inputs + reading_refs + reading_inputs)
        literal_readings = node.xpath('.//p[@class="measured-reading"]/span[@data-observation-id]/@data-observation-id')
        binding = dict(data_refs=list(dict.fromkeys(refs)), calculation_refs=list(dict.fromkeys(calcs)),
                       table_data_refs=list(dict.fromkeys(table_refs)), reading_data_refs=list(dict.fromkeys(literal_readings)))
        peer_tables = [list(dict.fromkeys(table.xpath('.//*[@data-observation-id]/@data-observation-id')))
                       for table in data_tables if 'financial-peer-table' in (table.get('class') or '').split()]
        if peer_tables:
            binding['peer_table_data_refs'] = peer_tables
        if context_refs:
            binding['plot_context_refs'] = list(dict.fromkeys(context_refs))
        bindings[key] = binding

        kinds = {spec.get('coverage_kind', 'numeric') for spec in visible}
        status = 'qualitative' if visible else 'unvisualized'
        if 'numeric' in kinds:
            status = 'numeric'
        elif table_refs or table_inputs:
            status = 'numeric_table'
        elif reading_refs or reading_inputs:
            status = 'numeric_text'
        result = dict(figures=len(plotted), status=status,
            schemes=list(dict.fromkeys(spec.get('auto_visual', {}).get('scheme', spec.get('scheme', '')) for spec in visible)),
            data_refs=list(dict.fromkeys([*binding['data_refs'], *context_refs])))
        document_sources = list(dict.fromkeys(ref for spec in visible if spec.get('document_groups')
                                              for ref in spec.get('source_refs', [])))
        if document_sources:
            result['source_refs'] = document_sources
        if reused:
            result['figure_links'] = [spec['target'] for spec in reused]
        if data_tables:
            result['tables'] = len(data_tables)
        if context_refs:
            result.update(argument_data_refs=binding['data_refs'], context_data_refs=binding['plot_context_refs'])
        coverage[key] = result
    presented = main.xpath('.//table[contains(concat(" ",normalize-space(@class)," ")," adopted-data-table ")]//tr/@data-observation-id')
    projection=project_content(content,profile,bindings,presented_data_refs=presented)
    if profile=='detailed':
        _compact_internal_data(main,content,bindings,projection)
        _remove_repeated_peer_ledgers(sections, specs, projection)
    dataset = publication_dataset(content)
    cutoff = content.get('cutoff')
    from .parallel_study import POLICY as PARALLEL_STUDY_POLICY

    independent_windows = content.get(PARALLEL_STUDY_POLICY) == 1
    data_day = None if independent_windows else _report_data_day(dataset, cutoff, content.get('draft', {}).get('calculations', ()))
    data_months = () if independent_windows else _report_data_months(dataset, cutoff)
    research_judgment = content.get('draft', {}).get('organization') not in ('fact', 'comparison')
    tree=compose_strategy_document(tree,resources,figures=specs,profile=profile,details=projection['logic_blocks'],
        cutoff=cutoff, data_day=data_day, data_months=data_months, masthead=RESEARCH_MASTHEAD,
        research_judgment=research_judgment);main=tree.find('.//main')
    blocks=_blocks(main,specs,detailed=profile=='detailed')
    selected_visual=visual_profile_for(visual_profile,specs,theme)
    profile_nodes=tree.xpath('//script[@id="rh-visual-profile"]')
    if len(profile_nodes)!=1:raise ContractError('报告缺少唯一图表设置')
    embedded_profile = selected_visual if selected_visual['schemes'] else {'theme':selected_visual['theme']}
    profile_nodes[0].text=canonical(embedded_profile).replace('<','\\u003c')
    data=dict(schema='researchhelper.presentation-document',version=1,profile=profile,theme=theme,template=dict(id=tree.find('body').get('data-report-template'),version=TEMPLATE_VERSION),output_type='report',
      audience=projection['audience'],dataset=dataset,figure_coverage=coverage,content_hash=projection['content_hash'],title=next((b['text'] for b in blocks if b['kind']=='title'),'研究报告'),blocks=blocks)
    data['visual_sources']=deepcopy(specs)
    data['visual_catalogue_version'] = CATALOGUE_VERSION
    data=style_document(data,selected_visual)
    body=tree.find('body');frozen=etree.SubElement(body,'script',id='rh-publication-document',type='application/json');frozen.text=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
    return dict(document=data,html='<!doctype html>\n'+etree.tostring(tree,encoding='unicode',method='html'))


def render_html(content,resources,*,profile='standard',theme=None,visual_profile=None):
    return design(content,resources,profile=profile,theme=theme,visual_profile=visual_profile)['html']
