"""Client analysis of revenue, earnings and cash flow, with linked workpaper inputs."""

from decimal import Decimal, ROUND_HALF_UP
import re

from .contracts import fingerprint
from .financial_data import UNIT_FACTORS
from .planning import input_fingerprint
from .stock_quality import (ADJUSTED, ASSET_LOSS, CASH, CASH_IN, CASH_OUT, CELL_KIND, CORE_FIELDS, COST,
                            INCOME_TAX, LABELS, MINORITY_PROFIT, POLICY, PRETAX, PROFIT, PURCHASE_PAYMENTS,
                            QUALITY_BRIDGE_VERSION, RECEIPTS, REVENUE, STRATEGY_POLICY, resolved_study_scope,
                            selected_pairs, study_scope)

PUBLICATION_POLICY = 'stock_quality_publication_version'


def _digits(value, places=2):
    rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return f'{rounded:,.{places}f}'.rstrip('0').rstrip('.')


def _amount(value, scale):
    amount = Decimal(value) * UNIT_FACTORS[scale] / UNIT_FACTORS['亿元']
    return ('负' if amount < 0 else '') + _digits(abs(amount), 1) + '亿元'


def _movement(value, unit='%'):
    if not value:
        return '持平'
    return ('上升' if value > 0 else '下降') + _digits(abs(value)) + unit


def _change_amount(row):
    value = Decimal(row['exact'])
    return ('增加' if value > 0 else '减少') + _amount(abs(value), row['unit'].removeprefix('人民币')) if value else '持平'


def _section(title, text, pairs, calculations, notes=(), *, chart=False, detail=''):
    rows = [row for pair in pairs for row in pair]
    result = dict(title=title, analysis=text, implication='', risk='', status='待验证',
        data_refs=list(dict.fromkeys(row['id'] for row in rows)),
        calculation_refs=list(dict.fromkeys(row['id'] for row in calculations if row)),
        source_refs=list(dict.fromkeys(row['artifact_id'] for row in rows)),
        knowledge_refs=[], workpaper_notes=list(notes), stock_quality_readout=True)
    if detail:
        result['detail_analysis'] = detail
    if chart:
        result['stock_growth_chart'] = True
    return result


def _comparison(calculations, method, refs):
    return next((row for row in calculations if row['method'] == method and row['data_refs'] == refs
                 and not row.get('conditional_equal_scale')), None)


def _impairment_note(row):
    note = row['locator'].get('management_explanation', {})
    value = re.sub(r'\s+', '', note.get('quote', ''))
    match = re.fullmatch(r'资产减值损失增加主要原因[:：](?:本期)?(.{3,100}?)所致。', value)
    return (match[1].replace('以及', '、').replace('商誉减值损失', '商誉减值')
            .replace('存货减值损失', '存货减值')) if match else ''


def _earnings_strategy(bridge, pairs, margin, rates):
    effects, scale = bridge['effects'], bridge['unit'].removeprefix('人民币')
    gross, impairment, tax = (effects[label] for label in ('毛利变化', '资产减值', '所得税费用'))
    change = bridge['current'] - bridge['prior']
    margin_declined = len(margin) == 2 and Decimal(margin[-1]['exact']) < Decimal(margin[0]['exact'])
    if not (gross > 0 and change < 0 and -impairment > gross and margin_declined):
        return None
    analysis = (f'毛利增加{_amount(gross, scale)}，新增资产减值损失{_amount(-impairment, scale)}，'
                f'减值已超过全部毛利增量。')
    if tax < 0:
        analysis += f'所得税费用增加{_amount(-tax, scale)}，归母净利润最终减少{_amount(-change, scale)}。'
    else:
        analysis += f'归母净利润最终减少{_amount(-change, scale)}。'
    analysis += f'扣非净利率降至{_digits(Decimal(margin[-1]["exact"]), 1)}%。'
    note = _impairment_note(pairs[ASSET_LOSS][-1])
    if note:
        analysis += f'半年报将减值增加归于{note}。'
    analysis += '待减值收敛、扣非净利率止跌后，再评估上修盈利预测。若未来利润反弹主要来自本期减值形成的低基数，盈利修复仍需验证。'
    other = effects['其他税前项目']
    other_text = ('其他税前项目合计影响接近零。' if abs(other) * UNIT_FACTORS[scale] < Decimal('5000000') else
                  f'其他税前项目合计{"增加" if other > 0 else "减少"}税前利润{_amount(abs(other), scale)}。')
    minority = effects['少数股东损益归属']
    detail = other_text
    if minority:
        detail += f'少数股东损益归属变化{"缓冲" if minority > 0 else "拖累"}归母利润{_amount(abs(minority), scale)}。'
    if PROFIT in rates and ADJUSTED in rates:
        detail += (f'归母净利润同比{_movement(rates[PROFIT])}，扣非归母净利润同比{_movement(rates[ADJUSTED])}。'
                   '后续优先核对发生减值的资产组盈利、存货跌价及费用率，再区分主业修复和低基数因素。')
    return '减值抵消毛利增量，盈利修复有待验证', analysis, detail


def _driver_amount(value, scale):
    yuan = abs(Decimal(value)) * UNIT_FACTORS[scale]
    unit = '亿元' if yuan >= 100_000_000 else '万元' if yuan >= 10_000 else '元'
    return _digits(yuan / UNIT_FACTORS[unit], 1) + unit


def _amount_change(value, scale):
    if not value:
        return '持平'
    return ('增加' if value > 0 else '减少') + _driver_amount(value, scale)


def _rank_drivers(effects, change):
    """Rank contributions that move with the outcome and the offsets separately."""
    aligned, offsets = [], []
    for name, value in effects.items():
        if value:
            (aligned if value * change > 0 else offsets).append(name)
    key = lambda name: abs(effects[name])
    return sorted(aligned, key=key, reverse=True), sorted(offsets, key=key, reverse=True)


def _contributions(effects, names, scale):
    return '、'.join(name + ('贡献' if effects[name] > 0 else '拖累') + _driver_amount(effects[name], scale)
                    for name in names)


def _gross_argument(scale_change, rate_change, revenue, prior_margin, current_margin):
    scale = scale_change['unit'].removeprefix('人民币')
    scale_effect, rate_effect = Decimal(scale_change['exact']), Decimal(rate_change['exact'])
    net = scale_effect + rate_effect
    effects = {'收入规模变化': scale_effect, '毛利率变化': rate_effect}
    same, offsets = _rank_drivers(effects, net)
    if not any(effects.values()):
        return '毛利及其构成持平', '两期收入规模与毛利率均持平。', ''
    if not net:
        title = '收入规模与毛利率变化相互抵消'
    elif scale_effect == rate_effect:
        title = '收入规模与毛利率共同影响毛利'
    elif same[0] == '收入规模变化':
        title = '增收带动毛利改善' if net > 0 else '营收回落拖累毛利'
    else:
        title = '毛利率改善支撑毛利' if net > 0 else '毛利率下行压低毛利'
    if same == ['收入规模变化'] and rate_effect > 0:
        observation = '毛利率回升缓和了收入收缩对毛利的影响。'
    elif same == ['收入规模变化'] and rate_effect < 0:
        observation = '毛利率下滑削弱了增收向毛利的转化。'
    else:
        observation = ''
    values = _contributions(effects, same + offsets, scale)
    analysis = f'毛利{_amount_change(net, scale)}，{values}。'
    if rate_effect:
        change = _movement(current_margin - prior_margin, '个百分点')
        analysis += f'本期毛利率为{_digits(current_margin, 1)}%，同比{change}。'
    check = ('如果下一期收入方向延续且毛利率没有出现更强的反向变动，当前毛利变化更可能延续。'
             if same and same[0] == '收入规模变化' else
             '判断毛利变化能否延续，需结合后续收入规模，检验毛利率变化是当期波动还是经营结构发生改变。')
    sensitivity = Decimal(revenue['raw_value']) * Decimal('0.001')
    detail = (f'按当期收入测算，毛利率每变动0.1个百分点，对应约{_driver_amount(sensitivity, scale)}毛利。'
              '若有可比的分部收入和毛利，可进一步区分变化来自业务组合，还是同一业务的毛利率。')
    return title, analysis + observation + check, detail


_EARNINGS_VALIDATION = {
    '毛利变化': '下一期若毛利变化延续且费用、减值等项目未出现更强的抵消，经营端的影响才会传导至归母利润。',
    '资产减值': '能否出现持续的盈利改善，取决于减值是否收敛，以及毛利和扣非净利率能否同步改善。若利润回升集中在减值基数，仍需核对经营端。',
    '其他税前项目': '其他税前项目是合计净影响。要区分费用、投资收益或其他损益的贡献，应比较各自附注和两期列报口径。',
    '所得税费用': '需要对照后续有效税率和税务附注，区分税率或税收事项的变化与税前经营利润变化。',
    '少数股东损益归属': '需对照合并净利润和少数股东损益，区分归母利润的变化来自整体盈利，还是股东间的利润归属。',
}


def _profit_driver_argument(bridge, pairs, adjusted_margin):
    scale = bridge['unit'].removeprefix('人民币')
    change = bridge['current'] - bridge['prior']
    effects = bridge['effects']
    same, offsets = _rank_drivers(effects, change)
    if not any(effects.values()):
        return None
    movement = '增长' if change > 0 else '下降'
    main = same[0] if same else None
    tight = len(same) >= 2 and abs(effects[same[0]]) == abs(effects[same[1]])
    if main is None:
        headline = '利润构成变动相互抵消'
    elif tight:
        headline = '多项损益共同推动归母利润' + movement
    else:
        labels = {'毛利变化': '毛利增长' if change > 0 else '毛利回落',
                  '所得税费用': '税费减少' if change > 0 else '税费增加',
                  '资产减值': '减值减少' if change > 0 else '减值增加'}
        headline = labels.get(main, main) + ('支撑' if change > 0 else '拖累') + '归母利润'
    cited = same[:2] + offsets[:1] if change else offsets
    lines = f'归母净利润{_amount_change(change, scale)}，{_contributions(effects, cited, scale)}。'
    confirming = (_impairment_note(pairs[ASSET_LOSS][-1]) if effects['资产减值'] < 0 and '资产减值' in cited else '')
    if confirming:
        lines += f'半年报将减值增加归于{confirming}。'
    if len(adjusted_margin) == 2:
        earlier, current = (Decimal(row['exact']) for row in adjusted_margin)
        lines += f'扣非净利率为{_digits(current, 1)}%，同比{_movement(current-earlier, "个百分点")}。'
    checks = same[:2] if tight else same[:1]
    check = ''.join(
        '若利润回升主要来自减值减少，而毛利和扣非净利率并未改善，经营盈利修复仍需验证。'
        if name == '资产减值' and effects[name] > 0 else _EARNINGS_VALIDATION[name]
        for name in checks)
    if not check:
        check = '净利润持平掩盖了构成变化。后续应核对本期相互抵消的损益项目是否仍同时出现。'
    others = [name for name, effect in effects.items() if effect and name not in cited]
    detail = (f'{_contributions(effects, others, scale)}。' if others else '')
    detail += '按同口径合并利润表比较毛利与税前损益，再计入所得税和少数股东损益，归因合计与归母净利润变动一致。'
    return headline, lines + check, detail


def _profit_gap_argument(changes):
    fields = (REVENUE, COST, PROFIT)
    if not all(changes[field] for field in fields):
        return None
    income, cost, parent = (changes[field] for field in fields)
    if len({row['unit'] for row in (income, cost, parent)}) != 1:
        return None
    scale = parent['unit'].removeprefix('人民币')
    gross = Decimal(income['exact']) - Decimal(cost['exact'])
    net = Decimal(parent['exact'])
    remaining = net - gross
    if not remaining or gross * net > 0:
        return None
    title = '毛利与归母利润的变化出现分化'
    paragraph = (f'毛利{_amount_change(gross, scale)}，归母净利润{_amount_change(net, scale)}。'
                 f'两个增量的差额为{_driver_amount(remaining, scale)}，来自毛利以下项目的合计影响。'
                 '下一步应通过费用、其他损益、税费及利润归属的同比差额判断各项贡献。')
    return title, paragraph


_CASH_VALIDATION = {
    '销售收现': '后续把销售收现与收入增速、应收周转并列，区分销售规模变化和跨期回款。',
    '采购付款': '对照后续采购付款与应付、预付项目，可区分采购活动变化和付款时点变化。',
    '其他经营流入': '需核对其他经营收款的构成，区分经营带来的持续流入与当期集中收款。',
    '其他经营流出': '需核对薪酬、税费及其他经营付款明细，区分经营支出变化与集中结算。',
    '经营现金流入': '结合销售收现和其他经营收款的明细，才能区分流入端是销售变化还是收款时点变化。',
    '经营现金流出': '结合采购、薪酬及税费项目，才能区分经营支出变化与付款时点变化。',
}


def _cash_driver_argument(pairs, changes, receipts):
    if not all(changes[field] for field in (CASH, CASH_IN, CASH_OUT)):
        return None
    fields = (CASH, CASH_IN, CASH_OUT)
    net, inflow, outflow = (Decimal(changes[field]['exact']) for field in fields)
    unit = changes[CASH]['unit']
    if any(changes[field]['unit'] != unit for field in fields) or net != inflow - outflow:
        return None
    usable_receipts = receipts and receipts['unit'] == unit
    purchase = changes[PURCHASE_PAYMENTS]
    usable_purchase = purchase and purchase['unit'] == unit
    income_label = '其他经营流入' if usable_receipts else '经营现金流入'
    expense_label = '其他经营流出' if usable_purchase else '经营现金流出'
    effects = {}
    if usable_receipts:
        effects['销售收现'] = Decimal(receipts['exact'])
    effects[income_label] = inflow - effects.get('销售收现', 0)
    if usable_purchase:
        effects['采购付款'] = -Decimal(purchase['exact'])
    effects[expense_label] = -outflow - effects.get('采购付款', 0)
    if not any(effects.values()):
        return None
    same, offsets = _rank_drivers(effects, net)
    scale = unit.removeprefix('人民币')
    main = same[0] if same else None
    headline = ('经营收付的变动相互抵消' if main is None else
                main + ('推动' if net > 0 else '拖累') + '经营现金流')
    tied = len(same) >= 2 and abs(effects[same[0]]) == abs(effects[same[1]])
    if tied:
        headline = '多项收付共同影响经营现金流'
    cited = same[:2] + offsets[:1] if net else offsets
    analysis = f'经营净现金流{_amount_change(net, scale)}，{_contributions(effects, cited, scale)}。'
    checks = list(dict.fromkeys([main or offsets[0],
                                *[name for name in cited if name in ('销售收现', '采购付款')]]))[:2]
    analysis += ''.join(_CASH_VALIDATION[name] for name in checks)
    detail = '按同一合并现金流量表，将两期经营流入和流出的差额分解到已取得的收付款分项。'
    scenario = next((name for name in ('采购付款', '销售收现') if name in cited and effects[name]), None)
    if scenario:
        current = Decimal(pairs[CASH][-1]['raw_value'])
        scenario_label = '采购及劳务付款' if scenario == '采购付款' else '销售收现'
        value = current - effects[scenario]
        estimate = ('负' if value < 0 else '') + _driver_amount(value, scale)
        detail += f'若将本期{scenario_label}按上年同期金额测算，其余经营收支维持本期读数，经营净现金流约为{estimate}。'
    return headline, analysis, detail, usable_receipts, usable_purchase


def stock_quality_readout(task, facts, calculations):
    scope = study_scope(task)
    if not scope or not (scope := resolved_study_scope(task, scope)) or not (pairs := selected_pairs(task, facts)):
        return None
    from .draft_analysis import GROSS_MARGIN_EFFECT, GROSS_SCALE_EFFECT, stock_profit_attribution
    from .planning import question_driven_arguments

    driven = question_driven_arguments(task)
    focus = scope.get('focus', 'operating')
    detailed = task.get(POLICY) == QUALITY_BRIDGE_VERSION
    outcome_copy = task.get(PUBLICATION_POLICY) == 1
    strategy = task.get(STRATEGY_POLICY) == 1
    calculations = list(calculations)

    def paired(field, method='raw_change_v1'):
        return _comparison(calculations, method, [row['id'] for row in pairs.get(field, ())])

    def derived(kind):
        return [row for row in calculations if row.get('comparison_kind') == kind]

    def amount(row):
        return _amount(row['raw_value'], row['raw_unit'])

    def pair_sentence(field):
        prior, current = pairs[field]
        if change := rates[field]:
            return f'{LABELS[field]}为{amount(current)}，同比{_movement(Decimal(change["exact"]))}'
        return f'{LABELS[field]}由上年同期的{amount(prior)}变动至{amount(current)}'

    rates = {field: paired(field) for field in pairs}
    change = {field: paired(field, 'raw_difference_v1') for field in
              (REVENUE, COST, CASH_IN, CASH_OUT, CASH, PURCHASE_PAYMENTS, ASSET_LOSS, PRETAX, INCOME_TAX, MINORITY_PROFIT, PROFIT)}
    rates_exact = {field: Decimal(row['exact']) for field, row in rates.items() if row}
    revenue_contracted = scope.get('standard_columns') and rates_exact.get(REVENUE, Decimal(0)) < 0
    gross_margin = derived('stock_gross_margin')
    adjusted_margin = derived('stock_adjusted_margin')
    cash_margin = derived('stock_cash_revenue')
    sections = {}
    if REVENUE in pairs and focus == 'operating':
        notes = ['营业收入与同份半年报的上年同期列对照。']
        refs = [pairs[REVENUE]]
        used = [rates[REVENUE]]
        title = '营业收入与增长幅度'
        analysis = pair_sentence(REVENUE) + '。'
        detail = ''
        if len(gross_margin) == 2 and change[REVENUE] and change[COST]:
            before, current = [Decimal(row['exact']) for row in gross_margin]
            title = '营收回落与毛利率' if revenue_contracted else '营收增量与毛利率'
            analysis = (f'营业收入同比{_change_amount(change[REVENUE])}，营业成本{_change_amount(change[COST])}。'
                        f'毛利率为{_digits(current)}%，同比{_movement(current - before, "个百分点")}。')
            if current < before and Decimal(change[REVENUE]['exact']) > 0:
                analysis += ('营业成本增速高于营收，成本率上升抵消了部分规模扩张带来的毛利。'
                             if detailed else '营业成本上升快于收入，销售规模扩张的盈利弹性收窄。')
            elif current > before and Decimal(change[REVENUE]['exact']) > 0:
                analysis += '收入增长的同时毛利率提高，规模扩张和毛利率改善共同贡献毛利。'
            notes.append('营业成本取合并利润表中营业成本一项。表中的支出括号金额转为正数用于同比与毛利率计算。')
            refs.append(pairs[COST])
            used = [change[REVENUE], change[COST], *gross_margin]
            bridge = {row['method']: row for row in calculations if row['method'] in (GROSS_SCALE_EFFECT, GROSS_MARGIN_EFFECT)}
            if detailed and GROSS_SCALE_EFFECT in bridge and GROSS_MARGIN_EFFECT in bridge:
                scale, margin = bridge[GROSS_SCALE_EFFECT], bridge[GROSS_MARGIN_EFFECT]
                margin_effect = Decimal(margin['exact'])
                margin_sentence = ('毛利率变化使毛利' + _change_amount(margin) + '。'
                                   if margin_effect else '同期毛利率持平。')
                if driven:
                    title, analysis, detail = _gross_argument(scale, margin, pairs[REVENUE][-1], before, current)
                elif revenue_contracted:
                    comparison = ('毛利率回升缓和了收入收缩对毛利的影响。' if margin_effect > 0 else
                                  '毛利率下滑加重了收入收缩对毛利的影响。' if margin_effect < 0 else '')
                    analysis += '收入规模变化使毛利' + _change_amount(scale) + '。' + margin_sentence + comparison
                    detail = '规模贡献以上年同期毛利率衡量，毛利率贡献按本期收入衡量。'
                else:
                    detail = f'按上年同期毛利率计算，收入规模变化使毛利{_change_amount(scale)}。' + margin_sentence
                    if strategy and 0 < margin_effect < Decimal(scale['exact']):
                        title = '增收带动毛利修复，延续性取决于毛利率'
                        gross = Decimal(scale['exact']) + margin_effect
                        unit = scale['unit'].removeprefix('人民币')
                        sensitivity = Decimal(pairs[REVENUE][-1]['raw_value']) * Decimal('0.001')
                        detail = (f'按本期收入测算，毛利率每变动0.1个百分点，对应约{_amount(sensitivity, unit)}毛利。'
                                  '由此观察增收能否弥补渠道促销和成本上升带来的毛利压力。')
                        analysis = (f'毛利增加{_amount(gross, unit)}，其中收入规模贡献{_amount(scale["exact"], unit)}，'
                                    f'毛利率提升贡献{_amount(margin_effect, unit)}。'
                                    f'当前毛利率升至{_digits(current, 1)}%，后续若收入继续增长且毛利率维持改善，'
                                    '毛利修复具备延续性。若增收依赖促销让利，且毛利率回落压缩毛利增量，应下修毛利预期。')
                if not driven:
                    detail += ('若品类毛利率基本稳定、高毛利品类收入占比改变，组合变化可以解释合并毛利率的变动。'
                               '若品类占比基本稳定而同品类毛利率变动，先核对品类内部的售价与投入成本。')
                used.extend((scale, margin))
        sections['gross'] = _section(title, analysis, refs, used, notes, detail=detail)

    profit_fields = [field for field in (PROFIT, ADJUSTED) if field in pairs]
    profit_bridge = stock_profit_attribution(pairs) if strategy else None
    earnings_strategy = None
    if profit_bridge and focus in ('operating', 'earnings'):
        earnings_strategy = (_profit_driver_argument(profit_bridge, pairs, adjusted_margin) if driven else
                             _earnings_strategy(profit_bridge, pairs, adjusted_margin, rates_exact))
    if profit_fields and focus in ('operating', 'earnings'):
        notes = []
        detail = ''
        analysis = '。'.join(pair_sentence(field) for field in profit_fields) + '。'
        both_falling = all(rates_exact.get(field, 0) < 0 for field in (PROFIT, ADJUSTED))
        below_gross = (outcome_copy and both_falling and len(gross_margin) == 2
                       and rates_exact.get(REVENUE, 0) > 0
                       and Decimal(gross_margin[-1]['exact']) >= Decimal(gross_margin[0]['exact']))
        title = '归母与扣非利润的分化' if len(profit_fields) == 2 else '利润变化'
        if outcome_copy and both_falling:
            title = '归母与扣非利润同步回落'
        if len(adjusted_margin) == 2:
            before, current = [Decimal(row['exact']) for row in adjusted_margin]
            analysis = (f'扣非归母净利润为{amount(pairs[ADJUSTED][-1])}，扣非净利率为{_digits(current)}%，'
                        f'同比{_movement(current - before, "个百分点")}。')
            if (revenue_contracted or outcome_copy and both_falling) and PROFIT in rates_exact and ADJUSTED in rates_exact:
                analysis = (f'归母净利润同比{_movement(rates_exact[PROFIT])}，'
                            f'扣非归母净利润同比{_movement(rates_exact[ADJUSTED])}。' + analysis)
            if below_gross:
                analysis += '收入和毛利率同时上升，扣非利润却下降，需从毛利以下的费用、损益和税费寻找差异。'
        gaps = derived('stock_reported_adjusted_gap')
        if len(gaps) == 2 and gaps[0]['unit'] == gaps[1]['unit']:
            old_gap, new_gap = gaps
            scale = new_gap['unit'].removeprefix('人民币')
            analysis += (f'归母与扣非利润差额由{_amount(old_gap["exact"], scale)}变为'
                         f'{_amount(new_gap["exact"], scale)}。')
            if (revenue_contracted and rates_exact.get(PROFIT, 0) < 0
                    and Decimal(new_gap['exact']) > Decimal(old_gap['exact'])):
                analysis += '非经常性损益净贡献增加，缓和了归母利润的下滑。'
            notes.append('归母利润减扣非归母利润为非经常性损益对归母利润的净影响。')
            if detailed:
                detail = ('归母利润与扣非利润的差额反映当期非经常性损益的净贡献。'
                          '用附注区分各项损益的规模与持续性，并以扣非净利率对照主业盈利变化。')
        if ADJUSTED in pairs and pairs[ADJUSTED][-1]['locator'].get('management_explanation'):
            analysis += ('半年报将外币货币性项目的汇兑损失列入经常性损益，'
                         '相关外汇对冲收益列入非经常性损益，这一列报差异扩大了两种利润口径的分化。')
            notes.append('汇兑和外汇套保的列报解释摘自半年报主要财务指标附注。')
            if detailed:
                detail = ('判断经营盈利与外汇损益各自的影响，应并列比较汇兑损益与对冲收益的净额、'
                          '剔除汇率影响后的分部利润率。若分部利润率稳定且利润变动主要来自外汇净损益，'
                          '盈利分化可更多由汇率因素解释。若汇率扰动减弱后分部利润率仍下行，'
                          '应下调主营盈利修复的判断。')
        chart = focus == 'operating' and all(rates.get(field) for field in CORE_FIELDS)
        profit_refs = list(CORE_FIELDS) if chart else list(profit_fields)
        if adjusted_margin and REVENUE not in profit_refs:
            profit_refs.append(REVENUE)
        if below_gross:
            profit_refs.extend(field for field in (REVENUE, COST) if field not in profit_refs)
        profit_calculations = [*(rates[field] for field in profit_refs), *gaps, *adjusted_margin,
                               *(gross_margin if below_gross else ())]
        if earnings_strategy:
            title, analysis, detail = earnings_strategy
            bridge_fields = (REVENUE, COST, ASSET_LOSS, PRETAX, INCOME_TAX, MINORITY_PROFIT, PROFIT)
            profit_refs.extend(field for field in bridge_fields if field not in profit_refs)
            profit_calculations.extend(change[field] for field in bridge_fields if field in change)
            notes.append('其他税前项目按利润总额变动扣除毛利和资产减值的变动计算。税费和少数股东损益按其对归母净利润的方向列示。')
        if driven and strategy and not earnings_strategy and focus == 'earnings' and (income_gap := _profit_gap_argument(change)):
            title, first = income_gap
            analysis = first + analysis
            profit_refs.extend(field for field in (REVENUE, COST, PROFIT) if field not in profit_refs)
            profit_calculations.extend(change[field] for field in (REVENUE, COST, PROFIT))
            notes.append('以营业收入减营业成本计算合并毛利，再对照归母净利润变化。')
        section = _section(title,
            analysis, [pairs[field] for field in profit_refs],
            profit_calculations, notes, chart=chart and not earnings_strategy, detail=detail)
        if earnings_strategy:
            section['stock_profit_chart'] = True
        sections['earnings'] = section

    cash_strategy = False
    if CASH in pairs and focus in ('operating', 'cash'):
        analysis = pair_sentence(CASH) + '。'
        title = '现金流与回款'
        cash_fields = [field for field in (REVENUE, CASH, RECEIPTS) if field in pairs]
        notes = []
        used = [rates.get(RECEIPTS)]
        if change[CASH_IN] and change[CASH_OUT] and change[CASH]:
            analysis = (f'经营现金流入同比{_change_amount(change[CASH_IN])}，现金流出{_change_amount(change[CASH_OUT])}，'
                        f'经营净现金流{_change_amount(change[CASH])}。')
            cash_fields.extend((CASH_IN, CASH_OUT))
            used.extend(change[field] for field in (CASH_IN, CASH_OUT, CASH))
            notes.append('合并现金流量表两期的流入减流出与半年报摘要经营现金流净额一致。')
        elif rates[CASH]:
            used.append(rates[CASH])
        if RECEIPTS in pairs:
            receipt_change = rates_exact.get(RECEIPTS)
            if receipt_change is not None and 0 < receipt_change < rates_exact.get(REVENUE, Decimal('0')):
                analysis += f'销售回款同比上升{_digits(receipt_change)}%，慢于收入增长。'
            else:
                analysis += pair_sentence(RECEIPTS) + '。'
        if len(cash_margin) == 2:
            before, current = [Decimal(row['exact']) for row in cash_margin]
            analysis += (f'经营现金流占营收的比重为{_digits(current)}%，'
                         f'同比{_movement(current - before, "个百分点")}。')
            notes.append('销售收现包括跨期回款，经营现金流率使用同期累计经营现金流和营业收入。')
        if (revenue_contracted and rates_exact.get(RECEIPTS, 0) < rates_exact[REVENUE]
                and rates_exact.get(CASH, 0) < 0 and change[CASH_IN] and change[CASH_OUT]
                and -Decimal(change[CASH_IN]['exact']) > max(0, Decimal(change[CASH_OUT]['exact']))):
            analysis += '销售收现降幅大于营收降幅，经营现金流转弱主要体现在流入端。'
        detail = ''
        if detailed and RECEIPTS in pairs and REVENUE in pairs:
            if outcome_copy and RECEIPTS in rates_exact and rates_exact[RECEIPTS] > rates_exact.get(REVENUE, 0):
                detail = '本期销售收现增速快于收入。将销售收现与应收周转和账龄对照，区分回收改善与跨期收款。'
                if change[CASH_OUT] and Decimal(change[CASH_OUT]['exact']) < 0:
                    detail += ('经营付款下降也推高净现金流。比较采购付款、薪酬和税费，并结合应付和预付款变化，'
                               '判断支出下降来自经营效率改善还是付款时点后移。')
            else:
                detail = ('销售回款覆盖本期和以前期间销售，单期增速差同时受信用账期和结算时点影响。'
                          '下期将销售回款与营收增速、应收周转和账龄放在同一窗口对照。'
                          '若销售回款追上营收，而应收周转和账龄稳定，本期差异更符合阶段性结算错位。'
                          '回款持续落后且应收周转放缓、长账龄应收占比上升，则指向现金回收压力。')
        receipt_change = paired(RECEIPTS, 'raw_difference_v1')
        if strategy and driven:
            cash_argument = _cash_driver_argument(pairs, change, receipt_change)
            if cash_argument:
                cash_strategy = True
                title, analysis, detail, use_receipts, use_purchase = cash_argument
                if use_receipts:
                    used.append(receipt_change)
                if use_purchase:
                    cash_fields.append(PURCHASE_PAYMENTS)
                    used.append(change[PURCHASE_PAYMENTS])
        elif (strategy and change[PURCHASE_PAYMENTS] and change[CASH] and receipt_change
                and change[CASH]['unit'] == change[PURCHASE_PAYMENTS]['unit'] == receipt_change['unit']):
            purchase_savings = -Decimal(change[PURCHASE_PAYMENTS]['exact'])
            cash_gain = Decimal(change[CASH]['exact'])
            receipt_gain = Decimal(receipt_change['exact'])
            if min(purchase_savings, cash_gain, receipt_gain) > 0:
                cash_strategy = True
                scale = change[CASH]['unit'].removeprefix('人民币')
                title = '现金流已改善，持续性要看付款恢复后的收现'
                analysis = (f'经营净现金流增加{_amount(cash_gain, scale)}，销售收现增长贡献{_amount(receipt_gain, scale)}，'
                            f'采购及劳务付款减少贡献{_amount(purchase_savings, scale)}。'
                            '若采购付款恢复后销售收现增速仍能跟上营收，且应收周转稳定，可上调对现金兑现的判断。'
                            '若净现金流改善主要伴随应付票据和账款累积，则应按正常付现节奏重估。')
                normalized = Decimal(pairs[CASH][-1]['raw_value']) - purchase_savings
                detail = (f'若把本期采购及劳务付款按上年同期金额测算，其余经营收支采用本期读数，'
                          f'经营净现金流约为{_amount(normalized, scale)}。'
                          '将销售收现与应收周转和账龄并列，再将采购付款与应付及预付款对照，'
                          '可以区分回收效率改善与收付时点变化。')
                cash_fields.append(PURCHASE_PAYMENTS)
                used.extend((receipt_change, change[PURCHASE_PAYMENTS]))
                notes.append('采购付款变动来自合并现金流量表，作为经营流出分项。付款恢复上期金额的比较保持其他经营收支不变。')
        sections['cash'] = _section(title, analysis, [pairs[field] for field in cash_fields],
                                    [*used, *cash_margin], notes, detail=detail)

    if not sections and RECEIPTS in pairs and focus != 'earnings':
        sections['cash'] = _section('销售回款', pair_sentence(RECEIPTS) + '。',
                                    [pairs[RECEIPTS]], [rates[RECEIPTS]])

    required = {'operating': CORE_FIELDS, 'earnings': (PROFIT,), 'cash': (CASH,)}[focus]
    missing = [LABELS[field] for field in required if field not in pairs]
    gaps = [f'{label}缺少可比的本期或上年同期数据。' for label in missing]
    name = scope['name']
    if gaps:
        judgment = '可比指标覆盖不全'
        observed = '，'.join(LABELS[field] + '同比' + _movement(rates_exact[field])
                            for field in required if field in rates_exact)
        conclusion = (observed + '。' if observed else '') + '、'.join(missing) + '暂无两期可比读数。'
    elif focus != 'operating':
        field = PROFIT if focus == 'earnings' else CASH
        argument = earnings_strategy if focus == 'earnings' else cash_strategy
        judgment = sections[focus]['title']
        conclusion = name + pair_sentence(field) + '。'
        if argument:
            conclusion += judgment + '。'
        if scope.get('explain_change') and not argument:
            conclusion += '现有证据可确认变化方向，具体驱动仍需同口径明细支持。'
            gaps.append('缺少可用于归因的同口径明细。')
    elif Decimal(pairs[REVENUE][-1]['raw_value']) <= Decimal(pairs[REVENUE][0]['raw_value']):
        judgment = '收入增长前提未成立'
        conclusion = pair_sentence(REVENUE) + '。'
        if revenue_contracted and ADJUSTED in rates_exact and CASH in rates_exact:
            conclusion = (f'{name}上半年营收同比{_movement(rates_exact[REVENUE])}，'
                          f'扣非归母净利润{_movement(rates_exact[ADJUSTED])}，'
                          f'经营现金流净额{_movement(rates_exact[CASH])}。')
            if rates_exact[CASH] < min(0, rates_exact[ADJUSTED]):
                conclusion += '现金流较利润承压更明显，销售收现值得重点跟踪。'
    elif REVENUE not in rates_exact:
        judgment = '收入基数非正'
        conclusion = pair_sentence(REVENUE) + '。上年同期收入基数非正，跨期对照采用金额变化。'
    else:
        sales = rates_exact[REVENUE]
        lagging = [LABELS[field] for field in CORE_FIELDS[1:] if 0 <= rates_exact.get(field, Decimal('-Infinity')) < sales]
        falling = [LABELS[field] for field in CORE_FIELDS[1:]
                   if Decimal(pairs[field][-1]['raw_value']) < Decimal(pairs[field][0]['raw_value'])]
        different_baseline = [LABELS[field] for field in CORE_FIELDS[1:] if field not in rates_exact and LABELS[field] not in falling]
        if lagging or falling or different_baseline:
            judgment = '收入增长未同步兑现'
            detail = []
            if lagging:
                detail.append('、'.join(lagging) + '增幅低于营收')
            if falling:
                detail.append('、'.join(falling) + '同比下降')
            if different_baseline:
                detail.append('、'.join(different_baseline) + '采用两期金额比较')
            if outcome_copy and rates_exact.get(CASH, 0) >= sales:
                detail.append('经营现金流净额同比' + _movement(rates_exact[CASH]))
            conclusion = f'{name}上半年营收同比增长{_digits(sales)}%。' + '，'.join(detail) + '。'
        else:
            judgment = '本期同步兑现'
            conclusion = f'{name}上半年营业收入、扣非利润及经营现金流同向增长，利润和经营现金流增速达到收入增速。'
    if not gaps and driven and focus == 'operating' and earnings_strategy:
        conclusion = (name + pair_sentence(REVENUE) + '。' + sections['earnings']['title'] + '。'
                      + (sections['cash']['title'] + '。' if cash_strategy else pair_sentence(CASH) + '。'))
    if not gaps and not driven and earnings_strategy and cash_strategy:
        conclusion = (f'{name}上半年已出现毛利修复和现金流改善。上修盈利预期需要减值收敛、扣非净利率止跌，'
                      '采购付款恢复后的现金表现可用于检验经营改善的持续性。')

    from .publication_content import requested_strategy_count

    sections = list(sections.values())
    count = requested_strategy_count(task['question'])
    if count and count < len(sections):
        tail = sections[count-1:]
        combined = dict(tail[0], title='经营增长与利润现金兑现' if count == 1 else '利润与现金兑现',
                        analysis='\n'.join(row['analysis'] for row in tail))
        for key in ('data_refs', 'source_refs', 'calculation_refs', 'workpaper_notes'):
            combined[key] = list(dict.fromkeys(value for row in tail for value in row[key]))
        if detail := '\n'.join(row['detail_analysis'] for row in tail if row.get('detail_analysis')):
            combined['detail_analysis'] = detail
        if any(row.get('stock_growth_chart') for row in tail):
            combined['stock_growth_chart'] = True
        if any(row.get('stock_profit_chart') for row in tail):
            combined['stock_profit_chart'] = True
        sections[count-1:] = [combined]

    risks = []
    if len(gross_margin) == 2 and focus == 'operating':
        if revenue_contracted:
            risks.append('若营收继续回落且成本降幅小于收入降幅，毛利率将承压。')
        elif outcome_copy and Decimal(gross_margin[-1]['exact']) >= Decimal(gross_margin[0]['exact']):
            risks.append('营业成本增速若超过收入增速，毛利率改善可能回吐。')
        else:
            risks.append('营业成本若持续快于营收增长，毛利率将继续承压。')
    if earnings_strategy and (not driven or profit_bridge['effects']['资产减值'] < 0):
        risks.append('资产进一步减值将拖累归母利润。')
    if CASH in pairs and focus != 'earnings':
        risks.append('销售回款放缓或经营付款提速会压缩经营净现金流。')
    if focus != 'cash' and ADJUSTED in pairs and pairs[ADJUSTED][-1]['locator'].get('management_explanation'):
        risks.append('汇兑损失延续将拖累扣非利润率。')
    topic = {'earnings': '盈利质量研究', 'cash': '经营现金流研究',
             'operating': '经营质量研究' if revenue_contracted else '增长质量研究'}[focus]
    output = dict(title=f'{name}{scope["year"]}年上半年{topic}', conclusion=conclusion, sections=sections,
                  risks=risks, gaps=gaps, judgment=judgment)
    if focus != 'operating':
        if scope.get('explain_change'):
            output['organization'] = 'mechanism'
        if not missing:
            output['workpaper_only_gaps'] = True
    return output


def verified_stock_quality_answer(task, draft):
    from .draft_analysis import current_analysis
    from .draft_sources import observations

    if (task.get('run_mode') not in ('direct', 'local_draft') or draft.get('origin') != 'data_outline'
            or not (scope := study_scope(task)) or scope.get('whole_question') is False
            or not (scope := resolved_study_scope(task, scope)) or task['input_hash'] != input_fingerprint(task)):
        return None
    facts = observations(task)
    used = [row for row in facts if row.get('locator', {}).get('kind') == CELL_KIND]
    if used != [row for row in draft['facts'] if row.get('locator', {}).get('kind') == CELL_KIND]:
        return None
    analysis = current_analysis(task, facts)
    readout = stock_quality_readout(task, used, analysis.get('results', ()))
    if not readout or readout['gaps'] or readout['conclusion'] != draft['conclusion']:
        return None
    fields = ('title', 'analysis', 'data_refs', 'source_refs', 'calculation_refs')
    if task.get(POLICY) == QUALITY_BRIDGE_VERSION:
        fields += ('detail_analysis',)
    expected = [{key: row.get(key) for key in fields} for row in readout['sections']]
    if [{key: row.get(key) for key in fields} for row in draft['logics']] != expected:
        return None
    pairs = selected_pairs(task, used)
    return dict(version=1, task_id=task['id'], research_version=task['version'], input_hash=task['input_hash'],
        question_id=scope['question_id'], issuer_code=next(iter(pairs.values()))[-1]['code'], periods=scope['periods'],
        judgment=readout['judgment'], facts_hash=fingerprint(used),
        answer_hash=fingerprint(dict(conclusion=readout['conclusion'], sections=expected)))
