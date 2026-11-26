"""Summarize actual observations when a first report has no written findings."""

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from itertools import islice, zip_longest
import re

from .contracts import fingerprint, market_date
from .draft_analysis import BALANCE, FINANCIAL, NAV_FIELDS, _value_key, execute, net_margin_components
from .draft_filing_amounts import CONSOLIDATED_METRICS_VERSION, is_filing_amount
from .draft_sources import (FINANCIAL_PERIOD_GAP_POLICY, FINANCIAL_PERIOD_GAP_VERSION,
                            LABELS, _sequence_day, economic_frequencies, retained_financial_statements)
from .economic_sources import (
    DRAFT_PERIOD_POLICY, DRAFT_PERIOD_VERSION, _FREQUENCY_MONTHS, _economic_query_links, _period_label, _period_number,
    economic_comparison_windows, projects_stated_periods,
)
from .market_series import SERIES_LABELS
from .official_sources import (direct_fomc_rate_request, fomc_rate_dates, fomc_rate_request, fomc_rate_statement,
                               fomc_statement_release_clock, research_fomc_rate_request)
from .publication_content import observation_presentation, requested_financial_statement
from .subject_scope import object_mentions, scope_question, subject_key
from .tinyshare_sources import analysis_field


FINANCIAL_ORDER = ('ths_revenue_stock', 'ths_np_atoopc_stock', 'ths_np_stock',
                   'ths_ncf_from_oa_stock', 'ths_operating_cost_stock')
FINANCIAL_LABELS = {'ths_ncf_from_oa_stock': '经营现金流净额', 'ths_np_atoopc_stock': '归母净利润'}
SERIES_OPERATIONS = {'cmd_history_quotation', 'date_sequence', 'daily', 'fund_daily', 'etf_pcf'}
SERIES_FIELDS = {'open', 'high', 'low', 'close', *NAV_FIELDS, *SERIES_LABELS}
OPERATING_FIELDS = {'ths_revenue_stock', 'ths_operating_cost_stock', 'ths_np_stock', 'ths_np_atoopc_stock'}
CASH_FLOW = 'ths_ncf_from_oa_stock'
PROFITABILITY = {'raw_margin_v1': '收入成本差占比', 'raw_percent_v1': '报表净利润率'}


def _direction(value):
    return '上升' if value > 0 else '下降' if value < 0 else '持平'


def _section(title, rows, calculations, paragraphs=(), risk=''):
    refs = dict.fromkeys([*(row['id'] for row in rows),
                          *(ref for calc in calculations for ref in calc['data_refs'])])
    return dict(title=title, analysis='\n'.join(paragraphs), implication='', risk=risk, status='待验证',
                data_refs=list(refs), calculation_refs=[calc['id'] for calc in calculations],
                source_refs=[], knowledge_refs=[])


def _with_unit_condition(text, calculations):
    prefix = '若所用原值单位一致，' if any(calc['conditional_equal_scale'] for calc in calculations) else ''
    return prefix + text


def _annual_window(calc, rows):
    """Key comparisons on the actual endpoints and report basis."""
    prior, current = (rows[ref] for ref in calc['data_refs'])
    return _sequence_day(current['period']), _sequence_day(prior['period']), current['series_basis'], current['unit']


def _financial_scope_names(task, rows):
    """Hide prose for distinct scopes that cannot be named unambiguously."""
    from .publication_content import financial_scope_labels

    content = dict(task_id=task['id'], research_version=task['version'], source_records=task['source_records'])
    labels = financial_scope_labels(content, rows.values())
    scopes = defaultdict(lambda: defaultdict(set))
    for row in rows.values():
        scope = row['series_basis'], row['unit']
        scopes[row['code']][scope].add(labels.get(row['id'], ''))
    result = {}
    for code, bases in scopes.items():
        names = {basis: next(iter(choices)) for basis, choices in bases.items()
                 if len(choices) == 1 and '' not in choices}
        if len(bases) == 1:
            result[code, *next(iter(bases))] = ''
        else:
            for basis in bases:
                name = names.get(basis)
                result[code, *basis] = name if list(names.values()).count(name) == 1 else None
    return result


def _operating_paragraphs(calculations, rows, scope_names, show_code):
    by_code = defaultdict(lambda: defaultdict(list))
    for calc in calculations:
        window = _annual_window(calc, rows)
        by_code[calc['code']][window].append(calc)
    result = []
    for code, windows in by_code.items():
        for (current, prior, basis, unit), group in sorted(windows.items(), reverse=True):
            scope = scope_names[code, basis, unit]
            if scope is None:
                continue
            # Never select a preferred profit definition by the sign of its change.
            fields = defaultdict(list)
            for calc in group:
                fields[analysis_field(rows[calc['data_refs'][-1]])].append(calc)
            by_field = {field: items[0] for field, items in fields.items() if len(items) == 1}
            revenue = by_field.get('ths_revenue_stock')
            profit = by_field.get('ths_np_atoopc_stock') or by_field.get('ths_np_stock')
            cost = by_field.get('ths_operating_cost_stock')
            cash = by_field.get(CASH_FLOW)
            selected = [calc for calc in (revenue, cost, cash, profit) if calc]
            if not selected:
                continue
            head = (code if show_code else '') + scope + f'{current}报告期较{prior.year}年同期，'
            directions = defaultdict(list)
            for calc in selected:
                row = rows[calc['data_refs'][-1]]
                label = FINANCIAL_LABELS.get(analysis_field(row), row['label'])
                directions[_direction(Decimal(calc['exact']))].append(label)
            changes = ['、'.join(labels) + ('均' if len(labels) > 1 else '') + direction
                       for direction, labels in directions.items()]
            explanation = ''
            if revenue and profit:
                income, earnings = (Decimal(calc['exact']) for calc in (revenue, profit))
                profit_label = '归母净利润' if 'ths_np_atoopc_stock' in by_field else '净利润'
                if income < 0 and earnings < income:
                    gap = income - earnings
                    explanation = (f'{profit_label}降幅大于收入降幅，差距{gap:.1f}个百分点。'
                                   if gap >= Decimal('0.05') else profit_label + '降幅略大于收入降幅。')
                    if not cost:
                        explanation += '判断盈利压力是否缓解，需观察收入企稳后利润能否同步回升。'
                elif income > 0 and earnings > income:
                    gap = earnings - income
                    explanation = (f'{profit_label}增速高于收入增速，差距{gap:.1f}个百分点。'
                                   if gap >= Decimal('0.05') else profit_label + '增速略高于收入增速。')
                    explanation += '盈利增长快于经营规模扩张，需结合主营利润率和非经常性损益评估增速的可持续性。'
                elif income < 0 < earnings or earnings < 0 < income:
                    explanation = '收入与利润变动方向相反。应拆分收入规模、成本与其他损益，判断变化是否来自核心经营。'
            elif cash and profit and {_direction(Decimal(calc['exact'])) for calc in (cash, profit)} == {'上升', '下降'}:
                explanation = '现金流与利润变动方向相反。'
                if Decimal(cash['exact']) < 0:
                    explanation += '利润增加的同时经营现金流减少，需结合收现、存货和应付款项核对现金回收。'
            if revenue and cost and Decimal(cost['exact']) < Decimal(revenue['exact']) < 0:
                explanation += '营业成本收缩快于收入，成本占收入的比重已下降。'
                prior_spread, current_spread = (
                    Decimal(str(revenue['inputs'][side])) - Decimal(str(cost['inputs'][side]))
                    for side in ('a', 'b'))
                if prior_spread > current_spread:
                    explanation += '收入与营业成本的金额差仍在收窄，成本率改善只缓解了部分收入收缩压力。'
            result.append(_with_unit_condition(head + '，'.join(changes) + '。' + explanation, selected))
    return result


def _ratio_explanation(readings):
    by_method = {bounds[0]['method']: bounds for _, bounds in readings}
    if costs := by_method.get('raw_margin_v1'):
        profits = by_method.get('raw_percent_v1')
        bridge = net_margin_components(costs, profits) if profits else None
        if bridge is not None:
            before, after, cost_change, other_change = bridge
            if cost_change >= Decimal('0.005') and before - after >= Decimal('0.005'):
                return (f'拆开看，收入成本差占比改善{cost_change:.2f}个百分点，被其余损益的'
                        f'{abs(other_change):.2f}个百分点拖累抵消。后续应区分期间费用、税费和其他损益，'
                        '以判断净利率下降来自持续经营压力还是单期因素。')
            if cost_change <= Decimal('-0.005') and after - before >= Decimal('0.005'):
                return (f'收入成本差占比下降{abs(cost_change):.2f}个百分点，其余损益改善'
                        f'{other_change:.2f}个百分点后，净利率仍有提升。判断改善质量需核对其他损益的构成和持续性。')
    if coverage := by_method.get('raw_multiple_v1'):
        before, after = (calc['inputs'] for calc in coverage)
        old_cash, new_cash = (Decimal(str(values['a'])) for values in (before, after))
        old_profit, new_profit = (Decimal(str(values['b'])) for values in (before, after))
        if 0 <= old_cash < new_cash and old_profit > new_profit > 0:
            return ('倍数上升同时受经营现金流增加和净利润基数缩小推动。现金净额确有改善，'
                    '判断改善能否延续需核对销售收现、应收及存货占用，以及应付账款变化。')
        if old_cash > new_cash >= 0 and 0 < old_profit < new_profit:
            return ('经营现金流减少、净利润增加，共同压低现金覆盖倍数。'
                    '需要区分收现节奏和营运资金占用，才能判断回落是否具有持续性。')
    return ''


def _ratio_paragraphs(calculations, rows, names, scope_names, show_code):
    by_scope = defaultdict(lambda: defaultdict(list))
    for calc in calculations:
        row = rows[calc['data_refs'][0]]
        scope = calc['code'], row['series_basis'], row['unit']
        by_scope[scope][calc['method']].append(calc)
    comparisons = defaultdict(list)
    for (code, basis, unit), methods in by_scope.items():
        scope = scope_names[code, basis, unit]
        if scope is None:
            continue
        for method, results in methods.items():
            periods = defaultdict(list)
            for calc in results:
                day = _sequence_day(calc['period'])
                if day:
                    periods[day].append(calc)
            for end, current in periods.items():
                try:
                    prior = end.replace(year=end.year - 1)
                except ValueError:
                    continue
                previous = periods.get(prior, [])
                if len(current) != 1 or len(previous) != 1:
                    continue
                before, after = previous[0], current[0]
                current_value, prior_value = Decimal(after['exact']), Decimal(before['exact'])
                change = _direction((current_value > prior_value) - (current_value < prior_value))
                comparisons[(code, scope, end, prior)].append((names[method] + change, (before, after)))
    return [_with_unit_condition((code if show_code else '') + scope + f'{end}较{prior.year}年同期，'
                                 + '，'.join(text for text, _ in readings) + '。' + _ratio_explanation(readings),
                                 [calc for _, bounds in readings for calc in bounds])
            for (code, scope, end, prior), readings in sorted(comparisons.items())]


def _financial_sections(task, rows, calculations, show_code):
    by_metric = defaultdict(list)
    for calc in calculations:
        by_metric[calc['method'], analysis_field(rows[calc['data_refs'][0]])].append(calc)
    operating = [calc for field in ('ths_revenue_stock', 'ths_operating_cost_stock', 'ths_np_stock', 'ths_np_atoopc_stock')
                 for calc in by_metric['raw_change_v1', field]]
    profitability = [calc for calc in calculations if calc['method'] in PROFITABILITY]
    cash_changes = by_metric['raw_change_v1', CASH_FLOW]
    coverage = [calc for calc in calculations if calc['method'] == 'raw_multiple_v1']
    if comparison := _cash_growth_profile(rows, calculations):
        income, profit, cash = comparison
        income_rate, profit_rate, cash_rate = (Decimal(calc['exact']) for calc in comparison)
        income_gap = income_rate - profit_rate
        cash_gap = profit_rate - cash_rate
        return [
            _section('收入扩张快于利润增长', (), [income, profit],
                [f'营业收入增速高于归母净利润{income_gap:.1f}个百分点，收入扩张期间归母净利率下降。'
                 '利润增速的可持续性取决于收入扩张后能否稳住成本费用率，同时需要关注业务结构变化对利润率的影响。']),
            _section('经营现金流增速落后于利润', (), [income, profit, cash],
                [f'经营现金流同比增长{cash_rate:.1f}%，较归母净利润增速低{cash_gap:.1f}个百分点。'
                 '本期经营现金净流入仍在增长，新增利润的现金兑现速度较慢。判断这一差距是否会收窄，应重点观察销售回款与备货占用。'])]
    if not (operating or profitability or cash_changes or coverage):
        return []
    scope_names = _financial_scope_names(task, rows)
    cash_windows = {(calc['code'], _annual_window(calc, rows)) for calc in cash_changes}
    cash_profits = [calc for calc in by_metric['raw_change_v1', 'ths_np_stock']
                    if (calc['code'], _annual_window(calc, rows)) in cash_windows]
    cash_profit_ids = {calc['id'] for calc in cash_profits}
    if cash_profit_ids:
        operating = [calc for calc in operating if calc['id'] not in cash_profit_ids]
    result = []
    moved_refs = {ref for calc in cash_profits for ref in calc['data_refs']}
    operating_rows = [row for row in rows.values()
                      if analysis_field(row) in OPERATING_FIELDS and row['id'] not in moved_refs]
    if operating_rows:
        fields = {analysis_field(row) for row in operating_rows if row['value'] is not None}
        title = '收入、成本与利润'
        if len(fields) == 1:
            field, = fields
            title = FINANCIAL_LABELS.get(field, LABELS.get(field, '财务读数')) + '变化'
        result.append(_section(title, operating_rows, operating,
            _operating_paragraphs(operating, rows, scope_names, show_code),
            '解释利润变动仍需拆分期间费用、税费及非经常性损益。' if operating else ''))
    if profitability:
        names = {PROFITABILITY[calc['method']] for calc in profitability}
        title = next(iter(names)) if len(names) == 1 else '收入成本差与净利润率'
        result.append(_section(title, (), profitability,
            _ratio_paragraphs(profitability, rows, PROFITABILITY, scope_names, show_code),
            '收入成本差只覆盖营业收入与营业成本，净利润率还受到费用、税费及其他损益影响。'))
    cash_rows = [row for row in rows.values() if analysis_field(row) == CASH_FLOW]
    if cash_rows:
        cash_comparisons = cash_changes + cash_profits
        cash_title = '经营现金流与利润' if cash_profits or coverage else '经营现金流'
        result.append(_section(cash_title, cash_rows, cash_comparisons + coverage,
            _operating_paragraphs(cash_comparisons, rows, scope_names, show_code)
            + _ratio_paragraphs(coverage, rows, {'raw_multiple_v1': '经营现金流/净利润倍数'}, scope_names, show_code),
            '判断现金流变化的持续性需结合营运资金变动与现金流量表附注。' if cash_changes or coverage else ''))
    return result


def _market_paragraphs(rows, changes, ranges, show_code):
    paragraphs = []
    scopes = defaultdict(set)
    for row in rows.values():
        scopes[row['code'], row['field']].add((row['series_basis'], row['unit']))
    for calc in changes:
        first, last = (rows[ref] for ref in calc['data_refs'])
        if len(scopes[first['code'], first['field']]) != 1:
            continue
        label = '单位净值' if first['field'] in NAV_FIELDS else first['label']
        subject = first['code'] + '的' if show_code else ''
        text = f"{calc['period']}，{subject}{label}期末读数较起点{_direction(Decimal(calc['exact']))}。"
        supporting = [calc]
        same_window = [sample for sample in ranges if all(
            sample.get(key) == calc.get(key) for key in ('code', 'period', 'window_start', 'window_end'))
            and all((row['field'], row['series_basis'], row['unit']) ==
                    (first['field'], first['series_basis'], first['unit'])
                    for row in (rows[ref] for ref in sample['data_refs']))]
        if len(same_window) == 1:
            sample = same_window[0]
            low, high, end = (Decimal(sample['minimum']), Decimal(sample['maximum']), _value_key(last))
            if low < end < high:
                opening = _sequence_day(first['period'])
                closing = _sequence_day(last['period'])
                low_day = _sequence_day(rows[sample['data_refs'][0]]['period'])
                if opening < low_day < closing:
                    text += '期末读数已较窗口内的样本低点回升。'
                    supporting.append(sample)
            elif low < high and end in (low, high):
                text += '期末读数为该窗口已取得读数中的' + ('最低值。' if end == low else '最高值。')
                supporting.append(sample)
        paragraphs.append(_with_unit_condition(text, supporting))
    return list(dict.fromkeys(paragraphs))


def _market_sections(rows, calculations, show_code):
    result = []
    for fields, title, risk in (
        ({'close'}, '收盘走势与样本区间', '区间收盘变化受观察窗口影响，归因仍需对照同期基准及相关事件。'),
        (NAV_FIELDS, '单位净值变化', '持有人收益还取决于分红、份额调整和实际持有期间。'),
    ):
        readings = [row for row in rows.values() if row['field'] in fields]
        selected = [calc for calc in calculations
                    if calc['method'] in ('raw_change_v1', 'raw_range_v1')
                    and rows[calc['data_refs'][0]]['field'] in fields]
        if not selected:
            continue
        changes = [calc for calc in selected if calc['method'] == 'raw_change_v1' and not calc.get('comparison_kind')]
        ranges = [calc for calc in selected if calc['method'] == 'raw_range_v1']
        paragraphs = _market_paragraphs(rows, changes, ranges, show_code)
        result.append(_section(title, readings, selected,
            paragraphs or (['最高和最低值均来自已取得的样本。'] if ranges else []), risk))
    return result


def _economic_sections(task, rows, calculations):
    """Write the observed change and the path between the same series' endpoints."""
    from .draft_economic_scope import DraftEconomicScope
    from .draft_sources import _observation_profile

    frequencies = economic_frequencies(task)
    by_code = defaultdict(list)
    for calc in calculations:
        if calc['method'] == 'raw_percentage_points_v1' and not calc.get('comparison_kind') and len(calc['data_refs']) == 2:
            by_code[calc['code']].append(calc)
    series = defaultdict(list)
    for row in rows.values():
        series[row['code']].append(row)
    names = DraftEconomicScope(task, rows.values()).names
    sections = defaultdict(list)
    for code, comparisons in by_code.items():
        if not (months := _FREQUENCY_MONTHS.get(frequencies.get(code))):
            continue
        closing_period = max(rows[calc['data_refs'][1]]['period'] for calc in comparisons)
        change = min((calc for calc in comparisons if rows[calc['data_refs'][1]]['period'] == closing_period),
                     key=lambda calc: rows[calc['data_refs'][0]]['period'])
        before, after = (rows[ref] for ref in change['data_refs'])
        cohort = [row for row in series[code] if row['series_basis'] == after['series_basis']
                  and row['unit'] == '%' and before['period'] <= row['period'] <= after['period']]
        profile = _observation_profile(cohort)
        if not profile or {profile[key]['data_ref'] for key in ('first', 'last')} != set(change['data_refs']):
            continue
        name = after['label'].replace(':', '').replace('：', '')

        def period(row):
            return _period_label(_period_number(_sequence_day(row['period']), months), months)

        first_period, last_period = period(before), period(after)
        if first_period[:5] == last_period[:5]:
            last_period = last_period[5:]
        text = f'{first_period}至{last_period}，{name}'
        links = []

        def reading(value, sources, calculation_refs=(), **binding):
            nonlocal text
            links.append(dict(start=len(text), end=len(text) + len(value), original=value,
                              data_refs=sources, calculation_refs=list(calculation_refs), **binding))
            text += value

        def level(row):
            reading(_decimal_text(_value_key(row))+'%', [row['id']], kind='economic_level', row_hash=fingerprint(row))

        difference = Decimal(change['exact'])
        if difference:
            text += '由'
            level(before)
            text += ('升至' if difference > 0 else '降至')
            level(after)
            text += '，' + _direction(difference)
            reading(_decimal_text(abs(difference))+'个百分点', change['data_refs'], [change['id']],
                    kind='economic_points', calculation_hash=fingerprint(change))
        else:
            text += '首末两期均为'
            level(after)
        text += '。'
        endpoints = set(change['data_refs'])
        endpoint_values = {_value_key(before), _value_key(after)}
        for key, description in (('maximum', '样本高点'), ('minimum', '样本低点')):
            ref = profile[key]['data_ref']
            row = rows[ref]
            if ref not in endpoints and _value_key(row) not in endpoint_values:
                text += f'{description}出现在{period(row)}，为'
                level(row)
                text += '。'
        in_scope = {row['id'] for row in cohort}
        section = _section(name, cohort, [calc for calc in comparisons if set(calc['data_refs']) <= in_scope], [text])
        section['source_refs'] = list(dict.fromkeys(row['artifact_id'] for row in cohort))
        section['economic_readout'] = True
        scope = names.get(after['id'])
        comparable = (scope and len(scope.metrics) == 1 and scope.regions and scope.rates and scope.variants
                      and scope.variants.isdisjoint({'预测', '未经季调', '未季调', '季调', '累计'}))
        key = (scope.metrics, scope.regions, scope.rates, months, before['period'], after['period']) if comparable else (code,)
        sections[key].append((section, links, difference))
    result = []
    for group in sections.values():
        if len(group) == 1:
            section, links, _ = group[0]
        else:
            directions = {difference.compare(Decimal(0)) for _, _, difference in group}
            title = '分项走势分化' if len(directions) > 1 else '分项走势比较'
            section = _section(title, (), ())
            section['economic_readout'] = True
            paragraphs, links, offset = [], [], 0
            for part, anchors, _ in group:
                paragraphs.append(part['analysis'])
                links.extend(dict(link, start=link['start']+offset, end=link['end']+offset) for link in anchors)
                offset += len(part['analysis']) + 1
            section['analysis'] = '\n'.join(paragraphs)
            for key in ('data_refs', 'calculation_refs', 'source_refs'):
                section[key] = list(dict.fromkeys(ref for part, _, _ in group for ref in part[key]))
        section['quantity_links'] = {'analysis': dict(text_hash=fingerprint(section['analysis']), links=links)}
        result.append(section)
    return result


def attach_valuation_scenario(logics, calculations):
    """Write a fixed-price scenario beside its verified valuation comparison."""
    from .draft_analysis import PE_EARNINGS_EFFECT, PE_MULTIPLE_EFFECT, VALUATION_ATTRIBUTION, VALUATION_SCENARIO

    scenarios = [row for row in calculations if row.get('comparison_kind') == VALUATION_SCENARIO]
    if len(scenarios) != 1 or scenarios[0]['display'] == '0':
        return
    scenario, = scenarios
    drivers = {row['method']: row for row in calculations if row.get('comparison_kind') == VALUATION_ATTRIBUTION
               and row['data_refs'] == scenario['data_refs'] and row['period'] == scenario['period']}
    if not {PE_MULTIPLE_EFFECT, PE_EARNINGS_EFFECT} <= drivers.keys():
        return
    driver_ids = [drivers[method]['id'] for method in (PE_MULTIPLE_EFFECT, PE_EARNINGS_EFFECT)]
    close_refs = set(scenario['data_refs'][:2])
    valuation_refs = set(scenario['data_refs'][2:])
    sections = [item for item in logics if item['analysis']
                and (close_refs | valuation_refs).isdisjoint(item.get('off_scope_data_refs', ()))]
    section = next((item for item in sections if any(ref in item['calculation_refs'] for ref in driver_ids)), None)
    attributed = section is not None
    if section is None:
        section = next((item for item in sections if any(pair <= set(item['data_refs'])
                       for pair in (valuation_refs, close_refs))), None)
    if section is None:
        section = _section('估值变化与盈利情景', (), ())
        logics.append(section)
    if scenario['id'] in section['calculation_refs']:
        return
    preceding = section['analysis']
    join = '\n\n' if preceding else ''
    added_links = []
    if not attributed:
        multiple, earnings = [drivers[method]['display'] for method in (PE_MULTIPLE_EFFECT, PE_EARNINGS_EFFECT)]
        # Contributions are signed percentage points, without a forecast about their direction.
        preceding += f'{join}{scenario["period"]}，估值倍数贡献'
        for method, reading, lead in ((PE_MULTIPLE_EFFECT, multiple, ''), (PE_EARNINGS_EFFECT, earnings, '，隐含滚动盈利贡献')):
            if method == PE_EARNINGS_EFFECT and reading == '0':
                preceding += '，隐含滚动盈利基本持平'
                continue
            preceding += lead
            calculation = drivers[method]
            value = reading + '个百分点'
            added_links.append(dict(start=len(preceding), end=len(preceding)+len(value), original=value,
                                    kind=VALUATION_ATTRIBUTION, calculation_refs=[calculation['id']],
                                    data_refs=calculation['data_refs'], calculation_hash=fingerprint(calculation)))
            preceding += value
        preceding += '。'
        join = '\n\n'
    direction = '高' if scenario['value'] > 0 else '低'
    prefix = f'若股价保持区间终点的收盘水平，回到起点市盈率所对应的隐含滚动盈利将比期末{direction}约'
    number = scenario['display'].lstrip('-') + '%'
    start = len(preceding) + len(join) + len(prefix)
    analysis = preceding + join + prefix + number + '。'
    section['analysis'] = analysis
    section['calculation_refs'] = list(dict.fromkeys([*section['calculation_refs'], *driver_ids, scenario['id']]))
    section['data_refs'] = list(dict.fromkeys([*section['data_refs'], *scenario['data_refs']]))
    links = section.setdefault('quantity_links', {})
    existing = links.get('analysis', {}).get('links', [])
    anchor = dict(start=start, end=start+len(number), original=number, kind=VALUATION_SCENARIO,
                  calculation_refs=[scenario['id']], data_refs=scenario['data_refs'], calculation_hash=fingerprint(scenario))
    links['analysis'] = dict(text_hash=fingerprint(analysis), links=[*existing, *added_links, anchor])


def _cash_bridge_section(rows, calculations):
    from .financial_data import CASH_BRIDGE_FIELDS, UNIT_FACTORS

    changes = {rows[calc['data_refs'][-1]]['field']: calc for calc in calculations
               if calc.get('comparison_kind') == 'cash_bridge_change'}
    if not changes or len({(calc['code'], calc['period'], calc['unit']) for calc in changes.values()}) != 1:
        return None
    sample = next(iter(changes.values()))
    year = sample['period'][:4]
    scale = UNIT_FACTORS[sample['unit']] / UNIT_FACTORS['亿元']
    parts = {field: Decimal(calc['exact'])*scale for field, calc in changes.items()}
    material = [(CASH_BRIDGE_FIELDS[field][1], value) for field, value in parts.items() if value]
    if not material:
        return None
    descriptions = [f'{name}{"增加" if amount > 0 else "减少"}现金流增量约{abs(amount):,.1f}亿元'
                    for name, amount in sorted(material, key=lambda item: item[1])]
    text = f'{year}年上半年合并现金流量表附注显示，' + '，'.join(descriptions) + '。'
    if len(parts) == len(CASH_BRIDGE_FIELDS):
        total = sum(parts.values())
        if total:
            text += f'三项合计{"增加" if total > 0 else "减少"}经营现金流增量约{abs(total):,.1f}亿元。'
    text += '这些变化反映当期资金占用与结算节奏。后续应结合回款进度、存货周转和供应商结算变化，判断现金转化能否改善。'
    return _section('营运资金变动与现金转化', (), list(changes.values()), [text],
                    '单个半年报的营运资金调整受备货、回款和付款时点影响，持续性仍需后续报告期验证。')


def _cash_growth_profile(rows, calculations):
    """Use the same disclosure when interpreting the three verified growth rates."""
    expected = ('ths_revenue_stock', 'ths_np_atoopc_stock', CASH_FLOW)
    comparisons = [calc for calc in calculations if calc.get('comparison_source_ref') and
                   calc['method'] == 'raw_change_v1' and calc['data_refs'][-1] in rows
                   and analysis_field(rows[calc['data_refs'][-1]]) in expected]
    fields = {analysis_field(rows[calc['data_refs'][-1]]): calc for calc in comparisons}
    if (len(calculations) != len(expected) or len(fields) != len(expected) or
            len({(calc['code'], calc['period'], calc['comparison_source_ref']) for calc in comparisons}) != 1):
        return None
    ordered = [fields[field] for field in expected]
    revenue, profit, cash = (Decimal(calc['exact']) for calc in ordered)
    return ordered if revenue > profit > cash > 0 else None


def segment_attribution_readout(task, rows, calculations):
    """Turn a reconciled sample's numbers into a scoped operating argument."""
    from .draft_analysis import segment_revenue_breakdown
    from .financial_data import UNIT_FACTORS

    value = segment_revenue_breakdown(calculations, {row['id']: row for row in rows})
    if not value:
        return None
    year = value['after_period'][:4]
    name = value['name']
    unit = UNIT_FACTORS[value['unit'].removeprefix('人民币')] / UNIT_FACTORS['亿元']
    before, after = value['before'], value['after']
    delta = after-before
    change = (f'同比{"增长" if delta > 0 else "下降"}{abs(delta)/before*100:.1f}%' if before > 0 and delta else
              '与上年同期持平' if not delta else f'同比{"增加" if delta > 0 else "减少"}{abs(delta)*unit:,.1f}亿元')
    direction = sorted(value['parts'], key=lambda part: part['change'], reverse=delta >= 0)
    primary = [item for item in direction if item['change'] * delta > 0][:3]
    basis = '上年同期采用本期报告重述口径。' if value['restated'] else ''
    conclusion = f'{year}年上半年，{name}营业收入{after*unit:,.1f}亿元，{change}。'
    if primary:
        largest = primary[0]
        conclusion += f'{largest["label"]}是收入{"增长" if delta > 0 else "下降"}的最大来源，贡献约{abs(largest["change"])*unit:,.1f}亿元。'
    change_paragraph = f'公司营业收入从上年同期的{before*unit:,.1f}亿元变为{after*unit:,.1f}亿元，{change}。'+basis
    change_paragraph += '分部收入两期分别与集团合并营业收入核对，可沿相同业务口径分解增量。'
    analyses = [change_paragraph]
    if primary:
        parts = '、'.join(item['label']+'约'+f'{abs(item["change"])*unit:,.1f}亿元' for item in primary)
        combined = sum(item['change'] for item in primary)
        summary = ('增量主要来自' if delta > 0 else '收入下降主要来自') + parts + '。'
        if all(part['change'] * delta >= 0 for part in value['parts']):
            summary += f'这些业务合计占公司营收变动的{combined/delta*100:.1f}%。'
        if delta > 0 and after > 0:
            unusual = max(value['parts'], key=lambda part: part['change']/delta-part['after']/after)
            contribution, weight = unusual['change']/delta, unusual['after']/after
            if contribution-weight >= Decimal('0.05'):
                summary += (f'{unusual["label"]}占本期营收{weight*100:.1f}%，贡献{contribution*100:.1f}%的同比增量。'
                            '评估增长持续性时，应进一步核对这部分收入的具体构成。')
        analyses.append(summary)
    scope = '这一结果对应公司披露的分部口径。'
    if task.get('entry') in ('行业', '产业链') or re.search(r'行业|产业链', task.get('question', '')):
        scope = ('这家公司同时经营多个业务，可用来识别行业研究应优先检验的渠道。'
                 '要判断主要驱动是否具有行业代表性，还需对照可比公司的分部变动及对应市场指标。')
    from .planning import question_driven_arguments

    selected = [item['calculation'] for item in value['parts']]
    if question_driven_arguments(task) and primary:
        title = ('分部收入如何形成公司营收增量' if delta > 0 else '分部收缩如何拖累公司营收')
        counter = [item['label'] for item in value['parts'] if item['change'] * delta < 0]
        explanation = ('、'.join(counter) + '的反向变动抵消了部分净变化。' if counter else '')
        section = _section(title, (), selected, [analyses[1] + explanation + scope])
        section['source_refs'] = [value['source']]
        if basis:
            section['detail_analysis'] = basis
        return dict(title=f'{name}{year}年上半年营收驱动', conclusion=conclusion, sections=[section])
    sections = [_section('公司经营规模与同比口径', (), (), [analyses[0]])]
    if len(analyses) > 1:
        sections.append(_section('营业收入增量来自哪些业务', (), selected, analyses[1:]))
    sections.append(_section('研究判断的适用范围', (), (), [scope],
        '半年报分部收入受业务结构、市场活跃度和估值变化影响，单期贡献的持续性需结合后续披露验证。'))
    for section in sections:
        section['source_refs'] = [value['source']]
    return dict(title=f'{name}{year}年上半年营收驱动', conclusion=conclusion, sections=sections)


def retained_argument_sections(task, rows, calculations):
    """Organize observed research around reproducible, task-bound comparisons."""
    if not calculations:
        return []
    owned = {row['id']: row for row in task.get('source_records', ())
             if row['task_id'] == task['id'] and row['research_version'] == task['version']}
    statements = retained_financial_statements(task, rows)
    by_family = defaultdict(dict)
    by_id = {row['id']: row for row in rows}
    for row in rows:
        if record := owned.get(row['artifact_id']):
            by_family[_family(row, record, statements)][row['id']] = row
    usable = [calc for calc in calculations if calc['data_refs'] and all(ref in by_id for ref in calc['data_refs'])]
    use_codes = len({row['code'] for row in rows if row.get('code')}) > 1
    scopes = {family: [calc for calc in usable if all(ref in by_family[family] for ref in calc['data_refs'])]
              for family in ('financial', 'series', 'economic')}
    result = _financial_sections(task, by_family['financial'], scopes['financial'], use_codes)
    if cash_bridge := _cash_bridge_section(by_id, usable):
        result.append(cash_bridge)
    if scopes['economic']:
        result.extend(_economic_sections(task, by_family['economic'], scopes['economic']))
    result.extend(_market_sections(by_family['series'], scopes['series'], use_codes))
    attach_valuation_scenario(result, usable)
    if not result:
        return []
    used_rows = {ref for section in result for ref in section['data_refs']}
    used_calculations = {ref for section in result for ref in section['calculation_refs']}
    other_rows = [row for row in rows if row['id'] not in used_rows]
    other_calculations = [calc for calc in usable if calc['id'] not in used_calculations]
    if other_rows or other_calculations:
        result.append(_section('其他相关读数', other_rows, other_calculations))
    return result


def _valuation_financial_paragraph(section, rows, calculations):
    fields = tuple(field for field in FINANCIAL_ORDER if any(
        analysis_field(rows[ref]) == field for ref in section['data_refs'] if ref in rows))
    income_fields = ('ths_revenue_stock', 'ths_np_atoopc_stock')
    if fields not in (income_fields, (CASH_FLOW,)):
        return
    use = [calc for calc in calculations if calc['id'] in section['calculation_refs'] and calc['method'] == 'raw_change_v1'
           and set(calc['data_refs']) <= rows.keys()]
    by_field = {analysis_field(rows[calc['data_refs'][-1]]): calc for calc in use}
    periods = {calc['period'] for calc in use}
    if (set(by_field) != set(fields) or len(use) != len(fields) or len(periods) != 1
            or any(rows[ref].get('source') != 'ifind' for calc in use for ref in calc['data_refs'])):
        return
    period, = periods
    day = _sequence_day(period)
    if not day or (day.month, day.day) != (12, 31):
        return
    basis = '按iFinD报表同字段原值测算，' if any(calc['conditional_equal_scale'] for calc in use) else ''
    text = f'{day.year}年，'+basis
    links = []
    for field in fields:
        calc = by_field[field]
        rate = Decimal(calc['exact'])
        label = FINANCIAL_LABELS.get(field, rows[calc['data_refs'][-1]]['label'])
        rounded = abs(rate).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
        if not rounded:
            text += label+'同比基本持平，'
            continue
        text += label+'同比'+('增长' if rate > 0 else '下降')
        value = f'{rounded:.1f}%'
        links.append(dict(kind='financial_yoy', start=len(text), end=len(text)+len(value), original=value,
                          data_refs=calc['data_refs'], calculation_refs=[calc['id']], calculation_hash=fingerprint(calc)))
        text += value+'，'
    text = text.rstrip('，')+'。'
    if fields == income_fields:
        revenue, profit = (Decimal(by_field[field]['exact']) for field in income_fields)
        if profit < revenue:
            text += '归母净利润增速低于收入，收入企稳后仍需跟踪成本费用率和产品结构，判断利润能否同步修复。'
        elif profit > revenue:
            text += '归母净利润增速高于收入，持续性取决于利润率改善能否延续。'
        section['title'] = '收入与归母利润'
    else:
        text += ('现金流的持续性取决于回款和营运资金占用。'
                 '结合销售收现及合同负债，可以区分经营变化与付款、结算时点的波动。')
    section['analysis'] = text
    if links:
        section['quantity_links'] = {'analysis': dict(text_hash=fingerprint(text), links=links)}


def valuation_research_readout(task, rows, calculations):
    """Make a first paper from an actual matched price and multiple window."""
    from .draft_analysis import PE_EARNINGS_EFFECT, PE_MULTIPLE_EFFECT, VALUATION_ATTRIBUTION, VALUATION_SCENARIO

    scenarios = [row for row in calculations if row.get('comparison_kind') == VALUATION_SCENARIO]
    if len(scenarios) != 1:
        return None
    scenario, = scenarios
    drivers = {row['method']: Decimal(row['exact']) for row in calculations
               if row.get('comparison_kind') == VALUATION_ATTRIBUTION and row['data_refs'] == scenario['data_refs']}
    if drivers.keys() != {PE_MULTIPLE_EFFECT, PE_EARNINGS_EFFECT}:
        return None
    sections = retained_argument_sections(task, rows, calculations)
    valuation = next((row for row in sections if scenario['id'] in row['calculation_refs']), None)
    if valuation is None:
        return None
    multiple, earnings = (drivers[method] for method in (PE_MULTIPLE_EFFECT, PE_EARNINGS_EFFECT))
    bias = '上涨' if multiple + earnings > 0 else '下跌'
    if abs(multiple) > abs(earnings) and multiple + earnings:
        label = '估值抬升' if multiple > 0 else '估值回落'
        valuation['title'] = label+'主导区间'+bias
        conclusion = '区间股价'+bias+'主要由'+label+'解释。'
    elif abs(earnings) > abs(multiple) and multiple + earnings:
        valuation['title'] = '盈利变化主导区间'+bias
        conclusion = '区间股价'+bias+'主要来自隐含滚动盈利的变化。'
    else:
        conclusion = '区间股价的变化取决于估值倍数和隐含滚动盈利的共同作用。'
    start, end = (_sequence_day(scenario[key]) for key in ('window_start', 'window_end'))
    conclusion = (f'{start.month}月{start.day}日至{end.month}月{end.day}日，'+conclusion
                  if start and end and start.year == end.year else conclusion)
    from .planning import question_driven_arguments

    financial = {row['id']: row for row in rows if analysis_field(row) in FINANCIAL}
    if question_driven_arguments(task):
        if multiple > 0 > earnings:
            conclusion += '倍数扩张受到隐含滚动盈利回落的抵消。后续若实际盈利改善，才有依据把定价支撑归于经营端。'
        elif earnings > 0 > multiple:
            conclusion += '隐含滚动盈利的正贡献被倍数压缩部分抵消。需用财报核对盈利兑现，再用同口径估值样本判断定价是否偏低。'
        elif multiple > 0 and earnings > 0:
            conclusion += '倍数和隐含滚动盈利同向贡献。两者的持续性分别取决于实际盈利兑现和市场愿意支付的估值。'
        elif multiple < 0 and earnings < 0:
            conclusion += '盈利与倍数读数同向走弱。重估需要分别确认盈利预测是否企稳和倍数压缩是否缓和。'
    elif any(set(calc['data_refs']) <= financial.keys() and calc['method'] == 'raw_change_v1' for calc in calculations):
        conclusion += '当前定价能否得到经营支撑，取决于收入企稳后利润率和经营现金流能否同步改善。'
    for section in sections:
        if section is not valuation:
            _valuation_financial_paragraph(section, financial, calculations)
    valuation['risk'] = ''
    valuation['valuation_research_readout'] = True
    return dict(title=scenario['code']+'经营与估值', conclusion=conclusion, sections=sections,
        risks=['盈利修复弱于预期，或市场风险偏好回落，均可能削弱阶段性估值表现。'])


def _decimal_text(value):
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _fomc_percent(rate):
    return _decimal_text(Decimal(rate.numerator) / Decimal(rate.denominator))


def fomc_readout(task, excerpts):
    """Summarize published statements, with no claims about first release or causality."""
    from .draft_sources import disclosure_excerpt_hash
    from .source_catalogue import official_record_label

    request = research_fomc_rate_request(task) or fomc_rate_request(task)
    sources = task.get('source_records', [])
    from .draft_sources import local_sources_only

    fetched_calendar = any(record.get('source') == 'official' and record.get('operation') == 'release_catalog'
                           and record.get('parameters', {}).get('channel') == 'fed_fomc' for record in sources)
    if (not request or (not fetched_calendar and not direct_fomc_rate_request(task) and not local_sources_only(task))):
        return None
    dates = fomc_rate_dates(task, request)
    records = {row['id']: row for row in sources if row.get('source') == 'official'
               and row.get('operation') == 'release_original'}
    readings, clocks, conflicts = {}, {}, set()
    for location in excerpts:
        record = records.get(location['source_ref'])
        if (record is None or record.get('task_id') != task['id'] or record.get('research_version') != task['version']
                or record.get('snapshot_hash') != location.get('snapshot_hash')
                or official_record_label(record, sources) != '美联储'):
            continue
        snapshot = record['snapshot']
        statement = fomc_rate_statement(record, snapshot['parts'])
        if not statement or statement['date'] not in dates or any(statement[key] != location[key]
                for key in ('part_index', 'start', 'end')):
            continue
        index, left, right = (statement[key] for key in ('part_index', 'start', 'end'))
        part = snapshot['parts'][index]
        if disclosure_excerpt_hash(record['id'], snapshot['name'], index, part.get('locator'), left, right,
                                    part['text'][left:right]) != location.get('excerpt_hash'):
            continue
        day = statement['date']
        previous = readings.setdefault(day, (statement['rates'], location))
        if previous[0] != statement['rates']:
            conflicts.add(day)
        if clock := fomc_statement_release_clock(record, statement, snapshot['parts']):
            clocks.setdefault(day, {})[clock['scheduled_at']] = dict(clock, source_ref=record['id'])
    for day in conflicts:
        readings.pop(day)

    text, links, notes = '', [], []
    for day in dates:
        if day in conflicts:
            notes.append(day + '的声明留存版本所载区间不一致。')
        elif day not in readings:
            notes.append(day + '的声明目标利率区间尚未核对。')
        else:
            rates, location = readings[day]
            text += day + '的FOMC声明载明，联邦基金目标利率区间为'
            value = '%至'.join(map(_fomc_percent, rates)) + '%'
            links.append(dict(kind='statement_readout', data_refs=[], calculation_refs=[], document=location,
                              start=len(text), end=len(text) + len(value), original=value))
            text += value + '。'
    if request['previous'] and len(dates) < 2:
        notes.append('前次声明日期尚未核实。' if dates else '尚未核实最新及前次声明日期。')
    elif not dates:
        notes.append('尚未核实最新FOMC声明日期。')
    direction = None
    if request['compare']:
        if len(dates) == 2 and all(day in readings for day in dates):
            before, after = (readings[day][0] for day in dates)
            changes = [Decimal(execute('raw_percentage_points_v1', dict(a=_fomc_percent(left), b=_fomc_percent(right)))['exact'])
                       for left, right in zip(before, after)]
            def delta(value):
                direction = '下调' if value < 0 else '上调'
                return direction + _decimal_text(value.copy_abs()) + '个百分点' if value else '持平'
            prefix = '较前次列示声明' if request['previous'] else '与前一列示日期相比'
            if changes == [Decimal(0), Decimal(0)]:
                direction = 'hold'
                text += prefix + '，目标利率区间维持不变。'
            elif changes[0] == changes[1]:
                direction = 'easing' if changes[0] < 0 else 'tightening'
                text += prefix + '，上下限均' + delta(changes[0]) + '。'
            else:
                text += prefix + '，下限' + delta(changes[0]) + '，上限' + delta(changes[1]) + '。'
        else:
            notes.append('两次声明的区间变化暂缺。')
    chosen_clocks = clocks.get(dates[-1], {}) if dates and dates[-1] in readings else {}
    from .treasury_rates import requested_series as requested_treasury_series

    decision_sources = list(dict.fromkeys(link['document']['source_ref'] for link in links))
    if requested_treasury_series(task) and direction == 'hold' and len(links) == 2:
        latest = dict(links[-1])
        opening = f'{dates[-1]}议息会议将联邦基金目标区间维持在'
        text = opening + latest['original'] + '。'
        latest.update(start=len(opening), end=len(text)-1)
        links = [latest]
    return dict(title='FOMC目标利率区间核对', text=text or (notes[0] if notes else ''), links=links, notes=notes,
                policy_direction=direction, latest_observation=max(readings, default=None),
                release_clock=next(iter(chosen_clocks.values())) if len(chosen_clocks) == 1 else None,
                decision_sources=decision_sources)


def _policy_market_observations(task, release, rows, calculations):
    """Describe explicitly scoped closes spanning the release schedule."""
    from .evidence import declared_security_codes
    from .market_calendar import stated_close_intervals

    explicit = stated_close_intervals(task, explicit=True)
    targets = {subject_key(code).upper() for code in declared_security_codes(task) if code.endswith(('.HK', '.SH', '.SZ'))}
    market = {row['id']: row for row in rows if analysis_field(row) == 'close' and subject_key(row['code']).upper() in targets}
    day = release.date()
    first_possible = day if release.hour < 9 else day + timedelta(days=1)
    by_code = defaultdict(list)
    for calculation in calculations:
        ids = calculation['data_refs']
        if (calculation['method'] != 'raw_change_v1' or calculation.get('comparison_kind')
                or calculation.get('conditional_equal_scale') or len(ids) != 2 or not set(ids) <= market.keys()):
            continue
        before, after = (market[ref] for ref in ids)
        code = subject_key(before['code']).upper()
        start, end = _sequence_day(before['period']), _sequence_day(after['period'])
        if (start is None or end is None or before['code'] != after['code'] or before['unit'] != after['unit']
                or not day - timedelta(days=7) <= start < day or not first_possible <= end <= day + timedelta(days=7)
                or not any(start == first and end == last for first, last, _ in explicit.get(code, ()))):
            continue
        by_code[code].append((before, after, calculation))
    chosen = [pairs[0] for pairs in by_code.values() if len(pairs) == 1]
    if not chosen:
        return None
    paragraphs = []
    for before, after, calculation in chosen:
        rate = Decimal(calculation['exact'])
        move = '上涨' if rate > 0 else '下跌'
        description = move+_decimal_text(rate.copy_abs())+'%' if rate else '持平'
        paragraphs.append(f'{after["object_label"]}在问题指定的{before["period"]}至{after["period"]}区间，收盘读数{description}。')
    paragraphs.append('区间变动包含同时发生的其他信息，单独的收盘比较无法分离本次决议的影响。')
    return _section('发布前后收盘读数', (), [item[2] for item in chosen], paragraphs)


def _event_relative_close(spread):
    from .event_market import INDEX_NAMES

    difference = Decimal(spread['exact'])
    sector = INDEX_NAMES[spread['code']]
    benchmark = INDEX_NAMES[spread['benchmark_code']]
    if not difference:
        return f'{sector}与{benchmark}涨跌幅持平。'
    direction = '高' if difference > 0 else '低'
    return f'{sector}较{benchmark}{direction}{abs(difference):.2f}个百分点。'


def _aligned_policy_market_observations(rows, calculations, *, expanded=False):
    """Interpret indexed closing changes with a common benchmark and local dates."""
    from .event_market import INDEXES

    facts = {row['id']: row for row in rows}
    available = {item['code']: item for item in calculations if item.get('comparison_kind') == 'event_close_return'
                 and item['code'] in INDEXES and set(item['data_refs']) <= facts.keys()}
    if not available:
        return None
    target_code = 'HSTECH.HI' if 'HSTECH.HI' in available else 'HSI.HI'
    target = available[target_code]
    start, end = target['window_start'], target['window_end']
    selected = [item for code, item in available.items()
                if code == target_code or (item['window_start'], item['window_end']) == (start, end)]
    selected.sort(key=lambda item: item['code'] != target_code)

    def description(item):
        rate = Decimal(item['exact'])
        return INDEXES[item['code']][2] + (
            f'{"上涨" if rate > 0 else "下跌"}{abs(rate):.2f}%' if rate else '持平')

    a, b = _sequence_day(start), _sequence_day(end)
    period = f'{a.month}月{a.day}日收盘至{b.month}月{b.day}日收盘'
    result = f'{period}，'+'，'.join(map(description, selected))+'。'
    if expanded:
        spreads = [item for item in calculations if item.get('comparison_kind') == 'event_market_spread'
                   and item['code'] == target_code and item.get('benchmark_code') in {row['code'] for row in selected}
                   and item['period'] == target['period'] and set(item['data_refs']) <= facts.keys()]
        section = _section('港股科技与大盘首日表现' if target_code == 'HSTECH.HI' else '港股大盘首日表现',
                           (), [*selected, *spreads], [result])
        section['event_session_summary'] = _event_relative_close(spreads[0]) if spreads else description(target)+'。'
        if spreads:
            section['detail_analysis'] = ('恒生指数提供同期市场对照。若实际利率走高，并伴随不同市场的长久期板块普遍相对走弱，'
                                          '折现率渠道更可能解释这部分差额。若利率变化有限，回落又集中在少数权重股，'
                                          '应优先核对成分股的盈利预期和同期公司消息。')
        section['workpaper_notes'] = ['指数各用香港当地上一交易日与会议后第一个交易日的收盘点位。']
        return section
    summary = '港股首个收盘窗口已有市场读数。'
    if len(selected) == 2:
        benchmark = selected[1]
        tech, wide = Decimal(target['exact']), Decimal(benchmark['exact'])
        spread = Decimal(execute('raw_percentage_points_v1', dict(a=wide, b=tech))['exact'])
        if tech < 0 and wide < 0:
            summary = ('港股科技与大盘同向回落，科技指数的跌幅'
                       + ('略小。' if spread > 0 and abs(spread) < 1 else '较小。' if spread > 0
                          else '更大。' if spread < 0 else '相同。'))
        else:
            summary = '首个收盘窗口内，科技指数与大盘的相对表现可据同段价格比较。'
        if spread:
            result += f'恒生科技指数{"高于" if spread > 0 else "低于"}市场基准{abs(spread):.2f}个百分点。'
        if tech < 0 and wide < 0 and spread > 0:
            result += '首个交易窗口的下跌具有市场共性，科技板块在两个指数的比较中承压相对较轻。'
        elif tech < 0 and spread < 0:
            result += '科技板块在首个交易窗口内相对大盘承压，成分差异、同期盈利信息和风险偏好均可能影响读数。'
    section = _section('港股首个收盘窗口的相对表现', (), selected, [result])
    section['event_session_summary'] = summary
    section['workpaper_notes'] = ['以声明所列时刻为界，选择当地前一笔和后一笔日收盘记录。'
                                  '相对表现按同窗的两个价格涨跌幅之差计算。']
    return section


def _mainland_policy_observations(rows, calculations, codes, *, expanded=False):
    from .event_market import INDEX_NAMES

    facts = {row['id']: row for row in rows}
    reading = {item['code']: item for item in calculations if item.get('comparison_kind') == 'event_close_return'
               and item['code'] in codes and set(item['data_refs']) <= facts.keys()}
    if not reading:
        return None
    groups = defaultdict(list)
    for code in codes:
        if code in reading:
            item = reading[code]
            groups[item['window_start'], item['window_end']].append(item)
    paragraphs = []
    for (first, last), items in groups.items():
        a, b = map(_sequence_day, (first, last))
        changes = []
        for item in items:
            rate = Decimal(item['exact'])
            move = '上涨' if rate > 0 else '下跌'
            changes.append(INDEX_NAMES[item['code']]+(f'{move}{abs(rate):.2f}%' if rate else '持平'))
        paragraphs.append(f'{a.month}月{a.day}日收盘至{b.month}月{b.day}日收盘，'+'，'.join(changes)+'。')
    spreads = [item for item in calculations if item.get('comparison_kind') == 'event_market_spread'
               and item['code'] in reading and item.get('benchmark_code') in reading and set(item['data_refs']) <= facts.keys()]
    summary = ''
    if spreads:
        comparisons = []
        for spread in spreads:
            diff = Decimal(spread['exact'])
            benchmark = INDEX_NAMES[spread['benchmark_code']]
            direction = '高' if diff > 0 else '低'
            comparisons.append(f'券商指数的涨跌幅较{benchmark}{direction}{abs(diff):.2f}个百分点。' if diff
                               else f'券商指数与{benchmark}的窗口涨跌幅持平。')
        summary = ''.join(_event_relative_close(row) for row in spreads) if expanded else ''.join(comparisons)
        if not expanded:
            paragraphs[-1] += summary
        paragraphs.append('券商的经纪和两融收入对成交活跃度与信用需求较敏感。单日相对表现只刻画市场定价，'
                          '判断盈利变化还须结合后续成交额、两融余额和公司披露。')
    if uncovered := [INDEX_NAMES[code] for code in codes if code not in reading]:
        paragraphs.append('、'.join(uncovered)+'缺少该窗口可配对的收盘读数。')
    if len(groups) > 1:
        paragraphs.append('上述收盘区间不同，分开列示各指数表现。')
    section = _section('A股首个收盘窗口的相对表现' if spreads else 'A股首个收盘窗口',
                       (), [*reading.values(), *spreads], paragraphs)
    section['event_session_summary'] = summary or (paragraphs[0] if expanded else 'A股首个收盘窗口内已取得实际指数读数。')
    if expanded and spreads:
        section['detail_analysis'] = ('以沪深市场自己的宽基指数衡量板块相对强弱。若随后成交额与两融余额同步回落，'
                                      '并在券商披露中反映为经纪或利息收入承压，盈利预期下修的解释更有依据。'
                                      '若成交和融资活跃度维持，首日差额更可能反映短期风险偏好或仓位调整。')
    section['workpaper_notes'] = ['境内指数使用各交易所当地交易日的收盘点位。同一日期区间内用价格指数涨跌幅之差衡量相对表现。']
    return section


def _other_event_window(rows, calculations):
    from .event_market import INDEX_NAMES

    refs = {row['id'] for row in rows}
    changes = [row for row in calculations if row.get('comparison_kind') == 'event_observed_return'
               and set(row['data_refs']) <= refs]
    if not changes:
        return None
    by_window = defaultdict(list)
    for row in changes:
        change = Decimal(row['exact'])
        movement = f'{"上涨" if change > 0 else "下跌"}{abs(change):.2f}%' if change else '持平'
        by_window[row['period']].append(INDEX_NAMES[row['code']]+movement)
    paragraphs = [period+'收盘区间内，'+'，'.join(readings)+'。' for period, readings in by_window.items()]
    section = _section('补充区间收盘表现', (), changes, paragraphs)
    section['event_observation_only'] = True
    section['workpaper_notes'] = ['此处采用两端实际收盘点位。缺少足以定位相邻交易日的资料，区间读数不纳入首日比较。']
    return section


def _treasury_sections(rows, calculations):
    from .treasury_rates import NAMES

    by_ref = {row['id']: row for row in rows}
    admitted = [row for row in calculations if set(row['data_refs']) <= by_ref.keys()]
    moves = [row for row in admitted if row.get('comparison_kind') == 'treasury_event_move']
    if not moves:
        return []

    def move(value, up='上行', down='下行'):
        return (up if value > 0 else down) + _decimal_text(abs(value)) + '个基点' if value else '持平'

    deltas = {row['code']: Decimal(row['exact']) for row in moves}
    reports = []
    for row in moves:
        before, after = [by_ref[ref] for ref in row['data_refs']]
        reports.append(f'{NAMES[row["code"]]}收益率由{before["value"]:.2f}%变为{after["value"]:.2f}%，'
                       + move(deltas[row['code']]))
    periods = {row['period'] for row in moves}
    same_window = len(periods) == 1
    if same_window:
        prior, today = next(iter(periods)).split('至')
        text = f'按美东日期，{today}与{prior}相比，' + '。'.join(reports) + '。'
    else:
        text = ''.join(row['period']+'，'+report+'。' for row, report in zip(moves, reports, strict=True))
    if (same_window and {'DGS2', 'DGS10'} <= deltas.keys() and deltas['DGS2']*deltas['DGS10'] < 0):
        if deltas['DGS10'] > 0:
            text += '短端利率回落，长端名义贴现率却上升。单看短端会低估长久期资产面对的贴现压力。'
        else:
            text += '短端利率上升，长端名义贴现率回落。短期利率预期与长期融资定价在这段窗口内出现分化。'
    changes = _section('美债收益率的期限表现', (), moves, [text])
    changes['event_observation_only'] = True
    changes['workpaper_notes'] = ['采用H.15同口径名义固定期限收益率。估价通常基于美东约15:30的参考报价，比较两个公布日的日度读数。']
    changes['detail_analysis'] = ('2年期利率与近端利率预期联系较紧，10年期还反映较长时期的名义利率预期与期限溢价。'
                                  '两个期限都是日度水平，变化以基点计量。')
    moves_text = '，'.join(NAMES[code] + '收益率' + move(change) for code, change in deltas.items()) + '。'

    spreads = [row for row in admitted if row.get('comparison_kind') == 'treasury_term_spread']
    shift = next((row for row in admitted if row.get('comparison_kind') == 'treasury_curve_shift'), None)
    if len(spreads) != 2 or not shift:
        changes['event_session_summary'] = '议息日' + moves_text
        return [changes]
    prior, current = sorted(spreads, key=lambda row: row['period'])
    movement = move(Decimal(shift['exact']), '走阔', '收窄')
    slope_text = f'10年期减2年期的利差由{_decimal_text(Decimal(prior["exact"]))}个基点变为'
    slope_text += f'{_decimal_text(Decimal(current["exact"]))}个基点，{movement}。'
    if deltas['DGS10'] > 0 > deltas['DGS2']:
        slope_text += '短端利率下行的同时，长期名义融资的市场利率仍在上移。'
    elif Decimal(shift['exact']) > 0:
        slope_text += '利差扩大时，较长期限相对短端的利率补偿上升。'
    elif Decimal(shift['exact']) < 0:
        slope_text += '曲线变平时，可结合两端的方向区分长端回落和短端抬升。两种路径对长期融资利率的含义不同。'
    curve = _section('期限利差与长久期定价', (), [prior, current, shift], [slope_text],
        '长端若继续上行，长久期债券对利率变化更敏感。若2年期回升而10年期企稳，期限利差可能收窄。')
    curve['event_observation_only'] = True
    curve['event_session_summary'] = '议息日' + moves_text + '10年期与2年期利差' + movement + '。'
    curve['detail_analysis'] = ('期限利差取同日10年期名义收益率减2年期名义收益率。两端变动共同决定利差变化。'
                                '核对长端上行的成分时，可同时查看同日10年期实际收益率和名义收益率，'
                                '再结合通胀补偿和国债供给，区分增长预期、通胀定价和期限溢价。')
    return [changes, curve]


def policy_transmission_sections(task, event, rows, calculations):
    """Explain the plausible channel and keep any market readout within its window."""
    from .event_market import event_reading_enabled, requested_markets
    from .treasury_rates import EASTERN, requested_series as requested_treasury_series

    if not event['links']:
        return []
    expanded = event_reading_enabled(task)
    direction = event.get('policy_direction')
    conditions = {
        'tightening': '如果目标利率上调未被市场充分计价，并推高相关期限的实际利率，估值的折现压力会增加。',
        'easing': '如果目标利率下调未被市场充分计价，并带动相关期限的实际利率下降，估值可获得折现率支持。',
        'hold': '目标区间维持时，定价更取决于声明前市场预期，以及声明发布后未来利率路径的变化。',
    }
    text = conditions.get(direction, '市场反应首先取决于政策相对原有预期的差异，以及相关期限市场利率的实际变动。')
    equity = re.search(r'港股|A股|股票|股价|权益|估值|科技指数|券商指数|上证', task['question'], re.I)
    if equity:
        text += '远期现金流占比较高的资产对折现率更敏感。若同期盈利预期变化，或风险溢价与实际利率反向变动，股价反应也会随之改变。'
    if expanded and direction == 'hold':
        text = ('两次声明的目标区间相同。区间持平时，市场仍会重估之后的利率路径；'
                '如果决议符合会前定价，利率目标本身带来的新增信息有限。')
    elif '港股' in task['question']:
        text += '判断港股反应时，应将美国市场的发布时点对齐港股下一可交易时段，并对照港元流动性与同期本地消息。'
    title = '目标利率区间与会前定价' if expanded else '政策变化怎样传导到资产定价'
    section = _section(title, (), (), [text])
    section['source_refs'] = event.get('decision_sources') or list(dict.fromkeys(
        link['document']['source_ref'] for link in event['links']))
    clock = event.get('release_clock')
    requested = requested_markets(task)
    treasuries = requested_treasury_series(task)
    yield_sections = _treasury_sections(rows, calculations) if treasuries else []
    if treasuries and clock:
        release = datetime.fromisoformat(clock['scheduled_at']).astimezone(EASTERN)
        policy = ('两次议息的目标区间相同。2年期收益率对未来政策利率的定价较敏感，'
                  '10年期还纳入远期增长、通胀及期限补偿。' if direction == 'hold' else text)
        section['analysis'] = f'美联储声明标注的发布时间为美东{release:%m月%d日 %H:%M}。' + policy
        section['workpaper_notes'] = [f'{clock["part"]}标注 {clock["quote"]}。']
        if not requested:
            return [section, *yield_sections]
    if expanded and clock:
        section['detail_analysis'] = ('比较声明前后短端政策利率定价与中长期实际利率。前者用于识别市场对后续利率路径的调整，'
                                      '后者用于核对权益折现率的方向。若两者保持平稳，两地股票的变化更适合从本地交易因素入手。')
    observed = []
    if clock and (requested or re.search(r'港股|恒生|A股|沪深|深市|沪市', task['question'])):
        from zoneinfo import ZoneInfo

        release = datetime.fromisoformat(clock['scheduled_at']).astimezone(ZoneInfo('Asia/Shanghai'))
        section['analysis'] = f'声明所列发布时间折合北京时间{release:%Y-%m-%d %H:%M}。' + section['analysis']
        section['workpaper_notes'] = [f'{clock["part"]}标注 {clock["quote"]}。']
        hk = _aligned_policy_market_observations(rows, calculations, expanded=expanded)
        if hk:
            observed.append(hk)
        mainland = _mainland_policy_observations(rows, calculations, requested.get('mainland', ()), expanded=expanded)
        if mainland:
            observed.append(mainland)
        if not observed:
            stated = _policy_market_observations(task, release, rows, calculations)
            if stated:
                observed.append(stated)
        if mainland and 'hong_kong' in requested and not hk:
            mainland['analysis'] += '\n本次港股缺少可配对的收盘读数，结论限于境内市场。'
        if hk and 'mainland' in requested and not mainland:
            hk['analysis'] += '\n本次境内指数缺少可配对的收盘读数，结论限于港股。'
        if hk and mainland:
            hk_returns = {item['code']: item for item in calculations
                          if item['id'] in hk['calculation_refs'] and item.get('comparison_kind') == 'event_close_return'}
            cn_spreads = [item for item in calculations if item['id'] in mainland['calculation_refs']
                          and item.get('comparison_kind') == 'event_market_spread']
            if ({'HSTECH.HI', 'HSI.HI'} <= hk_returns.keys() and cn_spreads
                    and Decimal(hk_returns['HSTECH.HI']['exact']) < Decimal(hk_returns['HSI.HI']['exact'])
                    and all(Decimal(item['exact']) < 0 for item in cn_spreads)):
                mainland['analysis'] += '\n港股科技和A股券商均弱于各自基准，显示回落中还有板块相对走弱。'
            if expanded:
                mainland['workpaper_notes'].append('沪深和香港按各自当地收盘计量，使用各自基准。')
            else:
                mainland['analysis'] += '\n沪深与香港收盘时刻不同。两地分别与自身基准对照，日收盘数据无法隔离跨市场时差。'
    boundary = '判断是否由本次决议推动，需核对会前利率预期，并用同窗利率、市场基准和同期消息区分解释。'
    if expanded:
        boundary = '会前利率预期与会后定价若基本相同，板块的相对涨跌更适合结合当地交易和本地消息判断。'
        if len(observed) > 1:
            boundary += '沪深与香港收盘时刻不同，各自的涨跌覆盖不同交易时段。'
    if context := _other_event_window(rows, calculations):
        observed.append(context)
    if not observed:
        observed = [_section('如何验证市场影响', (), (),
            ['区间变化已由会议声明确认。实际市场影响需要对照会前利率预期、发布前后的期限利率与目标资产表现，并检查同期其他消息。'],
            boundary)]
    else:
        observed[-1]['risk'] = boundary
        if any(item.get('event_session_summary') for item in observed) and '估值' in task['question']:
            observed[-1]['risk'] += '拆分估值倍数和盈利预期的贡献，还需取得相同日期的估值与盈利预期读数。'
    return [section, *yield_sections, *observed]


def verified_event_market_answer(task, draft):
    """Bind a completed local-session observation to its exact dated inputs."""
    from .draft_analysis import analyze
    from .draft_sources import observations, retained_public_excerpts
    from .evidence import declared_security_codes
    from .event_market import (asks_first_event_session, event_reading_enabled, market_scopes, requested_markets,
                               verified_event_sessions_enabled)
    from .planning import input_fingerprint
    from .treasury_rates import bounded_readout as bounded_treasury_readout, requested_series as requested_treasury_series

    question_text = task['question']
    treasuries = requested_treasury_series(task)
    treasury_only = bool(treasuries and bounded_treasury_readout(task))
    if (task.get('run_mode') not in ('direct', 'local_draft') or draft.get('origin') != 'data_outline'
            or task.get('question_origin') != 'default' or len(task['questions']) != 1
            or not (treasury_only or asks_first_event_session(task) and re.search(r'收盘(?:表现|变化|涨跌)|怎么走|走势', question_text))
            or re.search(r'估值|因果|归因|原因|驱动|为何|为什么|预测|目标价|未来|买入|卖出|推荐|公告|(?:半年|季度|年报)'
                         r'|成交额|汇率|两融|盈利|持仓|回测|中长|\d+个?交易日|除外|同时另|美股|纳指|大宗|期权', question_text)):
        return None
    question, = task['questions']
    if (not question['required'] or question.get('evidence_needs') or question.get('method_needs')
            or task['input_hash'] != input_fingerprint(task)):
        return None
    # Every requested venue needs its own aligned close window.
    requested = requested_markets(task)
    scopes = market_scopes(task)
    if (not scopes and not treasury_only) or set(scopes) != set(requested):
        return None
    if not verified_event_sessions_enabled(task) and set(scopes) != {'hong_kong', 'mainland'}:
        return None
    if set(declared_security_codes(task)).difference(code for codes in requested.values() for code in codes):
        return None
    facts = observations(task)
    calculations = analyze(task, facts)['results']
    if draft['facts'] != facts or draft['calculations'] != calculations:
        return None
    readouts = {row['code']: row for row in calculations if row.get('comparison_kind') == 'event_close_return'}
    windows = {}
    for venue, scope in scopes.items():
        if any(code not in readouts for code in scope['codes']):
            return None
        dates = {(readouts[code]['window_start'], readouts[code]['window_end']) for code in scope['codes']}
        if len(dates) != 1:
            return None
        start, end = dates.pop()
        windows[venue] = dict(codes=scope['codes'], start=start, end=end)
    if treasuries:
        reads = {row['code']: row for row in calculations if row.get('comparison_kind') == 'treasury_event_move'}
        dates = {(row['window_start'], row['window_end']) for row in reads.values()}
        if (set(reads) != set(treasuries) or len(dates) != 1 or
                len(treasuries) > 1 and not any(row.get('comparison_kind') == 'treasury_curve_shift' for row in calculations)):
            return None
        start, end = dates.pop()
        windows['us_treasury'] = dict(codes=list(treasuries), start=start, end=end)
    if re.search(r'同一(?:个)?(?:交易)?窗口|同窗', question_text) and len({
            (value['start'], value['end']) for value in windows.values()}) > 1:
        return None
    event = fomc_readout(task, retained_public_excerpts(task, verify_text=True))
    if not event or event['notes'] or not event['links'] or not event['release_clock']:
        return None
    sections = policy_transmission_sections(task, event, facts, calculations)
    reaction = ''.join(section['event_session_summary'] for section in sections if section.get('event_session_summary'))
    expanded = event_reading_enabled(task)
    conclusion = reaction + event['text'] if expanded else event['text'] + reaction
    fields = ('title', 'analysis', 'data_refs', 'source_refs', 'calculation_refs', 'risk')
    if expanded:
        fields += ('detail_analysis',)
    intended = [{key: section.get(key) for key in fields} for section in sections]
    actual = [{key: section.get(key) for key in fields} for section in draft['logics']]
    if actual != intended or draft['conclusion'] != conclusion:
        return None
    return dict(version=1, task_id=task['id'], research_version=task['version'], question_id=question['id'],
                input_hash=task['input_hash'], release_time=event['release_clock']['scheduled_at'], windows=windows,
                answer_hash=fingerprint(dict(conclusion=conclusion, sections=actual)))


def filing_directory_report(task, parameters):
    from .evidence import filing_directory_result

    directory = filing_directory_result(task, parameters)
    window = parameters['beginrDate'] + '至' + parameters['endrDate']
    prefix = parameters['codes'] + '在' + window + '的iFinD公告目录'
    ifind_status = directory.get('ifind_status', directory['status'])
    if ifind_status == 'unavailable':
        conclusion = prefix + '暂未取得可核对的结果。'
    elif ifind_status == 'empty':
        conclusion = prefix + '本次返回为空。'
    else:
        conclusion = prefix + f'有{len(directory["rows"])}条可核对记录。'
        if directory['rejected']:
            conclusion += f'另有{directory["rejected"]}处字段或范围异常。'
    notes = ['公告日期采用目录字段。目录用于定位披露，覆盖范围、首发时间和正文内容需独立核对。'] if directory.get('source_ref') else []
    if 'official' in directory:
        official = directory['official']
        if official is None:
            conclusion += '所用来源的完整覆盖尚未核实。'
        else:
            if official['complete']:
                conclusion += f'{official["name"]}已查完该区间的目录，共{len(official["rows"])}条。'
            else:
                count = len(official['source_refs'])
                conclusion += f'{official["name"]}已取得{count}页目录。' if count else f'{official["name"]}目录暂缺。'
                conclusion += official['gap']
            notes = ['公告日期采用所列目录字段。首发时刻与公告正文仍需依据原件核对。']
    return dict(title=parameters['codes'] + '公告目录核对', conclusion=conclusion, organization='fact',
                logics=[], risks=[], reader_notes=notes, workpaper_notes=[], filing_directory=directory)


def industry_collection_readout(collections):
    """Summarize the receipt projection, never a count of charted observations."""
    from .financial_sources import FINANCIAL_QUERY_PERIODS

    themes = list(dict.fromkeys(item['topic'] + item['discovery_kind'] for item in collections))
    title = (themes[0] if len(themes) == 1 else '行业与产业链') + '财务样本核对'
    lines = []
    period_labels = {day: label for label, day in FINANCIAL_QUERY_PERIODS.items()}
    for item in collections[:3]:
        period = item['requested_period']
        period_label = period[:4] + '年' + period_labels[period[4:]]
        scope = item['topic'] + item['discovery_kind']
        fields = []
        for field in item['fields']:
            name = {'归属于母公司所有者的净利润': '归母净利润'}.get(field['name'], field['name'])
            label = f'{name}{field["received"]}条'
            if field['missing']:
                label += f'，缺{field["missing"]}条'
            fields.append(label)
        count = item['returned_unique_codes']
        raw = f'{item["row_count"]}条、涉及{count}个证券代码' if item['row_count'] != count else f'{count}个证券代码'
        lines.append(f'{scope}{period_label}共返回{raw}，' + '、'.join(fields) + '。')
    if len(collections) > 3:
        lines.append('其他批次见数据与来源。')
    boundary = '上述批次尚未确认行业分类时点、完整名单、计量单位、币种及合并口径，无法计算行业覆盖率或比较金额。'
    return dict(title=title, text=''.join(lines) + boundary)


def _bank_profit_sections(peers, facts, calculations):
    from .draft_analysis import bank_profit_bridge

    breakdown = bank_profit_bridge(facts, calculations)
    selected = [(peer, breakdown[peer['code']]) for peer in peers if peer['code'] in breakdown]
    if not selected:
        return None

    def movement(value):
        if not value:
            return '持平'
        return f'{"增加" if value > 0 else "减少"}{abs(value)/Decimal(100000000):.1f}亿元'

    def rate_text(value):
        if value is None:
            return ''
        if not value:
            return '同比持平'
        return f'同比{"增长" if value > 0 else "下降"}{abs(value):.1f}%'

    income_text, credit_text, chart_rows = [], [], []
    income_calculations, profit_calculations = {}, {}
    for peer, bridge in selected:
        name = peer['name']
        interest, other, credit = (bridge[key] for key in ('net_interest', 'noninterest', 'credit_impact'))
        explanation = f'{name}净利息收入{movement(interest)}，非利息净收入{movement(other)}。'
        if interest > 0 and 0 <= other < interest:
            share = interest/(interest+other)*100
            explanation += f'净利息收入贡献约{share:.0f}%的营收增量。'
        if interest < 0 < other and interest+other < 0:
            share = other/abs(interest)*100
            explanation += f'非利息净收入抵消了约{share:.0f}%的利息收入缺口。'
        income_text.append(explanation)
        chart_rows.append(dict(code=peer['code'], name=name, calculations={
            key: item['id'] for key, item in bridge['calculations'].items()}))
        for item in bridge['calculations'].values():
            income_calculations[item['id']] = item
            profit_calculations[item['id']] = item
        profit = peer['fields']['ths_np_atoopc_stock']
        detail = f'{name}信用减值损失{movement(-credit)}'
        if observation := rate_text(profit['rate']):
            detail += f'，归母净利润{observation}'
        detail += '。'
        total = interest+other
        if total > 0 and credit < 0:
            detail += f'新增减值相当于营收增量的{abs(credit)/total*100:.0f}%。'
        if total < 0 and credit < 0:
            detail += '营收回落与减值增加共同压缩当期税前盈利空间。'
        credit_text.append(detail)
        profit_calculations[profit['calculation']['id']] = profit['calculation']
    income = _section('利息与非利息收入', (), income_calculations.values(), income_text)
    income['bank_profit_series'] = chart_rows
    income['workpaper_notes'] = ['非利息净收入为同一合并利润表营业收入减净利息收入，两期分别计算后作差。']
    quality = _section('信用减值与盈利分化', (), profit_calculations.values(), credit_text)
    quality['peer_summary_only'] = True
    quality['workpaper_notes'] = ['图中信用减值影响为两期利润表列报的带符号损益之差。溯源保留两期原值。']
    return dict(income=income, quality=quality, companies=[peer['code'] for peer, _ in selected])


def _peer_operating_sections(peers, facts, profitability, calculations):
    from .draft_analysis import PEER_NET_AFTER_GROSS, peer_gross_profit_bridge, peer_profit_components
    from .financial_sources import (PEER_FINANCIAL_FIELDS, PEER_OPERATING_FIELDS, PEER_PURCHASE_PAYMENTS,
                                    PEER_SALES_RECEIPTS)

    revenue, profit = PEER_FINANCIAL_FIELDS
    cost, cash = PEER_OPERATING_FIELDS
    operating_expenses = {'filing.peer_research_expense', 'filing.peer_administrative_expense',
                          'filing.peer_selling_expense'}
    evidence = {row['id']: row for row in facts}
    after_gross = {tuple(row['data_refs']): row for row in calculations if row['method'] == PEER_NET_AFTER_GROSS}
    margins, cash_rows, cash_copy, cash_calcs = [], [], [], []
    completed, diverging_cash = [], []

    def movement(amount):
        return f'{"增加" if amount > 0 else "减少"}{abs(amount)/Decimal(100000000):.1f}亿元' if amount else '持平'

    for peer in peers:
        fields, name = peer['fields'], peer['name']
        cost_calc = fields.get(cost, {}).get('calculation')
        revenue_calc = fields[revenue]['calculation']
        bridge = peer_gross_profit_bridge(revenue_calc, cost_calc, evidence)
        if bridge:
            profit_calc = fields[profit]['calculation']
            refs = tuple(ref for period in zip(revenue_calc['data_refs'], cost_calc['data_refs'], profit_calc['data_refs'],
                                               strict=True) for ref in period)
            margins.append((peer, bridge, revenue_calc, cost_calc, after_gross.get(refs)))
        if cash not in fields:
            continue
        before, after = fields[cash]['amounts']
        change = after-before
        if change:
            move = '升至' if change > 0 else '降至'
            copy = (f'{name}经营现金流净额由{before/Decimal(100000000):.1f}亿元{move}'
                    f'{after/Decimal(100000000):.1f}亿元。')
        else:
            copy = f'{name}经营现金流净额与上年同期持平，为{after/Decimal(100000000):.1f}亿元。'
        cash_calc = fields[cash]['calculation']
        cash_calcs.append(cash_calc)
        cash_rows.append(peer)
        changes, movements, cash_effect = [], {}, Decimal(0)
        for field, label, sign in ((PEER_SALES_RECEIPTS, '销售收款同比', 1),
                                   (PEER_PURCHASE_PAYMENTS, '采购付款同比', -1)):
            component = fields.get(field)
            if not component:
                continue
            component_calc = component['calculation']
            source_refs = {evidence[ref]['artifact_id'] for calc in (cash_calc, component_calc) for ref in calc['data_refs']}
            if len(source_refs) != 1:
                continue
            first, last = component['amounts']
            movements[field] = last-first
            changes.append(label+movement(last-first))
            cash_effect += sign*(last-first)
            cash_calcs.append(component_calc)
        sales_change = movements.get(PEER_SALES_RECEIPTS, Decimal(0))
        purchase_change = movements.get(PEER_PURCHASE_PAYMENTS, Decimal(0))
        diverges = fields[revenue]['rate']*change < 0
        if changes:
            copy += '，'.join(changes)
            if diverges and change < 0 and sales_change > 0:
                if purchase_change > sales_change:
                    copy += '，新增采购付款超过新增收款'
                elif purchase_change >= sales_change*Decimal('0.8'):
                    copy += '，新增收款大部分被新增采购付款吸收'
            copy += f'，其余经营收支净额同比{movement(change-cash_effect)}。'
        if diverges:
            diverging_cash.append(name)
        cash_copy.append(copy)
        if bridge:
            completed.append(peer['code'])

    summary = ''
    if margins:
        paragraphs, references, series, conversions, margin_notes = [], {}, [], [], []
        profit_details = False
        for peer, bridge, revenue_calc, cost_calc, remainder in margins:
            name = peer['name']
            rate_effect, revenue_effect = bridge['margin_effect'], bridge['revenue_effect']
            earlier, latest = bridge['prior_margin'], bridge['margin']
            direction = '升至' if latest > earlier else '降至'
            margin_copy = (f'毛利率由{earlier:.1f}%{direction}{latest:.1f}%'
                           if latest != earlier else f'毛利率维持在{latest:.1f}%')
            copy = (f'{name}毛利同比{movement(bridge["gross_change"])}。收入变动使毛利{movement(revenue_effect)}。'
                    f'{margin_copy}' + (f'，使毛利{movement(rate_effect)}。' if rate_effect else '。'))
            net = peer['fields'][profit]
            rate = net['rate']
            company = dict(code=peer['code'], name=name, calculations={revenue: revenue_calc['id'], cost: cost_calc['id']})
            if remainder is not None:
                start, end = net['amounts']
                profit_change = end-start
                net_effect = Decimal(remainder['exact'])
                components = peer_profit_components(remainder, calculations)
                drivers = sorted((field for field, part in components.items() if abs(part['effect']) >= Decimal(5000000)),
                                 key=lambda field: abs(components[field]['effect']), reverse=True)
                copy = f'{name}毛利同比{movement(bridge["gross_change"])}，{margin_copy}'
                if (set(drivers[:3]) == operating_expenses
                        and len({components[field]['effect'] > 0 for field in operating_expenses}) == 1):
                    total = sum(components[field]['change'] for field in operating_expenses)
                    research = components['filing.peer_research_expense']['change']
                    copy += (f'。研发、管理及销售费用合计{movement(total)}，其中研发费用{movement(research)}。'
                             f'归母净利润{movement(profit_change)}。')
                elif drivers:
                    changes = []
                    for field in drivers[:2]:
                        part = components[field]
                        label = part['label']
                        if label.endswith('减值损失'):
                            role = '增厚' if part['effect'] > 0 else '抵减'
                            changes.append(f'{label.removesuffix("损失")}同比{role}利润{abs(part["effect"])/Decimal(100000000):.1f}亿元')
                        else:
                            changes.append(label+movement(part['change']))
                    copy = (f'{name}毛利同比{movement(bridge["gross_change"])}，'+'、'.join(changes)
                            +f'，归母净利润{movement(profit_change)}。')
                    margin_notes.append(name+margin_copy)
                elif abs(net_effect) >= Decimal(5000000):
                    direction = '增厚' if net_effect > 0 else '抵减'
                    copy += (f'。毛利以下项目净变动{direction}{abs(net_effect)/Decimal(100000000):.1f}亿元，'
                             f'归母净利润{movement(profit_change)}。')
                else:
                    copy += f'，归母净利润{movement(profit_change)}。'
                company['calculations'][PEER_NET_AFTER_GROSS] = remainder['id']
                references[remainder['id']] = remainder
                references.update({item['calculation']['id']: item['calculation'] for item in components.values()})
                profit_details = profit_details or bool(components)
                conversions.append((net_effect, name, bridge['gross_change']))
            elif rate is not None and rate*bridge['gross_change'] < 0:
                change_text = f'{"增长" if rate > 0 else "下降"}{abs(rate):.1f}%'
                copy += f'同期归母净利润同比{change_text}。'
            paragraphs.append(copy)
            series.append(company)
            references.update({row['id']: row for row in (revenue_calc, cost_calc, net['calculation'])})
        profitability = _section('毛利增量与归母净利润' if conversions else '收入、成本与利润分化',
                                 (), list(references.values()), paragraphs)
        profitability['peer_summary_only'] = True
        profitability['workpaper_notes'] = ['毛利为同一合并利润表的营业收入减营业成本。收入贡献按上年毛利率测算，'
                                            '毛利率贡献按当期收入测算，两项相加等于毛利同比变动。']
        profitability['peer_operating_series'] = series
        if conversions:
            profitability['workpaper_notes'].append('毛利以下项目净变动＝归母净利润同比差额－毛利同比差额。')
            if profit_details:
                profitability['peer_profit_details'] = True
                profitability['detail_analysis'] = ('费用、所得税和少数股东损益按扣减方向计入归母净利润。投资收益及减值按报表列示的损益方向计入。'
                                                     '其余项目净额由毛利以下总变动扣除表中列明项目得出。')
                if margin_notes:
                    profitability['detail_analysis'] = '。'.join(margin_notes)+'。'+profitability['detail_analysis']
            else:
                profitability['detail_analysis'] = ('毛利以下项目的净变动可从费用、投资及其他损益、所得税和少数股东损益逐项复核。'
                                                     '其中费用随收入增长的部分与一次性损益对后续盈利的影响不同。')
        if len(conversions) == len(peers) and len(conversions) > 1:
            conversions.sort()
            lowest, name, gross = conversions[0]
            if lowest <= Decimal(-5000000) and lowest < conversions[1][0]:
                if gross > 0 and gross*Decimal('0.5') <= -lowest < gross:
                    summary = f'{name}毛利以下项目的同比净变动抵减了约{-lowest/gross:.0%}的新增毛利。'
                else:
                    summary = f'{name}毛利以下项目对利润的抵减额居样本首位。'
    sections = [profitability]
    if cash_rows:
        cash_section = _section('收入与现金回流', (), cash_calcs, cash_copy)
        cash_section['peer_summary_only'] = True
        if len(cash_calcs) > len(cash_rows):
            cash_section['workpaper_notes'] = ['现金流项目取同一份合并现金流量表。其余经营收支净额的同比变化，'
                                              '为经营现金流净额同比变化扣除已列示项目按收支方向计算的影响。']
        sections.append(cash_section)
    return sections, completed, diverging_cash, summary


def peer_financial_readout(task, facts, calculations):
    """Explain the reported growth and profitability of comparable companies."""
    from .draft_analysis import peer_financial_results, peer_revenue_breakdown
    from .financial_sources import PEER_FINANCIAL_FIELDS, PEER_REVENUE_COMPONENTS, peer_financial_scope
    from .index_sources import broker_reading_enabled

    scope = peer_financial_scope(task)
    if not scope:
        return None
    calculations = tuple(calculations)
    peers = peer_financial_results(task, facts, calculations, scope)
    revenue, profit = PEER_FINANCIAL_FIELDS
    year = scope['periods'][-1][:4]
    sample = scope.get('industry_sample')
    expanded_broker = bool(sample and scope.get('revenue_bridge') and broker_reading_enabled(task))
    group = sample['industry'] if sample else ('券商' if re.search(r'券商|证券(?:行业|板块)', task['question']) else '公司')
    title = year+'年上半年'+group+('经营分化' if sample else '样本业绩对照')
    selection = ''
    missing = sorted(set(scope['codes']) - {peer['code'] for peer in peers})
    if sample:
        names = {member['code']: re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])', '', member['name'])
                 for member in sample['selected']}
        for peer in peers:
            peer['name'] = names[peer['code']]
        count = len(names)
        weight = Decimal(sample['selected_weight'])
        selection = (f'按{sample["workbook_date"]}的{sample["index_name"]}权重选取'
                     f'{count}家上市公司，合计占指数权重{weight:.1f}%。指数共{sample["index_size"]}只成分股。')
        missing = [names[code] for code in missing]
    if len(peers) < 2:
        if not sample:
            return None
        names = '、'.join(member['name'] for member in sample['selected'])
        conclusion = (f'选取{names}作为研究样本，已取得{len(peers)}家公司的可比两期财务数据。'
                      '目前的样本数据不足以判断经营分化。')
        selected = _section('样本与财务覆盖', (), [item['calculation'] for peer in peers
                            for item in peer['fields'].values()], [selection])
        selected['source_refs'] = [sample['source_ref']]
        return dict(title=title, conclusion=conclusion, sections=[selected],
                    risks=['、'.join(missing)+'缺少可比的两期数据。'])

    def rate(peer):
        return peer['fields'][revenue]['rate']

    def revenue_change(peer):
        before, after = peer['fields'][revenue]['amounts']
        return (after - before) / Decimal(100000000)

    operating_only = [peer for peer in peers if (peer['fields'][revenue].get('comparison_note') or {}).get('kind')
                      == 'revenue_excludes_investment']
    separate_basis = expanded_broker and 0 < len(operating_only) < len(peers)
    ranked = [peer for peer in peers if peer not in operating_only] if separate_basis else peers
    latest = max(map(rate, ranked))
    delta = max(map(revenue_change, ranked))
    rate_leaders = [peer for peer in ranked if rate(peer) == latest]
    delta_leaders = [peer for peer in ranked if revenue_change(peer) == delta]
    rate_names = '、'.join(peer['name'] for peer in rate_leaders)
    delta_names = '、'.join(peer['name'] for peer in delta_leaders)
    rank_differs = delta > 0 and rate_names != delta_names
    if len(ranked) == 1:
        reading = f'同比{_direction(latest)}{abs(latest):.1f}%' if latest else '同比持平'
        opening = f'{rate_names}的营收{reading}。'
    elif latest > 0 and delta > 0 and not rank_differs:
        opening = f'{rate_names}营收同比增长{latest:.1f}%，增加{delta:.1f}亿元，增速与增量均居样本首位。'
    elif latest > 0:
        opening = f'{rate_names}营收同比增长{latest:.1f}%，居样本首位。'
    elif latest < 0:
        opening = f'{rate_names}营收同比下降{abs(latest):.1f}%，样本中降幅最小。'
    else:
        opening = f'{rate_names}营收同比持平。'
    if rank_differs and len(rate_leaders) == 1 and revenue_change(rate_leaders[0]) != delta:
        leader_increase = revenue_change(rate_leaders[0])
        opening += f'按新增规模计，{delta_names}增加{delta:.1f}亿元，{rate_names}同期增加{leader_increase:.1f}亿元。'
        if scope.get('operating_drivers') and len(delta_leaders) == 1:
            base, _ = delta_leaders[0]['fields'][revenue]['amounts']
            opening += (f'{delta_names}上年同期收入为{base/Decimal(100000000):.1f}亿元，'
                        '较大的营收基数使较低的增速仍对应更大的营收增量。')
        else:
            opening += '增速领先与增量领先分属两家，比较增长贡献时应同时看基期规模。'
    elif rank_differs:
        opening += f'{delta_names}同期营收增加{delta:.1f}亿元，新增规模居样本首位。'
    if separate_basis:
        opening = '在营业收入计入证券业务投资损益的样本中，' + opening.replace('样本首位', '首位')
    if sample and len(peers) <= 4:
        others = [
            f'{peer["name"]}{"增长" if rate(peer) > 0 else "下降"}{abs(rate(peer)):.1f}%'
            if rate(peer) else f'{peer["name"]}持平' for peer in ranked if peer not in rate_leaders]
        if others:
            opening += '、'.join(others)+'。'
    if separate_basis:
        for peer in operating_only:
            reading = f'同比{_direction(rate(peer))}{abs(rate(peer)):.1f}%' if rate(peer) else '同比持平'
            opening += f'{peer["name"]}披露的营业总收入{reading}，其口径剔除了证券业务的投资收益和公允价值变动。'
    rev_direction = ('样本公司营业收入均同比增长' if all(rate(peer) > 0 for peer in peers)
                     else '样本公司的营收增速存在分化')
    growth_values = [peer['fields'][field]['calculation'] for peer in peers for field in (revenue, profit)]
    growth = _section('收入增速与增量', (), growth_values, [opening] + ([selection] if sample else []))
    if sample:
        growth['source_refs'] = [sample['source_ref']]
        weights = '、'.join(f'{member["name"]}{Decimal(str(member["weight"])):.2f}%'
                          for member in sample['selected'])
        growth['workpaper_notes'] = [f'以同一份权重原表从大到小选样，同权重按证券代码排序。入选权重为{weights}。'
                                     '财务比较使用当年及上年同口径半年报数据。']
    if expanded_broker:
        growth['detail_analysis'] = ('样本按指数权重选定，主要反映大型公司的经营轮廓。评估行业整体，需要按同一收入口径'
                                     '汇总更多成分公司的数据，并将市值权重与营收、利润规模分开考察。')
    growth['peer_growth_series'] = [dict(code=peer['code'], name=peer['name'], calculations={
        field: item['calculation']['id'] for field, item in peer['fields'].items()}) for peer in peers]

    margins, margin_levels = defaultdict(list), []
    for peer in peers:
        if min(peer['fields'][revenue]['amounts']) <= 0:
            continue
        levels = [Decimal(execute('raw_percent_v1', dict(a=net, b=sales))['exact'])
                  for net, sales in zip(peer['fields'][profit]['amounts'], peer['fields'][revenue]['amounts'], strict=True)]
        change = levels[1]-levels[0]
        margins[_direction(change)].append(peer['name'])
        margin_levels.append((peer['name'], *levels, change))
    observations = [f'{"、".join(names)}的归母净利润率同比{direction}' for direction, names in margins.items()]
    profit_copy = '，'.join(observations) + '。' if observations else '样本两期收入存在非正值，利润表现以归母利润变动额衡量。'
    if expanded_broker and observations:
        profit_copy = '按各自披露的营收口径计算，' + profit_copy
    largest_scope = {peer['name'] for peer in ranked}
    comparable_margins = [row for row in margin_levels if row[0] in largest_scope]
    if comparable_margins and (len(comparable_margins) > 1 or not separate_basis):
        name, old, new, change = max(comparable_margins, key=lambda item: abs(item[-1]))
        if change and expanded_broker:
            profit_copy += f'可比样本中，{name}的归母净利率由{old:.1f}%变为{new:.1f}%。'
        elif change:
            profit_copy += f'{name}的利润率变动最大，从{old:.1f}%变为{new:.1f}%，同比{_direction(change)}{abs(change):.1f}个百分点。'
    if rank_differs and '下降' in margins and any(peer['name'] in margins['下降'] for peer in rate_leaders):
        profit_copy += '增速居前的公司每百元收入对应的归母净利润反而下降，报告期的收入增长与盈利转化出现分化。'
    boundary = ('样本集中于指数权重较高的公司，行业中小公司的业务结构和经营表现可能不同。'
                if sample else '本次结论限于所选可比公司，行业整体趋势还需检验更广的样本。')
    if missing:
        boundary += '、'.join(missing) + '缺少可比的两期数据，未纳入排序。'
    restated = [peer['name'] for peer in peers if any(row['restated'] for row in peer['fields'].values())]
    if restated:
        boundary += '、'.join(restated) + '以上年同期重述后的数据为比较基数。'
    profit_rates = [(peer['fields'][profit]['rate'], peer['name']) for peer in peers
                    if peer['fields'][profit]['rate'] is not None]
    if len(profit_rates) > 1 and (highest_profit_rate := max(rate for rate, _ in profit_rates)) > 0:
        leaders = '、'.join(name for rate, name in profit_rates if rate == highest_profit_rate)
        profit_copy = f'{leaders}归母净利润同比增长{highest_profit_rate:.1f}%，居样本首位。'+profit_copy
    profit_paragraphs = [profit_copy]
    profit_calculations, profit_notes, merger_base = [], [], []
    for peer in peers:
        reported = peer['fields'][profit]
        note = reported.get('comparison_note') or {}
        adjusted = peer['adjusted_profit']
        if note.get('kind') != 'prior_merger_gain' or not adjusted or reported['rate'] is None or adjusted['rate'] is None:
            continue
        name = peer['name']
        net_rate, adjusted_rate = reported['rate'], adjusted['rate']
        reported_move = f'{"增长" if net_rate >= 0 else "下降"}{abs(net_rate):.1f}%'
        adjusted_move = f'{"增长" if adjusted_rate >= 0 else "下降"}{abs(adjusted_rate):.1f}%'
        profit_paragraphs.append(f'{name}归母净利润同比{reported_move}，扣非归母净利润同比{adjusted_move}。'
                                '上年同期确认并购收益，抬高了归母净利润的比较基数。')
        profit_calculations.append(adjusted['calculation'])
        profit_notes.append(note)
        if net_rate < rate(peer) and adjusted_rate > net_rate:
            merger_base.append(name)
    profitability = _section('利润率与盈利弹性', (), growth_values+profit_calculations, profit_paragraphs)
    if expanded_broker and profit_notes:
        profitability['detail_analysis'] = ('比较并购前后的盈利能力时，把负商誉等一次性损益、纳入合并范围的增量和原业务增长分别看。'
                                             '若控股范围差异收敛后，经纪净佣和融资利息继续增加，经营利润改善的依据更充分。')
    if profit_notes:
        profitability['source_refs'] = list(dict.fromkeys(note['source_ref'] for note in profit_notes))
        profitability['workpaper_notes'] = [f'半年报{note["part"]}：{note["quote"]}' for note in profit_notes]
    sections = [growth, profitability]
    bridge = peer_revenue_breakdown(peers, facts, calculations) if scope.get('revenue_bridge') else []
    market_driven, fee_interest_driven, operating_only_driven = [], [], []
    if bridge:
        def movement(value):
            if not value:
                return '持平'
            return f'{"增加" if value > 0 else "减少"}{abs(value)/Decimal(100000000):.1f}亿元'

        fee, interest, investment, fair_value = PEER_REVENUE_COMPONENTS
        paragraphs, references = [], []
        for company in bridge:
            parts = company['parts']
            amounts = {key: value['change'] for key, value in parts.items()}
            references.extend(value['calculation'] for value in parts.values())
            if company['operating_only']:
                operating = amounts[fee]+amounts[interest]
                share = (operating/company['revenue_delta']*100) if company['revenue_delta'] > 0 and operating > 0 else None
                copy = (company['name']+'的手续费及佣金净收入'+movement(amounts[fee])+'，利息净收入'+movement(amounts[interest]))
                if share is not None:
                    copy += f'，两项合计占营收增量的{share:.0f}%'
                    if share > 50:
                        operating_only_driven.append(company['name'])
                ending = '。' if separate_basis else '。公司的营业总收入采用剔除证券业务投资收益与公允价值变动损益的披露口径。'
                paragraphs.append(copy+ending)
                continue
            market = amounts[investment]+amounts[fair_value]
            operating = amounts[fee]+amounts[interest]
            if company['revenue_delta'] > 0 and market > max(0, operating, company['residual']):
                market_driven.append(company['name'])
            elif company['revenue_delta'] > 0 and operating > max(0, market, company['residual']):
                fee_interest_driven.append(company['name'])
            trade = '投资收益和公允价值变动收益合计'+movement(market)
            if amounts[investment]*amounts[fair_value] < 0:
                trade += ('。其中投资收益'+movement(amounts[investment])
                          +'，公允价值变动收益'+movement(amounts[fair_value])+'，两项方向相反')
            elif company['revenue_delta'] > 0 and max(amounts, key=amounts.get) == fair_value:
                share = amounts[fair_value]/company['revenue_delta']*100
                trade += f'。其中公允价值变动收益{movement(amounts[fair_value])}，相当于营收增量的{share:.0f}%'
            operating_copy = ('的手续费及佣金净收入与利息净收入合计'+movement(operating)
                              if expanded_broker else '的手续费及佣金净收入'+movement(amounts[fee]))
            message = company['name']+operating_copy+'，'+trade+'。'
            if abs(company['residual']) > abs(company['revenue_delta'])*Decimal('0.1') and company['residual']:
                message += '上述四项之外的收入项目合计'+movement(company['residual'])+'。'
            paragraphs.append(message)
        contribution = _section('营收增量的业务来源', (), references, paragraphs)
        if expanded_broker:
            contribution['detail_analysis'] = ('投资收益和公允价值变动要结合看，前者受处置和兑现节奏影响，后者随持仓价格变动。'
                                                '手续费净收入反映佣金合计。后续若佣金增长延续而市值变动回落，'
                                                '两项贡献的相对大小将改变对收入持续性的判断。')
        contribution['workpaper_notes'] = ['列示项目取同一合并利润表两期差额。手续费及佣金净收入按合计列示，'
                                          '不与经纪、投行或资管手续费子项重复相加。']
        plotted = [row for row in bridge if not row['operating_only']]
        if len(plotted) < len(bridge):
            contribution['workpaper_notes'].append('图中列示计入投资收益与公允价值损益的营业收入口径。其他披露口径保留可比的公司内变动。')
        contribution['peer_revenue_series'] = [dict(code=row['code'], name=row['name'],
            calculations={field: item['calculation']['id'] for field, item in row['parts'].items()}) for row in plotted]
        sections.insert(1, contribution)
        boundary += '公允价值变动受市场价格影响，可持续收入还需结合分部数据和后续期间判断。'
        if len(bridge) < len(peers):
            boundary += '营收项目拆解限于具有同口径两期合并利润表的公司。'
        if len(plotted) >= 2:
            growth.pop('peer_growth_series', None)
            growth['peer_summary_only'] = True
            profitability['peer_summary_only'] = True
    elif scope.get('revenue_bridge'):
        boundary += '缺少可比的收入项目，两期拆解仅保留至合并营收。'
    bank = _bank_profit_sections(peers, facts, calculations) if scope.get('bank_profit_bridge') else None
    if bank:
        if len(bank['companies']) >= 2:
            growth.pop('peer_growth_series', None)
            growth['peer_summary_only'] = True
        sections = [growth, bank['income'], bank['quality']]
        boundary += '后续盈利持续性取决于净息差和信用成本。资产质量需结合不良生成率、贷款迁移和拨备覆盖率判断。'
        if len(bank['companies']) < len(peers):
            boundary += '利息收入和减值的比较限于已取得可比合并数据的银行。'
    elif scope.get('bank_profit_bridge'):
        boundary += '净利息收入和信用减值尚未形成可比序列，本次比较保留至营业收入及归母净利润。'
    operating, diverging_cash, profit_summary = [], [], ''
    if scope.get('operating_drivers'):
        operating_sections, operating, diverging_cash, profit_summary = _peer_operating_sections(peers, facts, profitability, calculations)
        sections = [growth, *operating_sections]
        boundary += '经营现金流还受经营性往来和付款时点影响，量价与渠道贡献需结合各公司分项披露判断。'
        if len(operating) < len(peers):
            boundary += '毛利及现金回流的比较限于披露口径可比的样本。'
    if not rank_differs:
        conclusion = rev_direction + f'，{rate_names}营收增速居样本首位。'
    else:
        conclusion = rev_direction + f'，{rate_names}增速最快，{delta_names}营收增量最大。'
    if separate_basis and len(ranked) > 1:
        conclusion = f'{rate_names}的营收增速位居可比样本首位。'
    elif separate_basis:
        conclusion = rev_direction+'。'
    if expanded_broker:
        drivers = []
        if market_driven:
            drivers.append('、'.join(market_driven)+'最大的营收增量来自投资收益与公允价值变动')
        if fee_interest_driven:
            drivers.append('、'.join(fee_interest_driven)+'的手续费及利息净收入合计贡献高于投资相关损益')
        if operating_only_driven:
            drivers.append('、'.join(operating_only_driven)+'披露的营收增量主要来自手续费及利息净收入')
        if drivers:
            conclusion += '，'.join(drivers)+'。'
        if merger_base:
            conclusion += '、'.join(merger_base)+'归母净利润增速低于营收，上年同期并购收益抬高利润基数。'
    if bank and any(peer['fields'][profit]['rate'] is not None and peer['fields'][profit]['rate'] < 0 for peer in peers):
        declining = '、'.join(peer['name'] for peer in peers if peer['fields'][profit]['rate'] is not None
                             and peer['fields'][profit]['rate'] < 0)
        conclusion += f'{declining}归母净利润同比回落。'
    conclusion += profit_summary
    if diverging_cash:
        conclusion += '、'.join(diverging_cash)+'的收入与经营现金流变动方向相反。'
    expected = len(scope['codes'])
    coverage = None
    if sample and len(peers) == expected:
        business = bank['companies'] if bank else operating or [row['code'] for row in bridge]
        if set(business) == set(scope['codes']):
            coverage = dict(sample_source=sample['source_ref'], sample_date=sample['workbook_date'],
                            codes=scope['codes'], periods=scope['periods'])
    return dict(title=title, conclusion=conclusion, sections=sections, risks=[boundary], coverage=coverage)


def verified_industry_answer(task, draft):
    """Recognize a saved operating comparison without approving an investment thesis."""
    from .index_sources import broker_reading_enabled, industry_operating_scope, operating_report_question
    from .planning import input_fingerprint

    if (task.get('run_mode') not in ('direct', 'local_draft') or draft.get('origin') != 'data_outline'
            or not operating_report_question(task) or industry_operating_scope(task, direct=True) is None
            or task['input_hash'] != input_fingerprint(task)):
        return None
    question, = task['questions']
    if question.get('evidence_needs') or question.get('method_needs') or not question['required']:
        return None
    readout = peer_financial_readout(task, draft['facts'], draft['calculations'])
    if not readout or not readout.get('coverage') or readout['conclusion'] != draft['conclusion']:
        return None
    fields = ('title', 'analysis', 'data_refs', 'calculation_refs')
    if broker_reading_enabled(task) and any(item.get('detail_analysis') for item in readout['sections']):
        fields += ('detail_analysis',)
    intended = [{field: item.get(field) for field in fields} for item in readout['sections']]
    delivered = [{field: item.get(field) for field in fields} for item in draft['logics']]
    if delivered != intended or readout['coverage']['sample_source'] not in draft['logics'][0].get('source_refs', []):
        return None
    return dict(version=1, task_id=task['id'], research_version=task['version'], input_hash=task['input_hash'],
                question_id=question['id'], scope=readout['coverage'],
                answer_hash=fingerprint(dict(conclusion=draft['conclusion'], sections=delivered)))


def etf_trading_readout(scope, facts, calculations, display_names=None):
    from .etf_trading import CELL_KIND

    terms = scope['trading']
    addresses = {row['id']: row for row in facts if row.get('locator', {}).get('kind') == CELL_KIND}
    calcs = [row for row in calculations if row.get('data_refs') and all(ref in addresses for ref in row['data_refs'])]
    codes = scope['codes']
    display_names = display_names or {}
    holding = scope.get('holding')

    def by_kind(kind):
        return {row['code']: row for row in calcs if row.get('comparison_kind') == kind}

    def name(code):
        return display_names.get(code) or next((row['object_label'] for row in addresses.values() if row['code'] == code), code)

    def section(title, calculations, paragraphs):
        refs = list(dict.fromkeys(ref for row in calculations for ref in row['data_refs']))
        data = [addresses[ref] for ref in refs]
        item = _section(title, data, calculations, paragraphs)
        item['source_refs'] = list(dict.fromkeys(row['artifact_id'] for row in data))
        item['etf_interim_readout'] = True
        return item

    sections, summary, coverage = [], [], {}
    turnover = by_kind('etf_turnover_mean')
    ticket = by_kind('etf_ticket_participation')
    daily_ratios = [row for row in calcs if row.get('comparison_kind') == 'etf_daily_turnover_multiple']
    shares = {tuple(calc['dates']) for calc in turnover.values()}
    common = len(codes) >= 2 and not scope.get('unresolved') and set(turnover) == set(codes) and len(shares) == 1
    size = 0
    turnover_leader = None
    if common:
        days, = shares
        size = len(days)
    if terms['turnover']:
        turnover_text = []
        if common:
            amounts = '，'.join(f'{name(code)}约{Decimal(turnover[code]["exact"])/Decimal(100000000):.2f}亿元'
                               for code in codes)
            turnover_text.append(f'{days[0]}至{days[-1]}共有{size}个可比交易日。样本日均成交额分别为{amounts}。')
            if size == terms['sessions'] and not terms['unresolved_window']:
                coverage['turnover_days'] = list(days)
                lead = max(Decimal(calc['exact']) for calc in turnover.values())
                leaders = [code for code in codes if Decimal(turnover[code]['exact']) == lead]
                if len(leaders) == 1:
                    turnover_leader = leaders[0]
                    summary.append(name(leaders[0])+'的样本日均成交额较高。')
            elif terms['unresolved_window']:
                turnover_text.append('这里列的是近期共同样本，指定期间的成交额仍需另行核对。')
            else:
                turnover_text.append(f'可比样本少于要求的{terms["sessions"]}个交易日。')
        elif turnover:
            for code, calc in turnover.items():
                turnover_text.append(f'{name(code)}于{calc["period"]}有{len(calc["dates"])}条成交额，'
                                     f'样本日均约{Decimal(calc["exact"])/Decimal(100000000):.2f}亿元。')
            turnover_text.append('两边交易日覆盖不同，执行比较需使用共同样本。')
        else:
            turnover_text.append('本次尚未取得可复算的交易所成交额序列。')
        if common and terms['ticket'] and set(ticket) == set(codes):
            ratio = '、'.join(f'{name(code)}约{ticket[code]["display"]}%' for code in codes)
            amount = _decimal_text(Decimal(terms['ticket'])/10000)
            turnover_text.append(f'{amount}万元委托占样本日均成交额的比例分别为{ratio}。'
                                 '这个比例用于衡量委托量级，实际可成交量取决于委托时段的对手盘。')
        if (not holding and common and {row['period'] for row in daily_ratios} == set(days)
                and len(daily_ratios) == len(days)):
            top, bottom = (pick(daily_ratios, key=lambda row: Decimal(row['exact'])) for pick in (max, min))
            leader = name(top['code'])
            comparator = name(next(code for code in codes if code != top['code']))
            dominant = sum(Decimal(row['exact']) > 1 for row in daily_ratios)
            turnover_text.append(f'{leader}在{dominant}个共同交易日的成交额高于{comparator}。'
                                 f'逐日比较，两只基金的成交额之比约为{Decimal(bottom["exact"]):.0f}至'
                                 f'{Decimal(top["exact"]):.0f}倍，{top["period"]}差距最大。')
        sections.append(section('二级市场成交活跃度', list(turnover.values())+list(ticket.values())+daily_ratios,
                                turnover_text))
    if terms['premium']:
        premiums = by_kind('etf_exchange_close_premium')
        days = {row['period'] for row in premiums.values()}
        if len(codes) >= 2 and not scope.get('unresolved') and set(premiums) == set(codes) and len(days) == 1:
            day, = days
            prices = []
            for code in codes:
                bp = (Decimal(premiums[code]['exact'])*100).quantize(Decimal('0.1'))
                prices.append(name(code)+('溢价约' if bp > 0 else '折价约')+_decimal_text(abs(bp))+'个基点'
                              if bp else name(code)+'收盘接近单位净值')
            compared = '，'.join(prices)
            if holding:
                detail = [f'{day}为最近共同估值日，{compared}。实际下单时应比较当时报价相对净值的偏离和券商费用。']
            else:
                detail = [f'在最近能配对收盘价和官方单位净值的共同估值日{day}，{compared}。'
                          '若计划买入，收盘溢价可用于复核当日价格相对净值的偏离，成交时仍需核对报价与净值时点。']
            if not terms['turnover']:
                values = [Decimal(calc['exact']) for calc in premiums.values()]
                if min(values) > 0:
                    summary.append('样本基金当日收盘价均高于各自单位净值。')
                elif max(values) < 0:
                    summary.append('样本基金当日收盘价均低于各自单位净值。')
                elif min(values) < 0 < max(values):
                    summary.append('样本基金当日收盘折溢价方向不同。')
            coverage['valuation_day'] = day
        else:
            detail = ['可比的收盘折溢价需要两只基金在同一估值日的收盘价与官方单位净值。'
                      '本次同日配对覆盖不足。']
        if holding and terms['turnover'] and not terms['execution']:
            sections[-1] = section('场内成交与买入成本', [*turnover.values(), *daily_ratios, *premiums.values()],
                                   turnover_text+detail)
        else:
            sections.append(section('同日收盘折溢价', list(premiums.values()), detail))
    if terms['execution']:
        sections.append(section('大额交易的执行条件', (), (
            '样本成交额可以用于筛查委托量级，盘后折溢价可以复核估值偏离。'
            '判断能否一次执行，需要再取得拟交易时段同步的买卖价差、对手盘分档数量及可接受价格范围。'
            '在获得这些信息前，无法据日均成交额判断一笔委托的成交比例与冲击成本。',)))
        summary.append('实际执行能力仍待同步盘口核验。')
    complete = (not scope.get('unresolved') and not terms['execution']
                and (not terms['turnover'] or 'turnover_days' in coverage)
                and (not terms['premium'] or 'valuation_day' in coverage))
    if ((terms['turnover'] and 'turnover_days' not in coverage)
            or (terms['premium'] and 'valuation_day' not in coverage)):
        summary.append('成交和净值比较以正文列示的日期与覆盖范围为准。')
    return dict(sections=sections, conclusion=''.join(summary), coverage=coverage if complete else None,
                turnover_leader=turnover_leader)


def etf_product_readout(task, facts, calculations):
    from .etf_comparison import (ASSETS, BENCHMARK, CUSTODY, FEE_SUM, MANAGEMENT, PERFORMANCE, REQUIRED_FIELDS,
                                 TRACKING_ERROR, TRACKING_GAP, common_benchmark, comparison_scope,
                                 product_observations)

    scope = comparison_scope(task)
    if scope is None:
        return None
    products = product_observations(facts, scope) if scope.get('year') else {}
    holding = scope.get('holding')
    benchmark = common_benchmark(products, scope) if scope.get('year') else None
    prefix = benchmark[0].removesuffix('指数') if benchmark else ''
    title = prefix+'ETF费率与跟踪表现比较' if scope.get('year') else 'ETF二级市场成交与估值比较'
    if scope.get('year') and scope.get('trading'):
        title = prefix+'ETF费率、跟踪与二级市场比较'
    if holding:
        title = prefix+'ETF长期持有比较'
    rows = list(products.values())
    requested_names = scope.get('names', ())
    unresolved = scope.get('unresolved', ())
    identity_refs = list(dict.fromkeys(item['source_ref'] for item in scope.get('resolved', ())))
    asset_rows = [product[ASSETS] for product in rows if ASSETS in product]
    eligible = {row['id'] for product in rows for row in product.values()}
    calcs = [calc for calc in calculations if set(calc['data_refs']) <= eligible]
    fees = {calc['code']: calc for calc in calcs if calc['method'] == FEE_SUM}
    gaps = {calc['code']: calc for calc in calcs if calc.get('comparison_kind') == TRACKING_GAP}
    coverage = None

    def label(product):
        return next(iter(product.values()))['object_label']

    def scoped_section(title, fields, values, paragraphs):
        data = [row for product in rows for field, row in product.items() if field in fields]
        section = _section(title, data, values, paragraphs)
        section['source_refs'] = list(dict.fromkeys(row['artifact_id'] for row in data))
        section['etf_interim_readout'] = True
        return section

    def percentage(value):
        return _decimal_text(Decimal(str(value)))

    remarks = []
    if fees and not unresolved and len(scope['codes']) >= 2 and len(fees) == len(scope['codes']):
        rates = {Decimal(calc['exact']) for calc in fees.values()}
        if len(rates) == 1 and all({MANAGEMENT, CUSTODY} <= product.keys() for product in rows):
            amounts = {(product[MANAGEMENT]['raw_value'], product[CUSTODY]['raw_value']) for product in rows}
            if len({tuple(map(Decimal, pair)) for pair in amounts}) == 1:
                management, custody = next(iter(amounts))
                rate = percentage(next(iter(rates)))
                named = '、'.join(f'{label(products[code])}（{code}）' for code in scope['codes'])
                remarks.append(f'{named}的年管理费率均为{percentage(management)}%，'
                               f'年托管费率均为{percentage(custody)}%，两项合计每年{rate}%。'
                               '按前一日基金资产净值逐日计提，费用金额随资产净值和持有天数变化。')
                if holding:
                    annual_cost = percentage(Decimal(rate)*1000)
                    remarks.append(f'按平均持有净值10万元测算，两项费用每年约{annual_cost}元，已通过日常净值反映。')
                else:
                    remarks.append('以基金净值衡量持有表现时，管理费与托管费已通过日常净值反映。'
                                   '两项费率相同的情况下，选品更应看组合跟踪管理与二级市场执行成本。')
                fee_summary = f'两项固定费率合计均为每年{rate}%。'
            else:
                remarks.append('样本基金费率结构有差异，管理费与托管费合计年费率相同。')
                fee_summary = '样本基金管理费与托管费合计年费率相同。'
        else:
            facts_text = '，'.join(f'{label(products[code])}为{percentage(calc["exact"])}%'
                                  for code, calc in fees.items())
            remarks.append('按披露费率合计，'+facts_text+'。费率差适用于同等持有基数和时长的费用比较。')
            fee_summary = '管理费与托管费合计年费率存在差异。'
    else:
        fee_summary = ''
        if fees:
            remarks.append('已披露的管理费与托管费合计年费率中，'+'，'.join(
                f'{label(products[code])}为{percentage(calc["exact"])}%' for code, calc in fees.items())+'。')
    if holding and fee_summary:
        remarks[0] = f'按{scope["year"]}年中报列示的费率，'+remarks[0]
        fee_summary = '中报列示的'+fee_summary
    if scope.get('fees_only') and not unresolved:
        complete = len(scope['codes']) >= 2 and set(fees) == set(scope['codes'])
        periods = f'{scope["year"]}年中报披露，'
        if complete and len({tuple(Decimal(product[field]['raw_value']) for field in (MANAGEMENT, CUSTODY))
                             for product in rows}) == 1:
            detail = [periods+remarks[0]]
        else:
            detail = []
            for code in scope['codes']:
                if product := products.get(code):
                    parts = [f'{name}{percentage(product[field]["raw_value"])}%'
                             for field, name in ((MANAGEMENT, '年管理费率'), (CUSTODY, '年托管费率'))
                             if field in product]
                    if code in fees:
                        parts.append(f'合计年费率{percentage(fees[code]["exact"])}%')
                    if parts:
                        detail.append(f'{label(product)}（{code}）'+ '，'.join(parts)+'。')
            if detail:
                detail = [periods+''.join(detail)]
        section = scoped_section('ETF管理费与托管费', {MANAGEMENT, CUSTODY}, fees.values(),
                                 detail or ('可比的中报费率尚未齐备。',))
        section['source_refs'] = list(dict.fromkeys([*section['source_refs'], *identity_refs]))
        if complete:
            coverage = dict(codes=scope['codes'], period=scope['period'], sources=section['source_refs'])
            subject = '两只ETF' if len(scope['codes']) == 2 else '样本ETF'
            fee_summary = subject+'中报披露的'+fee_summary.removeprefix('样本基金')
        title = prefix+'ETF年费率比较'
        return dict(title=title, conclusion=fee_summary, sections=[section], risks=[], coverage=coverage)

    tracking_summary = ''
    tracking_leader = None
    tracking = []
    if benchmark:
        index_name, index_return = benchmark
        tracking.append(f'{scope["year"]}年上半年，共同业绩基准{index_name}收益率为{percentage(index_return)}%。'
            '同期'+'，'.join(f'{label(products[code])}净值增长{percentage(products[code][PERFORMANCE]["raw_value"])}%'
                           if products[code][PERFORMANCE]['value'] >= 0 else
                           f'{label(products[code])}净值下降{percentage(abs(Decimal(products[code][PERFORMANCE]["raw_value"])))}%'
                           for code in scope['codes'] if PERFORMANCE in products[code])+'。')
        ranked = sorted(gaps, key=lambda code: abs(Decimal(gaps[code]['exact'])))
        if len(ranked) == len(scope['codes']):
            def describe_gap(code):
                amount = Decimal(gaps[code]['exact'])
                name = label(products[code])
                if not amount:
                    return name+'与基准持平'
                return name+('高于基准' if amount > 0 else '低于基准')+percentage(abs(amount))+'个百分点'

            difference = '，'.join(describe_gap(code) for code in scope['codes'])
            closest = [code for code in ranked if abs(Decimal(gaps[code]['exact'])) == abs(Decimal(gaps[ranked[0]]['exact']))]
            if len(closest) == len(ranked):
                tracking_summary = '样本基金的半年累计收益与基准的差距相同。'
            else:
                closest_names = '、'.join(label(products[code]) for code in closest)
                tracking_summary = f'{closest_names}的半年累计收益最接近基准。'
                if len(closest) == 1:
                    tracking_leader = closest[0]
            tracking.append(difference+'。')
            if not holding:
                tracking[-1] += '累计差距体现半年持有结果，评估日度跟踪稳定性需要以同一口径比较年化跟踪误差。'
            if all(REQUIRED_FIELDS <= products[code].keys() for code in scope['codes']):
                coverage = dict(codes=scope['codes'], period=scope['period'], benchmark=index_name,
                                sources=list(dict.fromkeys(
                                    product[field]['artifact_id'] for product in rows
                                    for field in (PERFORMANCE, BENCHMARK, MANAGEMENT, CUSTODY))))
                if requested_names:
                    coverage['product_mappings'] = scope['resolved']
    elif gaps:
        descriptions = '，'.join(
            f'{label(products[code])}较自身{products[code][BENCHMARK]["locator"]["benchmark_name"]}'
            f'偏离{calc["display"]}个百分点' for code, calc in gaps.items())
        tracking.append(descriptions+'。不同基准或不完整的同期基准读数会限制横向比较。')
    errors = [products[code][TRACKING_ERROR] for code in scope['codes'] if TRACKING_ERROR in products.get(code, {})]
    if errors and (not holding or len(errors) == len(scope['codes'])):
        shown = '，'.join(f'{row["object_label"]}披露的年化跟踪误差为{percentage(row["raw_value"])}%' for row in errors)
        tracking.append(shown+'。'+('横向比较日度跟踪稳定性还需核对其余产品的同口径数据。'
                               if len(errors) < len(scope['codes']) else
                               '该指标反映日度收益差的波动，结合累计差距可同时观察跟踪的方向与稳定性。'))
    for product in rows:
        if PERFORMANCE in product and (drivers := product[PERFORMANCE]['locator'].get('manager_drivers')):
            tracking.append(f'{label(product)}的管理人将本期偏离与'+ '、'.join(drivers)
                            +'等因素联系起来。基准差反映这些因素的合并结果，判断贡献大小须另作逐日归因。')
    sections = []
    if unresolved:
        notes = []
        for target in unresolved:
            if target['status'] == '简称有歧义':
                examples = '、'.join(f'{row["name"]}（{row["code"]}）' for row in target['examples'])
                notes.append(f'{target["query"]}可对应{target["count"]}只ETF，包括{examples}。'
                             '请补充管理人或证券代码。')
            elif target['status'] == '无精确匹配':
                notes.append(f'交易所名录未找到{target["query"]}的精确对应项，请核对产品全称或证券代码。')
            else:
                notes.append(f'取得两市名录后，才能核对{target["query"]}是否唯一对应一只ETF。')
        section = _section('标的核对', (), (), notes)
        section['source_refs'] = list(dict.fromkeys([*identity_refs, *(
            example['source_ref'] for item in unresolved for example in item['examples'])]))
        sections.append(section)
    elif requested_names and len(scope['codes']) == 1:
        section = _section('标的核对', (), (), ('给出的名称和代码都对应同一只ETF。请补充另一只比较标的。',))
        section['source_refs'] = identity_refs
        sections.append(section)
    if remarks:
        section = scoped_section('持有费率与费用口径', {MANAGEMENT, CUSTODY}, fees.values(), remarks)
        section['source_refs'] = list(dict.fromkeys([*section['source_refs'], *identity_refs]))
        sections.append(section)
    tracked_fields = {PERFORMANCE, BENCHMARK, TRACKING_ERROR}
    asset_section = None
    if asset_rows and len(asset_rows) == len(scope['codes']):
        sizes = '，'.join(f'{row["object_label"]}约{Decimal(row["raw_value"])/Decimal(100000000):.1f}亿元'
                         for row in asset_rows)
        if scope.get('trading') and tracking and not holding:
            tracking.append(f'截至{scope["year"]}年6月底，{sizes}。')
            tracked_fields.add(ASSETS)
        elif not holding:
            asset_section = scoped_section('产品规模与执行差异', {ASSETS}, (),
                (f'截至{scope["year"]}年6月底，{sizes}。规模反映产品资产体量。'
                 '对于需要在二级市场执行的资金，应结合委托规模比较报价价差和可成交深度，'
                 '再将即期折溢价纳入同一时点的买入成本。',))
    if tracking:
        sections.append(scoped_section('净值表现与基准贴合度', tracked_fields, gaps.values(), tracking))
    if asset_section:
        sections.append(asset_section)
    names = {code: label(product) for code, product in products.items()} if holding else None
    trade = etf_trading_readout(scope, facts, calculations, names) if scope.get('trading') else None
    if trade:
        if not scope.get('year') and identity_refs:
            section = next((item for item in trade['sections'] if item['data_refs']), None)
            if section:
                section['source_refs'] = list(dict.fromkeys([*section['source_refs'], *identity_refs]))
        sections.extend(trade['sections'])
        if trade['coverage'] is None:
            coverage = None
        elif coverage or not scope.get('year'):
            coverage = dict(coverage or dict(codes=scope['codes']), trading=trade['coverage'])
            if requested_names:
                coverage['product_mappings'] = scope['resolved']
    if not sections:
        sections.append(_section('比较口径', (), (),
            ('本次比较范围为同一个上半年内的固定年费率和披露净值表现。',)))
    conclusion = fee_summary + tracking_summary + (trade['conclusion'] if trade else '')
    if unresolved:
        conclusion = '比较标的尚未全部确认。下文列出可核对的产品和需要澄清的名称。'
    elif not conclusion:
        conclusion = ('已按实际覆盖列示两只基金的交易数据。' if coverage and trade else
                      '本次可用披露不足以形成覆盖全部产品的比较。')
    elif coverage is None and not trade:
        conclusion += '部分标的或同期基准读数缺失，比较限于本报告列示范围。'
    elif scope.get('year') and not (fee_summary and benchmark):
        conclusion += '固定费率和同期跟踪表现仅比较已取得的同口径披露。'
    elif (holding and coverage and trade and tracking_leader and tracking_leader == trade['turnover_leader']
          and len({Decimal(item['exact']) for item in fees.values()}) == 1):
        conclusion = (fee_summary+label(products[tracking_leader])+f'在{scope["year"]}年上半年的累计收益更接近共同基准，'
                      '近期共同交易日的日均成交额也更高。')
    risks = ['大额委托或市况波动时，价差与折溢价可能放大。']
    if scope.get('year'):
        risks.insert(0, '净值收益随市场波动，阶段性正偏离可能回吐。')
    if holding:
        risks = ['净值随指数波动。半年累计差距用于比较该期间的跟踪结果，长周期贴合度取决于日度偏离的波动和持续性。',
                 '买卖价差和折溢价随下单时点变化，成交额主要反映交易活跃度。']
    return dict(title=title, conclusion=conclusion, sections=sections, risks=risks, coverage=coverage)


def verified_etf_answer(task, draft):
    from .draft_analysis import analyze
    from .draft_sources import observations
    from .etf_comparison import comparison_scope
    from .planning import input_fingerprint

    if (task.get('run_mode') not in ('direct', 'local_draft') or draft.get('origin') != 'data_outline'
            or comparison_scope(task, direct=True) is None or task['input_hash'] != input_fingerprint(task)
            or len(task['questions']) != 1):
        return None
    question, = task['questions']
    if question.get('evidence_needs') or question.get('method_needs') or not question['required']:
        return None
    facts = observations(task)
    if draft['facts'] != facts or draft['calculations'] != analyze(task, facts)['results']:
        return None
    readout = etf_product_readout(task, facts, draft['calculations'])
    if not readout or readout['coverage'] is None or readout['conclusion'] != draft['conclusion']:
        return None
    fields = ('title', 'analysis', 'data_refs', 'source_refs', 'calculation_refs')
    intended = [{field: item[field] for field in fields} for item in readout['sections']]
    delivered = [{field: item.get(field) for field in fields} for item in draft['logics']]
    if delivered != intended:
        return None
    return dict(version=1, task_id=task['id'], research_version=task['version'], input_hash=task['input_hash'],
                question_id=question['id'], scope=readout['coverage'],
                answer_hash=fingerprint(dict(conclusion=draft['conclusion'], sections=delivered)))


def _family(row, record, legacy_statements):
    operation = record.get('operation')
    field = analysis_field(row)
    if field in FINANCIAL and (row.get('period_basis') in ('请求D报告期', '来源end_date报告期')
                               or row['id'] in legacy_statements):
        return 'financial'
    if operation == 'edb_service' and field == 'value' and row.get('axis_role') == 'time':
        return 'economic'
    if operation in SERIES_OPERATIONS and field in SERIES_FIELDS and row.get('axis_role') == 'time':
        return 'series'
    return None


def _changes(calculations, groups, eligible, stated_codes):
    changes = defaultdict(list)
    for calc in calculations:
        refs = calc['data_refs']
        if len(refs) != 2 or any(ref not in eligible for ref in refs):
            continue
        first, last = (eligible[ref] for ref in refs)
        first_key, first_day = first
        key, last_day = last
        if first_key != key or first_day >= last_day or calc.get('comparison_kind'):
            continue
        family, field = key[0], key[2]
        kind = None
        if calc['method'] == 'raw_change_v1' and calc['unit'] == '%':
            if family == 'financial':
                kind = 'financial_yoy'
            elif family == 'series' and field in {'close', *NAV_FIELDS}:
                kind = 'series_change'
        elif family == 'economic' and calc['method'] == 'raw_percentage_points_v1':
            kind = 'economic_points'
        if kind and (last_day == max(groups[key]) or kind == 'economic_points' and key[1] in stated_codes):
            changes[key].append((first_day, calc, kind))
    return changes


def _claim(content, key, days, changes, records, *, financial_level, legacy_statements, index_name=None):
    family, code, field = key
    if family == 'financial' and financial_level:
        readings = [day for day, rows in days.items() if any(row['value'] is not None for row in rows)]
        if not readings:
            return None
        end = max(readings)
        if not changes:
            # Another period can lack a verified unit or contain competing
            # values. A uniquely identified latest raw reading can still stand.
            days = {end: days[end]}
    if any(len({_value_key(row) for row in rows}) > 1 for rows in days.values()):
        return None
    if len({(row['series_basis'], row['unit']) for rows in days.values() for row in rows}) != 1:
        return None
    end = max(day for day, rows in days.items() if rows[0]['value'] is not None)
    row = days[end][0]
    label = FINANCIAL_LABELS.get(field, row['label'])
    if index_name and field == 'close':
        label = '收盘点位'
    statement = ''
    if family == 'financial':
        record = records[row['artifact_id']]
        legacy_statement = legacy_statements.get(row['id'])
        statement = legacy_statement or requested_financial_statement(content, [row])
        if not statement and record.get('source') == 'tinyshare' and record['parameters'].get('report_type') == '1':
            statement = '合并报表'
        if not statement:
            return None
        if legacy_statement and not changes and observation_presentation(row)['unit'] == '未确认':
            return None
        label = ('期末' if field in BALANCE else '') + label
    elif family == 'filing':
        statement = row['locator']['statement']

    if changes:
        start, calc, kind = min(changes, key=lambda item: item[0])
        comparison = '上升' if calc['value'] > 0 else '下降' if calc['value'] < 0 else '变化'
        number = calc['display'].removeprefix('-') + calc['unit']
        if family == 'financial':
            condition = '若两期单位一致，' if calc['conditional_equal_scale'] else ''
            head = (f'{condition}按请求{statement}口径，{code}于{end}报告期的' if legacy_statement
                    else f'{condition}{code}{statement}{end}报告期，')
            lead = label + '同比' + comparison
        elif family == 'economic':
            head = f'{start}至{end}，'
            lead = label.replace(':', '') + '两期读数' + comparison
        else:
            head = f'{start}至{end}，{code}'
            lead = label + '两期读数' + comparison
        link = dict(kind=kind, data_refs=calc['data_refs'], calculation_refs=[calc['id']],
                    calculation_hash=fingerprint(calc))
        suffix = '，以两端单位一致为前提' if family == 'series' and calc['conditional_equal_scale'] else ''
        return head, lead, number, suffix, link

    reading = observation_presentation(row)
    value = reading['value']
    unit = reading['unit']
    if family == 'filing':
        head = f"{code}{statement}{reading['period']}，"
        value = format(Decimal(row['raw_value']), ',f')
        if unit != '人民币元':
            unit = '人民币' + unit
    elif family == 'financial':
        condition = '金额单位待核对，' if unit == '未确认' else ''
        head = (f'按请求{statement}口径，{code}于{end}报告期的' if legacy_statement
                else f'{code}{statement}{end}报告期，{condition}')
    else:
        head = f'{end}，' + ((index_name or code) if family == 'series' else '')
        if family == 'economic':
            label = label.replace(':', '')
    lead = label + ('为人民币' if unit == '人民币元' else '为')
    suffix = unit
    if unit == '人民币元':
        suffix = '元'
    elif unit == '未确认':
        suffix = '' if family == 'financial' else '，单位待核对'
    kind = {'financial': 'financial_level', 'filing': 'filing_amount',
            'economic': 'economic_level', 'series': 'observed_readout'}[family]
    link = dict(kind=kind, data_refs=[row['id']], calculation_refs=[], row_hash=fingerprint(row))
    return head, lead, value, suffix, link


def _readout_title(by_code):
    if any(not re.fullmatch(r'\d{4,6}\.(?:SH|SZ|BJ|HK|OF)', code) for code in by_code):
        return ''
    if len(by_code) != 1:
        if not all(key[0] == 'series' for readings in by_code.values() for key, _ in readings):
            return ''
        titles = []
        for code, readings in by_code.items():
            labels = dict.fromkeys(SERIES_LABELS.get(key[2], LABELS.get(key[2], '行情')) for key, _ in readings)
            titles.append(code + '与'.join(labels))
        title = '与'.join(titles) + '核对'
        return title if len(title) <= 36 else '多标的行情与指标核对'
    code, readings = next(iter(by_code.items()))
    families = {key[0] for key, _ in readings}
    if families == {'financial'}:
        return code + ('财务对照' if any(claim[-1]['kind'] == 'financial_yoy' for _, claim in readings) else '财务核对')
    if families == {'series'}:
        fields = {key[2] for key, _ in readings}
        if len(fields) == 1:
            field, = fields
            return code + SERIES_LABELS.get(field, LABELS.get(field, '行情')) + '观察'
        return code + '行情观察'
    return ''


def index_weight_readout(sheet, calculations):
    from .draft_analysis import WEIGHT_TOTAL_METHOD
    from .index_sources import weight_total_label

    day, name, count = sheet['day'], sheet['name'], sheet['count']
    title = name+'成分权重核对'
    requested = f'{name}在{sheet["requested_day"]}的成分权重' if sheet['requested_day'] else f'{name}成分权重'
    if sheet['reason'] == 'source_restricted':
        note = f'按本次限定的数据来源，未取得{requested}。'
    elif sheet['reason'] == 'missing':
        note = f'未取得{requested}原表。'
    elif sheet['reason'] == 'versions':
        note = f'{name}在{day}留存有多份权重文件，版本待核对。'
    elif count is not None and count > sheet.get('available', 0):
        note = f'{name}在{day}的原表只有{sheet.get("available", 0)}项可用权重，前{count}项核对暂缺。'
    elif not sheet['complete']:
        note = f'{name}在{day}的原表有{sheet["available"]}项可用权重，登记规模为{sheet["expected_rows"]}项，完整排名及合计暂缺。'
    else:
        note = ''
    result = dict(title=title, text='', links=[], notes=[note] if note else [])
    if 'retained_from_version' in sheet:
        result['notes'].append(f'本版沿用第{sheet["retained_from_version"]}版留存的{day}权重原表。')
    if not sheet['rows']:
        return result
    caption = f'{day}，{name}' if sheet['requested_day'] else f'{name}在{day}的原表中，'
    total = next((row for row in calculations if sheet['total'] and row['method'] == WEIGHT_TOTAL_METHOD
                  and row['code'] == sheet['code'] and row['period'] == day
                  and row['label'] == weight_total_label(count)), None)
    if total:
        caption += total['label']+'为'
        value = total['display']+total['unit']
        result.update(text=caption+value+'。', links=[dict(kind='index_weight_total',
            start=len(caption),end=len(caption)+len(value),original=value,data_refs=total['data_refs'],
            calculation_refs=[total['id']],calculation_hash=fingerprint(total))])
    else:
        count_text = f'前{count}大' if count is not None else ''
        result['text'] = caption+f'{count_text}成分权重见原始观测。'
    return result


def market_query_readout(task, rows):
    """Present literal intraday or dated liquidity observations from their own series."""
    from .intraday_quotes import MINUTE, question_request
    from .market_calendar import direct_liquidity_scope, stated_close_intervals

    intraday = question_request(task, direct=True)
    intervals = direct_liquidity_scope(task) if intraday is None else None
    if intraday is None and not intervals:
        return None
    if intraday is not None:
        minute = intraday['operation'] == MINUTE
        code = intraday['parameters']['codes']
        field = 'minute_close' if minute else 'latest'
        title = code+('分钟行情' if minute else '最新报价')
        requested = {(code, field)}
        series = [row for row in rows if row.get('source_operation') == intraday['operation']]
    else:
        requested = set(intervals) | {(code, 'close') for code in stated_close_intervals(task, explicit=True)}
        codes = sorted({code for code, _ in requested})
        subject = '、'.join(codes[:2]) if len(codes) <= 2 else '多标的'
        title = subject+'日线量价'
        series = [row for row in rows if row.get('source') == 'ifind' and row['field'] in ('close', 'volume', 'amount')]
    groups = defaultdict(lambda: defaultdict(list))
    for row in series:
        if (row['code'], row['field']) in requested and row['value'] is not None:
            groups[row['code'], row['field']][row['period']].append(row)
    text, links, sections, gaps = '', [], [], []
    order = {'close': 0, 'volume': 1, 'amount': 2}
    show_code = len({code for code, _ in requested}) > 1
    for code, field in sorted(requested, key=lambda key: (key[0], order.get(key[1], 0))):
        label = LABELS[field]
        by_period = groups[code, field]
        usable = [same_period[0] for _, same_period in sorted(by_period.items())
                  if len({_value_key(row) for row in same_period}) == 1]
        if not usable:
            gaps.append(f'{code}的{label}暂无可用读数。')
            continue
        first, last = usable[0], usable[-1]
        period = last['period'].split('+')[0].replace('T', ' ')
        text += (f'{period}，{code}{label}为' if last.get('unit_reference')
                 else f'{period}，iFinD记录的{code}{label}为')
        value = _value_key(last)
        number = format(value, ',f')
        if '.' in number:
            number = number.rstrip('0').rstrip('.')
        links.append(dict(start=len(text), end=len(text)+len(number), original=number, code=code,
                          date=last['period'], data_refs=[last['id']]))
        unit = observation_presentation(last)['unit'] if last.get('unit_reference') else ''
        text += number + unit + '。'
        paragraphs = []
        if field == 'minute_close' and len(usable) > 1:
            start_time = first['period'].split('+')[0].replace('T', ' ')
            paragraphs.append(f'已取得{start_time}至{period[-8:]}的分时记录，共{len(usable)}个时间点。')
        heading = code+' '+label if show_code else label
        sections.append(_section(heading, usable, [], paragraphs))
    organization = 'comparison' if intervals and re.search(r'比较|对比|对照', task['question']) else 'fact'
    return dict(title=title, conclusion=text or '所查询的行情暂未取得可用读数。', links=links,
                organization=organization, sections=sections, gaps=gaps)


def retained_readout(task, rows, calculations, *, nav_readings=(), index_names=None):
    """Read the requested dates and comparison periods alongside other relevant observations."""
    from .market_calendar import stated_close_returns, stated_market_points

    cutoff = _sequence_day(market_date(task['cutoff']))
    records = {row['id']: row for row in task.get('source_records', [])
               if row.get('task_id') == task['id'] and row.get('research_version') == task['version']}
    statement_results = [calc for calc in calculations if calc.get('comparison_kind') != 'cash_bridge_change']
    bridge_results = [calc for calc in calculations if calc.get('comparison_kind') == 'cash_bridge_change']
    if bridge_results and (comparison := _cash_growth_profile({row['id']: row for row in rows}, statement_results)):
        filing = records[comparison[0]['comparison_source_ref']]
        issuer = filing['directory'].get('security_name') or comparison[0]['code']
        year = comparison[0]['period'][:4]
        text = f'{year}年上半年，{issuer}'
        links = []
        for label, calc in zip(('营业收入', '归母净利润', '经营现金流'), comparison, strict=True):
            text += label+'同比增长'
            figure = f'{Decimal(calc["exact"]):.1f}%'
            links.append(dict(start=len(text), end=len(text)+len(figure), original=figure,
                code=calc['code'], date=calc['period'], data_refs=calc['data_refs'], calculation_refs=[calc['id']]))
            text += figure+'，'
        text = text.rstrip('，')+'。利润增长尚未同步转化为经营现金流，后续重点关注回款和存货周转。'
        return f'{issuer}{year}年上半年经营质量', text, links
    economic_windows = economic_comparison_windows(task)
    stated_economics = set(economic_windows) if projects_stated_periods(task) else set()
    explicit_periods = task.get(DRAFT_PERIOD_POLICY) == DRAFT_PERIOD_VERSION
    frequencies = economic_frequencies(task) if explicit_periods and stated_economics else {}
    groups = defaultdict(lambda: defaultdict(list))
    eligible = {}
    filing_enabled = (task.get('draft_source_reader_version') or 0) >= CONSOLIDATED_METRICS_VERSION
    legacy_statements = retained_financial_statements(task, rows)
    for row in rows:
        record = records.get(row['artifact_id'])
        if record is None:
            continue
        family = _family(row, record, legacy_statements)
        period = row.get('period', '')
        if filing_enabled and record.get('kind') == 'filing' and is_filing_amount(row):
            # The reader binds the complete interval. The ending date only
            # orders observations and checks the cutoff.
            family = 'filing'
            day = _sequence_day(period.rsplit('至', 1)[-1])
        else:
            day = _sequence_day(period)
        if (not row.get('code') or not day or day > cutoff
                or row.get('previous_close_for')):
            continue
        if family:
            if family == 'economic' and (scopes := economic_windows.get(row['code'])) and not any(
                    start <= day <= min(end, last_closed) and
                    _economic_query_links(records[row['artifact_id']], row['code'], question)
                    for question, _, last_closed, start, end in scopes):
                continue
            key = (family, row['code'], analysis_field(row))
            groups[key][day].append(row)
            if row['value'] is not None:
                eligible[row['id']] = key, day
    changes = _changes(calculations, groups, eligible, stated_economics)
    content = dict(task_id=task['id'], research_version=task['version'], source_records=list(records.values()))
    by_code = defaultdict(list)
    exact_by_code = defaultdict(list)
    point_days = defaultdict(set)
    for code, day, field, _ in stated_market_points(task):
        point_days[code, field].add(day)
    nav_refs = {}
    for reading in nav_readings:
        code, day = reading['code'], _sequence_day(reading['period'])
        point_days[code, 'unit_nav'].add(day)
        nav_refs[code, day] = reading['data_ref']
    requested_returns = {(code, day.isoformat()) for code, day, _ in stated_close_returns(task)} if point_days else set()
    daily_rates = {(subject_key(calc['code']).upper(), calc['window_end']):calc for calc in calculations
                   if calc.get('comparison_kind') == 'previous_session'
                   and (subject_key(calc['code']).upper(), calc['window_end']) in requested_returns}
    premiums = {(subject_key(calc['code']).upper(), calc['period']): calc for calc in calculations
                if calc['method'] == 'etf_daily_nav_premium_v1'}
    additional = {}
    financial_level = task.get(FINANCIAL_PERIOD_GAP_POLICY) == FINANCIAL_PERIOD_GAP_VERSION
    field_order = {field: index for index, field in enumerate(FINANCIAL_ORDER)}
    for key in sorted(groups, key=lambda key: (not changes[key], field_order.get(key[2], len(field_order)))):
        if not any(row['value'] is not None for rows in groups[key].values() for row in rows):
            continue
        comparisons = changes[key]
        if key[0] == 'economic' and key[1] in stated_economics:
            requested = []
            for comparison in sorted(comparisons, key=lambda item: (item[0], eligible[item[1]['data_refs'][1]][1])):
                first, calculation, _ = comparison
                last = eligible[calculation['data_refs'][1]][1]
                endpoints = {day: [row for row in groups[key][day] if row['value'] is not None]
                             for day in (first, last)}
                claim = _claim(content, key, endpoints, [comparison], records,
                               financial_level=False, legacy_statements=legacy_statements)
                if claim:
                    requested.append((key, claim))
            scopes = economic_windows[key[1]]
            period_windows = [(start, end, months) for _, months, closed, start, end in scopes
                              if explicit_periods and end <= closed
                              and _period_number(start, months) == _period_number(end, months)]
            for start, end, months in sorted(set(period_windows)):
                # Preserve every requested period. Conflicting values inside the
                # period stay in the workpaper with the coverage note.
                readings = {day: [row for row in values if row['value'] is not None]
                            for day, values in groups[key].items() if start <= day <= end}
                readings = {day: values for day, values in readings.items() if values}
                if len({_value_key(row) for values in readings.values() for row in values}) != 1:
                    continue
                point = _claim(content, key, readings, (), records, financial_level=False,
                               legacy_statements=legacy_statements)
                if point:
                    if _FREQUENCY_MONTHS.get(frequencies.get(key[1])) == months:
                        label = _period_label(_period_number(start, months), months)
                        point = (label+'，', *point[1:])
                    requested.append((key, point))
            if requested:
                exact_by_code[key[1]].extend(requested)
            if requested or len(period_windows) == len(scopes):
                continue
        code, field = subject_key(key[1]).upper(), 'unit_nav' if key[2] in NAV_FIELDS else key[2]
        days = point_days.get((code, field), ()) if key[0] == 'series' else ()
        for day in sorted(days):
            # A point can prefer the exchange's verified unit. Keep the
            # supplier's row available for its own same-source interval.
            preferred = nav_refs.get((code, day)) if field == 'unit_nav' else None
            available = [row for row in groups[key].get(day, ()) if row['value'] is not None
                         and (preferred is None or row['id'] == preferred)]
            if not available:
                continue
            point = _claim(content, key, {day:available}, (), records, financial_level=financial_level,
                           legacy_statements=legacy_statements, index_name=(index_names or {}).get(code))
            if point:
                exact_by_code[key[1]].append((key, point))
                date_key = (code, day.isoformat())
                calc = daily_rates.get(date_key) if field == 'close' else premiums.get(date_key) if field == 'unit_nav' else None
                if calc:
                    label = '较上一交易日的涨跌幅为' if field == 'close' else '收盘折溢价率为'
                    link = dict(code=calc['code'], date=day.isoformat(), data_refs=calc['data_refs'],
                                calculation_refs=[calc['id']])
                    if field == 'unit_nav':
                        link.update(kind='daily_nav_premium', calculation_hash=fingerprint(calc))
                    exact_by_code[key[1]].append((key, (point[0], label, calc['display'], '%', link)))
        if days and not comparisons:
            continue
        claim = _claim(content, key, groups[key], comparisons, records, financial_level=financial_level,
                       legacy_statements=legacy_statements)
        if not claim:
            continue
        by_code[key[1]].append((key, claim))
        if key[0] == 'economic' and len(comparisons) > 1:
            latest = max(comparisons, key=lambda item: item[0])
            if any(start < latest[0] for start, _, _ in comparisons):
                additional[key] = _claim(content, key, groups[key], [latest], records, financial_level=False,
                                         legacy_statements=legacy_statements)
    if not by_code and not exact_by_code:
        return '', '', []
    # When the fallback can show only three numbers, avoid using two of those
    # slots for overlapping consolidated profit readings on the same subject.
    archived_refs = {row['id'] for row in rows if records.get(row['artifact_id'], {}).get('archive_reuse')}
    for readings in by_code.values():
        retained_fields = {key[2] for key, claim in readings if key[0] == 'financial'
                           and all(ref in legacy_statements or ref in archived_refs
                               for ref in claim[-1]['data_refs'])}
        if {'ths_np_atoopc_stock', 'ths_ncf_from_oa_stock'} <= retained_fields:
            readings.sort(key=lambda item: item[0][0] == 'financial' and item[0][2] == 'ths_np_stock')
    order = list(dict.fromkeys(subject_key(code) for code, _, _ in object_mentions(scope_question(task), task)))
    positions = {code: index for index, code in enumerate(order)}
    codes = sorted(dict.fromkeys([*exact_by_code, *by_code]), key=lambda code: positions.get(subject_key(code), len(positions)))
    interleaved = (item for batch in zip_longest(*(by_code.get(code, ()) for code in codes)) for item in batch if item is not None)
    chosen = list(islice(interleaved, 3))
    summaries = [claim for code in codes for _, claim in exact_by_code.get(code, ())]
    summaries.extend(claim for _, claim in chosen)
    summaries.extend(additional[key] for key, _ in chosen if key in additional)
    sentences = defaultdict(list)
    for head, lead, number, suffix, link in summaries:
        sentences[head].append((lead, number, suffix, link))
    text, links = '', []
    for head, claims in sentences.items():
        text += head
        for index, (lead, number, suffix, link) in enumerate(claims):
            text += ('，' if index else '') + lead
            links.append(dict(link, start=len(text), end=len(text)+len(number), original=number))
            text += number + suffix
        text += '。'
    title_claims = {code: [*exact_by_code.get(code, ()), *by_code.get(code, ())] for code in codes}
    return _readout_title(title_claims), text, links
