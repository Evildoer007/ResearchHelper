"""Registered arithmetic over draft observations, never financial approval.

The reviewed-calculation registry is unchanged. These methods describe raw
values with explicit scope and comparability conditions; failures are local.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import math

from .contracts import fingerprint, ContractError

VERSION = 2
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
    'raw_range_v1': dict(version=1, title='已取得原值范围', formula=r'[a,b]',
        fields=['a','b'], unit='原值', condition='仅已取得数值的最小与最大值；收盘价范围不是高低价振幅或波动率。'),
}
NAV_FIELDS = {'unit_nav', 'ths_unit_nvnf_fund'}
BALANCE = {'ths_account_receivable_stock','ths_inventory_stock','ths_accounts_payable_stock'}
FINANCIAL = BALANCE | {'ths_revenue_stock','ths_operating_cost_stock','ths_np_stock',
             'ths_np_atoopc_stock','ths_ncf_from_oa_stock'}


def registered_methods():
    return [dict(id=name, **definition) for name,definition in METHODS.items()]


def execute(method, values):
    definition=METHODS.get(method)
    if definition is None or set(values)!=set(definition['fields']):
        raise ContractError('草稿算术方法或输入未登记')
    try:
        if any(isinstance(value,bool) or value is None for value in values.values()):
            raise ValueError
        a,b=(Decimal(str(values[name])) for name in ('a','b'))
        if not a.is_finite() or not b.is_finite():raise ValueError
        with localcontext() as context:
            context.prec=40
            if method=='raw_change_v1':
                if a<=0:raise ValueError
                result=(b/a-1)*100
            elif method in ('raw_difference_v1','raw_percentage_points_v1'):result=b-a
            elif method in ('raw_percent_v1','raw_multiple_v1'):
                if b<=0:raise ValueError
                result=a/b*(100 if method=='raw_percent_v1' else 1)
            elif method=='raw_margin_v1':
                if a<=0 or b<0:raise ValueError
                result=(a-b)/a*100
            else:
                if a>b:raise ValueError
                return dict(minimum=str(a),maximum=str(b),display=f'{a}至{b}')
            if not result.is_finite() or not math.isfinite(float(result)):raise ValueError
            return dict(value=float(result),exact=str(result),display=str(result.quantize(Decimal('.01'))))
    except (ValueError,InvalidOperation,OverflowError):
        raise ContractError('该组原值不适用此项算术，保留原值及其他结果') from None


def _day(value):
    try:
        text=str(value)
        return date.fromisoformat(text if len(text)==10 else text[:4]+'-'+text[4:6]+'-'+text[6:])
    except ValueError:return None


def _value_key(row):
    return None if row.get('value') is None else Decimal(str(row.get('raw_value',row['value'])))


def input_binding(task, facts):
    return fingerprint(dict(task_id=task['id'],version=task['version'],input_hash=task.get('input_hash'),facts=facts))


def current_analysis(task, facts):
    value=task.get('draft_analysis',{})
    return value if (value.get('task_id')==task['id'] and value.get('research_version')==task['version']
        and value.get('binding')==input_binding(task,facts) and value.get('version')==VERSION
        and value.get('method_registry')==fingerprint(registered_methods())) else {}


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
        'financial_period_scope':'财务数据按该字段请求的D参数标记报告期，响应time和取数日期不能代替报告期；报告期未明确时保留原值，不按同日拼接计算同比或跨指标比率。',
        'cash_attribution':'经营现金流或现金流/净利润的变化不能单独证明回款、主营经营或盈利质量改善；归因需有同期间、同范围的实际分解。',
        'parent_profit_scope':'经营现金流与归母净利润可分别描述同比，但不能用归母净利润代替同口径报表净利润构造现金含量或覆盖比，也不以两者降幅确定盈利现金含量变化。',
        'balance_flow_scope':'应收、存货和应付是时点余额；余额变化不是当期现金收付，也不是现金流同比变化的直接贡献。',
        'valuation_scope':'估值读数变化不是估值高低结论。年度利润不能自动代替该观察时点的TTM分母；未核对适用盈利口径时，不仅凭年度利润下降和短窗价格上涨认定涨幅来源。',
        'close_scope':'收盘观测只描述实际取得的收盘路径，不代替日内高低价、总回报或事件因果。',
        'nav_scope':'单位净值读数变化未作分红再投资、份额折算和投资者现金流调整，不等于持有人总回报；净值绝对高低也不能直接比较基金优劣。',
        'macro_contribution':'宏观读数及百分点变化不等于贡献分解；缺少对应权重和分解时，不认定主要拉动或拖累，也不把共同变动写成因果。',
    }
    selected=defaultdict(list)
    for code,rows in grouped.items():
        fields={row['field'] for row in rows}
        if any(row.get('period_basis') for row in rows):selected['financial_period_scope'].append(code)
        if 'ths_ncf_from_oa_stock' in fields:
            selected['cash_attribution'].append(code)
            if 'ths_np_atoopc_stock' in fields:
                selected['parent_profit_scope'].append(code)
        if fields & BALANCE:selected['balance_flow_scope'].append(code)
        if fields & {'ths_pe_ttm_stock','ths_pb_mrq_stock','ths_pb_latest_stock','ths_pe_index','ths_pb_index','ths_ps_index'}:
            selected['valuation_scope'].append(code)
        if 'close' in fields:selected['close_scope'].append(code)
        if fields & NAV_FIELDS:selected['nav_scope'].append(code)
        if any(row['field']=='value' and records[row['artifact_id']].get('operation')=='edb_service' for row in rows):
            selected['macro_contribution'].append(code)
    return dict(version=1,binding=dict(task_id=task_id,research_version=version,
        input_hash=task.get('input_hash'),facts_hash=fingerprint(facts)),
        rules=[dict(id=key,objects=sorted(selected[key]),text=definitions[key])
               for key in definitions if selected[key]],financial_approval=False,delivery_blocking=False)


def formula_mathml(method):
    bodies={
        'raw_change_v1':'<mrow><mo>(</mo><mfrac><mi>b</mi><mi>a</mi></mfrac><mo>−</mo><mn>1</mn><mo>)</mo><mo>×</mo><mn>100</mn></mrow>',
        'raw_difference_v1':'<mrow><mi>b</mi><mo>−</mo><mi>a</mi></mrow>',
        'raw_percentage_points_v1':'<mrow><mi>b</mi><mo>−</mo><mi>a</mi></mrow>',
        'raw_percent_v1':'<mrow><mfrac><mi>a</mi><mi>b</mi></mfrac><mo>×</mo><mn>100</mn></mrow>',
        'raw_multiple_v1':'<mfrac><mi>a</mi><mi>b</mi></mfrac>',
        'raw_margin_v1':'<mrow><mfrac><mrow><mi>a</mi><mo>−</mo><mi>b</mi></mrow><mi>a</mi></mfrac><mo>×</mo><mn>100</mn></mrow>',
        'raw_range_v1':'<mrow><mo>[</mo><mi>a</mi><mo>,</mo><mi>b</mi><mo>]</mo></mrow>',
    }
    return '<math xmlns="http://www.w3.org/1998/Math/MathML">'+bodies[method]+'</math>' if method in bodies else ''


def analyze(task, facts):
    # Cache identity covers the full observed snapshot, including unrelated rows.
    # Selection affects arithmetic inputs, never the identity of its source set.
    binding=input_binding(task,facts)
    owned={row['id'] for row in task.get('source_records',[]) if row.get('task_id')==task['id']
           and row.get('research_version')==task['version']}
    economic={row['id'] for row in task.get('source_records',[]) if row['id'] in owned
              and row.get('source')=='ifind' and row.get('operation')=='edb_service'}
    facts=[row for row in facts if row.get('artifact_id') in owned and row.get('code')]
    results=[];notes=[];series=defaultdict(list);periods=defaultdict(list)

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
        if rows[0]['field'] in NAV_FIELDS:
            results[-1]['condition'] += '仅单位净值读数；未作分红再投资、份额折算和投资者现金流调整，不等于持有人总回报。'
        if rows[0]['field'] in BALANCE:
            results[-1]['period_kind'] = 'point_in_time'
            results[-1]['condition'] += '余额时点变化不能视为当期经营现金流贡献。'

    unresolved = {}
    for row in facts:
        if row.get('field') in FINANCIAL and row.get('period_basis') == '报告期未明确':
            key = (row['code'], row['field'])
            if key not in unresolved:
                unresolved[key] = dict(label=row['label'],code=row['code'],data_refs=[],
                    reason='报告期未明确，保留原值，不按响应时间补填或拼接计算；其他分析继续。')
                notes.append(unresolved[key])
            unresolved[key]['data_refs'].append(row['id'])
            continue
        if (row.get('field') in FINANCIAL or row.get('field') in {'close', *NAV_FIELDS}
                or row.get('field')=='value' and row.get('artifact_id') in economic
                and row.get('unit')=='%' and row.get('axis_role','time')=='time'):
            series[(row['code'],row['field'],row['unit'],row.get('series_basis',''))].append(row)
        if row.get('field') in FINANCIAL:
            periods[(row['code'],row['period'],row['unit'],row.get('series_basis',''))].append(row)
    for (_,field,unit,_),rows in series.items():
        dated=defaultdict(list)
        for row in rows:
            day=_day(row['period'])
            if day is not None:dated[day].append(row)
        ambiguous=any(len({_value_key(row) for row in group})>1 for group in dated.values())
        if ambiguous:
            notes.append(dict(label=rows[0]['label'],reason='同日原值存在差异，未选择其中一份作为计算基准。'));continue
        unique={day:group[0] for day,group in dated.items()}
        if field in {'close', *NAV_FIELDS} and len(unique)>=2:
            label='单位净值' if field in NAV_FIELDS else '收盘点位' if unit=='点' else '收盘价'
            ordered=[unique[day] for day in sorted(unique)]
            first,last=ordered[0],ordered[-1];window=first['period']+'至'+last['period']
            valid_endpoints=(first['value'] is not None and last['value'] is not None
                and first['value']>0 and (last['value']>=0 if unit=='点' else last['value']>0))
            if valid_endpoints:
                add('raw_change_v1',label+'变化',[first,last],
                    dict(a=first.get('raw_value',first['value']),b=last.get('raw_value',last['value'])),window,unit=='原始单位待核对')
            else:
                notes.append(dict(label=first['label'],reason='起终原值缺失或非正，未改用其他日期计算变化率。'))
            available=[row for row in ordered if row['value'] is not None and (field not in NAV_FIELDS or row['value']>0)]
            if len(available)>=2:
                low=min(available,key=lambda row:row['value']);high=max(available,key=lambda row:row['value'])
                add('raw_range_v1','已取得'+label+'范围',[low,high],
                    dict(a=low.get('raw_value',low['value']),b=high.get('raw_value',high['value'])),window,unit=='原始单位待核对')
        elif field in FINANCIAL:
            for day,current in sorted(unique.items()):
                try:prior=unique.get(day.replace(year=day.year-1))
                except ValueError:prior=None
                if prior is None or prior['value'] is None or current['value'] is None:continue
                values=dict(a=prior.get('raw_value',prior['value']),b=current.get('raw_value',current['value']))
                conditional=unit=='原始单位待核对'
                if prior['value']>0:
                    add('raw_change_v1',current['label']+('余额原值同比变化' if field in BALANCE else '原值同比变化'),[prior,current],values,current['period'],conditional)
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
        for row in rows:grouped[row['field']].append(row)
        by_field={field:group[0] for field,group in grouped.items()
                  if len({_value_key(row) for row in group})==1}
        for method,label,a,b in [('raw_margin_v1','收入成本原值差占比','ths_revenue_stock','ths_operating_cost_stock'),
            ('raw_percent_v1','报表净利润/收入原值比','ths_np_stock','ths_revenue_stock'),
            ('raw_multiple_v1','经营现金流/报表净利润原值比','ths_ncf_from_oa_stock','ths_np_stock')]:
            left,right=by_field.get(a),by_field.get(b)
            if left is None or right is None or left['value'] is None or right['value'] is None:continue
            add(method,label,[left,right],dict(a=left.get('raw_value',left['value']),b=right.get('raw_value',right['value'])),
                period,unit=='原始单位待核对')
    return dict(version=VERSION,task_id=task['id'],research_version=task['version'],binding=binding,
        method_registry=fingerprint(registered_methods()),results=results,notes=notes,financial_approval=False)
