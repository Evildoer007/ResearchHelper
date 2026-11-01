"""Deterministic report chart selection over frozen observations.

Selection is shared by App and Skill. It neither acquires data nor changes
research content. Unsupported shapes keep their original chart or table.
"""
from copy import deepcopy
from html import escape
from math import isfinite

VERSION = 1


def _number(value):
    return type(value) in (int, float) and isfinite(value)


def _shown(value):
    if value is None:
        return '未返回'
    if not _number(value):
        return str(value)
    if value and abs(value) < .0001:
        return format(value, '.3g')
    return format(value, ',.4f').rstrip('0').rstrip('.')


def _table(columns, rows):
    return dict(columns=columns, rows=rows)


def _trend_table(labels, series, unit):
    rows=[]
    for item in series:
        actual=[(index,value) for index,value in enumerate(item['values']) if _number(value)]
        if not actual:
            continue
        low=min(value for _,value in actual);high=max(value for _,value in actual)
        rows.append(dict(label=item.get('name') or '观测值', cells=[
            _shown(item['values'][0]), _shown(item['values'][-1]), _shown(low)+'至'+_shown(high)],
            values=[actual[0][1],actual[-1][1],low,high],
            observed_dates=[labels[actual[0][0]],labels[actual[-1][0]]]))
    return _table(['序列',str(labels[0]),str(labels[-1]),'区间'+unit],rows)


def _raw(spec):
    """Translate both existing figure contracts, without guessing extra series."""
    if spec.get('scheme') and isinstance(spec.get('data'),dict):
        return spec['scheme'],deepcopy(spec['data'])
    name=spec.get('name',spec.get('title',''))
    if spec.get('kind')=='paired':
        return 'dumbbell',dict(labels=spec['dates'],pairs=spec['pairs'],periods=spec['periods'],unit=spec['unit'],name=name)
    if spec.get('kind')=='changes':
        return 'diverging_bar',dict(labels=spec['dates'],values=spec['values'],unit=spec['unit'],name=name)
    if 'dates' in spec and 'values' in spec:
        scheme='line' if spec.get('type')=='line' and not spec.get('horizontal') else 'lollipop'
        return scheme,dict(labels=spec['dates'],values=spec['values'],unit=spec.get('unit',''),name=name)
    rows=spec.get('rows',[])
    if not rows or any(not _number(row.get('value')) or row.get('unit')!=spec.get('unit') for row in rows):
        return None
    if spec.get('kind')=='observations':
        labels=spec['categories'];observed={row['label']:row['value'] for row in rows}
        return 'line',dict(labels=labels,values=[observed.get(label) for label in labels],unit=spec['unit'],name=name)
    groups=spec.get('groups',[])
    if len(groups)>1:
        return 'grouped_bar',dict(labels=spec['categories'],series=[
            dict(name=group['name'],values=[rows[index]['value'] for index in group['indices']]) for group in groups],unit=spec['unit'])
    return 'lollipop',dict(labels=[row['label'] for row in rows],values=[row['value'] for row in rows],unit=spec['unit'],name=name)


def _select(content,spec):
    translated=_raw(spec)
    if translated is None:
        return spec
    scheme,data=translated;unit=data.get('unit','');ledger=None;reason='explicit_shape'
    draft=content.get('draft',{})
    calculations={row['id']:row for row in draft.get('calculations',[])}
    facts={row['id']:row for row in draft.get('facts',[])}
    refs=spec.get('data_refs',spec.get('refs',[]))
    if scheme in ('line','area','multi_line','indexed_line','rolling','excess_return','drawdown'):
        labels=data['labels'];series=data.get('series',[dict(name=data.get('name',''),values=data.get('values',[]))])
        if not labels or any(len(row['values'])!=len(labels) for row in series):
            return spec
        ledger=_trend_table(labels,series,unit);reason='actual_time_series'
        # Endpoints are not an observed path, even if a date looks plausible.
        if scheme=='line' and len(labels)<3:
            scheme='lollipop';reason='sparse_observations'
    elif scheme=='heatmap':
        ledger=_trend_table(data['labels'],[dict(name=name,values=values) for name,values in zip(data['rows'],data['matrix'])],unit)
        reason='aligned_matrix'
    elif scheme=='dumbbell':
        periods=data['periods'];rows=[]
        for label,pair in zip(data['labels'],data['pairs']):
            if len(pair)!=2 or not all(_number(value) for value in pair):
                return spec
            delta=pair[1]-pair[0];delta_unit='个百分点' if unit=='%' else unit
            rows.append(dict(label=label,cells=[_shown(pair[0]),_shown(pair[1]),('+' if delta>0 else '')+_shown(delta)+delta_unit],values=list(pair),
                             display_derivation=dict(method='endpoint_difference',inputs=list(pair),value=delta,unit=delta_unit)))
        ledger=_table(['指标',str(periods[0]),str(periods[1]),'变动'],rows);reason='same_object_two_periods'
    elif scheme in ('ranked_bar','lollipop','diverging_bar','benchmark_dot'):
        labels=data['labels'];values=data['values']
        if len(labels)!=len(values) or not values or any(value is not None and not _number(value) for value in values):
            return spec
        rows=[]
        if spec.get('kind')=='changes':
            for calc_id in spec.get('calculation_refs',[]):
                calc=calculations.get(calc_id,{});raw=[facts.get(ref) for ref in calc.get('data_refs',[])]
                if len(raw)!=2 or not all(raw):
                    continue
                index=len(rows)
                rows.append(dict(label=labels[index],cells=[_shown(raw[0]['value']),_shown(raw[1]['value']),_shown(calc['value'])+'%'],
                                 values=[raw[0]['value'],raw[1]['value'],calc['value']],data_refs=calc['data_refs']))
            if len(rows)==len(labels):
                ledger=_table(['指标',str(spec['comparison']),str(spec['period']),'同比'],rows)
        if ledger is None:
            ledger=_table(['对象','原值'+unit],[dict(label=label,cells=[_shown(value)],values=[value]) for label,value in zip(labels,values)])
        if scheme=='ranked_bar':scheme='lollipop'
        reason='category_comparison'
    elif scheme=='grouped_bar':
        rows=[dict(label=label,cells=[_shown(row['values'][index]) for row in data['series']],
                   values=[row['values'][index] for row in data['series']]) for index,label in enumerate(data['labels'])]
        ledger=_table(['指标']+[row['name'] for row in data['series']],rows);reason='same_unit_multiple_series'
    elif scheme=='candlestick':
        if data.get('values'):
            rows=[dict(label=label,cells=[_shown(value) for value in row],values=list(row)) for label,row in zip(data['labels'],data['values'])]
            ledger=_table(['日期','开','收','低','高'],rows);reason='complete_observed_ohlc'
    elif scheme=='range':
        ledger=_table(['对象','下界'+unit,'上界'+unit],[dict(label=row['name'],cells=[_shown(row['min']),_shown(row['max'])],values=[row['min'],row['max']]) for row in data['ranges']]);reason='observed_bounds'
    elif scheme=='percentile':
        ledger=_table(['对象','历史分位','样本数'],[dict(label=data.get('name','当前值'),cells=[_shown(data['percentile'])+'%',str(data['sampleCount'])],values=[data['percentile'],data['sampleCount']])]);reason='registered_percentile'
    if ledger:
        ledger['unit']=unit
    choice=dict(version=VERSION,scheme=scheme,data=data,reason=reason,
                source_refs=list(dict.fromkeys(refs)),ledger=ledger)
    return dict(spec,auto_visual=choice)


def select_figures(content,figures):
    """Return defensive copies; a failed optional visual never blocks delivery."""
    result=[]
    for original in figures:
        spec=deepcopy(original)
        try:
            result.append(_select(content,spec))
        except (KeyError,TypeError,ValueError,IndexError,OverflowError):
            result.append(spec)
    return result


def ledger_html(spec,chart,source_target=None):
    choice=spec.get('auto_visual',{});table=choice.get('ledger')
    if not table or not table['rows']:
        return chart
    columns=''.join('<th scope="col">'+escape(str(label))+'</th>' for label in table['columns'])
    rows=[]
    for index,row in enumerate(table['rows']):
        label=escape(str(row['label']))
        if source_target:
            label='<a href="#'+escape(source_target,quote=True)+'" aria-haspopup="dialog">'+label+'</a>'
        cells=''.join('<td>'+escape(str(value))+'</td>' for value in row['cells'])
        rows.append('<tr data-chart-row="'+str(index)+'"><th scope="row">'+label+'</th>'+cells+'</tr>')
    return ('<div class="evidence-layout" data-auto-scheme="'+escape(choice['scheme'])+'" data-ledger-columns="'+str(len(table['columns']))+'">'+chart+
            '<div class="evidence-ledger"><table><thead><tr>'+columns+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table></div></div>')
