"""Deterministic report chart selection over frozen observations.

Selection is shared by App and Skill. It neither acquires data nor changes
research content. Unsupported shapes keep their original chart or table.
"""
from copy import deepcopy
from datetime import date
from html import escape
from math import isclose, isfinite

from .contracts import MAX_BROWSER_INTEGER
from .visual_catalog import catalogue

VERSION = 1
_REGISTERED_SCHEMES = frozenset(row['id'] for row in catalogue()['schemes'])


def _number(value):
    return type(value) is int or type(value) is float and isfinite(value)


def _shown(value):
    if value is None:
        return '未返回'
    if type(value) is int:
        return format(value, ',d')
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
        if spec['scheme'] == 'dumbbell':
            return 'grouped_bar', paired_columns(spec['data'])
        return spec['scheme'],deepcopy(spec['data'])
    name=spec.get('name',spec.get('title',''))
    if spec.get('kind')=='paired':
        return 'grouped_bar', paired_columns(dict(
            labels=spec['dates'],pairs=spec['pairs'],periods=spec['periods'],unit=spec['unit'],name=name))
    if spec.get('kind')=='changes':
        return 'diverging_bar',dict(labels=spec['dates'],values=spec['values'],unit=spec['unit'],name=name)
    if 'dates' in spec and 'values' in spec:
        scheme='line' if spec.get('type')=='line' and not spec.get('horizontal') else 'ranked_bar'
        return scheme,dict(labels=spec['dates'],values=spec['values'],unit=spec.get('unit',''),name=name)
    rows=spec.get('rows',[])
    units = ('%', '%/年') if spec.get('unit_type') == 'annual_fee_rate' else (spec.get('unit'),)
    if not rows or any(not _number(row.get('value')) or row.get('unit') not in units for row in rows):
        return None
    if spec.get('kind')=='observations':
        labels=spec['categories'];observed={row['label']:row['value'] for row in rows}
        return 'line',dict(labels=labels,values=[observed.get(label) for label in labels],unit=spec['unit'],name=name)
    groups=spec.get('groups',[])
    if len(groups)>1:
        return 'grouped_bar',dict(labels=spec['categories'],series=[
            dict(name=group['name'],values=[rows[index]['value'] for index in group['indices']]) for group in groups],unit=spec['unit'])
    return 'ranked_bar',dict(labels=[row['label'] for row in rows],values=[row['value'] for row in rows],unit=spec['unit'],name=name)


def paired_columns(data):
    """Render a substantial two-period comparison on a common value axis."""
    return dict(data, series=[dict(name=name, values=[pair[index] for pair in data['pairs']])
                              for index, name in enumerate(data['periods'])])


def _select(content,spec):
    translated=_raw(spec)
    if translated is None:
        return spec
    scheme,data=translated;unit=data.get('unit','');ledger=None;reason='explicit_shape'
    draft=content.get('draft',{})
    calculations={row['id']:row for row in draft.get('calculations',[])}
    facts={row['id']:row for row in draft.get('facts',[])}
    refs=spec.get('data_refs',spec.get('refs',[]))
    if scheme in ('line','area','multi_line','small_multiples','indexed_line','rolling','excess_return','drawdown'):
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
    elif scheme=='grouped_bar' and data.get('pairs'):
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
        reason='category_comparison'
    elif scheme=='grouped_bar':
        rows=[dict(label=label,cells=[_shown(row['values'][index]) for row in data['series']],
                   values=[row['values'][index] for row in data['series']]) for index,label in enumerate(data['labels'])]
        ledger=_table(['指标']+[row['name'] for row in data['series']],rows);reason='same_unit_multiple_series'
    elif scheme == 'window_change':
        groups = data['series']
        rows = []
        for index, label in enumerate(data['labels']):
            readings = [group['values'][index] for group in groups]
            rows.append(dict(label=label, values=readings, cells=[_shown(value) for value in readings]))
        ledger = _table(['指标', *(group['name'] for group in groups)], rows)
        reason = 'registered_window_differences'
    elif scheme=='candlestick':
        if len(data['labels']) == 1 and len(spec.get('point_refs', [])) == 1:
            from .draft_visuals import display_observation
            point = spec['point_refs'][0]
            rows = [dict(label=label, cells=[display_observation(facts[point[index]])],
                         values=[data['values'][0][index]], data_refs=[point[index]])
                    for index, label in ((0, '开盘'), (3, '最高'), (2, '最低'), (1, '收盘'))]
            ledger = _table(['价格', unit or '原值'], rows)
            reason = 'single_session_ohlc'
        elif data.get('values'):
            rows=[dict(label=label,cells=[_shown(value) for value in row],values=list(row)) for label,row in zip(data['labels'],data['values'])]
            ledger=_table(['日期','开','收','低','高'],rows);reason='complete_observed_ohlc'
    elif scheme=='range':
        ledger=_table(['对象','下界'+unit,'上界'+unit],[dict(label=row['name'],cells=[_shown(row['min']),_shown(row['max'])],values=[row['min'],row['max']]) for row in data['ranges']]);reason='observed_bounds'
    elif scheme == 'sample_range':
        readings = [data[key] for key in ('minimum', 'maximum', 'latest')]
        ledger = _table(['统计量', '读数'+unit], [dict(label=name, values=[value], cells=[_shown(value)])
                        for name, value in zip(('样本最低', '样本最高', '期末'), readings, strict=True)])
        reason = 'observed_endpoint_and_range'
    elif scheme=='percentile':
        ledger=_table(['对象','历史分位','样本数'],[dict(label=data.get('name','当前值'),cells=[_shown(data['percentile'])+'%',str(data['sampleCount'])],values=[data['percentile'],data['sampleCount']])]);reason='registered_percentile'
    if ledger:
        ledger['unit']=unit
    choice=dict(version=VERSION,scheme=scheme,data=data,reason=reason,
                source_refs=list(dict.fromkeys(refs)),ledger=ledger)
    return dict(spec,auto_visual=choice)


def plot_values_fit_browser(values):
    """A chart must keep distinct absolute observations distinguishable."""
    if type(values) in (int, float):
        return abs(values) <= MAX_BROWSER_INTEGER
    if isinstance(values, dict):
        return all(plot_values_fit_browser(value) for value in values.values())
    if isinstance(values, (list, tuple)):
        return all(plot_values_fit_browser(value) for value in values)
    return True


def plot_precision_supported(spec):
    choice = spec.get('auto_visual') or {}
    if isinstance(choice.get('data'), dict):
        return plot_values_fit_browser(choice['data'])
    return plot_values_fit_browser([spec.get(key) for key in ('values', 'pairs', 'data')])


def report_chart_supported(spec):
    """Require a useful data shape, beyond the number of scalar inputs."""
    try:
        choice = spec.get('auto_visual')
        shape = (choice['scheme'], choice['data']) if choice else _raw(spec)
        return bool(shape and _displayable(*shape))
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def _displayable(scheme, data):
    if scheme not in _REGISTERED_SCHEMES:
        return False
    if scheme == 'evidence':
        nodes, links = data['nodes'], data['links']
        ids = {node['id'] for node in nodes}
        return (data.get('qualitative') is True and 2 <= len(ids) == len(nodes) and bool(links)
                and all(isinstance(node['id'], str) and bool(node['name']) for node in nodes)
                and all(link['source'] in ids and link['target'] in ids and link['source'] != link['target']
                        for link in links))
    if scheme == 'event_timeline':
        events = data['events']
        return (data.get('qualitative') is True
                and all(row['name'] and row['state'] in ('已发生', '已公告', '待观察') for row in events)
                and len({date.fromisoformat(row['date']) for row in events}) >= 2)
    if scheme == 'waterfall':
        steps = data['steps']
        if len(steps) < 2 or not all(isinstance(row['name'], str) and row['name'] and _number(row['value'])
                                    for row in steps):
            return False
        if not all(_number(data[key]) for key in ('start', 'end')):
            return False
        return (isclose(data['start'] + sum(row['value'] for row in steps), data['end'],
                        rel_tol=1e-9, abs_tol=1e-9) and plot_values_fit_browser(data))

    def count(readings):
        if isinstance(readings, (list, tuple)):
            return sum(count(value) for value in readings)
        return int(_number(readings))

    if scheme == 'candlestick':
        values = data['values']
        if not (2 <= len(values) == len(data['labels']) and all(
                len(row) == 4 and all(_number(value) for value in row)
                and row[2] <= min(row[:2]) and row[3] >= max(row[:2]) for row in values)):
            return False
    if scheme == 'grouped_bar' and 'pairs' in data:
        if (len(data['periods']) != 2 or not 3 <= len(data['labels']) == len(data['pairs'])
                or any(len(pair) != 2 or not all(_number(value) for value in pair) for pair in data['pairs'])):
            return False
    if scheme in ('line', 'area', 'multi_line', 'small_multiples', 'indexed_line', 'rolling', 'excess_return', 'drawdown'):
        paths = [path['values'] for path in data.get('series', [data])]
        if not paths or any(len(path) != len(data['labels']) or
                            any(value is not None and not _number(value) for value in path) for path in paths):
            return False
        if not any(count(path) >= 3 and any(_number(a) and _number(b) for a,b in zip(path, path[1:]))
                   for path in paths):
            return False
        if scheme == 'area' and (data.get('measure') != 'scale'
                                 or any(value is not None and value < 0 for value in data['values'])):
            return False
    if scheme in ('scatter', 'scatter_rug'):
        points = [(row['x'], row['y']) for row in data['points']]
        if any(not _number(x) or not _number(y) for x, y in points) or len(set(points)) < 3:
            return False
        if scheme == 'scatter_rug' and (len(points) < 6 or any(len(set(axis)) < 3 for axis in zip(*points))):
            return False
    if scheme == 'sample_range':
        readings = [data.get(key) for key in ('minimum', 'maximum', 'latest')]
    elif 'points' in data:
        readings = [[row.get('x'), row.get('y')] for row in data['points']]
    elif 'ranges' in data:
        readings = [[row.get('min'), row.get('max')] for row in data['ranges']]
    elif 'series' in data:
        readings = [series.get('values') for series in data['series']]
    else:
        readings = data.get('pairs', data.get('matrix', data.get('values', [])))
    return count(readings) >= 3 and plot_values_fit_browser(data)


def argument_charts(candidates, facts):
    """Use another view only for a distinct reading within this argument."""
    selected, purposes, market_fields = [], set(), set()
    for spec in candidates:
        if not report_chart_supported(spec):
            continue
        choice = spec.get('auto_visual')
        scheme = choice['scheme'] if choice else _raw(spec)[0]
        kind, field = spec.get('kind'), spec.get('field')
        objects = tuple(sorted({facts[ref].get('code', '') for ref in spec.get('data_refs', []) if ref in facts}))
        if scheme == 'evidence':
            purpose = ('process', '')
        elif (scheme == 'line' and field in ('close', 'ths_pe_ttm_stock') and len(objects) == 1
              and spec.get('dates')):
            # Price and valuation on the same dates are one reading with
            # two axes. Keep both when a diagram or range also has a role.
            purpose = ('market_window', objects, tuple(spec['dates']))
            if (purpose, field) in market_fields:
                continue
        elif scheme in ('line', 'area', 'multi_line', 'small_multiples', 'heatmap'):
            purpose = ('path', field, objects)
        elif scheme in ('event_timeline', 'sample_range', 'candlestick', 'waterfall'):
            purpose = (scheme, '')
        elif kind == 'peer_financial_changes':
            purpose = ('peer_financial_change', spec.get('comparison_scope'), spec.get('comparison_family'), objects)
        elif kind in ('paired', 'economic_window_comparison'):
            purpose = ('ratio_or_window', spec.get('comparison_scope'), objects)
        else:
            purpose = ('comparison', spec.get('comparison_scope'), objects)
        if purpose in purposes and purpose[0] not in ('process', 'market_window'):
            continue
        if purpose not in purposes and len(purposes) >= 2:
            continue
        selected.append(spec)
        purposes.add(purpose)
        if purpose[0] == 'market_window':
            market_fields.add((purpose, field))
    return selected


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
    from .publication_content import reader_period_label
    data=choice.get('data',{})
    date_labels=data.get('dateLabels',{})
    if not isinstance(date_labels,dict):date_labels={}
    period_columns=set()
    if choice.get('reason') in ('actual_time_series','aligned_matrix','same_object_two_periods','repeated_series_endpoint_comparison') or spec.get('kind') in ('changes','paired','indexed_comparison'):
        period_columns.update((1,2))
    declared=[value for values in (spec.get('periods'),data.get('periods'),data.get('comparison_periods')) if isinstance(values,(list,tuple)) for value in values]
    period_columns.update(index for index,label in enumerate(table['columns']) if index and label in declared)
    columns=''.join('<th scope="col">'+escape(str(reader_period_label(label) if index in period_columns else label))+'</th>' for index,label in enumerate(table['columns']))
    rows=[]
    single_day = choice.get('reason') == 'single_session_ohlc'
    for index,row in enumerate(table['rows']):
        shown=date_labels.get(str(row['label']))
        label=escape(shown if isinstance(shown,str) else str(row['label']))
        references = row.get('data_refs', [])
        observation = ''
        if source_target and single_day and len(references) == 1:
            reference = escape(references[0], quote=True)
            observation = ' data-observation-id="'+reference+'"'
            cells = ('<td><a href="#' + escape(source_target, quote=True) + '" aria-haspopup="dialog"'
                     ' data-chart-observation="' + reference + '" aria-label="查看'
                     + escape(str(row['label']), quote=True) + '原值">' + escape(str(row['cells'][0])) + '</a></td>')
        else:
            cells = ''.join('<td>'+escape(str(value))+'</td>' for value in row['cells'])
        if source_target and not single_day:
            label='<a href="#'+escape(source_target,quote=True)+'" aria-haspopup="dialog">'+label+'</a>'
        rows.append('<tr data-chart-row="'+str(index)+'"'+observation+'><th scope="row">'+label+'</th>'+cells+'</tr>')
    name = str(spec.get('title') or spec.get('name') or '图表') + '原始数据'
    return ('<div class="evidence-layout" data-auto-scheme="'+escape(choice['scheme'])+'" data-ledger-columns="'+str(len(table['columns']))+'">'+chart+
            '<div class="evidence-ledger"><table aria-label="'+escape(name,quote=True)+'"><thead><tr>'+columns+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table></div></div>')
