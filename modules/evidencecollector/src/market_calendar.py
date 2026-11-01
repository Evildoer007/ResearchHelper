"""Independent exchange sessions, never invented prices or security status.

Uses OH's separate-calendar principle and inspected HTTP request convention,
without importing its code, Store or credentials. Dates remain exact source
values. Missing prices may reflect suspension, listing scope or missing data;
an exchange calendar cannot decide which cause applies.
"""
import copy
from datetime import date
import math
import re

from .contracts import ContractError, DOCUMENT_CONTENT_INTENT, fingerprint, market_date

POLICY = 'market_calendar_coverage_version'
BASIS_POLICY = 'market_coverage_basis_version'
NEED_SCOPE = 'market_calendar_coverage'
DOCUMENTATION = 'https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/manual.html'
HTTP_EXAMPLE = 'https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/example.html'
MARKETS = {'SH': '212001', 'SZ': '212100'}
EXCHANGES = {'212001': 'SSE', '212100': 'SZSE'}
FUNCTION = {'mode': '1', 'dateType': '0', 'period': 'D', 'dateFormat': '0'}
REGISTRY = {'ifind.get_trade_dates': dict(version=1, kind='独立交易所交易日历',
    raw_only=True, fields={}, documentation=DOCUMENTATION)}


def enabled(task):
    return type(task.get(POLICY)) is int and task[POLICY] == 1 and task.get('intent') != DOCUMENT_CONTENT_INTENT


def scope_basis(task):
    """Verified technical metadata, never scalar financial-evidence approval.

    Coverage can satisfy only explicitly typed calendar needs. There is no
    keyword classifier and no migration of a previously adopted need type.
    """
    if type(task.get(BASIS_POLICY)) is not int or task[BASIS_POLICY] != 1 or not enabled(task):
        return None
    from .evidence import current_query_reference
    panels = copy.deepcopy(coverage(task)['panels'])
    records = {record['id']: record for record in task.get('source_records', [])}
    def metadata(ident):
        record = records.get(ident)
        if record is None or current_query_reference(task, record) is None:
            return None
        return {key: copy.deepcopy(record[key]) for key in
            ('id', 'source', 'operation', 'parameters', 'request_hash', 'hash', 'task_id', 'research_version')}
    for panel in panels:
        panel['query_source'] = metadata(panel['artifact_id'])
        reference = panel['calendar_ref']
        panel['calendar_source'] = metadata(reference['artifact_id']) if reference else None
        if panel['calendar_source']:
            record = records[reference['artifact_id']]
            panel['calendar_original_dates'] = sessions(record['parameters'], record['snapshot'])['dates']
    covered = {}
    for question in task['questions']:
        related = [panel for panel in panels if question['id'] in panel['question_ids']]
        valid = bool(related) and all(panel['query_source'] and panel['calendar_source']
            and panel['status'] in ('日历覆盖一致', '存在未观测交易日', '存在非日历日期', '日历明确无交易日')
            for panel in related)
        covered[question['id']] = sorted(fingerprint(text) for text, scope in
            question.get('evidence_need_scopes', {}).items() if scope == NEED_SCOPE) if valid else []
    return dict(version=1, panels=panels, covered_need_ids=covered, financial_approval=False,
        rule='仅证明本次实际取数的来源、窗口、独立日历原日期及与价格日期的比对。明确登记market_calendar_coverage的需求才由此覆盖；异常或缺数必须照实解释。不能替代价格、净值、收益、主体事实、证券成交状态或历史可得性。iFinD日历不因未另下交易所PDF就成为缺失，但不冒充交易所网站原件。')


def covered_need_ids(task, question):
    basis = scope_basis(task)
    return basis['covered_need_ids'].get(question['id'], []) if basis else []


def markets_for(task):
    from .evidence import available_security_codes
    return sorted({MARKETS[code.rsplit('.', 1)[-1]] for code in available_security_codes(task)
                   if re.fullmatch(r'\d{6}\.(?:SH|SZ)', code)})


def validate_request(task, parameters):
    if not enabled(task):
        raise ContractError('本任务未登记独立交易日历，不自动升级旧任务')
    if (not isinstance(parameters, dict)
            or set(parameters) != {'marketcode', 'functionpara', 'startdate', 'enddate'}
            or parameters['marketcode'] not in markets_for(task)
            or parameters['functionpara'] != FUNCTION):
        raise ContractError('日历须属于本任务明确市场，保持独立日度交易日口径，不取价格或偏移日期')
    dates = []
    for key in ('startdate', 'enddate'):
        value = parameters[key]
        try:
            if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        except ValueError:
            raise ContractError('交易日历窗口须为YYYY-MM-DD，不修复日期') from None
        dates.append(value)
    if dates[0] > dates[1] or dates[1] > market_date(task['cutoff']):
        raise ContractError('交易日历窗口倒置或越过截止日，不自动扩展')
    return parameters


def request_shapes(task):
    if not enabled(task) or not markets_for(task):
        return []
    return [dict(qualified_id_for_lookup_only='ifind.get_trade_dates', source='ifind',
        operation='get_trade_dates', parameter_keys=['marketcode', 'functionpara', 'startdate', 'enddate'],
        parameter_options={'marketcode': markets_for(task)}, fixed_parameters={'functionpara': copy.deepcopy(FUNCTION)},
        numeric_fields=[], raw_only=True, date_format='YYYY-MM-DD', example=None,
        coverage_role='独立核对交易所交易日，不用行情返回日期、工作日或其他标的日期代替；多日行情由内核按需补取并计原预算。',
        limitations='不是证券上市或停牌证明，不授权价格、收益或历史可得性；港股等未登记市场保留未核验。')]


def sessions(parameters, payload):
    """One unambiguous explicit date column, including an explicit empty one."""
    if (not isinstance(payload, dict) or type(payload.get('errorcode')) is bool
            or payload.get('errorcode') not in (0, '0')):
        raise ContractError('交易日历业务响应未成功')
    echoed = payload.get('inputParams', payload.get('inputparams', {}))
    if isinstance(echoed, dict):
        market = echoed.get('marketcode', echoed.get('marketCode'))
        if market is not None and str(market) not in (parameters['marketcode'], EXCHANGES[parameters['marketcode']]):
            raise ContractError('交易日历返回市场与请求不符')
    columns = []

    def column(parent, path):
        if isinstance(parent, dict):
            for field in ('sequencedate', 'sequenceDate', 'date', 'time'):
                if field in parent:
                    values = parent[field]
                    if not isinstance(values, list):
                        raise ContractError('交易日历日期列须保留数组结构')
                    columns.append((path + [field], values))

    tables = payload.get('tables')
    if isinstance(tables, dict):
        tables = [(tables, ['tables'])]
    elif isinstance(tables, list):
        tables = [(table, ['tables', index]) for index, table in enumerate(tables)]
    else:
        tables = []
    for table, path in tables:
        if not isinstance(table, dict):
            raise ContractError('交易日历表结构异常')
        for values in (table.get('table'), table.get('data')):
            if isinstance(values, dict) and {'open', 'high', 'low', 'close', 'volume', 'amount'}.intersection(values):
                raise ContractError('行情日期列不是独立日历，不接受价格表冒充日历响应')
        column(table, path)
        column(table.get('table'), path + ['table'])
        column(table.get('data'), path + ['data'])
    column(payload.get('data'), ['data'])
    if not columns or len(columns) > 16:
        raise ContractError('交易日历缺少明确日期列，不把空表当作无交易日')
    # Repeated layouts must agree, not concatenate dates or silently prefer
    # a convenient column. Original paths remain available for inspection.
    values = columns[0][1]
    if any(value != values for _, value in columns):
        raise ContractError('交易日历多个日期列冲突，不能择一或合并')
    if len(values) > 100000:
        raise ContractError('交易日历超过有界响应容量，不截断后核验')
    for value in values:
        try:
            valid = (isinstance(value, str) and date.fromisoformat(value).isoformat() == value
                     and parameters['startdate'] <= value <= parameters['enddate'])
        except ValueError:
            valid = False
        if not valid:
            raise ContractError('交易日须为窗口内的精确日期，不截时刻、去空白或补工作日')
    if values != sorted(set(values)):
        raise ContractError('交易日须严格递增且不重复，不排序修复原始响应')
    return dict(exchange=EXCHANGES[parameters['marketcode']], dates=copy.deepcopy(values),
        column_paths=[path for path, _ in columns], explicit_empty=not values)


def dependency_requests(task, prepared):
    """Only actually acquired multi-day prices trigger finite calendar needs."""
    if not enabled(task):
        return []
    from .evidence import current_query_reference
    result = {}
    for need, source, operation, parameters in prepared:
        if (source != 'ifind' or operation != 'cmd_history_quotation'
                or parameters['startdate'] == parameters['enddate']):
            continue
        request_hash = fingerprint(dict(source=source, operation=operation, parameters=parameters))
        record = next((item for item in task.get('source_records', [])
                       if item.get('request_hash') == request_hash), None)
        if record is None or current_query_reference(task, record) is None:
            continue
        for code in parameters['codes'].split(','):
            market = MARKETS.get(code.rsplit('.', 1)[-1])
            if market is None:
                continue  # Never infer an unregistered market calendar.
            reusable = False
            for calendar in task.get('source_records', []):
                if (calendar.get('source') != 'ifind' or calendar.get('operation') != 'get_trade_dates'
                        or current_query_reference(task, calendar) is None):
                    continue
                existing = calendar['parameters']
                if (existing['marketcode'] == market and existing['startdate'] <= parameters['startdate']
                        and existing['enddate'] >= parameters['enddate']):
                    try:
                        validate_request(task, existing)
                        sessions(existing, calendar['snapshot'])
                        reusable = True
                    except ContractError:
                        pass
            if reusable:
                continue  # Same-task coverage is reusable, not a new request.
            params = dict(marketcode=market, functionpara=copy.deepcopy(FUNCTION),
                startdate=parameters['startdate'], enddate=parameters['enddate'])
            identity = fingerprint(params)
            if identity not in result:
                result[identity] = dict(id='calendar_' + identity[:24], source='ifind',
                    operation='get_trade_dates', parameters=params, question_ids=[],
                    selection_reason='核对本任务已取得多日行情的交易所日期覆盖；不增加研究对象、预测或价格字段。')
            result[identity]['question_ids'] = list(dict.fromkeys(
                result[identity]['question_ids'] + need['question_ids']))
    return list(result.values())


def coverage(task):
    """Task-bound coverage diagnostics, separate from financial approval."""
    if not enabled(task):
        return None
    from .evidence import current_query_reference
    records = task.get('source_records', [])
    calendars = []
    for item in records:
        if item.get('source') != 'ifind' or item.get('operation') != 'get_trade_dates':
            continue
        if current_query_reference(task, item) is not None:
            validate_request(task, item['parameters'])
            calendars.append((item, sessions(item['parameters'], item['snapshot'])))
    panels = []
    for record in records:
        if (record.get('source') != 'ifind' or record.get('operation') != 'cmd_history_quotation'
                or current_query_reference(task, record) is None):
            continue
        params = record['parameters']
        if params['startdate'] == params['enddate']:
            continue
        tables = record['snapshot'].get('tables')
        if not isinstance(tables, list):
            continue
        for code in params['codes'].split(','):
            item = dict(artifact_id=record['id'], snapshot_hash=record['hash'], object=code,
                start_date=params['startdate'], end_date=params['enddate'],
                question_ids=list(record.get('question_ids', [])), status='日历未核验',
                calendar_ref=None, missing_dates=[], unexpected_dates=[], observed_dates=[],
                calendar_dates=[], financial_approval=False, historical_availability_approved=False)
            matching = [(calendar, details) for calendar, details in calendars
                if calendar['parameters']['marketcode'] == MARKETS.get(code.rsplit('.', 1)[-1])
                and calendar['parameters']['startdate'] <= params['startdate']
                and calendar['parameters']['enddate'] >= params['enddate']]
            try:
                rows = [table for table in tables if isinstance(table, dict) and table.get('thscode') == code]
                if len(rows) != 1 or not isinstance(rows[0].get('time'), list):
                    raise ContractError('行情日期表不唯一或缺失')
                dates = rows[0]['time']; values = rows[0].get('table', {}).get('close')
                if (not isinstance(values, list) or len(values) != len(dates)
                        or dates != sorted(set(dates))
                        or any(not isinstance(day, str) or date.fromisoformat(day).isoformat() != day
                               or not params['startdate'] <= day <= params['enddate'] for day in dates)):
                    raise ContractError('行情日期、价格列或窗口不一致')
                observed = [day for day, value in zip(dates, values)
                            if type(value) in (int, float) and math.isfinite(value)]
                item['observed_dates'] = observed
                if matching:
                    calendars_for_window = [tuple(day for day in details['dates']
                        if params['startdate'] <= day <= params['enddate']) for _, details in matching]
                    if len(set(calendars_for_window)) != 1:
                        raise ContractError('同窗口已取得交易日历相互冲突')
                    calendar, _ = matching[0]
                    expected = list(calendars_for_window[0])
                    item.update(calendar_dates=expected,
                        calendar_ref=dict(artifact_id=calendar['id'], snapshot_hash=calendar['hash'],
                            request_hash=calendar['request_hash']),
                        missing_dates=sorted(set(expected) - set(observed)),
                        unexpected_dates=sorted(set(dates) - set(expected)))
                    item['status'] = ('存在非日历日期' if item['unexpected_dates'] else
                        '存在未观测交易日' if item['missing_dates'] else
                        '日历覆盖一致' if expected else '日历明确无交易日')
            except (ContractError, ValueError, TypeError):
                item['status'] = '日期覆盖不可核对'
            panels.append(item)
    return dict(version=1, panels=panels,
        limitations='交易所交易日不证明个券应当成交。缺少价格的原因可能是停牌、上市范围或缺数，未单独识别；不补值，不证明历史当时可得。')
