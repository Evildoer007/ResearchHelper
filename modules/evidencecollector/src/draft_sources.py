"""Read acquired source shapes for a draft, without promoting financial facts.

No transport or model call lives here. Original rows, literal units and date
roles survive; unavailable cells stay missing rather than becoming zero.
"""
from bisect import bisect_right
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timedelta
from collections import Counter
import base64
import hashlib
import math
import re

from .contracts import ContractError, canonical, fingerprint, market_date, now, timepoint
from .ifind_financial_catalog import definition, evidence_definitions
from .intraday_quotes import LABELS as INTRADAY_LABELS, MINUTE, REALTIME
from .pdf_material import pdf_reparse_replacements
from .subject_scope import QUOTED_TEXT, excluded_subjects, subject_allowed

NUMBER = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z')
FINANCIAL_BATCH_COLUMNS_READER = 28
VERSION = FINANCIAL_BATCH_COLUMNS_READER
READER_POLICY = 'draft_source_reader_version'
SCOPE_VERSION = 3
CODE_FIELDS = ('thscode', 'ts_code', '股票代码', '证券代码')
NAME_FIELDS = ('secname', 'secName', '股票简称', '证券简称', 'name')
LABELS = dict(open='开盘价', high='最高价', low='最低价', close='收盘价',
    unit_nav='单位净值', ths_pe_ttm_stock='市盈率TTM', ths_pb_latest_stock='市净率',
    weight='成分权重', volume='成交量', amount='成交额', **INTRADAY_LABELS)
DATA_OPERATIONS = {'cmd_history_quotation', 'date_sequence', 'basic_data_service',
                   'smart_stock_picking', 'edb_service', 'etf_pcf', 'index_file', REALTIME, MINUTE}
MARKET_REFRESH_OPERATIONS = {'cmd_history_quotation', 'date_sequence'}
ECONOMIC_REFRESH_OPERATIONS = {'edb_service'}
MISSING_CELLS = (None, '', '-', '--', 'N/A', 'null')
META_OPERATIONS = {'get_limit_policy', 'get_indicators', 'get_indicator_params'}
DISCLOSURE_POLICY = 'draft_disclosure_scope_version'
EXCERPT_LOCATION_POLICY = 'draft_excerpt_location_version'
EXCERPT_LOCATION_VERSION = 2
FINANCIAL_PERIOD_GAP_POLICY = 'draft_financial_period_gap_version'
FINANCIAL_PERIOD_GAP_VERSION = 2
HISTORICAL_ORIGINAL_POLICY = 'draft_historical_original_version'
CATALOGUE_OPERATIONS = {'report_query', 'release_catalog'}
ORIGINAL_DOWNLOAD_OPERATIONS = {('ifind', 'filing_original'), ('official', 'release_original')}


class HistoricalOriginalUnavailable(ContractError):
    """A new transfer would be too late for the task's historical cutoff."""


def _owned(task, record):
    return isinstance(record,dict) and record.get('task_id') == task['id'] and record.get('research_version') == task['version']


def historical_original_scope(task):
    return (type(task.get(HISTORICAL_ORIGINAL_POLICY)) is int
            and task[HISTORICAL_ORIGINAL_POLICY] == 1 and task.get('research_mode') == 'historical'
            and task.get('intent') != '原件内容核对')


def historical_originals_local_only(task):
    return historical_original_scope(task) and timepoint(now()) >= timepoint(task['cutoff'])


def historical_structured_scope(task):
    return (task.get('research_mode') == 'historical' and task.get('intent') != '原件内容核对'
            and _reader_at_least(task, 23))


def historical_structured_issue(task, source, operation):
    """These registered paths cannot select the version available at a past cutoff."""
    if not historical_structured_scope(task):
        return None
    from .evidence import (ECONOMIC_REQUEST_REGISTRY, ETF_REQUEST_REGISTRY, FINANCIAL_QUERY_REGISTRY,
                           FINANCIAL_REQUEST_REGISTRY, INDEX_REQUEST_REGISTRY)

    qualified = f'{source}.{operation}'
    if (qualified in ('ifind.basic_data_service', 'ifind.search_edb', 'official.hsi_close_history')
            or any(qualified in registry for registry in (ECONOMIC_REQUEST_REGISTRY, ETF_REQUEST_REGISTRY,
                   FINANCIAL_QUERY_REGISTRY, FINANCIAL_REQUEST_REGISTRY, INDEX_REQUEST_REGISTRY))):
        return '该接口提供当前取得的版本，历史截止时点的可得版本待核对。'
    return None


def _historical_original_issue(task, record):
    """An as-of original needs both a version retained in time and publication proof."""
    from .evidence import validate_provenance

    obtained_at = record.get('obtained_at')
    try:
        if not isinstance(obtained_at, str) or len(obtained_at) <= 10:
            raise ContractError('缺少原件取得时点')
        obtained = timepoint(obtained_at)
        if obtained > timepoint(task['cutoff']):
            return '原件在研究截止时点后取得，历史可得版本待核对。'
    except ContractError:
        return '原件缺少可核对的截止前留存时点。'
    for item in task.get('evidence', []):
        artifact = item.get('artifact')
        if (item.get('task_id') != task['id'] or item.get('research_version') != task['version']
                or item.get('source_check') != '通过' or item.get('applicability_check') != '通过'
                or not isinstance(artifact, dict) or artifact.get('id') != record['id']
                or artifact.get('obtained_at') != obtained_at or item.get('obtained_at') != obtained_at
                or item.get('snapshot_hash') != record.get('snapshot_hash', record.get('hash'))):
            continue
        publication = item.get('publication')
        reference = publication.get('basis_ref') if isinstance(publication, dict) else None
        if (not isinstance(reference, dict) or reference.get('kind') not in (None, 'material')
                or item.get('availability') is not None):
            continue
        try:
            if timepoint(item.get('published_at')) > obtained:
                continue
            validate_provenance(item, task)
        except (ContractError, KeyError, TypeError, ValueError, OverflowError):
            continue
        return None
    return '截止前留存的原件缺少已核验的原文首发依据。'


def historical_restricted_sources(task):
    original_scope = historical_original_scope(task)
    if not original_scope and not historical_structured_scope(task):
        return set()
    return {record['id'] for record in task.get('source_records', []) + task.get('materials', [])
            if _owned(task, record)
            and (historical_structured_issue(task, record.get('source'), record.get('operation'))
                 or original_scope and (record.get('operation') in CATALOGUE_OPERATIONS
                    or record.get('kind') in ('material', 'filing') and _source_issue(task, record)))}


def _source_issue(task, record):
    """Quarantine explicit contradictions locally; raw absence is not approval.

    Legacy unlabelled snapshots remain readable as unverified input. Declared
    failure or a changed declared hash cannot supply numbers or writing text.
    """
    issue = historical_structured_issue(task, record.get('source'), record.get('operation'))
    if issue:
        return issue
    if (record.get('source'), record.get('operation')) == ('official', 'index_file'):
        from .index_sources import DRAFT_WEIGHT_POLICY, DRAFT_LATEST_WEIGHT_VERSIONS
        if task.get(DRAFT_WEIGHT_POLICY) in DRAFT_LATEST_WEIGHT_VERSIONS:
            try:
                validate_stated_source(task, 'official')
            except ContractError as error:
                return str(error)
    if excluded_subjects(task):
        params = record.get('parameters') or {}
        targets = [params.get(key) for key in ('codes', 'code', 'ts_code', 'index_code', 'secname')]
        if record.get('operation') == 'edb_service':
            targets.append(params.get('indicators'))
        directory = record.get('directory') or {}
        targets.extend(directory.get(key) for key in ('issuer', 'security_name'))
        if any(not subject_allowed(task, code) for value in targets if isinstance(value, str)
               for code in value.split(',')):
            return '原件包含用户排除的研究对象，仅在底稿留存。'
    structured = record.get('kind') == 'api' or record.get('operation') in DATA_OPERATIONS
    from .market_series import nav_source_scope_enabled

    source = record.get('source')
    if structured and nav_source_scope_enabled(task) and source in ('ifind', 'official', 'tinyshare'):
        try:
            validate_stated_source(task, source)
        except ContractError:
            return '该来源不在本次研究指定范围，原始响应留存于底稿。'
    if not structured:
        if record.get('kind') not in ('material', 'filing'):
            return None
        historical = historical_original_scope(task)
        if task.get(DISCLOSURE_POLICY) != 1 and not historical:
            return None
        snapshot = record.get('snapshot')
        declared_hash = record.get('snapshot_hash') if record.get('kind') == 'material' else record.get('hash')
        try:
            if declared_hash is not None and fingerprint(snapshot) != declared_hash:
                raise ContractError('原件快照已变更')
            directory = record.get('directory')
            if record.get('source') == 'ifind' and isinstance(directory, dict) and 'catalog_ref' in directory:
                from .evidence import validate_filing_catalog_binding
                if not isinstance(snapshot, dict) or snapshot.get('directory') != directory:
                    raise ContractError('公告原件与目录标识不一致')
                if not validate_filing_catalog_binding(task, directory)['downloadable']:
                    raise ContractError('目录条目未通过原件选择核对')
            if record.get('source') == 'official' and record.get('operation') == 'release_original':
                from .official_sources import validate_original
                validate_original(task, record)
        except (ContractError, KeyError, TypeError, ValueError, OverflowError):
            return '原件快照或公告目录绑定不一致，保留留存材料，正文未采用其中的摘录。'
        return _historical_original_issue(task, record) if historical else None
    status = record.get('status')
    if status is not None and status not in ('成功待核验','成功但为空','已解析'):
        return '来源未返回成功的业务响应，未采用其中的业务值。'
    snapshot = record.get('snapshot')
    if not isinstance(snapshot,dict):
        return '来源响应结构不完整，保留原记录但不采用业务值。'
    if record.get('source') == 'tinyshare' and task.get('draft_tinyshare_reader_version') == 1:
        code = snapshot.get('code')
        if type(code) not in (int,str) or code not in (0,'0'):
            return 'Tinyshare未明确返回成功业务状态，保留原始响应，不采用业务值。'
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
    if nav_source_scope_enabled(task) and (source, record.get('operation')) == ('official', 'etf_pcf'):
        from .market_calendar import market_date_ranges, market_question_ranges

        code = (record.get('parameters') or {}).get('ts_code')
        windows = market_date_ranges(market_question_ranges(task, record), code, 'unit_nav')
        origin = snapshot.get('exchange_origin')
        day = _quotation_day(origin.get('valuation_date')) if isinstance(origin, dict) else None
        if windows is not None and (day is None or not any(start <= day <= end for start, end in windows)):
            return '清单的净值所属日未匹配指定日期，原始清单留存于底稿。'
    if record.get('observation_requests') and task.get('market_observation_selection_version') is not None:
        from .market_series import latest_observation_selected
        try:latest_observation_selected(task,record)
        except (ContractError,KeyError,TypeError,ValueError):
            return '最近观测范围与原始请求不一致，保留原件但不采用该范围。'
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


def _raw_cell(column, row):
    return column[row] if isinstance(column, list) else column


def _bound_market_code(table, columns, row):
    code = _quotation_code(table, columns, row)
    if not isinstance(code, str) or not code or table.get('thscode') not in (None, '', code):
        return None
    for key in CODE_FIELDS:
        if key not in columns:
            continue
        values = columns[key]
        if isinstance(values, list) and len(values) != len(table['time']):
            return None
        if _raw_cell(values, row) not in (None, '', code):
            return None
    return code


def market_cell_binding_enabled(task):
    version = task.get('draft_quote_scope_version')
    return type(version) is int and version == SCOPE_VERSION


def bound_ifind_market_scope(task, record, *, close_calendars=None, latest_only=None, question_ranges=None):
    """Select unique original cells after agreeing on security, day and field."""
    from .market_series import daily_market_as_of, latest_observation_selected, stated_mixed_latest_scopes, valuation_exclusion_scope
    from .market_calendar import close_return_predecessors, market_date_ranges, stated_latest_session_calculation
    from .subject_scope import subject_key

    series = record['operation'] == 'date_sequence'
    scope = dict(tables={}, rejected_cells=0, rejected_tables=0, duplicate_cells=0, numeric_cells=0)
    if series:
        scope['kind'] = 'series_rows'
    params, snapshot = record.get('parameters'), record.get('snapshot')
    if not isinstance(params, dict) or not isinstance(snapshot, dict):
        scope['rejected_tables'] = 1
        return scope
    tables = snapshot.get('tables')
    request_codes = params.get('codes')
    if series:
        indicators = params.get('indipara')
        fields = {item['indicator'] for item in (indicators if isinstance(indicators, list) else [])
                  if isinstance(item, dict) and isinstance(item.get('indicator'), str)}
    else:
        fields = set(params['indicators'].split(',')) if isinstance(params.get('indicators'), str) else set()
    business_code = snapshot.get('errorcode')
    bad_response = type(business_code) not in (int, str) or business_code not in (0, '0')
    if (not isinstance(tables, list) or not isinstance(request_codes, str) or not fields
            or ((series or 'errorcode' in snapshot) and bad_response)):
        scope['rejected_tables'] = 1
        return scope
    codes = set(request_codes.split(','))
    excluded = valuation_exclusion_scope(task)[1] if series else frozenset()
    start, end = (_sequence_day(params.get(key)) for key in ('startdate', 'enddate'))
    try:
        cutoff = _quotation_day(daily_market_as_of(task))
    except (ContractError, KeyError, TypeError, ValueError):
        cutoff = None
    if start is None or end is None or cutoff is None:
        scope['rejected_tables'] = len(tables)
        return scope
    end = min(end, cutoff)
    if latest_only is None:
        latest_only = latest_observation_selected(task, record)
    candidates, counts = {}, Counter()
    predecessors = (close_return_predecessors(task, record, close_calendars, question_ranges)
                    if not series and 'close' in fields else {})
    dependency_cells = {}
    intervals = {(code, field): market_date_ranges(question_ranges, code, field)
                 for code in codes for field in fields} if question_ranges else {}
    if latest_only and intervals:
        # Let only an explicitly requested latest read use its bound window
        # alongside literal dates on the same question.
        linked = set(record.get('question_ids') or ())
        for reading in stated_mixed_latest_scopes(task):
            if reading['question_id'] in linked:
                for code in reading.get('codes', ()):
                    intervals.pop((code, reading['indicator']), None)
    for index, table in enumerate(tables):
        if (not isinstance(table, dict) or not isinstance(table.get('time'), list)
                or not isinstance(table.get('table'), dict)):
            scope['rejected_tables'] += 1
            continue
        table_code = table.get('thscode')
        if table_code not in (None, '') and (not isinstance(table_code, str) or table_code not in codes):
            scope['rejected_tables'] += 1
            continue
        dates, columns = table['time'], table['table']
        identities = [(_bound_market_code(table, columns, row), _sequence_day(raw))
                      for row, raw in enumerate(dates)]
        for field, column in columns.items():
            if field in CODE_FIELDS + NAME_FIELDS:
                continue
            values = column if isinstance(column, list) else [column]
            if field not in fields:
                scope['rejected_cells'] += sum(_number(raw) is not None or raw is None for raw in values)
                continue
            if field in excluded:
                continue  # The original response is retained outside the active research.
            if len(values) != len(dates) or series and not isinstance(column, list):
                scope['rejected_cells'] += max(1, len(values))
                continue
            for row, ((code, observed), raw) in enumerate(zip(identities, values)):
                if code not in codes or observed is None or not start <= observed <= end:
                    scope['rejected_cells'] += 1
                    continue
                windows = intervals.get((code, field))
                if windows is not None and not any(first <= observed <= last for first, last in windows):
                    target = (predecessors.get(subject_key(code).upper(), {}).get(observed)
                              if field == 'close' and predecessors else None)
                    if target is None:
                        continue
                    dependency_cells[index, row] = target
                counts[code, field, observed] += 1
                numeric = _number(raw) is not None
                if not numeric and raw not in MISSING_CELLS:
                    scope['rejected_cells'] += 1
                    continue
                candidates.setdefault((code, field), []).append((index, row, observed, numeric))
    fieldwise = task.get('market_observation_selection_version') != 1
    latest_days = {}
    if latest_only:
        scope.update(observation_mode='latest_available', omitted_cells=0)
        # Preserve the selected date even when the latest cell is ambiguous.
        # Its absence cannot silently promote an older result to that date.
        for (code, field), entries in candidates.items():
            if intervals.get((code, field)) is not None:
                continue
            key = (code, field if fieldwise else None)
            newest = max((numeric, observed) for _, _, observed, numeric in entries)
            latest_days[key] = max(newest, latest_days.get(key, newest))
    locations, close_candidates, previous = set(), {}, {}
    for (code, field), entries in candidates.items():
        latest = (latest_days.get((code, field if fieldwise else None), (False, None))[1]
                  if intervals.get((code, field)) is None else None)
        for index, row, observed, numeric in entries:
            if counts[code, field, observed] != 1:
                scope['rejected_cells'] += 1
                scope['duplicate_cells'] += 1
                continue
            if field == 'close' and (index, row) in dependency_cells:
                if numeric:
                    previous[index, row] = dependency_cells[index, row]
                continue
            if latest is not None and field == 'close' and not series and close_calendars is not None:
                close_candidates.setdefault((code, field), []).append((index, row, observed, numeric))
            if latest is not None and observed != latest:
                scope['omitted_cells'] += 1
                continue
            locations.add((index, field, row))
            scope['numeric_cells'] += numeric
    _set_market_locations(scope, locations)
    if (latest_only and not series and close_calendars is not None and close_candidates
            and stated_latest_session_calculation(task, for_report=True) is None):
        previous.update(_previous_close_cells(task, record, scope['tables'], close_candidates, close_calendars))
    if previous:
        scope['previous_close_cells'] = previous
    return scope


def bound_quote_days(task, record):
    """Give calendar coverage the same close cells the research can read."""
    scope = bound_ifind_market_scope(task, record, latest_only=False)
    dates = {}
    for index, field, row in _market_locations(scope):
        if field != 'close':
            continue
        table = record['snapshot']['tables'][index]
        columns = table['table']
        code = _bound_market_code(table, columns, row)
        day = _sequence_day(table['time'][row]).isoformat()
        dates.setdefault(code, {})[day] = _number(_raw_cell(columns[field], row)) is not None
    return dates


def partial_date_series_enabled(task):
    return type(task.get('draft_first_version')) is int and task['draft_first_version'] == 1 and _reader_at_least(task, 18)


def draft_date_series_scope(task, record, *, latest_only=None):
    """Select bound original cells, independently of the full-response check."""
    if market_cell_binding_enabled(task):
        return bound_ifind_market_scope(task, record, latest_only=latest_only)
    from .market_series import daily_market_as_of, latest_observation_selected

    params = record.get('parameters')
    snapshot = record['snapshot']
    scope = dict(kind='series_rows', tables={}, rejected_cells=0, rejected_tables=0, numeric_cells=0)
    code = snapshot.get('errorcode')
    tables = snapshot.get('tables')
    if (type(code) not in (int, str) or code not in (0, '0') or not isinstance(tables, list)
            or not isinstance(params, dict) or not isinstance(params.get('codes'), str)
            or not isinstance(params.get('indipara'), list)):
        scope['rejected_tables'] = 1
        return scope
    start = _sequence_day(params.get('startdate'))
    end = _sequence_day(params.get('enddate'))
    cutoff = _quotation_day(daily_market_as_of(task))
    if start is None or end is None or cutoff is None:
        scope['rejected_tables'] = len(tables)
        return scope
    end = min(end, cutoff)
    codes = set(params['codes'].split(','))
    fields = [item['indicator'] for item in params['indipara']
              if isinstance(item, dict) and isinstance(item.get('indicator'), str)]
    dates = Counter()
    candidates = {}
    for index, table in enumerate(tables):
        if (not isinstance(table, dict) or not isinstance(table.get('thscode'), str)
                or table['thscode'] not in codes or not isinstance(table.get('time'), list)
                or not isinstance(table.get('table'), dict)):
            scope['rejected_tables'] += 1
            continue
        code, columns = table['thscode'], table['table']
        days = [_sequence_day(value) for value in table['time']]
        dates.update((code, day) for day in days if day is not None and start <= day <= end)
        echoes = [columns[field] for field in CODE_FIELDS if field in columns]
        if any(isinstance(value, list) and len(value) != len(days) for value in echoes):
            scope['rejected_tables'] += 1
            continue
        rows = {row for row, day in enumerate(days)
                if day is not None and start <= day <= end
                and all((value[row] if isinstance(value, list) else value) in (None, '', code)
                        for value in echoes)}
        for field in fields:
            if field not in columns:
                continue
            values = columns[field]
            if not isinstance(values, list) or len(values) != len(days):
                scope['rejected_cells'] += len(values) if isinstance(values, list) and values else 1
                continue
            cells = candidates.setdefault((code, field), [])
            for row, raw in enumerate(values):
                numeric = _number(raw) is not None
                if row not in rows or not numeric and raw not in MISSING_CELLS:
                    scope['rejected_cells'] += 1
                    continue
                cells.append((index, row, days[row], numeric))

    if latest_only is None:
        latest_only = latest_observation_selected(task, record)
    fieldwise = task.get('market_observation_selection_version') != 1
    latest_dates, latest_numbers = {}, {}
    if latest_only:
        scope.update(observation_mode='latest_available', omitted_cells=0)
        for (code, field), cells in candidates.items():
            key = (code, field if fieldwise else None)
            for _, _, day, numeric in cells:
                latest_dates[key] = max(day, latest_dates.get(key, day))
                if numeric:
                    latest_numbers[key] = max(day, latest_numbers.get(key, day))
    for (code, field), cells in candidates.items():
        key = (code, field if fieldwise else None)
        latest = latest_numbers.get(key, latest_dates.get(key))
        for index, row, day, numeric in cells:
            if dates[code, day] != 1:
                scope['rejected_cells'] += 1
                continue
            if latest is not None and day != latest:
                scope['omitted_cells'] += 1
                continue
            selection = scope['tables'].setdefault(index, dict(cells={}, rows=set(), fields=set()))
            selection['cells'].setdefault(field, set()).add(row)
            selection['rows'].add(row)
            selection['fields'].add(field)
            scope['numeric_cells'] += numeric
    return scope


def _financial_rows(record, *, requested_fields=None):
    """Bind fixed financial rows to their requested subjects, without changing originals.

    Single-subject responses may omit an echoed code. Multi-subject responses
    need an aligned row code or an explicit table code; response identifiers
    must agree with each other before a cell can enter analysis or writing.
    """
    parameters = record.get('parameters') or {}
    requested_codes = parameters.get('codes')
    codes = {value.strip() for value in requested_codes.split(',') if value.strip()} if isinstance(requested_codes,str) else set()
    selected, rejected, rejected_tables = {}, 0, 0
    tables = (record.get('snapshot') or {}).get('tables', [])
    for index, table in enumerate(tables if isinstance(tables,list) else []):
        if not isinstance(table,dict) or not isinstance(table.get('table'),dict):
            rejected_tables += 1
            continue
        columns = table['table']
        values = {field: value if isinstance(value,list) else [value] for field,value in columns.items()}
        identifiers = {field: value for field,value in values.items() if field in CODE_FIELDS and value}
        code_lengths = {len(value) for value in identifiers.values()}
        if len(code_lengths) == 1:
            count = next(iter(code_lengths))
        elif requested_fields is not None:
            count = 1 if not code_lengths else 0  # A static table has one row absent an explicit vector of codes.
        else:
            count = max((len(value) for field,value in values.items() if field not in CODE_FIELDS+NAME_FIELDS), default=0)
        aligned_codes = len(code_lengths)<=1
        table_code = table.get('thscode')
        row_codes = {}
        if aligned_codes and codes:
            for row in range(count):
                literal = [table_code] + [value[row] for value in identifiers.values()]
                literal = [value for value in literal if value not in (None,'')]
                if any(not isinstance(value,str) or value not in codes for value in literal):
                    continue
                subjects = set(literal)
                if len(subjects) == 1:
                    row_codes[row] = next(iter(subjects))
                elif not subjects and len(codes) == 1:
                    row_codes[row] = next(iter(codes))
        fields = set()
        for field,column in values.items():
            if field in CODE_FIELDS+NAME_FIELDS:
                continue
            if len(column) == count and (requested_fields is None or field in requested_fields):
                fields.add(field)
                rejected += len(column)-len(row_codes)
            else:
                rejected += len(column)
        selected[index] = dict(rows=set(row_codes),fields=fields,row_count=count,codes=row_codes)
    return dict(kind='financial_rows',tables=selected,rejected_cells=rejected,rejected_tables=rejected_tables)


def _financial_scope(task, record):
    return _bound_financial_scope(record) if _reader_at_least(task, 21) else _financial_rows(record)


def _bound_financial_scope(record):
    from .financial_sources import basic_financial_response_fields

    snapshot = record['snapshot']
    try:
        requested = basic_financial_response_fields(record['parameters'], snapshot)
    except (ContractError, KeyError, TypeError, ValueError):
        return dict(kind='financial_rows', cell_bound=True, tables={}, rejected_cells=0, rejected_tables=1)
    scope = _financial_rows(record, requested_fields=requested)
    scope['cell_bound'] = True
    scope['request_fields'] = requested
    counts = Counter()
    candidates = []
    for index, selection in scope['tables'].items():
        columns = snapshot['tables'][index]['table']
        selection['cells'] = {}
        for field in selection['fields']:
            for row, code in selection['codes'].items():
                address = (code, field)  # All fields carry the request's independently checked report scope.
                counts[address] += 1
                raw = _raw_cell(columns[field], row)
                if raw is not None and (type(raw) not in (int, float) or _number(raw) is None):
                    scope['rejected_cells'] += 1
                    continue
                candidates.append((address, index, field, row))
    scope['duplicate_cells'] = sum(count for count in counts.values() if count > 1)
    for address, index, field, row in candidates:
        if counts[address] != 1:
            scope['rejected_cells'] += 1
            continue
        scope['tables'][index]['cells'].setdefault(field, set()).add(row)
    return scope


def retained_financial_statements(task, rows):
    """Recover only the request scope of a legacy current financial reading."""
    if task.get('research_mode') != 'current' or _reader_version(task) is not None:
        return {}
    records = {}
    for record in task.get('source_records', []):
        if _owned(task, record):
            records.setdefault(record['id'], []).append(record)
    by_source = {}
    for row in rows:
        if row.get('period_basis') is None and row.get('value') is not None:
            by_source.setdefault(row['artifact_id'], []).append(row)

    statements = {}
    for ident, readings in by_source.items():
        owned = records.get(ident, [])
        if len(owned) != 1:
            continue
        record = owned[0]
        if ((record.get('kind'), record.get('source'), record.get('operation'))
                != ('api', 'ifind', 'basic_data_service') or not record.get('hash')
                or not record.get('request_hash') or _source_issue(task, record)):
            continue
        scope = _bound_financial_scope(record)
        for row in readings:
            locator = row.get('locator') or {}
            field, table, index = row['field'], locator.get('table'), locator.get('row')
            request = scope.get('request_fields', {}).get(field)
            metadata = definition(field)
            selection = scope['tables'].get(table, {})
            if (not request or not metadata or locator.get('field') != field or type(index) is not int
                    or type(table) is not int
                    or index not in selection.get('cells', {}).get(field, ())
                    or row['code'] != selection['codes'][index]):
                continue
            period, report_type = request
            if not _financial_period(period) or _sequence_day(period) != _sequence_day(row.get('period')):
                continue
            choices = {value: label for label, value in metadata['supported_report_types'].items()}
            raw = _raw_cell(record['snapshot']['tables'][table]['table'][field], index)
            if report_type in choices and type(raw) is type(row['raw_value']) and raw == row['raw_value']:
                statements[row['id']] = choices[report_type]
    return statements


def _financial_projection(record, scope, *, text_only=False):
    """Project safe rows for both model phases; numeric facts travel once."""
    if scope.get('cell_bound'):
        by_code = {}
        if not text_only:
            for index, selection in scope['tables'].items():
                columns = record['snapshot']['tables'][index]['table']
                for field in sorted(selection['cells']):
                    for row in sorted(selection['cells'][field]):
                        code = selection['codes'][row]
                        by_code.setdefault(code, {})[field] = [_raw_cell(columns[field], row)]
        return dict(tables=[dict(thscode=code, table=columns) for code, columns in by_code.items()])
    projected = []
    for index,table in enumerate(record['snapshot'].get('tables',[])):
        selection = scope['tables'].get(index,{})
        rows = sorted(selection.get('rows',set()))
        fields = selection.get('fields',set())
        if not rows or not fields:
            continue
        columns = table['table']
        chosen = {}
        for field,value in columns.items():
            if field not in fields and field not in CODE_FIELDS+NAME_FIELDS:
                continue
            column = value if isinstance(value,list) else [value]
            if len(column)!=selection['row_count']:
                continue
            if text_only and field not in CODE_FIELDS+NAME_FIELDS and not any(
                    isinstance(column[row],str) and column[row].strip() not in ('','-','--','N/A','null')
                    and _number(column[row]) is None for row in rows):
                continue
            chosen[field] = [column[row] for row in rows]
        if not any(field not in CODE_FIELDS+NAME_FIELDS for field in chosen):
            continue
        item = dict(table=chosen)
        if 'thscode' in table:
            item['thscode'] = table['thscode']
        for name in ('time','date'):
            if isinstance(table.get(name),list) and len(table[name])==selection['row_count']:
                item[name] = [table[name][row] for row in rows]
        projected.append(item)
    return dict(tables=projected)


def _economic_scope(task, record, *, frequencies=None):
    """Keep only requested current-vintage economic cells, never infer a vintage.

    Unknown revision metadata does not remove an otherwise usable observation.
    An explicit later revision or an out-of-window date cannot supply a value;
    source bytes remain available in the workpaper.
    """
    parameters = record.get('parameters') or {}
    identifiers = parameters.get('indicators')
    requested = set(identifiers.split(',')) if isinstance(identifiers,str) else set()
    start = _quotation_day(parameters.get('startdate'))
    end = _quotation_day(parameters.get('enddate'))
    from .contracts import market_date
    try:cutoff = _quotation_day(market_date(task.get('cutoff')))
    except (ContractError,TypeError,ValueError):cutoff = None
    from .economic_observations import _period
    from .economic_sources import (direct_economic_readout, direct_economic_scopes, economic_period_reference,
                                   economic_reference_windows, projects_stated_periods, scoped_request_periods,
                                   stated_period_requirements)
    frequencies = economic_frequencies(task) if frequencies is None else frequencies
    reference = economic_period_reference(task)
    direct = (bool(reference) or direct_economic_readout(task)
              or (task.get('run_mode') == 'direct' and bool(direct_economic_scopes(task))))
    source_periods = {}
    if reference:
        code = reference['code']
        cadence = reference.get('frequency')
        if cadence and frequencies.get(code, cadence) == cadence:
            source_periods[code] = economic_reference_windows(reference)
    elif projects_stated_periods(task):
        records = {row['id']: row for row in task.get('source_records', [])
                   if row.get('operation') == 'edb_service' and _owned(task, row) and not _source_issue(task, row)}
        requirements = stated_period_requirements(task, records)
        if direct:
            source_periods = {code: windows for (code, _), (_, windows, _) in requirements.items()}
        else:
            source_periods = scoped_request_periods(task, record, requirements)
    def period_key(code, day):
        return (code,_period(day.isoformat(),frequencies.get(code)))
    selected,seen = {},Counter()
    rejected,rejected_tables,rejected_metadata = 0,0,0
    tables = (record.get('snapshot') or {}).get('tables',[])
    for index,table in enumerate(tables if isinstance(tables,list) else []):
        if not isinstance(table,dict):
            rejected_tables += 1
            continue
        codes = table.get('id')
        code = codes[0] if isinstance(codes,list) and len(codes)==1 else codes if isinstance(codes,str) else None
        dates,values = table.get('time'),table.get('value')
        if not isinstance(dates,list) or not isinstance(values,list) or len(dates)!=len(values):
            rejected += len(values) if isinstance(values,list) else 1
            rejected_tables += 1
            continue
        rows = set()
        names = table.get('index_name')
        names_aligned = isinstance(names,list) and len(names)==len(values)
        if names is not None and not names_aligned:rejected_metadata += 1
        revisions = table.get('rtime')
        revisions_aligned = isinstance(revisions,list) and len(revisions)==len(values)
        if revisions is not None and not revisions_aligned:rejected_metadata += 1
        for row,(raw_day,raw_value) in enumerate(zip(dates,values)):
            day = _quotation_day(raw_day)
            if (not isinstance(code,str) or code not in requested or not day or not start or not end or not cutoff
                    or not start<=day<=min(end,cutoff)):
                rejected += 1
                continue
            periods = source_periods.get(code)
            if (direct or periods is not None) and not any(first <= day <= last for first, last in (periods or ())):
                continue
            if _number(raw_value) is None and raw_value not in (None,'','-','--','N/A','null'):
                rejected += 1
                continue
            revision_day = None
            if revisions_aligned and revisions[row] not in (None,''):
                revision = revisions[row]
                try:
                    if isinstance(revision,str) and len(revision)==10:
                        revision_day = _quotation_day(revision)
                        if revision_day is None:raise ValueError
                    elif isinstance(revision,str) and len(revision)==19 and revision[10]==' ':
                        revision_day = datetime.strptime(revision,'%Y-%m-%d %H:%M:%S').date()
                    else:
                        revision_day = _quotation_day(market_date(revision))
                except (TypeError,ValueError):
                    rejected_metadata += 1
            if revision_day is not None and revision_day>cutoff:
                rejected += 1
                continue
            rows.add(row)
            seen[period_key(code,day)] += 1
        selected[index] = dict(rows=rows,fields={'value'},code=code,names_aligned=names_aligned)
    for index,choice in selected.items():
        rows={row for row in choice['rows'] if seen[period_key(choice['code'],_quotation_day(tables[index]['time'][row]))]==1}
        rejected += len(choice['rows'])-len(rows)
        choice['rows']=rows
    return dict(kind='economic_rows',tables=selected,rejected_cells=rejected,
        rejected_tables=rejected_tables,rejected_metadata=rejected_metadata)


def _economic_projection(record, scope):
    """Send adopted original values once; provider revision clocks stay in the workpaper."""
    result=[]
    for index,table in enumerate(record['snapshot'].get('tables',[])):
        choice=scope['tables'].get(index,{})
        rows=sorted(choice.get('rows',set()))
        if not rows:continue
        projected=dict(id=deepcopy(table['id']),time=[table['time'][row] for row in rows],
            value=[table['value'][row] for row in rows])
        if choice['names_aligned']:projected['index_name']=[table['index_name'][row] for row in rows]
        result.append(projected)
    return dict(tables=result)


def _observation_scope(task, record, *, close_calendars=None, latest_only=None):
    """Select raw cells under the saved quote, financial or Tinyshare policy.

    A bad subset does not block delivery. Quote dates and financial subjects
    use their own contracts; frozen tasks retain their original selection.
    """
    from .market_calendar import market_question_ranges

    if (_reader_at_least(task,10) and record.get('source')=='ifind'
            and record.get('operation')=='basic_data_service'):
        return _financial_scope(task,record)
    if (_reader_at_least(task,11) and record.get('source')=='ifind'
            and record.get('operation')=='edb_service'):
        return _economic_scope(task,record)
    if record.get('source') == 'tinyshare':
        from .tinyshare_sources import select_scope
        ranges = market_question_ranges(task, record) if record.get('operation') in ('daily', 'fund_daily') else None
        return select_scope(task, record, _number, question_ranges=ranges, close_calendars=close_calendars)
    version = task.get('draft_quote_scope_version')
    operation = record.get('operation')
    market = record.get('source') == 'ifind' and operation in MARKET_REFRESH_OPERATIONS
    if market and version is not None and (type(version) is not int or version not in (1, 2, SCOPE_VERSION)):
        raise ContractError('行情观测范围版本未登记')
    if market and market_cell_binding_enabled(task):
        return bound_ifind_market_scope(task, record, close_calendars=close_calendars, latest_only=latest_only,
                                        question_ranges=market_question_ranges(task, record))
    if record.get('source') == 'ifind' and operation == 'date_sequence' and partial_date_series_enabled(task):
        return draft_date_series_scope(task, record, latest_only=latest_only)
    if version is None or not market:
        return None
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
    from .market_series import daily_market_as_of, latest_observation_selected
    try:
        cutoff = _quotation_day(daily_market_as_of(task))
    except (ContractError, KeyError, ValueError, TypeError):
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
    if latest_only is None:
        latest_only = latest_observation_selected(task, record)
    omitted=0
    if latest_only and task['market_observation_selection_version'] >= 2:
        candidates = {}
        for index,choice in selected.items():
            if not choice['fields']:continue
            table=tables[index];columns=table['table']
            dates=table.get('time',[])
            for row,raw_day in enumerate(dates if isinstance(dates,list) else []):
                code=_quotation_code(table,columns,row);observed=day(raw_day)
                if not isinstance(code,str) or code not in codes or not observed or not start<=observed<=min(end,cutoff):continue
                for field in choice['fields']:
                    candidates.setdefault((code,field),[]).append(
                        (index,row,observed,_number(_raw_cell(columns[field], row)) is not None))
        keep=set()
        for (_,field),entries in candidates.items():
            available=[item[2] for item in entries if item[3]]
            latest=max(available or [item[2] for item in entries])
            keep.update((index,row,field) for index,row,observed,_ in entries if observed==latest)
        counts=Counter((_quotation_code(tables[index],tables[index]['table'],row),
                        day(tables[index]['time'][row]),field)
            for index,row,field in keep)
        for index,choice in selected.items():
            cells={}
            for field in choice['fields']:
                latest_rows={row for row in choice['rows'] if (index,row,field) in keep}
                rows={row for row in latest_rows if counts[(
                    _quotation_code(tables[index],tables[index]['table'],row),
                    day(tables[index]['time'][row]),field)]==1}
                # Older valid cells were intentionally omitted. Ambiguous
                # latest cells are a source issue, not an intentional omission.
                omitted+=len(choice['rows'])-len(latest_rows)
                rejected+=len(latest_rows)-len(rows)
                if rows:cells[field]=rows
            choice.update(cells=cells,fields=set(cells),
                rows=set().union(*cells.values()) if cells else set())
    elif latest_only:
        # Pending version-one tasks retain their original row selection. A
        # fresh task or explicit research update adopts per-field selection.
        candidates={}
        for index,choice in selected.items():
            if not choice['fields']:continue
            table=tables[index];columns=table['table'];dates=table.get('time',[])
            for row,raw_day in enumerate(dates if isinstance(dates,list) else []):
                code=_quotation_code(table,columns,row);observed=day(raw_day)
                if not isinstance(code,str) or code not in codes or not observed or not start<=observed<=min(end,cutoff):continue
                available=any(_number(_raw_cell(columns[field], row)) is not None for field in choice['fields'])
                candidates.setdefault(code,[]).append((index,row,observed,available))
        keep=set()
        for entries in candidates.values():
            available=[item[2] for item in entries if item[3]]
            latest=max(available or [item[2] for item in entries])
            keep.update((index,row) for index,row,observed,_ in entries if observed==latest)
        counts=Counter((_quotation_code(tables[index],tables[index]['table'],row),
                        day(tables[index]['time'][row]),field)
            for index,row in keep for field in selected[index]['fields'])
        for index,choice in selected.items():
            rows={row for row in choice['rows'] if (index,row) in keep}
            omitted+=(len(choice['rows'])-len(rows))*len(choice['fields'])
            choice['rows']=rows
            choice['fields']={field for field in choice['fields']
                if all(counts[(_quotation_code(tables[index],tables[index]['table'],row),
                    day(tables[index]['time'][row]),field)]==1 for row in rows)}
    result=dict(tables=selected,rejected_cells=rejected)
    if latest_only:result.update(omitted_cells=omitted,observation_mode='latest_available')
    if latest_only and close_calendars is not None and operation == 'cmd_history_quotation' and 'close' in fields:
        result['previous_close_cells'] = _previous_close_cells(task, record, selected, candidates, close_calendars)
    return result


def _previous_close_cells(task, record, selected, candidates, calendars):
    """Admit only an exact, same-response predecessor of a selected close."""
    from .evidence import validate_market_request
    from .market_calendar import previous_session_dates

    try:
        start, _ = validate_market_request(task, record['source'], record['operation'], record['parameters'],
                                          require_identity=False)
    except ContractError:
        return {}
    start = date.fromisoformat(start)
    tables = record['snapshot']['tables']
    cells = {}
    for (code, field), entries in candidates.items():
        if field != 'close':
            continue
        latest = [item for item in entries if item[1] in selected.get(item[0], {}).get('cells', {}).get(field, ())]
        if len(latest) != 1:
            continue
        table, row, day, _ = latest[0]
        if (_number(_raw_cell(tables[table]['table'][field], row)) or 0) <= 0:
            continue
        dates = previous_session_dates(code, start, day, calendars, observed_end=True)
        if dates is None:
            continue
        (previous, target), _ = dates
        previous_cells = [(index, offset) for index, offset, observed, _ in entries
                          if observed.isoformat() == previous]
        if len(previous_cells) != 1:
            continue
        index, offset = previous_cells[0]
        if (_number(_raw_cell(tables[index]['table'][field], offset)) or 0) > 0:
            cells[index, offset] = target
    return cells


def _observation_cell_selected(scope, table, field, row):
    selected=scope['tables'].get(table,{})
    if 'cells' in selected:
        return row in selected['cells'].get(field,set())
    return field in selected.get('fields',set()) and row in selected.get('rows',set())


def _observation_projection(record, scope):
    """Give models only selected original cells; stored snapshots stay intact."""
    if scope.get('kind') == 'economic_rows':
        return _economic_projection(record,scope)
    if scope.get('kind') == 'financial_rows':
        return _financial_projection(record,scope)
    if scope.get('kind') == 'tinyshare_records':
        from .tinyshare_sources import project_snapshot
        return project_snapshot(record, scope)
    tables = []
    for index, table in enumerate(record['snapshot'].get('tables', [])):
        selected = scope['tables'].get(index, {})
        if not selected.get('rows') or not selected.get('fields'):continue
        columns=table['table'];groups={}
        for field in columns:
            if field not in selected['fields']:continue
            rows=tuple(sorted(selected['cells'][field] if 'cells' in selected else selected['rows']))
            if rows:groups.setdefault(rows,set()).add(field)
        # Fields can have different latest dates. Keep their actual cells in
        # separate aligned tables rather than manufacturing missing values.
        for rows,fields in groups.items():
            projected={}
            for field in columns:
                if field not in fields and field not in CODE_FIELDS+NAME_FIELDS:continue
                values=columns[field] if isinstance(columns[field],list) else [columns[field]]
                if len(values)==len(table['time']):projected[field]=[values[row] for row in rows]
            item=dict(time=[table['time'][row] for row in rows],table=projected)
            if 'thscode' in table:item['thscode']=table['thscode']
            tables.append(item)
    return dict(tables=tables)


def source_issues(task):
    """Internal diagnostics distinguish a bad source from a bad subset."""
    result = []
    scopes, superseded = _updated_observation_scopes(task)
    for record in task.get('source_records', []) + task.get('materials', []):
        if record.get('id') in superseded:continue
        if not _owned(task, record):
            continue
        reason = _source_issue(task, record)
        scope = None if reason else scopes.get(record['id'])
        if not reason and scope is None:
            scope = _observation_scope(task, record)
        partial_series = (scope is not None and scope.get('kind') == 'series_rows'
                          and bool(record.get('series_contract', {}).get('draft_response_coverage')))
        partial = not reason and scope is not None and (scope['rejected_cells'] > 0 or scope.get('rejected_tables',0) > 0
                                                        or scope.get('rejected_metadata',0) > 0 or partial_series)
        if partial:
            reason = ('部分Tinyshare记录不对应本次标的、期间或字段，保留原始响应并使用其余可用观测。'
                if record.get('source') == 'tinyshare' else
                '部分财务字段的标的、报告期、报表类型或值无法核对，原响应保存在底稿。'
                if scope.get('kind')=='financial_rows' else
                '部分宏观记录的指标、区间或修订信息不适用，保留原始响应并使用其余可用数据。'
                if scope.get('kind')=='economic_rows' else
                '日期序列响应有异常，已按标的、日期和字段保留可用原值，完整响应可在底稿查看。'
                if scope.get('kind')=='series_rows' else
                '部分行情或日期序列记录不对应本次代码、日期或字段，保留原始响应并使用其余可用观测。')
            if scope.get('duplicate_cells'):
                reason += ('重复返回的财务字段未采用。' if scope.get('cell_bound') else
                           '同一标的、日期及字段的重复单元未采用。')
            if scope.get('refresh_conflicts'):
                reason += ('同版宏观读数存在冲突，相关统计期暂缺。' if scope.get('kind') == 'economic_rows'
                           else '同版财务原值存在冲突，相关字段暂缺。')
        if reason:
            issue = dict(artifact_id=record['id'], source=record.get('source'),
                         operation=record.get('operation'), reason=reason)
            if partial:
                issue['partial'] = True
            result.append(issue)
    if _reader_at_least(task,8):
        for code,metadata in _economic_metadata(task).items():
            reference=metadata.get('unit_reference',{})
            if reference.get('status')=='冲突':
                result.append(dict(artifact_id=reference['citations'][0]['artifact_id'],source='ifind',
                    operation='search_edb',partial=True,reason=code+'的指标目录单位不一致，保留数值，不选择单位。'))
    return result


def _number(value):
    if type(value) in (int, float):
        try:
            return value if math.isfinite(value) else None
        except OverflowError:
            return None
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
    modern=_reader_at_least(task,8)
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
        countries={item[0]['country'].strip() for item in items}
        row['country']=next(iter(countries)) if len(countries)==1 else ''
        if _reader_at_least(task,11):
            frequencies={item[0].get('freq') for item in items}
            row['freq']=next(iter(frequencies)) if len(frequencies)==1 else ''
            row['freq_conflict']=len(frequencies)>1
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
    if version is not None and (type(version) is not int or not 7 <= version <= VERSION):
        raise ContractError('原始数据读取版本未登记')
    return version


def _reader_at_least(task, minimum):
    return (_reader_version(task) or 0)>=minimum


def _financial_period(requested):
    """The explicit D argument is the report period, never response time."""
    if isinstance(requested, str) and re.fullmatch(r'\d{4}(0331|0630|0930|1231)', requested):
        try:
            date(int(requested[:4]), int(requested[4:6]), int(requested[6:]))
            return requested
        except ValueError:
            pass
    return ''


def _unit_definition_available(proof, cutoff):
    try:
        return timepoint(proof['basis']['obtained_at']) <= timepoint(cutoff)
    except (ContractError, KeyError, TypeError, ValueError):
        return False  # Unavailable metadata never discards the numeric cell.



def _superseded_snapshots(task):
    """Keep old originals, selecting a completed same-query refresh for new drafts.

    Only an actual current-version acquisition can replace an inherited
    snapshot. Conflicting responses acquired in this same version remain
    visible together; imports, failures and another query cannot replace it.
    """
    if not _reader_at_least(task,12) or not task.get('update_from') or task.get('research_mode')!='current':
        return {}
    from .evidence import current_query_reference
    records=task.get('source_records',[]);current={}
    market_refresh = market_refresh_enabled(task)
    financial_refresh = financial_refresh_enabled(task)
    economic_refresh = economic_refresh_enabled(task)
    def query(record):
        if (not _owned(task,record) or record.get('kind')!='api'
                or record.get('operation') not in DATA_OPERATIONS):return None
        if market_refresh and _market_refresh_record(record):
            return None  # Market refreshes replace addressed cells below.
        if financial_refresh and _financial_refresh_record(record):
            return None
        if economic_refresh and _economic_refresh_record(record):
            return None
        value=fingerprint(dict(source=record.get('source'),operation=record.get('operation'),parameters=record.get('parameters')))
        return value if value==record.get('request_hash') else None
    for record in records:
        key=query(record)
        if key is not None and current_query_reference(task,record) is not None:
            current.setdefault(key,[]).append(record['id'])
    prior=task['update_from'].get('version');superseded={}
    for record in records:
        version=record.get('retained_from_version');key=query(record)
        if (type(version) is int and type(prior) is int and 1<=version<=prior<task['version']
                and key in current and record['id'] not in current[key]):
            superseded[record['id']]=current[key]
    return superseded


def _current_update(task):
    prior = task.get('update_from')
    return (isinstance(prior, dict) and type(prior.get('version')) is int
            and prior['version'] == task['version'] - 1 and task.get('research_mode') == 'current')


def market_refresh_enabled(task):
    return _current_update(task) and _reader_at_least(task, 19)


def latest_market_refresh_enabled(task):
    return market_refresh_enabled(task) and _reader_at_least(task, 20)


def financial_refresh_enabled(task):
    return _current_update(task) and _reader_at_least(task, 22)


def economic_refresh_enabled(task):
    return _current_update(task) and _reader_at_least(task, 25)


def _economic_refresh_record(record):
    return (record.get('kind') == 'api' and record.get('source') == 'ifind'
            and record.get('operation') in ECONOMIC_REFRESH_OPERATIONS)


def _economic_refresh_receipt(task, record, history):
    from .evidence import current_query_reference

    version = record.get('retained_from_version', task['version'])
    if type(version) is not int or not 1 <= version <= task['version']:
        return None
    receipt = dict(version=version, snapshot_hash=record.get('hash'), request_hash=record.get('request_hash'))
    if version < task['version']:
        return receipt if isinstance(history, dict) and history.get(record['id']) == receipt else None
    return receipt if current_query_reference(task, record) is not None else None


def _retained_refreshes(task, frozen, load_version, *, key, is_source, make_receipt):
    """Carry verified acquisitions through a version without copying its ledger."""
    history = (frozen.get('update_from') or {}).get(key) or {}
    originals = {row['id']: row for row in frozen.get('source_records', []) if is_source(row)}
    archived, result = {}, {}
    for record in task.get('source_records', []):
        ident = record['id']
        if (not is_source(record) or not _owned(task, record)
                or originals.get(ident) != record or _source_issue(task, record)):
            continue
        receipt = make_receipt(task, record, history)
        origin = record.get('retained_from_version')
        acquisition = record.get('acquisition')
        if (receipt is None and type(origin) is int and 1 <= origin < task['version']
                and isinstance(acquisition, dict) and acquisition.get('research_version') == origin):
            if origin not in archived:
                archived[origin] = None, {}
                try:
                    previous = load_version(origin)
                    if (previous and previous.get('delivery') and previous.get('id') == task['id']
                            and previous.get('version') == origin):
                        archived[origin] = previous, {row['id']: row for row in previous['source_records']}
                except (ContractError, KeyError, TypeError, ValueError):
                    pass
            previous, rows = archived[origin]
            source = rows.get(ident)
            if (source is not None and is_source(source)
                    and all(source.get(key) == record.get(key) for key in ('hash', 'request_hash', 'acquisition'))):
                receipt = make_receipt(previous, source, {})
        if receipt is not None:
            result[ident] = receipt
    return result


def retained_economic_refreshes(task, frozen, load_version):
    return _retained_refreshes(task, frozen, load_version, key='economic_refreshes',
                               is_source=_economic_refresh_record, make_receipt=_economic_refresh_receipt)


def economic_frequencies(task):
    """Use a recorded frequency or a cadence explicitly linked to the code."""
    metadata = _economic_metadata(task)
    frequencies = {code: row.get('freq', '') for code, row in metadata.items()}
    if _reader_at_least(task, 25):
        from .economic_sources import (DRAFT_PERIOD_POLICY, draft_period_enabled, declared_economic_cadences,
                                        declared_economic_indicators, explicit_economic_cadence)
        if draft_period_enabled(task) and task[DRAFT_PERIOD_POLICY] >= 2:
            for code, cadence in declared_economic_cadences(task).items():
                frequencies.setdefault(code, cadence)
        else:
            declared = declared_economic_indicators(task)
            cadence = explicit_economic_cadence(task.get('question', ''))
            if len(declared) == 1 and cadence:
                frequencies.setdefault(declared[0], cadence)
    return frequencies


def _economic_refresh_scopes(task):
    """Replace only addressed periods backed by a completed query in a later version."""
    if not economic_refresh_enabled(task):
        return {}, {}
    records = [row for row in task.get('source_records', []) if _owned(task, row)
               and _economic_refresh_record(row) and not _source_issue(task, row)]
    if not any('retained_from_version' in row for row in records):
        return {}, {}
    from .economic_observations import _period
    frequencies = economic_frequencies(task)
    history = task['update_from'].get('economic_refreshes') or {}
    scopes, locations, latest = {}, {}, {}
    for record in records:
        ident = record['id']
        scope = scopes[ident] = _economic_scope(task, record, frequencies=frequencies)
        cells = locations[ident] = {}
        receipt = _economic_refresh_receipt(task, record, history)
        for table, selection in scope['tables'].items():
            original = record['snapshot']['tables'][table]
            code = selection['code']
            for row in sorted(selection['rows']):
                location = (table, row)
                address = (code, _period(original['time'][row], frequencies.get(code)))
                cells[location] = address
                if receipt is None:
                    continue
                version = receipt['version']
                raw = original['value'][row]
                number = None if raw in MISSING_CELLS else Decimal(str(raw).strip())
                current, candidates = latest.get(address, (0, []))
                if version > current:
                    latest[address] = (version, [(ident, location, number)])
                elif version == current:
                    candidates.append((ident, location, number))

    chosen = {}
    for address, (_, candidates) in latest.items():
        if len({value for _, _, value in candidates}) == 1:
            chosen[address] = candidates[0][:2]
        else:
            chosen[address] = None
            for ident, _, _ in candidates:
                scopes[ident]['rejected_cells'] += 1
                scopes[ident]['refresh_conflicts'] = True

    superseded = {}
    for ident, cells in locations.items():
        replacements, removed = set(), 0
        for location, address in cells.items():
            if address not in chosen or chosen[address] == (ident, location):
                continue
            table, row = location
            scopes[ident]['tables'][table]['rows'].remove(row)
            point = chosen[address]
            if point is not None:
                replacements.add(point[0])
                removed += 1
        if replacements and removed == len(cells):
            superseded[ident] = sorted(replacements)
    return scopes, superseded


def _financial_refresh_record(record):
    return (record.get('kind') == 'api' and record.get('source') == 'ifind'
            and record.get('operation') == 'basic_data_service')


def _financial_refresh_receipt(task, record, history):
    from .evidence import _current_api_reference
    from .financial_sources import validate_metadata_request

    version = record.get('retained_from_version', task['version'])
    if type(version) is not int or not 1 <= version <= task['version']:
        return None
    receipt = dict(version=version, snapshot_hash=record.get('hash'), request_hash=record.get('request_hash'))
    if version < task['version']:
        return receipt if isinstance(history, dict) and history.get(record['id']) == receipt else None
    # A partial draft response may supply raw cells. Formal fact availability
    # still uses the stricter whole-response contract in current_query_reference.
    try:
        reference = _current_api_reference(task, record, {'ifind.basic_data_service'}, 'query_request')
        if reference is None:
            return None
        validate_metadata_request(task, record['operation'], record['parameters'], before=record)
    except (ContractError, KeyError, TypeError, ValueError, OverflowError):
        return None
    return receipt


def retained_financial_refreshes(task, frozen, load_version):
    return _retained_refreshes(task, frozen, load_version, key='financial_refreshes',
                               is_source=_financial_refresh_record, make_receipt=_financial_refresh_receipt)


def _financial_refresh_scopes(task):
    if not financial_refresh_enabled(task):
        return {}, {}
    records = [record for record in task.get('source_records', []) if _financial_refresh_record(record)
               and _owned(task, record) and not _source_issue(task, record)]
    if not any('retained_from_version' in record for record in records):
        return {}, {}
    history = task['update_from'].get('financial_refreshes') or {}
    scopes, locations, authorities = {}, {}, {}
    for record in records:
        ident = record['id']
        scope = scopes[ident] = _financial_scope(task, record)
        locations[ident] = {}
        receipt = _financial_refresh_receipt(task, record, history)
        for table, selection in scope['tables'].items():
            columns = record['snapshot']['tables'][table]['table']
            for field, rows in selection['cells'].items():
                for row in rows:
                    location = (table, field, row)
                    address = (selection['codes'][row], field, tuple(scope['request_fields'][field]))
                    locations[ident][location] = address
                    if receipt is None:
                        continue
                    version = receipt['version']
                    current, candidates = authorities.get(address, (0, []))
                    value = fingerprint(_raw_cell(columns[field], row))
                    if version > current:
                        authorities[address] = (version, [(ident, location, value)])
                    elif version == current:
                        candidates.append((ident, location, value))

    selected = {}
    for address, (_, candidates) in authorities.items():
        if len({value for _, _, value in candidates}) == 1:
            selected[address] = candidates[0][:2]
        else:
            selected[address] = None
            for ident, _, _ in candidates:
                scope = scopes[ident]
                scope['rejected_cells'] += 1
                scope['refresh_conflicts'] = True

    superseded = {}
    for ident, cells in locations.items():
        replacements = set()
        removed = 0
        for location, address in cells.items():
            if address not in selected or selected[address] == (ident, location):
                continue
            table, field, row = location
            scopes[ident]['tables'][table]['cells'][field].remove(row)
            if selected[address] is not None:
                replacements.add(selected[address][0])
                removed += 1
        if replacements and removed == len(cells):
            superseded[ident] = sorted(replacements)
    return scopes, superseded


def _market_refresh_record(record):
    return (record.get('kind') == 'api' and record.get('source') == 'ifind'
            and record.get('operation') in MARKET_REFRESH_OPERATIONS)


def _market_refresh_receipt(task, record, history):
    from .evidence import current_query_reference
    from .market_series import latest_observation_selected

    version = record.get('retained_from_version', task['version'])
    if type(version) is not int:
        return None
    if version == task['version'] and current_query_reference(task, record):
        return dict(version=version, snapshot_hash=record['hash'], request_hash=record['request_hash'],
                    latest=latest_observation_selected(task, record))
    receipt = history.get(record['id']) if isinstance(history, dict) else None
    if (isinstance(receipt, dict) and 1 <= version < task['version']
            and receipt == dict(version=version, snapshot_hash=record.get('hash'),
                                request_hash=record.get('request_hash'), latest=receipt.get('latest'))
            and type(receipt['latest']) is bool):
        return receipt
    return None


def retained_market_refreshes(task, frozen):
    """Carry verified refreshes from delivered versions without retaining their ledgers."""
    history = (frozen.get('update_from') or {}).get('market_refreshes') or {}
    originals = {record['id']: record for record in frozen.get('source_records', [])
                 if _market_refresh_record(record)}
    result = {}
    for record in task.get('source_records', []):
        if (_market_refresh_record(record) and _owned(task, record)
                and originals.get(record['id']) == record and not _source_issue(task, record)):
            receipt = _market_refresh_receipt(task, record, history)
            if receipt is not None:
                result[record['id']] = receipt
    return result


def market_observation_basis(record, field):
    """Keep a field's full parameters when matching different request windows."""
    if not _market_refresh_record(record):
        return None
    params = record.get('parameters')
    if not isinstance(params, dict) or not isinstance(params.get('functionpara'), dict):
        return None
    keys = {'codes', 'startdate', 'enddate'}
    if record['operation'] == 'date_sequence':
        items = params.get('indipara')
        bindings = [item for item in items if isinstance(item, dict) and item.get('indicator') == field
                    and isinstance(item.get('indiparams'), list)] if isinstance(items, list) else []
        if len(bindings) != 1:
            return None
        field_parameters = bindings[0]
        keys.add('indipara')
    else:
        fields = params.get('indicators')
        if not isinstance(fields, str) or fields.split(',').count(field) != 1:
            return None
        field_parameters = None
        keys.add('indicators')
    return fingerprint(dict(operation=record['operation'], field=field_parameters,
        parameters={key: value for key, value in params.items() if key not in keys}))


def date_sequence_basis(record, field):
    """Compare matching requests, or retain the identity of one unbound original."""
    return market_observation_basis(record, field) or fingerprint(dict(source_ref=record['id']))


def _market_cell_address(record, index, field, row, basis):
    table = record['snapshot']['tables'][index]
    columns = table['table']
    code = _bound_market_code(table, columns, row)
    day = _sequence_day(table['time'][row])
    return (code, field, day.isoformat(), basis) if code and basis is not None and day is not None else None


def _market_locations(scope):
    for index, selection in scope['tables'].items():
        for field in selection['fields']:
            for row in selection.get('cells', {}).get(field, selection['rows']):
                yield index, field, row


def _market_addresses(record, scope, bases):
    addresses = {}
    tables = record['snapshot'].get('tables', ())
    for index, field, row in _market_locations(scope):
        raw = _raw_cell(tables[index]['table'][field], row)
        if _number(raw) is None and raw not in MISSING_CELLS:
            continue
        if field not in bases:
            bases[field] = market_observation_basis(record, field)
        addresses[index, field, row] = _market_cell_address(record, index, field, row, bases[field])
    return addresses


def _set_market_locations(scope, locations):
    tables = {}
    for index, field, row in locations:
        selection = tables.setdefault(index, dict(cells={}, fields=set(), rows=set()))
        selection['cells'].setdefault(field, set()).add(row)
        selection['fields'].add(field)
        selection['rows'].add(row)
    scope['tables'] = tables


def _market_refresh_scopes(task, *, close_calendars=None):
    """Apply newer verified cells to retained snapshots without changing their bytes."""
    if not market_refresh_enabled(task):
        return {}, {}
    records = [record for record in task.get('source_records', []) if _owned(task, record)
               and _market_refresh_record(record) and not _source_issue(task, record)]
    if not any('retained_from_version' in record for record in records):
        return {}, {}
    history = task['update_from'].get('market_refreshes') or {}
    receipts = {record['id']: receipt for record in records
                if (receipt := _market_refresh_receipt(task, record, history)) is not None}
    if not receipts:
        return {}, {}

    scopes, addresses, authority, bases = {}, {}, {}, {}
    refresh_latest = latest_market_refresh_enabled(task)
    for record in records:
        ident = record['id']
        receipt = receipts.get(ident)
        scope = _observation_scope(task, record, close_calendars=close_calendars,
                                   latest_only=receipt['latest'] if receipt else None)
        if scope is None:
            continue
        scopes[ident] = scope
        field_bases = bases[ident] = {}
        cells = addresses[ident] = _market_addresses(record, scope, field_bases)
        if not receipt:
            continue
        if refresh_latest and receipt['latest']:
            # A latest-only view hides other days in a valid response. Use
            # those cells only to correct the same addresses in earlier views.
            available = _observation_scope(task, record, latest_only=False)
            cells = _market_addresses(record, available, field_bases)
        version = receipt['version']
        counts = Counter(cells.values())
        for location, address in cells.items():
            if address is None or counts[address] != 1:
                continue
            previous_version, sources = authority.get(address, (0, {}))
            if version > previous_version:
                authority[address] = (version, {ident: location})
            elif version == previous_version:
                sources[ident] = location

    replaced, corrected = {}, {}
    prior = task['update_from']['version']
    for record in records:
        ident = record['id']
        version = record.get('retained_from_version')
        if ident not in scopes or type(version) is not int or not 1 <= version <= prior:
            continue
        replacements = replaced[ident] = {}
        for location, address in addresses[ident].items():
            source_version, sources = authority.get(address, (0, {}))
            if source_version <= version:
                continue
            replacements[location] = sources
            for source, offset in sources.items():
                if offset not in addresses[source]:
                    corrected.setdefault(source, set()).add(offset)

    superseded = {}
    for record in records:
        ident = record['id']
        if ident not in scopes:
            continue
        scope, cells = scopes[ident], addresses[ident]
        replacements = replaced.get(ident, {})
        corrections = corrected.get(ident, set())
        if replacements and len(replacements) == len(cells) and not corrections:
            superseded[ident] = sorted({source for sources in replacements.values() for source in sources})
            continue
        if replacements or corrections:
            _set_market_locations(scope, (set(_market_locations(scope)) - replacements.keys()) | corrections)
        if ident in replaced and scope.get('previous_close_cells'):
            version = record['retained_from_version']
            closes = {(address[0], address[2]) for (index, field, row), address in cells.items()
                      if field == 'close' and address and (index, field, row) not in replacements}
            previous = {}
            for (index, row), target in scope['previous_close_cells'].items():
                address = _market_cell_address(record, index, 'close', row, bases[ident].get('close'))
                if (address and (address[0], target) in closes
                        and authority.get(address, (0,))[0] <= version):
                    previous[index, row] = target
            scope['previous_close_cells'] = previous
    return scopes, superseded


def _updated_observation_scopes(task, *, close_calendars=None):
    market_scopes, market_superseded = _market_refresh_scopes(task, close_calendars=close_calendars)
    financial_scopes, financial_superseded = _financial_refresh_scopes(task)
    economic_scopes, economic_superseded = _economic_refresh_scopes(task)
    return (market_scopes | financial_scopes | economic_scopes,
            _superseded_snapshots(task) | market_superseded | financial_superseded | economic_superseded)


def _retained_market_interval_facts(task, facts):
    from .market_calendar import DAILY_CLOSE_RETURN_VERSIONS, QUOTE_ENDPOINT_POLICY, fund_share_requested, stated_close_intervals
    from .market_series import NAV_INDICATOR, stated_nav_intervals
    from .subject_scope import subject_key

    close_windows = stated_close_intervals(task)
    nav_windows = stated_nav_intervals(task)
    if not close_windows and not nav_windows:
        return facts
    windows = {'close': close_windows, NAV_INDICATOR: nav_windows, 'unit_nav': nav_windows}
    unlinked = {row['id'] for row in task.get('source_records', []) if not row.get('question_ids')}
    needs_shares = bool(nav_windows) and fund_share_requested(task)
    result = []
    for row in facts:
        if row['artifact_id'] in unlinked:
            if task.get(QUOTE_ENDPOINT_POLICY) in DAILY_CLOSE_RETURN_VERSIONS and row.get('previous_close_for'):
                result.append(row)
                continue
            code, field = subject_key(row['code']).upper(), row['field']
            if field == 'ths_fund_shares_fund' and code in nav_windows and not needs_shares:
                continue
            bounds = windows.get(field, {}).get(code)
            if bounds:
                day = _sequence_day(row['period'])
                if day is None or not any(first <= day <= last for first, last, _ in bounds):
                    continue
        result.append(row)
    # The raw response and the observation IDs remain available for audit.
    return result


def observations(task):
    """Normalize actual observations, not catalogue IDs or financial approval."""
    from .evidence import FIELD_REGISTRY, current_original_reference, current_query_reference
    from .intraday_quotes import retained_observations as retained_intraday
    from .etf_comparison import (CUSTODY as ETF_CUSTODY_FEE, MANAGEMENT as ETF_MANAGEMENT_FEE,
                                 comparison_scope as etf_comparison_scope, report_cells as etf_report_cells)
    from .etf_trading import CELL_KIND as ETF_TRADING_CELL, FIELDS as ETF_TRADING_FIELDS, source_cells
    from .financial_data import (cash_bridge_observation_cells, cash_bridge_reported_comparisons,
                                 matched_summary_comparison, retained_cash_bridge, stated_cash_bridge_scope)
    from .financial_sources import peer_financial_scope
    from .draft_filing_amounts import peer_filing_cells
    from .industry_chain import PERIOD_BASIS as INDUSTRY_CHAIN_PERIOD, bulletin_cells, study_scope
    from .monthly_statistics import (BASIS as NBS_MONTHLY_BASIS, bulletin_cells as nbs_monthly_cells,
                                     dated_pmi_scope, study_scope as monthly_scope)
    from .lpr_rates import (FIELD as LPR_FIELD, NAMES as LPR_NAMES, OPERATION as LPR_OPERATION,
                            cell_locator as lpr_locator, retained_cells as lpr_cells, study_scope as lpr_scope)
    from .nfra_bank_statistics import FIELD as NFRA_MARGIN, OPERATION as NFRA_STATISTICS, retained_cells as nfra_cells
    from .stock_quality import BASIS as STOCK_QUALITY_BASIS, filing_cells as quality_cells, study_scope as stock_scope
    from .event_market import (INDEX_NAMES as EVENT_INDEX_NAMES, MAINLAND_OPERATION as MAINLAND_EVENT_HISTORY,
                               OPERATION as EVENT_HISTORY, PERIOD_BASIS as EVENT_PERIOD_BASIS, ifind_parameters,
                               market_scopes, retained_rows)
    from .treasury_rates import (FIELD as H15_FIELD, NAMES as H15_NAMES, OPERATION as H15_YIELDS,
                                PERIOD_BASIS as H15_PERIOD_BASIS, cell_locator, retained_cells, study_scope as treasury_scope)
    from .index_sources import industry_operating_scope, selected_weight_rows, stated_weight_sheets
    from .market_calendar import retained_calendars
    from .market_series import SERIES_LABELS, OBSERVATION_POLICY, OBSERVATION_VERSION
    reader = _reader_version(task) or 0
    calibrated = evidence_definitions(2 if reader>=9 else 1)
    economic = _economic_metadata(task)
    facts = []
    previous_closes = []
    close_calendars = retained_calendars(task) if task.get(OBSERVATION_POLICY) == OBSERVATION_VERSION else None
    refresh_latest = latest_market_refresh_enabled(task)
    scopes, superseded = _updated_observation_scopes(task, close_calendars=close_calendars)
    weight_sheets = stated_weight_sheets(task)
    archive_cells = {record['id']: {tuple(cell[:3]) for cell in record['archive_reuse']['cells']}
                     for record in task.get('source_records', []) if record.get('archive_reuse')}
    bridge_scope = stated_cash_bridge_scope(task)
    peer_scope = peer_financial_scope(task)
    etf_scope = etf_comparison_scope(task)
    industry_scope = industry_operating_scope(task)
    chain_scope = study_scope(task)
    macro_scope = monthly_scope(task)
    pmi_scope = dated_pmi_scope(task)
    lpr = lpr_scope(task)
    quality_scope = stock_scope(task)
    channels_by_id = {record['id']: record['parameters']['channel'] for record in task.get('source_records', ())
                      if record.get('source') == 'official' and record.get('operation') == 'release_catalog'
                      and record.get('parameters', {}).get('channel')} if chain_scope else {}
    event_scopes = market_scopes(task)
    treasuries = treasury_scope(task)
    cash_bridge = retained_cash_bridge(task, bridge_scope,
        records=[row for row in task.get('source_records', ()) if row['id'] not in superseded
                 and row.get('kind') == 'filing' and _owned(task, row) and not _source_issue(task, row)])

    def append(record, code, field, label, period, raw, unit, locator, *, name='', role='time', basis=None,
               period_basis=None, unit_reference=None, previous_close_for=None):
        if not (subject_allowed(task, str(code or '')) and subject_allowed(task, str(name or ''))):
            return
        archived = archive_cells.get(record['id'])
        if archived is not None and (str(code), field, str(period)) not in archived:
            return
        # A number-shaped security identifier is never a numeric observation.
        value = _number(raw)
        if value is None and raw not in MISSING_CELLS:
            return
        result = dict(id='D' + str(len(facts) + 1), artifact_id=record['id'],
            source=record.get('source', ''), code=str(code or ''), field=field, label=label,
            period=str(period or ''), value=value, raw_value=raw, unit=unit or '原始单位待核对',
            unit_basis='来源字面口径' if unit else '接口未明确标注', locator=locator,
            status='原始取数', object_label=str(name or code or ''), axis_role=role,
            series_basis=fingerprint(dict(source=record.get('source'), operation=record.get('operation'), basis=basis)))
        metadata = definition(field)
        if metadata and metadata.get('period_kind') == 'point_in_time':
            result['period_kind'] = 'point_in_time'
        if reader>=8:
            reference=deepcopy(unit_reference)
            if reference is None and unit:
                reference=dict(kind='literal_source',artifact_id=record['id'],snapshot_hash=fingerprint(record.get('snapshot',record)),locator=deepcopy(locator))
            if reference:
                result['unit_reference']=reference
                result['unit_basis']=('指标目录冲突' if reference.get('status')=='冲突' else {
                    'source_metadata':'指标目录口径','calibrated_definition':'限定范围的原件校准',
                    'registered_field':'已登记字段口径','literal_source':'来源字面标注'}.get(reference.get('kind'),'接口未明确标注'))
        if (reader>=13 and unit=='元' and unit_reference and unit_reference.get('kind')=='registered_field'
                and unit_reference.get('family')=='price' and unit_reference.get('currency_parameter')=='RMB'):
            result['currency']='RMB'
        if period_basis is not None:
            result['period_basis'] = period_basis
            if metadata is not None or period_basis == '公告原文报告期间':
                result.setdefault('period_kind', 'cumulative')
        if previous_close_for is None:
            facts.append(result)
        else:
            result['previous_close_for'] = previous_close_for
            previous_closes.append(result)

    filing_scope = (_draft_original_scope(task)
                    if reader >= 15 and task.get('research_mode') == 'current' and task.get(DISCLOSURE_POLICY) == 1 else '')
    if filing_scope:
        from .draft_filing_amounts import filing_amounts, segment_revenue_cells, stated_segment_scope
        superseded.update(pdf_reparse_replacements(task))
        cutoff_day = market_date(task['cutoff'])
        segment_scope = stated_segment_scope(task)
    for record in task.get('source_records', []):
        if record.get('id') in superseded or not _owned(task, record) or _source_issue(task,record):
            continue
        origin = (record.get('source'), record.get('operation'))
        if origin in (('ifind', REALTIME), ('ifind', MINUTE)):
            if current_query_reference(task, record) is not None:
                for cell in retained_intraday(task, record):
                    field = 'minute_close' if origin[1] == MINUTE else 'latest'
                    count = len(facts)
                    append(record, cell['code'], field, LABELS[field], cell['local_time'], cell['value'], '',
                           cell['record_locator'], basis=dict(field=field, parameters=record['parameters']),
                           period_basis='交易市场当地时间')
                    if len(facts) > count:
                        facts[-1].update(source_operation=origin[1], source_timestamp=cell['quote_time'])
            continue
        if origin == ('official', LPR_OPERATION):
            if lpr:
                for tenor, day, raw, offset in lpr_cells(task, record, lpr):
                    append(record, 'LPR'+tenor, LPR_FIELD, LPR_NAMES[tenor], day, raw, '%',
                           lpr_locator(tenor, offset), name=LPR_NAMES[tenor], basis=dict(tenor=tenor),
                           period_basis='实际公布日')
            continue
        if origin == ('official', NFRA_STATISTICS):
            for cell in nfra_cells(task, record):
                append(record, '商业银行合计', NFRA_MARGIN, '商业银行净息差', cell['period'], cell['raw'], '%',
                       cell['locator'], name='商业银行合计', basis=dict(agency='金融监管总局', sample='商业银行合计'),
                       period_basis='季报表观测日期')
            continue
        if quality_scope and origin == ('official', 'release_original'):
            cells = quality_cells(task, record, quality_scope)
            for cell in cells:
                count = len(facts)
                unit_reference = dict(kind='literal_source', artifact_id=record['id'],
                                      snapshot_hash=record['snapshot_hash'], locator=cell['locator']['unit_basis'])
                append(record, cell['code'], cell['field'], cell['label'], cell['period'], cell['raw'],
                       cell['unit'], cell['locator'], name=quality_scope['name'], period_basis=STOCK_QUALITY_BASIS,
                       basis=dict(artifact_id=record['id'], field=cell['field']), unit_reference=unit_reference)
                if len(facts) > count:
                    facts[-1].update(source_operation=record['operation'], currency='RMB',
                                     raw_unit=cell['locator']['unit_basis']['scale'])
            if cells:
                continue
        if (macro_scope or pmi_scope) and origin == ('official', 'release_original'):
            for cell in nbs_monthly_cells(task, record, macro_scope or pmi_scope):
                append(record, cell['code'], cell['field'], cell['label'], cell['period'], cell['raw'],
                       cell['unit'], cell['locator'], name='中国', basis=dict(field=cell['field'], family='nbs_monthly'),
                       period_basis='国家统计局制造业PMI，经季节调整' if pmi_scope else NBS_MONTHLY_BASIS)
        if chain_scope and origin == ('official', 'release_original'):
            channel = channels_by_id.get(record.get('parameters', {}).get('catalog_id'))
            for cell in bulletin_cells(task, record, chain_scope, channel):
                count = len(facts)
                append(record, cell['code'], cell['field'], cell['label'], cell['period'], cell['raw'],
                       cell['unit'], cell['locator'], name=cell['code'], role='category',
                       basis=dict(agency=cell['basis'], field=cell['field']), period_basis=INDUSTRY_CHAIN_PERIOD)
                if len(facts) > count:
                    facts[-1].update(series_scope=cell['basis'], source_operation=record['operation'])
                    if cell['qualifier']:
                        facts[-1]['qualifier'] = cell['qualifier']
        if etf_scope and etf_scope.get('year') and origin == ('official', 'release_original'):
            code = record.get('snapshot', {}).get('official_origin', {}).get('code')
            if code in etf_scope['codes'] and current_original_reference(task, record):
                for cell in etf_report_cells(record, code, etf_scope['year']):
                    if etf_scope.get('fees_only') and cell['field'] not in (ETF_MANAGEMENT_FEE, ETF_CUSTODY_FEE):
                        continue
                    count = len(facts)
                    append(record, code, cell['field'], cell['label'], etf_scope['period'], cell['raw'],
                           cell['unit'], cell['locator'], name=cell['name'], role='category',
                           period_basis='基金中期报告',
                           basis=dict(artifact_id=record['id'], field=cell['field']))
                    if len(facts) > count:
                        facts[-1].update(source_operation='release_original', raw_unit=cell['unit'])
        if origin == ('official', H15_YIELDS):
            for code, day, value, offset in retained_cells(task, record, treasuries):
                append(record, code, H15_FIELD, H15_NAMES[code]+'收益率', day, value, '%', cell_locator(code, offset),
                       name=H15_NAMES[code], basis=dict(series=code, quotation='H.15名义固定期限收益率'),
                       period_basis=H15_PERIOD_BASIS)
            continue
        event_scope = None
        if origin == ('official', EVENT_HISTORY):
            event_scope = event_scopes.get('hong_kong')
        elif origin == ('official', MAINLAND_EVENT_HISTORY):
            event_scope = event_scopes.get('mainland')
        elif origin == ('ifind', 'cmd_history_quotation'):
            event_scope = next((scope for scope in event_scopes.values()
                                if record.get('parameters') == ifind_parameters(scope)), None)
        if event_scope or origin in (('official', EVENT_HISTORY), ('official', MAINLAND_EVENT_HISTORY)):
            for cell in retained_rows(task, record, event_scope):
                index = cell['code']
                append(record, index, 'close', '收盘点位', cell['date'], cell['close'], '点',
                       dict(table=cell['table'], row=cell['row'], field='close'), name=EVENT_INDEX_NAMES[index],
                       basis=dict(index_code=index, kind='price_index'), period_basis=EVENT_PERIOD_BASIS[index])
            continue
        if peer_scope and (record.get('source'), record.get('operation')) in (
                ('ifind', 'filing_original'), ('official', 'release_original')):
            from .financial_sources import PEER_PROFIT_DETAIL_VERSION, PEER_PROFIT_POLICY
            from .source_catalogue import disclosure_fields

            disclosure = disclosure_fields(record)
            if disclosure.get('issuer_code') in peer_scope['codes'] and current_original_reference(task, record):
                peer_cells = peer_filing_cells(record, peer_scope['periods'],
                                               revenue_bridge=peer_scope.get('revenue_bridge', False),
                                               bank_profit_bridge=peer_scope.get('bank_profit_bridge', False),
                                               operating_drivers=peer_scope.get('operating_drivers', False),
                                               profit_details=task.get(PEER_PROFIT_POLICY) == PEER_PROFIT_DETAIL_VERSION)
                for cell in peer_cells:
                    count = len(facts)
                    unit = '人民币元' if cell['unit'] == '元' else cell['unit']
                    unit_reference = dict(kind='literal_source', artifact_id=record['id'],
                                          snapshot_hash=record.get('snapshot_hash', record['hash']),
                                          locator=cell['locator']['unit_basis'])
                    append(record, cell['code'], cell['field'], cell['label'], cell['period'],
                           str(cell['value']), unit, cell['locator'], name=cell['issuer'],
                           period_basis='公告原文报告期间', basis=dict(artifact_id=record['id'], field=cell['field']),
                           unit_reference=unit_reference)
                    if len(facts) > count:
                        facts[-1].update(source_operation=record['operation'], currency='RMB', raw_unit=cell['unit'])
                if peer_cells:
                    continue  # Keep one pair per metric from a selected half-year statement.
        if filing_scope and record.get('kind') == 'filing':
            directory_day = _sequence_day(record.get('directory', {}).get('report_date'))
            if directory_day is None or directory_day.isoformat() > cutoff_day:
                continue
            comparables = []
            if segment_scope and current_original_reference(task, record):
                comparables = segment_revenue_cells(record, segment_scope)
            covered_segments = {(cell['field'], cell['period']) for cell in comparables}
            cells = filing_amounts(record, filing_scope, reader_version=reader)
            for cell in comparables + [cell for cell in cells if (cell['field'], cell['period']) not in covered_segments]:
                if cell['period'].split('/')[-1] > cutoff_day:
                    continue
                count = len(facts)
                unit = '人民币元' if cell['unit'] == '元' else cell['unit']
                unit_reference = dict(kind='literal_source', artifact_id=record['id'], snapshot_hash=record['hash'],
                                      locator=cell['locator']['unit_basis'])
                append(record, cell['code'], cell['field'], cell['label'], cell['period'].replace('/', '至'),
                       str(cell['value']), unit, cell['locator'], name=cell['issuer'], period_basis='公告原文报告期间',
                       basis=dict(artifact_id=record['id'], field=cell['field']), unit_reference=unit_reference)
                if len(facts) > count:
                    facts[-1].update(source_operation='filing_original', currency='RMB', raw_unit=cell['unit'])
            continue
        scope = scopes.get(record['id'])
        if scope is None:
            scope = _observation_scope(task, record, close_calendars=close_calendars)
        bound_financial = scope is not None and scope.get('cell_bound')
        if scope is not None and scope.get('kind') == 'tinyshare_records':
            from .tinyshare_sources import selected_cells
            for cell in selected_cells(record,scope):
                count = len(facts)
                append(record,**cell)
                if len(facts)>count:
                    facts[-1]['source_operation']=record['operation']
            continue
        if record.get('operation') not in DATA_OPERATIONS:
            continue
        snapshot, params = record.get('snapshot') or {}, record.get('parameters') or {}
        if not isinstance(snapshot, dict):
            continue
        operation = record.get('operation')
        if operation == 'index_file' and industry_scope and not weight_sheets and snapshot.get('index_code') == industry_scope['index_code']:
            continue  # The selection file is source context, not a company reporting date.
        batch_columns = None
        if operation == 'smart_stock_picking':
            contract = record.get('financial_query_contract') or {}
            if contract.get('query_kind') == 'object_discovery':
                continue
            if reader >= FINANCIAL_BATCH_COLUMNS_READER and task.get('financial_batch_query_version') == 1:
                from .financial_sources import financial_batch_response
                try:
                    batch = financial_batch_response(task, params, snapshot)
                except (ContractError, KeyError, TypeError, ValueError):
                    batch = None
                if batch:
                    batch_columns = {name for names in batch['matched_columns'].values() for name in names}
                elif contract.get('query_kind') == 'financial_batch':
                    continue
        if operation == 'index_file':
            parsed = snapshot.get('parsed') or {}
            if snapshot.get('role') == 'weights' and isinstance(parsed.get('members'), list):
                ordered = (selected_weight_rows(weight_sheets, record['id']).items()
                           if weight_sheets else enumerate(parsed['members']))
                for index, row in ordered:
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
                if scope is not None:
                    choice=scope['tables'].get(table_index,{})
                    if not choice.get('rows'):continue
                    code=choice['code']
                else:
                    code = _at(codes, 0) if isinstance(codes, list) else codes
                meta = economic.get(code, {})
                values = table.get('value', [])
                for index, value in enumerate(values if isinstance(values, list) else []):
                    if scope is not None and not _observation_cell_selected(scope,table_index,'value',index):
                        continue
                    names = table.get('index_name')
                    if scope is not None and not scope['tables'][table_index].get('names_aligned'):
                        names = None
                    append(record, code, 'value', _at(names, index, meta.get('indexName', code)),
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
                if field in CODE_FIELDS + NAME_FIELDS or (batch_columns is not None and field not in batch_columns):
                    continue
                values = values if isinstance(values, list) else [values]
                period, report_type = field_periods.get(field, [params.get('report_period', ''), params.get('statement_type', '')])
                explicit_period = re.search(r'\[(\d{8})\]', field)
                if not period and explicit_period:
                    period = explicit_period[1]
                metadata = definition(field)
                basis = dict(functionpara=params.get('functionpara'), statement_type=report_type)
                if reader >= 24 and operation == 'date_sequence' and record.get('source') == 'ifind':
                    basis = date_sequence_basis(record, field)
                for index, raw in enumerate(values):
                    selected = scope is None or _observation_cell_selected(scope, table_index, field, index)
                    close_target = (scope.get('previous_close_cells', {}).get((table_index, index))
                                    if scope is not None and field == 'close' else None)
                    if not selected and close_target is None:
                        continue
                    if bound_financial:
                        code = scope['tables'][table_index]['codes'][index]
                        name = ''
                    else:
                        code = next((_at(columns.get(key), index) for key in CODE_FIELDS if _at(columns.get(key), index)),
                                    table.get('thscode', params.get('codes', '')))
                        name = next((_at(columns.get(key), index) for key in NAME_FIELDS if _at(columns.get(key), index)), '')
                    candidates = [proof for proof in calibrated.values()
                        if proof['claims']['indicator'] == field and code in proof['claims']['applicable_codes']
                        and period in proof['claims']['applicable_periods'] and str(report_type) == proof['claims']['report_type']]
                    if reader>=8:
                        candidates = [proof for proof in candidates
                            if operation=='basic_data_service' and record.get('source')=='ifind'
                            and _unit_definition_available(proof, task.get('cutoff'))]
                    # Retain the selected proof, not a lookup by indicator alone:
                    # the same field can have separately reviewed period scopes.
                    proof = candidates[0] if len(candidates)==1 else None
                    known = proof['claims'] if proof else None
                    unit = known['unit'] if known else ''
                    unit_reference=None
                    if reader>=8 and known:
                        unit_reference=dict(kind='calibrated_definition',definition_hash=fingerprint(proof),
                            source=proof['basis']['source'],locator=proof['basis']['locator'],
                            basis_hash=proof['basis']['hash'],available_at=proof['basis']['obtained_at'],scope=dict(codes=known['applicable_codes'],
                                periods=known['applicable_periods'],report_type=known['report_type']))
                    quote_registry = FIELD_REGISTRY.get('ifind.' + str(operation), {}) if reader<8 or record.get('source')=='ifind' else {}
                    family = next((key for key, pattern in quote_registry.get('instrument_namespaces', {}).items()
                                   if re.fullmatch(pattern, str(code))), None)
                    registered = quote_registry.get('field_families', {}).get(family, {}).get(field, {})
                    if registered and params.get('functionpara', {}).get('Currency') == 'RMB':
                        unit = registered.get('raw_unit', '')
                        if reader>=8 and unit:
                            unit_reference=dict(kind='registered_field',registry='ifind.'+str(operation),
                                family=family,field=field,definition_hash=fingerprint(registered),currency_parameter='RMB')
                    label = metadata['name'] if metadata else SERIES_LABELS.get(field, LABELS.get(field, field))
                    if explicit_period and label == field:
                        label = label.replace(explicit_period[0], '')
                    if field in ('open', 'high', 'low', 'close') and family == 'index_level':
                        label = label.replace('价', '点位')
                    observed_period = _at(table.get('time', table.get('date')), index, period)
                    period_basis = None
                    if reader >= 7 and operation == 'basic_data_service' and (metadata is not None or bound_financial):
                        observed_period = _financial_period(period)
                        period_basis = '请求D报告期' if observed_period else '报告期未明确'
                    count = len(facts)
                    append(record, code, field, label, observed_period,
                        raw, unit, dict(table=table_index, field=field, row=index), name=name,
                        role='cross_section' if operation == 'smart_stock_picking' or (
                            operation == 'basic_data_service' and ',' in str(params.get('codes', ''))) else 'time',
                        basis=basis, period_basis=period_basis,
                        unit_reference=unit_reference, previous_close_for=None if selected else close_target)
                    if refresh_latest and close_target and len(facts) > count:
                        facts[-1]['previous_close_target'] = close_target
    for row in previous_closes:
        row['id'] = 'D' + str(len(facts) + 1)
        facts.append(row)
    if cash_bridge:
        record, bridge = cash_bridge
        sources = {row['id']: row for row in task['source_records']}
        reported = cash_bridge_reported_comparisons(record)
        for field, proof in reported.items():
            addresses = {(bridge_scope['code'], field, period, '1') for period in bridge_scope['periods']}
            rows = sorted((row for row in facts if row['field'] == field and
                _financial_cell_address(task, row, sources.get(row['artifact_id'])) in addresses),
                key=lambda row: row['period'])
            if len(rows) == 2 and [row['period'] for row in rows] == bridge_scope['periods']:
                paired = [dict(row, filing_comparison=proof) for row in rows]
                if matched_summary_comparison(*paired, sources):
                    for row in rows:
                        row['filing_comparison'] = deepcopy(proof)
        for cell in cash_bridge_observation_cells(record, bridge):
            append(record, **cell)
    if etf_scope and etf_scope.get('trading'):
        for record, code, name, field, day, raw, index in source_cells(task, etf_scope):
            if record['id'] in superseded or not _owned(task, record) or _source_issue(task, record):
                continue
            label, unit = ETF_TRADING_FIELDS[field]
            before = len(facts)
            append(record, code, field, label, day, raw, unit,
                dict(kind=ETF_TRADING_CELL, row=index, field=field), name=name,
                basis=dict(code=code, field=field, currency='RMB'), period_basis='交易所同日披露')
            if len(facts) > before:
                facts[-1].update(currency='RMB', raw_unit=unit, source_operation=record['operation'])
    if facts and (task.get('run_mode') == 'local_draft' or local_sources_only(task)):
        return _retained_market_interval_facts(task, facts)
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
    from .index_sources import selected_weight_rows, stated_weight_sheets

    weight_sheets = stated_weight_sheets(task)
    hidden = META_OPERATIONS | CATALOGUE_OPERATIONS if historical_original_scope(task) else META_OPERATIONS
    records = [row for row in task.get('source_records', []) + task.get('materials', [])
               if _owned(task, row) and row.get('operation') not in hidden]
    # Business responses precede navigation metadata in report context.
    records.sort(key=lambda row: row.get('operation') not in DATA_OPERATIONS
                 and not row.get('parts') and row.get('kind') != 'filing')
    scopes, superseded = _updated_observation_scopes(task)
    superseded.update((old, [new]) for old, new in pdf_reparse_replacements(task).items())
    share = max(1, limit // max(1, sum(record['id'] not in superseded and _source_issue(task,record) is None for record in records)))
    result = []
    for record in records:
        if record['id'] in superseded:
            result.append(dict(id=record['id'],source=record.get('source'),operation=record.get('operation'),
                parameters=record.get('parameters'),source_status=record.get('status'),content='',
                superseded_by=superseded[record['id']],truncated=False,original_characters=0,raw_binary_omitted=True))
            if 'retained_from_version' in record:
                result[-1]['retained_from_version'] = record['retained_from_version']
            continue
        reason = _source_issue(task,record)
        if reason:
            result.append(dict(id=record['id'],source=record.get('source'),operation=record.get('operation'),
                source_status=record.get('status'),parameters=record.get('parameters'),content='',
                excluded_reason=reason,truncated=False,original_characters=0,raw_binary_omitted=True))
            continue
        snapshot = record.get('snapshot', record)
        scope = scopes.get(record['id'])
        if scope is None:
            scope = _observation_scope(task, record)
        if scope is not None:
            snapshot = _observation_projection(record, scope)
        if (weight_sheets and record.get('operation') == 'index_file'
                and isinstance(snapshot, dict) and snapshot.get('role') == 'weights'):
            selected = selected_weight_rows(weight_sheets, record['id']).values()
            matching = [sheet for sheet in weight_sheets if sheet['source_ref'] == record['id']]
            scope = matching[0] if matching else next(
                (sheet for sheet in weight_sheets if sheet['code'] == snapshot.get('index_code')), {})
            counts = [sheet['count'] for sheet in matching] or [scope.get('count')]
            snapshot = dict(index_code=scope.get('code', snapshot.get('index_code')),
                            requested_day=scope.get('requested_day'),
                            observation_date=scope.get('day') if selected else None,
                            requested_count=None if None in counts else max(counts), members=[
                                dict(code=row['code'], name=row['name'], observation_date=row['observation_date'],
                                     weight=row['weight'], unit='%') for row in selected])
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
        if 'retained_from_version' in record:
            result[-1]['retained_from_version']=record['retained_from_version']
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
                if (record.get('source') == 'ifind' and notice.get('additional_fields')
                        and (record.get('operation') == 'edb_service' and _reader_at_least(task, 11)
                             or record.get('operation') == 'basic_data_service' and _reader_at_least(task, 21))):
                    notice['excluded_fields']=notice.pop('additional_fields')
                notices.append(notice)
    if not notices:
        return {}
    rule=('附带字段可按相关性用于分析，字段请求不明确的资料按实际原件使用。'
        '列名出现不等于对象、期间、单位或研究需求已满足；缺列与空值分别保留，不将空值当零。'
        '这些提示不阻断出稿，也不自动要求重新取数。')
    if any(notice.get('excluded_fields') for notice in notices):
        rule += 'excluded_fields仅说明原响应额外返回的指标，未提供其可用数值，不用于本次分析或自动扩大范围。'
    return dict(notices=notices,rule=rule)


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



def _query_only_request(task, request, matching):
    """Only an explicit typed query-only question can finish at navigation."""
    refs=request.get('question_ids') or list(dict.fromkeys(ident for row in matching for ident in row.get('question_ids',[])))
    known={row['id']:row for row in task.get('questions',[]) if isinstance(row,dict) and isinstance(row.get('id'),str)}
    if not refs or any(ident not in known for ident in refs):return False
    for ident in refs:
        question=known[ident]
        if question.get('answer_scope')=='general_definition':continue
        needs=question.get('evidence_needs',[]);scopes=question.get('evidence_need_scopes',{})
        if (question.get('kind')!='fact' or not needs or question.get('method_needs')
                or any(scopes.get(need)!='query_provenance' for need in needs)):
            return False
    return True


def acquisition_coverage(task, requests=None, *, facts=None, include_retained_economic=False,
                         include_economic_query_windows=False):
    """Compare requested raw cells with actual owned observations, without gating delivery.

    A successful response or one unrelated number does not complete the data
    plan. Later compatible requests can fill earlier missing cells. Units and
    investment applicability remain separate from raw acquisition coverage.
    """
    binding = dict(task_id=task['id'], research_version=task['version'], input_hash=task.get('input_hash'))
    records = {row['id']: row for row in task.get('source_records', [])
               if _owned(task, row) and isinstance(row.get('id'), str) and not _source_issue(task, row)}
    track_quote_sessions = _reader_at_least(task, 26)
    admitted_quote_ids = set()
    requests_from_attempts = requests is None
    admitted_period_ids = set()
    if requests is None:
        attempts = task.get('draft_source_attempts', [])
        owned_ids = {ident for row in records.values() for ident in row.get('data_need_ids', [])}
        admitted_period_ids.update(owned_ids)
        if isinstance(attempts, list):
            admitted_period_ids.update(row['encoded']['id'] for row in attempts
                if isinstance(row, dict) and row.get('binding') == binding and row.get('status') in ('已提交','未取得')
                and isinstance(row.get('encoded'), dict) and isinstance(row['encoded'].get('id'), str))
        requests = [row.get('encoded') if isinstance(row.get('encoded'),dict) else row.get('proposed')
            for row in attempts if isinstance(row, dict)
            and (row.get('binding')==binding or (row.get('binding') is None
                and isinstance(row.get('proposed'),dict) and row['proposed'].get('id') in owned_ids))] if isinstance(attempts,list) else []
        if track_quote_sessions and isinstance(attempts, list):
            admitted_quote_ids = {row['encoded']['id'] for row in attempts
                if isinstance(row, dict) and row.get('binding') == binding and row.get('status') in ('已提交','未取得')
                and isinstance(row.get('encoded'), dict) and isinstance(row['encoded'].get('id'), str)
                and (row['encoded'].get('source'), row['encoded'].get('operation')) == ('ifind','cmd_history_quotation')}
    elif track_quote_sessions and isinstance(requests, list):
        admitted_quote_ids = {request['id'] for request in requests if isinstance(request, dict)
                              and isinstance(request.get('id'), str)}
    selected = {}
    for request in requests if isinstance(requests, list) else []:
        if (isinstance(request, dict) and isinstance(request.get('id'), str)
                and isinstance(request.get('source'), str) and isinstance(request.get('operation'), str)
                and isinstance(request.get('parameters'), dict)):
            selected[request['id']] = request
    if include_retained_economic and economic_refresh_enabled(task):
        history = task['update_from'].get('economic_refreshes')
        windows = {fingerprint((request['parameters'], sorted(request.get('question_ids') or ())))
                   for request in selected.values()
                   if request['source'] == 'ifind' and request['operation'] == 'edb_service'}
        for record in records.values():
            if ('retained_from_version' not in record or not _economic_refresh_record(record)
                    or _economic_refresh_receipt(task, record, history) is None or not record.get('question_ids')):
                continue
            params, refs = record['parameters'], sorted(record['question_ids'])
            window_key = fingerprint((params, refs))
            if window_key not in windows:
                ident = 'retained_edb_' + window_key[:24]
                selected[ident] = dict(id=ident, source='ifind', operation='edb_service',
                                       parameters=params, question_ids=refs)
                admitted_period_ids.add(ident)
                windows.add(window_key)
    facts = [row for row in (observations(task) if facts is None else facts) if row.get('value') is not None
             and row.get('artifact_id') in records and not row.get('previous_close_for')]
    result = dict(version=1, binding=binding, scope='recorded_requests_only', research_sufficiency_approved=False,
        evaluated_requests=len(selected), complete_request_ids=[], unresolved=[], needs_followup=False,
        blocks_report=False, financial_approval=False, unit_approval=False)
    navigation_policy=_reader_at_least(task,14)
    local_originals = historical_originals_local_only(task)
    if navigation_policy:
        result.update(version=2,navigation_request_ids=[],completed_discovery_request_ids=[],
            navigation_rule='候选与目录只完成查询，按原问题选择实际数据；不重复检索，不要求取遍候选。明确的纯目录问题可直接作答。')
    from .economic_sources import (draft_period_gaps, draft_period_windows, scoped_request_periods,
                                   stated_period_coverage, stated_period_requirements)
    period_requirements = stated_period_requirements(task, records)
    calendars = None
    quote_windows = {}
    for ident, request in selected.items():
        params = request['parameters'];operation = request['operation'];targets = []
        codes = params.get('codes', '')
        codes = codes.split(',') if isinstance(codes, str) else codes if isinstance(codes, list) else []
        fields = params.get('indicators', [])
        fields = fields.split(',') if isinstance(fields, str) else fields if isinstance(fields, list) else []
        period = str(params.get('report_period', ''))
        statement = str(params.get('statement_type', ''))
        from .tinyshare_sources import enabled as tinyshare_enabled, request_targets as tinyshare_targets
        tinyshare = request['source']=='tinyshare' and tinyshare_enabled(task)
        if tinyshare:
            targets = tinyshare_targets(request)
        elif operation in ('basic_data_service', 'date_sequence'):
            items = params.get('indipara', [])
            definitions = [(item.get('indicator'), item.get('indiparams', [])) for item in items
                           if isinstance(item, dict) and isinstance(item.get('indiparams', []), list)] if isinstance(items,list) else []
            if not definitions:definitions = [(field, []) for field in fields]
            for code in codes:
                for field, arguments in definitions:
                    if not isinstance(field, str):continue
                    target = dict(code=code, field=field)
                    if operation == 'basic_data_service':
                        target.update(period=(str(arguments[0]) if arguments else period),
                                      statement=(str(arguments[1]) if len(arguments)>1 else statement))
                    targets.append(target)
        elif operation == 'cmd_history_quotation':
            targets = [dict(code=code, field=field) for code in codes for field in fields if isinstance(field, str)]
        elif operation == 'edb_service':
            targets = [dict(code=code, field='value') for code in fields if isinstance(code, str)]
        matching = [row for row in records.values() if ident in row.get('data_need_ids', [])
                    and row.get('source') == request['source'] and row.get('operation') == operation]
        economic_windows, scoped_periods = {}, {}
        if request['source'] == 'ifind' and operation == 'edb_service':
            economic_windows = draft_period_windows(task, request)
            scoped_periods = scoped_request_periods(task, request, period_requirements)
        from .market_calendar import market_date_ranges, market_question_ranges
        question_ranges = (market_question_ranges(task, request)
                           if operation in MARKET_REFRESH_OPERATIONS or tinyshare and operation in ('daily', 'fund_daily')
                           else {})
        quote_days = {}
        if (ident in admitted_quote_ids and request['source'] == 'ifind' and operation == 'cmd_history_quotation'
                and targets and 'observation_scope' not in request and params.get('startdate') != params.get('enddate')):
            from .evidence import validate_market_request
            from .market_calendar import retained_calendars, verified_session_window
            try:
                start, end = (date.fromisoformat(day) for day in
                              validate_market_request(task, 'ifind', operation, params))
            except (ContractError, KeyError, TypeError, ValueError):
                pass  # An unadmitted request cannot describe missing exchange sessions.
            else:
                if calendars is None:
                    calendars = retained_calendars(task)
                for code in codes:
                    key = code.rsplit('.', 1)[-1], start, end
                    if key not in quote_windows:
                        quote_windows[key] = verified_session_window(code, start, end, calendars)
                    if quote_windows[key] is not None:
                        quote_days[code] = quote_windows[key]
        question_ids = set(request.get('question_ids') or ()) if quote_days else set()
        missing = []
        for target in targets:
            intervals = market_date_ranges(question_ranges, target['code'], target['field'])
            if intervals == ():
                continue  # An extra source field is outside this question's requested measures.
            window, linked_questions = economic_windows.get(target['code'], (None, question_ids))
            found = []
            for row in facts:
                record = records[row['artifact_id']]
                if (record.get('source') != request['source'] or record.get('operation') != operation
                        or row.get('code') != target['code'] or row.get('field') != target['field']):continue
                if ((window is not None or target['code'] in quote_days)
                        and not linked_questions.issubset(record.get('question_ids', []))):
                    continue
                if operation == 'basic_data_service':
                    actual = next((item.get('indiparams', []) for item in record.get('parameters', {}).get('indipara', [])
                                   if isinstance(item, dict) and item.get('indicator') == target['field']), [])
                    if (str(row.get('period', '')).replace('-', '') != target.get('period', '').replace('-', '')
                            or len(actual)<2 or str(actual[1]) != target['statement']):continue
                elif tinyshare and 'period' in target:
                    if (row.get('period') != target['period']
                            or record.get('parameters',{}).get('report_type') != target['statement']):continue
                else:
                    day = str(row.get('period', '')).replace('-', '')
                    start = str(params.get('start_date' if tinyshare else 'startdate', '')).replace('-', '')
                    end = str(params.get('end_date' if tinyshare else 'enddate', '')).replace('-', '')
                    if (start and day<start) or (end and day>end):continue
                    if intervals is not None:
                        observed = _sequence_day(row['period'])
                        if observed is None or not any(first <= observed <= last for first, last in intervals):
                            continue
                    if operation in ('cmd_history_quotation','date_sequence') and record.get('parameters', {}).get('functionpara', {}) != params.get('functionpara', {}):continue
                if row.get('value') is not None or operation != 'cmd_history_quotation' or target['field'] not in ('volume', 'amount'):
                    found.append(row)
            gaps = (draft_period_gaps(window, found, requested_periods=scoped_periods.get(target['code'], ()))
                    if window is not None else {})
            sessions = quote_days.get(target['code'])
            if sessions is not None and intervals is not None:
                sessions = tuple(day for day in sessions if any(first <= day <= last for first, last in intervals))
            if sessions is not None:
                dates = sorted(set(sessions) - {_sequence_day(row['period']) for row in found})
                if dates:
                    gaps.update(missing_dates=[day.isoformat() for day in dates[:12]], missing_date_count=len(dates))
            # An independently verified empty exchange window expects no daily cells.
            if gaps or (window is None and not found and sessions != ()):
                missing.append(dict(target, **gaps))
        discovery=False
        if navigation_policy and request['source']=='ifind' and operation=='smart_stock_picking':
            from .object_discovery import discovery_request,discovery_response
            try:discovery=discovery_request(task,params) is not None
            except ContractError:discovery=True
        if discovery:
            complete=False
            for record in matching:
                if record.get('parameters')!=params:continue
                try:discovery_response(task,params,record['snapshot'])
                except (ContractError,KeyError,TypeError,ValueError):continue
                complete=True;break
        elif targets:
            complete = not missing
        elif operation in DATA_OPERATIONS:
            complete = any(row.get('artifact_id') in {record['id'] for record in matching} for row in facts)
        else:
            complete = bool(matching)
        if complete:
            result['complete_request_ids'].append(ident)
            if navigation_policy:
                if discovery:result['completed_discovery_request_ids'].append(ident)
                navigation=discovery or operation in {'get_limit_policy','get_indicators','get_indicator_params',
                    'get_thscode','search_edb','report_query','release_catalog'}
                if (navigation and not (local_originals and operation in CATALOGUE_OPERATIONS)
                        and not _query_only_request(task,request,matching)):
                    result['navigation_request_ids'].append(ident)
        else:
            # A returned empty/partial response can support a focused follow-up.
            # A failed transport is reported, never silently retried or treated
            # as proof that all other usable data must be discarded.
            result['unresolved'].append(dict(id=ident, source=request['source'], operation=operation,
                missing=missing, returned_source_refs=[row['id'] for row in matching],
                reason='requested_cells_missing' if matching else 'no_usable_response'))
            result['needs_followup'] |= bool(matching)
    period_requests = [request for request in selected.values()
                       if not requests_from_attempts or request['id'] in admitted_period_ids]
    stated = stated_period_coverage(task, period_requests, facts, records,
                                    include_query_windows=include_economic_query_windows, requirements=period_requirements)
    if stated:
        result['stated_economic_periods'] = stated
        result['needs_followup'] |= any(row['unqueried_period_count'] for row in stated)
    from .market_calendar import stated_close_coverage
    close_windows = stated_close_coverage(task, period_requests, records, facts, calendars=calendars)
    if close_windows:
        result['stated_close_windows'] = close_windows
        result['needs_followup'] |= any(row['unqueried_windows'] for row in close_windows)
    from .market_series import stated_nav_coverage
    nav_windows = stated_nav_coverage(task, period_requests, records.values())
    if nav_windows:
        result['stated_nav_windows'] = nav_windows
    from .market_series import (STATED_LATEST_EXACT_NAV_VERSION, STATED_LATEST_POLICY,
                                stated_direct_latest_scopes, stated_mixed_latest_scopes)
    direct_latest = stated_direct_latest_scopes(task, for_report=True)
    if len(direct_latest) == 1:
        result['stated_latest_observation'] = stated_latest_coverage(
            task, facts, records, period_requests, direct_latest[0], calendars=calendars)
    elif scopes := direct_latest or stated_mixed_latest_scopes(task):
        result['stated_latest_observations'] = [
            stated_latest_coverage(task, facts, records, period_requests, scope, calendars=calendars)
            for scope in scopes]
        if direct_latest and task[STATED_LATEST_POLICY] >= STATED_LATEST_EXACT_NAV_VERSION:
            result['direct_latest_readout'] = True
    from .market_series import valuation_history_sample, valuation_history_scope
    if history := valuation_history_scope(task):
        result['valuation_history'] = dict(code=history['code'], cutoff=history['cutoff'],
            comparable_sample_available=bool(valuation_history_sample(task, history, facts, records)))
    finance = _stated_financial_coverage(task, facts, records, period_requests)
    if finance:
        result['stated_financial_periods'] = finance
    return result


_RESTRICTED_ACQUISITION = re.compile(
    r'(?:不要|不得|禁止|无需|不用|不再|不)\s*(?:联网|对外查询|取数|补数|查(?:询|收盘|行情|历史数据|宏观数据)'
    r'|重新(?:取数|查询)|(?:使用|调用)\s*(?:iFinD|外部数据))'
    r'|(?:仅|只)\s*(?:使用|用|读取|看|基于|依据|引用)\s*(?:我(?:给|提供|上传)的|本地|现有|已(?:有|上传|提供|留存|取得|下载)|附件)'
    r'|(?<![A-Za-z])(?:Wind|Tinyshare|Tushare|DataFetcher|Yahoo\s*Finance|AkShare)(?![A-Za-z])'
    r'|(?:仅|只)\s*(?:使用|用|读取|看|基于|依据|引用)(?!\s*iFinD)'
    r'[^。；！？\n]{0,30}(?:数据|来源|原文|文件|报告|公告|官网|交易所|数据库)',
    re.I,
)
_DIRECTIVE_DELIMITERS = '，,、。；;！？!?\n'
_DIRECTIVE_START = rf'(?:^|(?<=[{_DIRECTIVE_DELIMITERS}]))\s*(?:请\s*)?'
_DIRECTIVE_END = rf'\s*(?=$|[{_DIRECTIVE_DELIMITERS}])'
_LOCAL_DIRECTIVE_START = _DIRECTIVE_START + r'(?:(?:本次|这次|本轮|这一轮)\s*(?:请\s*)?)?'
_EXCLUDED_PROVIDER = re.compile(_DIRECTIVE_START
    + r'(?:不要|不必|不再|无需|不需要|禁止|不得|别|不)\s*(?:使用|用|调用|走|查询|查|接入)?\s*'
    r'(?P<source>iFinD|Wind|Tinyshare|Tushare|DataFetcher|Yahoo\s*Finance|AkShare)' + _DIRECTIVE_END, re.I)
_OFFLINE_ONLY = re.compile(_LOCAL_DIRECTIVE_START
    + r'(?:(?:全程\s*)?(?:不|不要|不必|不再|不用|无需|禁止|不得|别|请勿)\s*联网'
    r'|全程\s*离线|仅离线|保持离线|离线(?:完成|处理|出稿))' + _DIRECTIVE_END)
_LOCAL_MATERIAL = r'(?:附件|数据|材料|文件|报告|资料|原文|原件|财报|年报|季报|公告)'
_LOCAL_DATA_ONLY = re.compile(_LOCAL_DIRECTIVE_START
    + r'(?:(?:不|不要|不必|不再|不用|无需|禁止|不得|别)\s*'
    r'(?:对外查询|(?:查询|获取|取|使用)\s*外部数据|(?:重新)?取数|补数)|'
    r'(?:仅|只)\s*(?:使用|用|看|读取|基于|依据|根据|引用|限)\s*'
    rf'(?:附件|(?:本地|现有|已有|已上传|已提供|已留存|我(?:给|上传|提供)的)\s*(?:的\s*)?{_LOCAL_MATERIAL}?'
    rf'|(?:我(?:刚|新)?|刚|新|本次|这次|本轮)上传的\s*(?:[0-9一二两三四五六七八九十]+\s*份\s*)?{_LOCAL_MATERIAL}))'
    + _DIRECTIVE_END)
_ARCHIVE_DATA_ONLY = re.compile(_LOCAL_DIRECTIVE_START
    + r'(?:仅|只)\s*(?:使用|用|读取|基于|根据)\s*(?:本地归档|已留存数据|留存数据|已有数据|现有数据)'
    + _DIRECTIVE_END)
_NO_PDF = re.compile(_DIRECTIVE_START
    + r'(?:不要|不必|不再|无需|不需要|禁止|不得|别|不)\s*(?:下载|读取|使用|用)?\s*PDF(?:文件)?'
    + _DIRECTIVE_END, re.I)
_NO_DEFAULT_PDF = re.compile(_DIRECTIVE_START
    + r'(?:不要|不必|无需|不需要|别|不)\s*(?:默认|自动)\s*(?:下载|读取|使用)\s*PDF(?:文件)?'
    + _DIRECTIVE_END, re.I)
_MIXED_QUOTE_METRICS = re.compile(r'净值|估值|市盈率|市净率|营收|营业收入|现金流|净利润|成交[额量]')
_FINANCIAL_ORIGINAL_ONLY = re.compile(
    r'(?:核对|阅读|查看|打开|下载|读取|引用|摘录|验证)[^。；！？\n]{0,16}(?:原文|原件|PDF|原始公告|财报原表)'
    r'|(?:^|[。；，,]\s*)(?:请\s*)?(?:用|使用|基于|按|依据|以)\s*(?:所附|我提供的)?'
    r'(?:公告原文|年报原文|财报原表|财报原文|财报原件|原文|原件|PDF)'
    r'|以[^。；！？\n]{0,25}(?:原文|原件|公告|PDF)为准'
    r'|(?:不|不要|无需|禁止|不用)\s*(?:查(?:询|看)?|获取|提取|使用|调用|用|取)?\s*(?:iFinD|财务数据|财报数据)',
    re.I)


def stated_source_text(task, text):
    """A source exclusion does not turn an allowed iFinD query into a ban."""
    from .market_series import mixed_latest_enabled

    if not mixed_latest_enabled(task):
        return text
    return _EXCLUDED_PROVIDER.sub(lambda match: match[0] if match['source'].casefold()=='ifind' else '', text)


def validate_stated_source(task, source):
    """Apply clear task-wide source instructions before App or Skill dispatch."""
    from .market_series import _IFIND_ONLY, mixed_latest_enabled, nav_source_scope_enabled, only_public_etf_source

    if not (mixed_latest_enabled(task) or nav_source_scope_enabled(task)):
        return
    if not isinstance(source, str):
        raise ContractError('数据来源须为已登记名称。')
    text = _unquoted_local_scope(task['question'])
    aliases = {'tinyshare', 'tushare'} if source == 'tinyshare' else {source}
    if any(match['source'].casefold() in aliases for match in _EXCLUDED_PROVIDER.finditer(text)):
        raise ContractError('用户已排除本次数据来源，未派发对应请求。')
    if source != 'ifind' and _IFIND_ONLY.search(text):
        raise ContractError('本次研究仅使用iFinD，未派发其他来源请求。')
    if source != 'official' and only_public_etf_source(task):
        raise ContractError('本次研究仅使用上交所申赎清单，未派发其他来源请求。')


def offline_requested(question):
    """Distinguish a network ban from a restriction on data acquisition."""
    return bool(_OFFLINE_ONLY.search(_unquoted_local_scope(question)))


def _unquoted_local_scope(text):
    # Keep offsets for local_scope_text, and keep clauses separate across a quote.
    return QUOTED_TEXT.sub(lambda match: '\uFFFC' * len(match[0]), text)


def local_sources_only(task):
    question = _unquoted_local_scope(task.get('question', ''))
    return bool(_OFFLINE_ONLY.search(question) or _LOCAL_DATA_ONLY.search(question) or _ARCHIVE_DATA_ONLY.search(question))


def local_scope_text(text, *, network_only=False):
    """Remove whole access directives when interpreting the remaining question."""
    unquoted = _unquoted_local_scope(text)
    patterns = (_OFFLINE_ONLY,) if network_only else (_OFFLINE_ONLY, _LOCAL_DATA_ONLY, _ARCHIVE_DATA_ONLY)
    removed = sorted({match.span() for pattern in patterns for match in pattern.finditer(unquoted)})
    for start, end in sorted(removed, reverse=True):
        while start > 0 and text[start - 1] in _DIRECTIVE_DELIMITERS:
            start -= 1
        text = text[:start] + text[end:]
    return text


def pdf_download_disabled(task):
    """Recognize an explicit task instruction, leaving download preferences flexible."""
    return bool(_NO_PDF.search(task['question']))


def stated_access_restricted(task, text, *, scoped_market=False, for_local_report=False):
    """Evaluate overall access separately from an individual metric's scope."""
    from .market_calendar import _CLOSE_NEGATION
    from .market_series import (_EXPLICIT_NAV_EXCLUSION, LOCAL_PCF_QUERY, mixed_latest_enabled,
                                without_market_source_directives)

    if not for_local_report and local_sources_only(task):
        return True
    text = stated_source_text(task, text)
    if mixed_latest_enabled(task):
        if _EXCLUDED_PROVIDER.search(text):
            return True
        text = LOCAL_PCF_QUERY.sub(r'\1', text)
    text = without_market_source_directives(task, text)
    if scoped_market:
        text = _EXPLICIT_NAV_EXCLUSION.sub('', _CLOSE_NEGATION.sub('', text))
    return bool(_RESTRICTED_ACQUISITION.search(text))


def stated_latest_coverage(task, facts, records, requests, scope, *, calendars=None):
    """Track latest-observation scope separately from whether it was queried."""
    from .evidence import FIELD_REGISTRY, IFIND_QUOTA_ERRORS, identity_candidates, identity_resolutions, current_query_reference
    from .etf_sources import enabled as etf_pcf_enabled, validate_snapshot as validate_etf_snapshot
    from .market_series import (NAV_INDICATOR, SERIES_FAMILIES, STATED_LATEST_POLICY, exact_session_nav,
                                mixed_latest_enabled, observation_selection, series_family, stated_latest_cutoff,
                                VALUATION_HISTORY_REQUEST_PREFIX)
    from .subject_scope import subject_key

    selection = observation_selection(task)
    if selection is None:
        return None
    lookback_days = scope.get('requested_lookback_days', selection['lookback_calendar_days'][-1])
    field, question = scope['indicator'], scope['question_id']
    registry = FIELD_REGISTRY['ifind.cmd_history_quotation']['instrument_namespaces']
    source_codes = list(scope.get('codes', ()))
    name = scope.get('name')
    queried_identity = False
    candidates = []
    matching_candidates = []
    if name:
        rows = [row for row in identity_candidates(task) if row['query'] == name and row['business_lookup_authorized']]
        candidates = sorted({code for row in rows for code in row['codes']})
        market = scope.get('market')
        matching_candidates = [code for code in candidates if market is None
                               or (code.endswith('.HK') if market == '港股' else code.endswith(('.SH', '.SZ')))]
        if len(matching_candidates) == 1 and any(matching_candidates[0] in row['exact_lookup_codes'] for row in rows):
            source_codes = matching_candidates
        queried_identity = any(row.get('source') == 'ifind' and row.get('operation') == 'get_thscode'
                               and row['parameters'].get('secname') == name
                               for row in [*requests, *records.values()])
    mapped = {subject_key(row['codes'][0]): row['codes'][0] for row in identity_resolutions(task)
              if row['status'] == '代码映射已核验' and row.get('mode') == 'seccode'}
    codes = [mapped.get(subject_key(code), code) for code in source_codes
             if not code.endswith('.HK') or name or subject_key(code) in mapped]
    if field == 'close':
        codes = [code for code in codes if any(re.fullmatch(pattern, code) for pattern in registry.values())]
    else:
        codes = [code for code in codes if field in SERIES_FAMILIES.get(series_family(code), {})]
    operation = 'cmd_history_quotation' if field == 'close' else 'date_sequence'
    targets = [mapped.get(subject_key(code),code) for code in source_codes]
    by_code = {code:stated_latest_cutoff(task, code, field) for code in targets}
    limit_day = {code:date.fromisoformat(day) for code, day in by_code.items()}
    lookback_start = {code:(day-timedelta(days=lookback_days-1)).isoformat() for code, day in limit_day.items()}
    require_lookback = mixed_latest_enabled(task)
    explicit_official = scope.get('nav_source') == 'official'
    expanded = task[STATED_LATEST_POLICY] >= 2
    official_codes = {code for code in codes if code.endswith('.SH')} if (
        expanded and field == NAV_INDICATOR and etf_pcf_enabled(task)
        and (explicit_official or 'nav_source' not in scope)) else set()
    if explicit_official:
        codes = [code for code in codes if code in official_codes]
    exhausted = {}
    failed_requests = {row['fingerprint']:row['business_code'] for row in task.get('ledger', {}).get('dispatches', [])
                       if expanded and row.get('source') == 'ifind' and row.get('operation') == operation
                       and row.get('supplier_rejection_category') == 'quota_exhausted'
                       and 'fingerprint' in row and str(row.get('business_code')) in IFIND_QUOTA_ERRORS}
    queried = set()
    official_queried = set()
    for row in requests:
        if question not in (row.get('question_ids') or ()) or row['id'].startswith(VALUATION_HISTORY_REQUEST_PREFIX):
            continue  # A failed history request does not exhaust the narrower latest read.
        params = row['parameters']
        if (row['source'], row['operation']) == ('official', 'etf_pcf'):
            official_queried.add(params.get('ts_code'))
            continue
        if (row['source'], row['operation']) != ('ifind', operation):
            continue
        indicators, items = params.get('indicators'), params.get('indipara')
        fields = (indicators.split(',') if isinstance(indicators,str) else [
            item.get('indicator') for item in items if isinstance(item,dict)] if isinstance(items,list) else [])
        if field in fields:
            requested_codes = params.get('codes')
            if isinstance(requested_codes, str):
                # A date-specific request can supply the latest value when
                # it returns that day's observation. An empty response for
                # that day does not exhaust the longer latest-available query.
                start = params.get('startdate')
                requested = {code for code in requested_codes.split(',') if code in by_code
                             and params.get('enddate') == by_code[code]
                             and (not require_lookback or isinstance(start, str) and start <= lookback_start[code])}
                queried.update(requested)
                if failed_requests:
                    key = fingerprint(dict(source='ifind', operation=operation, parameters=params))
                    if key in failed_requests:
                        exhausted.update(dict.fromkeys(requested, failed_requests[key]))
    relevant = {ident:row['parameters'].get('enddate') for ident, row in records.items()
                if not explicit_official and question in (row.get('question_ids') or ())
                and (row.get('source'), row.get('operation')) == ('ifind', operation)
                and row['parameters'].get('enddate') in by_code.values()
                and current_query_reference(task, row) is not None}
    observed = {}
    for row in facts:
        code, artifact = row['code'], row.get('artifact_id')
        if (row.get('value') is None or row.get('field') != field or row.get('previous_close_for')
                or artifact not in relevant or by_code.get(code) != relevant[artifact]):
            continue
        day = _sequence_day(row['period'])
        if day is None or not 0 <= (limit_day[code] - day).days < lookback_days:
            continue
        prior = observed.get(code)
        if prior is None or day > prior[0]:
            observed[code] = (day, [row])
        elif day == prior[0]:
            prior[1].append(row)
    current_refs = {code: rows[0]['id'] for code, (_, rows) in observed.items()
                    if len({(row['value'], row['unit'], row.get('currency')) for row in rows}) == 1}
    latest_sessions = {}
    exact_session = scope.get('require_latest_session') or scope.get('require_valuation_session')
    exact_nav = exact_session_nav(scope)
    session_missing = set()
    if exact_session and codes:
        from .market_calendar import MARKETS, retained_calendars, verified_session_window
        if calendars is None:
            calendars = retained_calendars(task)
        for code in codes:
            if code.rsplit('.', 1)[-1] not in MARKETS:
                continue
            last = (date.fromisoformat(stated_latest_cutoff(task, code, field, completed_session=True))
                    if exact_nav else limit_day[code])
            first = limit_day[code]-timedelta(days=lookback_days-1)
            days = verified_session_window(code, first, last, calendars)
            if days:
                latest_sessions[code] = days[-1].isoformat()
                if code in observed and days[-1] != observed[code][0]:
                    current_refs.pop(code, None)
            elif days == ():
                current_refs.pop(code, None)
                session_missing.add(code)
    # A source-wide quota is not identity, entitlement or a missing trade.
    # The public current PCF may supply a separate dated observation.
    eligible_official = official_codes if explicit_official else official_codes & exhausted.keys()
    pcf = {}
    for row in facts:
        code = row['code']
        if code not in eligible_official or code in current_refs or row['field'] != 'unit_nav':
            continue
        record = records.get(row.get('artifact_id'), {})
        if ((record.get('source'), record.get('operation')) != ('official', 'etf_pcf')
                or question not in (record.get('question_ids') or ()) or current_query_reference(task, record) is None):
            continue
        try:
            origin = validate_etf_snapshot(record, task['cutoff'])['exchange_origin']
        except ContractError:
            continue
        first = (date.fromisoformat(by_code[code])-timedelta(days=lookback_days-1)).isoformat()
        if (row['period'] == origin['valuation_date'] and first <= row['period'] <= by_code[code]
                and code not in session_missing and latest_sessions.get(code, row['period']) == row['period']):
            pcf.setdefault(code, []).append((row, origin['announcement_date']))
    official_announcements = {}
    for code, rows in pcf.items():
        if len({(row['period'], row['raw_value'], announced) for row, announced in rows}) == 1:
            row, announced = rows[0]
            current_refs[code] = row['id']
            official_announcements[code] = announced
    result = dict(**scope, as_of_by_code=by_code, lookback_calendar_days=lookback_days,
                callable_codes=codes, candidate_codes=candidates, matching_candidates=matching_candidates,
                identity_queried=queried_identity, current_observation_refs=current_refs,
                quota_exhausted=exhausted, official_announcements=official_announcements,
                official_unqueried_codes=[code for code in codes if code in eligible_official
                    and code not in official_queried and code not in current_refs],
                missing_codes=[code for code in targets if code not in current_refs],
                unqueried_codes=[code for code in codes if code not in current_refs and code not in queried
                    and (not explicit_official or code not in official_queried)])
    if exact_session:
        result.update(latest_sessions=latest_sessions,
            unconfirmed_session_codes=[code for code in targets if code not in latest_sessions
                                       and (exact_nav or code in current_refs)])
    return result


def stated_latest_requests(task, coverage):
    """Compile requests through the same contracts used by model and Skill plans."""
    if local_sources_only(task):
        return []
    single = coverage.get('stated_latest_observation')
    scopes = [single] if single else coverage.get('stated_latest_observations', [])
    requests = (request for scope in scopes for request in _latest_scope_requests(task, scope))
    return list({request['id']: request for request in requests}.values())


def _latest_scope_requests(task, scope):
    from .evidence import IFIND_QUOTE_FUNCTION, IDENTITY_CANDIDATE_PROFILE, identity_candidate_lookup_enabled, identity_query_parameters
    from .market_series import (SERIES_FAMILIES, SERIES_LABELS, STATED_LATEST_REQUEST_PREFIX, series_family, series_settings)

    question_ids = [scope['question_id']]

    def request(operation, parameters, reason, *, source='ifind'):
        return dict(id=STATED_LATEST_REQUEST_PREFIX+fingerprint((question_ids, operation, parameters))[:24],
                    source=source, operation=operation, parameters=parameters, question_ids=question_ids,
                    selection_reason=reason)

    if scope['official_unqueried_codes']:
        return [request('etf_pcf', dict(ts_code=code),
                        '按用户指定查询上交所公开申赎清单。' if scope.get('nav_source') == 'official' else
                        'iFinD数据额度用尽，查询上交所申赎清单的实际净值所属日。',
                        source='official') for code in scope['official_unqueried_codes']]
    if scope.get('nav_source') == 'official':
        return []
    if scope.get('name') and not scope['identity_queried'] and identity_candidate_lookup_enabled(task):
        return [request('get_thscode', identity_query_parameters('secname', scope['name'], IDENTITY_CANDIDATE_PROFILE),
                        '按用户原名查询供应商证券代码。')]
    codes = scope['unqueried_codes']
    if not codes:
        return []
    def window(code):
        return dict(mode='latest_available', as_of=scope['as_of_by_code'][code],
                    lookback_calendar_days=scope['lookback_calendar_days'])

    field = scope['indicator']
    if field == 'close':
        batches = {}
        for code in codes:
            batches.setdefault(scope['as_of_by_code'][code], []).append(code)
        return [request('cmd_history_quotation', dict(codes=','.join(group), indicators='close',
                        functionpara=deepcopy(IFIND_QUOTE_FUNCTION), observation_window=window(group[0])),
                        '按原问题取得最近可用收盘读数。') for group in batches.values()]
    return [request('date_sequence', dict(codes=code, functionpara=series_settings(task, code),
                    indipara=[dict(indicator=field, indiparams=deepcopy(SERIES_FAMILIES[series_family(code)][field]))],
                    observation_window=window(code)), f'按原问题取得最近可用{SERIES_LABELS[field]}。') for code in codes]


def stated_latest_subject_prompt(scope):
    candidates = scope.get('subject_candidates', [])
    if len(candidates) > 1:
        return '请明确要核对的标的，可选' + '、'.join(candidates[:4]) + ('等。' if len(candidates) > 4 else '。')
    return '请明确要核对的证券名称或代码。'


def stated_latest_notes(coverage):
    """Describe incomplete recent readings using business scope only."""
    scope = coverage.get('stated_latest_observation')
    if not scope:
        return []
    if scope.get('needs_subject'):
        return [stated_latest_subject_prompt(scope)]
    from .evidence import IFIND_QUOTA_ERRORS
    from .market_series import SERIES_LABELS
    label = SERIES_LABELS.get(scope['indicator'], '收盘价' if scope.get('local_sources_only') else '收盘读数')
    if scope.get('local_sources_only'):
        subjects = scope['missing_codes'] or ([scope['name']] if scope.get('name') and not scope['callable_codes'] else [])
        sessions = scope.get('latest_sessions', {})
        return [f'{subject}在最近一个交易日{sessions[subject]}的{label}暂缺。' if subject in sessions else
                f'现有资料无法核对{subject}的最近{label}。' for subject in subjects]
    if scope.get('name') and not scope['callable_codes']:
        candidates = scope['matching_candidates']
        if len(candidates) > 1:
            codes = '、'.join(candidates[:4])
            suffix = '等' if len(candidates) > 4 else ''
            choice = '明确证券代码' if scope.get('market') else '明确市场或证券代码'
            return [f'{scope["name"]}对应{codes}{suffix}，需要{choice}后核对{label}。']
        return [f'{scope["name"]}尚无可用于本次查询的明确证券代码，{label}暂缺。']
    notes = ['iFinD'+IFIND_QUOTA_ERRORS[str(code)]+'。'
             for code in dict.fromkeys(scope.get('quota_exhausted', {}).values())]
    named_source = {'official': '上交所申赎清单', 'ifind': 'iFinD'}.get(scope.get('nav_source'))
    if scope.get('nav_source') == 'official' and not scope['callable_codes']:
        notes.append('上交所申赎清单查询需要可识别的沪市ETF代码。')
    for code in scope['missing_codes']:
        day = scope.get('latest_sessions', {}).get(code)
        if named_source:
            requested = f'在最近一个交易日{day}的{label}' if day else f'截至{scope["as_of_by_code"][code]}的最近{label}'
            notes.append(f'未从{named_source}取得{code}{requested}。')
        elif day:
            notes.append(f'{code}在最近一个交易日{day}的{label}暂缺。')
        else:
            notes.append(f'{code}截至{scope["as_of_by_code"][code]}的最近{label}尚未取得。')
    return notes


def stated_latest_gap_messages(scope):
    """Short task labels for the unresolved requested observations."""
    from .market_series import SERIES_LABELS

    if scope.get('needs_subject'):
        return [stated_latest_subject_prompt(scope).removesuffix('。')]
    label = SERIES_LABELS.get(scope['indicator'], '收盘读数')
    if scope.get('name') and not scope['callable_codes'] and not scope.get('local_sources_only'):
        return [f'{scope["name"]}的证券代码尚未确认']
    if scope.get('local_sources_only'):
        return [note.removesuffix('。') for note in stated_latest_notes(dict(stated_latest_observation=scope))]
    sessions = scope.get('latest_sessions', {})
    return [f'{code}缺少{sessions[code]}的{label}' if code in sessions else f'{code}缺少可用的{label}'
            for code in scope['missing_codes']]


def _financial_request_cells(task, params):
    """Retain the field, fiscal end date and C parameter as a single address."""
    from .financial_sources import financial_request_parameters
    if not isinstance(params, dict):
        return set()
    try:
        params = financial_request_parameters(task, 'basic_data_service', params)
    except ContractError:
        return set()
    codes, items = params.get('codes'), params.get('indipara')
    if isinstance(codes, list) and all(isinstance(code, str) for code in codes):
        codes = ','.join(codes)
    if not isinstance(codes, str) or not isinstance(items, list):
        return set()
    return {(code, item['indicator'], day, str(statement)) for code in codes.split(',')
            if re.fullmatch(r'\d{6}\.(?:SH|SZ)', code)
            for item in items if isinstance(item, dict) and isinstance(item.get('indicator'), str)
            and isinstance(item.get('indiparams'), list) and len(item['indiparams']) == 2
            for day, statement in [item['indiparams']]
            if isinstance(day, str) and re.fullmatch(r'\d{4}(?:0331|0630|0930|1231)', day)
            and str(statement) in ('1', '2', '3', '4')}


def _financial_cell_address(task, row, record, *, include_alternatives=False):
    """Match an actual financial observation to its own fiscal window."""
    from .tinyshare_sources import analysis_field

    if record is None or row.get('value') is None or type(row.get('value')) is bool:
        return None
    code, field = row.get('code'), analysis_field(row)
    period = row.get('period', '')
    if (record.get('source'), record.get('operation')) == ('ifind', 'basic_data_service'):
        return next((cell for cell in _financial_request_cells(task, record['parameters'])
                     if cell[:3] == (code, field, period)), None)
    if not include_alternatives:
        return None
    if (record.get('source') == 'tinyshare' and row.get('source_operation') in ('income', 'cashflow')
            and record['parameters'].get('report_type') == '1'
            and record['parameters'].get('period') == period):
        return code, field, period, '1'
    locator = row.get('locator', {})
    if (locator.get('kind') == 'peer_filing_cell' and locator.get('statement') == '合并报表'
            and (record.get('source'), record.get('operation')) in (('ifind', 'filing_original'), ('official', 'release_original'))
            and re.fullmatch(r'\d{4}-06-30', period)):
        return code, field, period.replace('-', ''), '1'
    if (record.get('kind') == 'filing' and locator.get('statement') == '合并报表'
            and locator.get('kind') == 'filing_financial_cell' and row.get('period_kind') == 'cumulative'
            and (dates := re.fullmatch(r'(\d{4})-01-01至\1-(03-31|06-30|09-30|12-31)', period))):
        return code, field, dates[1] + dates[2].replace('-', ''), '1'
    return None


def direct_financial_observations(task, facts, scope):
    """Keep a direct report's numbers within its requested fiscal addresses."""
    records = {row['id']: row for row in task.get('source_records', []) if _owned(task, row)}
    return [row for row in facts if _financial_cell_address(task, row, records.get(row['artifact_id']),
                                                             include_alternatives=True) in scope['cells']]


def archived_financial_snapshots(task, archive, check_active):
    """Select same-account iFinD business responses for explicit offline reuse."""
    from .archive import ArchiveRecordError
    from .evidence import current_query_reference, security_code_mentions
    from .financial_sources import stated_financial_requests

    text = _unquoted_local_scope(task['question'])
    if (task.get('run_mode') != 'offline' or task.get('research_mode') != 'current'
            or not _ARCHIVE_DATA_ONLY.search(text)
            or any(match['source'].casefold() == 'ifind' for match in _EXCLUDED_PROVIDER.finditer(text))):
        return []
    scopes = stated_financial_requests(task)
    requested = {(row['codes'], field, row['report_period'], row['statement_type']): row['question_ids']
                 for row in scopes for field in row['indicators']}
    if not requested:
        return []
    current = {row['id']: row for row in task['source_records']}
    covered = {_financial_cell_address(task, row, current.get(row['artifact_id']), include_alternatives=True)
               for row in observations(task)}
    remaining = requested.keys() - covered
    if not remaining:
        return []
    codes = {code for code, _, _, _ in remaining}
    selected = []
    cutoff = timepoint(task['cutoff'])
    for summary in archive.list_workspace(unreadable={}):
        if not remaining:
            break
        check_active(task)
        if (summary['id'] == task['id'] or summary.get('owner_account') != task.get('owner_account')
                or summary.get('synthetic') or summary.get('fixture') or summary.get('status') == '运行中'):
            continue
        mentions = set(security_code_mentions(summary['question'], encoding=2)) | set(summary.get('objects', []))
        if not (mentions & codes):
            continue
        try:
            previous = archive.get(summary['id'], summary['version'], allow_missing=True)
        except ArchiveRecordError:
            continue
        if (not previous or previous.get('owner_account') != task.get('owner_account')
                or previous.get('synthetic') or previous.get('fixture')):
            continue
        for origin in reversed(previous.get('source_records', [])):
            if (origin.get('source'), origin.get('operation'), origin.get('status')) != (
                    'ifind', 'basic_data_service', '成功待核验'):
                continue
            cells = _financial_request_cells(task, origin.get('parameters')) & remaining
            if not cells or not current_query_reference(previous, origin) or timepoint(origin['obtained_at']) > cutoff:
                continue
            # The original account-policy receipt proves the past query. A
            # local read needs no new quota check or acquisition receipt.
            identity = 'rh_source_' + fingerprint(
                [task['id'], task['version'], origin['request_hash'], origin['hash']])[:24]
            if identity in current:
                continue
            fields = ('kind', 'source', 'operation', 'parameters', 'request_hash', 'snapshot',
                      'hash', 'content_version', 'obtained_at', 'status', 'business_code')
            record = dict({key: origin[key] for key in fields if key in origin},
                id=identity, task_id=task['id'], research_version=task['version'], input_hash=task['input_hash'],
                source_check='待核验', applicability_check='待核验')
            facts = observations(dict(task, source_records=[record]))
            adopted = cells & {_financial_cell_address(task, row, record) for row in facts}
            if not adopted:
                continue
            record['archive_reuse'] = dict(version=1, task_id=previous['id'], research_version=previous['version'],
                artifact_id=origin['id'], snapshot_hash=origin['hash'], cells=sorted(map(list, adopted)))
            record['question_ids'] = list(dict.fromkeys(ident for cell in sorted(adopted) for ident in requested[cell]))
            selected.append(record)
            current[record['id']] = record
            remaining -= adopted
    return selected


def _stated_financial_coverage(task, facts, records, requests):
    """Compare stated scope with admitted requests and comparable actual cells."""
    from .financial_sources import stated_financial_requests
    scopes = stated_financial_requests(task)
    if not scopes:
        return []
    observed = {address for row in facts
                if (address := _financial_cell_address(task, row, records.get(row['artifact_id']),
                                                       include_alternatives=True))}
    from itertools import chain
    submitted = set()
    for row in chain(requests, records.values()):
        if (row.get('source'), row.get('operation')) == ('ifind', 'basic_data_service'):
            submitted.update(_financial_request_cells(task, row['parameters']))
    covered = observed | submitted
    return [dict(**scope,
                 missing_indicators=[field for field in scope['indicators']
                     if (scope['codes'], field, scope['report_period'], scope['statement_type']) not in observed],
                 unqueried_indicators=[field for field in scope['indicators']
                     if (scope['codes'], field, scope['report_period'], scope['statement_type'])
                     not in covered])
            for scope in scopes]


def stated_financial_supplements(task, coverage):
    """Fetch only missing fields not yet submitted for this task version."""
    from .market_series import mixed_latest_enabled

    current_policy = mixed_latest_enabled(task)
    question = _NO_PDF.sub('', task['question']) if current_policy else task['question']
    from .financial_sources import (STATED_FINANCIAL_POLICY, STATED_FINANCIAL_VERSION, STATED_FINANCIAL_VERSIONS,
                                    basic_financial_cell_limit, has_account_policy)

    version = task.get(STATED_FINANCIAL_POLICY)
    if version == STATED_FINANCIAL_VERSION:
        question = _NO_DEFAULT_PDF.sub('', question)
    if stated_access_restricted(task, question, scoped_market=current_policy) or _FINANCIAL_ORIGINAL_ONLY.search(question):
        return []

    groups = {}
    limit = (basic_financial_cell_limit(task)
             if version in STATED_FINANCIAL_VERSIONS[1:] and has_account_policy(task) else 0)
    for row in coverage.get('stated_financial_periods', []):
        fields = tuple(row['unqueried_indicators'])
        if not fields:
            continue
        key = (row['report_period'], row['statement_type'], tuple(row['question_ids']), fields,
               None if limit else row['codes'])
        groups.setdefault(key, []).append(row['codes'])
    requests = []
    for (period, statement, questions, fields, _), codes in groups.items():
        # A known limit can bound a shared request. Without it, keep single-code
        # requests so the first one can obtain the policy without enlarging scope.
        field_limit = limit if version in STATED_FINANCIAL_VERSIONS[2:] and limit else len(fields)
        for offset in range(0, len(fields), field_limit):
            selected_fields = list(fields[offset:offset+field_limit])
            size = max(1, min(2000, limit // len(selected_fields)))
            for start in range(0, len(codes), size):
                params = dict(codes=','.join(codes[start:start+size]), report_period=period,
                              statement_type=statement, indicators=selected_fields)
                requests.append(dict(id='stated_financial_'+fingerprint((questions, params))[:24],
                    source='ifind', operation='basic_data_service', parameters=params,
                    question_ids=list(questions), selection_reason='按原问题取得尚未查询的指定财务指标。'))
    return requests


def direct_compound_scope(task):
    """Admit a direct report only when every clause has a matching lookup."""
    from .economic_sources import direct_economic_scope, direct_economic_scopes
    from .evidence import _DIRECT_FILING_OPTION, filing_directory_scope
    from .financial_sources import direct_financial_scope, stated_financial_requests
    from .index_sources import direct_weight_scope, stated_weight_scopes
    from .market_calendar import (resolve_named_index_points, resolve_same_day_market_clauses, stated_direct_market_day,
                                  stated_market_calculations, stated_market_points)
    from .market_series import (_IFIND_ONLY, _LATEST_QUERY_TAIL, _latest_clauses, latest_clauses_with_subject,
                                stated_direct_latest_scopes, stated_mixed_latest_scopes)
    from .subject_scope import scope_question

    if task.get('draft_first_version') != 1 or task.get('research_mode') != 'current':
        return None
    text = _IFIND_ONLY.sub('', _NO_PDF.sub('', stated_source_text(task, scope_question(task))))
    if len(text) > 840 or stated_access_restricted(task, text) or _FINANCIAL_ORIGINAL_ONLY.search(text):
        return None
    clauses = [part for part in _latest_clauses(_unquoted_local_scope(text), valuation=True)
               if not (_LATEST_QUERY_TAIL.fullmatch(part) or _DIRECT_FILING_OPTION.fullmatch(part))]
    if len(clauses) < 2:
        return None
    financial, latest, directories, weights, economics = [], [], [], [], []
    points, calculations = set(), set()
    market_clauses = [resolve_named_index_points(task, clause)[0] for clause in clauses]
    for clause, antecedent in latest_clauses_with_subject(task, resolve_same_day_market_clauses(task, market_clauses)):
        if directory := filing_directory_scope(task, clause=clause):
            if directory not in directories:
                directories.append(directory)
        elif scope := direct_financial_scope(task, clause=clause):
            financial.append(scope)
        elif scope := direct_weight_scope(task, clause=clause):
            if scope not in weights:
                weights.append(scope)
        elif scope := direct_economic_scope(task, clause=clause):
            if scope not in economics:
                economics.append(scope)
        elif scopes := stated_direct_latest_scopes(task, clause=clause, antecedent=antecedent):
            if any(not row.get('codes') or any(not code.endswith(('.SH', '.SZ')) for code in row['codes'])
                   for row in scopes):
                return None
            latest.extend(scopes)
        elif stated_direct_market_day(task, clause=clause):
            clause_points = stated_market_points(task, clause=clause)
            if not clause_points:
                return None
            points.update(clause_points)
            calculations.update(stated_market_calculations(task, clause=clause))
        else:
            return None
    # Keep the established single-family paths when the entire question
    # already matches their parser.
    if not (directories or financial or latest or weights or economics) and stated_direct_market_day(task):
        return None

    cells = set().union(*(scope['cells'] for scope in financial))
    admitted_cells = {(row['codes'], field, row['report_period'], row['statement_type'])
                      for row in stated_financial_requests(task) for field in row['indicators']}

    def latest_key(row):
        return (row['question_id'], tuple(sorted(row.get('codes', ()))), row['indicator'],
                bool(row.get('require_latest_session')), bool(row.get('require_valuation_session')))

    if (cells != admitted_cells or points != stated_market_points(task)
            or weights and weights != stated_weight_scopes(task)
            or economics and economics != direct_economic_scopes(task)
            or calculations != stated_market_calculations(task)
            or {latest_key(row) for row in latest} != {latest_key(row) for row in stated_mixed_latest_scopes(task)}):
        return None
    finance = None
    if financial:
        kind = 'comparison' if any(scope['kind'] == 'comparison' for scope in financial) else 'fact'
        finance = dict(financial[0], cells=cells, kind=kind)
    scope = dict(financial=finance, latest=bool(latest), directories=directories,
                 points={(code, day.isoformat(), field) for code, day, field, _ in points})
    if calculations:
        scope['market_calculations'] = {(code, start.isoformat(), end.isoformat(), measure)
                                        for code, start, end, measure, _ in calculations}
    if weights:
        scope['weights'] = weights
    if economics:
        scope['economics'] = economics
    return scope


def direct_financial_requests(task, coverage, scope):
    """Read the account limit before grouping the remaining requested cells."""
    from .financial_sources import direct_financial_quota_reason, has_account_policy

    if direct_financial_quota_reason(task):
        return []
    requests = stated_financial_supplements(task, coverage)
    if not requests or has_account_policy(task):
        return requests
    policy = dict(source='ifind', operation='get_limit_policy', parameters={})
    attempt = task.get('source_attempts', {}).get(fingerprint(policy))
    if attempt and not (attempt.get('status') == '待响应' and attempt.get('batch') != task['batch']):
        return []
    return [dict(policy, id='draft_account_policy', question_ids=scope['question_ids'],
                 selection_reason='核对本次已指定财务指标的实际单次查询上限。')]


def stated_window_requests(task, *, include_financial=True):
    """Compile registered requests for explicit intervals not already queried."""
    from .evidence import IFIND_QUOTE_FUNCTION, declared_security_codes, identity_resolutions
    from .economic_sources import (_merged_date_windows, declared_economic_indicators, economic_indicator_matches,
                                   research_yield_requests, scoped_period_enabled)
    from .market_calendar import (MULTI_MARKET_DAY_VERSIONS, QUOTE_ENDPOINT_POLICY, _OTHER_QUOTE_BASIS,
                                  market_metric_scope_enabled, stated_close_intervals,
                                  stated_close_pairs, stated_close_returns, stated_daily_nav_premiums,
                                  stated_liquidity_intervals)
    from .market_series import (NAV_INDICATOR, SERIES_FAMILIES, daily_market_as_of, nav_source_scope_enabled,
                                 series_settings, stated_nav_intervals, validate_series_request)
    from .subject_scope import scope_question, subject_key

    question = task['question']
    scoped_market = market_metric_scope_enabled(task)
    # Metric-specific exclusions are already applied to each parsed interval.
    # Strip only the recognized negated phrase when checking task-wide access.
    def access_restricted(text):
        return stated_access_restricted(task, text, scoped_market=scoped_market)

    if (task.get('intent') == '原件内容核对' or access_restricted(question)
            or task.get('research_mode') not in ('current', 'historical')):
        return []
    declared_edb = set(declared_economic_indicators(task)) if task['research_mode'] == 'current' else set()
    declared_securities = {subject_key(code).upper(): code for code in declared_security_codes(task)}
    quote_codes = {key: code for key, code in declared_securities.items() if code.endswith(('.SH', '.SZ'))}
    if any(code.endswith('.HK') for code in declared_securities.values()):
        from .monthly_statistics import dated_pmi_scope

        # A dated comparison can request fully specified exchange symbols
        # exactly as entered. Attribute only price cells returned for that code.
        if dated_pmi_scope(task):
            quote_codes.update({key: code for key, code in declared_securities.items()
                                if re.fullmatch(r'\d{5}\.HK', code)})
        for row in identity_resolutions(task):
            if row['status'] == '代码映射已核验' and row.get('mode') == 'seccode':
                code, = row['codes']
                key = subject_key(code).upper()
                if code.endswith('.HK') and key in declared_securities:
                    quote_codes[key] = code
    named_edb = (scoped_period_enabled(task) and task['research_mode'] == 'current'
                 and any(record.get('operation') == 'search_edb' for record in task['source_records']))
    liquidity = stated_liquidity_intervals(task)
    if not quote_codes and not declared_edb and not named_edb and not liquidity:
        return []
    questions = {row['id']: scope_question(task) if row['question'] == question else row['question']
                 for row in task['questions']}
    explicit_closes = stated_close_intervals(task, explicit=True) if scoped_market else {}
    coverage = acquisition_coverage(task, include_retained_economic=True, include_economic_query_windows=True)
    groups = {}
    return_counts = (Counter((code, ident) for code, _, ident in stated_close_returns(task))
                     if task.get(QUOTE_ENDPOINT_POLICY) in MULTI_MARKET_DAY_VERSIONS else {})
    pair_dates = {(code, day, ident) for code, pairs in stated_close_pairs(task).items()
                  for first, last, ident in pairs for day in (first, last)}
    quote_windows = {}
    for kind, operation in (('stated_close_windows', 'cmd_history_quotation'),
                            ('stated_economic_periods', 'edb_service')):
        for row in coverage.get(kind, []):
            code, ident = row['code'], row['question_id']
            merge_quote = False
            text = questions[ident]
            if access_restricted(text):
                continue
            if operation == 'cmd_history_quotation':
                if code not in quote_codes:
                    continue
                if scoped_market:
                    scope = (date.fromisoformat(row['startdate']), date.fromisoformat(row['enddate']), ident)
                    if scope not in explicit_closes.get(code, ()):
                        continue
                    merge_quote = scope[0] == scope[1] and (
                        return_counts.get((code, ident), 0) > 1 or (code, scope[0], ident) in pair_dates)
                elif ('收盘' not in text or _OTHER_QUOTE_BASIS.search(text)
                      or len(declared_securities) > 1 and _MIXED_QUOTE_METRICS.search(text)):
                    continue
                code = quote_codes[code]
            elif scoped_period_enabled(task):
                if not row.get('auto_query'):
                    continue
            else:
                explicit = {match.group() for match in economic_indicator_matches(
                    text, namespace=task.get('economic_identifier_namespace_version'))}
                if code not in declared_edb or code not in explicit:
                    continue
            for window in row['unqueried_windows']:
                first, last = window['startdate'], window['enddate']
                if merge_quote and last <= daily_market_as_of(task):
                    dates = date.fromisoformat(first), date.fromisoformat(last)
                    quote_windows.setdefault((ident, code), []).append(dates)
                else:
                    groups.setdefault((operation, ident, first, last), set()).add(code)
    # Merge only contiguous unqueried dates with the same subject and question.
    for (ident, code), windows in quote_windows.items():
        for first, last in _merged_date_windows(windows):
            groups.setdefault(('cmd_history_quotation', ident, first.isoformat(), last.isoformat()), set()).add(code)

    requests = []
    def add(operation, ident, params):
        binding = (ident, operation, params)
        requests.append(dict(id='stated_window_'+fingerprint(binding)[:24], source='ifind',
                             operation=operation, parameters=params, question_ids=[ident],
                             selection_reason='按原问题取得尚未查询的指定期间读数。'))
    quote_fields = {}
    for (operation, ident, first, last), codes in groups.items():
        if first == last and last > daily_market_as_of(task):
            continue
        if operation == 'cmd_history_quotation':
            for code in codes:
                quote_fields[(ident, first, last, code)] = {'close'}
        else:
            add(operation, ident, dict(startdate=first, enddate=last, indicators=','.join(sorted(codes))))
    covered = {}
    if liquidity:
        for record in task.get('source_records', ()):
            if ((record.get('source'), record.get('operation')) != ('ifind', 'cmd_history_quotation')
                    or not _owned(task, record) or _source_issue(task, record)):
                continue
            params = record['parameters']
            if params.get('functionpara') != IFIND_QUOTE_FUNCTION:
                continue
            for code in params['codes'].split(','):
                for field in params['indicators'].split(','):
                    if (code, field) in liquidity:
                        covered.setdefault((code, field), []).append((params['startdate'], params['enddate']))
        for (code, field), intervals in liquidity.items():
            for start, end, ident in intervals:
                first, last = start.isoformat(), end.isoformat()
                if not any(a <= first <= last <= b for a, b in covered.get((code, field), ())):
                    quote_fields.setdefault((ident, first, last, code), set()).add(field)
    quote_batches = {}
    for (ident, first, last, code), fields in sorted(quote_fields.items()):
        indicators = ','.join(field for field in ('close', 'volume', 'amount') if field in fields)
        quote_batches.setdefault((ident, first, last, indicators), []).append(code)
    for (ident, first, last, indicators), codes in quote_batches.items():
        add('cmd_history_quotation', ident, dict(codes=','.join(codes), indicators=indicators,
            startdate=first, enddate=last, functionpara=deepcopy(IFIND_QUOTE_FUNCTION)))
    requests.extend(research_yield_requests(task))
    if task['research_mode'] == 'current':
        source_scoped = nav_source_scope_enabled(task)
        ifind_windows = stated_nav_intervals(task, source='ifind') if source_scoped else {}
        exchange_windows = stated_nav_intervals(task, source='official') if source_scoped else {}
        exchange_questions = {}
        for code, day, ident in stated_daily_nav_premiums(task):
            scope = (day, day, ident)
            if scope in exchange_windows.get(code, ()) and day.isoformat() <= daily_market_as_of(task):
                exchange_questions.setdefault(code, set()).add(ident)
        for row in coverage.get('stated_nav_windows', []):
            code, ident = row['code'], row['question_id']
            if access_restricted(questions[ident]):
                continue
            first, last = row['window_start'], row['window_end']
            if first == last and last > daily_market_as_of(task):
                continue
            scope = (date.fromisoformat(first), date.fromisoformat(last), ident)
            if source_scoped and scope not in ifind_windows.get(code, ()):
                if scope in exchange_windows.get(code, ()):
                    exchange_questions.setdefault(code, set()).add(ident)
                continue
            for window in row['unqueried_windows']:
                params = dict(**window, codes=code, functionpara=series_settings(task, code),
                    indipara=[dict(indicator=NAV_INDICATOR, indiparams=deepcopy(SERIES_FAMILIES['ETF'][NAV_INDICATOR]))])
                try:
                    validate_series_request(task, 'date_sequence', params)
                except ContractError:
                    continue
                add('date_sequence', ident, params)
        if exchange_questions:
            from .etf_sources import OPERATION, validate_request

            retained_codes = {(record.get('parameters') or {}).get('ts_code') for record in task['source_records']
                              if _owned(task, record)
                              and (record.get('source'), record.get('operation')) == ('official', OPERATION)}
            for code, question_ids in sorted(exchange_questions.items()):
                if code in retained_codes:
                    continue
                params = dict(ts_code=code)
                try:
                    validate_stated_source(task, 'official')
                    validate_request(task, params)
                except ContractError:
                    continue
                requests.append(dict(id='stated_window_'+fingerprint(('official', OPERATION, code))[:24],
                    source='official', operation=OPERATION, parameters=params, question_ids=sorted(question_ids),
                    selection_reason='按原问题查阅上交所当前公布的申赎清单，按净值所属日匹配。'))
    if include_financial:
        requests.extend(stated_financial_supplements(task, coverage))
    return requests


def quote_session_summary(task, facts, *, endpoint_gaps=()):
    """Describe exchange-day gaps and identify series with incomplete coverage."""
    if not _reader_at_least(task, 26):
        return [], []
    from .market_calendar import stated_quote_enabled, stated_session_gaps_enabled
    from .subject_scope import subject_key
    if not stated_session_gaps_enabled(task) and not endpoint_gaps and not any(
               isinstance(attempt, dict) and isinstance(attempt.get('encoded'), dict)
               and attempt['encoded'].get('operation') == 'cmd_history_quotation'
               for attempt in task.get('draft_source_attempts', [])):
        return [], []
    from .evidence import FIELD_REGISTRY
    fields = FIELD_REGISTRY['ifind.cmd_history_quotation']
    coverage = acquisition_coverage(task, facts=facts)
    groups = {}
    endpoints = {(subject_key(row['code']), day) for row in endpoint_gaps for day in row['missing_close_dates']}

    def add_gap(code, field, days, count):
        group = groups.setdefault((code, field), dict(dates=set(), omitted=0))
        group['dates'].update(day for day in days if field != 'close' or (subject_key(code), day) not in endpoints)
        group['omitted'] = max(group['omitted'], count - len(days))

    for request in coverage['unresolved']:
        if (request['source'], request['operation']) != ('ifind', 'cmd_history_quotation'):
            continue
        for gap in request['missing']:
            if not gap.get('missing_dates'):
                continue
            code, field = gap['code'], gap['field']
            if field not in LABELS:
                continue
            add_gap(code, field, gap['missing_dates'], gap['missing_date_count'])
    for window in coverage.get('stated_close_windows', ()):
        if window.get('missing_session_count'):
            add_gap(window['code'], 'close', window['missing_session_dates'], window['missing_session_count'])
    notes = []
    for (code, field), group in sorted(groups.items()):
        days = sorted(group['dates'])
        if not days and not group['omitted']:
            continue
        shown = '、'.join(days[:3])
        if len(days) > 3 or group['omitted']:
            shown += '等交易日' if shown else '部分交易日'
        label = LABELS[field]
        if re.fullmatch(fields['instrument_namespaces']['index_level'], code):
            label = label.replace('价', '点位')
        notes.append(f'{code}在{shown}的{label}尚未取得。')
    if len(notes) > 6:
        notes = notes[:5] + ['其余部分标的与指标也存在交易日读数缺项。']
    incomplete = set(groups)
    if stated_quote_enabled(task):
        gap_codes = {subject_key(row['code']) for row in endpoint_gaps if not row.get('no_sessions')}
        incomplete.update((row['code'], 'close') for row in facts
                          if row['field']=='close' and subject_key(row['code']) in gap_codes)
    return notes, [dict(code=code, field=field) for code, field in sorted(incomplete)]

def financial_period_scope(task, facts):
    """Describe actual financial windows; absence never blocks a report.

    Expected windows come from the original question or admitted requests.
    Values, units and applicability keep their own contracts.
    """
    if type(task.get(CLIENT_COPY_POLICY)) is not int or task[CLIENT_COPY_POLICY] not in CLIENT_COPY_VERSIONS[1:]:
        return None
    policy = task.get(FINANCIAL_PERIOD_GAP_POLICY)
    include_single_periods = policy in (1, FINANCIAL_PERIOD_GAP_VERSION)
    binding=dict(task_id=task['id'],research_version=task['version'],input_hash=task.get('input_hash'))
    sources={row['id']:row for row in task.get('source_records',[]) if _owned(task,row)
        and row.get('source')=='ifind' and row.get('operation')=='basic_data_service' and not _source_issue(task,row)}
    from .financial_sources import stated_financial_requests
    stated=stated_financial_requests(task)
    if stated:
        sources.update({row['id']:row for row in task.get('source_records',[])
            if _owned(task,row) and (row.get('source')=='tinyshare' or row.get('kind')=='filing'
                                     or (row.get('source'), row.get('operation')) == ('official', 'release_original'))
            and not _source_issue(task,row)})
    selected=[];available={};labels={}
    units = {}
    for row in facts:
        address = _financial_cell_address(task, row, sources.get(row.get('artifact_id')), include_alternatives=bool(stated))
        if address is not None:
            code, field, period, statement = address
            key=(code,field,statement)
            available.setdefault(key,set()).add(period)
            labels.setdefault(key,row.get('label') or field)
            selected.append((row['id'], period))
            if policy == FINANCIAL_PERIOD_GAP_VERSION:
                units.setdefault(key, {}).setdefault(period, set()).add(row.get('unit') or '原始单位待核对')
    expected={}
    stated_keys = set()
    for scope in stated:
        for field in scope['indicators']:
            key = scope['codes'], field, scope['statement_type']
            stated_keys.add(key)
            expected.setdefault(key, set()).add(scope['report_period'])
    for attempt in task.get('draft_source_attempts',[]):
        if (not isinstance(attempt,dict) or attempt.get('binding')!=binding
                or attempt.get('status') not in ('已提交','未取得')):continue
        request=attempt.get('encoded',{})
        if request.get('source')!='ifind' or request.get('operation')!='basic_data_service':continue
        params=request.get('parameters',{})
        if not include_single_periods and 'indipara' not in params:continue
        for code, field, period, statement in sorted(_financial_request_cells(task, params)):
            key = code, field, statement
            if key not in stated_keys:
                expected.setdefault(key,set()).add(period)
    groups=[]
    for key,periods in expected.items():
        if len(periods)<2 and not include_single_periods:continue
        received=available.get(key,set()).intersection(periods)
        groups.append(dict(code=key[0],field=key[1],statement=key[2],requested_periods=sorted(periods),
            available_periods=sorted(received),missing_periods=sorted(periods-received),
            label=labels.get(key) or (definition(key[1]) or {}).get('name') or key[1]))
    periods=sorted({period for _,period in selected});missing=[row for row in groups if row['missing_periods']]
    if not selected and not groups:return None
    def display(values):return '、'.join(day[:4]+'-'+day[4:6]+'-'+day[6:] for day in values)
    notes=[]
    report_types={'1':'合并报表','2':'母公司报表','3':'合并调整报表','4':'母公司调整报表'}
    if missing:
        contexts=sorted({(row['code'],row['statement']) for row in missing})
        for code,statement in contexts:
            subject=code+report_types[statement]
            relevant=[row for row in groups if row['code']==code and row['statement']==statement];gaps=[row for row in relevant if row['missing_periods']]
            received=sorted({day for row in relevant for day in row['available_periods']})
            absent=sorted({day for row in gaps for day in row['missing_periods']})
            same_gap=len({tuple(row['missing_periods']) for row in gaps})==1 and len(gaps)==len(relevant)
            if not received and not include_single_periods:
                note=subject+'所请求报告期的财务原值尚未取得，暂不作期间比较。'
            elif same_gap and len(received)==1:
                note=subject+'原值仅覆盖'+display(received)+'，'+display(absent)+'报告期原值尚缺，暂不作这些期间的比较。'
            elif include_single_periods:
                by_periods = {}
                for row in gaps:
                    by_periods.setdefault(tuple(row['missing_periods']), []).append(row['label'])
                notes.extend(subject+'的'+'、'.join(dict.fromkeys(names))+'尚缺'+display(days)+'报告期原值。'
                             for days, names in by_periods.items())
                continue
            else:
                names='、'.join(dict.fromkeys(row['label'] for row in gaps))
                note=subject+'的'+names+'尚缺'+display(absent)+'报告期原值，相关期间比较暂缺依据。'
            notes.append(note)
        if sum(map(len,notes))>240:
            notes=['部分请求的财务原值尚缺，正文按实际取得的读数列示。' if include_single_periods else
                   '部分请求报告期尚缺财务原值，本报告仅采用已返回的期间；缺项不按零值补齐。']
    if policy == FINANCIAL_PERIOD_GAP_VERSION:
        for key, requested in expected.items():
            code, field, statement = key
            by_period = units.get(key, {})
            if len(requested) < 2 or not requested <= by_period.keys():
                continue
            observed = {unit for period in requested for unit in by_period[period]}
            if len(observed) <= 1:
                continue
            unresolved = sorted(period for period in requested if '原始单位待核对' in by_period[period])
            reason = ('尚未确认' + display(unresolved) + '报告期的金额单位' if unresolved
                      else '各报告期的金额单位未统一')
            notes.append(f"{code}{report_types[statement]}的{labels.get(key, field)}，"
                         f"{reason}，暂不比较这些期间的变化。")
            if sum(map(len, notes)) > 480:
                notes[-1] = '其余部分财务指标的金额单位亦待统一，暂不比较相关期间的变化。'
                break
    return dict(version=1,binding=binding,facts_hash=fingerprint(facts),available_periods=periods,financial_refs=[ref for ref,_ in selected],
        comparisons=[row for row in groups if len(row['requested_periods'])>1],
        notes=notes,financial_approval=False,blocks_report=False)


# Navigation vocabulary for reported accounting summaries and breakdowns.
# These headings only locate passages. They do not certify any amount,
# period, issuer, accounting scope or publication time.
_DRAFT_ORIGINAL_SECTIONS = (
    (('营业收入', '营收', '净利润', '利润总额', '现金流', '主要财务指标'),
     ('主要会计数据', '主要财务指标', '合并利润表', '合并现金流量表')),
    (('分部', '分行业', '分业务', '分产品', '收入结构'),
     ('主营业务分行业', '主营业务分产品', '分部报告', '分部业绩', '分业务情况')),
)
_DRAFT_TABLE_VALUES = re.compile(r'(?<![0-9])[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?')
_DRAFT_STATEMENT_BREAKS = '。；！？;!?\n'


def _draft_original_scope(task):
    questions = task.get('questions', [])
    texts = [task.get('question', '')]
    for question in questions:
        texts.append(question.get('question', ''))
        texts.extend(question.get('evidence_needs', []))
    return ' '.join(dict.fromkeys(value for value in texts if isinstance(value, str)))[:2048]


def _draft_passage_cues(scope):
    phrases = re.findall(r'[\u3400-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_]{2,}', scope.lower())
    terms = {phrase[index:index+4] for phrase in phrases for index in range(max(1, len(phrase)-3))}
    guides = [markers for requested, markers in _DRAFT_ORIGINAL_SECTIONS
              if any(label in scope for label in requested)]
    years = set(re.findall(r'(?:19|20)\d{2}(?=年)', scope))
    return terms, guides, years


def disclosure_excerpt_hash(source_ref, name, part_index, locator, start, end, excerpt):
    return fingerprint([source_ref, name, part_index, locator, start, end, excerpt])


# Keep punctuation and paragraph boundaries in the literal span. A single
# newline inside a PDF page is often a line wrap, not the end of a statement.
_ORIGINAL_SENTENCE_END = re.compile(r'[。！？]+[”’"」』]*')
_ORIGINAL_PARAGRAPH_END = re.compile(r'\n[ \t]*\n+')
_RETAINED_EXCERPT_LENGTH = 360
_RETAINED_EXCERPT_COUNT = 4


def _public_statement_spans(text):
    start = 0
    for paragraph in _ORIGINAL_PARAGRAPH_END.split(text):
        end = start + len(paragraph)
        if len(paragraph.strip()) <= _RETAINED_EXCERPT_LENGTH:
            yield start, end
        else:
            cursor = start
            for boundary in _ORIGINAL_SENTENCE_END.finditer(text, start, end):
                yield cursor, boundary.end()
                cursor = boundary.end()
        separator = _ORIGINAL_PARAGRAPH_END.match(text, end)
        start = separator.end() if separator else end


def retained_public_excerpts(task, *, verify_text=False, include_uploads=False):
    """Choose literal passages from retained originals and explicitly scoped uploads."""
    from .evidence import _original_download_profile, business_payload, validate_filing_original_request
    from .material_parser import check_verification_scope, verified_material
    from .official_sources import (article_url, fomc_rate_dates, fomc_rate_request, fomc_rate_statement,
                                   research_fomc_rate_request)
    from .source_catalogue import disclosure_fields

    terms, guides, _ = _draft_passage_cues(_draft_original_scope(task))
    terms.update(label for guide in guides for label in guide)
    terms.difference_update(('公告', '政策', '报告', '原件', '材料', '披露', '正文', '原文', 'the', 'and'))
    fomc_request = research_fomc_rate_request(task) or fomc_rate_request(task)
    fomc_dates = fomc_rate_dates(task, fomc_request) if fomc_request else ()
    records = task.get('source_records', [])
    attachments = task.get('materials', [])
    local_uploads = {row.get('id') for row in attachments
                     if row.get('source') == '用户提供材料' and row.get('kind') == 'material'
                     and row.get('status') == '已解析'} if include_uploads else set()
    if not terms and not local_uploads and not fomc_dates:
        return []
    originals = [record for record in records if (record.get('source'), record.get('operation'))
                 in ORIGINAL_DOWNLOAD_OPERATIONS]
    originals.extend(row for row in attachments if row.get('id') in local_uploads)
    if not originals:
        return []
    counts = Counter(row.get('id') for row in records + attachments)
    superseded = pdf_reparse_replacements(task)
    # Newly generated reports use the current source rules even when an older
    # retained task predates those rules.
    scoped = dict(task, **{DISCLOSURE_POLICY: 1, HISTORICAL_ORIGINAL_POLICY: 1})
    candidates = []
    for record in originals:
        check_verification_scope()
        ref = record.get('id')
        uploaded = ref in local_uploads
        if (not isinstance(ref, str) or counts[ref] != 1 or ref in superseded or not _owned(task, record)
                or (not uploaded and record.get('status') != '成功待核验')
                or record.get('input_hash', task.get('input_hash')) != task.get('input_hash')):
            continue
        if uploaded:
            material, digest = record.get('snapshot'), record.get('snapshot_hash')
            if not isinstance(digest, str):
                continue
        else:
            profile = _original_download_profile(record)
            if profile is None:
                continue
            material, digest = profile['material'], profile['snapshot_hash']
        if not isinstance(material, dict) or material.get('status') != '已解析' or _source_issue(scoped, record):
            continue
        parts = material.get('parts')
        if not isinstance(parts, list):
            continue
        if record.get('source') == 'ifind':
            directory = record.get('directory') or {}
            if directory.get('original_fetch') != 'public_https':
                continue
            parameters = record.get('parameters')
            try:
                selected = validate_filing_original_request(task, parameters)
            except (ContractError, KeyError, TypeError, ValueError):
                continue
            if (selected != directory or record.get('request_hash') != fingerprint(
                    dict(source='ifind', operation='filing_original', parameters=parameters))):
                continue
        fields = disclosure_fields(record)
        title = material.get('name') if uploaded else fields.get('title')
        if not title or business_payload(title) != title or any(business_payload(value) != value for value in fields.values()):
            continue
        origin = record.get('snapshot', {}).get('official_origin', {}) if record.get('source') == 'official' else {}
        if fomc_request and origin:
            try:
                article_url(origin.get('url'), channel='fed_fomc')
            except ContractError:
                pass
            else:
                if origin.get('path_date_hint') not in fomc_dates:
                    continue
                statement = fomc_rate_statement(record, parts)
                if not statement or not isinstance(material.get('original_base64'), str):
                    continue
                index, start, end = (statement[key] for key in ('part_index', 'start', 'end'))
                part = parts[index]; excerpt = part['text'][start:end]
                if (not isinstance(part.get('locator'), str) or not part['locator']
                        or len(excerpt) > _RETAINED_EXCERPT_LENGTH or business_payload(excerpt) != excerpt):
                    continue
                location = dict(source_ref=ref, snapshot_hash=digest, part_index=index, start=start, end=end,
                    excerpt_hash=disclosure_excerpt_hash(ref, material['name'], index, part['locator'], start, end, excerpt))
                candidates.append((float('inf'), location, material, set(), ()))
                continue
        best = None
        by_term = {}
        for index, part in enumerate(parts):
            check_verification_scope()
            if not isinstance(part, dict):
                continue
            text = part.get('text')
            if (not isinstance(text, str) or not isinstance(part.get('locator'), str) or not part['locator']
                    or any(part.get(flag) for flag in ('metadata_field', 'pdf_table', 'pdf_text_limited', 'pdf_layout_limited'))):
                continue
            for left, right in _public_statement_spans(text):
                raw = text[left:right]
                excerpt = raw.strip()
                if not 8 <= len(excerpt) <= _RETAINED_EXCERPT_LENGTH or '\t' in excerpt or business_payload(excerpt) != excerpt:
                    continue
                compact = re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])', '', excerpt.lower())
                matched = {term for term in terms if term in compact}
                score = sum(map(len, matched))
                if not score and not uploaded:
                    continue
                primary = best is None or score > best[0]
                # One candidate per matching cue bounds memory when a long
                # attachment repeats the same wording across many pages.
                uncovered = [term for term in matched if term not in by_term or score > by_term[term][0]] if uploaded else []
                if not primary and not uncovered:
                    continue
                start, end = left + len(raw) - len(raw.lstrip()), left + len(raw.rstrip())
                location = dict(source_ref=ref, snapshot_hash=digest, part_index=index, start=start, end=end,
                    excerpt_hash=disclosure_excerpt_hash(ref, material.get('name'), index, part['locator'], start, end, excerpt))
                candidate = (score, location, matched)
                if primary:
                    best = candidate
                for term in uncovered:
                    by_term[term] = candidate
        if best is not None and isinstance(material.get('original_base64'), str):
            score, location, matched = best
            unique = {row[1]['excerpt_hash']:row for row in by_term.values()}
            candidates.append((score, location, material, matched, sorted(
                unique.values(), key=lambda row: (row[1]['part_index'], row[1]['start']))))
    excerpts, supplemental = [], []
    covered = {}
    for _, location, material, matched, alternatives in sorted(candidates, key=lambda row: -row[0]):
        check_verification_scope()
        try:
            payload = base64.b64decode(material['original_base64'], validate=True)
        except ValueError:
            continue
        if hashlib.sha256(payload).hexdigest() != material.get('hash'):
            continue
        if verify_text:
            try:
                verified_material(material, payload)
            except ContractError:
                check_verification_scope()
                continue
        excerpts.append(location)
        covered[location['source_ref']] = set(matched)
        supplemental.extend(alternatives)
        if len(excerpts) == _RETAINED_EXCERPT_COUNT:
            return excerpts
    def supplemental_rank(candidate):
        score, location, matched = candidate
        new_terms = matched - covered[location['source_ref']]
        return sum(map(len, new_terms)), score

    # First preserve coverage across different originals. Additional passages
    # from a verified attachment are useful only if they cover a new cue.
    while supplemental and len(excerpts) < _RETAINED_EXCERPT_COUNT:
        check_verification_scope()
        candidate = max(supplemental, key=supplemental_rank)
        if supplemental_rank(candidate)[0] == 0:
            break
        _, location, matched = candidate
        excerpts.append(location)
        covered[location['source_ref']].update(matched)
    order = {ref: index for index, ref in enumerate(covered)}
    return sorted(excerpts, key=lambda item: (order[item['source_ref']], item['part_index'], item['start']))


def _draft_original_excerpts(parts, scope, limit):
    """Replay the previous one-excerpt-per-part selection."""
    terms, guides, years = _draft_passage_cues(scope)
    candidates = []
    for index, part in enumerate(parts):
        raw = part['text']
        text = re.sub(r'\s+', '', raw.lower())
        matches = {term for term in terms if term in text}
        headings = {label for guide in guides for label in guide if label in text}
        if not matches and not headings:
            continue
        table_values = min(12, sum(1 for _ in _DRAFT_TABLE_VALUES.finditer(raw))) if guides else 0
        rank = len(matches) + min(10, table_values) + 4 * len(headings) + 2 * any(year in text for year in years)
        candidates.append((index, rank, headings, matches, table_values))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (-row[1], row[0]))
    # One page per requested family gets a place before any family consumes
    # the remainder. Prefer distinct headings for its second useful page.
    primaries, secondaries = [], []
    for markers in guides:
        relevant = [row for row in candidates if row[2].intersection(markers)]
        if relevant:
            first = max(relevant, key=lambda row: (row[4] > 0, row[1]))
            primaries.append(first)
            alternatives = [row for row in relevant if row[2].difference(first[2]) and row[4] > 0]
            if alternatives:
                secondaries.append(alternatives[0])
    excerpts = {}
    if parts and parts[0]['text']:
        excerpts[0] = dict(parts[0], text=parts[0]['text'][:min(700, limit//8)])
    remaining = limit - sum(len(part['text']) for part in excerpts.values())
    primary_count = len({item[0] for item in primaries} - excerpts.keys())
    for index, _, headings, matches, _ in primaries + secondaries + candidates:
        if remaining < 48:
            break
        if index in excerpts:
            continue
        raw = parts[index]['text']
        length = min(remaining, max(1_200, limit//3))
        if primary_count:
            length = min(length, max(64, remaining//primary_count))
            primary_count -= 1
        if len(raw) <= length:
            start = 0
        else:
            terms_in_order = sorted(headings or matches, key=lambda term: (-len(term), term))
            hits = [match.start() for term in terms_in_order
                    if (match := re.search(r'\s*'.join(map(re.escape,term)), raw, re.IGNORECASE))]
            start = min(max(0, min(hits, default=0)-length//3), len(raw)-length)
        excerpt = raw[start:start+length]
        excerpts[index] = dict(parts[index], text=excerpt, source_offset=start)
        remaining -= len(excerpt)
    return [excerpts[index] for index in sorted(excerpts) if excerpts[index]['text']]


def _original_passage(part, start, end):
    """Keep literal offsets and identify incomplete edge statements."""
    text = part['text']
    excerpt = dict(part, text=text[start:end], source_offset=start)
    if start and text[start-1] not in _DRAFT_STATEMENT_BREAKS:
        excerpt['partial_start'] = True
    if end < len(text) and text[end-1] not in _DRAFT_STATEMENT_BREAKS and text[end] not in _DRAFT_STATEMENT_BREAKS:
        excerpt['partial_end'] = True
    return excerpt


def _wrapped_original_ranges(text):
    from .numeric_integrity import literal_quantity_ranges
    return [(span.start, span.end) for span in literal_quantity_ranges(text)
            if '\n' in text[span.start:span.end]] if '\n' in text else []


def _draft_original_passages(parts, scope, limit, *, protect_ranges):
    """Select several literal spans under one budget, prioritizing uncovered question terms."""
    from .material_parser import check_verification_scope

    terms, guides, years = _draft_passage_cues(scope)
    headings = {term for group in guides for term in group}
    breaks = re.compile('['+re.escape(_DRAFT_STATEMENT_BREAKS)+']+')
    width = min(limit, max(320, min(3_000, limit//6)))
    edge = width//5
    step = max(1, width//2)
    candidates = []
    opening_ranges = []
    for index, part in enumerate(parts):
        check_verification_scope()
        raw = part['text']
        ranges = _wrapped_original_ranges(raw) if protect_ranges else []
        if index == 0:
            opening_ranges = ranges
        range_starts = [start for start, _ in ranges]
        for left in range(0, len(raw), step):
            if left and left//step % 128 == 0:
                check_verification_scope()
            start, end = left, min(len(raw), left+width)
            if start and raw[start-1] not in _DRAFT_STATEMENT_BREAKS:
                first = breaks.search(raw, start, start+edge)
                if first:
                    start = first.end()
            if end < len(raw) and raw[end-1] not in _DRAFT_STATEMENT_BREAKS:
                for last in breaks.finditer(raw, end-edge, end):
                    end = last.end()
            first_range = bisect_right(range_starts, start)-1
            last_range = bisect_right(range_starts, end)-1
            if first_range >= 0 and ranges[first_range][0] < start < ranges[first_range][1]:
                start = ranges[first_range][1]
            if last_range >= 0 and ranges[last_range][0] < end < ranges[last_range][1]:
                end = ranges[last_range][0]
            text = raw[start:end]
            compact = re.sub(r'\s+', '', text.lower())
            matches = {term for term in terms if term in compact}
            sections = {term for term in headings if term in compact}
            if matches or sections:
                dates = {year for year in years if year in compact}
                table_values = sum(1 for _ in _DRAFT_TABLE_VALUES.finditer(text)) if guides else 0
                rank = len(matches) + 4*len(sections) + 2*len(dates) + min(10, table_values)
                candidates.append((index, start, end, matches | sections | dates, rank))
            if left+width >= len(raw):
                break
    if not candidates:
        return None

    selected = []
    covered = set()
    remaining = limit
    while candidates:
        index, start, end, features, rank = max(candidates,
            key=lambda row: (len(row[3]-covered), row[4], -row[0], -row[1]))
        selected.append((index, start, end))
        covered.update(features)
        remaining -= end-start
        candidates = [row for row in candidates if row[2]-row[1] <= remaining
                      and (row[0] != index or row[2] <= start or end <= row[1])]
    # A small opening excerpt keeps issuer and document context when room remains.
    if parts and parts[0]['text']:
        first_span = min((start for index, start, _ in selected if index == 0), default=len(parts[0]['text']))
        end = min(700, limit//8, first_span, remaining)
        end = next((start for start, stop in opening_ranges if start < end < stop), end)
        if end:
            selected.append((0, 0, end))
    spans = []
    for index, start, end in sorted(selected):
        if spans and spans[-1][0] == index and spans[-1][2] == start:
            spans[-1] = (index, spans[-1][1], end)
        else:
            spans.append((index, start, end))
    return [_original_passage(parts[index], start, end) for index, start, end in spans]


def writing_context(task, facts, text_limit=24_000, *, prefer_current_pdf=True):
    """Send observations once, with literal bases and report-local references.

    Workflow review states are not investment facts. Complete originals remain
    in the workpaper; document scope restrictions still accompany quoted text.
    """
    from .index_sources import selected_weight_rows, stated_weight_sheets
    from .market_series import valuation_history_writing_rows

    weight_sheets = stated_weight_sheets(task)
    selected_weights = {sheet['source_ref']: selected_weight_rows(weight_sheets, sheet['source_ref'])
                        for sheet in weight_sheets if sheet['source_ref']}
    refreshed, superseded = _updated_observation_scopes(task)
    retired = pdf_reparse_replacements(task) if prefer_current_pdf else {}
    historical = historical_original_scope(task)
    records = {row['id']: row for row in task.get('source_records', []) + task.get('materials', [])
               if row['id'] not in superseded and row['id'] not in retired and _owned(task, row)
               and not (historical and row.get('operation') in CATALOGUE_OPERATIONS)
               and not _source_issue(task,row)}
    scopes = {ident: refreshed[ident] if ident in refreshed else _observation_scope(task, record)
              for ident, record in records.items()}
    request_fields = {ident: _request_field_info(task, record) for ident, record in records.items()}
    groups = {}
    for row in facts:
        if row.get('artifact_id') not in records:
            continue
        if weight_sheets and records[row['artifact_id']].get('operation') == 'index_file' and row.get('field') == 'weight':
            if row.get('locator', {}).get('member') not in selected_weights.get(row['artifact_id'], ()):
                continue
        scope = scopes[row['artifact_id']]
        if scope is not None:
            locator = row.get('locator', {})
            if scope.get('kind') == 'tinyshare_records':
                if row.get('field') not in scope['records'].get(locator.get('row'),set()):
                    continue
            else:
                if not _observation_cell_selected(scope,locator.get('table'),row.get('field'),locator.get('row')):
                    continue
        key = (row['artifact_id'], row['field'], row['unit'], row.get('axis_role', 'time'),
               row.get('series_basis', ''), _fact_request_scope(row, request_fields[row['artifact_id']]))
        groups.setdefault(key, []).append(row)
    series = []
    used = set()
    for (artifact, field, unit, role, _, request_scope), rows in groups.items():
        used.add(artifact)
        projected_rows = valuation_history_writing_rows(records[artifact], rows)
        item = dict(source_ref=artifact, field=field, label=rows[0]['label'], unit=unit,
                    unit_basis=rows[0].get('unit_basis', ''), axis_role=role)
        if len(projected_rows) != len(rows):
            item['frequency'] = '每月最后一个可用读数'
            rows = projected_rows
        currencies={row.get('currency') for row in rows}
        if len(currencies)==1 and None not in currencies:
            item['currency']=next(iter(currencies))
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
            if scopes[record['id']].get('kind')=='financial_rows':
                remaining = _financial_projection(record,scopes[record['id']],text_only=True)
                if remaining['tables']:
                    other.append(record)
                    projections[record['id']]=remaining
            continue
        snapshot = record.get('snapshot', record)
        material = snapshot.get('material', snapshot) if isinstance(snapshot, dict) else {}
        parts = material.get('parts') if isinstance(material, dict) else None
        if isinstance(parts, list) and any(isinstance(part, dict) and part.get('text') for part in parts):
            document = dict(source_ref=record['id'], name=material.get('name'),
                content_scope=dict(status=material.get('status'), limitation=material.get('limitation')),
                published_at=material.get('published_at'),
                parts=[dict(locator=part.get('locator'), text=part['text'], source_part_index=index, source_offset=0)
                       for index, part in enumerate(parts) if isinstance(part, dict) and isinstance(part.get('text'), str)])
            if task.get(DISCLOSURE_POLICY) == 1:
                from .source_catalogue import disclosure_fields
                disclosure = disclosure_fields(record)
                if disclosure:
                    document['disclosure'] = disclosure
                    document['snapshot_hash'] = record.get('snapshot_hash', record.get('hash'))
            documents.append(document)
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
    scope = _draft_original_scope(task) if _reader_at_least(task, 15) else ''
    excerpt_version = task.get(EXCERPT_LOCATION_POLICY)
    from .numeric_integrity import quantity_ranges_enabled
    protect_ranges = excerpt_version == EXCERPT_LOCATION_VERSION and quantity_ranges_enabled(task)
    for document in documents:
        original_length = sum(len(part['text']) for part in document['parts'])
        if scope and original_length > share and share >= 120:
            excerpts = (_draft_original_passages(document['parts'], scope, share, protect_ranges=protect_ranges)
                        if excerpt_version == EXCERPT_LOCATION_VERSION
                        else _draft_original_excerpts(document['parts'], scope, share))
            if excerpts:
                document.update(parts=excerpts, truncated=True)
                continue
        remaining = share
        clipped = []
        for part in document['parts']:
            if remaining <= 0:
                break
            end = min(remaining, len(part['text']))
            if excerpt_version == EXCERPT_LOCATION_VERSION:
                if protect_ranges and end < len(part['text']):
                    end = next((start for start, stop in _wrapped_original_ranges(part['text']) if start < end < stop), end)
                if not end:
                    continue
                clipped.append(_original_passage(part, 0, end))
            else:
                clipped.append(dict(part, text=part['text'][:end]))
            remaining -= end
        document['truncated'] = original_length > share
        document['parts'] = clipped
    if excerpt_version not in (1, EXCERPT_LOCATION_VERSION):
        for document in documents:
            for part in document['parts']:
                part.pop('source_part_index', None)
                part.pop('source_offset', None)
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
    if historical:
        result['historical_original_rule'] = ('documents仅含截止前留存且首发依据已核验的原件。'
            '缺少适用原件时注明历史可得性待核对，目录、用户问题中的引述和后来取得的材料只作查找线索。')
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


CLIENT_COPY_POLICY = 'draft_client_copy_version'
CLIENT_COPY_OBSERVATION_VERSION = 4
CLIENT_COPY_ATTRIBUTION_VERSION = 5
CLIENT_COPY_MARKET_LEVEL_VERSION = 6
CLIENT_COPY_MARKET_DAY_VERSION = 7
CLIENT_COPY_PROSE_VERSION = 8
CLIENT_COPY_VERSION = 9
CLIENT_COPY_VERSIONS = (1, 2, 3, CLIENT_COPY_OBSERVATION_VERSION, CLIENT_COPY_ATTRIBUTION_VERSION,
                        CLIENT_COPY_MARKET_LEVEL_VERSION, CLIENT_COPY_MARKET_DAY_VERSION, CLIENT_COPY_PROSE_VERSION,
                        CLIENT_COPY_VERSION)


def client_observation_links_enabled(task):
    return task.get(CLIENT_COPY_POLICY) in CLIENT_COPY_VERSIONS[3:]


def client_writing_context(task, facts, *, prefer_current_pdf=True):
    """Public writing sees safe quantities; the workpaper keeps every raw cell.

    Explicit numeric checking still receives literal values. Unreviewed
    investment prose uses registered relative arithmetic for financial amounts
    whose unit is unknown. A withheld amount is not represented as missing.
    """
    data = writing_context(task, facts, prefer_current_pdf=prefer_current_pdf)
    version = task.get(CLIENT_COPY_POLICY)
    if version is None:
        return data
    if type(version) is not int or version not in CLIENT_COPY_VERSIONS:
        raise ContractError('对客数值呈现版本未登记')
    from .draft_analysis import current_analysis, writing_calculations, interpretation_context, FINANCIAL
    from .tinyshare_sources import analysis_field
    from .planning import draft_research_scope
    scope = draft_research_scope(task)
    literal = scope is not None and scope['mode'] in ('fact','comparison')
    withheld = []
    sources={record['id']:record for record in task.get('source_records',[])}
    for group in data['observations']:
        source=sources.get(group['source_ref'],{})
        mapped=analysis_field(dict(group,source=source.get('source'),source_operation=source.get('operation')))
        if literal or group['unit'] != '原始单位待核对' or mapped not in FINANCIAL:
            continue
        index = group['columns'].index('value')
        group['columns'][index] = 'raw_value_available'
        for row in group['rows']:
            withheld.append(row[0]);row[index] = row[index] is not None
        group.pop('observed_profile',None)
        group['value_policy'] = '金额单位未明确，原值保存在底稿；本次对客表述使用已登记相对比较，不换算为元、万元或亿元。'
    checked = current_analysis(task,facts)
    data['calculations'] = writing_calculations(task,facts,checked=checked)
    if checked.get('quote_interval_gaps'):
        data['quote_interval_gaps'] = checked['quote_interval_gaps']
    if checked.get('nav_interval_gaps'):
        data['nav_interval_gaps'] = checked['nav_interval_gaps']
        data['nav_interval_rule'] = '所请求起止日缺少同口径单位净值时，只列可用日期和读数，不用区间内的其他日期替代端点。'
    from .economic_sources import economic_period_scope
    if economic_period_scope(task):
        data['economic_period_rule'] = '宏观首末差只采用所问统计期的端点和已登记计算。缺首期或末期时列实际读数，不用相邻期替代。中间缺期时可以比较齐全的端点，不据此描述连续走势。'
    data['interpretation_context'] = interpretation_context(task,facts)
    data['public_value_scope'] = dict(version=1, relative_amount_refs=withheld,
        raw_values_retained=True, source_or_unit_approval=False,
        rule='raw_value_available仅表示底稿中有原值，不是空值或金额。正文不从其他上下文推测这些金额；可引用已登记同比和比率，并保留同尺度前提。')
    from .numeric_integrity import quantity_qualifiers_enabled, quantity_ranges_enabled
    if quantity_qualifiers_enabled(task):
        data['public_value_scope']['rule'] += '原件的超过、至少、不足、约或左右等限定须随数值写入正文。'
    if quantity_ranges_enabled(task):
        data['public_value_scope']['rule'] += '原件区间保留上下限、单位与时间；端点不写成单点观测。'
    if version >= 2:
        period_scope=financial_period_scope(task,facts)
        if period_scope and (period_scope['notes'] or len(period_scope['available_periods'])<2):
            data['financial_period_scope']=dict(available_periods=period_scope['available_periods'],notes=period_scope['notes'],
                rule='按实际可用期间表述财务范围；单期资料不写成两期齐备，不对缺失报告期给出增长判断。保留其他有效分析，不要求重新取数。')
    return data
