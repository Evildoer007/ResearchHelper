"""Test a demand hypothesis against separate price and retail readings."""

from decimal import Decimal

from .contracts import fingerprint
from .monthly_statistics import (CELL_KIND, FIELDS, PMI_FIELDS, RETAIL_STUDY, pmi_quote_readout_scope,
                                 pmi_release_context, selected_series, study_scope)
from .planning import input_fingerprint


def _number(value, digits=1):
    result = format(Decimal(str(value)), f'.{digits}f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def _month(period):
    return f'{int(period[-2:])}月'


def _reading(row):
    return _number(row['raw_value']) + row['unit']


def _movement(first, last):
    a, b = (Decimal(row['raw_value']) for row in (first, last))
    return ('由' + _reading(first) + ('升至' if a < b else '降至') + _reading(last)
            if a != b else '维持' + _reading(last))


def _draft_section(title, analysis, rows, calculations, notes, *, chart_fields=()):
    section = dict(title=title, analysis=analysis, implication='', risk='', status='待验证',
        data_refs=list(dict.fromkeys(row['id'] for row in rows)),
        calculation_refs=[row['id'] for row in calculations],
        source_refs=list(dict.fromkeys(row['artifact_id'] for row in rows)),
        knowledge_refs=[], workpaper_notes=list(notes), macro_demand_readout=True)
    if chart_fields:
        section['macro_monthly_chart'] = list(chart_fields)
    return section


def pmi_market_readout(task, facts, calculations):
    """Compose a dated cross-market study from verified release cells and calculated returns."""
    from .subject_scope import subject_key

    scope = pmi_quote_readout_scope(task)
    if not scope or not (release := pmi_release_context(task, scope)):
        return None
    series = selected_series(row for row in facts if row['artifact_id'] == release['source_ref'])
    period = release['reference_month']
    levels = [series.get(field, {}).get(period) for field in PMI_FIELDS]
    if any(row is None for row in levels):
        return None
    changes = [series.get(field+'_change', {}).get(period) for field in PMI_FIELDS]
    by_id = {row['id']: row for row in facts}
    quotes = [row for row in facts if row['field'] == 'close'
              and subject_key(row['code']).upper() in scope['codes']
              and scope['start'] <= row['period'] <= scope['end']]
    returns = {}
    for code in scope['codes']:
        selected = [row for row in calculations if subject_key(row['code']).upper() == code
                    and all(ref in by_id for ref in row['data_refs'])]
        daily = sorted([row for row in selected if row.get('comparison_kind') == 'daily_close_return'],
                       key=lambda row: row['period'])
        total = [row for row in selected if row['method'] == 'raw_change_v1' and not row.get('comparison_kind')
                 and (row.get('window_start'),row.get('window_end')) == (scope['start'],scope['end'])]
        if len(daily) != 2 or len(total) != 1:
            return None
        returns[code] = [*daily, total[0]]
    all_returns = [row for group in returns.values() for row in group]
    window = tuple(returns[scope['codes'][0]][index]['window_end'] for index in range(2))
    if any(tuple(group[index]['window_end'] for index in range(2)) != window for group in returns.values()):
        return None

    def pct(calculation):
        return ('+' if Decimal(calculation['exact']) > 0 else '') + _number(calculation['exact'], 2) + '%'

    def level_text(row, change):
        if not change:
            direction = ''
        elif change['value'] == 0:
            direction = '，较上月持平'
        else:
            direction = '，较上月' + ('上升' if change['value'] > 0 else '下降') + _number(abs(change['value'])) + '个百分点'
        return row['label'] + '为' + _reading(row) + direction

    broad_weakness = all(row['value'] < 50 for row in levels)
    macro_signal = ('生产和订单同步位于荣枯线下方' if broad_weakness else
                    '生产、订单和总量读数需要分别解读')
    ranked = sorted(returns.values(), key=lambda group: Decimal(group[-1]['exact']), reverse=True)
    close_signal = '，'.join(group[-1]['code']+'累计'+pct(group[-1]) for group in ranked)
    window_label = f'{window[0]}与{window[1]}'
    actual_ends = {tuple(by_id[ref]['period'] for ref in group[-1]['data_refs']) for group in ranked}
    if len(actual_ends) != 1:
        return None
    (_, last_close), = actual_ends
    conclusion = (f'{_month(period)}制造业PMI为{_reading(levels[0])}，{macro_signal}。'
                  f'截至{last_close}收盘，{close_signal}。'
                  '短窗价格排序不足以识别PMI的净影响，周期盈利与港股相对强弱需要各自的证据。')
    macro_rows = [*levels, *(row for row in changes if row)]
    marked = release['displayed_release_time'].replace(' +08:00', '（北京时间）')
    readings = '。'.join(level_text(row, change) for row, change in zip(levels, changes, strict=True))
    macro_story = f'国家统计局于{marked}发布本期数据。{readings}。'
    if broad_weakness:
        macro_story += ('订单和生产的同步偏弱给周期行业的收入与产能利用率带来下行约束。'
                        '当需求领先于生产走弱时，若库存未同步下降，后续可能先压利润率，再压收入增长预期。')
    else:
        macro_story += '判断需求向盈利的传导，应先区分订单的持续性与生产端的调整，再用库存和利润率判断企业能否消化产量变化。'
    macro = _draft_section('制造业需求与周期盈利', macro_story, macro_rows, (),
                           ('制造业正文读数均为季调扩散指数。',))
    macro['detail_analysis'] = (
        'PMI衡量调查样本中经营活动方向的分布，不能据此推算工业增加值或企业利润的具体跌幅。'
        '若后续订单回到扩张区间，同时库存周转改善、工业企业盈利转强，前述需求约束应下修。')
    market_story = '。'.join(f'{group[-1]["code"]}首日{pct(group[0])}、次日{pct(group[1])}、累计{pct(group[2])}'
                           for group in ranked) + '。'
    directions = [{Decimal(group[index]['exact']).compare(0) for group in ranked} for index in range(2)]
    if directions == [{1}, {-1, 1}]:
        market_story += '首日普涨后，次日已出现方向分化，港股与A股的持续性需要分别判断。'
    elif directions == [{-1}, {-1, 1}]:
        market_story += '首日普跌后，次日已出现方向分化，不能把连续两日视为同一轮抛压。'
    else:
        market_story += '比较两个交易日的方向和幅度，有助于区分共同波动与个别标的的持续变化。'
    market = _draft_section('发布后首日与次日的跨市场反应', market_story, quotes, all_returns,
                            ('两地交易日由各自的交易所日历核对。',))
    market['detail_analysis'] = (f'以发布前最近收盘为基准，对照{window_label}的收盘路径。'
        '单日涨跌按本日收盘除以前一交易日收盘减一，累计涨跌用区间两端复算。'
        '港股价格按行情接口返回的人民币口径列示，汇率变动可能影响与港币本地回报的对比。')
    mechanism = _draft_section('相对表现的传导与反证',
        '周期资产主要通过订单、利润率和盈利预期消化制造业信号。'
        '港股的相对表现还取决于行业构成与风险偏好。优先检验港股强势能否在扣除宽基表现后保留，'
        '以及它是否集中在制造业直接暴露有限的成分。若相对强势主要由同期市场普涨解释，则应降低对行业传导的权重。',
        [*levels, *quotes], all_returns, ())
    mechanism['detail_analysis'] = (
        '先把发布值与发布前可得的市场一致预期比较，再检验价格反应与意外方向是否一致。'
        '结合港股宽基、科技基准及人民币汇率检查共同扰动，并用公开的基金基准、持仓和净值区分成分暴露和交易折溢价。'
        '若调整这些因素后相对涨幅消失，或同类事件未重复出现差异，当前窗口无法支持PMI敏感度的排序。')
    return dict(title=f'{_month(period)}制造业PMI与A港股市场反应', conclusion=conclusion,
        sections=[macro,market,mechanism], organization='research',
        risks=['发布前一致预期和同期扰动未纳入本次对比，宏观方向与价格方向的差异不能直接归因。',
               '跨市场收盘时刻与报价币种不同，ETF交易价格也受折溢价影响，区间价格变化不等同于总回报。'])


def _retail_readout(task, scope, series, facts, calculations):
    periods = scope['periods']
    if not periods:
        return None
    first, last = periods[0], periods[-1]
    previous = periods[-2]
    by_id = {row['id']: row for row in facts}
    rate_fields = ('retail', 'ex_auto', 'goods', 'catering')
    rates = {key: [series.get(key, {}).get(period) for period in periods] for key in rate_fields}
    contributions = {(row['component'], row['period']): row for row in calculations
                     if row.get('comparison_kind') == 'nbs_retail_contribution'}

    def section(title, paragraphs, fields, used=(), notes=(), chart_fields=()):
        cited = [row for key in fields for row in rates[key] if row]
        cited.extend(by_id[ref] for calc in used for ref in calc['data_refs'])
        return _draft_section(title, '\n\n'.join(paragraphs), cited, used, notes, chart_fields=chart_fields)

    def signed_change(value):
        return ('上升' if value > 0 else '下降') + _number(abs(value)) + '个百分点' if value else '持平'

    logics = []
    headline = [row for row in rates['retail'] if row]
    excluding = [row for row in rates['ex_auto'] if row]
    conclusion = ''
    judgment = '证据不足'
    if headline:
        text = '、'.join(_month(row['period']) + _reading(row) for row in headline)
        paragraphs = [f'社零名义同比为{text}。']
        if len(headline) > 1:
            latest = headline[-1]
            change = Decimal(latest['raw_value']) - Decimal(headline[-2]['raw_value'])
            level = '增长' if latest['value'] > 0 else ('下降' if latest['value'] < 0 else '持平')
            value = (f'{_month(latest["period"])}社零同比{level}{_number(abs(Decimal(latest["raw_value"])))}%'
                     if latest['value'] else f'{_month(latest["period"])}社零同比持平')
            conclusion = f'{value}，较{_month(headline[-2]["period"])}{signed_change(change)}。'
            if len(headline) == len(periods):
                pace = [row['value'] for row in headline]
                if len(pace) >= 3 and pace[-2] > pace[-3] and pace[-1] <= pace[-3]:
                    judgment = '阶段性回升后回落'
                    paragraphs.append(f'{_month(previous)}的提速在{_month(last)}回吐，增速回到{_month(periods[-3])}水平附近。'
                                      if pace[-1] == pace[-3] else
                                      f'{_month(previous)}的提速在{_month(last)}回吐，增速低于{_month(periods[-3])}。')
                elif all(after > before for before, after in zip(pace, pace[1:])):
                    judgment = '社零连续改善' if len(pace) >= 3 else '社零增速回升'
                    paragraphs.append('所选月份的名义销售增速逐期改善，持续程度可结合分项确认。')
                else:
                    judgment = ('社零同比回落' if change < 0 else
                                '社零最近一月回升' if change > 0 else '社零最近一月持平')
            if rates['ex_auto'][-2] and rates['ex_auto'][-1]:
                split_pace = _movement(rates['ex_auto'][-2], rates['ex_auto'][-1])
                paragraphs.append(f'剔除汽车后，最近两月增速{split_pace}。这一口径涵盖其他商品和餐饮，'
                                  '有助于区分车市变动与更广的消费品需求。')
        else:
            conclusion = f'{_month(headline[0]["period"])}社零名义同比为{_reading(headline[0])}。'
        chart = ('retail', 'ex_auto') if len(headline) == len(excluding) == len(periods) >= 3 else ()
        logics.append(section('社零总量与回暖节奏', [''.join(paragraphs)], ('retail', 'ex_auto'), chart_fields=chart,
                              notes=('同比采用当月名义值，使用各月最新已发布的统计局公报。',)))

    composition = []
    used = [row for label in ('商品零售', '餐饮') if (row := contributions.get((label, last)))]
    for key in ('goods', 'catering'):
        before, current = rates[key][-2:]
        if current:
            movement = '同比' + (_movement(before, current) if before else '为' + _reading(current))
            composition.append(FIELDS[key][0].removesuffix('同比') + movement)
    if composition:
        text = f'{_month(last)}' + '，'.join(composition) + '。'
        goods = next((row for row in used if row['component'] == '商品零售'), None)
        if goods:
            share = Decimal(goods['inputs']['subset']) / Decimal(goods['inputs']['total']) * 100
            text += f'商品零售约占社零{_number(share, 0)}%，其增速变化对总量读数影响更大。'
        if len(used) == 2:
            good, dining = (Decimal(row['exact']) for row in used)
            if good >= Decimal('0.05') and dining >= Decimal('0.05'):
                text += '按上年同期销售额加权估算，商品与餐饮分别拉动社零' + _number(good) + '和' + _number(dining) + '个百分点。'
            earlier = [contributions.get((label, previous)) for label in ('商品零售', '餐饮')]
            if all(earlier):
                shifts = [value - Decimal(row['exact']) for value, row in zip((good, dining), earlier, strict=True)]
                if shifts[0] < shifts[1] < 0:
                    text += '两类消费对社零增长的贡献均较上月收窄，商品零售贡献回落更多。'
                    used.extend(earlier)
        logics.append(section('商品零售与餐饮的贡献', [text], ('goods', 'catering'), used,
            notes=('商品与餐饮是互斥的消费类型。汽车归入商品零售，贡献不可重复加总。',)))

    auto = contributions.get(('汽车', last))
    other = contributions.get(('剔除汽车', last))
    auto_before = contributions.get(('汽车', previous))
    other_before = contributions.get(('剔除汽车', previous))
    attribution = [row for row in (auto_before, other_before, auto, other) if row]
    if auto and other:
        auto_points, other_points = [Decimal(row['exact']) for row in (auto, other)]

        def contribution(points):
            value = _number(abs(points))
            if value == '0':
                return '贡献接近零'
            return ('拉动' if points > 0 else '拖累') + f'社零同比约{value}个百分点'

        text = f'{_month(last)}剔除汽车后的消费品和餐饮合计{contribution(other_points)}，汽车{contribution(auto_points)}。'
        title = '汽车与其他消费的贡献分解'
        if auto_before and other_before:
            auto_change = auto_points - Decimal(auto_before['exact'])
            other_change = other_points - Decimal(other_before['exact'])
            text += f'较{_month(previous)}，两者贡献分别{signed_change(other_change)}和{signed_change(auto_change)}。'
            if auto_points < 0 and other_change < 0 < auto_change:
                if auto_change + other_change < 0:
                    conclusion += '汽车仍拖累总量，非汽车消费放缓主导本月回落。'
                    text += '汽车拖累较上月减轻，部分抵消了其他消费放缓的影响。'
                    title = '汽车拖累与社零降温的来源'
        item = section(title, [text], ('retail', 'ex_auto'), attribution,
            notes=('用当月金额及已公布同比反推上年同期基数。汽车按社零扣除非汽车部分估算。'
                   '公报增速及金额四舍五入，贡献为估算值。',))
        if len(attribution) == 4:
            item['macro_retail_bridge'] = True
        logics.append(item)

    if not logics:
        return None
    if not conclusion:
        conclusion = '。'.join(item['analysis'].split('。', 1)[0] for item in logics) + '。'
    gaps = [f'{FIELDS[key][0]}缺少' + '、'.join(_month(p) for p, row in zip(periods, rates[key], strict=True) if row is None)
            + '读数。' for key in rate_fields if not all(rates[key])]
    if not auto or not other:
        gaps.append(f'{_month(last)}的汽车与除汽车外分项无法用同一份公报完成贡献拆分。')
    logics[0]['workpaper_notes'].extend(gaps)
    from .publication_content import requested_strategy_count

    count = requested_strategy_count(task['question'])
    if count and count < len(logics):
        related = logics[count-1:]
        combined = dict(related[0], title='消费回暖的节奏与来源',
                        analysis='\n\n'.join(logic['analysis'] for logic in related))
        for key in ('data_refs', 'source_refs', 'calculation_refs', 'workpaper_notes'):
            combined[key] = list(dict.fromkeys(value for logic in related for value in logic[key]))
        logics[count-1:] = [combined]
    date_range = (f'{first[:4]}年{_month(first)}至{_month(last)}' if first[:4] == last[:4]
                  else f'{first[:4]}年{_month(first)}至{last[:4]}年{_month(last)}')
    return dict(title=date_range+'国内消费观察', conclusion=conclusion, sections=logics, gaps=gaps,
        workpaper_only_gaps=True, judgment=judgment, risks=[
            '上年基数和节假日错位会改变单月同比。判断需求的持续性，需结合实际销量和服务零售的同口径变化。',
        ])


def macro_demand_readout(task, facts, calculations):
    if not (scope := study_scope(task)) or not (series := selected_series(facts)):
        return None
    if scope.get('study') == RETAIL_STUDY:
        return _retail_readout(task, scope, series, facts, calculations)
    periods = scope['periods']
    first, last = periods[0], periods[-1]
    fields = ('cpi', 'core', 'ppi', 'retail', 'ex_auto')
    gaps = [f'{FIELDS[field][0]}缺少' + '、'.join(_month(period) for period in periods if period not in series.get(field, {}))
            + '的同期读数。' for field in fields if not set(periods) <= series.get(field, {}).keys()]
    def rows(field):
        return [series[field][period] for period in periods if period in series.get(field, {})]

    def ends(field):
        current = series.get(field, {})
        return [current[first], current[last]] if first in current and last in current else None

    def values_for(*keys):
        picked = [row for field in keys for row in rows(field)]
        ids = {row['id'] for row in picked}
        calculated = [row for row in calculations if row.get('method') == 'raw_percentage_points_v1'
                      and row.get('data_refs') and set(row['data_refs']) <= ids]
        return picked, calculated

    section_window = _month(first) + '至' + _month(last)
    logics = []
    for a, b, title, notes in (
        ('cpi', 'core', '居民消费价格的广度', (
            '核心CPI剔除食品和能源。其变化仍可能受服务、耐用品和上年同期基数影响。',
            '判断是否持续，用相同观察区间继续对照核心CPI环比、服务价格和更多商品类别。',)),
        ('retail', 'ex_auto', '消费品销售与汽车影响', (
            '社零使用当月名义同比。社零统计范围与CPI覆盖内容不同，估计实际消费量宜使用匹配的价格口径。',
            '判断居民总需求需同时核对服务零售及假日和普遍价格变化。剔除汽车后的读数只捕捉非汽车消费品。',)),
    ):
        group, derived = values_for(a, b)
        if not group:
            continue
        latest = [ends(key) for key in (a, b)]
        statements = []
        for field, pair in zip((a, b), latest, strict=True):
            label = FIELDS[field][0]
            if pair:
                line = label + _movement(*pair)
                original = rows(field)
                if len(original) >= 3:
                    peak = max(original, key=lambda row: Decimal(row['raw_value']))
                    if peak['period'] not in (first, last) and peak['value'] > max(value['value'] for value in pair):
                        line += f'，{_month(peak["period"])}一度达到{_reading(peak)}'
                statements.append(line)
            else:
                statements.extend(f'{_month(row["period"])}{label}为{_reading(row)}' for row in rows(field))
        text = '。'.join(statements) + '。'
        follow = ''
        if a == 'cpi' and all(latest):
            headline, core = (Decimal(pair[1]['raw_value']) - Decimal(pair[0]['raw_value']) for pair in latest)
            if headline > 0 and core <= 0:
                follow = '整体同比升幅未在剔除食品和能源后延续，核心消费价格没有同步走强。'
            elif headline > 0 and core > 0:
                follow = '剔除食品和能源后同比增速仍同步抬升，价格改善的范围延伸至核心消费品和服务合计口径。'
        if a == 'retail':
            total, ex_auto = (series.get(field, {}).get(last) for field in (a, b))
            if total and ex_auto:
                spread = Decimal(ex_auto['raw_value']) - Decimal(total['raw_value'])
                if spread:
                    impact = '拉低' if spread > 0 else '抬高'
                    relative = '高' if spread > 0 else '低'
                    follow = f'{_month(last)}非汽车商品零售同比更{relative}，汽车类零售{impact}社零整体增速。'
        complete_chart = all(len(rows(key)) == len(periods) for key in (a, b)) and len(periods) >= 3
        logics.append(_draft_section(title, text + follow, group, derived, notes,
                                     chart_fields=(a, b) if complete_chart else ()))

    ppi, derived = values_for('ppi')
    if ppi:
        comparison = ends('ppi')
        direction = '工业品与终端价格的传导'
        text = ('PPI同比' + _movement(*comparison) + '。') if comparison else '。'.join(
            f'{_month(row["period"])}PPI同比{_reading(row)}' for row in ppi) + '。'
        latest = series.get('ppi', {}).get(last)
        additions = []
        for key, verb in (('ppi_up', '拉动'), ('ppi_down', '下拉')):
            item = series.get(key, {}).get(last)
            if not (latest and item and item['locator'].get('number_of_industries')):
                continue
            count = item['locator']['number_of_industries']
            additions.append(f'{count}个行业合计{verb}约{_number(abs(Decimal(item["raw_value"])), 2)}个百分点')
            ppi.append(item)
        if additions:
            text += f'统计局{_month(last)}解读列举的行业中，' + '，'.join(additions) + '。'
        core = ends('core')
        if comparison and core and comparison[1]['value'] > comparison[0]['value'] and core[1]['value'] <= core[0]['value']:
            direction = '工业品涨价与终端价格分化'
            text += ('同期核心CPI回落。' if core[1]['value'] < core[0]['value'] else '同期核心CPI持平。')
            text += '成本向终端传导的幅度取决于各行业议价能力、库存和需求弹性。'
            ppi += core
        else:
            text += '识别工业品涨价如何传向终端，要分别检验上游投入成本、终端售价和销量。'
        logics.insert(min(len(logics), 1), _draft_section(direction, text, ppi, derived, (
            '行业贡献取同月PPI同比的公开分项解读，所列行业随月份变化。图表和全文分别保留指标口径。',
            '要确认成本传导，需要可比品种的投入价格、产量、下游成交价及时间顺序。',)))

    if not logics:
        return None
    judgment = '证据不足'
    conclusion = f'{section_window}已取得的读数分别列于下文，关键指标尚未取齐。'
    if not gaps:
        deltas = {field: [Decimal(row['raw_value']) for row in rows(field)] for field in fields}
        movements = {field: values[-1] - values[0] for field, values in deltas.items()}
        signals = [movements[field] for field in ('core', 'retail', 'ex_auto')]
        if all(value > 0 for value in signals):
            judgment = '初步支持'
            conclusion = f'{section_window}核心CPI、社零及剔除汽车后的社零同比共同走高，价格与名义销售同步改善。'
        elif all(value <= 0 for value in signals):
            judgment = '缺乏同期支持'
            lead = ('PPI同比走高，核心CPI和零售指标维持期初读数或回落。' if movements['ppi'] > 0
                    else '核心CPI和零售指标维持期初读数或回落。')
            conclusion = f'{section_window}的数据对内需普遍改善缺乏支持。' + lead
        else:
            judgment = '证据分化'
            stronger = '、'.join(FIELDS[field][0] for field in ('core', 'retail', 'ex_auto') if movements[field] > 0)
            flat_or_lower = '、'.join(FIELDS[field][0] for field in ('core', 'retail', 'ex_auto') if movements[field] <= 0)
            conclusion = f'{section_window}{stronger}走高，{flat_or_lower}持平或回落，内需改善集中在部分口径。'
    from .publication_content import requested_strategy_count

    count = requested_strategy_count(task['question'])
    if count and count < len(logics):
        related = logics[count-1:]
        combined = dict(related[0], title='价格、消费与内需判断' if count == 1 else '上游涨价与零售需求',
                        analysis='\n'.join(section['analysis'] for section in related))
        for key in ('data_refs', 'source_refs', 'calculation_refs', 'workpaper_notes'):
            combined[key] = list(dict.fromkeys(value for section in related for value in section[key]))
        logics[count-1:] = [combined]
    return dict(title=f'{first[:4]}年{section_window}物价与内需观察', conclusion=conclusion, sections=logics,
        gaps=gaps, judgment=judgment, risks=[
            '上年基数与节假日错位会扰动单月同比。服务销售和实际销量若未回升，名义零售走强的持续性会受限。',
            '工业品价格与居民消费价格的统计对象不同。成本上行能否传向终端售价，取决于供给、合同和议价条件。',
        ])


def verified_macro_answer(task, draft):
    from .draft_analysis import current_analysis
    from .draft_sources import observations

    if (task.get('run_mode') not in ('direct', 'local_draft') or draft.get('origin') != 'data_outline'
            or not (scope := study_scope(task)) or task['input_hash'] != input_fingerprint(task)):
        return None
    facts = observations(task)
    used = [row for row in facts if row.get('locator', {}).get('kind') == CELL_KIND]
    if used != [row for row in draft['facts'] if row.get('locator', {}).get('kind') == CELL_KIND]:
        return None
    analysis = current_analysis(task, facts)
    readout = macro_demand_readout(task, used, analysis.get('results', ()))
    if not readout or readout['gaps'] or readout['conclusion'] != draft['conclusion']:
        return None
    fields = ('title', 'analysis', 'data_refs', 'source_refs', 'calculation_refs')
    expected = [{key: row[key] for key in fields} for row in readout['sections']]
    if [{key: row.get(key) for key in fields} for row in draft['logics']] != expected:
        return None
    return dict(version=1, task_id=task['id'], research_version=task['version'], input_hash=task['input_hash'],
        question_id=scope['question_id'], periods=scope['periods'], judgment=readout['judgment'],
        facts_hash=fingerprint(used), answer_hash=fingerprint(dict(conclusion=readout['conclusion'], sections=expected)))
