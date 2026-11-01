"""Read acquired source shapes for a draft, without promoting financial facts.

No transport or model call lives here. Original rows, literal units and date
roles survive; unavailable cells stay missing rather than becoming zero.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from datetime import date
from collections import Counter
import math
import re

from .contracts import ContractError, canonical, fingerprint, timepoint
from .ifind_financial_catalog import definition, evidence_definitions

NUMBER = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z')
VERSION = 8
READER_POLICY = 'draft_source_reader_version'
SCOPE_VERSION = 2
CODE_FIELDS = ('thscode', 'ts_code', '股票代码', '证券代码')
NAME_FIELDS = ('secname', 'secName', '股票简称', '证券简称', 'name')
LABELS = dict(open='开盘价', high='最高价', low='最低价', close='收盘价',
    unit_nav='单位净值', ths_pe_ttm_stock='市盈率TTM', ths_pb_latest_stock='市净率',
    weight='成分权重')
DATA_OPERATIONS = {'cmd_history_quotation', 'date_sequence', 'basic_data_service',
                   'smart_stock_picking', 'edb_service', 'etf_pcf', 'index_file'}
META_OPERATIONS = {'get_limit_policy', 'get_indicators', 'get_indicator_params'}


def _owned(task, record):
    return isinstance(record,dict) and record.get('task_id') == task['id'] and record.get('research_version') == task['version']


def _source_issue(task, record):
    """Quarantine explicit contradictions locally; raw absence is not approval.

    Legacy unlabelled snapshots remain readable as unverified input. Declared
    failure or a changed declared hash cannot supply numbers or writing text.
    """
    structured = record.get('kind') == 'api' or record.get('operation') in DATA_OPERATIONS
    if not structured:
        return None
    status = record.get('status')
    if status is not None and status not in ('成功待核验','成功但为空','已解析'):
        return '来源未返回成功的业务响应，未采用其中的业务值。'
    snapshot = record.get('snapshot')
    if not isinstance(snapshot,dict):
        return '来源响应结构不完整，保留原记录但不采用业务值。'
    if record.get('operation') in DATA_OPERATIONS and record.get('source') == 'ifind' and 'errorcode' in snapshot:
        code = snapshot['errorcode']
        if type(code) not in (int,str) or code not in (0,'0'):
            return '接口业务返回失败或错误码无效，未采用其中的业务值。'
    try:
        for name in ('hash','snapshot_hash'):
            if name in record and record[name] != fingerprint(snapshot):
                return '来源内容与留存快照哈希不一致，未采用变化后的业务值。'
        if record.get('kind') == 'api' and 'request_hash' in record:
            expected = fingerprint(dict(source=record.get('source'),operation=record.get('operation'),parameters=record.get('parameters')))
            if record['request_hash'] != expected:
                return '来源请求与留存哈希不一致，未采用变化后的业务值。'
    except (ContractError,TypeError,ValueError,OverflowError):
        return '来源内容无法核对，保留原记录但不采用业务值。'
    if record.get('input_hash') is not None and task.get('input_hash') is not None and record['input_hash'] != task['input_hash']:
        return '来源不属于本次任务输入，未采用其中的业务值。'
    return None


def _quotation_day(value):
    if not isinstance(value, str) or re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _sequence_day(value):
    """Read the vendor's documented daily formats without inventing a date."""
    if isinstance(value, str):
        if re.fullmatch(r'\d{8}', value):
            value = value[:4] + '-' + value[4:6] + '-' + value[6:]
        elif re.fullmatch(r'\d{4}/\d{2}/\d{2}', value):
            value = value.replace('/', '-')
    return _quotation_day(value)


def _quotation_code(table, columns, row):
    return next((_at(columns.get(key), row) for key in CODE_FIELDS
                 if _at(columns.get(key), row)), table.get('thscode'))


def _quotation_scope(task, record):
    """Select requested daily cells without replacing the raw source.

    Version two also covers date_sequence and its documented daily formats.
    A bad subset does not block delivery; frozen tasks retain their reader.
    """
    version = task.get('draft_quote_scope_version')
    operation = record.get('operation')
    if version is None or record.get('source') != 'ifind' or operation not in ('cmd_history_quotation', 'date_sequence'):
        return None
    if type(version) is not int or version not in (1, SCOPE_VERSION):
        raise ContractError('行情观测范围版本未登记')
    if version == 1 and operation == 'date_sequence':
        return None  # A frozen task keeps the original sequence reader.
    params = record.get('parameters', {})
    codes = set(params['codes'].split(',')) if isinstance(params.get('codes'), str) else set()
    if operation == 'date_sequence':
        indicators = params.get('indipara')
        fields = {item['indicator'] for item in indicators
                  if isinstance(item, dict) and isinstance(item.get('indicator'), str)} if isinstance(indicators, list) else set()
    else:
        fields = set(params['indicators'].split(',')) if isinstance(params.get('indicators'), str) else set()
    day = _quotation_day if version == 1 else _sequence_day
    start = day(params.get('startdate'))
    end = day(params.get('enddate'))
    from .contracts import market_date
    try:
        cutoff = _quotation_day(market_date(task.get('cutoff')))
    except (ContractError, ValueError, TypeError):
        cutoff = None
    tables = (record.get('snapshot') or {}).get('tables', [])
    selected, rejected = {}, 0
    for index, table in enumerate(tables if isinstance(tables, list) else []):
        if not isinstance(table, dict):
            continue
        dates, columns = table.get('time', []), table.get('table', {})
        dates = dates if isinstance(dates, list) else []
        columns = columns if isinstance(columns, dict) else {}
        row_codes = [_quotation_code(table, columns, row) for row in range(len(dates))]
        counts = Counter((code, raw_day if version == 1 else day(raw_day)) for code, raw_day in zip(row_codes, dates)
                         if isinstance(code, str) and isinstance(raw_day, str))
        rows = set()
        for row, (code, raw_day) in enumerate(zip(row_codes, dates)):
            observed = day(raw_day)
            key = (code, raw_day if version == 1 else observed) if isinstance(code, str) else None
            if (isinstance(code, str) and code in codes and observed and start and end and cutoff
                    and start <= observed <= end and observed <= cutoff and counts[key] == 1):
                rows.add(row)
        allowed = set()
        for field, values in columns.items():
            if field in CODE_FIELDS + NAME_FIELDS:
                continue
            values = values if isinstance(values, list) else [values]
            if field in fields and len(values) == len(dates):
                allowed.add(field)
                rejected += len(values) - len(rows)
            else:
                rejected += sum(_number(value) is not None or value is None for value in values)
        selected[index] = dict(rows=rows, fields=allowed)
    return dict(tables=selected, rejected_cells=rejected)


def _quotation_projection(record, scope):
    """Give models only selected original cells; stored snapshots stay intact."""
    tables = []
    for index, table in enumerate(record['snapshot'].get('tables', [])):
        selected = scope['tables'].get(index, {})
        rows, fields = sorted(selected.get('rows', [])), selected.get('fields', set())
        if not rows or not fields:
            continue
        columns, projected = table['table'], {}
        for field in fields | set(CODE_FIELDS + NAME_FIELDS):
            if field not in columns:
                continue
            values = columns[field] if isinstance(columns[field], list) else [columns[field]]
            if len(values) == len(table['time']):
                projected[field] = [values[row] for row in rows]
        item = dict(time=[table['time'][row] for row in rows], table=projected)
        if 'thscode' in table:
            item['thscode'] = table['thscode']
        tables.append(item)
    return dict(tables=tables)


def source_issues(task):
    """Internal diagnostics distinguish a bad source from a bad subset."""
    result = []
    for record in task.get('source_records', []) + task.get('materials', []):
        if not _owned(task, record):
            continue
        reason = _source_issue(task, record)
        scope = None if reason else _quotation_scope(task, record)
        partial = not reason and scope is not None and scope['rejected_cells'] > 0
        if partial:
            reason = '部分行情或日期序列记录不对应本次代码、日期或字段，保留原始响应并使用其余可用观测。'
        if reason:
            issue = dict(artifact_id=record['id'], source=record.get('source'),
                         operation=record.get('operation'), reason=reason)
            if partial:
                issue['partial'] = True
            result.append(issue)
    if task.get(READER_POLICY)==8:
        for code,metadata in _economic_metadata(task).items():
            reference=metadata.get('unit_reference',{})
            if reference.get('status')=='冲突':
                result.append(dict(artifact_id=reference['citations'][0]['artifact_id'],source='ifind',
                    operation='search_edb',partial=True,reason=code+'的指标目录单位不一致，保留数值，不选择单位。'))
    return result


def _number(value):
    if type(value) in (int, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str) and NUMBER.fullmatch(value.strip()):
        try:
            number = Decimal(value.strip())
            result = float(number)
            return result if number.is_finite() and math.isfinite(result) else None
        except (InvalidOperation, OverflowError):
            pass
    return None


def _at(column, index, default=''):
    return column[index] if isinstance(column, list) and index < len(column) else default


def _economic_metadata(task):
    """Keep literal metadata citations; modern readers do not pick a conflict by order."""
    from .economic_sources import search_response
    modern=task.get(READER_POLICY)==8
    result={};candidates={}
    for record in task.get('source_records', []):
        if not _owned(task,record) or _source_issue(task,record) or record.get('operation')!='search_edb':continue
        if modern and (record.get('source')!='ifind' or not isinstance(record.get('id'),str) or not record['id']):continue
        try:rows=search_response(record['snapshot'])
        except (ContractError,KeyError,TypeError,ValueError):continue
        if not modern:
            result.update({row['displayId']:row for row in rows});continue
        for row in rows:
            citation=dict(artifact_id=record['id'],snapshot_hash=fingerprint(record['snapshot']),
                locator=dict(indicator_id=row['displayId'],field='unit'),raw_unit=row['unit'])
            candidates.setdefault(row['displayId'],[]).append((row,citation))
    for code,items in candidates.items():
        row=deepcopy(min((item[0] for item in items),key=canonical))
        units={item[0]['unit'].strip() for item in items}
        citations={canonical(item[1]):item[1] for item in items}
        row['unit']=next(iter(units)) if len(units)==1 else ''
        row['unit_reference']=dict(kind='source_metadata',status='明确' if len(units)==1 else '冲突',
            citations=[citations[key] for key in sorted(citations)])
        result[code]=row
    return result


def _ordered_fields(columns, parameters):
    # Request arrays survive canonical JSON storage; object-key order does not.
    requested=[item['indicator'] for item in parameters.get('indipara', [])
               if isinstance(item,dict) and isinstance(item.get('indicator'),str)]
    indicators=parameters.get('indicators', [])
    if isinstance(indicators,str):indicators=[value.strip() for value in indicators.split(',')]
    if isinstance(indicators,list):requested.extend(value for value in indicators if isinstance(value,str))
    selected=list(dict.fromkeys(field for field in requested if field in columns))
    return selected+sorted(field for field in columns if field not in selected)


def _reader_version(task):
    """Missing policy preserves a saved task's original reader semantics."""
    version = task.get(READER_POLICY)
    if version is not None and (type(version) is not int or version not in (7,8)):
        raise ContractError('原始数据读取版本未登记')
    return version


def _financial_period(requested):
    """The explicit D argument is the report period, never response time."""
    if isinstance(requested, str) and re.fullmatch(r'\d{4}(0331|0630|0930|1231)', requested):
        try:
            date(int(requested[:4]), int(requested[4:6]), int(requested[6:]))
            return requested
        except ValueError:
            pass
    return ''


def observations(task):
    """Normalize actual observations, not catalogue IDs or financial approval."""
    from .evidence import FIELD_REGISTRY
    from .market_series import SERIES_LABELS
    reader = _reader_version(task)
    calibrated = evidence_definitions()
    units = [row['claims'] for row in calibrated.values()]
    economic = _economic_metadata(task)
    facts = []

    def append(record, code, field, label, period, raw, unit, locator, *, name='', role='time', basis=None, period_basis=None, unit_reference=None):
        # A number-shaped security identifier is never a numeric observation.
        value = _number(raw)
        if value is None and raw not in (None, '', '-', '--', 'N/A', 'null'):
            return
        facts.append(dict(id='D' + str(len(facts) + 1), artifact_id=record['id'],
            source=record.get('source', ''), code=str(code or ''), field=field, label=label,
            period=str(period or ''), value=value, raw_value=raw, unit=unit or '原始单位待核对',
            unit_basis='来源字面口径' if unit else '接口未明确标注', locator=locator,
            status='原始取数', object_label=str(name or code or ''), axis_role=role,
            series_basis=fingerprint(dict(source=record.get('source'), operation=record.get('operation'), basis=basis))))
        metadata = definition(field)
        if metadata and metadata.get('period_kind') == 'point_in_time':
            facts[-1]['period_kind'] = 'point_in_time'
        if reader==8:
            reference=deepcopy(unit_reference)
            if reference is None and unit:
                reference=dict(kind='literal_source',artifact_id=record['id'],snapshot_hash=fingerprint(record.get('snapshot',record)),locator=deepcopy(locator))
            if reference:
                facts[-1]['unit_reference']=reference
                facts[-1]['unit_basis']=('指标目录冲突' if reference.get('status')=='冲突' else {
                    'source_metadata':'指标目录口径','calibrated_definition':'限定范围的原件校准',
                    'registered_field':'已登记字段口径','literal_source':'来源字面标注'}.get(reference.get('kind'),'接口未明确标注'))
        if period_basis is not None:
            facts[-1]['period_basis'] = period_basis
            facts[-1].setdefault('period_kind', 'cumulative')

    for record in task.get('source_records', []):
        if not _owned(task, record) or _source_issue(task,record) or record.get('operation') not in DATA_OPERATIONS:
            continue
        snapshot, params = record.get('snapshot') or {}, record.get('parameters') or {}
        if not isinstance(snapshot, dict):
            continue
        operation = record.get('operation')
        scope = _quotation_scope(task,record)
        if operation == 'smart_stock_picking' and record.get('financial_query_contract', {}).get('query_kind') == 'object_discovery':
            continue
        if operation == 'index_file':
            parsed = snapshot.get('parsed') or {}
            if snapshot.get('role') == 'weights' and isinstance(parsed.get('members'), list):
                for index, row in enumerate(parsed['members']):
                    if not isinstance(row, dict):
                        continue
                    append(record, row.get('code'), 'weight', '成分权重',
                        row.get('observation_date', parsed.get('observation_date')), row.get('weight'),
                        row.get('unit', parsed.get('unit', '')), dict(member=index, field='weight'),
                        name=row.get('name', ''), role='cross_section',
                        basis=dict(index_code=parsed.get('index_code'), period=parsed.get('observation_date')))
            continue
        if operation == 'etf_pcf':
            for index, row in enumerate(snapshot.get('data', [])):
                if isinstance(row, dict) and 'unit_nav' in row:
                    # Parsed exchange NAV keeps its valuation day, not announcement day.
                    origin = snapshot.get('exchange_origin') or {}
                    unit = '元' if str(origin.get('raw_token', '')).startswith(('￥', '¥')) else ''
                    append(record, row.get('ts_code'), 'unit_nav', '单位净值', row.get('trade_date'),
                        row['unit_nav'], unit, dict(row=index, field='unit_nav'), name=origin.get('fund_name', ''))
            continue
        tables = snapshot.get('tables', [])
        if not isinstance(tables, list):
            continue
        for table_index, table in enumerate(tables):
            if not isinstance(table, dict):
                continue
            if operation == 'edb_service':
                codes = table.get('id', [])
                code = _at(codes, 0) if isinstance(codes, list) else codes
                meta = economic.get(code, {})
                values = table.get('value', [])
                for index, value in enumerate(values if isinstance(values, list) else []):
                    append(record, code, 'value', _at(table.get('index_name'), index, meta.get('indexName', code)),
                        _at(table.get('time'), index), value, meta.get('unit', ''),
                        dict(table=table_index, field='value', row=index), basis=dict(indicator=code),unit_reference=meta.get('unit_reference'))
                continue
            columns = table.get('table')
            if not isinstance(columns, dict):
                continue
            field_periods = {row.get('indicator'): row['indiparams'][:2] for row in params.get('indipara', [])
                if isinstance(row, dict) and isinstance(row.get('indiparams'), list) and len(row['indiparams']) >= 2}
            for field in _ordered_fields(columns, params):
                values = columns[field]
                if field in CODE_FIELDS + NAME_FIELDS:
                    continue
                values = values if isinstance(values, list) else [values]
                period, report_type = field_periods.get(field, [params.get('report_period', ''), params.get('statement_type', '')])
                explicit_period = re.search(r'\[(\d{8})\]', field)
                if not period and explicit_period:
                    period = explicit_period[1]
                metadata = definition(field)
                for index, raw in enumerate(values):
                    if scope is not None:
                        selected = scope['tables'].get(table_index, {})
                        if field not in selected.get('fields', set()) or index not in selected.get('rows', set()):
                            continue
                    code = next((_at(columns.get(key), index) for key in CODE_FIELDS if _at(columns.get(key), index)),
                                table.get('thscode', params.get('codes', '')))
                    name = next((_at(columns.get(key), index) for key in NAME_FIELDS if _at(columns.get(key), index)), '')
                    known = next((row for row in units if row['indicator'] == field and code in row['applicable_codes']
                        and period in row['applicable_periods'] and str(report_type) == row['report_type']), None)
                    if reader==8 and known:
                        proof=calibrated[field]
                        try:available=timepoint(proof['basis']['obtained_at'])<=timepoint(task['cutoff'])
                        except (ContractError,KeyError,TypeError,ValueError):available=False
                        if operation!='basic_data_service' or record.get('source')!='ifind' or not available:known=None
                    unit = known['unit'] if known else ''
                    unit_reference=None
                    if reader==8 and known:
                        proof=calibrated[field]
                        unit_reference=dict(kind='calibrated_definition',definition_hash=fingerprint(proof),
                            source=proof['basis']['source'],locator=proof['basis']['locator'],
                            basis_hash=proof['basis']['hash'],available_at=proof['basis']['obtained_at'],scope=dict(codes=known['applicable_codes'],
                                periods=known['applicable_periods'],report_type=known['report_type']))
                    quote_registry = FIELD_REGISTRY.get('ifind.' + str(operation), {}) if reader!=8 or record.get('source')=='ifind' else {}
                    family = next((key for key, pattern in quote_registry.get('instrument_namespaces', {}).items()
                                   if re.fullmatch(pattern, str(code))), None)
                    registered = quote_registry.get('field_families', {}).get(family, {}).get(field, {})
                    if registered and params.get('functionpara', {}).get('Currency') == 'RMB':
                        unit = registered.get('raw_unit', '')
                        if reader==8 and unit:
                            unit_reference=dict(kind='registered_field',registry='ifind.'+str(operation),
                                family=family,field=field,definition_hash=fingerprint(registered),currency_parameter='RMB')
                    label = metadata['name'] if metadata else SERIES_LABELS.get(field, LABELS.get(field, field))
                    if explicit_period and label == field:
                        label = label.replace(explicit_period[0], '')
                    if field in ('open', 'high', 'low', 'close') and family == 'index_level':
                        label = label.replace('价', '点位')
                    observed_period = _at(table.get('time', table.get('date')), index, period)
                    period_basis = None
                    if reader in (7,8) and operation == 'basic_data_service' and metadata is not None:
                        observed_period = _financial_period(period)
                        period_basis = '请求D报告期' if observed_period else '报告期未明确'
                    append(record, code, field, label, observed_period,
                        raw, unit, dict(table=table_index, field=field, row=index), name=name,
                        role='cross_section' if operation == 'smart_stock_picking' or (
                            operation == 'basic_data_service' and ',' in str(params.get('codes', ''))) else 'time',
                        basis=dict(functionpara=params.get('functionpara'), statement_type=report_type), period_basis=period_basis,unit_reference=unit_reference)
    return facts


def _without_binary(value):
    if isinstance(value, dict):
        return {key: _without_binary(item) for key, item in value.items()
                if key not in ('original_base64', 'original_bytes', 'response_headers')}
    if isinstance(value, list):
        return [_without_binary(item) for item in value]
    return value


def source_context(task, limit=24_000):
    """Bound useful source text fairly; never send PDF/base64 bytes to a model."""
    records = [row for row in task.get('source_records', []) + task.get('materials', [])
               if _owned(task, row) and row.get('operation') not in META_OPERATIONS]
    # Business responses precede navigation metadata in report context.
    records.sort(key=lambda row: row.get('operation') not in DATA_OPERATIONS
                 and not row.get('parts') and row.get('kind') != 'filing')
    share = max(1, limit // max(1, sum(_source_issue(task,record) is None for record in records)))
    result = []
    for record in records:
        reason = _source_issue(task,record)
        if reason:
            result.append(dict(id=record['id'],source=record.get('source'),operation=record.get('operation'),
                source_status=record.get('status'),parameters=record.get('parameters'),content='',
                excluded_reason=reason,truncated=False,original_characters=0,raw_binary_omitted=True))
            continue
        snapshot = record.get('snapshot', record)
        scope = _quotation_scope(task,record)
        if scope is not None:
            snapshot = _quotation_projection(record, scope)
        material = snapshot.get('material', snapshot) if isinstance(snapshot, dict) else {}
        if isinstance(material, dict) and isinstance(material.get('parts'), list):
            body = dict(name=material.get('name'), status=material.get('status'), limitation=material.get('limitation'),
                parts=[dict(locator=row.get('locator'), text=row.get('text', ''))
                for row in material['parts'] if isinstance(row, dict)])
        else:
            body = _without_binary(snapshot)
            if isinstance(body, dict) and 'tables' in body:
                body = dict(tables=body['tables'])
        raw = canonical(body)
        result.append(dict(id=record['id'], source=record.get('source'), operation=record.get('operation'),
            source_status=record.get('status'),
            parameters=record.get('parameters'), content=raw[:share], truncated=len(raw) > share,
            original_characters=len(raw), raw_binary_omitted=True))
    return result


def _request_field_info(task, record):
    """Describe literal request columns, without approving response coverage.

    This is writing guidance only. Unknown grammar stays unknown; derived
    selection contracts cannot replace the retained request parameters.
    """
    operation = record.get('operation')
    if record.get('source') != 'ifind' or operation not in {
            'cmd_history_quotation', 'date_sequence', 'basic_data_service',
            'edb_service', 'smart_stock_picking'}:
        return None
    params = record.get('parameters') or {}
    expected = None
    dimension = 'code' if operation == 'edb_service' else 'field'
    if isinstance(params, dict):
        if operation in ('cmd_history_quotation', 'edb_service'):
            values = params.get('indicators')
            if isinstance(values, str) and values.strip():
                expected = [value.strip() for value in values.split(',') if value.strip()]
        elif operation in ('date_sequence', 'basic_data_service'):
            values = params.get('indipara')
            if (isinstance(values, list) and values and all(isinstance(value, dict)
                    and isinstance(value.get('indicator'), str) and value['indicator'].strip() for value in values)):
                expected = [value['indicator'].strip() for value in values]
        else:
            from .financial_sources import validate_financial_query
            try:
                query = validate_financial_query(task, params, require_identity=False)
                if query.get('query_kind') == 'object_discovery':
                    return None
                expected = [field + '[' + query['period'] + ']' for field in query['fields']]
            except (ContractError, KeyError, TypeError, ValueError):
                pass
    columns = {}
    snapshot = record.get('snapshot') or {}
    tables = snapshot.get('tables', []) if isinstance(snapshot, dict) else []
    for table in tables if isinstance(tables, list) else []:
        if not isinstance(table, dict):
            continue
        if dimension == 'code':
            ids = table.get('id')
            name = _at(ids, 0) if isinstance(ids, list) else ids
            items = {name: table.get('value', [])} if isinstance(name, str) else {}
        else:
            items = table.get('table', {})
        if isinstance(items, dict):
            for name, values in items.items():
                if not isinstance(name, str) or name in CODE_FIELDS + NAME_FIELDS:
                    continue
                columns.setdefault(name, []).extend(values if isinstance(values, list) else [values])
    return dict(dimension=dimension, requested=set(expected) if expected is not None else None,
                columns=columns)


def _request_field_notice(record, info):
    if info is None:
        return None
    expected, columns = info['requested'], info['columns']
    notice = dict(source_ref=record['id'])
    if expected is None:
        if columns:
            notice['request_scope'] = 'unspecified'
    else:
        missing = sorted(expected - columns.keys())
        additional = sorted(columns.keys() - expected)
        empty, incomplete = [], []
        for field in sorted(expected & columns.keys()):
            values = columns[field]
            missing_values = sum(value is None or isinstance(value, str) and value.strip().lower()
                                 in ('', '--', 'null', 'none', 'nan', 'n/a') for value in values)
            if not values or missing_values == len(values):
                empty.append(field)
            elif missing_values:
                incomplete.append(field)
        for name, values in (('missing_fields', missing), ('additional_fields', additional),
                             ('empty_fields', empty), ('partially_empty_fields', incomplete)):
            if values:
                notice[name] = values
        if info['dimension'] == 'code' and len(notice) > 1:
            notice['dimension'] = 'indicator_id'
    return notice if len(notice) > 1 else None


def request_field_context(task):
    """Share exception-only field guidance between the App and Skill host."""
    notices = []
    for record in task.get('source_records', []):
        if _owned(task, record) and not _source_issue(task, record):
            notice = _request_field_notice(record, _request_field_info(task, record))
            if notice:
                notices.append(notice)
    if not notices:
        return {}
    return dict(notices=notices, rule='附带字段可按相关性用于分析，字段请求不明确的资料按实际原件使用。'
        '列名出现不等于对象、期间、单位或研究需求已满足；缺列与空值分别保留，不将空值当零。'
        '这些提示不阻断出稿，也不自动要求重新取数。')


def _fact_request_scope(row, info):
    if info is None:
        return ''
    if info['requested'] is None:
        return 'unspecified'
    return '' if row.get(info['dimension']) in info['requested'] else 'additional'


OBSERVATION_PROFILE_RULE = ('observed_profile只定位本来源、本对象及同口径序列的实际观测，不计算收益或作趋势判断。'
    '最低、最高必须注明样本范围；first、last不代替完整路径，缺值不当零。'
    '正负记录并存时不能将整个区间写成持续同号，局部走势须另指明月份。')


def _observation_profile(rows):
    """Select existing observations, never manufacture a derived financial value."""
    if len(rows) < 2 or len({row.get('code') for row in rows}) != 1:
        return None
    dated = [(_sequence_day(row.get('period')), row) for row in rows]
    dates = [day for day, _ in dated]
    if None in dates or len(set(dates)) != len(dates):
        return None
    values = []
    for day, row in dated:
        raw = row.get('raw_value', row.get('value'))
        if type(raw) in (int, float) or isinstance(raw, str) and NUMBER.fullmatch(raw.strip()):
            try:
                number = Decimal(str(raw).strip())
                if number.is_finite():
                    values.append((number, day, row))
            except InvalidOperation:
                pass
    if len(values) < 2:
        return None
    def selected(value):
        row = value[2]
        return dict(data_ref=row['id'], period=row['period'], value=row.get('raw_value', row['value']))
    ordered = sorted(values, key=lambda value:value[1])
    return dict(scope='retained_observations', start=min(dates).isoformat(), end=max(dates).isoformat(),
        observations=len(values), missing=len(rows)-len(values),
        first=selected(ordered[0]), last=selected(ordered[-1]),
        minimum=selected(min(values,key=lambda value:(value[0],value[1]))),
        maximum=selected(max(values,key=lambda value:(value[0],-value[1].toordinal()))),
        has_positive=any(value[0]>0 for value in values), has_negative=any(value[0]<0 for value in values),
        has_zero=any(value[0]==0 for value in values))


def writing_context(task, facts, text_limit=24_000):
    """Send observations once, with literal bases and report-local references.

    Workflow review states are not investment facts. Complete originals remain
    in the workpaper; document scope restrictions still accompany quoted text.
    """
    records = {row['id']: row for row in task.get('source_records', []) + task.get('materials', [])
               if _owned(task, row) and not _source_issue(task,row)}
    scopes = {ident: _quotation_scope(task, record) for ident, record in records.items()}
    request_fields = {ident: _request_field_info(task, record) for ident, record in records.items()}
    groups = {}
    for row in facts:
        if row.get('artifact_id') not in records:
            continue
        scope = scopes[row['artifact_id']]
        if scope is not None:
            locator = row.get('locator', {})
            selected = scope['tables'].get(locator.get('table'), {})
            if row.get('field') not in selected.get('fields', set()) or locator.get('row') not in selected.get('rows', set()):
                continue
        key = (row['artifact_id'], row['field'], row['unit'], row.get('axis_role', 'time'),
               row.get('series_basis', ''), _fact_request_scope(row, request_fields[row['artifact_id']]))
        groups.setdefault(key, []).append(row)
    series = []
    used = set()
    for (artifact, field, unit, role, _, request_scope), rows in groups.items():
        used.add(artifact)
        item = dict(source_ref=artifact, field=field, label=rows[0]['label'], unit=unit,
                    unit_basis=rows[0].get('unit_basis', ''), axis_role=role)
        if request_scope:
            item['request_scope'] = request_scope
        profile = _observation_profile(rows) if role == 'time' else None
        if profile:
            item['observed_profile'] = profile
        if rows[0].get('unit_reference'):
            reference=rows[0]['unit_reference'];projected={'kind':reference['kind']}
            if reference.get('status'):projected['status']=reference['status']
            if reference.get('kind')=='source_metadata':
                selected=[];seen_units=set()
                for citation in reference.get('citations',[]):
                    if citation['raw_unit'] not in seen_units and len(selected)<4:
                        selected.append(citation['artifact_id']);seen_units.add(citation['raw_unit'])
                projected.update(source_refs=selected,omitted_citations=len(reference.get('citations',[]))-len(selected))
            elif reference.get('artifact_id'):projected['source_refs']=[reference['artifact_id']]
            item['unit_reference']=projected
        if rows[0].get('period_kind'):
            item['period_kind'] = rows[0]['period_kind']
        if rows[0].get('period_basis'):
            item['period_basis'] = rows[0]['period_basis']
        common = {}
        dimensions = ('code', 'object_label', 'period')
        for name in dimensions:
            values = {row.get(name, '') for row in rows}
            if len(values) == 1:
                common[name] = next(iter(values))
        if 'code' in common and common.get('object_label') == common['code']:
            common.pop('object_label', None)
            dimensions = ('code', 'period')
        columns = ['id'] + [name for name in dimensions if name not in common] + ['value']
        item.update(common)
        item['columns'] = columns
        item['rows'] = [[row.get('raw_value', row['value']) if name == 'value' else row.get(name, '')
                         for name in columns] for row in rows]
        series.append(item)
    documents, other, projections = [], [], {}
    coverage = {}
    for row in facts:
        locator = row.get('locator', {})
        coverage.setdefault((row.get('artifact_id'), locator.get('table'), locator.get('field')), set()).add(locator.get('row'))
    for record in records.values():
        # Quote numbers travel only through the selected observations. Excluded
        # cells cannot re-enter through unmatched columns or a raw fallback.
        if scopes[record['id']] is not None:
            continue
        snapshot = record.get('snapshot', record)
        material = snapshot.get('material', snapshot) if isinstance(snapshot, dict) else {}
        parts = material.get('parts') if isinstance(material, dict) else None
        if isinstance(parts, list) and any(isinstance(part, dict) and part.get('text') for part in parts):
            documents.append(dict(source_ref=record['id'], name=material.get('name'),
                content_scope=dict(status=material.get('status'), limitation=material.get('limitation')),
                published_at=material.get('published_at'),
                parts=[dict(locator=part.get('locator'), text=part['text'])
                       for part in parts if isinstance(part, dict) and isinstance(part.get('text'), str)]))
            used.add(record['id'])
        elif record['id'] in used and isinstance(snapshot, dict) and isinstance(snapshot.get('tables'), list):
            # Numeric columns must not hide a business-description column in
            # the same response. Preserve unmatched cells and release metadata.
            remaining_tables = []
            for index, table in enumerate(snapshot['tables']):
                if not isinstance(table, dict):
                    continue
                columns = table.get('table', {})
                extra = {}
                if isinstance(columns, dict):
                    for field, values in columns.items():
                        values = values if isinstance(values, list) else [values]
                        covered = coverage.get((record['id'], index, field), set())
                        if field not in CODE_FIELDS + NAME_FIELDS and any(i not in covered for i in range(len(values))):
                            extra[field] = values
                metadata = {key:value for key,value in table.items()
                            if key not in ('table','thscode','time','id','index_name','value')}
                if extra or metadata:
                    retained = {key:table[key] for key in ('thscode','time','id') if key in table}
                    retained.update(metadata)
                    if extra:
                        retained['table'] = {**{key:value for key,value in columns.items() if key in CODE_FIELDS + NAME_FIELDS}, **extra}
                    remaining_tables.append(retained)
            if remaining_tables:
                other.append(record)
                projections[record['id']]=dict(tables=remaining_tables)
        elif (record['id'] not in used and record.get('operation') not in META_OPERATIONS | {'get_thscode', 'search_edb'}
              and isinstance(snapshot, dict) and snapshot.get('errorcode', 0) == 0
              and record.get('status') not in ('失败', '请求失败')):
            other.append(record)
    share = max(1, text_limit // max(1, len(documents) + len(other)))
    for document in documents:
        remaining = share
        clipped = []
        for part in document['parts']:
            if remaining <= 0:
                break
            clipped.append(dict(locator=part['locator'], text=part['text'][:remaining]))
            remaining -= len(clipped[-1]['text'])
        document['truncated'] = sum(len(part['text']) for part in document['parts']) > share
        document['parts'] = clipped
    context = source_context(dict(task, source_records=other, materials=[]), limit=share * len(other)) if other else []
    for row in context:
        if row['id'] in projections:
            raw=canonical(projections[row['id']])
            row.update(content=raw[:share],truncated=len(raw)>share,original_characters=len(raw))
        row.pop('source_status', None)
        used.add(row['id'])
    sources = [dict(id=record['id'], source=record.get('source'), operation=record.get('operation'),
                    parameters=_without_binary(record.get('parameters', {})))
               for record in records.values() if record['id'] in used]
    result = dict(observations=series, sources=sources, documents=documents, other_data=context)
    request_context = request_field_context(task)
    if request_context:
        result['request_context'] = request_context
    if any(item.get('observed_profile') for item in series):
        result['observation_profile_rule'] = OBSERVATION_PROFILE_RULE
    return result


def observation_context(task, facts):
    """Compact selectors shared by the Skill host and frozen report context."""
    data=writing_context(task,facts)
    profiles=[{key:item[key] for key in ('source_ref','field','code','observed_profile') if key in item}
              for item in data['observations'] if item.get('observed_profile')]
    return dict(profiles=profiles,rule=OBSERVATION_PROFILE_RULE) if profiles else {}
