"""Financial comparison figures from frozen, registered draft arithmetic.

Only presentation is derived. Invalid or inapplicable calculations fall back to
raw figures and tables; no report, prose, observation or method is rewritten.
"""
from collections import defaultdict
from decimal import Decimal,InvalidOperation
from datetime import date
from math import isfinite, ceil
from .draft_analysis import METHODS,execute,_day,BALANCE
from .contracts import ContractError,fingerprint
from .publication_content import draft_logic_data_refs

FIELDS={
    'ths_revenue_stock':'营业收入','ths_operating_cost_stock':'营业成本',
    'ths_np_stock':'净利润','ths_np_atoopc_stock':'归母净利润',
    'ths_ncf_from_oa_stock':'经营现金流',
    'ths_account_receivable_stock':'应收账款','ths_inventory_stock':'存货','ths_accounts_payable_stock':'应付账款',
}
RATIO_FIELDS={
    'raw_margin_v1':('ths_revenue_stock','ths_operating_cost_stock'),
    'raw_percent_v1':('ths_np_stock','ths_revenue_stock'),
    'raw_multiple_v1':('ths_ncf_from_oa_stock','ths_np_stock'),
}


def _validated(content):
    draft=content.get('draft',{})
    owned={row['id'] for row in content.get('source_records',[]) if
           row.get('task_id')==content['task_id'] and row.get('research_version')==content['research_version']}
    groups=defaultdict(list)
    for row in draft.get('facts',[]):groups[row['id']].append(row)
    facts={key:rows[0] for key,rows in groups.items() if len(rows)==1 and rows[0].get('artifact_id') in owned}
    result=[]
    for item in draft.get('calculations',[]):
        method=item.get('method');refs=item.get('data_refs',[])
        if method not in {'raw_change_v1',*RATIO_FIELDS} or item.get('method_version')!=METHODS[method]['version']:
            continue
        if len(refs)!=2 or not all(ref in facts for ref in refs):continue
        left,right=(facts[ref] for ref in refs)
        if (not left.get('code') or left['code']!=right['code'] or item.get('code')!=left['code']
                or left['unit']!=right['unit'] or left.get('series_basis')!=right.get('series_basis')
                or right['period']!=item.get('period') or item.get('unit')!=METHODS[method]['unit']):continue
        if left['unit']=='原始单位待核对' and item.get('conditional_equal_scale') is not True:continue
        if method=='raw_change_v1':
            before,after=_day(left['period']),_day(right['period'])
            if left['field'] not in FIELDS or left['field']!=right['field'] or before is None or after is None:continue
            try:matching=before==after.replace(year=after.year-1)
            except ValueError:matching=False
            if not matching:continue
        elif ((left['field'],right['field'])!=RATIO_FIELDS[method] or left['period']!=right['period']):continue
        try:
            values=item.get('inputs',{})
            if any(row['value'] is None or isinstance(row.get('raw_value',row['value']),bool) for row in (left,right)):continue
            if any(Decimal(str(values.get(key)))!=Decimal(str(row.get('raw_value',row['value'])))
                   for key,row in zip(('a','b'),(left,right))):continue
            expected=execute(method,values)
            if expected['display']!=item.get('display') or expected['value']!=item.get('value'):continue
        except (ContractError,InvalidOperation,ValueError,TypeError):continue
        result.append(dict(item,rows=[left,right]))
    return result


def comparison_figures(content,raw_figures):
    """Return a comparable view while preserving all unrepresented raw series."""
    calculations=_validated(content)
    growth=defaultdict(list);ratios=defaultdict(list);figures=[]
    codes={row['code'] for row in calculations}
    prefix=lambda code:code+' ' if len(codes)>1 else ''
    for row in calculations:
        if row['method']=='raw_change_v1':
            growth[(row['code'],row['rows'][0]['period'],row['period'],row['rows'][0]['field'] in BALANCE)].append(row)
        else:ratios[(row['code'],row['method'])].append(row)

    def add(name,kind,rows,**payload):
        figures.append(dict(name=name,kind=kind,field='financial_comparison',
            data_refs=list(dict.fromkeys(ref for row in rows for ref in row['data_refs'])),
            calculation_refs=[row['id'] for row in rows],horizontal=True,**payload))

    for (code,before,after,is_balance),rows in growth.items():
        by_field=defaultdict(list)
        for row in rows:by_field[row['rows'][0]['field']].append(row)
        # Conflicting duplicates remain visible in their raw series.
        rows=[by_field[field][0] for field in FIELDS if len(by_field[field])==1]
        if len(rows)<2:continue
        add(prefix(code)+('营运资金余额同比变化' if is_balance else '经营指标同比变化'),'changes',rows,unit='%',
            dates=[FIELDS[row['rows'][0]['field']] for row in rows],values=[row['value'] for row in rows],
            period=after,comparison=before,height=max(220,len(rows)*38+50),
            keywords=['同比','增长','收入','成本','利润','盈利'],type='bar')

    paired={}
    for (code,method),rows in ratios.items():
        by_period=defaultdict(list)
        for row in rows:by_period[row['period']].append(row)
        if (len(by_period)!=2 or any(len(items)!=1 for items in by_period.values())
                or len({item['rows'][0].get('series_basis') for item in rows})!=1):continue
        periods=sorted(by_period,key=lambda value:_day(value) or date.min)
        before,after=(_day(value) for value in periods)
        if before is None or after is None:continue
        try:matching=before==after.replace(year=after.year-1)
        except ValueError:matching=False
        if matching:paired[(code,method)]=(periods,[by_period[value][0] for value in periods])
    for code in sorted(codes):
        profit=[paired[(code,method)] for method in ('raw_margin_v1','raw_percent_v1') if (code,method) in paired]
        if profit and all(pair[0]==profit[0][0] for pair in profit):
            rows=[row for _,items in profit for row in items]
            labels=['收入成本差占比' if items[0]['method']=='raw_margin_v1' else '报表净利润率' for _,items in profit]
            add(prefix(code)+'收入成本差占比与净利润率' if len(profit)>1 else prefix(code)+labels[0],
                'paired',rows,unit='%',dates=labels,periods=profit[0][0],
                pairs=[[items[0]['value'],items[1]['value']] for _,items in profit],height=225,
                keywords=['净利率','净利润率','差额占比','成本差','利润'],type='scatter')
        coverage=paired.get((code,'raw_multiple_v1'))
        if coverage:
            periods,rows=coverage
            add(prefix(code)+'经营现金流覆盖倍数','paired',rows,unit='倍',dates=['经营现金流/净利润'],
                periods=periods,pairs=[[row['value'] for row in rows]],height=190,
                keywords=['现金流','现金覆盖','现金回收'],type='scatter')
    represented={ref for figure in figures for ref in figure['data_refs']}
    remaining=[figure for figure in raw_figures if not (figure['field'] in FIELDS
        and figure.get('data_refs') and set(figure['data_refs']).issubset(represented))]
    for index,figure in enumerate(figures+remaining,1):figure['target']='draft-chart-'+str(index)
    return figures+remaining


def comparison_table(content):
    """A complete two-period matrix for one comparable financial source set."""
    facts=content.get('draft',{}).get('facts',[])
    if not facts or any(row.get('field') not in FIELDS for row in facts):return None
    owned={row['id'] for row in content.get('source_records',[]) if row.get('task_id')==content['task_id']
           and row.get('research_version')==content['research_version']}
    if any(row.get('artifact_id') not in owned for row in facts):return None
    if len({row['code'] for row in facts})!=1 or len({row['unit'] for row in facts})!=1:return None
    if len({row.get('series_basis') for row in facts})!=1:return None
    periods={row['period'] for row in facts}
    if len(periods)!=2 or any(_day(value) is None for value in periods):return None
    periods=sorted(periods,key=_day);grouped=defaultdict(lambda:defaultdict(list))
    for row in facts:grouped[row['field']][row['period']].append(row)
    if any(set(rows)!=set(periods) or any(len(batch)!=1 for batch in rows.values()) for rows in grouped.values()):return None
    return dict(code=facts[0]['code'],unit=facts[0]['unit'],periods=[_day(value).isoformat() for value in periods],
        rows=[dict(label=grouped[field][periods[0]][0]['label'],
                   observations=[grouped[field][period][0] for period in periods])
              for field in FIELDS if field in grouped],observations=len(facts))


def temporal_table(content):
    """Aligned economic series as columns, without filling gaps or merging bases."""
    facts=content.get('draft',{}).get('facts',[])
    sources={row['id'] for row in content.get('source_records',[]) if row.get('task_id')==content['task_id']
             and row.get('research_version')==content['research_version'] and row.get('source')=='ifind'
             and row.get('operation')=='edb_service'}
    if not facts or any(row.get('artifact_id') not in sources or row.get('field')!='value'
                        or row.get('axis_role','time')!='time' for row in facts):return None
    if len({row['unit'] for row in facts})!=1 or facts[0]['unit'] in ('','原始单位待核对'):return None
    groups={}
    for row in facts:
        if _day(row['period']) is None:return None
        key=(row['code'],row.get('series_basis',''));groups.setdefault(key,[]).append(row)
    if len(groups)<2:return None
    periods=None;columns=[]
    for rows in groups.values():
        indexed={row['period']:row for row in rows}
        if len(indexed)!=len(rows):return None
        current=set(indexed)
        if periods is not None and current!=periods:return None
        periods=current
        columns.append(dict(label=rows[0]['label'],values=indexed))
    return dict(unit=facts[0]['unit'],columns=[row['label'] for row in columns],
        rows=[dict(period=period,observations=[row['values'][period] for row in columns])
              for period in sorted(periods,key=_day)],observations=len(facts))


def display_observation(row):
    """Round display-only percentage noise; retain exact inputs in workpapers."""
    if row['value'] is None:return '未返回'
    raw=row.get('raw_value',row['value'])
    if row.get('unit')=='%':
        value=Decimal(str(raw));places=2 if abs(value)>=Decimal('.01') or not value else min(6,max(2,1-value.adjusted()))
        shown=format(value,',.'+str(places)+'f')
        return shown if Decimal(shown.replace(',','')) or not value else format(value,'.3g')
    if isinstance(raw,str):return raw
    value=row['value'];return format(int(value) if type(value) is float and value.is_integer() else value,',')


def catalogue_figures(content, figures):
    """Select comparative views from aligned actual macro observations."""
    aligned=temporal_table(content)
    if not aligned or len(aligned['columns']) not in range(2,7) or len(aligned['rows'])<3:return figures
    rows=aligned['rows'];labels=[row['period'] for row in rows];names=aligned['columns']
    values=[[row['observations'][index]['value'] for row in rows] for index in range(len(names))]
    refs=[observation['id'] for row in rows for observation in row['observations']]
    root=names[0].split(':')[0] if all(name.split(':')[0]==names[0].split(':')[0] for name in names) else '指标'
    selected=[dict(name=root+'走势对照',scheme='multi_line',kind='catalogue',field='value',unit=aligned['unit'],
        data=dict(labels=labels,series=[dict(name=name,values=value) for name,value in zip(names,values)],unit=aligned['unit']),
        data_refs=refs,horizontal=False,height=280)]
    if len(rows)>=4:
        selected.append(dict(name=root+'月度分化',scheme='heatmap',kind='catalogue',field='value',unit=aligned['unit'],
            data=dict(labels=labels,rows=names,matrix=values,unit=aligned['unit']),data_refs=refs,horizontal=False,height=260))
    covered=set(refs)
    for figure in figures:
        present=set(figure.get('data_refs',[]))
        if not present or not present.issubset(covered):selected.append(figure)
    for index,figure in enumerate(selected):figure['target']='draft-chart-'+str(index+1)
    return selected


def ohlc_figures(content, figures):
    """Use native candles only for complete, same-basis actual OHLC rows."""
    facts=content.get('draft',{}).get('facts',[])
    source_ids={row['id'] for row in content.get('source_records',[]) if row.get('task_id')==content['task_id']
                and row.get('research_version')==content['research_version'] and row.get('operation')=='cmd_history_quotation'}
    groups=defaultdict(list)
    for row in facts:
        if row.get('artifact_id') in source_ids and row.get('field') in ('open','close','low','high') and row.get('axis_role','time')=='time':
            groups[(row['code'],row['unit'],row.get('series_basis'))].append(row)
    candles=[];covered=set()
    for (code,unit,_),rows in groups.items():
        periods=defaultdict(dict);conflict=False
        for row in rows:
            if row['field'] in periods[row['period']]:conflict=True
            periods[row['period']][row['field']]=row
        if conflict or len(periods)<2 or any(not _day(period) for period in periods) or any(set(fields)!=set(('open','close','low','high')) for fields in periods.values()):continue
        ordered=sorted(periods,key=lambda value:_day(value) or date.min);values=[];refs=[]
        for period in ordered:
            fields=periods[period];parts=[fields[field]['value'] for field in ('open','close','low','high')]
            if any(isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value) for value in parts) or not parts[2]<=min(parts[0],parts[1])<=max(parts[0],parts[1])<=parts[3]:break
            values.append(parts);refs.extend(fields[field]['id'] for field in ('open','close','low','high'))
        if len(values)!=len(ordered):continue
        covered.update(refs);candles.append(dict(name=code+'开高低收',scheme='candlestick',kind='catalogue',field='ohlc',
            unit=unit,data=dict(labels=ordered,values=values,unit=unit),data_refs=refs,horizontal=False,height=280,
            keywords=['股价','价格','行情','走势','点位']))
    if not candles:return figures
    for spec in figures:
        refs=set(spec.get('data_refs',[]))
        if not refs or not refs.issubset(covered):candles.append(spec)
    for index,spec in enumerate(candles):spec['target']='draft-chart-'+str(index+1)
    return candles


def planned_figures(content, *, report_profile=None):
    """Single chart plan shared by HTML and static delivery."""
    from .drafts import draft_figures
    from .visual_selection import select_figures
    figures=comparison_figures(content,draft_figures(content['draft']['facts']))
    figures=select_figures(content,catalogue_figures(content,ohlc_figures(content,figures)))
    if report_profile!='standard':return figures
    from copy import deepcopy
    result=[]
    for original in figures:
        # Unknown currency scales belong to the workpaper; relative comparisons
        # can still be useful with their explicitly retained reading condition.
        if original.get('unit') in ('原始单位待核对','接口原值','原值') and original.get('field') in FIELDS:continue
        spec=deepcopy(original)
        if spec.get('unit') in ('原始单位待核对','接口原值','原值'):
            spec['unit']=''
            if spec.get('auto_visual',{}).get('data') is not None:spec['auto_visual']['data']['unit']=''
        result.append(spec)
    return result



def _indexed_comparison(spec,calculations):
    """Change only the plotting scale, using registered percentage changes."""
    if len(calculations)<2 or any(row['rows'][0]['value']<=0 or row['rows'][1]['value']<0 for row in calculations):return spec
    from copy import deepcopy
    result=deepcopy(spec);before=calculations[0]['rows'][0]['period'];after=calculations[0]['period']
    periods=[str(_day(value).year) for value in (before,after)]
    pairs=[[100.0,float(Decimal('100')+Decimal(str(row['value'])))] for row in calculations]
    data=dict(labels=spec['dates'],pairs=pairs,periods=periods,unit='',name='收入与利润变化')
    result.update(name='收入与利润变化',kind='indexed_comparison',pairs=pairs,periods=periods,unit='指数',
        context=periods[0]+'年=100',plot_scale=dict(kind='index',base=100,baseline_period=before,
        source_method='raw_change_v1',operation='100 + registered_percentage_change'),
        visual_reference=dict(family='F12 Dumbbell Queue',encoding='paired endpoints',implementation='ResearchHelper independent ECharts'))
    result['auto_visual']=dict(result.get('auto_visual',{}),scheme='dumbbell',data=data,reason='argument_bound_indexed_comparison')
    return result



def _cash_comparison(spec,calculations):
    """Emphasize the cash observation without claiming a causal decomposition."""
    from copy import deepcopy
    result=deepcopy(spec);fields=[row['rows'][0]['field'] for row in calculations]
    cash='ths_ncf_from_oa_stock'
    if cash not in fields:return result
    focus=fields.index(cash)
    data=dict(labels=spec['dates'],values=spec['values'],unit='%',name='年度同比',focusIndex=focus)
    result.update(name='现金流与盈利变化',context=str(_day(spec['period']).year)+'年同比',
        visual_reference=dict(family='F5 Tick Rows',encoding='ranked comparison',implementation='ResearchHelper independent ECharts'))
    result['auto_visual']=dict(result.get('auto_visual',{}),scheme='lollipop',data=data,reason='cash_flow_argument_comparison')
    return result



def _topic_references(logic,facts):
    """Recover optional plotting references from literal, task-owned fields."""
    aliases={
        'ths_revenue_stock':('收入','营收'), 'ths_operating_cost_stock':('成本',),
        'ths_np_stock':('净利润',), 'ths_np_atoopc_stock':('归母','利润'),
        'ths_ncf_from_oa_stock':('现金流','现金兑现','回款'), 'close':('收盘','股价','价格'),
        'ths_pe_ttm_stock':('PE','估值'), 'weight':('权重','成分'), 'unit_nav':('净值',), 'ths_unit_nvnf_fund':('净值',),
    }
    topic=logic.get('title','')+' '+logic.get('analysis','')
    sources=set(logic.get('source_refs',[]))
    named={row['code'] for row in facts.values() if row.get('code') and row['code'] in topic}
    return {ident for ident,row in facts.items() if (not sources or row.get('artifact_id') in sources)
            and (not named or row.get('code') in named)
            and any(word in topic for word in aliases.get(row.get('field'),(row.get('label',''),)) if word)}



def _mechanism_figures(logic,index,facts):
    """Preserve every frozen step; small connected panels keep the path printable."""
    steps=(logic.get('mechanism') or {}).get('steps',[])
    if len(steps)<2 or any(not isinstance(step,dict) or not isinstance(step.get('label'),str)
                          or not step['label'].strip() for step in steps):return []
    # A panel has at most four edges, never a limit on the research itself.
    # Balance long paths and repeat only their shared handoff node.
    count=ceil((len(steps)-1)/4)
    edges,remainder=divmod(len(steps)-1,count)
    result=[];start=0
    for segment in range(count):
        edge_count=edges+(1 if segment<remainder else 0)
        part=steps[start:start+edge_count+1]
        refs=list(dict.fromkeys(ref for step in part for ref in step.get('data_refs',[]) if ref in facts))
        nodes=[dict(id='step-'+str(start+i),name=step['label'],level=i,kind=step.get('kind','hypothesis'))
               for i,step in enumerate(part)]
        links=[dict(source=nodes[i]['id'],target=nodes[i+1]['id']) for i in range(len(nodes)-1)]
        rows=1 if len(part)<=3 else 2
        node_height=max(54,ceil(max(len(step['label']) for step in part)/4)*15+16)
        minimum_height=rows*node_height+(rows-1)*20+24
        name='传导路径示意' if count==1 else '传导路径示意'+str(segment+1)
        result.append(dict(name=name,context='传导假设，非数值预测',field='mechanism',unit='',horizontal=False,type='graph',
            paper_logic=f'logic-{index}',coverage_kind='mechanism',min_height=minimum_height,
            data_refs=refs,calculation_refs=[],workpaper_notes=[logic.get('analysis',''),logic.get('implication','')],risk=logic.get('risk',''),
            auto_visual=dict(version=1,scheme='evidence',data=dict(nodes=nodes,links=links,qualitative=True),reason='frozen_qualitative_mechanism')))
        start+=edge_count
    return result


def _observed_series_rows(content):
    """Read original time-series cells without adopting research evidence."""
    from .draft_sources import observations
    task=dict(id=content['task_id'],version=content['research_version'],input_hash=content.get('input_hash'),
        source_records=[row for row in content.get('source_records',[]) if row.get('kind')=='api'
            and row.get('operation') in ('cmd_history_quotation','date_sequence','edb_service','search_edb')],materials=[])
    if 'draft_source_reader_version' in content:
        task['draft_source_reader_version']=content['draft_source_reader_version']
    try:
        return {_market_row_key(row) for row in observations(task)}
    except (ContractError,TypeError,ValueError,KeyError):
        return set()


def _market_row_key(row):
    from .contracts import canonical
    return canonical({key:row.get(key) for key in
        ('artifact_id','locator','code','field','period','unit','series_basis','value','raw_value')})


def _observed_window(content,spec,refs,facts,original_rows):
    """Add only real, same-original context between explicitly cited endpoints.

    Context points stay separate from the argument's submitted references.
    A missing or changed cell disables expansion, never the report itself.
    """
    from .market_series import SERIES_LABELS
    if spec.get('kind') or spec.get('type')!='line' or spec.get('field') not in {'close','value',*SERIES_LABELS}:
        return None
    anchors=[ref for ref in spec.get('data_refs',[]) if ref in refs and ref in facts]
    if len(anchors)<2 or len({facts[ref]['artifact_id'] for ref in anchors})!=1:return None
    dates=[_day(facts[ref]['period']) for ref in anchors]
    if any(day is None for day in dates) or len(set(dates))<2:return None
    start,end=min(dates),max(dates);source=facts[anchors[0]]['artifact_id'];selected=[]
    from .contracts import market_date
    try:
        if end>date.fromisoformat(market_date(content['cutoff'])):return None
    except (ContractError,ValueError,KeyError):return None
    for ref in spec.get('data_refs',[]):
        row=facts.get(ref);day=_day(row.get('period')) if row else None
        if day is None:return None
        if start<=day<=end:
            try:matches=_market_row_key(row) in original_rows
            except (ContractError,ValueError,TypeError):return None
            if row['artifact_id']!=source or not matches:return None
            selected.append(ref)
    context=[ref for ref in selected if ref not in refs]
    if not context:return None
    return dict(kind='observed_window',source_ref=source,anchor_refs=anchors,
        start=start.isoformat(),end=end.isoformat(),context_data_refs=context)


def _argument_series(content):
    """Keep underlying macro series available after a combined view is chosen."""
    if temporal_table(content) is None:return []
    from .drafts import draft_figures
    from .visual_selection import select_figures
    figures=select_figures(content,draft_figures(content['draft']['facts']))
    for index,spec in enumerate(figures,1):
        spec.update(target='argument-series-'+str(index),argument_only=True)
    return figures


def _same_period_comparison(refs,facts):
    """Plot cited macro readings side by side, never as contribution shares."""
    rows=[row for ident,row in facts.items() if ident in refs]
    if (len(rows)<2 or len({row['period'] for row in rows})!=1
            or any(row.get('field')!='value' or type(row['value']) not in (int,float)
                   or not isfinite(row['value']) for row in rows)
            or len({row['code'] for row in rows})!=len(rows)
            or len({row['unit'] for row in rows})!=1
            or rows[0]['unit'] in ('','原始单位待核对')):return None
    return dict(name='当期读数对照',field='value',unit=rows[0]['unit'],context=rows[0]['period'],
        dates=[row['label'] for row in rows],values=[row['value'] for row in rows],
        data_refs=[row['id'] for row in rows],type='bar',horizontal=True)


VISUAL_LAYOUT_VERSION = 1


def _repeated_endpoint_views(figures, facts):
    """Retain the first full paths; compare repeated endpoints in one view.

    Only literal endpoint arguments qualify. A middle-point citation, different
    range, unknown unit, or different source keeps its complete original view.
    """
    from copy import deepcopy

    def series(spec):
        dates,values,points=spec.get('dates',[]),spec.get('values',[]),spec.get('point_refs',[])
        if (spec.get('auto_visual',{}).get('scheme')!='line' or len(dates)<3
                or len(values)!=len(dates) or len(points)!=len(dates)
                or spec.get('unit') in ('',None,'原始单位待核对')):
            return None
        days=[_day(value) for value in dates]
        if None in days or days!=sorted(set(days)):
            return None
        if any(not isinstance(refs,list) or len(refs)!=1 or refs[0] not in facts for refs in points):
            return None
        for offset in (0,len(points)-1):
            row=facts[points[offset][0]];value=values[offset]
            if (type(value) not in (int,float) or not isfinite(value) or row.get('value') is None
                    or row.get('unit')!=spec['unit'] or _day(row.get('period'))!=days[offset]):
                return None
            try:
                if Decimal(str(row.get('raw_value',row['value'])))!=Decimal(str(value)):return None
            except (InvalidOperation,ValueError,TypeError):return None
        identity=fingerprint({key:spec.get(key) for key in ('name','field','unit','dates','values','point_refs','data_refs')})
        return dict(identity=identity,periods=[days[0].isoformat(),days[-1].isoformat()],
                    endpoints=[points[0][0],points[-1][0]])

    result=[];seen={};by_logic={}
    for spec in figures:
        by_logic.setdefault(spec.get('paper_logic') or spec['target'],[]).append(spec)
    for specs in by_logic.values():
        groups={}
        for spec in specs:
            info=series(spec)
            if not info or info['identity'] not in seen:continue
            own_refs=set(spec.get('argument_data_refs',[])) & set(spec.get('data_refs',[]))
            if not own_refs or not own_refs<=set(info['endpoints']):continue
            key=(spec['unit'],*info['periods'])
            groups.setdefault(key,[]).append((spec,info))
        replacements={};removed=set()
        for (unit,before,after),items in groups.items():
            if len(items)<2 or len({spec['name'] for spec,_ in items})!=len(items):continue
            first=items[0][0];labels=[spec['name'] for spec,_ in items]
            pairs=[[facts[ref]['value'] for ref in info['endpoints']] for _,info in items]
            refs=list(dict.fromkeys(ref for _,info in items for ref in info['endpoints']))
            ledger=dict(columns=['指标',before,after],rows=[dict(label=label,
                cells=[display_observation(facts[ref]) for ref in info['endpoints']],values=list(pair))
                for label,pair,(_,info) in zip(labels,pairs,items)],unit=unit)
            combined=dict(name='区间首末读数',kind='paired',field='value',unit=unit,
                dates=labels,pairs=pairs,periods=[before,after],context=before+'与'+after,
                type='scatter',horizontal=True,height=max(180,86+44*len(items)),
                target=first['target'],paper_logic=first['paper_logic'],coverage_kind='numeric',
                data_refs=refs,point_refs=[info['endpoints'] for _,info in items],
                calculation_refs=list(dict.fromkeys(ref for spec,_ in items for ref in spec.get('calculation_refs',[]))),
                argument_data_refs=sorted(set(refs)),reference_origin=first.get('reference_origin','explicit'),
                inspection_target=first['inspection_target'],
                reused_time_context=[dict(source_figure=seen[info['identity']],
                    full_data_refs=list(spec['data_refs']),endpoint_refs=list(info['endpoints'])) for spec,info in items],
                auto_visual=dict(version=1,scheme='dumbbell',data=dict(labels=labels,pairs=pairs,
                    periods=[before,after],unit=unit,name='区间首末读数'),
                    source_refs=refs,ledger=ledger,reason='repeated_series_endpoint_comparison'))
            replacements[first['target']]=combined
            removed.update(spec['target'] for spec,_ in items[1:])
        for spec in specs:
            if spec['target'] in removed:continue
            selected=replacements.get(spec['target'],spec)
            result.append(deepcopy(selected))
            if selected is spec:
                info=series(spec)
                if info:seen.setdefault(info['identity'],spec['target'])
    return result


def logic_figure_plan(content, *, report_profile='standard'):
    """Assign a fresh, data-bound figure to every compatible argument.

    A figure is a view, not a consumable resource. The same observation may
    support different arguments, but each view must stay inside its references.
    """
    from copy import deepcopy
    base=planned_figures(content,report_profile=report_profile)
    argument_series=_argument_series(content)
    calculations={row['id']:row for row in _validated(content)}
    owned={row['id'] for row in content.get('source_records',[]) if
           row.get('task_id')==content['task_id'] and row.get('research_version')==content['research_version']}
    facts={row['id']:row for row in content.get('draft',{}).get('facts',[]) if row.get('artifact_id') in owned}
    result=[];represented=set();covered_series_refs=set();original_rows=_observed_series_rows(content)
    for index,logic in enumerate(content.get('draft',{}).get('logics',[]),1):
        refs={ref for ref in draft_logic_data_refs(logic) if ref in facts}
        refs.update(ref for ident in logic.get('calculation_refs',[]) if ident in calculations for ref in calculations[ident]['data_refs'])
        reference_origin='explicit'
        if not refs:
            refs=_topic_references(logic,facts);reference_origin='literal_topic_match'
        candidates=[]
        for original in base+argument_series:
            if original.get('argument_only') and any(spec.get('kind')=='catalogue' and spec.get('field')=='value' for spec in candidates):continue
            spec=deepcopy(original)
            window=_observed_window(content,spec,refs,facts,original_rows)
            plot_refs=refs|set(window['context_data_refs']) if window else refs
            if window:spec['plot_context']=window
            if spec.get('kind')=='changes':
                selected=[calculations[ident] for ident in spec.get('calculation_refs',[]) if ident in calculations
                          and set(calculations[ident]['data_refs'])<=refs]
                if not selected:continue
                spec.update(dates=[FIELDS[row['rows'][0]['field']] for row in selected],
                    values=[row['value'] for row in selected],calculation_refs=[row['id'] for row in selected],
                    data_refs=list(dict.fromkeys(ref for row in selected for ref in row['data_refs'])))
                from .visual_selection import select_figures
                spec.pop('auto_visual',None);spec=select_figures(content,[spec])[0]
                if any(word in logic.get('title','') for word in ('现金','回款','资金')):
                    spec=_cash_comparison(spec,selected)
                else:spec=_indexed_comparison(spec,selected)
            elif not spec.get('data_refs') or not set(spec['data_refs'])<=plot_refs:
                selected=[offset for offset,ref in enumerate(spec.get('data_refs',[])) if ref in plot_refs]
                if spec.get('kind') or len(selected)<2 or len(spec.get('dates',[]))!=len(spec.get('data_refs',[])):continue
                spec.update(dates=[spec['dates'][offset] for offset in selected],
                            values=[spec['values'][offset] for offset in selected],
                            data_refs=[spec['data_refs'][offset] for offset in selected])
                from .visual_selection import select_figures
                spec.pop('auto_visual',None);spec=select_figures(content,[spec])[0]
            candidates.append(spec);represented.add(original['target'])
            if original.get('argument_only'):covered_series_refs.update(spec['data_refs'])
        if argument_series and not candidates:
            comparison=_same_period_comparison(refs,facts)
            if comparison:
                from .visual_selection import select_figures
                candidates.extend(select_figures(content,[comparison]))
        candidates.extend(_mechanism_figures(logic,index,facts))
        market=[spec for spec in candidates if spec.get('type')=='line' and spec.get('dates')]
        aligned=len(market)>1 and all(spec['dates']==market[0]['dates'] for spec in market)
        for number,spec in enumerate(candidates,1):
            spec.update(target=f'logic-{index}-chart-{number}',paper_logic=f'logic-{index}',
                        coverage_kind=spec.get('coverage_kind','numeric'),argument_data_refs=sorted(refs),reference_origin=reference_origin)
            if spec in market:
                dates=[_day(value) for value in spec['dates']]
                if all(dates):spec['context']=dates[0].isoformat()+'至'+dates[-1].isoformat()
                data=spec.get('auto_visual',{}).get('data',{})
                data['compactDates']=True
                data['numberDigits']=6 if any(0<abs(value)<.01 for value in spec.get('values',[]) if isinstance(value,(int,float))) else 2
                if aligned:spec.update(sync_group=f'logic-{index}-'+('series' if spec.get('field')=='value' else 'market'),axis_alignment='same_observed_dates')
            if spec.get('kind') in ('changes','indexed_comparison'):
                spec['point_refs']=[calculations[ident]['data_refs'] for ident in spec.get('calculation_refs',[]) if ident in calculations]
            elif len(spec.get('data_refs',[]))==len(spec.get('dates',[])):
                spec['point_refs']=[[ref] for ref in spec['data_refs']]
            spec['inspection_target']='draft-figure-basis-'+spec['target']
            result.append(spec)
    for original in base:
        if (original['target'] not in represented and set(original.get('data_refs',[]))<=facts.keys()
                and not (original.get('kind')=='catalogue' and original.get('field')=='value'
                         and set(original.get('data_refs',[]))<=covered_series_refs)):
            result.append(dict(deepcopy(original),target='supplement-'+original['target']))
    policy=content.get('draft',{}).get('visual_layout_version')
    return _repeated_endpoint_views(result,facts) if type(policy) is int and policy==VISUAL_LAYOUT_VERSION else result
