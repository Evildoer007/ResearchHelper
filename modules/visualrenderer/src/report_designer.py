"""ResearchDesigner: one frozen research projection for every output format."""
from copy import deepcopy
import hashlib
import html
import json
import re
from lxml import etree
from lxml import html as dom
from .contracts import ContractError,canonical
from .visual_catalog import theme as visual_theme

from .publication_content import PROFILES,project_content,publication_dataset
from .layout import resource_directory

def _text(node):
    return ' '.join(''.join(node.itertext()).split())


def _block_html(blocks):
    result=[];esc=lambda v:html.escape(str(v))
    for block in blocks:
        if block['kind']=='heading':result.append('<h'+str(block['level'])+'>'+esc(block['text'])+'</h'+str(block['level'])+'>')
        elif block['kind']=='paragraph':result.append('<p>'+esc(block['text'])+'</p>')
        elif block['kind']=='formula':result.append(block['mathml'])
        elif block['kind']=='table':result.append('<div class="publication-table-scroll"><table>'+''.join('<tr>'+''.join('<'+('th' if i==0 else 'td')+'>'+esc(v)+'</'+('th' if i==0 else 'td')+'>' for v in row)+'</tr>' for i,row in enumerate(block['rows']))+'</table></div>')
    return ''.join(result)


def _public_appendix(content):
    return _block_html(project_content(content,'detailed')['appendix'])


def _specs(content,tree,profile='standard'):
    if content.get('report_kind')=='research_draft':
        from .draft_visuals import logic_figure_plan
        targets=set(tree.xpath('//main//*[@id]/@id'))
        return {spec['target']:spec for spec in logic_figure_plan(content,report_profile=profile) if spec['target'] in targets}
    from .visual import research_figures
    from .visual_selection import select_figures
    original=research_figures(content);result={}
    for figure in tree.xpath('//main//figure[@data-calculation-ref]'):
        ref=figure.get('data-calculation-ref');nodes=figure.xpath('.//div[contains(concat(" ",normalize-space(@class)," ")," chart ")]')
        if ref in original and nodes:
            spec=deepcopy(original[ref]);spec['target']=nodes[0].get('id');result[spec['target']]=select_figures(content,[spec])[0]
    return result


def _blocks(main,specs):
    result=[]
    def walk(node):
        tag=etree.QName(node).localname if isinstance(node.tag,str) else ''
        if tag in ('script','style','nav','button','dialog'):return
        if tag in ('h1','h2','h3','h4'):
            result.append(dict(kind='title' if tag=='h1' else 'heading',level=int(tag[1]),text=_text(node)));return
        if tag=='table':
            rows=[[_text(cell) for cell in row if isinstance(cell.tag,str) and etree.QName(cell).localname in ('td','th')] for row in node.xpath('./thead/tr|./tbody/tr|./tfoot/tr|./tr')]
            if rows:result.append(dict(kind='table',rows=rows))
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
            elif _text(node):result.append(dict(kind='paragraph',text=_text(node)))
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
    return result



DEFAULT_TEMPLATE_ID='report-standard'
TEMPLATE_VERSION=1


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
body.rh-publication main{box-sizing:border-box;width:210mm;max-width:100%;min-height:0;margin:0;padding:14px 26px;background:#fff}
body.rh-publication :is(.argument-reading,.argument-introduction,.argument-visual,.argument-continuation){display:block;max-width:none}
body.rh-publication p,body.rh-publication li{font-size:12px;line-height:1.4}
body.rh-publication .paper-conclusion p{max-width:none}body.rh-publication .strategy>h2 a{color:inherit;text-decoration:none}body.rh-publication .strategy>h2 a:hover{text-decoration:underline}body.rh-publication .paper-conclusion{padding:6px 0 3px;border:0}body.rh-publication .paper-conclusion h2{font-size:14.5px;margin:0 0 5px}
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
body.rh-publication .chart svg text{font-size:11px}body.rh-publication .figure-context,body.rh-publication .figure-note,body.rh-publication .figure-source{font-size:11px;line-height:1.3;margin:3px 0}
body.rh-publication .report-meta{font-size:11px}body.rh-publication .argument-heading{margin:0}body.rh-publication .argument-heading h2{margin:0 0 5px}
body.rh-publication .paper-supporting h2{font-size:14.5px;margin:10px 0 5px}body.rh-publication .paper-supporting :is(th,td){font-size:11px;padding:3px 5px}
body.rh-publication main>.report-header{margin-bottom:10px;padding:7px 0 8px}
body.rh-publication main>.paper-conclusion{padding:6px 0 8px}
body.rh-publication main>.report-source-line{padding:5px 0 0;margin:5px 0 0}
body.rh-publication main>.report-source-line p{margin:0}
#report-inspector tr[data-chart-selected]{background:#F7EDF0;color:#252525}
body.rh-publication .chart:focus-visible{outline:2px solid #C8102E;outline-offset:3px}
body.rh-publication .reader-basis{font-size:11px;color:var(--color-muted);margin:5px 0 0}
body.rh-publication .logic-detail{border-top:1px solid #ddd;margin-top:8px;padding-top:5px}
body.rh-publication .logic-detail h3{font-size:13px;margin:8px 0 4px}
body.rh-publication .publication-table-scroll{max-width:100%;overflow-x:auto}body.rh-publication .logic-detail table{font-size:11px}
body.rh-publication .logic-reading-grid{display:grid;grid-template-columns:minmax(0,1.02fr) minmax(0,.98fr);gap:18px;align-items:center}
body.rh-publication .logic-copy{min-width:0}body.rh-publication .logic-copy p{margin:5px 0;line-height:1.5}
body.rh-publication .logic-reading-grid .paper-figure-group{min-width:0;margin:0}
body.rh-publication .chart-figure figcaption{font-size:11px;line-height:1.4;margin:0 0 3px}
body.rh-publication .chart-figure figcaption a{display:inline;min-height:0;padding:0;line-height:inherit}
body.rh-publication main>#summary{padding:6px 0 8px}
body.rh-publication main>#risk ul{margin:5px 0;padding-left:18px}
@media(max-width:700px){body.rh-publication .chart-figure figcaption a{display:flex;align-items:center;min-height:44px}}

@media(max-width:700px){body.rh-publication .logic-reading-grid{display:block}body.rh-publication .logic-reading-grid .paper-figure-group{margin-top:8px}}
body.rh-publication .report-toc nav{min-width:0}body.rh-publication .report-toc a{white-space:nowrap}
@media(max-width:700px){body.rh-publication main{width:100%;margin:0;padding:14px 16px}body.rh-publication .paper-figure-group[data-columns]{grid-template-columns:minmax(0,1fr)}body.rh-publication .paper-figure-group .chart{height:var(--paper-mobile-height,220px)!important}body.rh-publication .report-toc nav{overflow-x:auto;justify-content:flex-start}body.rh-publication .report-toc a{padding:9px 8px}}
@media print{body.rh-publication .rh-print-table-original{display:none!important}body.rh-publication .rh-print-table-parts{display:block}body.rh-publication .rh-print-table-copy{break-inside:avoid;page-break-inside:avoid;width:100%}body.rh-publication :is(.report-data-scroll,.publication-table-scroll,.table-wrap,.evidence-ledger){overflow:visible;max-height:none}body.rh-publication thead{display:table-header-group}body.rh-publication tbody{display:table-row-group}body.rh-publication table{break-inside:auto}body.rh-publication table.publication-table--short{break-inside:avoid;page-break-inside:avoid}body.rh-publication .publication-detail-unit{break-inside:avoid;page-break-inside:avoid}body.rh-publication[data-report-variant="detailed"] h3{break-after:avoid;page-break-after:avoid}body.rh-publication #risk li{break-inside:avoid;page-break-inside:avoid}body.rh-publication tr{break-inside:avoid}body.rh-publication .logic-reading-grid{display:grid}body.rh-publication .report-header{display:grid}body.rh-publication{print-color-adjust:exact;-webkit-print-color-adjust:exact}body.rh-publication main{width:196mm;max-width:none;margin:0;padding:0}.report-toc{display:none}body.rh-publication .paper-figure-group[data-columns="2"]{grid-template-columns:repeat(2,minmax(0,1fr))}body.rh-publication .paper-figure-group[data-columns="3"]{grid-template-columns:repeat(3,minmax(0,1fr))}body.rh-publication .paper-figure-group[data-columns] .chart{height:var(--paper-print-height,var(--paper-chart-height,160px))!important}.paper-figure-group{break-inside:avoid}body.rh-publication .logic-copy p{line-height:1.4}body.rh-publication main>.report-header{margin-bottom:4px}body.rh-publication main>.paper-conclusion{padding:4px 0 5px}body.rh-publication :is(.strategy,[id="risk"]){padding:4px 0 3px}body.rh-publication main>.report-source-line{margin:3px 0 0;padding:3px 0 0}body.rh-publication[data-report-variant="standard"] main>#risk ul{padding:0;margin:3px 0;list-style:none}body.rh-publication[data-report-variant="standard"] main>#risk li{display:inline}body.rh-publication[data-report-variant="standard"] main>#risk li:not(:last-child)::after{content:attr(data-print-separator)}}
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


def _support_figures(content,existing,profile='detailed'):
    """Expose raw inputs behind registered calculations; no new financial maths."""
    draft=content.get('draft')
    if not draft or len(existing)>=6:return []
    from .draft_visuals import _validated,FIELDS,BALANCE
    from .draft_analysis import _day
    from .visual_selection import _shown
    registered=_validated(content);owned={row['id']:row for calc in registered for row in calc['rows']}
    figures=[]
    for index,logic in enumerate(draft.get('logics',[]),1):
        if any(spec.get('paper_logic')=='logic-'+str(index) for spec in existing):continue
        refs=set(logic.get('data_refs',[]));groups={}
        for ref in refs:
            row=owned.get(ref)
            if row is None or _day(row['period']) is None:continue
            if profile=='standard' and row.get('unit') in ('','原始单位待核对'):continue
            key=(row['code'],row['field'],row['unit'],row.get('series_basis'))
            groups.setdefault(key,{})[row['period']]=row
        by_period={}
        for key,periods in groups.items():
            if len(periods)!=2:continue
            dates=tuple(sorted(periods,key=lambda value:_day(value)))
            by_period.setdefault((key[0],key[2],key[3],dates,key[1] in BALANCE),[]).append((key[1],periods))
        if not by_period:continue
        key,metrics=max(by_period.items(),key=lambda pair:len(pair[1]))
        # One broad input panel per actual logic, bounded to its own cited data.
        metrics=sorted(metrics,key=lambda pair:list(FIELDS).index(pair[0]) if pair[0] in FIELDS else 999)[:3]
        dates=key[3];labels=[FIELDS.get(field,rows[dates[0]]['label']) for field,rows in metrics]
        pairs=[[rows[date]['value'] for date in dates] for _,rows in metrics]
        data_refs=[rows[date]['id'] for _,rows in metrics for date in dates]
        if len(metrics)<2:continue
        periods=[_day(date).isoformat() for date in dates];plot_unit='原值' if key[1]=='原始单位待核对' else key[1]
        data=dict(labels=labels,series=[dict(name=periods[i],values=[pair[i] for pair in pairs]) for i in range(2)],comparison_periods=periods,unit=plot_unit,name='两期原值对照')
        scheme='grouped_bar';name='两期原值对照'
        ledger=dict(columns=['指标',*periods],rows=[dict(label=label,cells=[_shown(value) for value in pair],values=pair) for label,pair in zip(labels,pairs)],unit=key[1])
        ratios=[c for c in registered if c['method']=='raw_multiple_v1' and c['id'] in logic.get('calculation_refs',[]) and set(c['data_refs'])<=refs]
        if len(ratios)==2:
            ratios=sorted(ratios,key=lambda row:_day(row['period']))
            points=[dict(name=_day(c['period']).isoformat(),x=c['rows'][1]['value'],y=c['rows'][0]['value']) for c in ratios]
            data=dict(points=points,comparison_periods=periods,unit=plot_unit,xName='净利润',yName='经营现金流');scheme='scatter';name='现金流与利润原值'
            data_refs=list(dict.fromkeys(ref for c in ratios for ref in c['data_refs']))
            ledger=dict(columns=['期间','净利润原值','经营现金流原值'],rows=[dict(label=row['name'],cells=[_shown(row['x']),_shown(row['y'])],values=[row['x'],row['y']]) for row in points],unit=key[1])
        figures.append(dict(target='paper-inputs-logic-'+str(index),name=name,field='financial_inputs',
          data_refs=data_refs,calculation_refs=[c['id'] for c in registered if set(c['data_refs'])<=set(data_refs)],
          paper_logic='logic-'+str(index),auto_visual=dict(version=1,scheme=scheme,data=data,ledger=ledger,reason='cited_registered_calculation_inputs')))
        if len(figures)+len(existing)>=6:break
    return figures


def _append_support(main,specs,extra,profile='detailed'):
    from .visual import inspection_link
    from .visual_selection import ledger_html
    for spec in extra:
        nodes=main.xpath('.//*[@id="'+spec['paper_logic']+'"]')
        if not nodes:continue
        section=nodes[0];links=section.xpath('.//h2//a/@href');source=links[0][1:] if links and links[0].startswith('#') else None
        caption=inspection_link(source,spec['name']) if source else html.escape(spec['name'])
        plot='<div class="chart" id="'+spec['target']+'" role="img" aria-label="两期原值对照"></div>'
        visual=ledger_html(spec,plot,source) if profile=='detailed' else plot
        figure=dom.fromstring('<figure><figcaption>'+caption+'</figcaption>'+visual+'</figure>')
        section.append(figure);specs[spec['target']]=spec


def _group_figures(section,specs,group_index,profile='standard'):
    figures=section.xpath('.//figure')
    if not figures:return
    for figure in figures:
        spec=next((specs[key] for key in figure.xpath('.//*[@id]/@id') if key in specs),{})
        reused=bool(spec.get('reused_time_context'))
        # Paired endpoint values already appear in the detailed logic basis.
        # Keep that complete table once; the two dates remain in the legend.
        if reused or figure.xpath('.//*[contains(@class,"evidence-ledger")]'):
            for context in figure.xpath('./p[@class="figure-context"]'):context.getparent().remove(context)
        if profile=='detailed' and reused:
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
    def minimum_height(items):
        heights=[]
        for figure in items:
            for key in figure.xpath('.//*[@id]/@id'):
                if key not in specs:continue
                current=specs[key];choice=current.get('auto_visual',{})
                height=current.get('min_height',0)
                if choice.get('scheme',current.get('scheme'))=='dumbbell':
                    height=max(height,96+28*len(choice.get('data',current.get('data',{})).get('labels',[])))
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
        if columns==1:
            target=items[0].xpath('.//*[@id]/@id')
            spec=next((specs[key] for key in target if key in specs),{})
            if spec.get('kind')=='changes':height=min(200,max(145,80+30*len(spec.get('values',[]))))
        # Preserve screen geometry; reserve a compact print rhythm for paired panels.
        print_height=height-24 if profile=='standard' and columns==2 else height
        required_height=minimum_height(items)
        height=max(height,required_height)
        print_height=max(print_height,required_height)
        wrapper.set('style','--paper-chart-height:'+str(height)+'px;--paper-print-height:'+str(print_height)+'px'+(';--paper-mobile-height:'+str(max(220,required_height))+'px' if required_height else ''))
    body=section.xpath('./div[not(contains(@class,"paper-figure-group"))]|./p|./table')
    tabular=[node for node in body if node.tag=='table' or node.xpath('.//table')]
    prose=[node for node in body if node not in tabular]
    single=next((specs[key] for key in figures[0].xpath('.//*[@id]/@id') if key in specs),{}) if len(figures)==1 else {}
    compact_reading=profile=='standard' or bool(single.get('reused_time_context'))
    if compact_reading and len(figures)==1 and sum(len(_text(node)) for node in prose)<=340:
        layout=etree.SubElement(section,'div',{'class':'logic-reading-grid'})
        copy=etree.SubElement(layout,'div',{'class':'logic-copy'})
        for node in prose:copy.append(node)
        for group in section.xpath('./div[contains(@class,"paper-figure-group")]'):
            compact=(spec.get('kind') in ('indexed_comparison','paired','changes')
                     and len(spec.get('dates',[]))<=4)
            print_height=131 if compact else 155
            required_height=minimum_height(group.xpath('./figure'))
            group.set('style','--paper-chart-height:'+str(max(155,required_height))+'px;--paper-print-height:'+str(max(print_height,required_height))+'px'+(';--paper-mobile-height:'+str(max(220,required_height))+'px' if required_height else ''));layout.append(group)
        # Evidence matrices need the paper width, below the paired prose/chart.
        for node in tabular:section.append(node)


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
    # A heading and its short value/table are one print unit. Long sections
    # remain splittable; rows, text, IDs and their order are unchanged.
    for heading in main.xpath('.//h2|.//h3'):
        parent=heading.getparent();following=heading.getnext()
        if following is None or 'publication-detail-unit' in (parent.get('class') or '').split():continue
        tables=[following] if following.tag=='table' else [following[0]] if len(following)==1 and following[0].tag=='table' else []
        short_table=bool(tables) and all('publication-table--short' in (table.get('class') or '').split() for table in tables)
        short_value=heading.tag=='h3' and following.tag=='p' and len(_text(following))<=240
        if short_table or short_value:
            wrapper=etree.Element('div',{'class':'publication-detail-unit'})
            parent.replace(heading,wrapper);wrapper.append(heading);wrapper.append(following)


def compose_strategy_document(source,resources,*,figures=None,profile='standard',details=None):
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
    template.find('body').set('data-report-template',definition['id']);template.find('body').set('data-report-variant',profile)
    template.xpath('//meta[@name="rh-report-template"]')[0].set('content',definition['id']+'@'+str(definition['version']))
    slots={node.get('data-paper-slot'):node for node in template.xpath('//*[@data-paper-slot]')}
    for slot in slots.values():
        for child in list(slot):slot.remove(child)
        slot.text=None
    header=main.find('header')
    if header is not None:
        for child in list(header):slots['header'].append(child)
    if not slots['header'].xpath('.//h1'):
        title=etree.SubElement(slots['header'],'h1');title.text='研究报告'
    title_node=slots['header'].xpath('.//h1')[0]
    if title_node.getparent()==slots['header']:
        wrapper=etree.Element('div',{'class':'report-head__copy'});slots['header'].replace(title_node,wrapper);wrapper.append(title_node)
    template.find('.//title').text=_text(title_node)
    logics=main.xpath('.//section[starts-with(@id,"logic-")]|.//article[@data-thesis-ref]')
    specs=figures or {}
    for index,node in enumerate(logics,1):
        node.set('class',(node.get('class','')+' strategy').strip())
        for heading in node.xpath('./div[contains(@class,"argument-heading")]/h2'):
            owner=heading.getparent();node.insert(0,heading)
            if not len(owner):owner.getparent().remove(owner)
        _group_figures(node,specs,index,profile);slots['logics'].append(node)
    for node in list(main):
        if node is header:continue
        identity=node.get('id','');tag=etree.QName(node).localname if isinstance(node.tag,str) else ''
        if identity in ('summary','conclusion'):
            for child in list(node):slots['summary'].append(child)
            for para in slots['summary'].xpath('./p'):para.set('class','conclusion-band')
        elif identity in ('risk','risks','section-risk'):
            for child in list(node):slots['risk'].append(child)
        elif tag=='footer':
            for child in list(node):slots['source'].append(child)
            slots['source'].text=(slots['source'].text or '')+(node.text or '')
        elif tag not in ('script','style'):
            # Empty thesis wrappers carry no extra research content.
            if not _text(node).strip():continue
            _group_figures(node,specs,len(logics)+1,profile);slots['supporting'].append(node)
    for item in slots['risk'].xpath('.//li'):
        item.set('data-print-separator','' if _text(item).rstrip().endswith(('。','；','！','？','.',';','!','?')) else '；')
    for name in ('summary','risk'):
        if not len(slots[name]):slots[name].getparent().remove(slots[name])
    headings=template.xpath('//main//h2')
    for index,heading in enumerate(headings,1):
        section=heading.getparent();target=section.get('id') or 'paper-section-'+str(index);section.set('id',target)
        label=re.sub(r'^[一二三四五六七八九十]+、','',_text(heading))
        if label.startswith('策略逻辑'):label=label.split('：')[0]
        link=etree.SubElement(slots['contents'],'a',href='#'+target);number=etree.SubElement(link,'span');number.text=str(index)+'.';number.tail=label
    for name,node in slots.items():
        if node.getparent() is not None:node.attrib.pop('data-paper-slot',None)
    new_head=template.find('head');old_head=tree.find('head')
    for child in list(old_head) if old_head is not None else []:
        if child.tag=='title' or child.tag=='meta' and (child.get('charset') or child.get('name') in ('viewport','rh-report-template')):continue
        new_head.append(child)
    style=etree.SubElement(new_head,'style');style.text=(resource_directory(resources,'frontend')/'publication.css').read_text()+STYLE
    new_body=template.find('body');new_body.set('class',((body.get('class') or '')+' report-profile-a4 rh-publication').strip())
    for child in list(body):
        if child is main or main in child.iterdescendants() or child.tag=='a' and child.get('class')=='skip-link':continue
        new_body.append(child)
    if not any('/* Offline evidence charts.' in (node.text or '') for node in template.xpath('//script')):
        asset=etree.SubElement(new_head,'script');asset.text=(design_dir/'evidence-charts.js').read_text()
    if profile=='detailed':
        if details is not None:
            for key,items in details.items():
                nodes=template.xpath('//main//*[@id="'+key+'"]|//main//*[@data-thesis-ref="'+key+'"]');parent=nodes[0] if nodes else template.find('.//main')
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



def compatible_schemes(spec):
    """The same lossless visual choices offered by the browser gallery."""
    choice=spec.get('auto_visual') or dict(scheme=spec.get('scheme'),data=spec.get('data',{}))
    kind,data=choice.get('scheme'),choice.get('data',{})
    if kind in ('line','area'):
        area=data.get('measure')=='scale' and all(value is None or value>=0 for value in data.get('values',[]))
        return ['line',*(['area'] if area else []),'grouped_bar','lollipop']
    if kind in ('ranked_bar','lollipop','diverging_bar'):return ['ranked_bar','lollipop','diverging_bar']
    if kind=='dumbbell' or kind=='grouped_bar' and data.get('pairs'):return ['dumbbell','grouped_bar','slope']
    if kind=='heatmap' and all(re.match(r'^\d{4}-\d{2}',str(value)) for value in data.get('labels',[])):
        return ['heatmap','multi_line','grouped_bar']
    if kind in ('multi_line','grouped_bar') and len(data.get('series',[]))>1:return ['multi_line','grouped_bar','heatmap']
    return [kind]


def visual_profile_for(profile,specs,theme='research'):
    if profile is None:profile={}
    if not isinstance(profile,dict) or set(profile)-{'theme','schemes'}:raise ContractError('图表样式设置无效')
    selected=profile.get('theme',theme);schemes=profile.get('schemes',{})
    if not isinstance(selected,str) or visual_theme(selected) is None:raise ContractError('配色未登记')
    if not isinstance(schemes,dict):raise ContractError('图形选择须对应当前报告')
    for target,scheme in schemes.items():
        if not isinstance(target,str) or target not in specs:raise ContractError('图表不属于当前报告，请刷新后重新选择')
        if not isinstance(scheme,str) or scheme not in compatible_schemes(specs[target]):raise ContractError('当前数据不适用所选图形')
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


def style_document(document,profile=None):
    """Resolve view choices over frozen source specs, never over financial data."""
    result=deepcopy(document)
    sources=result.get('visual_sources') or {block['spec']['target']:deepcopy(block['spec']) for block in result['blocks'] if block['kind']=='chart'}
    selected=visual_profile_for(profile,sources,result.get('theme','research'))
    result.update(theme=selected['theme'],visual_profile=selected,visual_sources=sources)
    for block in result['blocks']:
        if block['kind']=='chart':
            target=block['spec']['target'];base=sources[target]
            block['spec']=styled_spec(base,selected['schemes'][target]) if target in selected['schemes'] else deepcopy(base)
    for key,coverage in result.get('figure_coverage',{}).items():
        items=[block['spec'] for block in result['blocks'] if block['kind']=='chart' and block['spec'].get('paper_logic')==key]
        if items:coverage['schemes']=list(dict.fromkeys(item.get('auto_visual',{}).get('scheme',item.get('scheme','')) for item in items))
    return result


def design(content,resources,*,profile='standard',theme='research',visual_profile=None):
    if not isinstance(profile,str) or profile not in PROFILES:raise ContractError('请选择标准报告或详细报告')
    if visual_theme(theme) is None:raise ContractError('配色未登记')
    from .visual import render as render_source
    tree=dom.document_fromstring(render_source(content,resources,visual_profile=dict(theme=theme),report_profile=profile));main=tree.find('.//main')
    if main is None:raise ContractError('报告正文不可用')
    specs=_specs(content,tree,profile);_append_support(main,specs,_support_figures(content,list(specs.values()),profile),profile)
    sections={f'logic-{i}':node for i,node in enumerate(main.xpath('.//section[starts-with(@id,"logic-")]'),1)} if content.get('draft') else {node.get('data-thesis-ref'):node for node in main.xpath('.//article[@data-thesis-ref]')}
    bindings={}
    for key,node in sections.items():
        refs=[];calcs=[];context_refs=[]
        for plot in node.xpath('.//*[@id]'):
            spec=specs.get(plot.get('id'))
            if spec:
                actual=spec.get('data_refs',spec.get('refs',[]))
                context=spec.get('plot_context',{}).get('context_data_refs',[])
                refs.extend(ref for ref in actual if ref not in context)
                context_refs.extend(context)
                calcs.extend(spec.get('calculation_refs',[]))
        refs.extend(node.xpath('.//tr[@data-observation-id]/@data-observation-id'))
        for figure in node.xpath('.//figure[@data-calculation-ref]'):calcs.append(figure.get('data-calculation-ref'))
        bindings[key]=dict(data_refs=list(dict.fromkeys(refs)),calculation_refs=list(dict.fromkeys(calcs)))
        if context_refs:
            bindings[key]['plot_context_refs']=list(dict.fromkeys(context_refs))
    coverage={}
    for key,node in sections.items():
        plotted=[specs[ident] for ident in node.xpath('.//*[@id]/@id') if ident in specs]
        kinds={spec.get('coverage_kind','numeric') for spec in plotted}
        table_refs=node.xpath('.//tr[@data-observation-id]/@data-observation-id')
        coverage[key]=dict(figures=len(plotted),status='numeric' if 'numeric' in kinds else 'numeric_table' if table_refs else 'qualitative' if plotted else 'unvisualized',
                           schemes=list(dict.fromkeys(spec.get('auto_visual',{}).get('scheme',spec.get('scheme','')) for spec in plotted)),
                           data_refs=list(dict.fromkeys([ref for spec in plotted for ref in spec.get('data_refs',spec.get('refs',[]))]+table_refs)))
        if table_refs:coverage[key]['tables']=len(node.xpath('.//table[.//tr[@data-observation-id]]'))
        if bindings[key].get('plot_context_refs'):
            coverage[key].update(argument_data_refs=bindings[key]['data_refs'],context_data_refs=bindings[key]['plot_context_refs'])
    projection=project_content(content,profile,bindings)
    tree=compose_strategy_document(tree,resources,figures=specs,profile=profile,details=projection['logic_blocks']);main=tree.find('.//main')
    blocks=_blocks(main,specs)
    selected_visual=visual_profile_for(visual_profile,specs,theme)
    profile_nodes=tree.xpath('//script[@id="rh-visual-profile"]')
    if len(profile_nodes)!=1:raise ContractError('报告缺少唯一图表设置')
    profile_nodes[0].text=canonical(selected_visual).replace('<','\\u003c')
    data=dict(schema='researchhelper.presentation-document',version=1,profile=profile,theme=theme,template=dict(id=tree.find('body').get('data-report-template'),version=TEMPLATE_VERSION),output_type='report',
      audience=projection['audience'],dataset=publication_dataset(content),figure_coverage=coverage,content_hash=projection['content_hash'],title=next((b['text'] for b in blocks if b['kind']=='title'),'研究报告'),blocks=blocks)
    data['visual_sources']=deepcopy(specs)
    data=style_document(data,selected_visual)
    body=tree.find('body');frozen=etree.SubElement(body,'script',id='rh-publication-document',type='application/json');frozen.text=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
    return dict(document=data,html='<!doctype html>\n'+etree.tostring(tree,encoding='unicode',method='html'))


def render_html(content,resources,*,profile='standard',theme='research',visual_profile=None):
    return design(content,resources,profile=profile,theme=theme,visual_profile=visual_profile)['html']
