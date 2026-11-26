"""Task-scoped reading of the Tinyshare adapters' retained row records.

No SDK, network or unit conversion. Source field names, raw cells and record
positions remain intact. Compatibility documentation does not grant permission,
financial approval or historical availability.
"""
from collections import Counter
from datetime import date
import re

from .contracts import ContractError, fingerprint, market_date, timepoint

POLICY = 'draft_tinyshare_reader_version'
VERSION = 1
LABELS = dict(open='开盘价',high='最高价',low='最低价',close='收盘价',pct_chg='披露涨跌幅',
    vol='成交量',amount='成交额',pe='市盈率',pe_ttm='PE(TTM)',pb='PB',ps='市销率',ps_ttm='滚动市销率',
    volume_ratio='量比',turnover_rate='换手率',turnover_rate_f='自由流通股换手率',
    dv_ratio='股息率',dv_ttm='滚动股息率',total_share='总股本',float_share='流通股本',
    free_share='自由流通股本',total_mv='总市值',circ_mv='流通市值',
    revenue='营业收入',oper_cost='营业成本',n_income='净利润',n_cashflow_act='经营现金流')
# Calculation keys only. Never rename a source column or compare providers on
# these keys alone; the series basis retains provider, endpoint and report type.
ANALYSIS_FIELDS = {
    'income':dict(revenue='ths_revenue_stock',oper_cost='ths_operating_cost_stock',n_income='ths_np_stock'),
    'cashflow':dict(n_cashflow_act='ths_ncf_from_oa_stock'),
    'daily_basic':dict(pe_ttm='ths_pe_ttm_stock',pb='ths_pb_mrq_stock'),
}


def enabled(task):
    version = task.get(POLICY)
    if version is None:
        return False
    if type(version) is not int or version != VERSION:
        raise ContractError('Tinyshare原值读取版本未登记')
    return True


def analysis_field(row):
    if row.get('source') == 'tinyshare':
        return ANALYSIS_FIELDS.get(row.get('source_operation'), {}).get(row.get('field'), row.get('field',''))
    return row.get('field','')


def _day(value):
    if not isinstance(value, str) or re.fullmatch(r'\d{8}', value) is None:
        return None
    try:
        timepoint(value[:4]+'-'+value[4:6]+'-'+value[6:])
        return value
    except ContractError:
        return None


def select_scope(task, record, number_reader, *, question_ranges=None, close_calendars=None):
    """Select actual requested cells; an unusable subset never erases raw data."""
    if record.get('source') != 'tinyshare' or not enabled(task):
        return None
    from .evidence import FIELD_REGISTRY, validate_tinyshare_request
    from .financial_sources import FINANCIAL_REQUEST_REGISTRY, FINANCIAL_RECORD_KEYS
    from .market_calendar import close_return_predecessors, market_date_ranges
    from .subject_scope import subject_key
    operation = record.get('operation')
    qualified = 'tinyshare.' + str(operation)
    financial = qualified in FINANCIAL_REQUEST_REGISTRY
    definition = (FINANCIAL_REQUEST_REGISTRY if financial else FIELD_REGISTRY).get(qualified)
    if definition is None:
        return None
    rows = record.get('snapshot', {}).get('data', [])
    rows = rows if isinstance(rows, list) else []
    result = dict(kind='tinyshare_records', records={}, financial=financial,
                  rejected_cells=0, fields=[], definition=definition)
    params = record.get('parameters', {})
    try:
        from .market_series import daily_market_as_of
        validate_tinyshare_request(task, operation, params)
        cutoff = (market_date(task['cutoff']) if financial else daily_market_as_of(task)).replace('-', '')
    except (ContractError, KeyError, TypeError, ValueError):
        result['rejected_cells'] = max(1, len(rows))
        return result
    fields = [field for field in params['fields'].split(',') if field in definition['fields']]
    result['fields'] = fields
    intervals = {field: market_date_ranges(question_ranges, params['ts_code'], field) for field in fields
                 if question_ranges and not financial}
    predecessors = (close_return_predecessors(task, record, close_calendars, question_ranges)
                    .get(subject_key(params['ts_code']).upper(), {})
                    if 'close' in fields and not financial else {})
    duplicates = Counter((row.get('ts_code'), row.get('trade_date')) for row in rows
        if not financial and isinstance(row, dict) and isinstance(row.get('ts_code'), str)
        and isinstance(row.get('trade_date'), str))
    for index, row in enumerate(rows):
        valid = isinstance(row, dict) and row.get('ts_code') == params['ts_code']
        if valid and financial:
            valid = (all(key in row for key in FINANCIAL_RECORD_KEYS)
                and row['end_date'] == params['period'] and row['report_type'] == params['report_type'])
            for field in ('ann_date', 'f_ann_date'):
                value = row.get(field)
                if value in (None, ''):
                    continue
                day = _day(value)
                if (day is None or day > cutoff
                        or field == 'ann_date' and not params['start_date'] <= day <= params['end_date']):
                    valid = False
        elif valid:
            day = _day(row.get('trade_date'))
            valid = (day is not None and params['start_date'] <= day <= params['end_date']
                and day <= cutoff and duplicates[(row['ts_code'], row['trade_date'])] == 1)
        if not valid:
            result['rejected_cells'] += max(1, len(fields))
            continue
        selected = set()
        for field in fields:
            windows = intervals.get(field)
            previous_close_for = None
            if windows is not None:
                observed = date.fromisoformat(day)
                if not any(first <= observed <= last for first, last in windows):
                    previous_close_for = predecessors.get(observed) if field == 'close' else None
                    if previous_close_for is None:
                        continue
            if field not in row:
                result['rejected_cells'] += 1
                continue
            raw = row[field]
            numeric = number_reader(raw) is not None
            if not numeric and raw not in (None, '', '-', '--', 'N/A', 'null'):
                result['rejected_cells'] += 1
                continue
            if previous_close_for is not None:
                if not numeric:
                    continue
                result.setdefault('previous_close_cells', {})[index] = previous_close_for
            selected.add(field)
        extra = set(row) - set(params['fields'].split(','))
        result['rejected_cells'] += len(extra)
        if selected:
            result['records'][index] = selected
    return result


def selected_cells(record, scope):
    """Yield source-native fields, locations and documented raw units."""
    rows, params = record['snapshot']['data'], record['parameters']
    for index, fields in scope['records'].items():
        row = rows[index]
        for field in scope['fields']:
            if field not in fields:
                continue
            registered = scope['definition']['fields'][field]
            unit = '' if scope['financial'] else registered.get('raw_unit', '')
            reference = (dict(kind='registered_field',registry='tinyshare.'+record['operation'],
                field=field,definition_hash=fingerprint(registered)) if unit else None)
            cell = dict(code=row['ts_code'],field=field,label=LABELS.get(field,field),
                period=row['end_date'] if scope['financial'] else row['trade_date'],
                raw=row[field],unit=unit,locator=dict(row=index,field=field),
                basis=dict(report_type=params.get('report_type'),raw_price_adjustment='unadjusted'),
                period_basis='来源end_date报告期' if scope['financial'] else None,
                unit_reference=reference)
            if field == 'close' and index in scope.get('previous_close_cells', {}):
                cell['previous_close_for'] = scope['previous_close_cells'][index]
            yield cell


def project_snapshot(record, scope):
    """Only selected rows/fields reach the writer, with their source keys."""
    from .financial_sources import FINANCIAL_RECORD_KEYS
    metadata = set(FINANCIAL_RECORD_KEYS if scope['financial'] else ('ts_code','trade_date'))
    rows = []
    for index, fields in scope['records'].items():
        row = record['snapshot']['data'][index]
        rows.append({field:row[field] for field in sorted(fields | metadata) if field in row})
    return dict(code=record['snapshot'].get('code'),data=rows)


def request_targets(request):
    from .evidence import FIELD_REGISTRY
    from .financial_sources import FINANCIAL_REQUEST_REGISTRY
    qualified = 'tinyshare.' + request['operation']
    financial = qualified in FINANCIAL_REQUEST_REGISTRY
    definition = (FINANCIAL_REQUEST_REGISTRY if financial else FIELD_REGISTRY).get(qualified, {})
    params = request.get('parameters', {})
    fields = params.get('fields', '')
    if not isinstance(fields, str):
        return []
    return [dict(code=params.get('ts_code'),field=field,
        **(dict(period=params.get('period'),statement=params.get('report_type')) if financial else {}))
        for field in fields.split(',') if field in definition.get('fields', {})]
