"""Registered arithmetic over draft observations, never financial approval.

The reviewed-calculation registry is unchanged. These methods describe raw
values with explicit scope and comparability conditions; failures are local.
"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Context, Decimal, DecimalException, InvalidOperation, ROUND_HALF_UP, localcontext
import math

from .contracts import (ContractError, METHOD_REGISTRY, calculate, calculation_context_contract, fingerprint,
                        validate_calculation_context)
from .financial_data import CASH_BRIDGE_FIELDS, UNIT_FACTORS, matched_summary_comparison
from .financial_sources import (BANK_CREDIT_IMPAIRMENT, BANK_INTEREST_INCOME, BANK_PROFIT_COMPONENTS,
                                PEER_ADJUSTED_PROFIT, PEER_CASH_COMPONENTS, PEER_OPERATING_FIELDS,
                                PEER_PROFIT_COMPONENTS, PEER_PROFIT_DETAIL_VERSION, PEER_PROFIT_POLICY,
                                PEER_REVENUE_COMPONENTS)
from .draft_filing_amounts import SEGMENT_CHANGE, segment_field
from .etf_comparison import (BENCHMARK, CUSTODY, FEE_SUM, MANAGEMENT, PERFORMANCE, TRACKING_GAP,
                             comparison_scope, product_observations)
from .etf_trading import AVERAGE as ETF_TURNOVER_AVERAGE, CELL_KIND as ETF_TRADING_CELL
from .etf_trading import CLOSE as ETF_CLOSE, NAV as ETF_NAV, TURNOVER as ETF_TURNOVER
from .index_sources import (DRAFT_WEIGHT_POLICY, DRAFT_WEIGHT_REFRESH_VERSION, DRAFT_WEIGHT_VERSION,
                            stated_weight_sheets, weight_total_label)
from .market_series import (VALUATION_HISTORY_MONTHS, VALUATION_HISTORY_POLICY, VALUATION_HISTORY_VERSION,
                            valuation_attribution_enabled, valuation_attribution_points, valuation_history_sample,
                            valuation_history_scope)
from .tinyshare_sources import analysis_field

VERSION = 2
WEIGHT_TOTAL_METHOD = 'raw_index_weight_sum_v1'
DAILY_NAV_PREMIUM_METHOD = 'etf_daily_nav_premium_v1'
PEER_REVENUE_CHANGE = 'peer_revenue_component_change'
BANK_PROFIT_CHANGE = 'bank_profit_component_change'
GROSS_SCALE_EFFECT = 'stock_gross_scale_effect_v1'
GROSS_MARGIN_EFFECT = 'stock_gross_margin_effect_v1'
GROSS_BRIDGE_METHODS = (GROSS_SCALE_EFFECT, GROSS_MARGIN_EFFECT)
PEER_NET_AFTER_GROSS = 'peer_net_after_gross_v1'
PEER_PROFIT_LINE_CHANGE = 'peer_profit_line_change'
PEER_PROFIT_FIELDS = ('ths_revenue_stock', 'ths_operating_cost_stock', 'ths_np_atoopc_stock')
PEER_PROFIT_INPUTS = ('同期营业收入', '同期营业成本', '同期归母净利润', '本期营业收入', '本期营业成本', '本期归母净利润')
VALUATION_POSITION = 'valuation_history_position'
VALUATION_POSITION_METHOD = 'pe_history_percentile_v1'
VALUATION_ATTRIBUTION = 'pe_price_attribution'
PE_MULTIPLE_EFFECT = 'pe_multiple_effect_v1'
PE_EARNINGS_EFFECT = 'pe_implied_earnings_effect_v1'
PE_EARNINGS_SCENARIO = 'pe_earnings_at_reference_multiple_v1'
VALUATION_SCENARIO = 'pe_earnings_reference_scenario'
RETAIL_CONTRIBUTION = 'nbs_retail_contribution_v1'
YIELD_BASIS_POINTS = 'yield_basis_points_v1'
YIELD_CURVE_SHIFT = 'yield_curve_shift_v1'
PE_ATTRIBUTION_METHODS = {PE_MULTIPLE_EFFECT: '估值倍数', PE_EARNINGS_EFFECT: '隐含滚动盈利'}
_PE_ATTRIBUTION_FIELDS = ('start_price', 'end_price', 'start_pe', 'end_pe')
_PE_ATTRIBUTION_BASIS = ('按同日收盘价除以PE(TTM)得到隐含滚动盈利。rE为隐含盈利变化率，rM为倍数变化率，'
                         '两项各分配一半交互项。分解口径为未复权收盘价变化。')
GROSS_BRIDGE_INPUTS = ('同期营收', '同期成本', '本期营收', '本期成本')
_DAILY_PREMIUM = METHOD_REGISTRY[DAILY_NAV_PREMIUM_METHOD]
METHODS = {
    'raw_change_v1': dict(version=1, title='可比原值变化率', formula=r'(b/a-1)\times100',
        fields=['a','b'], unit='%', condition='同序列或同字段可比期间，基期为正；不等于总收益或预测。'),
    'raw_difference_v1': dict(version=1, title='可比原值差额', formula=r'b-a',
        fields=['a','b'], unit='原值', condition='同字段、同尺度，仅描述差额。'),
    'raw_percent_v1': dict(version=1, title='原值百分比', formula=r'\frac{a}{b}\times100',
        fields=['a','b'], unit='%', condition='分母为正，各输入尺度一致；不是已核验财务指标。'),
    'raw_multiple_v1': dict(version=1, title='原值倍数', formula=r'\frac{a}{b}',
        fields=['a','b'], unit='倍', condition='分母为正，各输入尺度一致；不代表现金流质量或投资结论。'),
    'raw_margin_v1': dict(version=1, title='原值收入成本差占比', formula=r'\frac{a-b}{a}\times100',
        fields=['a','b'], unit='%', condition='收入为正、营业成本非负且同尺度，不以营业总成本替代。'),
    'raw_percentage_points_v1': dict(version=1, title='百分比读数差', formula=r'b-a',
        fields=['a','b'], unit='个百分点', condition='同一百分比序列的读数差；不等于指数涨跌幅、环比增速或因果。'),
    YIELD_BASIS_POINTS: dict(version=1, title='名义收益率差', formula=r'(b-a)\times100',
        fields=['a', 'b'], unit='基点', condition='输入按百分数填写，两项之差乘100换算为基点。'),
    YIELD_CURVE_SHIFT: dict(version=1, title='期限利差变动', formula=r'[(d-c)-(b-a)]\times100',
        fields=['a', 'b', 'c', 'd'], unit='基点', condition='两日均按10年期减2年期计算期限利差，再比较两日差额。'),
    VALUATION_POSITION_METHOD: dict(version=1, title='滚动市盈率历史分位',
        formula=r'\frac{\sum_{i=1}^{n}\mathbf{1}(PE_i\le PE_0)}{n}\times100',
        fields=['current', 'history'], unit='%',
        condition='同一标的同口径PE(TTM)。过去12个完整月份每月取最后一个读数，正值齐备时计算低于或等于当期读数的比例。'),
    PE_EARNINGS_SCENARIO: dict(version=1, title='回到起点市盈率所对应的隐含盈利变化',
        formula=r'100(M_1/M_0-1)', fields=list(_PE_ATTRIBUTION_FIELDS), unit='%',
        condition='股价固定在期末收盘价。以区间起点市盈率为参照，比较期末隐含滚动盈利与参照值对应的滚动盈利。'),
    RETAIL_CONTRIBUTION: dict(version=1, title='社零分项同比贡献',
        formula=r'100(x-x/(1+g_x/100))/(T/(1+g_T/100))',
        fields=['total', 'total_yoy', 'subset', 'subset_yoy', 'residual'], unit='个百分点',
        condition='T为当月社零总额，x为同月分项金额，g为各自名义同比百分数。用公布的金额和同比反推基期。'
                  '汽车项为社零整体贡献减去非汽车项。结果受公告四舍五入影响。'),
    FEE_SUM: dict(version=1, title='管理费与托管费合计年费率', formula='a+b',
        fields=['a', 'b'], unit='%/年', condition='同一基金原件所载年管理费率与年托管费率之和。'),
    ETF_TURNOVER_AVERAGE: dict(version=1, title='共同样本日均成交额', formula=r'\sum A_i/n',
        fields=['amounts'], unit='元', condition='相同交易日范围内人民币成交额的算术平均。'),
    GROSS_SCALE_EFFECT: dict(version=1, title='收入规模对毛利的贡献', formula=r'(c-a)(1-b/a)',
        fields=['a', 'b', 'c', 'd'], unit='原值', condition='两期合并利润表同单位，按上年同期毛利率计算收入变动的贡献。'),
    GROSS_MARGIN_EFFECT: dict(version=1, title='毛利率变化对毛利的贡献', formula=r'bc/a-d',
        fields=['a', 'b', 'c', 'd'], unit='原值', condition='两期合并利润表同单位，以本期收入计量毛利率变动的贡献。'),
    PEER_NET_AFTER_GROSS: dict(version=1, title='毛利以下项目净变动',
        formula=r'\Delta\text{归母净利润}-\Delta\text{毛利}', fields=list('abcdef'), unit='元',
        condition='两期按同一合并口径。毛利以下项目净变动是归母净利润变动减毛利变动，合计包含费用、其他损益、税项与少数股东损益。'),
    'raw_range_v1': dict(version=1, title='已取得原值范围', formula=r'[a,b]',
        fields=['a','b'], unit='原值', condition='仅已取得数值的最小与最大值；收盘价范围不是高低价振幅或波动率。'),
    WEIGHT_TOTAL_METHOD: dict(version=1, title='成分权重合计', formula=r'\sum_{i=1}^{N}w_i',
        fields=['weights'], unit='%', condition='同一份指数权重原表同日列值，直接加总原表百分比。'),
    DAILY_NAV_PREMIUM_METHOD: dict(version=_DAILY_PREMIUM['implementation_version'], title='收盘折溢价率',
        formula=r'(\mathrm{price}/\mathrm{nav}-1)\times100', fields=['price', 'nav', 'valuation_date'],
        unit=_DAILY_PREMIUM['output_unit'], condition=_DAILY_PREMIUM['conditions']),
}
for method, label in PE_ATTRIBUTION_METHODS.items():
    rate, other = ('M', 'E') if method == PE_MULTIPLE_EFFECT else ('E', 'M')
    METHODS[method] = dict(version=1, title=label+'贡献', fields=list(_PE_ATTRIBUTION_FIELDS), unit='个百分点',
                          formula=rf'100r_{rate}(1+r_{other}/2)', condition=_PE_ATTRIBUTION_BASIS)

NAV_FIELDS = {'unit_nav', 'ths_unit_nvnf_fund'}
BALANCE = {'ths_account_receivable_stock','ths_inventory_stock','ths_accounts_payable_stock'}
FINANCIAL = BALANCE | {'ths_revenue_stock','ths_operating_cost_stock','ths_np_stock',
             'ths_np_atoopc_stock','ths_ncf_from_oa_stock'}


def registered_methods(task=None):
    from .market_calendar import stated_daily_nav_premiums

    version = task.get(DRAFT_WEIGHT_POLICY) if task is not None else None
    etf_scope = comparison_scope(task) if task is not None else None
    trading = (etf_scope or {}).get('trading') or {}
    optional = {WEIGHT_TOTAL_METHOD: type(version) is int and version in (1, DRAFT_WEIGHT_REFRESH_VERSION, DRAFT_WEIGHT_VERSION),
                DAILY_NAV_PREMIUM_METHOD: bool(trading.get('premium')) or task is not None and bool(stated_daily_nav_premiums(task)),
                FEE_SUM: bool((etf_scope or {}).get('year')), ETF_TURNOVER_AVERAGE: bool(trading.get('turnover')),
                VALUATION_POSITION_METHOD: bool(task is not None and valuation_history_scope(task))}
    from .monthly_statistics import POLICY as MONTHLY_POLICY, VERSION as MONTHLY_VERSION
    optional[RETAIL_CONTRIBUTION] = bool(task is not None and task.get(MONTHLY_POLICY) == MONTHLY_VERSION)
    optional.update(dict.fromkeys(PE_ATTRIBUTION_METHODS, bool(task is not None
        and valuation_attribution_enabled(task) and valuation_history_scope(task))))
    optional[PE_EARNINGS_SCENARIO] = bool(optional[PE_MULTIPLE_EFFECT]
        and task.get(VALUATION_HISTORY_POLICY) == VALUATION_HISTORY_VERSION)
    from .stock_quality import POLICY as STOCK_QUALITY_POLICY, QUALITY_BRIDGE_VERSION, study_scope

    stock_bridge = bool(task is not None and task.get(STOCK_QUALITY_POLICY) == QUALITY_BRIDGE_VERSION and study_scope(task))
    optional.update(dict.fromkeys(GROSS_BRIDGE_METHODS, stock_bridge))
    optional[PEER_NET_AFTER_GROSS] = bool(task and task.get(PEER_PROFIT_POLICY) in (1, PEER_PROFIT_DETAIL_VERSION))
    return [dict(id=name, **definition) for name,definition in METHODS.items()
            if optional.get(name, True)]


def _display_result(result):
    """Two decimals normally; retain a visible sign for small nonzero results."""
    if not result:
        return '0.00'
    with localcontext() as context:
        context.prec = max(40, len(result.as_tuple().digits) + max(0, result.adjusted()) + 8)
        digits = 2
        rounded = result.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if not rounded:
            if result.copy_abs() >= Decimal('0.000001'):
                digits = max(2, 2 - result.adjusted())
                rounded = result.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
            else:
                rounded = result.quantize(Decimal(1).scaleb(result.adjusted()-2), rounding=ROUND_HALF_UP)
                exponent = rounded.adjusted()
                mantissa = format(rounded.scaleb(-exponent), 'f').rstrip('0').rstrip('.')
                return mantissa + 'e' + str(exponent)
        shown = format(rounded, 'f')
        return shown.rstrip('0').rstrip('.') if digits > 2 else shown


def execute(method, values):
    definition=METHODS.get(method)
    if definition is None or not isinstance(values,dict) or set(values)!=set(definition['fields']):
        raise ContractError('草稿算术方法或输入未登记')
    if method == DAILY_NAV_PREMIUM_METHOD:
        params = dict(_DAILY_PREMIUM['parameters'], valuation_date=values['valuation_date'])
        result = calculate(method, {role: values[role] for role in ('price', 'nav')}, params)['value']
        exact = Decimal(str(result))
        display = _display_result(exact)
        if Decimal('0.0001') <= abs(exact) < Decimal('0.1'):
            display = format(exact.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP), 'f')
        return dict(value=result, exact=str(exact), display=display)
    try:
        if method in PE_ATTRIBUTION_METHODS or method == PE_EARNINGS_SCENARIO:
            if any(type(value) is bool for value in values.values()):
                raise ValueError
            price0, price1, pe0, pe1 = (Decimal(str(values[key])) for key in _PE_ATTRIBUTION_FIELDS)
            if any(not number.is_finite() or number <= 0 for number in (price0, price1, pe0, pe1)):
                raise ValueError
            with localcontext(Context(prec=40, rounding=ROUND_HALF_UP)):
                multiple = (pe1-pe0)/pe0
                if method == PE_EARNINGS_SCENARIO:
                    result = Decimal(100)*multiple
                else:
                    earnings = price1*pe0/(price0*pe1)-1
                    rate, other = (multiple, earnings) if method == PE_MULTIPLE_EFFECT else (earnings, multiple)
                    result = Decimal(100)*rate*(1+other/2)
                rounded = result.quantize(Decimal('0.1'))
            if not math.isfinite(float(result)):
                raise ValueError
            return dict(value=float(result), exact=str(result),
                        display=format(rounded, 'f').removesuffix('.0') if rounded else '0')
        if method == RETAIL_CONTRIBUTION:
            if type(values['residual']) is not bool:
                raise ValueError
            names = ('total', 'total_yoy', 'subset', 'subset_yoy')
            total, full_rate, subset, subset_rate = (Decimal(str(values[key])) for key in names)
            if (any(not value.is_finite() for value in (total, full_rate, subset, subset_rate))
                    or not 0 < subset <= total or min(full_rate, subset_rate) <= -100):
                raise ValueError
            with localcontext(Context(prec=40, rounding=ROUND_HALF_UP)):
                base = total/(1+full_rate/100)
                subset_base = subset/(1+subset_rate/100)
                if subset_base > base:
                    raise ValueError
                result = (subset-subset_base)*100/base
                if values['residual']:
                    result = full_rate-result
                rounded = result.quantize(Decimal('0.1'))
            if not math.isfinite(float(result)):
                raise ValueError
            return dict(value=float(result), exact=str(result),
                        display=format(rounded, 'f').removesuffix('.0') if rounded else '0')
        if method == VALUATION_POSITION_METHOD:
            history = values['history']
            if not isinstance(history, list) or len(history) != VALUATION_HISTORY_MONTHS:
                raise ValueError
            readings = [values['current'], *history]
            if any(type(value) is bool for value in readings):
                raise ValueError
            current, *sample = [Decimal(str(value)) for value in readings]
            if any(not value.is_finite() or value <= 0 for value in (current, *sample)):
                raise ValueError
            with localcontext(Context(prec=40, rounding=ROUND_HALF_UP)):
                result = Decimal(100) * sum(value <= current for value in sample) / len(sample)
                display = format(result.quantize(Decimal('0.1')), 'f').removesuffix('.0')
            return dict(value=float(result), exact=str(result), display=display)
        if method == ETF_TURNOVER_AVERAGE:
            raw = values['amounts']
            if not isinstance(raw, list) or not 1 <= len(raw) <= 200 or any(type(item) is bool for item in raw):
                raise ValueError
            with localcontext(Context(prec=40, rounding=ROUND_HALF_UP)):
                amounts = [Decimal(str(item)) for item in raw]
                if any(not item.is_finite() or not 0 <= item <= Decimal('1e14') for item in amounts):
                    raise ValueError
                result = sum(amounts, Decimal('0'))/len(amounts)
                return dict(value=float(result), exact=str(result), display=_display_result(result))
        if method == WEIGHT_TOTAL_METHOD:
            weights = values['weights']
            if (not isinstance(weights, list) or not 1 <= len(weights) <= 1000
                    or any(type(weight) not in (int, float) for weight in weights)):
                raise ValueError
            with localcontext(Context(prec=40, rounding=ROUND_HALF_UP)):
                decimals = [Decimal(str(weight)) for weight in weights]
                if any(not weight.is_finite() or not 0 <= weight <= 100 for weight in decimals):
                    raise ValueError
                total = sum(decimals, Decimal(0))
                return dict(value=float(total), exact=str(total), display=format(total.normalize(), 'f'))
        if any(isinstance(value,bool) or value is None for value in values.values()):
            raise ValueError
        a,b=(Decimal(str(values[name])) for name in ('a','b'))
        if not a.is_finite() or not b.is_finite():raise ValueError
        # An isolated context keeps the same task reproducible in App and Skill.
        with localcontext(Context(prec=40, rounding=ROUND_HALF_UP)):
            if method in GROSS_BRIDGE_METHODS:
                c, d = (Decimal(str(values[name])) for name in ('c', 'd'))
                if not (c.is_finite() and d.is_finite() and a > 0 and c > 0 and b >= 0 and d >= 0):
                    raise ValueError
                prior_cost_rate = b / a
                result = ((c - a) * (1 - prior_cost_rate) if method == GROSS_SCALE_EFFECT
                          else c * prior_cost_rate - d)
            elif method == PEER_NET_AFTER_GROSS:
                c, d, e, f = (Decimal(str(values[name])) for name in 'cdef')
                if not all(item.is_finite() for item in (c, d, e, f)) or min(a, d) <= 0 or min(b, e) < 0:
                    raise ValueError
                result = (f - c) - ((d - e) - (a - b))
            elif method=='raw_change_v1':
                if a<=0:raise ValueError
                # Subtract first so nearly equal inputs do not lose their change.
                result=(b-a)/a*100
            elif method in ('raw_difference_v1','raw_percentage_points_v1'):result=b-a
            elif method == YIELD_BASIS_POINTS:
                result = (b - a)*100
            elif method == YIELD_CURVE_SHIFT:
                c, d = (Decimal(str(values[name])) for name in ('c', 'd'))
                result = ((d - c) - (b - a))*100
            elif method==FEE_SUM:
                if not 0 <= a <= 10 or not 0 <= b <= 10:raise ValueError
                result=a+b
            elif method in ('raw_percent_v1','raw_multiple_v1'):
                if b<=0:raise ValueError
                result=a/b*(100 if method=='raw_percent_v1' else 1)
            elif method=='raw_margin_v1':
                if a<=0 or b<0:raise ValueError
                result=(a-b)/a*100
            else:
                if a>b:raise ValueError
                return dict(minimum=str(a),maximum=str(b),display=f'{a}至{b}')
            numeric = float(result)
            if not result.is_finite() or not math.isfinite(numeric) or result and numeric == 0:
                raise ValueError
            return dict(value=numeric,exact=str(result),display=_display_result(result))
    except (ValueError,DecimalException,OverflowError):
        raise ContractError('该组原值不适用此项算术，保留原值及其他结果') from None


def net_margin_components(cost_margins, net_margins):
    """Decompose margin changes when both ratios share the revenue cell in each period."""
    if any(cost['data_refs'][0] != net['data_refs'][1]
           for cost, net in zip(cost_margins, net_margins, strict=True)):
        return None
    cost_before, cost_after = (Decimal(execute(calc['method'], calc['inputs'])['exact']) for calc in cost_margins)
    net_before, net_after = (Decimal(execute(calc['method'], calc['inputs'])['exact']) for calc in net_margins)
    cost_change = cost_after - cost_before
    return net_before, net_after, cost_change, net_after - net_before - cost_change


def segment_revenue_breakdown(calculations, facts):
    """Aggregate a complete same-issuer comparison with exact segment amounts."""
    selected = [calc for calc in calculations if calc.get('comparison_kind') == SEGMENT_CHANGE
                and len(calc['data_refs']) == 2 and all(ref in facts for ref in calc['data_refs'])]
    if len(selected) < 2:
        return None
    pairs = [(calc, *(facts[ref] for ref in calc['data_refs'])) for calc in selected]
    cohorts = {(a['artifact_id'], a['code'], a['unit'], a['period'], b['period']) for _, a, b in pairs
               if a['artifact_id'] == b['artifact_id'] and a['field'] == b['field'] and a['unit'] == b['unit']}
    if (len(cohorts) != 1 or len({row['field'] for _, _, row in pairs}) != len(pairs)
            or not all(a['locator'].get('segment_count') == b['locator'].get('segment_count') == len(pairs)
                       and a['artifact_id'] == b['artifact_id'] and a['field'] == b['field'] and a['unit'] == b['unit']
                       for _, a, b in pairs)):
        return None
    parts = [dict(label=after['label'], before=Decimal(calc['inputs']['a']), after=Decimal(calc['inputs']['b']),
                  change=Decimal(calc['exact']), refs=calc['data_refs'], calculation=calc)
             for calc, _, after in pairs]
    source, code, unit, before_period, after_period = cohorts.pop()
    return dict(parts=parts, source=source, code=code, unit=unit, before_period=before_period,
                after_period=after_period, name=pairs[0][2]['object_label'],
                before=sum(item['before'] for item in parts), after=sum(item['after'] for item in parts),
                restated=any(row['locator'].get('restated') for _, row, _ in pairs))


def _day(value):
    try:
        text=str(value)
        return date.fromisoformat(text.replace('/','-') if len(text)==10 else text[:4]+'-'+text[4:6]+'-'+text[6:])
    except ValueError:return None


def _value_key(row):
    return None if row.get('value') is None else Decimal(str(row.get('raw_value',row['value'])))


def peer_financial_results(task, facts, calculations, scope):
    """Select complete comparable pairs and reject conflicts within a peer cell."""
    from .draft_sources import _financial_cell_address
    from .financial_sources import PEER_FINANCIAL_FIELDS

    comparison_fields = (*PEER_FINANCIAL_FIELDS, PEER_ADJUSTED_PROFIT)
    if scope.get('operating_drivers'):
        comparison_fields += (*PEER_OPERATING_FIELDS, *PEER_CASH_COMPONENTS.values())
    records = {row['id']: row for row in task.get('source_records', [])
               if row.get('task_id') == task['id'] and row.get('research_version') == task['version']}
    rows = {row['id']: row for row in facts}
    pairs = defaultdict(list)
    for calculation in calculations:
        refs = calculation['data_refs']
        if (calculation['method'] not in ('raw_change_v1', 'raw_difference_v1') or calculation.get('conditional_equal_scale')
                or calculation.get('code') not in scope['codes'] or len(refs) != 2 or any(ref not in rows for ref in refs)):
            continue
        before, after = (rows[ref] for ref in refs)
        field = analysis_field(after)
        if (field not in comparison_fields or
                field == 'ths_revenue_stock' and calculation['method'] != 'raw_change_v1'):
            continue
        addresses = [_financial_cell_address(task, row, records.get(row['artifact_id']), include_alternatives=True)
                     for row in (before, after)]
        code = calculation['code']
        if addresses != [(code, field, period, '1') for period in scope['periods']]:
            continue
        values = []
        for row in (before, after):
            unit = row['unit']
            factor = UNIT_FACTORS.get(unit.removeprefix('人民币'))
            if (factor is None or not (unit.startswith('人民币') or row.get('currency') == 'RMB')):
                break
            values.append(_value_key(row) * factor)
        if len(values) == 2:
            pairs[code, field].append((tuple(values), calculation, after, before['locator'].get('restated', False)))
    result = []
    for code in scope['codes']:
        values = {}
        for field in comparison_fields:
            options = pairs[code, field]
            if len({amounts for amounts, *_ in options}) != 1:
                continue
            amounts, calculation, row, restated = min(options, key=lambda item: item[2]['source'] != 'ifind')
            rate = Decimal(calculation['exact']) if calculation['method'] == 'raw_change_v1' else None
            context = next((dict(candidate['locator']['comparison_note'], source_ref=candidate['artifact_id'])
                            for _, _, candidate, _ in options if candidate['locator'].get('comparison_note')), None)
            values[field] = dict(amounts=amounts, rate=rate, calculation=calculation,
                                 name=row['object_label'] or code, restated=restated, comparison_note=context)
        if set(PEER_FINANCIAL_FIELDS) <= values.keys():
            adjusted = values.pop(PEER_ADJUSTED_PROFIT, None)
            result.append(dict(code=code, name=values[PEER_FINANCIAL_FIELDS[0]]['name'], fields=values,
                               adjusted_profit=adjusted))
    return result


def peer_gross_profit_bridge(revenue, cost, facts):
    """Decompose comparable gross profit without mixing total and operating revenue."""
    if not revenue or not cost or revenue.get('conditional_equal_scale') or cost.get('conditional_equal_scale'):
        return None
    reports = []
    for volume_ref, cost_ref in zip(revenue['data_refs'], cost['data_refs'], strict=True):
        sales = facts.get(volume_ref)
        expense = facts.get(cost_ref)
        if (not sales or not expense or sales['code'] != expense['code'] or sales['period'] != expense['period']
                or sales['field'] != 'ths_revenue_stock' or expense['field'] != PEER_OPERATING_FIELDS[0]
                or sales.get('currency') != 'RMB' or expense.get('currency') != 'RMB'
                or sales['source'] != expense['source'] or sales['source_operation'] != expense['source_operation']):
            return None
        if sales['source_operation'] != 'basic_data_service':
            sales_locator, cost_locator = (row['locator'] for row in (sales, expense))
            if (sales['artifact_id'] != expense['artifact_id'] or not sales_locator.get('statement_basis')
                    or sales_locator.get('statement_basis') != cost_locator.get('statement_basis')
                    or '营业总收入' in sales_locator.get('quote', '')):
                return None
        factors = [UNIT_FACTORS.get(row['unit'].removeprefix('人民币')) for row in (sales, expense)]
        if None in factors:
            return None
        sales_amount, expense_amount = (_value_key(row)*scale for row, scale in zip((sales, expense), factors, strict=True))
        if sales_amount <= 0 or expense_amount < 0:
            return None
        reports.append((sales_amount, sales_amount-expense_amount))
    if len(reports) != 2:
        return None
    (prior_revenue, prior_gross), (current_revenue, current_gross) = reports
    with localcontext(Context(prec=40, rounding=ROUND_HALF_UP)):
        prior_margin = prior_gross/prior_revenue
        margin = current_gross/current_revenue
        revenue_effect = (current_revenue-prior_revenue)*prior_margin
        gross_change = current_gross-prior_gross
        return dict(prior_margin=prior_margin*100, margin=margin*100, gross_change=gross_change,
                    revenue_effect=revenue_effect, margin_effect=gross_change-revenue_effect)


def _peer_income_statements(task, facts, records):
    """Pair the three group-statement fields before any cross-field arithmetic."""
    from .draft_sources import _financial_cell_address

    statements = defaultdict(lambda: defaultdict(list))
    for row in facts:
        field = analysis_field(row)
        locator = row.get('locator', {})
        if field not in PEER_PROFIT_FIELDS or row.get('currency') != 'RMB' or row.get('value') is None:
            continue
        record = records.get(row['artifact_id'])
        if record is None or record['source'] != row.get('source'):
            continue
        source = record['source'], record['operation']
        if source == ('ifind', 'basic_data_service'):
            statement = ('ifind', row.get('series_basis'))
        elif (locator.get('kind') == 'peer_filing_cell' and locator.get('statement_basis')
              and not (field == PEER_PROFIT_FIELDS[0] and '营业总收入' in locator.get('quote', ''))):
            statement = (row['artifact_id'], fingerprint(locator['statement_basis']))
        else:
            continue
        address = _financial_cell_address(task, row, record, include_alternatives=True)
        if address is None or address[-1] != '1' or row['unit'].removeprefix('人民币') not in UNIT_FACTORS:
            continue
        code, _, period, _ = address
        statements[code, statement, period][field].append(row)

    def statement_rows(bucket):
        if set(bucket) != set(PEER_PROFIT_FIELDS) or any(len(values) != 1 for values in bucket.values()):
            return ()
        rows = tuple(bucket[field][0] for field in PEER_PROFIT_FIELDS)
        return rows if len({row['artifact_id'] for row in rows}) == 1 else ()

    candidates = defaultdict(list)
    for (code, statement, period), items in statements.items():
        prior = str(int(period[:4])-1)+period[4:]
        before = statement_rows(statements.get((code, statement, prior), {}))
        after = statement_rows(items)
        if before and after:
            candidates[code, period].append(before+after)
    details = task.get(PEER_PROFIT_POLICY) == PEER_PROFIT_DETAIL_VERSION
    for pairs in candidates.values():
        if len(pairs) > 1:
            if not details or len({tuple(_peer_profit_amount(row) for row in pair) for pair in pairs}) != 1:
                continue
        primary = min(pairs, key=lambda pair: pair[0]['locator'].get('kind') == 'peer_filing_cell')
        originals = [pair for pair in pairs if pair[0]['locator'].get('kind') == 'peer_filing_cell']
        yield primary, originals[0] if details and len(originals) == 1 else None


def _peer_profit_amount(row):
    return _value_key(row)*UNIT_FACTORS[row['unit'].removeprefix('人民币')]


def _peer_profit_line_pairs(anchor, facts):
    prior, current = anchor[0], anchor[3]
    basis = current['locator']['statement_basis']
    periods = prior['period'], current['period']
    lines = defaultdict(list)
    for row in facts:
        locator = row.get('locator', {})
        if (row['field'] in PEER_PROFIT_COMPONENTS and row['code'] == current['code']
                and row.get('currency') == 'RMB' and row['unit'] == current['unit'] and row['value'] is not None
                and row['artifact_id'] == current['artifact_id'] and row['period'] in periods
                and locator.get('kind') == 'peer_filing_cell' and locator.get('statement') == '合并报表'
                and locator.get('statement_basis') == basis):
            lines[row['field']].append(row)
    for field in PEER_PROFIT_COMPONENTS:
        pair = sorted(lines[field], key=lambda row: row['period'])
        if tuple(row['period'] for row in pair) == periods:
            yield field, pair


def peer_profit_components(bridge, calculations):
    """Convert the disclosed line changes to signed effects on parent profit."""
    parts = {}
    for row in calculations:
        field = row.get('component')
        if (row.get('comparison_kind') != PEER_PROFIT_LINE_CHANGE or row['code'] != bridge['code']
                or row['period'] != bridge['period'] or field not in PEER_PROFIT_COMPONENTS):
            continue
        label, sign = PEER_PROFIT_COMPONENTS[field]
        change = Decimal(row['exact'])*UNIT_FACTORS[row['unit'].removeprefix('人民币')]
        parts[field] = dict(label=label, change=change, effect=sign*change, calculation=row)
    return parts


def peer_revenue_breakdown(peers, facts, calculations):
    """Use only disjoint lines from the same filed group revenue statement."""
    rows = {row['id']: row for row in facts}
    candidates = defaultdict(dict)
    for result in calculations:
        if result.get('comparison_kind') != PEER_REVENUE_CHANGE or not set(result['data_refs']) <= rows.keys():
            continue
        before, after = (rows[ref] for ref in result['data_refs'])
        field = after['field']
        factor = UNIT_FACTORS.get(after['unit'].removeprefix('人民币'))
        if (field not in PEER_REVENUE_COMPONENTS or not factor or before['unit'] != after['unit']
                or before.get('currency') != 'RMB' or after.get('currency') != 'RMB'):
            continue
        key = result['code'], after['artifact_id'], before['period'], after['period']
        value = dict(change=Decimal(result['exact'])*factor, calculation=result)
        options = candidates[key].setdefault(field, [])
        options.append(value)
    result = []
    for peer in peers:
        base = peer['fields']['ths_revenue_stock']
        prior, current = (rows[ref] for ref in base['calculation']['data_refs'])
        dates = (prior['period'], current['period'])
        matching = []
        note = base.get('comparison_note') or {}
        operating_only = note.get('kind') == 'revenue_excludes_investment'
        allowed = {'filing.peer_fee_income', 'filing.peer_net_interest'} if operating_only else PEER_REVENUE_COMPONENTS.keys()
        for (code, original, start, end), components in candidates.items():
            if code != peer['code'] or (start, end) != dates or components.keys() != allowed:
                continue
            if operating_only and original != note['source_ref']:
                continue
            if any(len(options) != 1 for options in components.values()):
                continue
            totals = [row for row in facts if row['artifact_id'] == original and row['code'] == code
                      and row['field'] == 'ths_revenue_stock' and row['period'] in dates
                      and row.get('locator', {}).get('kind') == 'peer_filing_cell' and row.get('currency') == 'RMB'
                      and row['unit'].removeprefix('人民币') in UNIT_FACTORS]
            if len(totals) != 2 or [row['period'] for row in sorted(totals, key=lambda row: row['period'])] != list(dates):
                continue
            amounts = tuple(_value_key(row)*UNIT_FACTORS[row['unit'].removeprefix('人民币')]
                            for row in sorted(totals, key=lambda row: row['period']))
            if amounts == base['amounts']:
                matching.append({field: options[0] for field, options in components.items()})
        if len(matching) == 1:
            values, = matching
            delta = base['amounts'][1] - base['amounts'][0]
            result.append(dict(code=peer['code'], name=peer['name'], parts=values,
                               revenue_delta=delta, residual=delta-sum(part['change'] for part in values.values()),
                               operating_only=operating_only))
    return result


def bank_profit_bridge(facts, calculations):
    """Reconcile income and signed impairment changes from one group statement."""
    revenue = 'ths_revenue_stock'
    fields = {revenue, *BANK_PROFIT_COMPONENTS}
    rows = {row['id']: row for row in facts}
    pairs = defaultdict(dict)
    for calc in calculations:
        if len(calc['data_refs']) != 2 or not set(calc['data_refs']) <= rows.keys():
            continue
        before, after = (rows[ref] for ref in calc['data_refs'])
        field, code = after['field'], after['code']
        scale = UNIT_FACTORS.get(after['unit'].removeprefix('人民币'))
        if (field not in fields or not scale or before['period'] >= after['period']
                or any(row['locator'].get('kind') != 'peer_filing_cell' or row.get('currency') != 'RMB'
                       or row['field'] != field or row['code'] != code or row['unit'] != after['unit']
                       or row['artifact_id'] != after['artifact_id'] for row in (before, after))):
            continue
        if field == revenue and calc['method'] != 'raw_change_v1':
            continue
        if field != revenue and calc.get('comparison_kind') != BANK_PROFIT_CHANGE:
            continue
        prior, current = (_value_key(row)*scale for row in (before, after))
        if field == BANK_CREDIT_IMPAIRMENT and max(prior, current) > 0:
            continue  # Do not infer the expense sign from a table that prints positive losses.
        key = code, after['artifact_id'], before['period'], after['period']
        pairs[key].setdefault(field, {})[calc['id']] = dict(change=current-prior, calculation=calc)
    by_code = defaultdict(list)
    for (code, source, first, last), values in pairs.items():
        if set(values) != fields or any(len(items) != 1 for items in values.values()):
            continue
        selected = {field: next(iter(items.values())) for field, items in values.items()}
        total = selected[revenue]['change']
        interest = selected[BANK_INTEREST_INCOME]['change']
        by_code[code].append(dict(net_interest=interest, noninterest=total-interest,
                                 credit_impact=selected[BANK_CREDIT_IMPAIRMENT]['change'],
                                 calculations={field: item['calculation'] for field, item in selected.items()},
                                 source_ref=source, before=first, after=last))
    return {code: options[0] for code, options in by_code.items() if len(options) == 1}


def input_binding(task, facts):
    return fingerprint(dict(task_id=task['id'],version=task['version'],input_hash=task.get('input_hash'),facts=facts))


def current_analysis(task, facts):
    """Return only reproducible cached results; retain other usable analysis.

    Input identity alone cannot validate a damaged saved result. Verification
    uses this module's existing arithmetic, with no fetching or model calls.
    The archive is unchanged and invalid entries do not block the report.
    """
    value=task.get('draft_analysis',{})
    try:
        if (not isinstance(value,dict) or value.get('task_id')!=task['id']
                or value.get('research_version')!=task['version']
                or value.get('binding')!=input_binding(task,facts) or value.get('version')!=VERSION
                or value.get('method_registry')!=fingerprint(registered_methods(task))):return {}
        rows=value.get('results')
        if not isinstance(rows,list):return {}
        recomputed=analyze(task,facts)
        expected={row['id']:row for row in recomputed['results']}
        counts=defaultdict(int)
        for row in rows:
            if isinstance(row,dict) and isinstance(row.get('id'),str):counts[row['id']]+=1
        valid=[]
        for row in rows:
            if not isinstance(row,dict) or not isinstance(row.get('id'),str) or counts[row['id']]!=1:continue
            original=expected.get(row['id'])
            if original is None:continue
            try:
                if fingerprint({key:row.get(key) for key in original})==fingerprint(original):valid.append(row)
            except (ContractError,InvalidOperation,KeyError,TypeError,ValueError,OverflowError):continue
        checked=dict(value,results=valid)
        for key in ('quote_interval_gaps', 'close_return_gaps', 'disconnected_closes',
                    'nav_interval_gaps', 'nav_point_readings', 'daily_nav_premium_gaps'):
            checked.pop(key, None)
            if key in recomputed:
                checked[key] = recomputed[key]
        return checked
    except (ContractError,InvalidOperation,KeyError,TypeError,ValueError,OverflowError):return {}


def writing_calculations(task, facts, *, checked=None):
    fields = ('id','label','period','display','unit','data_refs','conditional_equal_scale','condition',
              'comparison_kind','window_start','window_end','session_basis','comparison_source_ref','sample_count','component')
    return [{key:item[key] for key in fields if key in item}
            for item in (current_analysis(task,facts) if checked is None else checked).get('results',[])
            if item.get('comparison_kind') != VALUATION_SCENARIO]


def interpretation_context(task, facts):
    """Task-specific writing boundaries, never a second review or a gate.

    Rules are sent once for their actual objects. No business number is
    repeated, and no missing field becomes a mandatory acquisition request.
    """
    from .draft_sources import source_issues
    task_id,version=task.get('id'),task.get('version')
    excluded={row['artifact_id'] for row in source_issues(task) if not row.get('partial')}
    records={row['id']:row for row in task.get('source_records',[])
        if task_id and version and isinstance(row,dict) and row.get('task_id')==task_id
        and row.get('research_version')==version and row['id'] not in excluded}
    grouped=defaultdict(list)
    for row in facts:
        if row.get('artifact_id') in records and row.get('code') and row.get('value') is not None:
            grouped[row['code']].append(row)
    definitions={
        'financial_period_scope':'财务数据按该字段请求的D参数标记报告期，响应time和取数日期不能代替报告期。'
            '累计收入、利润和现金流按年初至期末表述，不能写成同一截止日的单季数值；余额按实际时点。'
            '报告期未明确时保留原值，不按同日拼接计算同比或跨指标比率。',
        # Income-statement levels follow the SEC financial-statement guide:
        # https://www.sec.gov/about/reports-publications/investorpubsbegfinstmtguide
        'profit_attribution':'收入、毛利与净利润增长率的基数不同，降幅大小不等于利润变动贡献。可讨论经营变化和竞争解释，但不能仅凭净利润降幅大于收入认定费用、税项或其他损益主导；主要拖累须有同口径利润变动分解，未知项仍可作为条件性假设。',
        'gross_margin_scope':'收入成本差占比是收入比率，不是单位产品毛利。未取得同口径销量、单位收入或单位成本时，不推断单位毛利变化；也不因毛利率百分点变化看似较小，就认定毛利不是利润下降的主要拖累。',
        'cash_attribution':'经营现金流或现金流/净利润的变化不能单独证明回款、主营经营或盈利质量改善；归因需有同期间、同范围的实际分解。',
        'parent_profit_scope':'经营现金流与归母净利润可分别描述同比，但不能用归母净利润代替同口径报表净利润构造现金含量或覆盖比，也不以两者降幅确定盈利现金含量变化。',
        'balance_flow_scope':'应收、存货和应付是时点余额；余额变化不是当期现金收付，也不是现金流同比变化的直接贡献。',
        'valuation_scope':'可以围绕盈利兑现、增长持续性与风险补偿讨论条件性估值逻辑，不能仅凭单个读数认定高估或低估。年度利润不能自动代替该观察时点的TTM分母；未核对适用盈利口径时，不仅凭年度利润下降和短窗价格上涨认定涨幅来源。口径核对放底稿，不能替代投资含义。估值单位沿用本任务unit与unit_basis，未知时称读数，不按指标名称自行添加倍或百分比。',
        'close_scope':'收盘观测只描述实际取得的收盘路径。区间起终点变化不能写成日涨跌幅，'
            '上一交易日比较使用已登记相邻两日计算；缺少任一收盘不往前补。供应商披露涨跌幅保留披露口径。'
            '收盘路径不代替日内高低价、总回报或事件因果。',
        'nav_scope':'单位净值读数变化未作分红再投资、份额折算和投资者现金流调整，不等于持有人总回报；净值绝对高低也不能直接比较基金优劣。',
        'macro_contribution':'宏观读数及百分点变化不等于贡献分解；缺少对应权重和分解时，不认定主要拉动或拖累，也不把共同变动写成因果。',
        'sovereign_yield_scope':'中债国债到期收益率是该期限的利率基准，不是债券价格或持有回报。'
            '用实际观测日和同期限读数描述变化。宏观序列标注统计所属期，若要判断市场对发布的反应，须另核对发布时间和当时预期。',
    }
    selected=defaultdict(list)
    for code,rows in grouped.items():
        fields={analysis_field(row) for row in rows}
        if any(row.get('period_basis') for row in rows):selected['financial_period_scope'].append(code)
        if 'ths_revenue_stock' in fields and fields & {'ths_np_stock','ths_np_atoopc_stock'}:
            selected['profit_attribution'].append(code)
        if {'ths_revenue_stock','ths_operating_cost_stock'}<=fields:
            selected['gross_margin_scope'].append(code)
        if 'ths_ncf_from_oa_stock' in fields:
            selected['cash_attribution'].append(code)
            if 'ths_np_atoopc_stock' in fields:
                selected['parent_profit_scope'].append(code)
        if fields & BALANCE:selected['balance_flow_scope'].append(code)
        if fields & {'ths_pe_ttm_stock','ths_pb_mrq_stock','ths_pb_latest_stock','ths_pe_index','ths_pb_index','ths_ps_index'}:
            selected['valuation_scope'].append(code)
        if 'close' in fields:selected['close_scope'].append(code)
        if fields & NAV_FIELDS:selected['nav_scope'].append(code)
        if any(analysis_field(row)=='value' and records[row['artifact_id']].get('operation')=='edb_service' for row in rows):
            selected['macro_contribution'].append(code)
            if any(row.get('label', '').startswith('中债国债到期收益率:') and row.get('unit') == '%' for row in rows):
                selected['sovereign_yield_scope'].append(code)
    return dict(version=1,binding=dict(task_id=task_id,research_version=version,
        input_hash=task.get('input_hash'),facts_hash=fingerprint(facts)),
        rules=[dict(id=key,objects=sorted(selected[key]),text=definitions[key])
               for key in definitions if selected[key]],financial_approval=False,delivery_blocking=False)


def formula_mathml(method, *, labels=None):
    bodies={
        'raw_change_v1':'<mrow><mo>(</mo><mfrac><mi>b</mi><mi>a</mi></mfrac><mo>−</mo><mn>1</mn><mo>)</mo><mo>×</mo><mn>100</mn></mrow>',
        'raw_difference_v1':'<mrow><mi>b</mi><mo>−</mo><mi>a</mi></mrow>',
        'raw_percentage_points_v1':'<mrow><mi>b</mi><mo>−</mo><mi>a</mi></mrow>',
        YIELD_BASIS_POINTS:'<mrow><mo>(</mo><mi>b</mi><mo>−</mo><mi>a</mi><mo>)</mo><mo>×</mo><mn>100</mn></mrow>',
        YIELD_CURVE_SHIFT: ('<mrow><mo>[</mo><mo>(</mo><mi>d</mi><mo>−</mo><mi>c</mi><mo>)</mo><mo>−</mo>'
                            '<mo>(</mo><mi>b</mi><mo>−</mo><mi>a</mi><mo>)</mo><mo>]</mo><mo>×</mo><mn>100</mn></mrow>'),
        VALUATION_POSITION_METHOD: ('<mrow><mfrac><mtext>不高于当前读数的样本数</mtext><mi>n</mi></mfrac>'
                                    '<mo>×</mo><mn>100</mn></mrow>'),
        RETAIL_CONTRIBUTION: ('<mrow><mfrac><mtext>分项同比增量</mtext><mtext>社零同期基数</mtext></mfrac>'
                              '<mo>×</mo><mn>100</mn></mrow>'),
        FEE_SUM:'<mrow><mi>a</mi><mo>+</mo><mi>b</mi></mrow>',
        ETF_TURNOVER_AVERAGE:'<mfrac><mrow><mo>∑</mo><msub><mi>A</mi><mi>i</mi></msub></mrow><mi>n</mi></mfrac>',
        'raw_percent_v1':'<mrow><mfrac><mi>a</mi><mi>b</mi></mfrac><mo>×</mo><mn>100</mn></mrow>',
        'raw_multiple_v1':'<mfrac><mi>a</mi><mi>b</mi></mfrac>',
        'raw_margin_v1':'<mrow><mfrac><mrow><mi>a</mi><mo>−</mo><mi>b</mi></mrow><mi>a</mi></mfrac><mo>×</mo><mn>100</mn></mrow>',
        GROSS_SCALE_EFFECT: ('<mrow><mo>(</mo><mi>c</mi><mo>−</mo><mi>a</mi><mo>)</mo><mo>×</mo>'
                             '<mo>(</mo><mn>1</mn><mo>−</mo><mfrac><mi>b</mi><mi>a</mi></mfrac><mo>)</mo></mrow>'),
        GROSS_MARGIN_EFFECT: ('<mrow><mfrac><mrow><mi>b</mi><mo>×</mo><mi>c</mi></mrow><mi>a</mi></mfrac>'
                              '<mo>−</mo><mi>d</mi></mrow>'),
        PEER_NET_AFTER_GROSS: '<mrow><mo>Δ</mo><mtext>归母净利润</mtext><mo>−</mo><mo>Δ</mo><mtext>毛利</mtext></mrow>',
        'raw_range_v1':'<mrow><mo>[</mo><mi>a</mi><mo>,</mo><mi>b</mi><mo>]</mo></mrow>',
        PE_EARNINGS_SCENARIO: ('<mrow><mo>(</mo><mfrac><mtext>期末市盈率</mtext><mtext>起点市盈率</mtext></mfrac>'
                               '<mo>−</mo><mn>1</mn><mo>)</mo><mo>×</mo><mn>100</mn></mrow>'),
        WEIGHT_TOTAL_METHOD: '<mrow><munderover><mo>∑</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow><mi>N</mi></munderover><msub><mi>w</mi><mi>i</mi></msub></mrow>',
        DAILY_NAV_PREMIUM_METHOD: '<mrow><mo>(</mo><mfrac><mtext>收盘价</mtext><mtext>单位净值</mtext></mfrac><mo>−</mo><mn>1</mn><mo>)</mo><mo>×</mo><mn>100</mn></mrow>',
    }
    if method in PE_ATTRIBUTION_METHODS:
        rate, other = ('M', 'E') if method == PE_MULTIPLE_EFFECT else ('E', 'M')
        bodies[method] = (f'<mrow><mn>100</mn><mo>×</mo><msub><mi>r</mi><mi>{rate}</mi></msub><mo>(</mo>'
                          f'<mn>1</mn><mo>+</mo><mfrac><msub><mi>r</mi><mi>{other}</mi></msub><mn>2</mn></mfrac>'
                          '<mo>)</mo></mrow>')
    body = bodies.get(method)
    if not body:
        return ''
    if labels is None and method in GROSS_BRIDGE_METHODS:
        labels = dict(zip('abcd', GROSS_BRIDGE_INPUTS, strict=True))
    if labels:
        from html import escape
        for symbol, label in labels.items():
            body = body.replace(f'<mi>{symbol}</mi>', '<mtext>'+escape(label)+'</mtext>')
    return '<math xmlns="http://www.w3.org/1998/Math/MathML">'+body+'</math>'


def _closing_request_windows(task, records):
    """Use only the registered, non-filled daily quote requests."""
    from .evidence import validate_market_request

    windows = {}
    for record in records:
        source, operation = record.get('source'), record.get('operation')
        if (source, operation) not in (('ifind', 'cmd_history_quotation'),
                                       ('tinyshare', 'daily'), ('tinyshare', 'fund_daily')):
            continue
        params = record.get('parameters')
        try:
            start, end = validate_market_request(task, source, operation, params, require_identity=False)
        except (ContractError, KeyError, TypeError, ValueError):
            continue
        fields = params['indicators'] if source == 'ifind' else params['fields']
        if 'close' in fields.split(','):
            codes = params['codes'] if source == 'ifind' else params['ts_code']
            windows[record['id']] = (_day(start), _day(end), set(codes.split(',')))
    return windows


def _quote_request_intervals(task, records, windows, stated):
    """Use request bounds only for securities without an explicit interval."""
    from .draft_sources import _source_issue
    from .market_series import latest_observation_selected
    from .subject_scope import subject_key

    scopes = defaultdict(set)
    for ident, (start, end, codes) in windows.items():
        record = records[ident]
        if _source_issue(task, record):
            continue
        if record.get('observation_requests') and latest_observation_selected(task, record):
            continue
        if start != end:
            questions = tuple(sorted(set(record.get('question_ids') or ())))
            for code in codes:
                if subject_key(code).upper() not in stated:
                    scopes[record['source'], record['operation'], code].add((start,end,questions))
    return scopes


def _interval_close_rows(dated, code, start, end, questions, records, windows, *, allow_unlinked=False):
    """Use actual cells from their own request bounds, including compatible supplements."""
    selected, conflicts = {}, set()
    required = set(questions)
    for day, group in sorted(dated.items()):
        if not start <= day <= end:
            continue
        matching = []
        for row in group:
            ident = row['artifact_id']
            window = windows.get(ident)
            linked = records[ident].get('question_ids') or ()
            if (window is not None and code in window[2] and window[0] <= day <= window[1]
                    and (required.issubset(linked) or (allow_unlinked and not linked))):
                matching.append(row)
        if not matching:
            continue
        candidates = [row for row in matching if row['value'] is not None] or matching
        if len({_value_key(row) for row in candidates}) > 1:
            conflicts.add(day)
        else:
            selected[day] = candidates[0]
    return selected, conflicts


def _previous_session_pair(code, rows, windows, calendars, *, target=None):
    """Choose an exact prior session, never the previous nonmissing price."""
    from .market_calendar import previous_session_dates

    eligible = {}
    quote_windows = []
    for day, group in rows.items():
        same_day = []
        for row in group:
            window = windows.get(row['artifact_id'])
            if window is None or code not in window[2] or not window[0] <= day <= window[1]:
                continue
            quote_windows.append(window)
            same_day.append(row)
        if same_day and len({_value_key(row) for row in same_day}) == 1:
            eligible[day] = same_day[0]
    if len(eligible) < 2:
        return None
    start, end = min(window[0] for window in quote_windows), max(window[1] for window in quote_windows)
    dates = previous_session_dates(code, start, target or end, calendars, observed_end=target is not None)
    if dates is None:
        return None
    (base, end), proof = dates
    first, last = eligible.get(_day(base)), eligible.get(_day(end))
    if first is None or last is None or first['value'] is None or last['value'] is None:
        return None
    if first['value'] <= 0 or last['value'] <= 0:
        return None
    return first, last, base, end, proof


def _daily_nav_premium_pair(task, code, day, prices, navs, records):
    """Reuse the registered same-share and same-valuation-day evidence gates."""
    from .draft_sources import validate_stated_source
    from .etf_sources import share_context_choices
    from .evidence import api_calculation_context_contract

    prices = [row for row in prices if (records[row['artifact_id']]['source'],
              records[row['artifact_id']]['operation']) == ('ifind', 'cmd_history_quotation')]
    navs = [row for row in navs if (records[row['artifact_id']]['source'],
            records[row['artifact_id']]['operation']) == ('official', 'etf_pcf')]
    if any(len({_value_key(row) for row in rows}) != 1 for rows in (prices, navs)):
        return None
    price, nav = prices[0], navs[0]
    quote, official = (records[row['artifact_id']] for row in (price, nav))
    try:
        for record in (quote, official):
            validate_stated_source(task, record['source'])
        choices = share_context_choices(task, quote, price['locator'], code, allowed_ids={official['id']})
        if len(choices) != 1:
            return None
        nav_context = api_calculation_context_contract(official)['fixed_fields']
        quote_context = api_calculation_context_contract(quote)['fixed_fields']
        if quote_context.get('price_basis') != 'unadjusted' or quote['parameters']['functionpara'].get('Fill') != 'Omit':
            return None
        bindings = dict(price=dict(price, object=code, artifact=quote, record_locator=price['locator'],
            calculation_context=dict(fund_id=code, share_class=choices[0]['value'], currency=quote_context['currency'],
                                     price_kind='close', observation_date=day)),
            nav=dict(nav, object=code, artifact=official, record_locator=nav['locator'],
                     calculation_context=dict(nav_context, fund_id=code)))
        params = dict(_DAILY_PREMIUM['parameters'], valuation_date=day)
        validate_calculation_context(DAILY_NAV_PREMIUM_METHOD, bindings, params,
            context_contract=calculation_context_contract(task, DAILY_NAV_PREMIUM_METHOD))
        return price, nav
    except (ContractError, KeyError, TypeError):
        return None


def stock_profit_attribution(pairs):
    """Reconcile the change in parent profit against the same filing's income statements."""
    from .stock_quality import ASSET_LOSS, COST, INCOME_TAX, MINORITY_PROFIT, PRETAX, PROFIT, REVENUE

    fields = (REVENUE, COST, ASSET_LOSS, PRETAX, INCOME_TAX, MINORITY_PROFIT, PROFIT)
    if not set(fields) <= pairs.keys():
        return None
    rows = [row for field in fields for row in pairs[field]]
    if len({(row['artifact_id'], row['code'], row['unit']) for row in rows}) != 1:
        return None

    amounts = {field: tuple(Decimal(row['raw_value']) for row in pairs[field]) for field in fields}
    prior, current = amounts[PROFIT]
    profit_periods = zip(amounts[PRETAX], amounts[INCOME_TAX], amounts[MINORITY_PROFIT], amounts[PROFIT], strict=True)
    if any(pretax - tax - minority != parent for pretax, tax, minority, parent in profit_periods):
        return None

    def difference(field):
        before, after = amounts[field]
        return after - before

    gross = difference(REVENUE) - difference(COST)
    impairment = difference(ASSET_LOSS)
    effects = {'毛利变化': gross, '资产减值': impairment, '其他税前项目': difference(PRETAX) - gross - impairment,
               '所得税费用': -difference(INCOME_TAX), '少数股东损益归属': -difference(MINORITY_PROFIT)}
    return dict(unit=rows[0]['unit'], prior=prior, current=current,
                effects=effects, data_refs=[row['id'] for row in rows])


def analyze(task, facts, *, calendars=None, stock_pairs=None, valuation_scope=None, treasury_pairs=None):
    # Cache identity covers the full observed snapshot, including unrelated rows.
    # Selection affects arithmetic inputs, never the identity of its source set.
    binding=input_binding(task,facts)
    owned={row['id']:row for row in task.get('source_records',[]) if row.get('task_id')==task['id']
           and row.get('research_version')==task['version']}
    quote_windows = _closing_request_windows(task, owned.values())
    from .draft_sources import (financial_refresh_enabled, latest_market_refresh_enabled, local_sources_only,
                                validate_stated_source)
    from .economic_sources import _economic_query_links, _period_number, economic_comparison_windows
    from .market_calendar import (QUOTE_ENDPOINT_POLICY, close_return_previous_session, quote_interval_enabled,
                                  retained_calendars, stated_close_intervals, stated_close_pairs,
                                  stated_close_returns, stated_daily_nav_premiums, verified_session_window)
    from .market_series import nav_request_window, nav_source_scope_enabled, stated_nav_intervals
    from .monthly_statistics import dated_pmi_scope, pmi_release_context
    from .subject_scope import subject_key
    refresh_latest = latest_market_refresh_enabled(task)
    refresh_financial = financial_refresh_enabled(task)
    reuse_retained = task.get('run_mode') == 'local_draft' or local_sources_only(task)
    stated = stated_close_intervals(task)
    nav_intervals = stated_nav_intervals(task)
    if calendars is None:
        calendars = retained_calendars(task) if quote_windows or stated else []
    return_scopes = stated_close_returns(task)
    return_pairs = {(code, day): close_return_previous_session(code, day, calendars)
                    for code, day, _ in return_scopes}
    endpoint_policy = task.get(QUOTE_ENDPOINT_POLICY)
    interval_policy = quote_interval_enabled(task)
    interval_scopes = _quote_request_intervals(task, owned, quote_windows, stated) if interval_policy else {}
    economic={row['id'] for row in owned.values() if row.get('source')=='ifind' and row.get('operation')=='edb_service'}
    economic_windows = economic_comparison_windows(task)
    facts=[row for row in facts if row.get('artifact_id') in owned and row.get('code')]
    results=[];notes=[];series=defaultdict(list);periods=defaultdict(list);daily=[]
    quote_interval_gaps = {}
    disconnected_closes = set()
    nav_endpoints = defaultdict(set)
    comparable_nav = set()
    nav_windows = {ident: nav_request_window(task, row) for ident, row in owned.items()} if nav_intervals else {}
    interval_sessions = {}
    analyzed_stated = set()
    event_return_pairs = set()
    pmi = dated_pmi_scope(task)
    release_day = (_day(pmi['release_day']) if pmi and pmi_release_context(task, pmi)
                   and '首日' in task['question'] and '次日' in task['question'] else None)

    def add(method,label,rows,values,period,conditional=False):
        try:output=execute(method,values)
        except ContractError:
            notes.append(dict(label=label,reason='分母、缺失值或数值范围不适用，未影响其他分析。'));return
        definition=METHODS[method]
        results.append(dict(id='A'+str(len(results)+1),method=method,method_version=definition['version'],
            label=label,code=rows[0]['code'],period=period,data_refs=list(dict.fromkeys(row['id'] for row in rows)),
            inputs=values,unit=rows[0]['unit'] if definition['unit']=='原值' else definition['unit'],
            formula=definition['formula'],condition=definition['condition'],conditional_equal_scale=conditional,
            status='原值算术',financial_approval=False,**output))
        if analysis_field(rows[0]) in NAV_FIELDS:
            results[-1]['condition'] += '仅单位净值读数；未作分红再投资、份额折算和投资者现金流调整，不等于持有人总回报。'
        if analysis_field(rows[0]) in BALANCE:
            results[-1]['period_kind'] = 'point_in_time'
            results[-1]['condition'] += '余额时点变化不能视为当期经营现金流贡献。'
        return results[-1]

    from .stock_quality import (ADJUSTED, ASSET_LOSS, CASH, CASH_IN, CASH_OUT, CELL_KIND as STOCK_QUALITY_CELL,
                                COST, INCOME_TAX, LABELS, MINORITY_PROFIT, POLICY as STOCK_QUALITY_POLICY,
                                PRETAX, PROFIT, PURCHASE_PAYMENTS, QUALITY_BRIDGE_VERSION, RECEIPTS, REVENUE,
                                STRATEGY_POLICY, selected_pairs)

    quality = selected_pairs(task, facts) if stock_pairs is None else stock_pairs
    difference_fields = (REVENUE, COST, CASH_IN, CASH_OUT, CASH)
    if task.get(STRATEGY_POLICY) == 1:
        difference_fields += (RECEIPTS, PURCHASE_PAYMENTS, ASSET_LOSS, PRETAX, INCOME_TAX, MINORITY_PROFIT, PROFIT)
    for field in difference_fields:
        if field in quality:
            first, second = quality[field]
            calculation = add('raw_difference_v1', LABELS[field]+'同比增减', [first, second],
                              dict(a=first['raw_value'], b=second['raw_value']), second['period'])
            if calculation:
                cash_field = field in (CASH_IN, CASH_OUT, CASH, RECEIPTS, PURCHASE_PAYMENTS)
                calculation['comparison_kind'] = 'stock_cash_change' if cash_field else 'stock_income_change'
                calculation['condition'] = ('合并现金流量表，两期累计金额相减。' if cash_field
                                            else '合并利润口径，两期累计金额相减。')
    for position in (0, 1):
        for numerator, denominator, kind, label, method, condition in (
            (CASH, REVENUE, 'stock_cash_revenue', '经营现金流净额占营收', 'raw_percent_v1',
             '同份半年报同一期间、同币种同量级的经营现金流净额除以营业收入。'),
            (REVENUE, COST, 'stock_gross_margin', '毛利率', 'raw_margin_v1',
             '以合并营业收入和营业成本计算毛利率。营业收入与半年报主要财务指标对账。'),
            (ADJUSTED, REVENUE, 'stock_adjusted_margin', '扣非净利率', 'raw_percent_v1',
             '半年报本期或上年同期扣非归母净利润除以营业收入，同一人民币量级。'),
            (ADJUSTED, PROFIT, 'stock_reported_adjusted_gap', '归母净利润与扣非归母净利润差额', 'raw_difference_v1',
             '同份半年报同一期间归母净利润减扣非归母净利润；差额是扣非口径的净影响。'),
        ):
            if numerator not in quality or denominator not in quality:
                continue
            first, second = quality[numerator][position], quality[denominator][position]
            if first['unit'] != second['unit']:
                continue
            calculation = add(method, label, [first, second], dict(a=first['raw_value'], b=second['raw_value']),
                              first['period'])
            if calculation:
                calculation.update(comparison_kind=kind, condition=condition)

    from .monthly_statistics import selected_series as monthly_statistics_series

    monthly = monthly_statistics_series(facts)
    for group in monthly.values():
        if len(group) < 2:
            continue
        before, after = sorted(group)[0], sorted(group)[-1]
        a, b = group[before], group[after]
        if a['unit'] != '%':
            continue  # Amounts and published contribution points are not growth rates.
        result = add('raw_percentage_points_v1', b['label'] + '变化', [a, b],
                     dict(a=a['raw_value'], b=b['raw_value']), before + '至' + after)
        if result:
            result['condition'] = '国家统计局同指标当月同比读数的百分点差。'

    for period, total in sorted(monthly.get('retail_amount', {}).items()):
        headline = monthly.get('retail', {}).get(period)
        goods = monthly.get('goods_amount', {}).get(period)
        catering = monthly.get('catering_amount', {}).get(period)
        non_auto = monthly.get('ex_auto_amount', {}).get(period)
        if not headline:
            continue
        # Three figures rounded to whole 亿元 can differ by at most 1.5亿元.
        compatible = not (goods and catering) or abs(goods['value'] + catering['value'] - total['value']) <= 1.5
        if goods and non_auto and catering and goods['value'] + 1.5 < total['value'] - non_auto['value']:
            compatible = False
        for field, label, residual in (('goods', '商品零售', False), ('catering', '餐饮', False),
                                       ('ex_auto', '剔除汽车', False), ('ex_auto', '汽车', True)):
            if field in ('goods', 'catering') and not compatible:
                continue
            inputs = [total, headline, monthly.get(field+'_amount', {}).get(period), monthly.get(field, {}).get(period)]
            if any(item is None for item in inputs) or len({item['artifact_id'] for item in inputs}) != 1:
                continue
            values = dict(zip(('total', 'total_yoy', 'subset', 'subset_yoy'), (item['raw_value'] for item in inputs), strict=True))
            result = add(RETAIL_CONTRIBUTION, label+'对社零同比的贡献', inputs,
                         dict(values, residual=residual), period)
            if result:
                result.update(comparison_kind='nbs_retail_contribution', component=label)

    from .industry_chain import selected_cells as industry_chain_cells

    chain_cells = industry_chain_cells(facts)
    vehicle_rows = [(chain_cells.get((year, 'vehicle_sales')), chain_cells.get((year, 'vehicle_exports')))
                    for year in sorted({year for year, _ in chain_cells})]
    for sales, exports in vehicle_rows:
        if not sales or not exports or sales['artifact_id'] != exports['artifact_id']:
            continue
        if sales['unit'] != exports['unit'] or not 0 <= exports['value'] <= sales['value']:
            continue
        raw = dict(a=exports['raw_value'], b=sales['raw_value'])
        for method, label in (('raw_difference_v1', '新能源汽车境内销量估算'),
                              ('raw_percent_v1', '新能源汽车出口占销量比重')):
            calculation = add(method, label, [exports, sales], raw, sales['period'])
            if calculation:
                calculation['condition'] = '同一份中汽协整车统计公报；境内销量为全国销量减出口量的近似值。'

    if etf_scope := comparison_scope(task):
        interim_observations = product_observations(facts, etf_scope) if etf_scope.get('year') else {}
        for rows in interim_observations.values():
            if {MANAGEMENT, CUSTODY} <= rows.keys():
                a, b = rows[MANAGEMENT], rows[CUSTODY]
                add(FEE_SUM, '管理费与托管费合计年费率', [a, b],
                    dict(a=a['raw_value'], b=b['raw_value']), etf_scope['period'])
            if {BENCHMARK, PERFORMANCE} <= rows.keys():
                a, b = rows[BENCHMARK], rows[PERFORMANCE]
                gap = add('raw_percentage_points_v1', '同期净值收益相对基准差', [a, b],
                          dict(a=a['raw_value'], b=b['raw_value']), etf_scope['period'])
                if gap:
                    gap.update(comparison_kind=TRACKING_GAP,
                        condition='同一份中期报告披露的份额净值增长率减去同期业绩基准收益率。')
        if terms := etf_scope.get('trading'):
            book = defaultdict(dict)
            means = {}
            for row in facts:
                if row.get('locator', {}).get('kind') == ETF_TRADING_CELL:
                    book[row['code']][row['field'], row['period']] = row
            for code in etf_scope['codes']:
                cells = book[code]
                amounts = [row for (field, _), row in sorted(cells.items()) if field == ETF_TURNOVER]
                if amounts:
                    days = [row['period'] for row in amounts]
                    window = days[0]+'至'+days[-1]
                    mean = add(ETF_TURNOVER_AVERAGE, '样本日均成交额', amounts,
                               dict(amounts=[row['raw_value'] for row in amounts]), window)
                    if mean:
                        mean.update(comparison_kind='etf_turnover_mean', dates=days)
                        means[code] = mean
                        if terms['ticket'] and mean['value'] > 0:
                            load = add('raw_percent_v1', '委托金额占样本日均成交额', amounts,
                                       dict(a=terms['ticket'], b=mean['exact']), window)
                            if load:
                                load.update(comparison_kind='etf_ticket_participation', dates=days,
                                    input_origin='用户提问金额', condition='与历史全天市场成交额的比例，仅度量委托量级。')
                for (field, day), price in cells.items():
                    if field == ETF_CLOSE and (nav := cells.get((ETF_NAV, day))) is not None:
                        premium = add(DAILY_NAV_PREMIUM_METHOD, '同日收盘折溢价率', [price, nav],
                                      dict(price=price['value'], nav=nav['value'], valuation_date=day), day)
                        if premium:
                            premium.update(comparison_kind='etf_exchange_close_premium',
                                condition='同一基金同一估值日的未复权收盘价与官方单位净值，盘中成本须随报价核对。')
            if len(means) == len(etf_scope['codes']) == 2:
                leading, other = sorted(means, key=lambda code: Decimal(means[code]['exact']), reverse=True)
                days = means[leading]['dates']
                if (len(days) >= 5 and days == means[other]['dates']
                        and Decimal(means[leading]['exact']) > Decimal(means[other]['exact'])
                        and all(Decimal(book[other][ETF_TURNOVER, day]['raw_value']) > 0 for day in days)):
                    for day in days:
                        a, b = book[leading][ETF_TURNOVER, day], book[other][ETF_TURNOVER, day]
                        ratio = add('raw_multiple_v1', '同日成交额比值', [a, b],
                                    dict(a=a['raw_value'], b=b['raw_value']), day)
                        if ratio:
                            ratio.update(comparison_kind='etf_daily_turnover_multiple',
                                         condition='同一交易日两只ETF的人民币成交额之比。')

    from .event_market import INDEX_NAMES as EVENT_INDEX_NAMES, PERIOD_BASIS as EVENT_PERIOD_BASIS
    from .event_market import event_reading_enabled, market_scopes, matched_sessions, verified_event_sessions_enabled

    event_scopes = market_scopes(task)
    for market, scope in event_scopes.items():
        event_pairs = matched_sessions(scope, facts, task=task)
        approximate = (matched_sessions(scope, facts) if verified_event_sessions_enabled(task) else {})
        for code in event_pairs:
            approximate.pop(code, None)
        for before, after, days in approximate.values():
            a, b = days[before], days[after]
            change = add('raw_change_v1', a['object_label']+'收盘区间变化', [a, b],
                         dict(a=a['raw_value'], b=b['raw_value']), before+'至'+after)
            if change:
                change.update(comparison_kind='event_observed_return', window_start=before, window_end=after,
                              condition='按两端实际收盘点位计算区间变化。')
        event_changes = {}
        for code, (before, after, days) in event_pairs.items():
            a, b = days[before], days[after]
            change = add('raw_change_v1', a['object_label']+'决议前后收盘变动', [a, b],
                         dict(a=a['raw_value'], b=b['raw_value']), before+'至'+after)
            if change:
                change.update(comparison_kind='event_close_return', window_start=before, window_end=after,
                              condition='用当地相邻交易日的价格指数收盘值计算，窗口内的信息共同作用。')
                event_changes[code] = change
        if market == 'mainland':
            sector_code, sector_label, benchmarks, venue = '399975.SZ', '券商指数', ('000300.SH', '000001.SH'), '境内'
        elif event_reading_enabled(task):
            sector_code, sector_label, benchmarks, venue = 'HSTECH.HI', '恒生科技指数', ('HSI.HI',), '香港'
        else:
            continue
        if not (sector := event_changes.get(sector_code)):
            continue
        for benchmark_code in benchmarks:
            benchmark = event_changes.get(benchmark_code)
            if not benchmark or sector['period'] != benchmark['period']:
                continue
            refs = [*sector['data_refs'], *benchmark['data_refs']]
            cells = {row['id']: row for row in facts if row['id'] in refs}
            title = sector_label + '相对' + EVENT_INDEX_NAMES[benchmark_code] + '的同窗表现'
            spread = add('raw_percentage_points_v1', title, [cells[ref] for ref in refs],
                         dict(a=benchmark['exact'], b=sector['exact']), sector['period'])
            if spread:
                spread.update(comparison_kind='event_market_spread', benchmark_code=benchmark_code,
                              condition=f'同一交易日期区间内两只{venue}价格指数的收盘涨跌幅之差。')

    from .treasury_rates import FIELD as H15_FIELD, selected_pairs as h15_pairs

    yield_pairs = h15_pairs(task, facts) if treasury_pairs is None else treasury_pairs
    for prior, current in yield_pairs.values():
        period = prior['period']+'至'+current['period']
        if change := add(YIELD_BASIS_POINTS, current['label']+'变动', [prior, current],
                         dict(a=prior['raw_value'], b=current['raw_value']), period):
            change.update(comparison_kind='treasury_event_move', window_start=prior['period'], window_end=current['period'])
    if (short := yield_pairs.get('DGS2')) and (long := yield_pairs.get('DGS10')):
        if all(a['period'] == b['period'] and a['artifact_id'] == b['artifact_id'] for a, b in zip(short, long, strict=True)):
            for a, b in zip(short, long, strict=True):
                spread = add(YIELD_BASIS_POINTS, '10年期与2年期利差', [a, b],
                             dict(a=a['raw_value'], b=b['raw_value']), a['period'])
                if spread:
                    spread['comparison_kind'] = 'treasury_term_spread'
            inputs = [short[0], long[0], short[1], long[1]]
            values = {key: row['raw_value'] for key, row in zip('abcd', inputs, strict=True)}
            span = short[0]['period']+'至'+short[1]['period']
            if shift := add(YIELD_CURVE_SHIFT, '10年期与2年期利差变动', inputs, values, span):
                shift['comparison_kind'] = 'treasury_curve_shift'

    from .lpr_rates import quote_pairs

    for prior, current in quote_pairs(facts).values():
        span = prior['period'] + '至' + current['period']
        if change := add(YIELD_BASIS_POINTS, current['label'] + '较上期变化', [prior, current],
                         dict(a=prior['raw_value'], b=current['raw_value']), span):
            change.update(comparison_kind='lpr_move', condition='本期减上期同期限LPR报价，百分点差乘以100换算为基点。')

    def add_price_summary(ordered, anchors, label, field, unit, *, request=None, sampled=False):
        if not ordered:
            return
        def period(first, last):
            return first['period']+'至'+last['period']

        def attach_scope(result):
            if result and request:
                result.update(window_start=request[0].isoformat(), window_end=request[1].isoformat())

        if anchors:
            a, b = anchors
            if (a['value'] is not None and b['value'] is not None and a['value'] > 0
                    and (b['value'] >= 0 if unit == '点' else b['value'] > 0)):
                change = add('raw_change_v1', label+('读数变化' if sampled else '变化'), [a,b],
                    dict(a=a.get('raw_value',a['value']), b=b.get('raw_value',b['value'])),
                    period(a,b), unit=='原始单位待核对')
                attach_scope(change)
                if sampled and change:
                    change['condition'] += '仅比较所列日期的两个收盘读数，交易日历未核对。'
            else:
                notes.append(dict(label=ordered[0]['label'],reason='起终原值缺失或非正，未改用其他日期计算变化率。'))
        available = [row for row in ordered if row['value'] is not None and (field not in NAV_FIELDS or row['value'] > 0)]
        if len(available) >= 2:
            low, high = min(available,key=_value_key), max(available,key=_value_key)
            attach_scope(add('raw_range_v1', '已取得'+label+'范围', [low,high],
                dict(a=low.get('raw_value',low['value']), b=high.get('raw_value',high['value'])),
                period(ordered[0],ordered[-1]), unit=='原始单位待核对'))

    def calendar_window(code, start, end):
        key = (code.rsplit('.',1)[-1],start,end)
        if key not in interval_sessions:
            interval_sessions[key] = verified_session_window(code, start, end, calendars)
        return interval_sessions[key]

    def record_gap(code, start, end, missing, **details):
        quote_interval_gaps[code,start,end,tuple(missing)] = dict(code=code, window_start=start.isoformat(),
            window_end=end.isoformat(), missing_close_dates=missing, **details)

    def add_post_release_returns(sessions, selected, conflicts, label):
        # Without an intraday baseline, a release on a trading day cannot be
        # assigned to before or after that session by a date-only comparison.
        if release_day is None or not sessions or release_day in sessions:
            return
        before = [day for day in sessions if day < release_day]
        after = [day for day in sessions if day > release_day][:2]
        if not before or len(after) != 2:
            return
        dates = [before[-1], *after]
        if any(day in conflicts or day not in selected or (selected[day]['value'] or 0) <= 0 for day in dates):
            return
        for name, first, last in zip(('发布后首日', '发布后次日'), dates[:-1], dates[1:], strict=True):
            left, right = selected[first], selected[last]
            pair = left['id'], right['id']
            if pair in event_return_pairs:
                continue
            calculation = add('raw_change_v1', label+name+'涨跌幅', [left, right],
                              dict(a=left['raw_value'], b=right['raw_value']), right['period'])
            if calculation:
                event_return_pairs.add(pair)
                calculation.update(comparison_kind='daily_close_return', window_start=left['period'],
                                   window_end=right['period'], session_basis='exchange_calendar',
                                   condition='以同一标的、同一币种及复权口径的相邻交易日收盘价复算。')

    def summarize_intervals(code, unit, dated, scopes):
        label = '收盘点位' if unit == '点' else '收盘价'
        last_end = None
        summarized = set()
        for start, end, questions in sorted(scopes):
            if last_end is not None and (start-last_end) > timedelta(days=1):
                intervening = calendar_window(code, last_end, start)
                if intervening is None or any(last_end < day < start for day in intervening):
                    disconnected_closes.add(code)
            last_end = max(last_end, end) if last_end is not None else end
            normalized = subject_key(code).upper()
            matching = {(normalized,start,end,question) for question in questions
                        if (start,end,question) in stated.get(normalized, ())}
            analyzed_stated.update(matching)
            exact_scope = bool(matching)
            if exact_scope and start == end:
                continue  # A single reading has no pair of endpoints to compare.
            selected, conflicts = _interval_close_rows(dated, code, start, end, questions, owned, quote_windows,
                allow_unlinked=reuse_retained and exact_scope)
            sessions = calendar_window(code, start, end)
            days = sorted(set(selected) | conflicts) if sessions is None else sessions
            endpoints = tuple(dict.fromkeys((days[0], days[-1]))) if days else ()
            calendar_unverified = exact_scope and sessions is None and endpoints != (start,end)
            if exact_scope and sessions == ():
                record_gap(code, start, end, [], no_sessions=True)
            elif calendar_unverified:
                record_gap(code, start, end, [], calendar_unverified=True)
            missing = [day.isoformat() for day in endpoints
                       if day not in conflicts and (day not in selected or selected[day]['value'] is None)]
            if not calendar_unverified and missing and (sessions is not None or exact_scope):
                record_gap(code, start, end, missing)
            if exact_scope and not calendar_unverified and not conflicts.isdisjoint(endpoints):
                record_gap(code,start,end,[day.isoformat() for day in endpoints if day in conflicts], conflicting=True)
            if any(day in conflicts for day in days):
                notes.append(dict(label=label,reason='区间内有同日收盘读数冲突，保留各来源原值。',
                                  period=start.isoformat()+'至'+end.isoformat()))
            anchors = (tuple(selected[day] for day in endpoints)
                       if len(endpoints) == 2 and not missing and not calendar_unverified
                       and conflicts.isdisjoint(endpoints) else None)
            ordered = [selected[day] for day in days if day in selected]
            identity = (start, end, tuple(row['id'] for row in ordered))
            if identity in summarized:
                continue
            summarized.add(identity)
            add_price_summary(ordered, anchors, label, 'close', unit, request=(start,end),
                              sampled=sessions is None and (exact_scope or endpoints != (start,end)))
            if exact_scope:
                add_post_release_returns(sessions, selected, conflicts, label)

    def summarize_nav(code, field, unit, dated):
        for start, end, question in sorted(nav_intervals[code]):
            if start == end:
                continue
            selected = {}
            for day, group in sorted(dated.items()):
                if not start <= day <= end:
                    continue
                candidates = [row for row in group if row['value'] is not None and row['value'] > 0
                              and market_question_matches(row, question)
                              and nav_date_matches(row, day)]
                if candidates and len({_value_key(row) for row in candidates}) == 1:
                    selected[day] = candidates[0]
            endpoints = {start, end} & selected.keys()
            scope = code, start, end, question
            nav_endpoints[scope].update(endpoints)
            anchors = (selected[start], selected[end]) if len(endpoints) == 2 else None
            if anchors:
                comparable_nav.add(scope)
            add_price_summary(list(selected.values()), anchors, '单位净值', field, unit, request=(start,end))

    def market_question_matches(row, question):
        linked = owned[row['artifact_id']].get('question_ids') or ()
        return question in linked or (reuse_retained and not linked)

    def nav_date_matches(row, day):
        record = owned[row['artifact_id']]
        if (record.get('source'), record.get('operation'), row['field']) == ('official', 'etf_pcf', 'unit_nav'):
            return True
        window = nav_windows.get(row['artifact_id'])
        return window is not None and window[0] <= day <= window[1] and row['code'] in window[2]

    def summarize_economic(code, dated):
        seen = set()
        for question, months, last_closed, start, end in economic_windows[code]:
            first, last = _period_number(start, months), _period_number(end, months)
            if first >= last or end > last_closed:
                continue
            endpoints = {first: [], last: []}
            for day, group in sorted(dated.items()):
                if not start <= day <= end:
                    continue
                slot = _period_number(day, months)
                if slot in endpoints:
                    endpoints[slot].extend(row for row in group if row['value'] is not None and
                        _economic_query_links(owned[row['artifact_id']], code, question))
            if any(len({_value_key(row) for row in rows}) != 1 for rows in endpoints.values()):
                continue
            left, right = (rows[-1] for rows in endpoints.values())
            pair = left['id'], right['id']
            if pair in seen:
                continue
            seen.add(pair)
            add('raw_percentage_points_v1', left['label']+'期间读数变化', [left, right],
                dict(a=left.get('raw_value', left['value']), b=right.get('raw_value', right['value'])),
                left['period']+'至'+right['period'])

    unresolved = {}
    for row in facts:
        field = analysis_field(row)
        if field == H15_FIELD:
            continue  # H.15 percent levels have their own release-aligned basis-point arithmetic.
        if field == 'close' and row.get('period_basis') in EVENT_PERIOD_BASIS.values():
            continue  # Use only the release-aligned price pairs for event arithmetic.
        if field in FINANCIAL and row.get('period_basis') == '报告期未明确':
            key = (row['code'], field)
            if key not in unresolved:
                unresolved[key] = dict(label=row['label'],code=row['code'],data_refs=[],
                    reason='报告期未明确，保留原值，不按响应时间补填或拼接计算；其他分析继续。')
                notes.append(unresolved[key])
            unresolved[key]['data_refs'].append(row['id'])
            continue
        if (field in FINANCIAL or field in {'close', PEER_ADJUSTED_PROFIT, *PEER_CASH_COMPONENTS.values(), *NAV_FIELDS}
                or field=='value' and row.get('artifact_id') in economic
                and row.get('unit')=='%' and row.get('axis_role','time')=='time'):
            series[(row['code'],field,row['unit'],row.get('series_basis',''))].append(row)
        if field in FINANCIAL:
            periods[(row['code'],row['period'],row['unit'],row.get('series_basis',''))].append(row)
    paired_notes = defaultdict(list)
    for row in facts:
        kind = row.get('locator', {}).get('kind')
        if ((kind == 'cash_bridge_cell' and row['field'] in CASH_BRIDGE_FIELDS)
                or (kind == 'filing_segment_comparison' and segment_field(row['field']))
                or (kind == 'peer_filing_cell' and row['field'] in (PEER_REVENUE_COMPONENTS.keys() | BANK_PROFIT_COMPONENTS.keys()))):
            paired_notes[kind, row['code'], row['field'], row['artifact_id'], row['unit']].append(row)
    for (kind, *_), rows in paired_notes.items():
        if len(rows) != 2:
            continue
        prior, current = sorted(rows, key=lambda item: item['period'])
        bank = current['field'] in BANK_PROFIT_COMPONENTS
        subject = '经营现金流' if kind == 'cash_bridge_cell' else '营业收入'
        label = current['label']+'同比变动' if bank else current['label']+'对'+subject+'同比变化的贡献'
        result = add('raw_difference_v1', label, [prior, current],
                     {'a': prior['raw_value'], 'b': current['raw_value']}, current['period'])
        if result:
            kind, condition = {
                'cash_bridge_cell': ('cash_bridge_change', '同份合并现金流量表附注两期调节项之差；反映会计调节贡献。'),
                'filing_segment_comparison': (SEGMENT_CHANGE, '同份半年度报告的两期分部营业收入之差，两期均与合并营业收入对账。'),
                'peer_filing_cell': (PEER_REVENUE_CHANGE, '同份合并利润表两期同名营业收入项目之差。'),
            }[kind]
            if bank:
                kind, condition = BANK_PROFIT_CHANGE, '同份合并利润表两期同名项目之差，减值保留列报损益符号。'
            result.update(comparison_kind=kind, condition=condition)
    for (code,field,unit,_),rows in series.items():
        dated=defaultdict(list)
        previous_closes=[]
        for row in rows:
            day=_day(row['period'])
            if day is None:
                continue
            hidden_target = row.get('previous_close_for')
            if not hidden_target:
                dated[day].append(row)
            target = hidden_target or (row.get('previous_close_target') if refresh_latest else None)
            if target:
                previous_closes.append((day, row, target))
        if field in NAV_FIELDS and code in nav_intervals:
            summarize_nav(code, field, unit, dated)
            continue
        if field == 'value' and code in economic_windows:
            summarize_economic(code, dated)
            continue
        scopes = set()
        if field == 'close' and interval_scopes:
            for ident in {row['artifact_id'] for row in rows if row['artifact_id'] in quote_windows}:
                origin = owned[ident]
                scopes.update(interval_scopes.get((origin['source'], origin['operation'], code), ()))
        if field == 'close':
            scopes.update((start,end,(question,)) for start,end,question in stated.get(subject_key(code).upper(), ()))
        if scopes:
            summarize_intervals(code, unit, dated, scopes)
        ambiguous=any(len({_value_key(row) for row in group})>1 for group in dated.values())
        if ambiguous:
            if not scopes:
                notes.append(dict(label=rows[0]['label'],reason='同日原值存在差异，未选择其中一份作为计算基准。'))
            continue
        unique={day:group[0] for day,group in dated.items()}
        if field == 'close' and quote_windows and dated:
            pair = _previous_session_pair(code, dated, quote_windows, calendars)
            if pair is None and previous_closes:
                available = [day for day, row in unique.items() if row['value'] is not None] if refresh_latest else dated
                target = max(available, default=None)
                if target is not None:
                    anchors = {row['artifact_id'] for row in dated[target]}
                    matches = [(day, row) for day, row, end in previous_closes
                               if end == target.isoformat() and row['artifact_id'] in anchors]
                    if matches or not refresh_latest:
                        inputs = defaultdict(list, {day:list(group) for day,group in dated.items()})
                        for day, row in matches:
                            if row.get('previous_close_for'):
                                inputs[day].append(row)
                        pair = _previous_session_pair(code, inputs, quote_windows, calendars, target=target)
            if pair and (subject_key(code).upper(), _day(pair[3])) not in return_pairs:
                requested = stated.get(subject_key(code).upper(), ())
                if not requested or any(first <= _day(pair[2]) <= _day(pair[3]) <= last for first,last,_ in requested):
                    daily.append((pair, unit))
        if scopes:
            continue
        if field in {'close', *NAV_FIELDS} and unique:
            label='单位净值' if field in NAV_FIELDS else '收盘点位' if unit=='点' else '收盘价'
            ordered=[unique[day] for day in sorted(unique)]
            anchor_rows = (ordered[0], ordered[-1]) if len(ordered)>=2 else None
            if field == 'close' and endpoint_policy == 1:
                windows = {quote_windows[row['artifact_id']][:2] for row in ordered
                           if row['artifact_id'] in quote_windows and code in quote_windows[row['artifact_id']][2]}
                if len(windows) == 1:
                    (start, end), = windows
                    sessions = calendar_window(code, start, end)
                    if sessions is not None:
                        endpoints = tuple(dict.fromkeys((sessions[0],sessions[-1]))) if sessions else ()
                        missing = [day.isoformat() for day in endpoints
                                   if day not in unique or unique[day]['value'] is None]
                        if missing:
                            record_gap(code, start, end, missing)
                        anchor_rows = tuple(unique[day] for day in endpoints) if len(endpoints)==2 and not missing else None
            add_price_summary(ordered, anchor_rows, label, field, unit,
                              sampled=field == 'close' and interval_policy)
        elif field in FINANCIAL or field in (PEER_ADJUSTED_PROFIT, *PEER_CASH_COMPONENTS.values()):
            for day,current in sorted(unique.items()):
                try:prior=unique.get(day.replace(year=day.year-1))
                except ValueError:prior=None
                if prior is None or prior['value'] is None or current['value'] is None:continue
                values=dict(a=prior.get('raw_value',prior['value']),b=current.get('raw_value',current['value']))
                corroborated = matched_summary_comparison(prior, current, owned)
                conditional = unit=='原始单位待核对' and not corroborated
                if prior['value']>0:
                    result = add('raw_change_v1',current['label']+('余额原值同比变化' if field in BALANCE else '原值同比变化'),[prior,current],values,current['period'],conditional)
                    if result and current.get('locator', {}).get('kind') == STOCK_QUALITY_CELL:
                        result.update(label=current['label']+'同比',
                                      condition='采用同份半年报本期与上年同期的累计金额。')
                    if result and corroborated:
                        result['comparison_source_ref'] = current['filing_comparison']['artifact_id']
                else:
                    add('raw_difference_v1',current['label']+('余额原值同比差额' if field in BALANCE else '原值同比差额'),[prior,current],values,current['period'],conditional)
                    notes.append(dict(label=current['label'],reason='同比基数非正，仅列原值差额，不给出增长率。'))
        elif field=='value' and unit=='%' and len(unique)>=2:
            ordered=[unique[day] for day in sorted(unique)]
            pairs=[('期间读数变化',ordered[0],ordered[-1])]
            if len(ordered)>2:pairs.append(('最近两期读数变化',ordered[-2],ordered[-1]))
            for label,first,last in pairs:
                if first['value'] is None or last['value'] is None:
                    notes.append(dict(label=first['label'],reason='指定端点缺失，未改用其他日期计算百分点差。'))
                    continue
                add('raw_percentage_points_v1',first['label']+label,[first,last],
                    dict(a=first.get('raw_value',first['value']),b=last.get('raw_value',last['value'])),
                    first['period']+'至'+last['period'])
    for (_,period,unit,_),rows in periods.items():
        grouped=defaultdict(list)
        for row in rows:grouped[analysis_field(row)].append(row)
        by_field={field:group[0] for field,group in grouped.items()
                  if len({_value_key(row) for row in group})==1}
        for method,label,a,b in [('raw_margin_v1','收入成本原值差占比','ths_revenue_stock','ths_operating_cost_stock'),
            ('raw_percent_v1','报表净利润/收入原值比','ths_np_stock','ths_revenue_stock'),
            ('raw_multiple_v1','经营现金流/报表净利润原值比','ths_ncf_from_oa_stock','ths_np_stock')]:
            left,right=by_field.get(a),by_field.get(b)
            if left is None or right is None or left['value'] is None or right['value'] is None:continue
            left_source, right_source = (owned[row['artifact_id']] for row in (left, right))
            if (refresh_financial and left_source.get('operation') == right_source.get('operation') == 'basic_data_service'
                    and left_source.get('retained_from_version', task['version'])
                    != right_source.get('retained_from_version', task['version'])):
                notes.append(dict(label=label, reason='同一报告期的两个原值来自不同研究版本，暂不合算。'))
                continue
            add(method,label,[left,right],dict(a=left.get('raw_value',left['value']),b=right.get('raw_value',right['value'])),
                period,unit=='原始单位待核对')
    point_fields = {(code, day.isoformat(), field) for field, mapping in (('close', stated), ('unit_nav', nav_intervals))
                    for code, intervals in mapping.items() for day, end, _ in intervals if day == end}
    point_fields.update((code, resolved[0][0], 'close') for (code, _), resolved in return_pairs.items() if resolved)
    point_rows = defaultdict(list)
    if point_fields:
        for row in facts:
            field = analysis_field(row)
            if field in NAV_FIELDS:
                field = 'unit_nav'
            day = _day(row['period'])
            key = (subject_key(row['code']).upper(), day.isoformat() if day else '', field)
            if key in point_fields and row['value'] is not None:
                point_rows[key].append(row)

    def close_points(code, day, question, *, previous_for=None):
        for row in point_rows[code, day.isoformat(), 'close']:
            window = quote_windows.get(row['artifact_id'])
            if (window and row['code'] in window[2] and window[0] <= day <= window[1]
                    and market_question_matches(row, question)
                    and (not row.get('previous_close_for') or row['previous_close_for'] == previous_for)):
                yield row

    def closing_basis(row):
        return row['code'], row['unit'], row.get('series_basis')

    return_gaps, explicit_daily = {}, {}
    def compare_requested_day(code, day, question, current):
        key = code, day
        resolved = return_pairs[key]
        if not resolved:
            return_gaps[key] = dict(code=code, day=day.isoformat(), reason='calendar_unverified')
            return
        (start, end), proof = resolved
        previous = list(close_points(code, _day(start), question, previous_for=end))
        gap = dict(code=code, day=end, previous_date=start)
        if not previous:
            gap['reason'] = ('previous_missing' if proof['kind']=='exchange_calendar' else 'calendar_unverified')
        else:
            by_basis = {closing_basis(row): row for row in current}
            same_basis = [row for row in previous if closing_basis(row) in by_basis]
            if not same_basis:
                gap['reason'] = 'incompatible_basis'
            elif len({(_value_key(row),row['unit']) for row in same_basis}) > 1:
                gap['reason'] = 'previous_conflict'
            else:
                first = same_basis[0]
                last = by_basis[closing_basis(first)]
                if first['value'] > 0 and last['value'] > 0:
                    explicit_daily.setdefault(key, ((first,last,start,end,proof), last['unit']))
                    return
                gap['reason'] = 'invalid_close'
        return_gaps[key] = gap

    for code, windows in stated.items():
        for start, end, question in sorted(windows):
            if start == end:
                day = start.isoformat()
                rows = list(close_points(code, start, question))
                values = {(_value_key(row),row['unit']) for row in rows}
                sessions = calendar_window(code, start, end)
                if sessions == ():
                    record_gap(code, start, end, [day], no_sessions=True)
                elif len(values) > 1:
                    record_gap(code, start, end, [day], conflicting=True)
                elif not values:
                    record_gap(code, start, end, [day], calendar_unverified=sessions is None)
                elif (code, start, question) in return_scopes:
                    compare_requested_day(code, start, question, rows)
                continue
            if (code,start,end,question) in analyzed_stated:
                continue
            sessions = calendar_window(code, start, end)
            if sessions is None:
                record_gap(code, start, end, [], calendar_unverified=True)
            elif not sessions:
                record_gap(code, start, end, [], no_sessions=True)
            else:
                record_gap(code, start, end, list(dict.fromkeys((sessions[0].isoformat(),sessions[-1].isoformat()))))
    compared_pairs = set()
    for code, pairs in stated_close_pairs(task).items():
        for start, end, question in sorted(pairs):
            dates = (start, end)
            points = [list(close_points(code, day, question)) for day in dates]
            missing = [day.isoformat() for day, rows in zip(dates, points)
                       if not rows or calendar_window(code,day,day) == ()]
            if missing:
                record_gap(code, start, end, missing)
                continue
            conflicts = [day.isoformat() for day, rows in zip(dates, points)
                         if len({(_value_key(row), row['unit']) for row in rows}) != 1]
            if conflicts:
                record_gap(code, start, end, conflicts, conflicting=True)
                continue
            closing = {closing_basis(row): row for row in points[1]}
            first = next((row for row in points[0] if closing_basis(row) in closing), None)
            if first is None:
                record_gap(code, start, end, [], incompatible_basis=True)
                continue
            last = closing[closing_basis(first)]
            if first['value'] <= 0 or last['value'] <= 0:
                record_gap(code, start, end, [], invalid_close=True)
                continue
            identity = (first['id'], last['id'])
            if identity in compared_pairs:
                continue
            compared_pairs.add(identity)
            period = start.isoformat()+'至'+end.isoformat()
            result = add('raw_change_v1', '区间收盘涨跌幅', [first,last],
                         dict(a=first['raw_value'], b=last['raw_value']), period,
                         first['unit'] == '原始单位待核对')
            if result:
                result.update(window_start=start.isoformat(), window_end=end.isoformat())
                result['condition'] += '比较指定两日的同口径收盘原值，不含分红或其他持有收益。'
    # Append daily calculations after existing results to preserve saved IDs.
    for (first,last,start,end,proof), unit in daily + list(explicit_daily.values()):
        if (first['id'], last['id']) in event_return_pairs:
            continue
        values = dict(a=first.get('raw_value',first['value']), b=last.get('raw_value',last['value']))
        result = add('raw_change_v1', '较上一交易日的收盘原值变化', [first,last], values, last['period'],
                     unit == '原始单位待核对')
        if result:
            basis = '交易日历已核对两日相邻' if proof['kind']=='exchange_calendar' else '两条未填充收盘记录的日期相连'
            if proof['kind'] == 'exchange_calendar' and task.get('research_mode') == 'historical':
                proof['historical_availability_approved'] = False
                basis = '按本次取得的交易日历核对两日相邻，价格和日历在历史时点的可得性未核验'
            result.update(comparison_kind='previous_session', window_start=start, window_end=end, session_basis=proof,
                condition=basis+'，使用同口径收盘原值。结果不等于含分红总回报或供应商另行披露的涨跌幅。')
    weight_totals = set()
    for sheet in stated_weight_sheets(task):
        if sheet['total'] and sheet['complete'] and sheet['rows']:
            by_member = defaultdict(list)
            for row in facts:
                if row['artifact_id'] == sheet['source_ref'] and row['field'] == 'weight':
                    by_member[row.get('locator', {}).get('member')].append(row)
            selected = []
            for position, member in sheet['rows']:
                candidates = by_member[position]
                if len(candidates) != 1:
                    break
                row, = candidates
                if (row['code'], row['period'], row['raw_value'], row['value'], row['unit']) != (
                        member['code'], sheet['day'], member['weight'], member['weight'], '%'):
                    break
                selected.append(row)
            if len(selected) == len(sheet['rows']):
                label = weight_total_label(sheet['count'])
                identity = (label, tuple(row['id'] for row in selected))
                if identity in weight_totals:
                    continue
                weight_totals.add(identity)
                if total := add(WEIGHT_TOTAL_METHOD, label, selected,
                                dict(weights=[row['raw_value'] for row in selected]), sheet['day']):
                    total.update(code=sheet['code'], comparison_kind='index_weight_total')

    premium_gaps = []
    attempted_premiums = set()
    for code, day, question in sorted(stated_daily_nav_premiums(task)):
        period = day.isoformat()
        navs = [row for row in point_rows[code, period, 'unit_nav'] if market_question_matches(row, question)]
        pair = _daily_nav_premium_pair(task, code, period, list(close_points(code, day, question)), navs, owned)
        if pair:
            price, nav = pair
            identity = (price['id'], nav['id'])
            if identity in attempted_premiums:
                continue
            attempted_premiums.add(identity)
            if add(DAILY_NAV_PREMIUM_METHOD, '收盘折溢价率', pair,
                   dict(price=price['value'], nav=nav['value'], valuation_date=period), period):
                continue
        premium_gaps.append(dict(code=code, period=period))
    valuation_scope = valuation_scope or valuation_history_scope(task)
    if valuation_scope and (sample := valuation_history_sample(task, valuation_scope, facts, owned)):
        current, history = sample
        ranking = add(VALUATION_POSITION_METHOD, '滚动市盈率历史分位', [current, *history],
                      dict(current=current['raw_value'], history=[row['raw_value'] for row in history]),
                      current['period'])
        if ranking:
            ranking.update(comparison_kind=VALUATION_POSITION, window_start=history[0]['period'],
                           window_end=history[-1]['period'], sample_count=len(history))

    if valuation_scope and valuation_attribution_enabled(task):
        points = valuation_attribution_points(task, valuation_scope, facts, owned)
        if points:
            start, end = points[0]['period'], points[1]['period']
            inputs = {key: row['raw_value'] for key, row in zip(_PE_ATTRIBUTION_FIELDS, points, strict=True)}
            for method, name in PE_ATTRIBUTION_METHODS.items():
                contribution = add(method, name+'贡献', points, inputs, start+'至'+end)
                if contribution:
                    contribution.update(comparison_kind=VALUATION_ATTRIBUTION, component=name,
                                        window_start=start, window_end=end)
            if task.get(VALUATION_HISTORY_POLICY) == VALUATION_HISTORY_VERSION:
                result = add(PE_EARNINGS_SCENARIO, METHODS[PE_EARNINGS_SCENARIO]['title'], points, inputs, start+'至'+end)
                if result:
                    result.update(comparison_kind=VALUATION_SCENARIO, window_start=start, window_end=end)

    if task.get(PEER_PROFIT_POLICY) in (1, PEER_PROFIT_DETAIL_VERSION):
        for rows, original in _peer_income_statements(task, facts, owned):
            inputs = {key: str(_peer_profit_amount(row)) for key, row in zip('abcdef', rows, strict=True)}
            bridge = add(PEER_NET_AFTER_GROSS, METHODS[PEER_NET_AFTER_GROSS]['title'], rows, inputs, rows[3]['period'])
            if bridge and original:
                for field, (before, after) in _peer_profit_line_pairs(original, facts):
                    label, _ = PEER_PROFIT_COMPONENTS[field]
                    item = add('raw_difference_v1', label+'同比变动', [before, after],
                               dict(a=before['raw_value'], b=after['raw_value']), after['period'])
                    if item:
                        item.update(comparison_kind=PEER_PROFIT_LINE_CHANGE, component=field,
                                    condition='同一份合并利润表的本期金额减上年同期金额。')

    analysis = dict(version=VERSION,task_id=task['id'],research_version=task['version'],binding=binding,
        method_registry=fingerprint(registered_methods(task)),results=results,notes=notes,financial_approval=False)
    if premium_gaps:
        analysis['daily_nav_premium_gaps'] = premium_gaps
    if quote_interval_gaps:
        analysis['quote_interval_gaps'] = list(quote_interval_gaps.values())
    if return_gaps:
        analysis['close_return_gaps'] = [gap for key, gap in return_gaps.items() if key not in explicit_daily]
    if disconnected_closes:
        analysis['disconnected_closes'] = sorted(disconnected_closes)
    nav_gaps, nav_readings = [], []
    source_windows = ({source: stated_nav_intervals(task, source=source) for source in ('ifind', 'official')}
                      if nav_intervals and nav_source_scope_enabled(task) else {})
    for code, windows in nav_intervals.items():
        point_questions = defaultdict(set)
        for start, end, question in sorted(windows):
            scope = code, start, end, question
            if start == end:
                point_questions[start].add(question)
            elif scope not in comparable_nav:
                nav_gaps.append(dict(code=code, window_start=start.isoformat(), window_end=end.isoformat(),
                    missing_dates=[day.isoformat() for day in sorted({start, end} - nav_endpoints[scope])]))
        for day, questions in point_questions.items():
            candidates = []
            for row in point_rows[code, day.isoformat(), 'unit_nav']:
                if (row['value'] <= 0 or not nav_date_matches(row, day)
                        or not any(market_question_matches(row, question) for question in questions)):
                    continue
                try:
                    validate_stated_source(task, owned[row['artifact_id']]['source'])
                except ContractError:
                    continue
                candidates.append(row)
            values = {_value_key(row) for row in candidates}
            units = {row['unit'] for row in candidates if row['unit'] not in ('', '原始单位待核对')}
            period = day.isoformat()
            if len(values) != 1 or len(units) > 1:
                gap = dict(code=code, window_start=period, window_end=period, missing_dates=[period],
                           conflicting=len(values)>1, unit_conflict=len(units)>1)
                if not values and source_windows:
                    requested = {(day, day, question) for question in questions}
                    allowed_sources = [source for source, windows in source_windows.items()
                                       if not requested.isdisjoint(windows.get(code, ()))]
                    if len(allowed_sources) == 1:
                        gap['requested_source'] = allowed_sources[0]
                nav_gaps.append(gap)
                continue
            # Agreement on the raw number can be reported with a proven unit
            # from its own source. Other rows retain their original units.
            reading = next((row for row in candidates if row['unit'] in units), candidates[0])
            nav_readings.append(dict(code=code, period=period, data_ref=reading['id']))
    if nav_gaps:
        analysis['nav_interval_gaps'] = nav_gaps
    if nav_readings:
        analysis['nav_point_readings'] = nav_readings
    if task.get(STOCK_QUALITY_POLICY) == QUALITY_BRIDGE_VERSION and REVENUE in quality and COST in quality:
        revenue_before, revenue_after = quality[REVENUE]
        cost_before, cost_after = quality[COST]
        rows = (revenue_before, cost_before, revenue_after, cost_after)
        if len({row['unit'] for row in rows}) == 1:
            inputs = {symbol: row['raw_value'] for symbol, row in zip('abcd', rows, strict=True)}
            for method in GROSS_BRIDGE_METHODS:
                add(method, METHODS[method]['title'], rows, inputs, revenue_after['period'])
    return analysis
