"""Choose complementary views for the evidence already bound to an argument."""
from collections import defaultdict
from datetime import date
from math import isfinite
import re

from .contracts import ContractError, fingerprint, market_date, timepoint
from .draft_analysis import NAV_FIELDS, _day
from .visual_selection import plot_values_fit_browser, select_figures


_SSE_WEEK = re.compile(
    r'上海证券交易所，沪市市场运行情况例行发布，统计期'
    r'(\d{4})年(\d{1,2})月(\d{1,2})日至(?:(\d{1,2})月)?(\d{1,2})日。')
_SSE_WEEK_ORIGINAL = re.compile(r'公开原件：(https://www\.sse\.com\.cn/aboutus/mediacenter/conference/c/\d+/files/[\da-f]+\.pdf)')
_SSE_WEEK_UNITS = {'指数一周涨跌幅': '%', '股票周成交额': '亿元'}
_SSE_WEEK_CELL = re.compile(r'(?P<label>[一-龥A-Za-z0-9]{2,16}?)(?P<number>[+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,12})(?:\.[0-9]{1,6})?)(?P<unit>%|亿元)')


def etf_turnover_figure(calculations):
    """Compare daily trading size on one scale, using both original cells per point."""
    ratios = sorted((row for row in calculations if row.get('comparison_kind') == 'etf_daily_turnover_multiple'),
                    key=lambda row: row['period'])
    values = [row['value'] for row in ratios]
    if len(ratios) < 5 or len(set(values)) < 3 or not plot_values_fit_browser(values):
        return None
    days = [row['period'] for row in ratios]
    names = {tuple((fact['code'], fact['object_label']) for fact in row['rows']) for row in ratios}
    if len(set(days)) != len(days) or len(names) != 1:
        return None
    ((_, leader), (_, comparator)), = names
    label = leader+'成交额相对'+comparator+'的倍数'
    points = [row['data_refs'] for row in ratios]
    return dict(name=label, field='etf_turnover_multiple', kind='etf_turnover_multiple', type='line',
                scheme='line', unit='倍', horizontal=False, full_width=True, height=180,
                data=dict(labels=days, values=values, unit='倍', numberDigits=0, compactDates=True,
                          endAnnotation=False),
                data_refs=list(dict.fromkeys(ref for point in points for ref in point)), point_refs=points,
                calculation_refs=[row['id'] for row in ratios])


def _sse_week_lines(record, links, cutoff):
    """Read the retained exchange summary only where each cell was cited."""
    from .draft_sources import disclosure_excerpt_hash

    snapshot = record.get('snapshot')
    if record.get('kind') != 'material' or not isinstance(snapshot, dict):
        return
    for index, part in enumerate(snapshot.get('parts', [])):
        if not isinstance(part.get('text'), str):
            continue
        lines = part['text'].splitlines(keepends=True)
        if len(lines) < 3:
            continue
        window = _SSE_WEEK.fullmatch(lines[0].strip())
        original = _SSE_WEEK_ORIGINAL.fullmatch(lines[-1].strip())
        if not window or not original:
            continue
        if record.get('snapshot_hash') != fingerprint(snapshot):
            return
        year, month, first, end_month, last = (int(part) if part else None for part in window.groups())
        try:
            start, end = date(year, month, first), date(year, end_month or month, last)
        except ValueError:
            continue
        if not start < end <= cutoff or (end - start).days > 6:
            continue
        offset = 0
        for raw in lines:
            line = raw.rstrip('\r\n')
            metric, _, readings = line.removesuffix('。').partition('：')
            unit = _SSE_WEEK_UNITS.get(metric)
            cells = {}
            if unit:
                cell_offset = offset + len(metric) + 1
                for text in readings.split('，'):
                    cell = _SSE_WEEK_CELL.fullmatch(text)
                    if not cell or cell['unit'] != unit or cell['label'] in cells:
                        break
                    value_start = cell_offset + cell.start('number')
                    value_end = cell_offset + len(text)
                    key = record['id'], index, value_end
                    link = links.get(key)
                    if (not link or link['original'] != part['text'][value_start:value_end]
                            or not offset <= link['document']['start'] <= value_start):
                        break
                    location = link['document']
                    excerpt = part['text'][location['start']:value_end]
                    if disclosure_excerpt_hash(record['id'], snapshot['name'], index, part.get('locator'),
                                               location['start'], value_end, excerpt) != location.get('excerpt_hash'):
                        break
                    amount = float(cell['number'].replace(',', ''))
                    if not isfinite(amount) or (unit == '亿元' and amount < 0):
                        break
                    cells[cell['label']] = amount
                    cell_offset = value_end + 1
                else:
                    if 2 <= len(cells) <= 8:
                        yield dict(metric=metric, start=start, end=end, cells=cells, line=line,
                                   source_ref=record['id'], url=original[1])
            offset += len(raw)


def weekly_market_figures(content, logic):
    """Compare weekly source excerpts proportionally, without adding observations."""
    if content.get('research_mode') != 'current':
        return []
    analysis = logic.get('analysis', '')
    binding = logic.get('quantity_links', {}).get('analysis', {})
    if not binding or binding.get('text_hash') != fingerprint(analysis):
        return []
    citations = set(logic.get('source_refs', ()))
    links = {}
    for link in binding['links']:
        location = link.get('document') or {}
        first, last = link.get('start'), link.get('end')
        if (link.get('kind') == 'document_excerpt' and location.get('source_ref') in citations
                and all(type(position) is int and position >= 0 for position in
                        (first, last, location.get('part_index'), location.get('start'), location.get('end')))
                and first < last <= len(analysis) and analysis[first:last] == link.get('original')):
            links[location['source_ref'], location['part_index'], location['end']] = link
    if not links:
        return []
    cutoff = date.fromisoformat(market_date(content['cutoff']))
    groups = defaultdict(list)
    sourced = {key[0] for key in links}
    for record in content.get('materials', []):
        if (record['id'] not in sourced or record.get('task_id') != content['task_id']
                or record.get('research_version') != content['research_version']):
            continue
        for row in _sse_week_lines(record, links, cutoff):
            groups[row['metric']].append(row)

    def source_figure(name, scheme, data, rows, context, height):
        return dict(name=name, scheme=scheme, type='bar', horizontal=scheme == 'diverging_bar',
                    data=data, unit=data['unit'], kind='weekly_market_excerpt', context=context,
                    height=height, min_height=height,
                    source_refs=[row['source_ref'] for row in rows],
                    document_groups=[dict(line=row['line'], url=row['url'],
                                         period=f'{row["start"].isoformat()}至{row["end"].isoformat()}') for row in rows])

    result = []
    index_weeks = groups['指数一周涨跌幅']
    if (len(index_weeks) == 1 and len(index_weeks[0]['cells']) >= 3
            and len(set(index_weeks[0]['cells'].values())) > 1):
        row = index_weeks[0]
        labels = sorted(row['cells'], key=row['cells'].get, reverse=True)
        start, end = row['start'], row['end']
        end_label = f'{end.day}日' if start.month == end.month else f'{end.month}月{end.day}日'
        result.append(source_figure('沪市指数周涨跌幅', 'diverging_bar',
            dict(labels=labels, values=[row['cells'][label] for label in labels], unit='%', numberDigits=2),
            index_weeks, f'{start.month}月{start.day}日至{end_label}', 148))
    turnover = sorted(groups['股票周成交额'], key=lambda row: row['start'])
    if len(turnover) == 2 and (turnover[1]['start'] - turnover[0]['start']).days == 7:
        before, after = turnover
        if (before['cells'].keys() == after['cells'].keys() and before['cells'] != after['cells']
                and len(after['cells']) <= 4 and (after['end'] - before['end']).days == 7):
            labels = list(after['cells'])
            periods = [f'{row["start"]:%m.%d}至{row["end"]:%m.%d}' for row in turnover]
            result.append(source_figure('沪市两周成交额', 'grouped_bar',
                dict(labels=labels, series=[dict(name=label, values=[row['cells'][key] for key in labels])
                                          for label, row in zip(periods, turnover, strict=True)],
                     unit='亿元', comparison_periods=periods, unitOnAxis=True),
                turnover, '', 155))
    return select_figures(content, result)


def _token(row):
    return row['code'], row['field'], row['unit'], row.get('series_basis')


def _date_label(day, *, compact=False):
    return day.strftime('%m-%d') if compact else day.isoformat()


def _window_changes(calculations, original_rows, row_key, cutoff):
    """Pivot verified percentage-point results, preserving the two input cells."""
    cells = defaultdict(dict)
    names = {}
    for calculation in calculations:
        if (calculation['method'] != 'raw_percentage_points_v1' or calculation['unit'] != '个百分点'
                or not plot_values_fit_browser(calculation['value'])):
            continue
        before, after = calculation['rows']
        key = _token(before)
        start, end = _day(before['period']), _day(after['period'])
        if (key != _token(after) or before['field'] != 'value' or before['unit'] != '%'
                or not start or not end or not start < end <= cutoff
                or any(row_key(row) not in original_rows for row in (before, after))):
            continue
        window = start, end
        if key in cells[window]:
            return None
        cells[window][key] = calculation
        names[key] = before['label']
    if not 2 <= len(cells) <= 3:
        return None
    windows = sorted(cells)
    keys = list(cells[windows[0]])
    # Keep a single series' interval results in the conclusions and their
    # calculation links. This view is useful for comparing several series.
    if not 2 <= len(keys) <= 6 or any(cells[window].keys() != cells[windows[0]].keys() for window in windows):
        return None
    labels = [names[key] for key in keys]
    if len(set(labels)) != len(labels):
        return None
    compact = len({day.year for window in windows for day in window}) == 1
    period_labels = ['至'.join(_date_label(day, compact=compact) for day in window) for window in windows]
    groups = [[cells[window][key] for key in keys] for window in windows]
    refs = [[calculation['data_refs'] for calculation in group] for group in groups]
    return dict(kind='economic_window_comparison', scheme='window_change', name='各区间读数变化',
        field='economic_change', unit='个百分点', horizontal=False, full_width=True,
        context=f'{windows[0][0].year}年，单位为百分点' if compact else '单位为百分点', plot_scale=True,
        min_height=54 + len(keys) * (18 + 18 * len(windows)),
        data=dict(labels=labels, series=[dict(name=label, values=[row['value'] for row in group])
                  for label, group in zip(period_labels, groups, strict=True)], unit='个百分点'),
        series_point_refs=refs,
        data_refs=list(dict.fromkeys(ref for group in refs for cell in group for ref in cell)),
        calculation_refs=[row['id'] for group in groups for row in group])


def _observed_range(calculations, facts, original_rows, row_key, cutoff):
    """Locate the window endpoint on its verified sample range."""
    for calculation in calculations:
        if calculation['method'] != 'raw_range_v1':
            continue
        lower, upper = calculation['rows']
        key = _token(lower)
        known_market_measure = lower['field'] in {'close', *NAV_FIELDS}
        if (not known_market_measure or key != _token(upper)
                or lower['unit'] in ('', '原值', '接口原值', '原始单位待核对')
                or not plot_values_fit_browser((calculation['minimum'], calculation['maximum']))):
            continue
        start, end = map(_day, (calculation.get('window_start'), calculation.get('window_end')))
        if not start or not end:
            parts = calculation['period'].split('至')
            if len(parts) == 2:
                start, end = map(_day, parts)
        if not start or not end or not start < end <= cutoff:
            continue
        values_by_day = defaultdict(list)
        for row in facts.values():
            day = _day(row['period'])
            if (_token(row) == key and day and start <= day <= end and not row.get('previous_close_for')
                    and type(row['value']) in (int, float) and isfinite(row['value'])):
                values_by_day[day].append(row)
        if (len(values_by_day) < 3 or any(len(rows) != 1 for rows in values_by_day.values())
                or start not in values_by_day or end not in values_by_day):
            continue
        latest = values_by_day[end][0]
        samples = [rows[0] for _, rows in sorted(values_by_day.items())]
        cited_rows = [lower, upper, *samples]
        if (len({row['artifact_id'] for row in cited_rows}) != 1
                or any(row_key(row) not in original_rows for row in cited_rows)):
            continue
        low, high, value = lower['value'], upper['value'], latest['value']
        if (any(not low <= row['value'] <= high or not plot_values_fit_browser(row['value']) for row in samples)
                or {lower['id'], upper['id']} - {row['id'] for row in samples} or low == high):
            continue
        unit = lower['unit']
        dates = dict(start=start.isoformat(), end=end.isoformat())
        return dict(kind='market_observed_range', scheme='sample_range', name=lower['label']+'样本区间与期末读数',
            field=lower['field'], unit=unit, horizontal=False, full_width=True, min_height=150,
            context=f'{dates["start"]}至{dates["end"]}，{len(samples)}个已取得读数', plot_scale=True,
            data=dict(unit=unit, minimum=low, maximum=high, latest=value, window=dates,
                      samples=[dict(date=_day(row['period']).isoformat(), value=row['value']) for row in samples]),
            data_refs=[row['id'] for row in samples],
            series_point_refs=[[[lower['id'], upper['id']]], [[row['id']] for row in samples], [[latest['id']]]],
            calculation_refs=[calculation['id']])
    return None


def _select_macro_path(candidates, facts, original_rows, row_key, cutoff):
    comparison = next((spec for spec in candidates if spec.get('scheme') == 'multi_line'
                       and spec.get('kind') == 'catalogue'), None)
    if comparison is None:
        return candidates
    data, data_refs = comparison['data'], comparison['data_refs']
    labels, series = data['labels'], data['series']
    last = _day(labels[-1])
    if (not 2 <= len(series) <= 4 or len(labels) < 4 or not last or last > cutoff
            or not plot_values_fit_browser(data)
            or any(all(value is None for value in row['values']) for row in series)
            or any(ref not in facts or row_key(facts[ref]) not in original_rows for ref in data_refs)):
        return candidates
    matrix = next((spec for spec in candidates if spec.get('scheme') == 'heatmap'
                   and spec.get('data_refs') == data_refs), None)
    if matrix is None:
        return candidates
    data = dict(data, compactDates=len({day[:4] for day in labels}) == 1)
    count = len(series)
    facets = dict(comparison, name=comparison['name'].replace('走势对照', '同尺度走势'),
        scheme='small_multiples', data=data, full_width=True, min_height=25+65*count, plot_scale=True,
        context=f'{labels[0]}至{labels[-1]}，各行使用相同纵轴尺度')
    facets = select_figures({}, [facets])[0]
    return [facets if spec is comparison else spec for spec in candidates if spec is not matrix]


def _economic_basis(row):
    labels = row['label'].replace('：', ':').split(':')
    return (labels[0], labels[-1]) if len(labels) > 1 else None


def align_argument_paths(content, candidates, facts, sources, original_rows, row_key):
    """Compare only equally dated, same-basis paths from the same original."""
    groups = defaultdict(list)
    for index, spec in enumerate(candidates):
        chart = spec.get('auto_visual', {})
        if chart.get('scheme') != 'line' or spec.get('field') != 'value':
            continue
        refs, data = spec['data_refs'], chart['data']
        days, values = data['labels'], data['values']
        if (len(days) < 3 or len(refs) != len(days)
                or any(not plot_values_fit_browser(value) or value is None for value in values)):
            continue
        rows = [facts.get(ref) for ref in refs]
        if any(row is None or row_key(row) not in original_rows for row in rows):
            continue
        row = rows[0]
        origin = row['artifact_id']
        basis = _economic_basis(row)
        if (not basis or not row['unit'] or sources[origin].get('operation') != 'edb_service'
                or any(item['artifact_id'] != origin or _token(item) != _token(row) for item in rows)):
            continue
        groups[(origin, basis, row['unit'], tuple(days))].append(index)

    replacements, removed = {}, set()
    for (origin, (family, _), unit, dates), indices in groups.items():
        if not 2 <= len(indices) <= 6:
            continue
        items = [candidates[index] for index in indices]
        labels = [item['name'] for item in items]
        paths = [item['auto_visual']['data']['values'] for item in items]
        all_refs = [ref for item in items for ref in item['data_refs']]
        if len(set(labels)) != len(labels) or len(set(all_refs)) != len(all_refs):
            continue
        scheme = 'multi_line'
        data = dict(labels=list(dates), unit=unit, compactDates=len({day[:4] for day in dates}) == 1)
        if len(items) >= 5 and len(dates) >= 4:
            scheme = 'heatmap'
            data.update(rows=labels, matrix=paths)
        else:
            if len(items) >= 3 and len(dates) >= 4:
                scheme = 'small_multiples'
            data['series'] = [dict(name=name, values=path) for name, path in zip(labels, paths, strict=True)]
        combined = dict(name=family + '走势对照', field='value', kind='argument_paths', scheme=scheme,
            unit=unit, data=data, horizontal=False, height=270, full_width=True,
            context=f'{dates[0]}至{dates[-1]}',
            data_refs=all_refs, series_point_refs=[[[ref] for ref in item['data_refs']] for item in items])
        contexts = [item['plot_context'] for item in items if item.get('plot_context')]
        if contexts:
            combined['plot_context'] = dict(kind='observed_window', source_ref=origin,
                start=dates[0], end=dates[-1],
                anchor_refs=list(dict.fromkeys(ref for item in contexts for ref in item['anchor_refs'])),
                context_data_refs=list(dict.fromkeys(ref for item in contexts for ref in item['context_data_refs'])))
        replacements[indices[0]] = select_figures(content, [combined])[0]
        removed.update(indices[1:])
    return [replacements.get(index, spec) for index, spec in enumerate(candidates) if index not in removed]


def same_period_figure(cited_facts, sources, original_rows, row_key):
    """Select an actual cross-section with a single measurement and period."""
    from .draft_visuals import UNKNOWN_FINANCIAL_UNITS

    groups = defaultdict(list)
    for row in cited_facts.values():
        day, field, unit = _day(row['period']), row['field'], row['unit']
        if (not day or unit in UNKNOWN_FINANCIAL_UNITS or type(row['value']) not in (int, float)
                or not plot_values_fit_browser(row['value']) or row.get('previous_close_for')):
            continue
        origin = sources[row['artifact_id']]
        if field == 'value' and origin.get('operation') == 'edb_service':
            basis = _economic_basis(row)
            if not basis or row_key(row) not in original_rows:
                continue
        elif row.get('axis_role') == 'cross_section':
            basis = row.get('series_basis')
            if not basis:
                continue
        else:
            continue
        groups[(origin['id'], field, unit, day, basis)].append(row)
    eligible = [rows for rows in groups.values() if len(rows) >= 3
                and len({row['code'] for row in rows}) == len(rows)]
    if not eligible:
        return None
    rows = max(eligible, key=len)
    dates = sorted(rows, key=lambda row: row['value'], reverse=True)[:15]
    macro = rows[0]['field'] == 'value'
    unit = rows[0]['unit']
    values = [row['value'] for row in dates]
    labels = [row['label'] if macro else row.get('object_label') or row['code'] for row in dates]
    name = '当期读数对照' if macro else rows[0]['label']
    scheme = 'diverging_bar' if min(values) < 0 < max(values) else 'lollipop'
    return dict(name=name, kind='argument_cross_section', field=rows[0]['field'], unit=unit,
        horizontal=True, context=_day(rows[0]['period']).isoformat(), height=95 + 27 * len(dates),
        scheme=scheme, data=dict(labels=labels, values=values, unit=unit),
        data_refs=[row['id'] for row in dates], point_refs=[[row['id']] for row in dates])


def financial_input_figure(cited_facts, registered, sources):
    """Show a comparable two-period matrix when the argument has only raw inputs."""
    from .draft_visuals import BALANCE, FIELDS, UNKNOWN_FINANCIAL_UNITS, analysis_field

    rows = {row['id']: row for calc in registered for row in calc['rows'] if row['id'] in cited_facts}
    groups = defaultdict(lambda: defaultdict(list))
    for row in rows.values():
        field, day, unit = analysis_field(row), _day(row['period']), row['unit']
        source = sources[row['artifact_id']]
        if field not in FIELDS or not day or unit in UNKNOWN_FINANCIAL_UNITS or not row.get('series_basis'):
            continue
        key = (row['code'], unit, row['series_basis'], source['source'], source['operation'], field in BALANCE)
        groups[key][field].append((day, row))

    cohorts = defaultdict(dict)
    for key, fields in groups.items():
        for field, entries in fields.items():
            if len(entries) != 2 or entries[0][0] == entries[1][0]:
                continue
            (before, left), (after, right) = sorted(entries, key=lambda entry: entry[0])
            cohorts[(key, before, after)][field] = (left, right)
    if not cohorts:
        return None
    (key, before, after), metrics = max(cohorts.items(), key=lambda entry: len(entry[1]))
    if len(metrics) < 3:
        return None
    fields = [field for field in FIELDS if field in metrics][:4]
    observations = list(zip(*(metrics[field] for field in fields), strict=True))
    dates = [before.isoformat(), after.isoformat()]
    refs = [row['id'] for group in observations for row in group]
    return dict(name='两期财务指标对照', kind='financial_inputs', field='financial_inputs',
        unit=key[1], horizontal=False, scheme='grouped_bar',
        context=key[0], data_refs=refs,
        series_point_refs=[[[row['id']] for row in group] for group in observations],
        data=dict(labels=[FIELDS[field] for field in fields], unit=key[1], categoricalComparison=True,
                  series=[dict(name=day, values=[row['value'] for row in group])
                          for day, group in zip(dates, observations, strict=True)]))


def stock_growth_figure(calculations, facts):
    """Show a coherent set of comparable growth rates on one axis."""
    from .stock_quality import CELL_KIND, CORE_FIELDS, LABELS

    by_field = {}
    for row in calculations:
        refs = row['data_refs']
        if row['method'] != 'raw_change_v1' or len(refs) != 2 or any(ref not in facts for ref in refs):
            continue
        left, right = (facts[ref] for ref in refs)
        if (left['field'] == right['field'] in CORE_FIELDS
                and left['locator'].get('kind') == right['locator'].get('kind') == CELL_KIND):
            by_field[right['field']] = row
    if by_field.keys() != set(CORE_FIELDS) or len({(row['code'], row['period']) for row in by_field.values()}) != 1:
        return None
    ordered = [by_field[field] for field in CORE_FIELDS]
    values = [row['value'] for row in ordered]
    if len(set(values)) < 3 or not plot_values_fit_browser(values):
        return None
    point_refs = [row['data_refs'] for row in ordered]
    return dict(name='营收、利润与经营现金流同比', kind='stock_growth', field='stock_growth', type='bar',
        scheme='diverging_bar' if min(values) < 0 < max(values) else 'ranked_bar', horizontal=True,
        full_width=True, height=162, unit='%', context=ordered[0]['period'][:4]+'年上半年',
        data=dict(labels=[LABELS[field] for field in CORE_FIELDS], values=values, unit='%', numberDigits=1),
        data_refs=[ref for pair in point_refs for ref in pair], point_refs=point_refs,
        calculation_refs=[row['id'] for row in ordered])


def cash_bridge_figure(calculations, facts):
    from decimal import Decimal
    from .financial_data import CASH_BRIDGE_FIELDS, UNIT_FACTORS

    selected = [calc for calc in calculations if calc.get('comparison_kind') == 'cash_bridge_change'
                and len(calc['data_refs']) == 2 and set(calc['data_refs']) <= facts.keys()]
    if len(selected) != len(CASH_BRIDGE_FIELDS) or len({(row['code'], row['period'], row['unit']) for row in selected}) != 1:
        return None
    by_field = {facts[calc['data_refs'][-1]]['field']: calc for calc in selected}
    if set(by_field) != CASH_BRIDGE_FIELDS.keys():
        return None
    scale = UNIT_FACTORS[selected[0]['unit']] / UNIT_FACTORS['亿元']
    entries = sorted(((Decimal(calc['exact'])*scale, CASH_BRIDGE_FIELDS[field][1], calc['data_refs'], calc['id'])
                      for field, calc in by_field.items()), key=lambda entry: entry[0])
    if len({entry[0] for entry in entries}) < 2:
        return None
    return dict(name='营运资金对现金流变化的贡献', kind='cash_bridge', field='cash_bridge', unit='亿元',
                scheme='diverging_bar' if entries[0][0] < 0 < entries[-1][0] else 'ranked_bar',
                type='bar', horizontal=True, context=selected[0]['period'][:4]+'年上半年同比',
                data=dict(labels=[entry[1] for entry in entries], values=[float(entry[0]) for entry in entries],
                          unit='亿元', numberDigits=1), data_refs=[ref for entry in entries for ref in entry[2]],
                point_refs=[entry[2] for entry in entries], calculation_refs=[entry[3] for entry in entries])


def _peer_figure_inputs(cohort, fields, available, facts):
    if not isinstance(cohort, list) or not 2 <= len(cohort) <= 6:
        return None
    labels, series_rows = [], {field: [] for field in fields}
    for company in cohort:
        if not isinstance(company, dict) or not company.get('name') or not isinstance(company.get('calculations'), dict):
            return None
        labels.append(company['name'])
        for field in fields:
            row = available.get(company['calculations'].get(field))
            if (not row or row['code'] != company['code'] or len(row['data_refs']) != 2
                    or not set(row['data_refs']) <= facts.keys()
                    or any(facts[ref]['field'] != field for ref in row['data_refs'])):
                return None
            series_rows[field].append(row)
    combinations = [row for rows in series_rows.values() for row in rows]
    if len(set(labels)) != len(labels) or len({row['period'] for row in combinations}) != 1:
        return None
    return labels, series_rows, combinations


def peer_growth_figure(cohort, calculations, facts):
    """Use the same period and recorded metrics for every pair of bars."""
    from .financial_sources import PEER_FINANCIAL_FIELDS

    available = {row['id']: row for row in calculations if row['method'] == 'raw_change_v1'
                 and row.get('unit') == '%' and not row.get('conditional_equal_scale')}
    inputs = _peer_figure_inputs(cohort, PEER_FINANCIAL_FIELDS, available, facts)
    if not inputs:
        return None
    labels, series_rows, combinations = inputs
    series = [dict(name=name, values=[row['value'] for row in rows])
              for name, rows in zip(('营业收入', '归母净利润'), series_rows.values(), strict=True)]
    return dict(name='样本公司营收与归母净利润同比', kind='peer_growth', field='peer_growth',
                unit='%', type='bar', scheme='grouped_bar', horizontal=False, full_width=True, height=210,
                context=combinations[0]['period'][:4]+'年上半年同比',
                data=dict(labels=labels, series=series, unit='%', unitOnAxis=True, numberDigits=1),
                data_refs=list(dict.fromkeys(ref for row in combinations for ref in row['data_refs'])),
                series_point_refs=[[row['data_refs'] for row in rows] for rows in series_rows.values()],
                calculation_refs=[row['id'] for row in combinations])


def peer_revenue_figure(cohort, calculations, facts):
    """Compare the four independently reported income lines across issuers."""
    from decimal import Decimal

    from .draft_analysis import PEER_REVENUE_CHANGE
    from .financial_data import UNIT_FACTORS
    from .financial_sources import PEER_REVENUE_COMPONENTS

    available = {row['id']: row for row in calculations if row.get('comparison_kind') == PEER_REVENUE_CHANGE
                 and row['unit'].removeprefix('人民币') in UNIT_FACTORS}
    inputs = _peer_figure_inputs(cohort, PEER_REVENUE_COMPONENTS, available, facts)
    if not inputs:
        return None
    companies, dimensions, combinations = inputs
    groups = list(zip(*dimensions.values(), strict=True))
    series = []
    for company, group in zip(companies, groups, strict=True):
        values = [float(Decimal(row['exact'])*UNIT_FACTORS[row['unit'].removeprefix('人民币')]/100000000)
                  for row in group]
        series.append(dict(name=company, values=values))
    return dict(name='合并利润表收入项目同比变动', kind='peer_revenue', field='peer_revenue',
                unit='亿元', type='bar', scheme='grouped_bar', horizontal=False, full_width=True, height=238,
                context=combinations[0]['period'][:4]+'年上半年较上年同期',
                data=dict(labels=['手续费及佣金', '利息净收入', '投资收益', '公允价值变动'], series=series,
                          unit='亿元', unitOnAxis=True, numberDigits=1),
                data_refs=list(dict.fromkeys(ref for row in combinations for ref in row['data_refs'])),
                series_point_refs=[[row['data_refs'] for row in group] for group in groups],
                calculation_refs=[row['id'] for row in combinations])


def peer_operating_figure(cohort, calculations, facts):
    """Keep one comparison across issuers, using the richest common bridge."""
    from decimal import Decimal

    from .draft_analysis import PEER_NET_AFTER_GROSS, peer_gross_profit_bridge
    from .financial_sources import PEER_FINANCIAL_FIELDS, PEER_OPERATING_FIELDS

    fields = PEER_FINANCIAL_FIELDS[0], PEER_OPERATING_FIELDS[0]
    available = {row['id']: row for row in calculations if row['method'] in ('raw_change_v1', 'raw_difference_v1')}
    inputs = _peer_figure_inputs(cohort, fields, available, facts)
    if not inputs:
        return None
    companies, rows, combined = inputs
    pairs = list(zip(*rows.values(), strict=True))
    effects = [peer_gross_profit_bridge(revenue, cost, facts) for revenue, cost in pairs]
    if not all(effects):
        return None
    series = [dict(name=name, values=[float(row[key]/100000000) for row in effects])
              for name, key in (('收入贡献', 'revenue_effect'), ('毛利率贡献', 'margin_effect'))]
    contributions = [list(dict.fromkeys(ref for calculation in pair for ref in calculation['data_refs'])) for pair in pairs]
    point_refs = [contributions, contributions]
    title = '收入和毛利率对毛利同比变化的贡献'
    context = combined[0]['period'][:4]+'年上半年较上年同期'
    residuals = {row['id']: row for row in calculations if row['method'] == PEER_NET_AFTER_GROSS}
    net_steps = []
    for company, (revenue, cost) in zip(cohort, pairs, strict=True):
        item = residuals.get(company['calculations'].get(PEER_NET_AFTER_GROSS))
        if not item or item['code'] != revenue['code'] or item['period'] != revenue['period']:
            break
        refs = item['data_refs']
        if len(refs) != 6 or not set(refs) <= facts.keys() or refs[::3] != revenue['data_refs'] or refs[1::3] != cost['data_refs']:
            break
        net_steps.append(item)
    if len(net_steps) == len(companies):
        series.append(dict(name='毛利以下净变动', values=[float(Decimal(row['exact'])/100000000) for row in net_steps]))
        point_refs.append([row['data_refs'] for row in net_steps])
        combined.extend(net_steps)
        title = '归母净利润同比变动拆解'
        context += '，三项合计为归母净利润同比变动'
    if sum(value != 0 for item in series for value in item['values']) < 3:
        return None
    return dict(name=title, kind='peer_operating_bridge', field='peer_operating_bridge',
                unit='亿元', type='bar', scheme='grouped_bar', horizontal=False, full_width=True, height=218,
                context=context,
                data=dict(labels=companies, series=series, unit='亿元', unitOnAxis=True, numberDigits=1),
                data_refs=list(dict.fromkeys(ref for series_refs in point_refs for group in series_refs for ref in group)),
                series_point_refs=point_refs, calculation_refs=[row['id'] for row in combined])


def bank_profit_figure(cohort, calculations, facts):
    """Compare income contributions and credit impairment on the same scale."""
    from .draft_analysis import bank_profit_bridge
    from .financial_sources import BANK_CREDIT_IMPAIRMENT, BANK_INTEREST_INCOME

    if not isinstance(cohort, list) or not 2 <= len(cohort) <= 5:
        return None
    bridges = bank_profit_bridge(facts.values(), calculations)
    names, rows = [], []
    for company in cohort:
        if not isinstance(company, dict) or not company.get('name'):
            return None
        row = bridges.get(company.get('code'))
        if not row or {key: item['id'] for key, item in row['calculations'].items()} != company.get('calculations'):
            return None
        names.append(company['name'])
        rows.append(row)
    if len(names) != len(set(names)) or len({(row['before'], row['after']) for row in rows}) != 1:
        return None
    groups = [
        ('净利息收入', 'net_interest', (BANK_INTEREST_INCOME,)),
        ('非利息净收入', 'noninterest', ('ths_revenue_stock', BANK_INTEREST_INCOME)),
        ('信用减值影响', 'credit_impact', (BANK_CREDIT_IMPAIRMENT,)),
    ]
    series = [dict(name=name, values=[float(row[key]/100000000) for row in rows]) for name, key, _ in groups]
    if sum(value != 0 for item in series for value in item['values']) < 3:
        return None
    points = [[list(dict.fromkeys(ref for field in fields for ref in row['calculations'][field]['data_refs']))
               for row in rows] for _, _, fields in groups]
    included = [item for row in rows for item in row['calculations'].values()]
    return dict(name='营收构成与信用减值的同比变化', kind='bank_profit_bridge', field='bank_profit_bridge',
                unit='亿元', type='bar', scheme='grouped_bar', horizontal=False, full_width=True, height=220,
                context=rows[0]['after'][:4]+'年上半年较上年同期',
                data=dict(labels=names, series=series, unit='亿元', unitOnAxis=True, numberDigits=1),
                data_refs=list(dict.fromkeys(ref for item in included for ref in item['data_refs'])),
                calculation_refs=[item['id'] for item in included], series_point_refs=points)


def stock_profit_figure(content, calculations, facts):
    from .draft_analysis import stock_profit_attribution
    from .financial_data import UNIT_FACTORS
    from .planning import question_driven_arguments
    from .stock_quality import (ASSET_LOSS, COST, INCOME_TAX, MINORITY_PROFIT, PRETAX, PROFIT, REVENUE,
                                frozen_pairs)

    pairs = frozen_pairs(content, facts.values())
    bridge = stock_profit_attribution(pairs)
    if not bridge:
        return None
    if question_driven_arguments(content) and sum(bool(value) for value in bridge['effects'].values()) < 3:
        return None
    if bridge['prior'] + sum(bridge['effects'].values()) != bridge['current']:
        return None

    parts = (
        ('毛利变化', '毛利', (REVENUE, COST)),
        ('资产减值', '资产减值', (ASSET_LOSS,)),
        ('其他税前项目', '其他税前', (PRETAX, REVENUE, COST, ASSET_LOSS)),
        ('所得税费用', '所得税', (INCOME_TAX,)),
        ('少数股东损益归属', '少数股东', (MINORITY_PROFIT,)),
    )
    factor = UNIT_FACTORS[bridge['unit'].removeprefix('人民币')] / UNIT_FACTORS['亿元']
    before, after = [row['period'][:4]+'H1' for row in pairs[PROFIT]]
    view = dict(start=float(bridge['prior']*factor), end=float(bridge['current']*factor),
                startName=before, endName=after, unit='亿元', unitOnAxis=True, numberDigits=1,
                steps=[dict(name=label, value=float(bridge['effects'][effect]*factor)) for effect, label, _ in parts])
    points = [[pairs[PROFIT][0]['id']],
              *[[row['id'] for field in fields for row in pairs[field]] for _, _, fields in parts],
              [pairs[PROFIT][1]['id']]]
    refs = set(bridge['data_refs'])
    return dict(name='归母净利润变动来源', kind='stock_profit_bridge', field='stock_profit_bridge',
                scheme='waterfall', type='bar', horizontal=False, full_width=True, unit='亿元', height=168,
                data=view, data_refs=bridge['data_refs'], point_refs=points,
                calculation_refs=[row['id'] for row in calculations if row['method'] == 'raw_difference_v1'
                                  and set(row['data_refs']) <= refs])


def segment_revenue_figure(calculations, facts):
    from .draft_analysis import segment_revenue_breakdown
    from .financial_data import UNIT_FACTORS

    view = segment_revenue_breakdown(calculations, facts)
    if not view or not 3 <= len(view['parts']) <= 8 or sum(bool(part['change']) for part in view['parts']) < 3:
        return None
    factor = UNIT_FACTORS[view['unit'].removeprefix('人民币')] / UNIT_FACTORS['亿元']
    parts = sorted(view['parts'], key=lambda item: abs(item['change']), reverse=True)
    before = view['before_period'][:4]+'年上半年'
    after = view['after_period'][:4]+'年上半年'
    refs = list(dict.fromkeys(ref for part in parts for ref in part['refs']))
    return dict(name='各分部对营业收入变化的贡献', kind='segment_revenue_bridge', field='segment_revenue',
        scheme='waterfall', type='bar', horizontal=False, full_width=True, unit='亿元', height=180,
        context='上年同期采用重述口径' if view['restated'] else before+'至'+after,
        data=dict(start=float(view['before']*factor), end=float(view['after']*factor), startName=before, endName=after,
            steps=[dict(name=part['label'], value=float(part['change']*factor)) for part in parts],
            unit='亿元', numberDigits=1), data_refs=refs,
        point_refs=[[part['refs'][0] for part in parts], *[part['refs'] for part in parts],
                    [part['refs'][1] for part in parts]],
        calculation_refs=[part['calculation']['id'] for part in parts])


def complement_argument(content, candidates, *, calculations, cited_facts, original_rows, row_key, profile):
    """Path, measured change, then sample context. Keep the existing fallback."""
    try:
        cutoff = date.fromisoformat(market_date(content['cutoff']))
    except (ContractError, KeyError, ValueError):
        return candidates
    change = _window_changes(calculations, original_rows, row_key, cutoff)
    if change or profile == 'standard':
        candidates = _select_macro_path(candidates, cited_facts, original_rows, row_key, cutoff)
    if change:
        return candidates + select_figures(content, [change])
    # A sample range answers a narrower question than the trend. Include it
    # only when this argument actually cites the registered range result.
    observed_range = _observed_range(calculations, cited_facts, original_rows, row_key, cutoff)
    if observed_range:
        return candidates + select_figures(content, [observed_range])
    return candidates


def announcement_timeline(content, source_refs, references=()):
    """Use confirmed publication times of the argument's cited official originals."""
    from .source_catalogue import disclosure_fields

    owned = {record['id']: record for record in content.get('source_records', [])
             if record.get('source') == 'official' and record.get('operation') == 'release_original'
             and record.get('task_id') == content['task_id'] and record.get('research_version') == content['research_version']}
    wanted, cited = set(source_refs), set(references)
    events = {}
    for evidence in content.get('evidence', []):
        ref = evidence.get('artifact_id')
        record = owned.get(ref)
        if (record is None or ref not in wanted and evidence.get('id') not in cited
                or evidence.get('source_check') != '通过' or evidence.get('applicability_check') != '通过'
                or evidence.get('snapshot_hash') != fingerprint(record.get('snapshot'))):
            continue
        title = disclosure_fields(record).get('title')
        try:
            if not title or timepoint(evidence.get('published_at')) > timepoint(content['cutoff']):
                continue
            day = market_date(evidence['published_at'])
        except (ContractError, ValueError, TypeError):
            continue
        events[ref] = dict(date=day, name=title, state='已公告', ref=evidence['id'])
    if not 2 <= len(events) <= 6 or len({event['date'] for event in events.values()}) < 2:
        return None
    events = sorted(events.items(), key=lambda pair: (pair[1]['date'], pair[0]))
    return dict(kind='timeline', name='公告时间线', field='publication', unit='', horizontal=False,
        height=230, full_width=True, coverage_kind='mechanism',
        scheme='event_timeline', data=dict(qualitative=True, events=[item for _, item in events]),
        source_refs=[ref for ref, _ in events], data_refs=[])


def industry_chain_figure(cohort, calculations, facts):
    from .industry_chain import selected_cells

    if not isinstance(cohort, dict) or not set(cohort.get('data_refs', ())) <= facts.keys():
        return None
    cells = selected_cells(facts[ref] for ref in cohort['data_refs'])
    if cohort['kind'] == 'battery_mix':
        rows = [row for (_, field), row in cells.items() if field in
                ('battery_power', 'battery_storage', 'battery_consumer') and row['unit'] == 'GWh']
        if len(rows) != 3 or len({row['period'] for row in rows}) != 1:
            return None
        rows.sort(key=lambda row: row['value'], reverse=True)
        refs = [row['id'] for row in rows]
        return dict(name='锂电池产量分用途', scheme='ranked_bar', kind='industry_chain', field='battery_mix',
            full_width=True, horizontal=True, height=156, unit='GWh', data_refs=refs,
            point_refs=[[ref] for ref in refs],
            data=dict(labels=[row['label'].removesuffix('产量') for row in rows],
                      values=[row['value'] for row in rows], unit='GWh', unitOnAxis=True, numberDigits=0))
    if cohort['kind'] != 'vehicle_channels':
        return None
    domestic = sorted((row for row in calculations if row['method'] == 'raw_difference_v1'
                       and row['label'] == '新能源汽车境内销量估算'), key=lambda row: row['period'])
    if len(domestic) != 2:
        return None
    years = [row['period'][:4] for row in domestic]
    exports = [cells.get((year, 'vehicle_exports')) for year in years]
    if not all(exports) or any(row['id'] not in calc['data_refs'] or calc['unit'] != '万辆'
                               for row, calc in zip(exports, domestic, strict=True)):
        return None
    export_refs = [[row['id']] for row in exports]
    domestic_refs = [row['data_refs'] for row in domestic]
    return dict(name='新能源汽车境内销量与出口', scheme='grouped_bar', kind='industry_chain',
        field='vehicle_channels', full_width=True, horizontal=False, height=170, unit='万辆',
        context='境内销量按全国销量扣减出口量估算',
        data_refs=list(dict.fromkeys(ref for group in domestic_refs for ref in group)),
        series_point_refs=[domestic_refs, export_refs], calculation_refs=[row['id'] for row in domestic],
        data=dict(labels=[year + '年上半年' for year in years], unit='万辆', unitOnAxis=True, numberDigits=1,
                  series=[dict(name='境内销量估算', values=[row['value'] for row in domestic]),
                          dict(name='出口量', values=[row['value'] for row in exports])]))


def macro_monthly_figure(fields, facts):
    from .monthly_statistics import FIELDS, selected_series

    if fields not in (['cpi', 'core'], ['retail', 'ex_auto']):
        return None
    observed = selected_series(facts.values())
    groups = [observed.get(key, {}) for key in fields]
    periods = sorted(groups[0])
    if len(periods) < 3 or groups[0].keys() != groups[1].keys():
        return None
    period_numbers = [int(period[:4]) * 12 + int(period[-2:]) for period in periods]
    if any(b-a != 1 for a,b in zip(period_numbers, period_numbers[1:])):
        return None
    refs = [[[group[period]['id']] for period in periods] for group in groups]
    return dict(name='CPI与核心CPI' if fields[0] == 'cpi' else '消费品零售同比', scheme='multi_line',
        kind='macro_monthly', field='_'.join(fields), full_width=True, horizontal=False, height=142, unit='%',
        data_refs=[ref for group in refs for month in group for ref in month], series_point_refs=refs,
        data=dict(labels=periods, unit='%', unitOnAxis=True, numberDigits=1,
            series=[dict(name=FIELDS[field][0], values=[group[period]['value'] for period in periods])
                    for field,group in zip(fields,groups,strict=True)]))


def retail_change_figure(calculations, facts):
    from .draft_analysis import RETAIL_CONTRIBUTION
    from .monthly_statistics import selected_series
    from .publication_content import reader_period_label

    contributions = {(row['component'], row['period']): row for row in calculations
                     if row.get('method') == RETAIL_CONTRIBUTION and set(row['data_refs']) <= facts.keys()}
    periods = sorted({period for _, period in contributions})
    if len(periods) != 2:
        return None
    rows = selected_series(facts.values()).get('retail', {})
    if not all(period in rows for period in periods):
        return None
    labels = ('剔除汽车', '汽车')
    pairs = [[contributions.get((key, period)) for period in periods] for key in labels]
    if not all(all(pair) for pair in pairs):
        return None
    changes = [pair[-1]['value'] - pair[0]['value'] for pair in pairs]
    if min(map(abs, changes)) < 0.05:
        return None
    first, last = [rows[period] for period in periods]
    middle = [list(dict.fromkeys(ref for row in pair for ref in row['data_refs'])) for pair in pairs]
    points = [[first['id']], *middle, [last['id']]]
    return dict(name='社零同比变化的贡献拆分', scheme='waterfall', kind='macro_retail_change',
        field='retail_change', full_width=True, horizontal=False, height=151, unit='%',
        data_refs=list(dict.fromkeys(ref for point in points for ref in point)), point_refs=points,
        calculation_refs=[row['id'] for pair in pairs for row in pair],
        data=dict(start=first['value'], end=last['value'], startName=reader_period_label(first['period']),
                  endName=reader_period_label(last['period']), unit='%', changeUnit='个百分点', numberDigits=1,
                  steps=[dict(name=name, value=change) for name, change in zip(('非汽车消费', '汽车'), changes, strict=True)]))
