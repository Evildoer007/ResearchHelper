"""Financial comparison figures from frozen, registered draft arithmetic.

Only presentation is derived. Invalid or inapplicable calculations fall back to
raw figures and tables; no report, prose, observation or method is rewritten.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date
from math import isfinite, ceil
from .draft_analysis import METHODS,execute,_day,BALANCE,FINANCIAL
from .draft_sources import _sequence_day
from .contracts import MAX_BROWSER_INTEGER,ContractError,fingerprint
from .tinyshare_sources import analysis_field
from .publication_content import draft_logic_data_refs, draft_logic_reading
from .industry_chain import CELL_KIND as INDUSTRY_CELL_KIND

FIELDS={
    'ths_revenue_stock':'营业收入','ths_operating_cost_stock':'营业成本',
    'ths_np_stock':'净利润','ths_np_atoopc_stock':'归母净利润',
    'ths_ncf_from_oa_stock':'经营现金流',
    'ths_account_receivable_stock':'应收账款','ths_inventory_stock':'存货','ths_accounts_payable_stock':'应付账款',
}
UNKNOWN_FINANCIAL_UNITS = ('', '原始单位待核对', '接口原值', '原值')
RATIO_FIELDS={
    'raw_margin_v1':('ths_revenue_stock','ths_operating_cost_stock'),
    'raw_percent_v1':('ths_np_stock','ths_revenue_stock'),
    'raw_multiple_v1':('ths_ncf_from_oa_stock','ths_np_stock'),
}


def _validated(content):
    draft=content.get('draft',{})
    owned={row['id']: row for row in content.get('source_records',[]) if
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
        if left['unit']=='原始单位待核对' and item.get('conditional_equal_scale') is not True:
            from .financial_data import matched_summary_comparison
            if not matched_summary_comparison(left, right, owned):
                continue
        if method=='raw_change_v1':
            before,after=_day(left['period']),_day(right['period'])
            if analysis_field(left) not in FIELDS or analysis_field(left)!=analysis_field(right) or before is None or after is None:continue
            try:matching=before==after.replace(year=after.year-1)
            except ValueError:matching=False
            if not matching:continue
        elif ((analysis_field(left),analysis_field(right))!=RATIO_FIELDS[method] or left['period']!=right['period']):continue
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


def _margin_bridge(name, periods, costs, profits):
    """Reconcile opposite margin movements using a shared revenue denominator."""
    from .draft_analysis import net_margin_components

    components = net_margin_components(costs, profits)
    if components is None:
        return None
    profit_before, profit_after, cost_change, other_change = components
    profit_change = profit_after - profit_before
    if cost_change * profit_change >= 0 or min(abs(cost_change), abs(profit_change)) < Decimal('0.005'):
        return None
    from .publication_content import reader_period_label
    labels = [reader_period_label(period) for period in periods]
    return dict(name=name, kind='margin_bridge', field='financial_comparison', full_width=True,
        horizontal=False, type='bar', height=174, min_height=174, unit='%',
        context='其余损益为净利润率扣除收入成本差占比后的差额，图示其同比变化。',
        data_refs=list(dict.fromkeys(ref for row in (*costs, *profits) for ref in row['data_refs'])),
        calculation_refs=[row['id'] for row in (*costs, *profits)],
        point_refs=[profits[0]['data_refs'],
            list(dict.fromkeys(ref for row in costs for ref in row['data_refs'])),
            list(dict.fromkeys(ref for row in (*costs, *profits) for ref in row['data_refs'])),
            profits[1]['data_refs']],
        scheme='waterfall', data=dict(
            start=float(profit_before), end=float(profit_after), startName=labels[0], endName=labels[1],
            steps=[dict(name='收入成本差', value=float(cost_change)),
                   dict(name='其余损益', value=float(other_change))],
            unit='%', changeUnit='个百分点', numberDigits=2))


def _calculation_dependencies(content, original_rows=None):
    """Resolve frozen arithmetic, including macro readings, for figure evidence.

    Financial plot eligibility is narrower than arithmetic provenance. Reuse
    the registered analyzer over the same owned cells; this is read-only local
    arithmetic, not a new research result or an approval of the conclusion.
    """
    from .draft_analysis import PEER_PROFIT_POLICY, VALUATION_ATTRIBUTION, VALUATION_POSITION, VALUATION_SCENARIO, analyze
    from .contracts import market_date
    from .economic_sources import DRAFT_PERIOD_POLICY
    from .etf_comparison import CELL_KIND as ETF_INTERIM_CELL, HOLDING_POLICY, frozen_cell_locations
    from .index_sources import DRAFT_WEIGHT_POLICY, PROFILE_POLICY
    from .market_calendar import (LATEST_SESSION_CALCULATION_POLICY, POLICY as CALENDAR_POLICY,
                                  QUOTE_ENDPOINT_POLICY, replay_calendar_windows)
    from .market_series import (BATCH_POLICY, NAV_INTERVAL_POLICY, NAV_SOURCE_POLICY, STATED_LATEST_POLICY,
                                VALUATION_ATTRIBUTION_POLICY, VALUATION_HISTORY_POLICY)
    from .monthly_statistics import CELL_KIND as MONTHLY_CELL, POLICY as MONTHLY_POLICY, frozen_retail_refs
    from .parallel_study import POLICY as PARALLEL_POLICY
    draft = content.get('draft', {})
    sources = [row for row in content.get('source_records', [])
               if row.get('task_id') == content['task_id']
               and row.get('research_version') == content['research_version']]
    owned = {row['id'] for row in sources}
    grouped = defaultdict(list)
    for row in draft.get('facts', []):
        grouped[row['id']].append(row)
    facts = [rows[0] for rows in grouped.values() if len(rows) == 1 and rows[0].get('artifact_id') in owned]
    try:
        cutoff = date.fromisoformat(market_date(content['cutoff']))
        retail_refs = frozen_retail_refs(content, facts) if content.get('monthly_retail_scope') else set()
        interim_refs = frozen_cell_locations(content, facts)
        def observation_day(row):
            period = row.get('period', '')
            kind = row.get('locator', {}).get('kind')
            if kind == MONTHLY_CELL and row['id'] in retail_refs:
                return _day(period+'-01')
            if kind == ETF_INTERIM_CELL:
                return _day(period.rsplit('至', 1)[-1]) if row['id'] in interim_refs else None
            if kind in ('filing_segment_comparison', INDUSTRY_CELL_KIND):
                period = period.rsplit('至', 1)[-1]
            return _day(period)

        facts = [row for row in facts if (day := observation_day(row)) is not None and day <= cutoff]
        calendars = replay_calendar_windows(content)
        mode = content.get('research_mode')
        if mode is None and calendars:
            mode = content['calendar_replay']['research_mode']
        task = dict(id=content['task_id'], version=content['research_version'], cutoff=content['cutoff'],
                    input_hash=content.get('input_hash'), source_records=sources, research_mode=mode)
        for key in ('question', 'questions', 'objects', 'intent', 'subject_scope', 'security_declaration_encoding',
                    'question_origin', 'market_date_reference'):
            if key in content:
                task[key] = content[key]
        # A refreshed latest observation can include a later blank. Replay the
        # version's rules when choosing the actual close and its previous session.
        for key in ('draft_source_reader_version', 'draft_historical_daily_version', 'draft_tinyshare_reader_version',
                    'market_observation_selection_version', QUOTE_ENDPOINT_POLICY, NAV_INTERVAL_POLICY, NAV_SOURCE_POLICY, STATED_LATEST_POLICY,
                    BATCH_POLICY, DRAFT_PERIOD_POLICY, DRAFT_WEIGHT_POLICY, PROFILE_POLICY, 'official_index_sources_version',
                    CALENDAR_POLICY, LATEST_SESSION_CALCULATION_POLICY, VALUATION_ATTRIBUTION_POLICY, VALUATION_HISTORY_POLICY,
                    'economic_discovery_version', 'draft_acquisition_coverage', 'update_from',
                    MONTHLY_POLICY, PEER_PROFIT_POLICY, PARALLEL_POLICY):
            if key in content:
                task[key] = content[key]
        # Relative-date parsing and arithmetic replay must see the same verified
        # windows. Link the retained transport events in a detached ledger.
        events = {}
        if CALENDAR_POLICY in task and calendars:
            replay = content['calendar_replay']
            task['created_at'] = replay['created_at']
            events.update({record['id']: replay['events'][record['id']]['event'] for record, _ in calendars})
        if premium := content.get('daily_nav_premium_sources'):
            from .etf_sources import POLICY as ETF_PCF_POLICY

            task.setdefault('created_at', premium['created_at'])
            task['daily_nav_date_roles_version'] = content['daily_nav_date_roles_version']
            task[ETF_PCF_POLICY] = content[ETF_PCF_POLICY]
            for record in sources:
                source_operation = (record.get('source'), record.get('operation'))
                if source_operation in (('ifind', 'cmd_history_quotation'), ('official', 'etf_pcf')):
                    if event := premium['events'].get(record['id']):
                        events[record['id']] = event
        if trading := content.get('etf_trading_replay'):
            from .etf_listing import POLICY as ETF_LISTING_POLICY
            from .etf_trading import POLICY as ETF_TRADING_POLICY

            task.setdefault('created_at', trading['created_at'])
            for key in (HOLDING_POLICY, ETF_TRADING_POLICY, ETF_LISTING_POLICY, 'official_source_expansion_version'):
                if key in content:
                    task[key] = content[key]
            events.update(trading['events'])
        valuation = content.get('valuation_history_replay')
        if valuation:
            task.setdefault('created_at', valuation['created_at'])
            events.update(valuation['events'])
        if events:
            dispatches = []
            task['ledger'] = {'dispatches': dispatches}
            for index, record in enumerate(sources):
                if event := events.get(record['id']):
                    sources[index] = dict(record, acquisition=dict(record['acquisition'], dispatch_index=len(dispatches)))
                    dispatches.append(event)
        prior = task.get('update_from')
        history = prior.get('market_refreshes') if isinstance(prior, dict) else None
        if mode is None and isinstance(history, dict) and history:
            from .draft_sources import _market_refresh_receipt
            # Older current reports froze receipts for inherited market responses
            # without an explicit mode. Historical updates did not issue these.
            if any(row.get('retained_from_version') and row['id'] in history
                   and _market_refresh_receipt(task, row, history) for row in sources):
                task['research_mode'] = 'current'
        from .stock_quality import (CELL_KIND as STOCK_QUALITY_CELL, POLICY as STOCK_QUALITY_POLICY,
                                    STRATEGY_POLICY as STOCK_STRATEGY_POLICY, frozen_pairs)
        from .treasury_rates import FIELD as H15_FIELD, frozen_pairs as frozen_yield_pairs
        from .lpr_rates import FIELD as LPR_FIELD, frozen_quotes

        for policy in (STOCK_QUALITY_POLICY, STOCK_STRATEGY_POLICY):
            if policy in content:
                task[policy] = content[policy]

        stock_pairs = frozen_pairs(content, facts)
        filing_refs = {row['id'] for pair in stock_pairs.values() for row in pair}
        facts = [row for row in facts if row.get('locator', {}).get('kind') != STOCK_QUALITY_CELL
                 or row['id'] in filing_refs]
        yields = frozen_yield_pairs(content, facts) if content.get('fomc_treasury_scope') else {}
        yield_refs = {row['id'] for pair in yields.values() for row in pair}
        facts = [row for row in facts if row['field'] != H15_FIELD or row['id'] in yield_refs]
        quote_refs = {row['id'] for row in frozen_quotes(content, facts)}
        facts = [row for row in facts if row['field'] != LPR_FIELD or row['id'] in quote_refs]
        expected = analyze(task, facts, calendars=calendars, stock_pairs=stock_pairs,
                           valuation_scope=valuation['scope'] if valuation else None, treasury_pairs=yields)['results']
        keys = ('method', 'method_version', 'code', 'period', 'data_refs', 'inputs',
                'unit', 'value', 'minimum', 'maximum', 'display', 'exact', 'condition', 'conditional_equal_scale',
                'comparison_kind', 'window_start', 'window_end', 'session_basis', 'sample_count', 'component')
        identity = lambda item: fingerprint({key: item.get(key) for key in keys})
        allowed = {identity(item) for item in expected}
        by_id = {row['id']: row for row in facts}
        grouped_results = defaultdict(list)
        for item in draft.get('calculations', []):
            grouped_results[item['id']].append(item)
        original_series_calculations = ('previous_session', VALUATION_POSITION, VALUATION_ATTRIBUTION, VALUATION_SCENARIO)
        if original_rows is None:
            original_rows = _observed_series_rows(content) if any(
                row.get('comparison_kind') in original_series_calculations for row in expected) else set()
        dependencies = {}
        for ident, rows in grouped_results.items():
            if len(rows) != 1 or rows[0].get('financial_approval') or identity(rows[0]) not in allowed:
                continue
            item = rows[0]
            inputs = [by_id[ref] for ref in item['data_refs']]
            if item.get('comparison_kind') in original_series_calculations and any(
                    _market_row_key(row) not in original_rows for row in inputs):
                continue
            dependencies[ident] = dict(item, rows=inputs)
        return dependencies
    except (ContractError, InvalidOperation, KeyError, TypeError, ValueError):
        # A malformed old result must not interrupt prose or raw observations.
        return {}


def _financial_observation(row, record):
    from .financial_sources import FINANCIAL_QUERY_FIELDS

    return analysis_field(row) in FIELDS or (row.get('label') in FINANCIAL_QUERY_FIELDS
        and (record.get('source'), record.get('operation')) == ('ifind', 'smart_stock_picking'))


def _financial_figure(content, figure):
    """Classify from owned source fields, without renaming source identifiers."""
    refs=set(figure.get('data_refs',figure.get('refs',[])))
    if not refs:
        return figure.get('field') in FIELDS
    owned={row['id']: row for row in content.get('source_records',[]) if
        row.get('task_id')==content.get('task_id') and row.get('research_version')==content.get('research_version')}
    rows=[row for row in content.get('draft',{}).get('facts',[]) if row.get('id') in refs
          and row.get('artifact_id') in owned]
    return {row['id'] for row in rows}==refs and all(_financial_observation(row, owned[row['artifact_id']]) for row in rows)


def comparison_figures(content,raw_figures):
    """Return a comparable view while preserving all unrepresented raw series."""
    from .publication_content import financial_scope_labels
    calculations=_validated(content)
    def cohort(row):return row.get('code',''),row.get('series_basis',''),row.get('unit','')
    owned={record['id'] for record in content.get('source_records',[]) if
        record.get('task_id')==content.get('task_id') and record.get('research_version')==content.get('research_version')}
    facts={row['id']:row for row in content.get('draft',{}).get('facts',[]) if row.get('artifact_id') in owned}
    scope_labels=financial_scope_labels(content,facts.values())
    def scope_label(rows):
        labels={scope_labels.get(row['id'],'') for row in rows}
        return next(iter(labels)) if len(labels)==1 and '' not in labels else ''
    growth=defaultdict(list);ratios=defaultdict(list);figures=[]
    codes={row['code'] for row in calculations}
    prefix=lambda code:code+' ' if len(codes)>1 else ''
    for row in calculations:
        if row['method']=='raw_change_v1':
            growth[(cohort(row['rows'][0]),row['rows'][0]['period'],row['period'],analysis_field(row['rows'][0]) in BALANCE)].append(row)
        else:ratios[(cohort(row['rows'][0]),row['method'])].append(row)

    def add(name,kind,rows,**payload):
        figures.append(dict(name=name,kind=kind,field='financial_comparison',
            data_refs=list(dict.fromkeys(ref for row in rows for ref in row['data_refs'])),
            calculation_refs=[row['id'] for row in rows],horizontal=True,**payload))
        label=scope_label([fact for row in rows for fact in row['rows']])
        if label:figures[-1]['comparison_scope']=label

    for (basis,before,after,is_balance),rows in growth.items():
        code=basis[0]
        by_field=defaultdict(list)
        for row in rows:by_field[analysis_field(row['rows'][0])].append(row)
        # Conflicting duplicates remain visible in their raw series.
        rows=[by_field[field][0] for field in FIELDS if len(by_field[field])==1]
        single_unknown=(len(rows)==1 and rows[0]['rows'][0]['unit']=='原始单位待核对'
                        and rows[0].get('conditional_equal_scale') is True)
        if len(rows)<2 and not single_unknown:continue
        context={}
        if single_unknown:
            name=FIELDS[analysis_field(rows[0]['rows'][0])]+('余额变化' if is_balance else '变化')
            first,last=_day(before),_day(after)
            context['context']=(str(last.year)+'年同比' if (first.month,first.day)==(12,31) and (last.month,last.day)==(12,31)
                                else last.isoformat()+'较'+first.isoformat())
        else:name='营运资金余额同比变化' if is_balance else '经营指标同比变化'
        add(prefix(code)+name,'changes',rows,unit='%',
            dates=[FIELDS[analysis_field(row['rows'][0])] for row in rows],values=[row['value'] for row in rows],
            period=after,comparison=before,height=170 if single_unknown else max(220,len(rows)*38+50),
            keywords=['同比','增长','收入','成本','利润','盈利'],type='bar',**context)

    paired={}
    for (basis,method),rows in ratios.items():
        by_period=defaultdict(list)
        for row in rows:by_period[row['period']].append(row)
        if (len(by_period)!=2 or any(len(items)!=1 for items in by_period.values())
                or len({item['rows'][0].get('series_basis') for item in rows})!=1):continue
        periods=sorted(by_period,key=lambda value:_day(value) or date.min)
        before,after=(_day(value) for value in periods)
        if before is None or after is None:continue
        try:matching=before==after.replace(year=after.year-1)
        except ValueError:matching=False
        if matching:paired[(basis,method)]=(periods,[by_period[value][0] for value in periods])
    for basis in sorted(dict.fromkeys(key[0] for key in paired),key=lambda item:item[0]):
        code=basis[0]
        profit=[paired[(basis,method)] for method in ('raw_margin_v1','raw_percent_v1') if (basis,method) in paired]
        if profit and all(pair[0]==profit[0][0] for pair in profit):
            rows=[row for _,items in profit for row in items]
            labels=['收入成本差占比' if items[0]['method']=='raw_margin_v1' else '报表净利润率' for _,items in profit]
            add(prefix(code)+'收入成本差占比与净利润率' if len(profit)>1 else prefix(code)+labels[0],
                'paired',rows,unit='%',dates=labels,periods=profit[0][0],
                pairs=[[items[0]['value'],items[1]['value']] for _,items in profit],height=225,
                keywords=['净利率','净利润率','差额占比','成本差','利润'],type='scatter')
            if len(profit) == 2 and (bridge := _margin_bridge(prefix(code)+'净利润率变动拆解',
                    profit[0][0], profit[0][1], profit[1][1])):
                comparison = scope_label([fact for row in rows for fact in row['rows']])
                figures.append(dict(bridge, comparison_scope=comparison))
        coverage=paired.get((basis,'raw_multiple_v1'))
        if coverage:
            periods,rows=coverage
            add(prefix(code)+'经营现金流覆盖倍数','paired',rows,unit='倍',dates=['经营现金流/净利润'],
                periods=periods,pairs=[[row['value'] for row in rows]],height=190,
                keywords=['现金流','现金覆盖','现金回收'],type='scatter')
    represented={ref for figure in figures for ref in figure['data_refs']}
    remaining=[figure for figure in raw_figures if not (_financial_figure(content,figure)
        and figure.get('data_refs') and set(figure['data_refs']).issubset(represented))]
    for figure in remaining:
        rows=[facts[ref] for ref in figure.get('data_refs',[]) if ref in facts]
        if rows and len(rows)==len(figure.get('data_refs',[])) and all(analysis_field(row) in FIELDS for row in rows) and len({cohort(row) for row in rows})==1:
            label=scope_label(rows)
            if label:figure['comparison_scope']=label
    for index,figure in enumerate(figures+remaining,1):figure['target']='draft-chart-'+str(index)
    return figures+remaining


def comparison_table(content):
    """A complete two-period matrix for one comparable financial source set."""
    facts=content.get('draft',{}).get('facts',[])
    if not facts or any(analysis_field(row) not in FIELDS for row in facts):return None
    owned={row['id'] for row in content.get('source_records',[]) if row.get('task_id')==content['task_id']
           and row.get('research_version')==content['research_version']}
    if any(row.get('artifact_id') not in owned for row in facts):return None
    if len({row['code'] for row in facts})!=1 or len({row['unit'] for row in facts})!=1:return None
    if len({row.get('series_basis') for row in facts})!=1:return None
    periods={row['period'] for row in facts}
    if len(periods)!=2 or any(_day(value) is None for value in periods):return None
    periods=sorted(periods,key=_day);grouped=defaultdict(lambda:defaultdict(list))
    for row in facts:grouped[analysis_field(row)][row['period']].append(row)
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
    groups=defaultdict(dict)
    for row in facts:
        day = _sequence_day(row['period'])
        series = groups[row['code'],row.get('series_basis','')]
        if day is None or day in series:
            return None
        series[day] = row
    if len(groups)<2:return None
    periods=None;columns=[]
    for series in groups.values():
        current=set(series)
        if periods is not None and current!=periods:return None
        periods=current
        columns.append(dict(label=next(iter(series.values()))['label'],values=series))
    return dict(unit=facts[0]['unit'],columns=[row['label'] for row in columns],
        rows=[dict(period=period.isoformat(),observations=[row['values'][period] for row in columns])
              for period in sorted(periods)],observations=len(facts))


def display_observation(row):
    """Round display-only percentage noise; retain exact inputs in workpapers."""
    if row['value'] is None:return '未返回'
    raw=row.get('raw_value',row['value'])
    if row.get('qualifier') == 'lower_exclusive':
        return '超过' + str(raw)
    if row.get('unit')=='%':
        value=Decimal(str(raw));places=2 if abs(value)>=Decimal('.01') or not value else min(6,max(2,1-value.adjusted()))
        shown=format(value,',.'+str(places)+'f')
        return shown if Decimal(shown.replace(',','')) or not value else format(value,'.3g')
    if isinstance(raw,str):return raw
    if type(raw) is int and abs(raw)>MAX_BROWSER_INTEGER:return format(raw, ',d')
    value=row['value'];return format(int(value) if type(value) is float and value.is_integer() else value,',')


def catalogue_figures(content, figures):
    """Select comparative views from aligned actual macro observations."""
    aligned=temporal_table(content)
    if not aligned or len(aligned['columns']) not in range(2,7) or len(aligned['rows'])<3:return figures
    rows=aligned['rows'];labels=[row['period'] for row in rows];names=aligned['columns']
    series_rows = list(zip(*(row['observations'] for row in rows), strict=True))
    values = [[row['value'] for row in series] for series in series_rows]
    point_refs = [[[row['id']] for row in series] for series in series_rows]
    refs=[observation['id'] for row in rows for observation in row['observations']]
    root=names[0].split(':')[0] if all(name.split(':')[0]==names[0].split(':')[0] for name in names) else '指标'
    selected=[dict(name=root+'走势对照',scheme='multi_line',kind='catalogue',field='value',unit=aligned['unit'],
        data=dict(labels=labels,series=[dict(name=name,values=value) for name,value in zip(names,values)],unit=aligned['unit']),
        data_refs=refs,series_point_refs=point_refs,horizontal=False,height=280)]
    if len(rows)>=4:
        selected.append(dict(name=root+'月度分化',scheme='heatmap',kind='catalogue',field='value',unit=aligned['unit'],
            data=dict(labels=labels,rows=names,matrix=values,unit=aligned['unit']),
            data_refs=refs,series_point_refs=point_refs,horizontal=False,height=260))
    covered=set(refs)
    for figure in figures:
        present=set(figure.get('data_refs',[]))
        if not present or not present.issubset(covered):selected.append(figure)
    for index,figure in enumerate(selected):figure['target']='draft-chart-'+str(index+1)
    return selected


OHLC_FIELDS = ('open', 'close', 'low', 'high')


def _quote_row_identity(fact):
    locator = fact.get('locator')
    if not isinstance(locator, dict) or locator.get('field') != fact['field']:
        return None
    table, row = locator.get('table'), locator.get('row')
    if type(table) is not int or type(row) is not int or min(table, row) < 0:
        return None
    return fact['artifact_id'], table, row


def ohlc_figures(content, figures):
    """Draw each candle from one observed row of a price response."""
    facts = content.get('draft', {}).get('facts', [])
    source_ids = {row['id'] for row in content.get('source_records', [])
                  if row.get('task_id') == content['task_id']
                  and row.get('research_version') == content['research_version']
                  and row.get('operation') == 'cmd_history_quotation'}
    groups = defaultdict(list)
    for row in facts:
        if (row.get('artifact_id') in source_ids and row.get('field') in OHLC_FIELDS
                and row.get('axis_role', 'time') == 'time' and not row.get('previous_close_for')):
            groups[(row['code'], row['unit'], row.get('series_basis'))].append(row)

    candles, covered = [], set()
    expected_fields = set(OHLC_FIELDS)
    for (code, unit, _), rows in groups.items():
        periods = defaultdict(list)
        for row in rows:
            periods[_sequence_day(row['period'])].append(row)
        if None in periods:
            continue
        labels, values, point_refs = [], [], []
        for day in sorted(periods):
            cells = periods[day]
            fields = {cell['field']: cell for cell in cells}
            if len(cells) != len(OHLC_FIELDS) or fields.keys() != expected_fields:
                break
            ordered = [fields[field] for field in OHLC_FIELDS]
            origins = {_quote_row_identity(cell) for cell in ordered}
            # A refresh can supersede one field while retaining the other
            # observations. The resulting values do not form a single candle.
            if None in origins or len(origins) != 1:
                break
            parts = [cell['value'] for cell in ordered]
            if any(type(value) not in (int, float) or not isfinite(value) for value in parts):
                break
            open_price, close, low, high = parts
            if not low <= min(open_price, close) <= max(open_price, close) <= high:
                break
            labels.append(day.isoformat())
            values.append(parts)
            point_refs.append([cell['id'] for cell in ordered])
        if len(values) != len(periods):
            continue
        refs = [ref for point in point_refs for ref in point]
        covered.update(refs)
        single_day = len(values) == 1
        name = code + ('单日开高低收' if single_day else '开高低收')
        candles.append(dict(name=name, scheme='candlestick', kind='catalogue', field='ohlc',
            unit=unit, data=dict(labels=labels, values=values, unit=unit), data_refs=refs,
            point_refs=point_refs, horizontal=False, height=132 if single_day else 280,
            keywords=['股价', '价格', '行情', '走势', '点位']))
    if not candles:
        return figures
    for spec in figures:
        refs = set(spec.get('data_refs', []))
        if not refs or not refs.issubset(covered):
            candles.append(spec)
    for index, spec in enumerate(candles, 1):
        spec['target'] = 'draft-chart-' + str(index)
    return candles

def planned_figures(content, *, report_profile=None):
    """Single chart plan shared by HTML and static delivery."""
    from .drafts import draft_figures
    from .visual_selection import select_figures
    incomplete = set(content['draft'].get('incomplete_economic_series', ()))
    incomplete.update(content['draft'].get('disconnected_economic_series', ()))
    incomplete_quotes = {(row['code'], row['field']) for row in content['draft'].get('incomplete_quote_series', ())}
    incomplete_quotes.update((code, 'close') for code in content['draft'].get('disconnected_closes', ()))
    base = draft_figures(content['draft']['facts'], incomplete_economic_series=incomplete,
                         incomplete_quote_series=incomplete_quotes)
    # Incomplete market paths stay as dated readings. They do not supply a
    # cross-sectional ranking of the same instrument on different days.
    base = [spec for spec in base if not spec.get('incomplete_market_path')]
    figures=comparison_figures(content,base)
    figures=ohlc_figures(content,figures)
    if not incomplete:
        figures=catalogue_figures(content,figures)
    figures=select_figures(content,figures)
    if content['draft'].get('visual_layout_version') == VISUAL_LAYOUT_VERSION:
        figures = [spec for spec in figures if not (spec.get('axis_role') == 'cross_section'
                   and spec.get('unit') in UNKNOWN_FINANCIAL_UNITS and _financial_figure(content, spec))]
    if report_profile!='standard':return figures
    from copy import deepcopy
    result=[]
    for original in figures:
        # Unknown currency scales belong to the workpaper; relative comparisons
        # can still be useful with their explicitly retained reading condition.
        if original.get('unit') in ('原始单位待核对','接口原值','原值') and _financial_figure(content,original):continue
        spec=deepcopy(original)
        if spec.get('unit') in ('原始单位待核对','接口原值','原值'):
            spec['unit']=''
            if spec.get('auto_visual',{}).get('data') is not None:spec['auto_visual']['data']['unit']=''
        result.append(spec)
    return result



def _comparison_name(spec, calculations, title):
    code=calculations[0]['code'] if calculations else ''
    return (code+' ' if code and spec.get('name','').startswith(code+' ') else '')+title


def _named_financial_changes(spec, calculations, *, report_profile):
    """Label comparable published changes in their calculated percent units."""
    fields={analysis_field(row['rows'][0]) for row in calculations}
    profit=fields & {'ths_np_stock','ths_np_atoopc_stock'}
    cash='ths_ncf_from_oa_stock' in fields
    if cash and profit:title='现金流与盈利变化'
    elif cash and 'ths_revenue_stock' in fields:title='收入与现金流变化'
    elif profit and 'ths_revenue_stock' in fields:title='收入与利润变化'
    elif profit and 'ths_operating_cost_stock' in fields:title='成本与利润变化'
    elif len(profit)==2:title='净利润与归母净利润变化'
    elif {'ths_revenue_stock','ths_operating_cost_stock'}<=fields:title='收入与成本变化'
    else:title='营运资金余额变化' if fields<=BALANCE else '经营指标变化'
    name=_comparison_name(spec,calculations,title)
    chart=spec['auto_visual']
    digits = 2
    if report_profile == 'standard' and all(abs(value) <= MAX_BROWSER_INTEGER for value in spec['values']):
        rates = {Decimal(str(value)) for value in spec['values']}
        tenth = Decimal('0.1')
        rounded = {value.quantize(tenth, rounding=ROUND_HALF_UP) for value in rates}
        if len(rounded) == len(rates) and not any(0 < abs(value) < tenth for value in rates):
            digits = 1
    return dict(spec, name=name, auto_visual=dict(chart, data=dict(chart['data'], name=name, numberDigits=digits)))



def _legacy_cash_comparison(spec, calculations):
    """Retain the selected rate format for reports without argument scope."""
    fields = [analysis_field(row['rows'][0]) for row in calculations]
    cash = 'ths_ncf_from_oa_stock'
    if cash not in fields:
        return spec
    before, after = (_day(spec.get(key, '')) for key in ('comparison', 'period'))
    if not before or not after:
        return spec
    annual = all((day.month, day.day) == (12, 31) for day in (before, after))
    name = ('现金流与盈利变化' if {'ths_np_stock', 'ths_np_atoopc_stock'}.intersection(fields) else
            '收入与现金流变化' if 'ths_revenue_stock' in fields else '经营现金流变化')
    context = f'{after.year}年同比' if annual else f'{after.isoformat()}较{before.isoformat()}'
    data = dict(labels=spec['dates'], values=spec['values'], unit='%',
                name='年度同比' if annual else '原值同比', focusIndex=fields.index(cash))
    visual = dict(spec['auto_visual'], scheme='lollipop', data=data, reason='cash_flow_argument_comparison')
    return dict(spec, context=context, name=_comparison_name(spec, calculations, name), auto_visual=visual)


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
            and any(word in topic for word in aliases.get(analysis_field(row),(row.get('label',''),)) if word)}



def _mechanism_figures(logic,index,facts,calculations=None):
    """Preserve every frozen step; small connected panels keep the path printable."""
    steps=(logic.get('mechanism') or {}).get('steps',[])
    if len(steps)<2 or any(not isinstance(step,dict) or not isinstance(step.get('label'),str)
                          or not step['label'].strip() for step in steps):return []
    # A panel has at most four edges, never a limit on the research itself.
    # Balance long paths and repeat only their shared handoff node.
    count=ceil((len(steps)-1)/4)
    edges,remainder=divmod(len(steps)-1,count)
    # A pathway can quote a registered change as well as an observation.
    # Preserve its parent argument's computation dependencies in the figure
    # inspector; do not pretend the step labels are a new numeric calculation.
    calculations = calculations or {}
    selected = [calculations[ref] for ref in dict.fromkeys(logic.get('calculation_refs', []))
                if ref in calculations]
    calculation_refs = [row['id'] for row in selected]
    calculation_inputs = [ref for row in selected for ref in row['data_refs'] if ref in facts]
    calculation_sources = list(dict.fromkeys(ref for row in selected
        for ref in row.get('session_basis', {}).get('source_refs', [])))
    result=[];start=0
    for segment in range(count):
        edge_count=edges+(1 if segment<remainder else 0)
        part=steps[start:start+edge_count+1]
        refs=list(dict.fromkeys([ref for step in part for ref in step.get('data_refs',[]) if ref in facts] + calculation_inputs))
        nodes=[dict(id='step-'+str(start+i),name=step['label'],level=i,kind=step.get('kind','hypothesis'))
               for i,step in enumerate(part)]
        links=[dict(source=nodes[i]['id'],target=nodes[i+1]['id']) for i in range(len(nodes)-1)]
        rows=1 if len(part)<=3 else 2
        node_height=max(54,ceil(max(len(step['label']) for step in part)/4)*15+16)
        minimum_height=rows*node_height+(rows-1)*20+24
        name='传导路径示意' if count==1 else '传导路径示意'+str(segment+1)
        result.append(dict(name=name,context='传导假设，非数值预测',field='mechanism',unit='',horizontal=False,type='graph',
            paper_logic=f'logic-{index}',coverage_kind='mechanism',min_height=minimum_height,
            data_refs=refs,calculation_refs=calculation_refs,source_refs=calculation_sources,
            workpaper_notes=[logic.get('analysis',''),logic.get('implication','')],risk=logic.get('risk',''),
            auto_visual=dict(version=1,scheme='evidence',data=dict(nodes=nodes,links=links,qualitative=True),reason='frozen_qualitative_mechanism')))
        start+=edge_count
    return result


def _frozen_observations(content, records):
    """Replay the report's source reader without transport or a new research run."""
    from .draft_sources import observations
    task=dict(id=content['task_id'],version=content['research_version'],cutoff=content.get('cutoff'),input_hash=content.get('input_hash'),
        question=content.get('question',''),objects=content.get('objects',[]),source_records=records,materials=[])
    for key in ('draft_source_reader_version', 'draft_quote_scope_version', 'draft_tinyshare_reader_version',
                'draft_historical_daily_version', 'research_mode'):
        if key in content:
            task[key] = content[key]
    try:
        return observations(task)
    except (ContractError,TypeError,ValueError,KeyError):
        return []


def _observed_series_rows(content):
    """Read original time-series cells without adopting research evidence."""
    records = [row for row in content.get('source_records',[]) if row.get('kind')=='api'
               and row.get('operation') in ('cmd_history_quotation','date_sequence','edb_service','search_edb','daily','fund_daily')]
    return {_market_row_key(row) for row in _frozen_observations(content, records)}


def _financial_row_key(row):
    # The row number is local to a reader run. The literal cell and its
    # reporting basis and unit must still match the saved source exactly.
    return fingerprint({key: value for key, value in row.items() if key != 'id'})


def _observed_financial_rows(content):
    return {_financial_row_key(row) for row in _frozen_observations(content, content.get('source_records', []))
            if analysis_field(row) in FINANCIAL}


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
    draft = content['draft']
    if draft.get('incomplete_economic_series') or draft.get('disconnected_economic_series') or temporal_table(content) is None:
        return []
    from .drafts import draft_figures
    from .visual_selection import select_figures
    figures=select_figures(content,draft_figures(content['draft']['facts']))
    for index,spec in enumerate(figures,1):
        spec.update(target='argument-series-'+str(index),argument_only=True)
    return figures


FILING_CAPTION_VERSION = 1
FILING_METRIC_LABELS = {**FIELDS, 'ths_ncf_from_oa_stock': '经营现金流净额'}


def _filing_amount_figures(content, refs, facts, locations):
    from .visual_selection import select_figures

    current_captions = content['draft'].get('filing_caption_version') == FILING_CAPTION_VERSION
    groups = defaultdict(list)
    for row in facts.values():
        location = locations.get(row['id'])
        if row['id'] in refs and location and type(row['value']) in (int, float) and isfinite(row['value']):
            key = (row['artifact_id'], row['code'], row['period'], row['unit'], location['statement'])
            groups[key].append(row)
    for (artifact, code, period, unit, statement), rows in groups.items():
        if not 2 <= len(rows) <= 12 or len({row['field'] for row in rows}) != len(rows):
            continue
        rows.sort(key=lambda row: Decimal(row['raw_value']), reverse=True)
        factor = Decimal('0.00000001') if unit == '人民币元' and max(abs(row['value']) for row in rows) >= 1e8 else Decimal(1)
        amounts = [float(Decimal(row['raw_value'])*factor) for row in rows]
        if len(set(amounts)) < len({Decimal(row['raw_value']) for row in rows}):
            continue
        segment = statement == '集团分部'
        if segment:
            title = '分部营业收入'
            labels = [row['label'].removesuffix('收入') for row in rows]
        elif current_captions:
            labels = [FILING_METRIC_LABELS.get(row['field'], row['label']) for row in rows]
            fields = {row['field'] for row in rows}
            ordered = [label for field, label in FILING_METRIC_LABELS.items() if field in fields]
            if len(ordered) == len(fields) and len(ordered) <= 3:
                title = '合并' + '、'.join(ordered[:-1]) + '与' + ordered[-1]
            else:
                title = '合并报表主要财务指标'
        else:
            title = '合并营业收入与归母净利润'
            labels = [FIELDS.get(row['field'], row['label']) for row in rows]
        refs = [row['id'] for row in rows]
        plot_unit = '亿元' if factor != 1 else unit
        spec = dict(name=title, field='filing_amount', unit=plot_unit, context=code+'，'+period,
            dates=labels, values=amounts, data_refs=refs, source_refs=[artifact],
            height=74+31*len(rows), type='bar', horizontal=True)
        selected, = select_figures(content, [spec])
        visual = selected['auto_visual']
        visual['data']['numberDigits'] = 2
        visual['ledger'] = dict(unit=unit, columns=['指标', '披露原值（'+unit+'）'],
            rows=[dict(label=label, cells=[row['raw_value']], data_refs=[row['id']])
                  for label, row in zip(labels, rows, strict=True)])
        yield selected


VISUAL_LAYOUT_VERSION = 2
ARGUMENT_VISUAL_SCOPE_VERSION = 1


def financial_peer_tables(content, rows):
    """Project cited two-period financial cells without treating units as shared."""
    if content.get('draft', {}).get('visual_layout_version') != VISUAL_LAYOUT_VERSION:
        return []
    from .publication_content import requested_financial_statement

    cohorts = defaultdict(list)
    for row in rows:
        field = analysis_field(row)
        period = _day(row.get('period'))
        basis = row.get('series_basis')
        if field not in FIELDS or not row.get('code') or period is None or not isinstance(basis, str) or not basis:
            continue
        statement = requested_financial_statement(content, [row])
        if statement:
            cohorts[(statement, basis)].append((field, period, row))

    tables = []
    for (statement, _), items in cohorts.items():
        codes = list(dict.fromkeys(row['code'] for _, _, row in items))
        periods = sorted({period for _, period, _ in items})
        if len(codes) not in (2, 3) or len(periods) != 2:
            continue
        cells = defaultdict(list)
        for field, period, row in items:
            cells[field, row['code'], period].append(row)
        columns = [(code, period) for code in codes for period in periods]
        table_rows = {}
        for field in FIELDS:
            groups = [cells.get((field, code, period), ()) for code, period in columns]
            if any(len(group) > 1 for group in groups) or sum(bool(group) for group in groups) < 2:
                continue
            if {group[0]['code'] for group in groups if group} != set(codes):
                continue
            table_rows[field] = [group[0] if group else None for group in groups]
        if len(table_rows) >= 3:
            tables.append(dict(statement=statement, codes=codes, periods=periods, columns=columns, rows=table_rows,
                data_refs={row['id'] for group in table_rows.values() for row in group if row is not None}))
    return tables


def _peer_change_figures(tables, calculations):
    """Use registered changes at a common pair of reporting dates."""
    for table in tables:
        rates = defaultdict(list)
        codes, periods = table['codes'], table['periods']
        for calc in calculations.values():
            if calc['method'] != 'raw_change_v1' or not set(calc['data_refs']) <= table['data_refs']:
                continue
            before, after = calc['rows']
            if [_day(row['period']) for row in (before, after)] == periods:
                rates[analysis_field(before), calc['code']].append(calc)

        for balance, title in ((False, '经营指标同比'), (True, '营运资金余额同比')):
            fields = [field for field in table['rows'] if (field in BALANCE) == balance
                      and any(rates.get((field, code)) for code in codes)
                      and all(len(rates.get((field, code), ())) <= 1 for code in codes)]
            if len(fields) < 2 or not any(sum(bool(rates.get((field, code))) for code in codes) >= 2 for field in fields):
                continue
            series = [[next(iter(rates.get((field, code), ())), None) for field in fields] for code in codes]
            used = [calc for group in series for calc in group if calc is not None]
            if len({bool(calc.get('conditional_equal_scale')) for calc in used}) != 1:
                continue
            if sum(any(calc is not None for calc in group) for group in series) != len(codes):
                continue
            data_refs = list(dict.fromkeys(ref for calc in used for ref in calc['data_refs']))
            conditional = bool(used[0].get('conditional_equal_scale'))
            labels = [FIELDS[field] for field in fields]
            context = periods[1].isoformat()+'较'+periods[0].isoformat()
            if conditional:
                context += '，以同一指标两期原值单位一致为前提'
            if any(not rates.get((field, code)) for field in table['rows'] for code in codes):
                context += '。空档表示缺少可比读数'
            yield dict(kind='peer_financial_changes', field='financial_comparison', name=title, unit='%',
                horizontal=False, type='bar', min_height=172, comparison_scope=table['statement'], context=context,
                comparison_family='balance' if balance else 'operating',
                data_refs=data_refs, calculation_refs=[calc['id'] for calc in used],
                series_point_refs=[[calc['data_refs'] if calc is not None else [] for calc in group] for group in series],
                scheme='grouped_bar', data=dict(labels=labels, unit='%', categoricalComparison=True,
                    series=[dict(name=code, values=[calc['value'] if calc is not None else None for calc in group])
                            for code, group in zip(codes, series)]))


def _repeated_endpoint_views(previous, candidates, facts, sources, argument_refs):
    """Retain the first full paths; compare repeated endpoints in one view.

    Only literal endpoint arguments qualify. A middle-point citation, different
    range, unknown unit, or different source keeps its complete original view.
    """
    from .evidence_visuals import _economic_basis

    def series(spec):
        dates, values, refs = (spec.get(key, []) for key in ('dates', 'values', 'data_refs'))
        if (spec.get('field') != 'value' or spec.get('auto_visual',{}).get('scheme')!='line' or len(dates)<3
                or len(refs) != len(dates) or any(ref not in facts for ref in refs)
                or len(values)!=len(dates)
                or spec.get('unit') in ('',None,'原始单位待核对')):
            return None
        origins = {facts[ref]['artifact_id'] for ref in refs}
        basis = _economic_basis(facts[refs[0]])
        if len(origins) != 1 or not basis:
            return None
        origin, = origins
        if sources.get(origin, {}).get('operation') != 'edb_service':
            return None
        days=[_day(value) for value in dates]
        if None in days or days!=sorted(set(days)):
            return None
        for offset in (0,len(refs)-1):
            row=facts[refs[offset]];value=values[offset]
            if (type(value) not in (int,float) or not isfinite(value) or row.get('value') is None
                    or row.get('unit')!=spec['unit'] or _day(row.get('period'))!=days[offset]):
                return None
            try:
                if Decimal(str(row.get('raw_value',row['value'])))!=Decimal(str(value)):return None
            except (InvalidOperation,ValueError,TypeError):return None
        identity = fingerprint({key: spec.get(key) for key in ('name', 'unit', 'dates', 'values', 'data_refs')})
        return identity, (origin, basis, spec['unit'], days[0].isoformat(), days[-1].isoformat()), [refs[0], refs[-1]]

    seen = {}
    for spec in previous:
        info = series(spec)
        if info:
            seen.setdefault(info[0], spec['target'])
    groups = defaultdict(dict)
    for index, spec in enumerate(candidates):
        info = series(spec)
        if info and info[0] in seen and argument_refs.intersection(spec['data_refs']) == set(info[2]):
            groups[info[1]].setdefault(info[0], (index, info[2]))

    from .visual_selection import paired_columns
    replacements, removed = {}, set()
    for (_, _, unit, before, after), items in groups.items():
        if len(items) < 3:
            continue
        labels = [candidates[index]['name'] for index, _ in items.values()]
        if len(set(labels)) != len(labels):
            continue
        point_refs = [endpoints for _, endpoints in items.values()]
        pairs = [[facts[ref]['value'] for ref in endpoints] for endpoints in point_refs]
        refs = [ref for endpoints in point_refs for ref in endpoints]
        ledger = dict(columns=['指标', before, after], rows=[
            dict(label=label, cells=[display_observation(facts[ref]) for ref in endpoints], values=list(pair))
            for label, endpoints, pair in zip(labels, point_refs, pairs, strict=True)], unit=unit)
        first = next(iter(items.values()))[0]
        replacements[first] = dict(name='区间首末读数', kind='paired', field='value', unit=unit,
            dates=labels, pairs=pairs, periods=[before, after], context=before+'与'+after,
            type='bar', horizontal=False, height=200,
            data_refs=refs, point_refs=point_refs, calculation_refs=[],
            reused_time_context=[
                dict(source_figure=seen[identity], full_data_refs=list(candidates[index]['data_refs']), endpoint_refs=endpoints)
                for identity, (index, endpoints) in items.items()],
            auto_visual=dict(version=1, scheme='grouped_bar', data=paired_columns(dict(labels=labels, pairs=pairs,
                periods=[before, after], unit=unit, name='区间首末读数')),
                source_refs=refs, ledger=ledger, reason='repeated_series_endpoint_comparison'))
        removed.update(index for index, _ in items.values() if index != first)
    return [replacements.get(index, spec) for index, spec in enumerate(candidates) if index not in removed]


def _figure_key(spec):
    choice = spec.get('auto_visual')
    if not (spec.get('data_refs') and choice and spec.get('coverage_kind', 'numeric') == 'numeric'):
        return None
    display_fields = {'name', 'dateLabels', 'compactDates', 'numberDigits', 'keyExtrema'}
    return fingerprint(dict(scheme=choice['scheme'], field=spec.get('field'),
        observations=spec['data_refs'], calculations=spec.get('calculation_refs', []),
        scope=spec.get('comparison_scope'), scale=spec.get('plot_scale'),
        data={k: v for k, v in choice['data'].items() if k not in display_fields}))


def _reference_figure(earlier, spec, logic):
    reused = earlier.setdefault('reused_by', [])
    if logic not in reused:
        reused.append(logic)
    # The same drawing can serve two arguments with different citation
    # anchors. Its raw data agree, but each argument keeps its own context.
    context = spec.get('plot_context', {}).get('context_data_refs', [])
    earlier.setdefault('reused_context_data_refs', {})[logic] = list(context)
    data = spec['auto_visual']['data']
    if data.get('keyExtrema'):
        first = earlier['auto_visual']['data']
        first['keyExtrema'] = sorted(set(first.get('keyExtrema', []) + data['keyExtrema']))


def _share_identical_figures(specs):
    """Keep one drawing for the same frozen view and mark later references."""
    unique, views = [], {}
    for spec in specs:
        key = _figure_key(spec) if spec.get('paper_logic') else None
        if key is None:
            unique.append(spec)
            continue
        earlier = views.get(key)
        if earlier is None:
            unique.append(spec)
            views[key] = spec
        elif earlier['paper_logic'] != spec['paper_logic']:
            _reference_figure(earlier, spec, spec['paper_logic'])
    return unique



def _omit_redundant_valuation_line(specs, code, facts):
    """Compare normalized paths only when both charts cite the same sessions and stock."""
    from .market_series import PE_TTM_INDICATOR

    lines = [spec for spec in specs if spec.get('type') == 'line'
             and spec.get('field') in ('close', PE_TTM_INDICATOR)
             and {facts.get(ref, {}).get('code') for ref in spec.get('data_refs', ())} == {code}]
    if len(lines) != 2 or {spec['field'] for spec in lines} != {'close', PE_TTM_INDICATOR}:
        return specs
    price, multiple = sorted(lines, key=lambda spec: spec['field'] != 'close')
    if price['dates'] != multiple['dates'] or len(price['dates']) < 3:
        return specs
    left, right = price['values'], multiple['values']
    if (len(left) != len(right) or
            any(type(value) not in (int, float) or not isfinite(value) or value <= 0 for value in (*left, *right))):
        return specs
    # A path difference smaller than 0.05 percentage points rounds away at
    # report precision. Retain the redundant series in the linked workpaper.
    if all(abs(a/left[0]-b/right[0]) < .0005 for a, b in zip(left, right, strict=True)):
        price['basis_only_refs'] = list(multiple['data_refs'])
        return [spec for spec in specs if spec is not multiple]
    return specs


def logic_figure_plan(content, *, report_profile='standard'):
    """Assign a fresh, data-bound figure to every compatible argument.

    Each view stays inside its argument's references. An identical view can
    serve multiple arguments without repeating its drawing.
    """
    from copy import deepcopy
    from .draft_analysis import VALUATION_ATTRIBUTION, VALUATION_POSITION
    from .drafts import draft_figures, logic_basis
    from .evidence_visuals import (align_argument_paths, announcement_timeline, cash_bridge_figure, complement_argument,
                                   bank_profit_figure, etf_turnover_figure, financial_input_figure,
                                   industry_chain_figure, macro_monthly_figure, peer_growth_figure, stock_growth_figure,
                                   peer_operating_figure, peer_revenue_figure, retail_change_figure, same_period_figure,
                                   segment_revenue_figure, stock_profit_figure, weekly_market_figures)
    from .visual_catalog import evidence_design_enabled
    from .visual_selection import argument_charts, report_chart_supported, select_figures
    base=planned_figures(content,report_profile=report_profile)
    refine = evidence_design_enabled(content)
    layout_version = content.get('draft', {}).get('visual_layout_version')
    scope_policy=content.get('draft',{}).get('argument_visual_scope_version')
    scoped=type(scope_policy) is int and scope_policy==ARGUMENT_VISUAL_SCOPE_VERSION
    observed_sections = content.get('draft', {}).get('observed_argument_sections') is True
    argument_series=_argument_series(content)
    calculations={row['id']:row for row in _validated(content)}
    original_rows=_observed_series_rows(content)
    dependencies=_calculation_dependencies(content, original_rows)
    owned={row['id']: row for row in content.get('source_records',[]) if
           row.get('task_id')==content['task_id'] and row.get('research_version')==content['research_version']}
    facts={row['id']:row for row in content.get('draft',{}).get('facts',[]) if row.get('artifact_id') in owned}
    if content.get('monthly_retail_scope'):
        from .monthly_statistics import CELL_KIND as MONTHLY_CELL, frozen_retail_refs

        verified = frozen_retail_refs(content, facts.values())
        facts = {ref: row for ref, row in facts.items()
                 if row.get('locator', {}).get('kind') != MONTHLY_CELL or ref in verified}
    from .draft_filing_amounts import verified_filing_locations
    from .draft_sources import _draft_original_scope
    filing_locations = verified_filing_locations(content, facts.values(), _draft_original_scope(content))
    result=[]
    earlier_paths = {}
    for index,logic in enumerate(content.get('draft',{}).get('logics',[]),1):
        summary_only = logic.get('peer_summary_only')
        if (logic.get('event_session_summary') or logic.get('event_observation_only')
                or (summary_only and not logic.get('peer_operating_series'))):
            continue
        submitted_refs = draft_logic_data_refs(logic) or logic.get('calculation_refs', ())
        excluded = set(logic.get('off_scope_data_refs', ()))
        logic_facts = {ref: row for ref, row in facts.items() if ref not in excluded} if excluded else facts
        logic = draft_logic_reading(logic, dependencies.values())
        refs={ref for ref in draft_logic_data_refs(logic) if ref in logic_facts}
        refs.update(ref for ident in logic.get('calculation_refs',[]) if ident in dependencies
                    for ref in dependencies[ident]['data_refs'] if ref in logic_facts)
        reference_origin='explicit'
        if not refs and not logic.get('etf_interim_readout') and (not excluded or not submitted_refs):
            refs=_topic_references(logic,logic_facts);reference_origin='literal_topic_match'
        candidates=[]
        # Rank the argument's own cited cross-section, before applying the
        # display cap. A global top list can omit every cited constituent.
        tables = financial_peer_tables(content, logic_basis(content, logic)[0]) if layout_version == VISUAL_LAYOUT_VERSION else []
        table_refs = {ref for table in tables for ref in table['data_refs']}
        explicit_calcs = set(logic.get('calculation_refs', ()))
        allowed_calcs = explicit_calcs if observed_sections else calculations.keys()
        cited_rows = {ref: logic_facts[ref] for ref in refs}
        cited_calculations = [row for ident, row in dependencies.items() if ident in explicit_calcs]
        shared = next((spec for spec in result if spec.get('kind') == 'peer_growth'
                       and explicit_calcs == set(spec['calculation_refs'])), None)
        if shared:
            _reference_figure(shared, shared, f'logic-{index}')
            continue
        valuation = next((calc for calc in cited_calculations if calc.get('comparison_kind') == VALUATION_POSITION
                          and set(calc['data_refs']) <= cited_rows.keys()), None)
        valuation_figure = None
        if valuation:
            valuation_figure, = draft_figures(valuation['rows'])
            valuation_figure.update(name='滚动市盈率月度读数', calculation_refs=[valuation['id']])
            if valuation_figure['unit'] == '接口原值':
                valuation_figure['unit'] = ''
        growth_figure = stock_growth_figure(cited_calculations, cited_rows) if logic.get('stock_growth_chart') else None
        profit_figure = (stock_profit_figure(content, cited_calculations, cited_rows)
                         if logic.get('stock_profit_chart') else None)
        comparison = (profit_figure or growth_figure or valuation_figure
                      or macro_monthly_figure(logic.get('macro_monthly_chart'), cited_rows)
                      or (retail_change_figure(cited_calculations, cited_rows) if logic.get('macro_retail_bridge') else None)
                      or industry_chain_figure(logic.get('industry_chain_chart'), cited_calculations, cited_rows)
                      or bank_profit_figure(logic.get('bank_profit_series'), cited_calculations, cited_rows)
                      or peer_revenue_figure(logic.get('peer_revenue_series'), cited_calculations, cited_rows)
                      or peer_operating_figure(logic.get('peer_operating_series'), cited_calculations, cited_rows)
                      or peer_growth_figure(logic.get('peer_growth_series'), cited_calculations, cited_rows)
                      or etf_turnover_figure(cited_calculations))
        if comparison:
            selected, = select_figures(content, [comparison])
            if comparison is valuation_figure:
                selected.get('auto_visual', {}).get('data', {}).update(compactDates=True, numberDigits=1)
            if report_chart_supported(selected):
                target = f'logic-{index}-chart-1'
                selected.update(target=target, paper_logic=f'logic-{index}', coverage_kind='numeric',
                                argument_data_refs=sorted(refs), reference_origin=reference_origin,
                                inspection_target='draft-figure-basis-'+target)
                result.append(selected)
                continue
        if summary_only or logic.get('macro_demand_readout') or logic.get('stock_quality_readout') or logic.get('lpr_readout') or any(row.get('locator', {}).get('kind') == INDUSTRY_CELL_KIND
                               for row in cited_rows.values()):
            continue
        bridges = [figure for render_bridge in (cash_bridge_figure, segment_revenue_figure)
                   if (figure := render_bridge(cited_calculations, cited_rows))]
        candidates.extend(select_figures(content, bridges))

        def table_represents(spec):
            plotted = set(spec.get('data_refs', ()))
            return bool(plotted and plotted <= table_refs and (
                not spec.get('kind') or (spec.get('kind') == 'paired'
                    and not explicit_calcs.intersection(spec.get('calculation_refs', ())))))

        cross=select_figures(content,draft_figures([row for row in facts.values()
            if row['id'] in refs and row.get('axis_role')=='cross_section' and row['id'] not in table_refs
            and not (layout_version == VISUAL_LAYOUT_VERSION and row.get('unit') in UNKNOWN_FINANCIAL_UNITS
                     and _financial_observation(row, owned[row['artifact_id']]))]))
        for number,spec in enumerate(cross,1):spec['target']=f'argument-cross-{index}-{number}'
        originals=[spec for spec in base if spec.get('axis_role')!='cross_section']+argument_series+cross
        for original in originals:
            if tables and table_represents(original):
                continue
            if original.get('argument_only') and any(spec.get('kind')=='catalogue' and spec.get('field')=='value' for spec in candidates):continue
            spec=deepcopy(original)
            window=_observed_window(content,spec,refs,logic_facts,original_rows)
            plot_refs=refs|set(window['context_data_refs']) if window else refs
            if window:spec['plot_context']=window
            if spec.get('kind')=='changes':
                selected=[calculations[ident] for ident in spec.get('calculation_refs',[]) if ident in calculations and ident in allowed_calcs
                          and set(calculations[ident]['data_refs'])<=refs]
                if not selected:continue
                spec.update(dates=[FIELDS[analysis_field(row['rows'][0])] for row in selected],
                    values=[row['value'] for row in selected],calculation_refs=[row['id'] for row in selected],
                    data_refs=list(dict.fromkeys(ref for row in selected for ref in row['data_refs'])))
                spec.pop('auto_visual',None);spec=select_figures(content,[spec])[0]
                if not scoped and any(word in logic.get('title','') for word in ('现金','回款','资金')):
                    spec=_legacy_cash_comparison(spec,selected)
                else:
                    spec=_named_financial_changes(spec, selected, report_profile=report_profile)
            elif scoped and spec.get('kind')=='paired':
                ids=spec.get('calculation_refs',[])
                pairs=[offset for offset in range(len(spec.get('pairs',[])))
                    if len(ids[offset*2:offset*2+2])==2 and all(ident in calculations and
                        ident in allowed_calcs and
                        set(calculations[ident]['data_refs'])<=refs for ident in ids[offset*2:offset*2+2])]
                if not pairs:continue
                spec.update(dates=[spec['dates'][offset] for offset in pairs],
                    pairs=[spec['pairs'][offset] for offset in pairs],
                    calculation_refs=[ident for offset in pairs for ident in ids[offset*2:offset*2+2]])
                spec['data_refs']=list(dict.fromkeys(ref for ident in spec['calculation_refs'] for ref in calculations[ident]['data_refs']))
                if len(pairs)==1:
                    code=calculations[spec['calculation_refs'][0]]['code']
                    prefix=code+' ' if len({row['code'] for row in calculations.values()})>1 else ''
                    spec['name']=prefix+spec['dates'][0]
                spec.pop('auto_visual',None);spec=select_figures(content,[spec])[0]
            elif observed_sections and not explicit_calcs.issuperset(spec.get('calculation_refs', ())):
                continue
            elif not spec.get('data_refs') or not set(spec['data_refs'])<=plot_refs:
                selected=[offset for offset,ref in enumerate(spec.get('data_refs',[])) if ref in plot_refs]
                if spec.get('kind') or len(selected)<2 or len(spec.get('dates',[]))!=len(spec.get('data_refs',[])):continue
                spec.update(dates=[spec['dates'][offset] for offset in selected],
                            values=[spec['values'][offset] for offset in selected],
                            data_refs=[spec['data_refs'][offset] for offset in selected])
                spec.pop('auto_visual',None);spec=select_figures(content,[spec])[0]
            candidates.append(spec)
        if tables:
            peer_figures = select_figures(content, list(_peer_change_figures(tables, calculations)))
            combined = {ident for spec in peer_figures for ident in spec['calculation_refs']}
            candidates = peer_figures + [spec for spec in candidates if not (
                spec.get('kind') in ('changes', 'indexed_comparison') and set(spec['calculation_refs']) <= combined)]
            if not candidates:
                # When no registered comparable change can be drawn, keep one
                # same-unit original cross-section per table if one exists.
                for table in tables:
                    known = [spec for spec in base if not spec.get('kind')
                             and spec.get('axis_role') == 'cross_section'
                             and spec.get('unit') not in UNKNOWN_FINANCIAL_UNITS
                             and set(spec.get('data_refs', ())) <= table['data_refs']]
                    if known:
                        candidates.append(deepcopy(max(known, key=lambda spec: (
                            _day(spec.get('context')) or date.min, len(spec['data_refs'])))))
        cited_facts = {ref: row for ref, row in logic_facts.items() if ref in refs}
        candidates = [spec for spec in candidates if report_chart_supported(spec)]
        if type(layout_version) is int and layout_version in (1, VISUAL_LAYOUT_VERSION):
            candidates = _repeated_endpoint_views(result, candidates, logic_facts, owned, refs)
        # A shared-axis comparison still has a purpose when an argument
        # references the interior of a path or introduces a new path.
        # Endpoint-only repeats keep links to their first full drawings.
        if any(spec.get('field') == 'value' and spec.get('type') == 'line' and (
                _figure_key(spec) not in earlier_paths or refs.intersection(spec['data_refs'][1:-1]))
               for spec in candidates):
            candidates = align_argument_paths(content, candidates, logic_facts, owned, original_rows, _market_row_key)
        remaining, shared_refs = [], set()
        for spec in candidates:
            earlier = earlier_paths.get(_figure_key(spec)) if spec.get('field') == 'value' else None
            if earlier is None:
                remaining.append(spec)
            else:
                shared_refs.update(spec['data_refs'])
                _reference_figure(earlier, spec, f'logic-{index}')
        candidates = remaining
        if refine:
            adopted_refs = dict.fromkeys(logic.get('calculation_refs', []))
            if report_profile == 'standard' and logic.get('economic_readout') is True:
                # Auxiliary windows remain in the detailed basis. The client
                # chart should only add a view of a comparison made in prose.
                named = logic.get('quantity_links', {}).get('analysis', {}).get('links', ())
                adopted_refs = dict.fromkeys(ref for link in named for ref in link['calculation_refs'] if ref in adopted_refs)
            adopted = [dependencies[ident] for ident in adopted_refs
                       if ident in dependencies and set(dependencies[ident]['data_refs']) <= cited_facts.keys()]
            candidates = complement_argument(content, candidates, calculations=adopted, cited_facts=cited_facts,
                original_rows=original_rows, row_key=_market_row_key, profile=report_profile)
        if report_profile == 'standard' and (attribution := next((row for row in cited_calculations
                if row.get('comparison_kind') == VALUATION_ATTRIBUTION), None)):
            candidates = _omit_redundant_valuation_line(candidates, attribution['code'], logic_facts)
        plotted_refs = {ref for spec in candidates for ref in spec.get('data_refs', [])}
        candidates.extend(_filing_amount_figures(content, refs - plotted_refs, logic_facts, filing_locations))
        candidates = [spec for spec in candidates if report_chart_supported(spec)]
        if not candidates:
            uncovered = {ref: row for ref, row in cited_facts.items() if ref not in shared_refs}
            comparison = same_period_figure(uncovered, owned, original_rows, _market_row_key)
            if comparison is None and not tables:
                comparison = financial_input_figure(uncovered, calculations.values(), owned)
            if comparison:
                candidates.extend(spec for spec in select_figures(content, [comparison]) if report_chart_supported(spec))
        candidates.extend(weekly_market_figures(content, logic))
        mechanisms = _mechanism_figures(logic,index,logic_facts,dependencies)
        if any(word in logic.get('title', '') for word in ('公告','事件','发布','生效','进展')):
            timeline = announcement_timeline(content, logic.get('source_refs', ()))
            if timeline:
                mechanisms.extend(select_figures(content, [timeline]))
        # A validated causal path or event chronology earns a slot before a
        # redundant rendering of the same numerical comparisons.
        candidates = argument_charts(candidates[:1] + mechanisms + candidates[1:], logic_facts)
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
                values=spec.get('values',[])
                measured=[value for value in values if type(value) in (int,float) and isfinite(value)]
                story=logic.get('title','')+' '+logic.get('analysis','')
                if measured and spec.get('point_refs') is None and len(values)==len(spec.get('data_refs', [])):
                    extrema=[]
                    for reading,words in ((max(measured),('峰值','最高','高点')),(min(measured),('最低','低点','触底'))):
                        offset=values.index(reading)
                        if any(word in story for word in words) and spec['data_refs'][offset] in refs:
                            extrema.append(offset)
                    if extrema:data['keyExtrema']=sorted(set(extrema))
                if aligned:spec.update(sync_group=f'logic-{index}-'+('series' if spec.get('field')=='value' else 'market'),axis_alignment='same_observed_dates')
            if spec.get('kind') in ('changes','indexed_comparison'):
                spec['point_refs']=[calculations[ident]['data_refs'] for ident in spec.get('calculation_refs',[]) if ident in calculations]
            elif spec.get('data_refs') and not spec.get('point_refs') and len(spec['data_refs'])==len(spec.get('dates',[])):
                spec['point_refs']=[[ref] for ref in spec['data_refs']]
            spec['inspection_target']='draft-figure-basis-'+spec['target']
            result.append(spec)
            if spec.get('field') == 'value' and spec.get('type') == 'line' and (key := _figure_key(spec)):
                earlier_paths.setdefault(key, spec)
    for original in base if not content['draft'].get('logics') else ():
        if set(original.get('data_refs',[]))<=facts.keys():
            target = 'supplement-' + original['target']
            result.append(dict(deepcopy(original),target=target,inspection_target='draft-figure-basis-'+target))
    result = [spec for spec in result if report_chart_supported(spec)]
    if report_profile == 'standard' and any(spec.get('kind') == 'margin_bridge' for spec in result):
        draft = content['draft']
        links = draft.get('quantity_links', {}).get('conclusion', {})
        if links.get('text_hash') == fingerprint(draft.get('conclusion')):
            summarized = {ref for link in links.get('links', []) for ref in link.get('calculation_refs', [])}
            result = [spec for spec in result if not (spec.get('kind') == 'changes'
                and len(spec.get('calculation_refs', [])) <= 3
                and len(summarized.intersection(spec.get('calculation_refs', []))) >= 2)]
    for spec in result:
        if spec.get('comparison_scope'):
            spec['name']+='，'+spec['comparison_scope']
    from .publication_content import reader_date_label
    for spec in result:
        if spec.get('axis_role')=='cross_section':continue
        choice=spec.get('auto_visual',{})
        data=choice.get('data')
        if not isinstance(data,dict):continue
        names=set()
        for field in ('labels','periods'):
            values=data.get(field)
            if isinstance(values,list):names.update(value for value in values if isinstance(value,str))
        for field in ('series','points'):
            values=data.get(field)
            if isinstance(values,list):names.update(row['name'] for row in values if isinstance(row,dict) and isinstance(row.get('name'),str))
        labels={}
        for ref in spec.get('data_refs',[]):
            row=facts.get(ref);period=row.get('period') if row else None
            if isinstance(period,str) and period in names and reader_date_label(period)!=period:
                labels[period]=reader_date_label(period)
        if labels:data['dateLabels']=labels
    from .publication_content import observation_unit
    for spec in result:
        rows=[facts[ref] for ref in spec.get('data_refs',[]) if ref in facts]
        data=spec.get('auto_visual',{}).get('data')
        if (rows and len(rows)==len(spec.get('data_refs',[])) and spec.get('unit')=='元'
                and {observation_unit(row) for row in rows}=={'人民币元'}
                and (not isinstance(data,dict) or data.get('unit','元')=='元')):
            spec['unit']='人民币元'
            if isinstance(data,dict):data['unit']='人民币元'
    return _share_identical_figures(result)
