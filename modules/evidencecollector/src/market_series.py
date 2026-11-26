"""OH-compatible, task-bound iFinD research panels, not numeric approval.

The field bindings mirror inspected OH dashboard source. They are copied
constants, not runtime imports. Account permission, field units and historical
vintages remain separate acceptance gates.
"""
from __future__ import annotations

import copy
import math
from datetime import date, timedelta, timezone
import re

from .contracts import ContractError, market_date, timepoint, fingerprint

SERIES_DOCUMENTATION = "https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/manual.html"
NAV_INDICATOR = 'ths_unit_nvnf_fund'
PE_TTM_INDICATOR = 'ths_pe_ttm_stock'
PB_MRQ_INDICATOR = 'ths_pb_mrq_stock'
INDEX_PE_INDICATOR = 'ths_pe_index'
INDEX_PB_INDICATOR = 'ths_pb_index'
LATEST_VALUATION_FIELDS = frozenset({PE_TTM_INDICATOR, PB_MRQ_INDICATOR, INDEX_PE_INDICATOR, INDEX_PB_INDICATOR})
SERIES_FAMILIES = {
    "个股": {PE_TTM_INDICATOR: ["", "100"], PB_MRQ_INDICATOR: [""],
        "ths_dividend_yield_ttm_ex_sd_stock": [""], "ths_total_shares_stock": [""],
        "ths_market_value_stock": [""], "ths_turnover_ratio_stock": [""]},
    "指数": {INDEX_PE_INDICATOR: ["", "100", "100"], INDEX_PB_INDICATOR: ["", "100", "100"],
        "ths_ps_index": ["", "100", "100"]},
    "ETF": {NAV_INDICATOR: [""], "ths_fund_shares_fund": [""]},
}
SERIES_LABELS = {"ths_pe_ttm_stock": "PE(TTM)", "ths_pb_mrq_stock": "PB(MRQ)",
    "ths_dividend_yield_ttm_ex_sd_stock": "股息率(TTM)", "ths_total_shares_stock": "总股本",
    "ths_market_value_stock": "总市值", "ths_turnover_ratio_stock": "换手率",
    "ths_pe_index": "指数PE", "ths_pb_index": "指数PB", "ths_ps_index": "指数PS",
    NAV_INDICATOR: "单位净值", "ths_fund_shares_fund": "基金份额"}
SERIES_ROUTING_VERSION = 2
MAX_SERIES_WINDOW_DAYS = 365
DRAFT_SERIES_WINDOW_POLICY = 'draft_series_window_version'
BATCH_POLICY = 'series_batch_version'
BATCH_MAX_CODES = 20
BATCH_MAX_CELLS = 50000
NAV_INTERVAL_POLICY = 'draft_nav_interval_version'
NAV_INTERVAL_VERSION = 2
_OTHER_NAV_MEASURE = re.compile(
    r'累计净值|复权|净值估算|估算(?:单位)?净值|IOPV|溢价|折价|含分红|再投资|总回报|总收益'
    r'|收盘(?:价|点位)|成交[额量]|基金份额|估值|市盈率|现金流', re.I)
_NAV_SCOPE_NEGATION = re.compile(r'(?:不|无需|不必|不要|不需要)\s*(?:查(?:询|看)?|取|计算)?\s*单位净值')
_EXPLICIT_NAV_EXCLUSION = re.compile(
    r'(?:不要|不需要|不必|不用|无需|不得|禁止|不)\s*(?:查(?:询|看)?|取|计算|比较|核对)?\s*单位净值')
_CLOSE_LABEL = r'(?:日终)?(?:未复权|不复权)?收盘(?:价|点位)'


def batch_enabled(task):
    version = task.get(BATCH_POLICY)
    if version is None:
        return False
    if type(version) is not int or version != 1:
        raise ContractError('日期序列批处理版本未登记')
    return True


def batch_capacity(task):
    """Use actual task-local account policy, bounded by parser capacity."""
    if not batch_enabled(task):
        return None
    from .financial_sources import _metadata_records
    policies = _metadata_records(task, 'get_limit_policy')
    if not policies or any(policy['parsed']['request_data_limit'].get('unit') != 'cells'
            or not isinstance(policy['parsed']['request_data_limit'].get('rules'), list) for policy in policies):
        return None
    limits = [rule.get('max_per_request') for policy in policies
        for rule in policy['parsed']['request_data_limit'].get('rules', [])
        if isinstance(rule, dict) and isinstance(rule.get('data_types'), list)
        and all(isinstance(kind, str) for kind in rule['data_types']) and 'dateSequence' in rule['data_types']]
    if not limits or any(type(limit) is not int or limit <= 0 for limit in limits):
        return None
    return dict(version=1, max_codes=BATCH_MAX_CODES, max_cells=min(BATCH_MAX_CELLS, *limits))
# These are registered indicator namespaces, not proof of listing status,
# fund classification, permission or availability at a historical date.
SERIES_NAMESPACES = {
    "指数": r"(?:000\d{3}\.SH|399\d{3}\.SZ)",
    "ETF": r"(?:(?:510|511|512|513|515|516|517|518|520|530|560|561|562|563|565|566|567|588|589)\d{3}\.SH|(?:158|159)\d{3}\.SZ)",
    "个股": r"(?:(?:600|601|603|605|688)\d{3}\.SH|(?:000|001|002|003|300|301)\d{3}\.SZ)",
}
SERIES_LIMITATIONS = ("登记OH同源字段及HTTP形状，不证明账号权限、字段单位、证券类型或历史版本可恢复。"
    "原值不补零、不沿用前值、不推定币种；观察日期不是首次公开时间。"
    "原始记录不能直接作数字证据、财务计算输入或投资判断，估值倍数不是买卖信号。")
SERIES_REQUEST_REGISTRY = {"ifind.date_sequence": {
    "version": 1, "kind": "估值与ETF原始日期序列", "raw_only": True,
    "parameters": ["codes", "startdate", "enddate", "functionpara", "indipara"],
    "fields": {name: {"label": label, "unit_verified": False} for name, label in SERIES_LABELS.items()},
    "documentation": SERIES_DOCUMENTATION, "permission": "未验收", "live_samples": [],
    "limitations": SERIES_LIMITATIONS,
}}


def series_family(code):
    """Select a registered request family, never infer an actual identity."""
    if not isinstance(code, str):
        return None
    return next((family for family, pattern in SERIES_NAMESPACES.items()
                 if re.fullmatch(pattern, code)), None)


def series_codes(task, family=None):
    """Only this task's declared/mapped codes, independent of its UI entry."""
    from .evidence import available_security_codes
    return [code for code in available_security_codes(task)
            if series_family(code) is not None and (family is None or series_family(code) == family)]


def series_settings(task, code=None, *, family=None):
    if code is not None:
        resolved = series_family(code)
        if resolved is None or family is not None and family != resolved:
            raise ContractError('日期序列代码与登记指标家族不一致')
        family = resolved
    if family is None:
        families = {series_family(item) for item in series_codes(task)}
        if len(families) == 1:
            family = next(iter(families))
        elif not families and task.get('entry') in SERIES_FAMILIES:
            family = task['entry']  # Field description only; not a callable code.
        else:
            raise ContractError('多类对象的日期序列参数须按本次单个代码选择')
    if family not in SERIES_FAMILIES:
        raise ContractError('日期序列代码不属于登记指标命名空间')
    return {"Fill": "Blank", **({"block": "history"} if family == "指数" else {})}


def stated_nav_intervals(task, *, source=None, clause=None):
    """Exact calendar dates for an explicitly requested unit NAV comparison."""
    from .contracts import DOCUMENT_CONTENT_INTENT
    from .evidence import declared_security_codes, security_code_mentions
    from .market_calendar import stated_day_intervals, stated_market_intervals
    from .subject_scope import scope_question

    version = task.get(NAV_INTERVAL_POLICY)
    if type(version) is not int or version not in (1, NAV_INTERVAL_VERSION) or task.get('intent') == DOCUMENT_CONTENT_INTENT:
        return {}
    if version == NAV_INTERVAL_VERSION:
        return stated_market_intervals(task, 'unit_nav', source=source, clause=clause)
    if clause is not None:
        return {}
    declared = set(declared_security_codes(task))
    if len(declared) != 1:
        return {}
    code, = declared
    if series_family(code) != 'ETF':
        return {}
    cutoff = date.fromisoformat(daily_market_as_of(task))
    encoding = task.get('security_declaration_encoding', 1)
    scopes = set()
    for question in task.get('questions', []):
        if not question.get('required', True) or question.get('answer_scope') == 'general_definition':
            continue
        text = scope_question(task) if question['question'] == task.get('question') else question['question']
        if _NAV_SCOPE_NEGATION.search(text):
            continue
        mentions = set(security_code_mentions(text, encoding=encoding))
        if mentions - {code}:
            continue
        for sentence in re.split(r'[。；;！？?!\n]', text):
            if '单位净值' not in sentence or _OTHER_NAV_MEASURE.search(sentence):
                continue
            scopes.update((first, last, question['id'])
                          for _, _, first, last in stated_day_intervals(sentence) if first < last <= cutoff)
    return {code: scopes} if scopes else {}


def nav_source_scope_enabled(task):
    version = task.get(NAV_SOURCE_POLICY)
    return type(version) is int and version == 1


def only_public_etf_source(task):
    from .draft_sources import _unquoted_local_scope

    return nav_source_scope_enabled(task) and bool(_STANDALONE_PCF_SOURCE.search(
        _unquoted_local_scope(task['question'])))


def market_source_directive(text):
    return bool(_IFIND_ONLY.fullmatch(text) or _STANDALONE_PCF_SOURCE.fullmatch(text))


def without_market_source_directives(task, text):
    from .draft_sources import _unquoted_local_scope

    if not nav_source_scope_enabled(task):
        return text
    masked = _unquoted_local_scope(text)
    spans = [(match.end('lead') if pattern is _DATED_PCF_SOURCE else match.start(), match.end())
             for pattern in (_IFIND_ONLY, _DATED_PCF_SOURCE, _INLINE_IFIND_CLOSE, _INLINE_PCF_NAV)
             for match in pattern.finditer(masked)]
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + ' ' * (end-start) + text[end:]
    return text


def dated_nav_source_allowed(task, fragment, source):
    from .draft_sources import _unquoted_local_scope

    if source is None or not nav_source_scope_enabled(task):
        return True
    if _IFIND_ONLY.search(_unquoted_local_scope(task['question'])) and source != 'ifind':
        return False
    text = _unquoted_local_scope(fragment)
    if only_public_etf_source(task) or _DATED_PCF_SOURCE.search(text) or _INLINE_PCF_NAV.search(text):
        return source == 'official'
    return True


def nav_request_window(task, request):
    """Recognize the registered daily NAV field in a question-bound request."""
    if (request.get('source'), request.get('operation')) != ('ifind', 'date_sequence'):
        return None
    params = request.get('parameters')
    try:
        first, last = validate_series_request(task, 'date_sequence', params, require_identity=False)
    except (ContractError, TypeError, ValueError):
        return None
    if any(item['indicator'] == NAV_INDICATOR for item in params['indipara']):
        return date.fromisoformat(first), date.fromisoformat(last), params['codes'].split(',')
    return None


def validate_dated_nav_request(task, need):
    if not nav_source_scope_enabled(task) or not (window := nav_request_window(task, need)):
        return
    first, last, codes = window
    selected = need.get('question_ids')
    if not isinstance(selected, list) or not all(isinstance(ident, str) for ident in selected):
        return
    allowed = stated_nav_intervals(task, source='ifind')
    for code, intervals in stated_nav_intervals(task).items():
        if code in codes and any(
            start <= last and end >= first and ident in selected and (start, end, ident) not in allowed.get(code, ())
            for start, end, ident in intervals
        ):
            raise ContractError('该净值期间指定上交所申赎清单，未派发iFinD净值请求。')


def stated_nav_coverage(task, requests, records):
    """Distinguish unqueried NAV days from queried days with absent readings."""
    from itertools import chain
    from .economic_sources import unqueried_date_windows

    scopes = stated_nav_intervals(task)
    if not scopes:
        return []
    queried = {}
    for request in chain(requests, records):
        window = nav_request_window(task, request)
        if window and not (request.get('observation_scope') or latest_observation_selected(task, request)):
            first, last, codes = window
            for code in codes:
                for question in request.get('question_ids') or ():
                    queried.setdefault((code, question), set()).add((first, last))
    return [dict(code=code, question_id=question, window_start=first.isoformat(), window_end=last.isoformat(),
                 unqueried_windows=[dict(startdate=a.isoformat(), enddate=b.isoformat())
                     for a, b in unqueried_date_windows(first, last, sorted(queried.get((code, question), ())))])
            for code, intervals in scopes.items() for first, last, question in sorted(intervals)]


def validate_series_request(task, operation, params, *, require_identity=True):
    if operation != "date_sequence":
        raise ContractError("日期序列操作未登记")
    keys = set(SERIES_REQUEST_REGISTRY["ifind.date_sequence"]["parameters"])
    if not isinstance(params, dict) or set(params) != keys:
        raise ContractError("日期序列须明确单证券、窗口、登记字段及缺失口径")
    enabled = batch_enabled(task)
    codes = params['codes'].split(',') if isinstance(params['codes'], str) else []
    if (not codes or len(codes) != len(set(codes))
            or any(not re.fullmatch(r'\d{6}\.(?:SH|SZ)', code) for code in codes)
            or len(codes) > (BATCH_MAX_CODES if enabled else 1)):
        raise ContractError('日期序列须为有限、不重复且明确的沪深代码；旧任务保持单标的规则')
    families = {series_family(code) for code in codes}
    if None in families or len(families) != 1:
        raise ContractError('日期序列代码不属于已登记指标命名空间，不猜对象类型')
    family_name = next(iter(families))
    if require_identity and not set(codes) <= set(series_codes(task)):
        raise ContractError("日期序列证券未由本任务声明或核验映射")
    if params["functionpara"] != series_settings(task, family=family_name):
        raise ContractError("日期序列须保留Blank缺失值及登记指数历史成分口径")
    fields = params["indipara"]
    family = SERIES_FAMILIES[family_name]
    if not isinstance(fields, list) or not fields or len(fields) > len(family):
        raise ContractError("日期序列须选择有限个登记字段")
    selected = []
    for item in fields:
        if (not isinstance(item, dict) or set(item) != {"indicator", "indiparams"}
                or not isinstance(item["indicator"], str) or item["indicator"] not in family
                or item["indiparams"] != family[item["indicator"]]):
            raise ContractError("日期序列字段或参数未登记，不能猜指标、改口径或跨入口混用")
        selected.append(item["indicator"])
    if len(selected) != len(set(selected)):
        raise ContractError("日期序列不能重复字段")
    dates = []
    for key in ("startdate", "enddate"):
        value = params[key]
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ContractError("日期序列窗口须为明确YYYY-MM-DD格式")
        timepoint(value)
        dates.append(value)
    if not task.get("cutoff") or dates[0] > dates[1] or dates[1] > market_date(task["cutoff"]):
        raise ContractError("日期序列窗口未绑定截止时点、倒置或越过截止日期")
    if (date.fromisoformat(dates[1]) - date.fromisoformat(dates[0])).days >= MAX_SERIES_WINDOW_DAYS:
        raise ContractError("日期序列单次窗口最多365个日历日，不自动拆分并扩大请求预算")
    if len(codes) > 1:
        capacity = batch_capacity(task)
        days = (date.fromisoformat(dates[1])-date.fromisoformat(dates[0])).days+1
        if capacity is None or len(codes)*len(fields)*days > capacity['max_cells']:
            raise ContractError('批量日期序列须先取得本任务账号策略，并满足单次单元格上限')
    return dates


def draft_series_windows(task, request):
    # Split long single-security windows without changing their date coverage.
    if (task.get(DRAFT_SERIES_WINDOW_POLICY) != 1 or not isinstance(request, dict)
            or (request.get('source'), request.get('operation')) != ('ifind', 'date_sequence')):
        return [request]
    params = request.get('parameters')
    if not isinstance(params, dict) or not isinstance(params.get('codes'), str) or ',' in params['codes']:
        return [request]
    try:
        start, end = (date.fromisoformat(params[key]) for key in ('startdate', 'enddate'))
    except (KeyError, TypeError, ValueError):
        return [request]
    if (start.isoformat() != params['startdate'] or end.isoformat() != params['enddate']
            or not task.get('cutoff') or end.isoformat() > market_date(task['cutoff'])):
        return [request]
    days = (end - start).days + 1
    if days <= MAX_SERIES_WINDOW_DAYS:
        return [request]
    slices = (days + MAX_SERIES_WINDOW_DAYS - 1) // MAX_SERIES_WINDOW_DAYS
    ledger = task['ledger']
    if slices > ledger['limits']['data_requests'] - ledger['used']['data_requests']:
        return [request]
    width = (days + slices - 1) // slices
    windows = []
    # Comparable window sizes prevent an anniversary date becoming a
    # one-day query with no sessions. Bring back the most recent slice first.
    for offset in range(0, days, width):
        last = end - timedelta(days=offset)
        first = end - timedelta(days=min(offset + width, days) - 1)
        windows.append(dict(request, parameters=dict(params, startdate=first.isoformat(), enddate=last.isoformat())))
    return windows


def series_request_shapes(task):
    codes = series_codes(task)
    families = list(dict.fromkeys(series_family(code) for code in codes)) or list(SERIES_FAMILIES)
    profiles = [{"family": family, "available_codes": series_codes(task, family),
        "code_pattern": '^' + SERIES_NAMESPACES[family] + '$',
        "field_parameters": copy.deepcopy(SERIES_FAMILIES[family]),
        "fixed_parameters": {"functionpara": series_settings(task, family=family)}} for family in families]
    capacity = batch_capacity(task)
    if capacity is not None:
        for profile in profiles:
            if profile['available_codes']:
                profile['batch_limits'] = dict(capacity)
    fields = {field: parameters for family in families for field, parameters in SERIES_FAMILIES[family].items()}
    shape = {"qualified_id_for_lookup_only": "ifind.date_sequence", "source": "ifind",
        "operation": "date_sequence", "parameter_keys": SERIES_REQUEST_REGISTRY["ifind.date_sequence"]["parameters"][:],
        "routing_version": SERIES_ROUTING_VERSION, "family_shapes": profiles,
        "routing_is_identity_approval": False,
        "routing_rule": "按每次单个代码选择对应字段和参数，不按任务入口选；无本任务明确或核验代码不得派发。",
        "raw_only": True, "numeric_evidence_registered": False, "numeric_fields": [],
        "raw_fields": list(fields), "field_parameters": copy.deepcopy(fields), "available_codes": codes,
        "date_format": "YYYY-MM-DD", "max_calendar_days": 365, "example": None,
        "limitations": SERIES_LIMITATIONS}
    if batch_enabled(task):
        shape['routing_rule'] = ('同一指标家族、字段参数、窗口及缺失设置的明确代码可合并请求；'
            '批量前先取得本任务get_limit_policy，没有适用策略时只允许单只。'
            '遵守batch_limits及日历日单元格上界，不扩大样本、不混合家族、不批准金融口径。')
    if len(profiles) == 1:
        shape['fixed_parameters'] = copy.deepcopy(profiles[0]['fixed_parameters'])
    return [shape]


def series_record_contract(task, parameters=None, payload=None):
    code = parameters.get('codes') if isinstance(parameters, dict) else None
    codes = code.split(',') if isinstance(code, str) else []
    families = {series_family(item) for item in codes}
    family = next(iter(families)) if len(families) == 1 else None
    result = {"version": 2, "routing_version": SERIES_ROUTING_VERSION,
        "family": family, "code": code, "task_entry": task['entry'], "raw_only": True,
        "numeric_evidence_registered": False, "unit_verified": False,
        "historical_vintage_verified": False, "documentation": SERIES_DOCUMENTATION,
        "limitations": SERIES_LIMITATIONS}
    if batch_enabled(task):
        result.update(version=3, codes=codes, batch_version=1)
        if payload is not None:
            result['response_coverage'] = series_response_coverage(task, parameters, payload)
    return result


def series_response_coverage(task, parameters, payload):
    """Check each raw table independently without filling or adopting values."""
    validate_series_request(task, 'date_sequence', parameters)
    if (not isinstance(payload, dict) or type(payload.get('errorcode')) is bool
            or payload.get('errorcode') not in (0, '0') or not isinstance(payload.get('tables'), list)):
        raise ContractError('日期序列响应业务状态或表结构无效')
    requested = parameters['codes'].split(',')
    fields = [item['indicator'] for item in parameters['indipara']]
    seen, tables, missing, cells = set(), [], [], 0
    for table in payload['tables']:
        if (not isinstance(table, dict) or table.get('thscode') not in requested
                or table['thscode'] in seen or not isinstance(table.get('time'), list)
                or not isinstance(table.get('table'), dict)):
            raise ContractError('日期序列含外来、重复代码或未对齐表结构')
        code, days = table['thscode'], table['time']
        seen.add(code)
        if (any(not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day)
                or not parameters['startdate'] <= day <= parameters['enddate'] for day in days)
                or len(days) != len(set(days))):
            raise ContractError('日期序列包含重复或超出请求窗口的观测日期')
        for day in days:
            timepoint(day)
        missing_fields, numeric = [], 0
        for field in fields:
            values = table['table'].get(field)
            if values is None:
                missing_fields.append(field)
                continue
            if not isinstance(values, list) or len(values) != len(days):
                raise ContractError('日期序列字段长度与原始日期不一致')
            for row, value in enumerate(values):
                if value is None:
                    missing.append(dict(code=code, field=field, row=row, date=days[row]))
                else:
                    try:
                        finite = type(value) in (int, float) and math.isfinite(value)
                    except OverflowError:
                        finite = False
                    if not finite:
                        raise ContractError('日期序列含非有限数值或占位符，不替换为零')
                    numeric += 1
        cells += numeric
        tables.append(dict(code=code, rows=len(days), numeric_cells=numeric, missing_fields=missing_fields))
    return dict(version=1, requested_codes=requested, returned_codes=[row['code'] for row in tables],
        missing_codes=[code for code in requested if code not in seen], tables=tables,
        numeric_cells=cells, missing_cells=missing, raw_only=True, financial_approval=False,
        continuous_calendar_approved=False, absence_proved=False)


OBSERVATION_POLICY = 'market_observation_selection_version'
OBSERVATION_VERSION = 3
STATED_LATEST_POLICY = 'draft_stated_latest_version'
STATED_LATEST_MIXED_VERSION = 4
STATED_LATEST_FOLLOWUP_VERSION = 5
STATED_LATEST_VALUATION_VERSION = 6
STATED_LATEST_COMPOUND_VERSION = 7
STATED_LATEST_LOCAL_REPORT_VERSION = 8
STATED_LATEST_EXACT_NAV_VERSION = 9
VALUATION_EXCLUSIONS_VERSION = 10
RESEARCH_MARKET_INPUT_VERSION = 11
STATED_LATEST_VERSION = RESEARCH_MARKET_INPUT_VERSION
STATED_LATEST_VERSIONS = (1, 2, 3, STATED_LATEST_MIXED_VERSION, STATED_LATEST_FOLLOWUP_VERSION,
                          STATED_LATEST_VALUATION_VERSION, STATED_LATEST_COMPOUND_VERSION,
                          STATED_LATEST_LOCAL_REPORT_VERSION, STATED_LATEST_EXACT_NAV_VERSION,
                          VALUATION_EXCLUSIONS_VERSION, RESEARCH_MARKET_INPUT_VERSION)
_LATEST_OBSERVATION = (
    r'(?:最近|最新)(?:的|可用的?|一(?:个|次)?(?:交易日|估值日)的?|'
    r'(?:一个)?(?:已结束|已完成)的?交易日的?)?')
_LATEST_CLOSE = re.compile(_LATEST_OBSERVATION + r'\s*' + _CLOSE_LABEL)
_LATEST_SESSION = r'(?:最近|最新)(?:的)?(?:一个|一)?(?:已结束|已完成)?(?:的)?交易日(?:的)?'
_LATEST_SESSION_CLOSE = re.compile(_LATEST_SESSION + r'\s*' + _CLOSE_LABEL)
_LATEST_NAV = re.compile(_LATEST_OBSERVATION + r'\s*单位净值')
_LATEST_SESSION_NAV = re.compile(_LATEST_SESSION + r'\s*(?:基金份额|份额|单位)净值')
_LATEST_MARKET_TIME = _LATEST_SESSION + '|' + _LATEST_OBSERVATION
_LATEST_MARKET_METRIC = (
    r'(?P<close>' + _CLOSE_LABEL + r')|(?P<' + NAV_INDICATOR + r'>单位净值)|'
    r'(?P<' + PE_TTM_INDICATOR + r'>(?:PE|市盈率)\s*[(（]\s*TTM\s*[)）]|滚动市盈率)|'
    r'(?P<' + PB_MRQ_INDICATOR + r'>(?:PB|市净率)\s*[(（]\s*MRQ\s*[)）])|'
    r'(?P<' + INDEX_PE_INDICATOR + r'>指数\s*(?:PE(?![A-Za-z0-9_])|市盈率))|'
    r'(?P<' + INDEX_PB_INDICATOR + r'>指数\s*(?:PB(?![A-Za-z0-9_])|市净率))')
_LATEST_MARKET_FIRST = re.compile(r'(?P<when>' + _LATEST_MARKET_TIME + r')\s*(?:' + _LATEST_MARKET_METRIC + r')', re.I)
_LATEST_MARKET_NEXT = re.compile(r'\s*(?:、|以及|及|和|与)\s*(?P<when>' + _LATEST_MARKET_TIME + r')?\s*(?:'
                                 + _LATEST_MARKET_METRIC + r')', re.I)
_LATEST_PUNCTUATED_METRIC = re.compile(r'\s*[，,]\s*(?:以及|和|与|及)?\s*(?P<when>' + _LATEST_MARKET_TIME
                                       + r')?\s*(?:' + _LATEST_MARKET_METRIC + r')', re.I)
_LATEST_SUBJECT_SEPARATOR = re.compile(r'\s*(?:[，,]\s*(?:以及|和|与|及)?|以及|和|与|及|、)\s*(?P<subject>.+?)\s*')
_LATEST_SUBJECT_NAME = re.compile(r'[\u3400-\u9fffA-Za-z0-9·]{0,24}')
_VALUATION_NEGATION = re.compile(r'(?:不要|不必|不用|无需|不得|禁止|别|不)\s*'
    r'(?:查(?:询|看)?|取|计算|比较|核对)?\s*(?:最新|最近)?\s*(?:指数\s*)?'
    r'(?:P[EB](?![A-Za-z0-9_])|估值|市盈率|市净率|滚动市盈率)', re.I)
_EXCLUDED_VALUATION_CLAUSE = re.compile(
    r'(?:^|[，,。；;！？!?\n])\s*(?:本轮\s*|本次\s*)?(?:请\s*)?'
    r'(?:不要|不用|不必|不需要|无需|不得|禁止|别|不)\s*'
    r'(?:查(?:询|看)?|看|取|核对|计算|比较)\s*(?:(?:' + _LATEST_MARKET_TIME + r')\s*)?'
    r'(?P<family>指数|个股)?\s*'
    r'(?:(?P<pe>滚动市盈率|(?:PE|市盈率)(?:\s*[(（]\s*TTM\s*[)）])?)'
    r'|(?P<pb>(?:PB|市净率)(?:\s*[(（]\s*MRQ\s*[)）])?))\s*'
    r'(?=$|[，,。；;！？!?\n])', re.I)
_LATEST_NEGATED_HEAD = re.compile(r'^\s*(?:请\s*)?(?:不要|不用|不必|无需|不得|禁止|别|不)')
_SSE_NAV = re.compile(r'(?:(?:最近|最新)(?:可用的?|的)?\s*)?(?:基金份额|份额|单位)净值')
_SSE_PCF = r'(?:上海证券交易所|上交所)(?:官网(?:的)?)?\s*(?:ETF\s*)?(?:申赎|申购赎回)清单'
_LATEST_SOURCE_VERB = r'(?:只|仅)?\s*(?:使用|用|按|根据|依据)\s*'
_LATEST_QUERY_VERB = r'(?:查一下|查询|查看|核对|看看|给出|列出|比较|对比|查)'
_LATEST_SOURCE_CLAUSE = re.compile(
    r'(^|[，,])\s*(?:请\s*)?' + _LATEST_SOURCE_VERB + r'(?P<source>' + _SSE_PCF + r'|iFinD)(?![A-Za-z0-9])', re.I)
_NAV_DATE_SUFFIX = re.compile(r'^\s*(?:和|及|、)\s*(?:净值)?(?:所属|观测)(?:日期|日)')
_LATEST_QUERY_HEAD = re.compile(r'^(?:请(?:帮我)?\s*|帮我\s*)?' + _LATEST_QUERY_VERB)
_LATEST_QUERY_TAIL = re.compile(
    r'(?:[\s，,。！？?!]+|是多少|多少|是什么|如何|并?生成(?:一份)?(?:简短|完整)?(?:研究)?报告|'
    r'并?出(?:一份)?(?:简短)?报告|(?:仅|只)(?:用|使用)iFinD|不要下载PDF|不下载PDF|不(?:用|要)PDF)+')
_LOCAL_REPORT_PRESENTATION = re.compile(
    r'(?:[，,。；;]\s*(?:并\s*)?|并\s*)给出(?:一份)?(?:简短|完整)?(?:研究)?报告\s*$')
_LATEST_FOLLOWUP_HEAD = re.compile(r'^(?:(?:那么|那|请(?:帮我)?|帮我|再|继续|接着|分别|单独|只看)\s*){0,4}(?:'
                                   + _LATEST_QUERY_VERB + r'\s*)?')
_LATEST_SINGLE_REFERENT = re.compile(r'(?:它|其|该(?:股|公司|基金|ETF|指数|标的)|这(?:只|家)(?:基金|ETF|公司|股票)?|'
                                     r'同一(?:公司|基金|ETF|指数|标的)|前者|后者)的?', re.I)
_LATEST_PLURAL_REFERENT = re.compile(r'(?:两者|二者|它们|这两只|两只(?:基金|ETF|股票)?|前者(?:与|和|及)后者)的?', re.I)
_LATEST_FOLLOWUP_PARTICLE = re.compile(r'^\s*(?:呢|吗)')
_UNSUPPORTED_LATEST = re.compile(r'分析|研究(?!报告)|解释|为何|原因|依据|实时|盘中|今[天日]|当天|走势|序列|'
                                 r'(?:本|上|下|这)(?:周|月|季度|半年|年度)|近[两三四五六七八九十\d]+[天日周月年]|'
                                 r'收益|回报|分红|溢价|折价|累计|涨跌|成交|估算|IOPV|(?:前|后)复权|'
                                 r'昨日|昨天|上个交易日|开盘价|最高价|最低价|任一|或|排名|成分|行业|板块', re.I)
_LATEST_NAME = re.compile(r'(?:(?P<market>港股|A股)\s*)?(?P<name>[\u3400-\u9fff][\u3400-\u9fffA-Za-z0-9·]{1,19})')
_LATEST_SCOPE_WORDS = re.compile(
    r'不\s*(?:要|必|再)?\s*(?:查|看|核对|取|使用)|(?<!不)(?<!未)复权|'
    r'(?:[0-9零一二三四五六七八九十]+)月|截至|截止|最新披露|近几个|每个交易日|'
    r'上涨|下跌|预测|目标|支撑|估值|市盈率|市净率|财务|营收|营业收入|净利润|现金流|公告|原文|财报|'
    r'(?<![A-Za-z])(?:PE|PB|TTM|ROE)(?![A-Za-z])', re.I)
_LATEST_CLAUSE_BOUNDARY = re.compile(
    r'[。；;！？?!\n]+|[，,](?=\s*(?:并|以及|同时|另外|再)?\s*(?:请|帮我)?\s*(?:'
    + _LATEST_SOURCE_VERB + r'(?:' + _SSE_PCF + r'|iFinD)\s*)?' + _LATEST_QUERY_VERB + r')', re.I)
_LATEST_VALUATION_CLAUSE_BOUNDARY = re.compile(_LATEST_CLAUSE_BOUNDARY.pattern +
    r'|[，,](?=\s*(?:并|同时|再)?\s*(?:给出|写出|形成|整理|生成|解释|分析|注明|列明))', re.I)
_MARKET_INPUT_VERB = r'(?:结合|参考|基于|对照|核对|查询|查看|看看|比较|对比|查|看|取)'
_MARKET_INPUT_BOUNDARY = re.compile(r'[，,](?=\s*(?:请|并|再|同时|顺带|也|另外){0,3}\s*' + _MARKET_INPUT_VERB + r')')
_MARKET_INPUT_PREFIX = re.compile(r'\s*(?:请|帮我|并|再|同时|顺带|也|另外){0,4}\s*(?:' + _MARKET_INPUT_VERB + r')?\s*')
_MARKET_INPUT_TIME = r'(?:当前的?|' + _LATEST_MARKET_TIME + ')'
_MARKET_INPUT_LABEL = (
    r'(?:市盈率|PE)\s*[（(]\s*TTM\s*[)）]|(?:市净率|PB)\s*[（(]\s*MRQ\s*[)）]|'
    r'滚动市盈率|指数市盈率|指数市净率|收盘点位|收盘价|单位净值|市盈率|股价')
_MARKET_INPUT_LIST = re.compile(r'(?P<when>' + _MARKET_INPUT_TIME + r')\s*(?P<labels>(?:' + _MARKET_INPUT_LABEL
    + r')(?:\s*(?:、|以及|及|和|与)\s*(?:' + _MARKET_INPUT_LABEL + r')){0,4})', re.I)
_MARKET_INPUT_FIELDS = re.compile(_MARKET_INPUT_LABEL, re.I)
_MARKET_INPUT_TAIL = re.compile(
    r'\s*(?:(?:怎么(?:样|看)|如何|(?:是)?多少|处于(?:什么|哪个)水平)\s*|'
    r'(?:[，,]\s*)?(?:并|再|来|以此|用来)?\s*(?:分析|判断|解释|研究|评估|说明)\S[^。！？]*)?\s*')
_MARKET_INPUT_PLURAL = re.compile(r'^(?:两者|二者|它们|这两(?:只|家)|两(?:只|家))的?\s*')
_MARKET_INPUT_HYPOTHETICAL = re.compile(r'如果|假如|假设|暂不|不要|别|不考虑|忽略|不使用|不看')
_IFIND_ONLY = re.compile(
    r'(?:^|[，,。；;！？!?\n])\s*(?:并|统一)?\s*(?:仅|只)(?:用|使用)\s*iFinD'
    r'(?:\s*数据)?(?:\s*(?:取数|查询))?\s*'
    r'(?=$|[，,。；;！？!?\n]|核对|查询|查看|查一下|查齐|查全|找全|列出|对比|比较|分析|统计|计算)', re.I)
_SSE_PCF_ONLY = re.compile(r'(?:仅|只)\s*(?:使用|用)\s*' + _SSE_PCF, re.I)
LOCAL_PCF_QUERY = re.compile(
    r'(^|[，,。；;！？!?\n])\s*(?:并|以及|同时|另外|再)?\s*(?:请\s*)?' + _LATEST_SOURCE_VERB + _SSE_PCF
    + r'(?=\s*' + _LATEST_QUERY_VERB + r'[^，,。；;！？!?\n]*(?:单位|份额)净值)', re.I)
NAV_SOURCE_POLICY = 'draft_nav_source_scope_version'
_DATED_PCF_SOURCE = re.compile(
    r'(?P<lead>^|[，,。；;！？!?\n])\s*(?:并|以及|同时|另外|再)?\s*(?:请\s*)?'
    + _LATEST_SOURCE_VERB + _SSE_PCF
    + r'(?=\s*(?:$|[，,。；;！？!?\n]|' + _LATEST_QUERY_VERB + r'))', re.I)
_INLINE_IFIND_CLOSE = re.compile(
    r'(?<![A-Za-z0-9])(?:使用|用)?\s*iFinD\s*的?\s*(?=收盘(?:价|点位))', re.I)
_INLINE_PCF_NAV = re.compile(
    r'(?:使用|用)?\s*' + _SSE_PCF + r'\s*的?\s*(?:官方\s*)?(?:' + _LATEST_QUERY_VERB
    + r'\s*)?(?:(?:当日|当天|同日)的?\s*)?(?=(?:单位|(?:基金)?份额)净值)', re.I)
_STANDALONE_PCF_SOURCE = re.compile(
    r'(?:^|[，,。；;！？!?\n])\s*(?:请\s*)?' + _SSE_PCF_ONLY.pattern
    + r'\s*(?=$|[，,。；;！？!?\n])', re.I)
STATED_LATEST_REQUEST_PREFIX = 'stated_latest_'
OBSERVATION_OPERATIONS = frozenset({'cmd_history_quotation', 'date_sequence'})
HISTORICAL_DAILY_POLICY = 'draft_historical_daily_version'
DAILY_MARKET_OPERATIONS = {
    'ifind': OBSERVATION_OPERATIONS,
    'tinyshare': frozenset({'daily', 'fund_daily', 'daily_basic'}),
}


def daily_market_as_of(task):
    """The latest date-only market observation permitted by the task's cutoff."""
    cutoff = task['cutoff']
    day = market_date(cutoff)
    version = task.get(HISTORICAL_DAILY_POLICY)
    if version is None or task.get('research_mode') != 'historical':
        return day
    if type(version) is not int or version != 1:
        raise ContractError('历史日频时点规则版本未登记')
    if timepoint(cutoff) < timepoint(day):
        # Without a time of availability, the same date is known at end of day.
        return (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    return day


def bound_daily_market_window(task, source, operation, parameters, *, shorten=False):
    """Limit dispatch without changing the meaning of a same-day-only request.

    Drafts can keep the eligible portion of a crossing window. The common
    dispatch validator also guards callers that bypass draft serialization.
    """
    if (task.get(HISTORICAL_DAILY_POLICY) is None or task.get('research_mode') != 'historical'
            or not isinstance(source, str) or not isinstance(operation, str)
            or operation not in DAILY_MARKET_OPERATIONS.get(source, ()) or not isinstance(parameters, dict)):
        return parameters
    limit = daily_market_as_of(task)
    cutoff_day = market_date(task['cutoff'])
    if limit == cutoff_day:
        return parameters
    compact = source == 'tinyshare'
    start_key, end_key = ('start_date', 'end_date') if compact else ('startdate', 'enddate')
    pattern = r'\d{8}' if compact else r'\d{4}-\d{2}-\d{2}'
    raw_dates = (parameters.get(start_key), parameters.get(end_key))
    if not all(isinstance(day, str) and re.fullmatch(pattern, day) for day in raw_dates):
        return parameters  # The provider contract reports malformed dates.
    try:
        start, end = (date.fromisoformat(day).isoformat() for day in raw_dates)
    except ValueError:
        return parameters
    if end <= limit:
        return parameters
    if not shorten:
        raise ContractError('历史截止时点位于日内，仅有日期的行情和估值可取至'+limit+'。')
    if start <= limit and end == cutoff_day:
        return {**parameters, end_key: limit.replace('-', '') if compact else limit}
    return parameters


def observation_selection(task):
    """A typed latest-observation choice; never classify the user's question."""
    version = task.get(OBSERVATION_POLICY)
    if version is None:
        return None
    if type(version) is not int or version not in (1, 2, OBSERVATION_VERSION):
        raise ContractError('市场观测选择版本未登记')
    selection=dict(version=1, as_of=daily_market_as_of(task), lookback_calendar_days=[7, 14, 30],
        rule='最近观测选择observation_window，明确区间继续使用startdate和enddate。最近观测只在所选回看窗口内查找；不自动扩大日期或重试。财务所属年度不能用作最近行情或估值的起始日期。')
    if selection['as_of'] != market_date(task['cutoff']):
        selection['rule'] += '历史日内截止仅使用此前日期的日频观测，as_of为日期上限，不代表已确认交易日。'
    if version >= 2:
        selection.update(select_per=['code','field'])
        selection['rule']+='各标的、各字段分别选取最后一个实际非空观测，保留各自日期；字段全空时仍为缺失，不把不同日期写成同日截面。'
    if version == OBSERVATION_VERSION:
        selection['rule'] += '最近一条未填充收盘观测可复用同响应内核对过的前收计算日涨跌幅。前收仅用于该计算，不当作请求了走势。'
    return selection


def mixed_latest_enabled(task):
    return task.get(STATED_LATEST_POLICY) in STATED_LATEST_VERSIONS[3:]


def latest_followup_enabled(task):
    return task.get(STATED_LATEST_POLICY) in STATED_LATEST_VERSIONS[4:]


def latest_valuation_enabled(task):
    return task.get(STATED_LATEST_POLICY) in STATED_LATEST_VERSIONS[5:]


def _latest_metric_list(text, *, multiple_metrics=False):
    """Read a contiguous list of registered metrics with an explicit date scope."""
    first = _LATEST_MARKET_FIRST.search(text)
    if first is None:
        return None
    items, end, when = [(first.lastgroup, first['when'])], first.end(), first['when']
    while match := _next_latest_metric(text, end, punctuated=multiple_metrics):
        items.append((match.lastgroup, match['when'] or when))
        end = match.end()
    if not (multiple_metrics and len(items) > 1 or any(field in LATEST_VALUATION_FIELDS for field, _ in items)):
        return None
    if len({field for field, _ in items}) != len(items):
        return None
    return first.start(), end, items


def _next_latest_metric(text, start, *, punctuated):
    return _LATEST_MARKET_NEXT.match(text, start) or (punctuated and _LATEST_PUNCTUATED_METRIC.match(text, start))


def _latest_followup_subject(prefix):
    """Recognize the missing or relative subject in a short follow-up."""
    subject = _LATEST_FOLLOWUP_HEAD.sub('', prefix, count=1).strip()
    if not subject:
        return 'omitted'
    if _LATEST_SINGLE_REFERENT.fullmatch(subject):
        return subject.removesuffix('的') if subject.startswith(('前者', '后者')) else 'single'
    if _LATEST_PLURAL_REFERENT.fullmatch(subject):
        return 'both'
    return None


def is_latest_readout_followup(text):
    """A short latest-observation question can activate one retained name."""
    match = (_LATEST_SESSION_CLOSE.search(text) or _LATEST_CLOSE.search(text)
             or _LATEST_SESSION_NAV.search(text) or _LATEST_NAV.search(text))
    if match is None:
        return False
    prefix = _LATEST_SOURCE_CLAUSE.sub(r'\1', text[:match.start()]).strip(' ，,')
    trailing = _LATEST_FOLLOWUP_PARTICLE.sub('', text[match.end():])
    return _latest_followup_subject(prefix) is not None and (not trailing or bool(_LATEST_QUERY_TAIL.fullmatch(trailing)))


def stated_latest_request(task):
    """Read a direct request for one latest metric."""
    scopes = stated_direct_latest_scopes(task)
    return scopes[0] if len(scopes) == 1 else None


def valuation_exclusion_scope(task, text=None):
    """Project standalone PE/PB exclusions while retaining the original research question."""
    from .draft_sources import _unquoted_local_scope
    from .subject_scope import scope_question

    if text is None:
        text = task['question'] if research_market_inputs_enabled(task) else scope_question(task)
    if task.get(STATED_LATEST_POLICY) not in (VALUATION_EXCLUSIONS_VERSION, RESEARCH_MARKET_INPUT_VERSION):
        return text, frozenset()

    fields = set()
    matches = list(_EXCLUDED_VALUATION_CLAUSE.finditer(_unquoted_local_scope(text)))
    for match in reversed(matches):
        stock, index = ((PE_TTM_INDICATOR, INDEX_PE_INDICATOR) if match['pe'] else
                        (PB_MRQ_INDICATOR, INDEX_PB_INDICATOR))
        if match['family'] != '指数':
            fields.add(stock)
        if match['family'] != '个股':
            fields.add(index)
        text = text[:match.start()] + text[match.end():]
    if matches:
        text = text.strip(' ，,。；;！？!?\n')
    return text, frozenset(fields)


def stated_direct_latest_scopes(task, *, for_report=False, clause=None, antecedent=None):
    """Read the whole supplied text as market observations, without analysis."""
    from .draft_sources import local_scope_text, local_sources_only, stated_source_text
    from .subject_scope import scope_question

    question = _latest_question(task)
    if not question:
        return []
    local = (for_report and task[STATED_LATEST_POLICY] >= STATED_LATEST_LOCAL_REPORT_VERSION
             and local_sources_only(task))
    text = scope_question(task) if clause is None else clause
    if clause is None and task.get('run_mode') in ('direct', 'offline', 'local_draft'):
        from .market_calendar import latest_session_calculation_sessions, stated_latest_session_calculation

        calculation = stated_latest_session_calculation(task, for_report=for_report)
        if calculation and latest_session_calculation_sessions(task, calculation) is None:
            scope = dict(question_id=question['id'], codes=(calculation['code'],),
                         requested_lookback_days=calculation['lookback'])
            scopes = [dict(scope, indicator='close', require_latest_session=True)]
            if 'unit_nav' in calculation['fields']:
                nav = dict(scope, indicator=NAV_INDICATOR)
                if not dated_nav_source_allowed(task, text, 'ifind'):
                    nav['nav_source'] = 'official'
                elif _IFIND_ONLY.search(text):
                    nav['nav_source'] = 'ifind'
                scopes.append(nav)
            return scopes
    if local:
        text = local_scope_text(text).strip(' ，,；;。！？!?\n')
        text = _LOCAL_REPORT_PRESENTATION.sub('', text)
    text, excluded = valuation_exclusion_scope(task, text)
    if mixed_latest_enabled(task):
        text = stated_source_text(task, text).strip(' ，,；;')
        if sum(not _LATEST_QUERY_TAIL.fullmatch(part) for part in _latest_clauses(text)) > 1:
            return []
    scopes = _latest_clause_scopes(task, text, question['id'], local=local,
                                   separate=clause is not None, antecedent=antecedent) or []
    return [row for row in scopes if row['indicator'] not in excluded]


def _latest_question(task):
    from .contracts import DOCUMENT_CONTENT_INTENT

    version = task.get(STATED_LATEST_POLICY)
    if (type(version) is not int or version not in STATED_LATEST_VERSIONS or task.get('research_mode') != 'current'
            or task.get('intent') == DOCUMENT_CONTENT_INTENT):
        return None
    required = [row for row in task['questions'] if row.get('required', True)]
    if len(required) != 1 or required[0].get('answer_scope') == 'general_definition':
        return None
    question = required[0]
    if (question['question'] != task['question'] or question.get('evidence_needs')
            or question.get('method_needs') or task.get('calculation_specs')
            or question.get('kind') not in ('fact','comparison') and task.get('question_origin') != 'default'):
        return None
    return question


def _split_latest_subjects(task, text):
    """Split at a new complete code followed by its own latest metric."""
    from .evidence import security_code_matches

    first = _LATEST_MARKET_FIRST.search(text)
    if first is None:
        return [text]
    parts, start, end = [], 0, first.end()
    while True:
        if match := _next_latest_metric(text, end, punctuated=True):
            end = match.end()
            continue
        following = _LATEST_MARKET_FIRST.search(text, end)
        separator = _LATEST_SUBJECT_SEPARATOR.fullmatch(text, end, following.start()) if following else None
        if separator is None:
            break
        subject = separator['subject']
        matches = list(security_code_matches(subject, encoding=task.get('security_declaration_encoding', 1)))
        if len(matches) != 1:
            break
        match, = matches
        name = subject[:match.start(1)].strip()
        if (_LATEST_NEGATED_HEAD.match(name) or not _LATEST_SUBJECT_NAME.fullmatch(name)
                or subject[match.end(1):].strip() not in ('', '的')):
            break
        parts.append(text[start:end])
        start, end = separator.start('subject'), following.end()
    return parts + [text[start:]]


def _latest_clause_scopes(task, text, question_id, *, separate=False, local=False, antecedent=None):
    compound = task[STATED_LATEST_POLICY] >= STATED_LATEST_COMPOUND_VERSION
    parts = _split_latest_subjects(task, text) if compound else [text]
    if len(parts) == 1:
        return _latest_scopes(task, text, question_id, separate=separate, local=local, antecedent=antecedent)

    # All pieces must parse before using this clause without model planning.
    first = _latest_scopes(task, parts[0], question_id, separate=True, local=local, antecedent=antecedent)
    if not first or any(row.get('nav_source') == 'official' for row in first):
        return None
    result = list(first)
    ifind = bool(_IFIND_ONLY.search(text)) or any(row.get('nav_source') == 'ifind' for row in first)
    for part in parts[1:]:
        scopes = _latest_scopes(task, '核对' + part, question_id, separate=True, local=local)
        if not scopes:
            return None
        for row in scopes:
            if ifind:
                row.setdefault('nav_source', 'ifind')
            if row in result:
                continue
            if any(previous['indicator'] == row['indicator']
                   and not set(previous['codes']).isdisjoint(row['codes']) for previous in result):
                return None
            result.append(row)
    return result


def _latest_scopes(task, text, question_id, *, separate=False, local=False, antecedent=None):
    from .draft_sources import _RESTRICTED_ACQUISITION, stated_access_restricted, stated_source_text
    from .evidence import declared_security_codes, security_code_matches
    from .market_calendar import _OTHER_QUOTE_BASIS
    from .numeric_integrity import _draft_periods

    version = task[STATED_LATEST_POLICY]
    compound = version >= STATED_LATEST_COMPOUND_VERSION
    text = stated_source_text(task, text)
    source = None
    no_calendar_lookup = False
    if version >= 2:
        specified = list(_LATEST_SOURCE_CLAUSE.finditer(text))
        if len(specified) > 1:
            return None
        if specified:
            clause, = specified
            source = 'ifind' if clause['source'].casefold() == 'ifind' else 'official'
            no_calendar_lookup = source == 'official' and _SSE_PCF_ONLY.search(clause[0]) is not None
            text = text[:clause.start()] + clause[1] + text[clause.end():]
        if re.search(_SSE_PCF, text) or source == 'official' and re.search(r'iFinD', text, re.I):
            return None
        if source is None and re.search(r'iFinD', text, re.I):
            source = 'ifind'
    restricted = (stated_access_restricted(task, text, for_local_report=local) if mixed_latest_enabled(task)
                  else _RESTRICTED_ACQUISITION.search(text))
    if (restricted or _OTHER_QUOTE_BASIS.search(text)
            or version == 1 and (_RESTRICTED_ACQUISITION.search(task['question'])
                                or _OTHER_QUOTE_BASIS.search(task['question']))):
        return None
    matches = list(security_code_matches(text, encoding=task.get('security_declaration_encoding', 1)))
    codes = tuple(dict.fromkeys(match[1] for match in matches))
    # Codes can touch the dates in Chinese chat; remove only recognized code spans.
    for match in reversed(matches):
        start, stop = match.span(1)
        text = text[:start] + ' '*(stop-start) + text[stop:]
    group = (_latest_metric_list(text, multiple_metrics=compound)
             if latest_valuation_enabled(task) and source != 'official' else None)
    other_text = text[:group[0]] + text[group[1]:] if group else text
    if (_UNSUPPORTED_LATEST.search(other_text) or _LATEST_SCOPE_WORDS.search(other_text) or _draft_periods(other_text)
            or group and (_VALUATION_NEGATION.search(text) or _LATEST_NEGATED_HEAD.match(text))):
        return None
    if group:
        start, end, metrics = group
        if (compound and len(metrics) > 1
                and (any(match.start(1) >= start for match in matches)
                     or _OTHER_NAV_MEASURE.search(other_text) and any(field == NAV_INDICATOR for field, _ in metrics))):
            return None
    else:
        close = (_LATEST_SESSION_CLOSE.search(text) if version >= 3 else None) or _LATEST_CLOSE.search(text)
        nav = (_LATEST_SESSION_NAV.search(text) if version >= STATED_LATEST_EXACT_NAV_VERSION else None)
        nav = nav or (_SSE_NAV if source == 'official' else _LATEST_NAV).search(text)
        if bool(close) == bool(nav) or source == 'official' and close:
            return None
        match = close or nav
        start, end = match.span()
        metrics = [('close' if close else NAV_INDICATOR, match.group())]
    prefix = text[:start].strip(' ，,')
    if mixed_latest_enabled(task) and re.search(r'收盘|净值', prefix):
        return None
    implicit_subject = not prefix and (group or compound and codes)
    relative = _latest_followup_subject(prefix) if antecedent and not codes else None
    if separate and not (_LATEST_QUERY_HEAD.match(prefix) or implicit_subject or relative == 'single'):
        return None
    scope = dict(question_id=question_id)
    if local:
        scope['local_sources_only'] = True
    if source:
        scope['nav_source'] = source
    trailing = text[end:]
    if source == 'official':
        trailing = _NAV_DATE_SUFFIX.sub('', trailing)
    followup = latest_followup_enabled(task) and task.get('parent') and not separate
    if followup:
        trailing = _LATEST_FOLLOWUP_PARTICLE.sub('', trailing)
    if trailing and not _LATEST_QUERY_TAIL.fullmatch(trailing):
        return None
    declared = set(declared_security_codes(task))
    if not codes and separate:
        if relative == 'single':
            codes = (antecedent,)
        elif group and len(declared) == 1 and _latest_followup_subject(prefix) == 'omitted':
            codes = tuple(declared)
        else:
            return None
    if codes:
        if not set(codes) <= declared or not separate and set(codes) != declared:
            return None
        # Require complete codes on both sides of a comparison.
        if len(codes) == 1 and re.search(r'与|和|以及|对比|比较|分别|两只|两家公司|同时', prefix):
            return None
        scope['codes'] = codes
    elif followup and (relative := _latest_followup_subject(prefix)):
        scope = _latest_followup_scope(task, scope, relative)
        if scope is None:
            return None
    else:
        if source == 'official':
            return None
        prefix = _LATEST_QUERY_HEAD.sub('', prefix).strip(' ，,')
        named = _LATEST_NAME.fullmatch(prefix.removesuffix('的').strip())
        if (not named or re.search(r'对象|标的|前者|后者|这只|这家|该股|上述|[、及与和或]', named['name'])
                or named['name'] in ('公司','基金','指数','股票','港股','A股')):
            return None
        if task.get('objects') and named['name'] not in task['objects']:
            return None
        scope.update(name=named['name'], market=named['market'])
    scopes = []
    for field, when in metrics:
        if separate and field == NAV_INDICATOR and any(series_family(code) != 'ETF' for code in codes):
            if compound and len(metrics) > 1:
                return None
            continue
        row = dict(scope, indicator=field)
        if version >= 3 and '交易日' in when:
            if field == 'close':
                row['require_latest_session'] = True
            elif field in LATEST_VALUATION_FIELDS:
                row['require_valuation_session'] = True
            elif field == NAV_INDICATOR and version >= STATED_LATEST_EXACT_NAV_VERSION:
                row['require_latest_session'] = True
                if no_calendar_lookup:
                    row['no_calendar_lookup'] = True
        scopes.append(row)
    return scopes


def _latest_followup_scope(task, scope, relative):
    from .evidence import declared_security_codes
    from .planning import conversation_context

    context = conversation_context(task)
    if context is None:
        return None
    codes = declared_security_codes(task)
    pair = context.get('comparison_codes', [])
    if relative == 'both' and len(codes) == 2:
        return dict(scope, codes=tuple(pair if set(pair) == set(codes) else codes))
    selected = context.get('comparison_subject')
    if len(codes) == 1 and (relative in ('omitted', 'single') or selected == codes[0]):
        return dict(scope, codes=tuple(codes))
    objects = task.get('objects', [])
    names = context.get('security_name_queries', [])
    if (relative != 'both' and not codes and len(objects) == 1 and objects[0] in names
            and scope.get('nav_source') != 'official'
            and (relative in ('omitted', 'single') or selected == objects[0])):
        return dict(scope, name=objects[0], market=context.get('latest_subject_market'))
    return dict(scope, needs_subject=True, subject_candidates=codes or names)


def _latest_clauses(text, *, valuation=False):
    pattern = _LATEST_VALUATION_CLAUSE_BOUNDARY if valuation else _LATEST_CLAUSE_BOUNDARY
    return [re.sub(r'^(?:并|以及|同时|另外|再)\s*', '', part.strip())
            for part in pattern.split(text) if part.strip()]


def latest_clauses_with_subject(task, clauses):
    """Supply an explicit prior security for a singular reference in this question."""
    from .evidence import declared_security_codes, filing_directory_scope, security_code_mentions
    from .financial_sources import direct_financial_scope
    from .index_sources import direct_weight_scope
    from .market_calendar import stated_direct_market_day

    declared = set(declared_security_codes(task))
    encoding = task.get('security_declaration_encoding', 1)
    question = _latest_question(task)
    seen = set()
    prior_lookup = None
    for clause in clauses:
        antecedent = None
        if prior_lookup and len(seen) == 1 and _LATEST_SINGLE_REFERENT.search(clause):
            previous, code = prior_lookup
            if (stated_direct_market_day(task, clause=previous)
                    or filing_directory_scope(task, clause=previous)
                    or direct_financial_scope(task, clause=previous)
                    or direct_weight_scope(task, clause=previous)
                    or question and _latest_clause_scopes(task, previous, question['id'], separate=True)):
                antecedent = code
        yield clause, antecedent
        if not _LATEST_NEGATED_HEAD.match(clause):
            codes = declared.intersection(security_code_mentions(clause, encoding=encoding))
            seen.update(codes)
            if len(codes) == 1:
                prior_lookup = (clause, next(iter(codes)))


def research_market_inputs_enabled(task):
    return task.get(STATED_LATEST_POLICY) == RESEARCH_MARKET_INPUT_VERSION


def _research_market_input(task, clause, question_id, prior_codes, allowed_codes):
    """Use the registered daily readout for a quoted input to a wider study."""
    from .evidence import security_code_matches
    from .market_calendar import _OTHER_QUOTE_BASIS

    match = _MARKET_INPUT_LIST.search(clause)
    if not match or not _MARKET_INPUT_TAIL.fullmatch(clause[match.end():]) or _OTHER_QUOTE_BASIS.search(clause):
        return []
    prefix = clause[:match.start()]
    if _MARKET_INPUT_HYPOTHETICAL.search(prefix):
        return []
    mentions = list(security_code_matches(prefix, encoding=task.get('security_declaration_encoding', 1)))
    codes = tuple(dict.fromkeys(item[1] for item in mentions))
    name = ''
    if mentions:
        start, end = mentions[0].start(1), mentions[-1].end(1)
        label = _MARKET_INPUT_PREFIX.sub('', prefix[:start], count=1).strip()
        if not _LATEST_SUBJECT_NAME.fullmatch(label) or _LATEST_SCOPE_WORDS.search(label):
            return []
        if not re.fullmatch(r'\s*(?:的|分别)?\s*', prefix[end:]):
            return []
        # An extra unbound name between two codes must not become a third security.
        for left, right in zip(mentions, mentions[1:]):
            if not re.fullmatch(r'\s*(?:、|与|和|及|以及)\s*[\u3400-\u9fff]{0,19}', prefix[left.end(1):right.start(1)]):
                return []
    else:
        remainder = _MARKET_INPUT_PREFIX.sub('', prefix, count=1).strip()
        plural = _MARKET_INPUT_PLURAL.fullmatch(remainder)
        singular = not remainder or _LATEST_SINGLE_REFERENT.fullmatch(remainder)
        if (singular or plural) and len(prior_codes) == (2 if plural else 1):
            codes = tuple(prior_codes)
        elif not plural and _LATEST_NAME.fullmatch(remainder.removesuffix('的')) and not _LATEST_SCOPE_WORDS.search(remainder):
            name = remainder.removesuffix('的')
        else:
            return []
    if not set(codes) <= allowed_codes or _LATEST_NEGATED_HEAD.match(clause):
        return []
    labels = _MARKET_INPUT_FIELDS.findall(match['labels'])
    if any(label in ('股价', '市盈率') for label in labels) and any(series_family(code) != '个股' for code in codes):
        return []
    aliases = {'股价': '收盘价', '市盈率': 'PE(TTM)'}
    when = '最新' if match['when'].startswith('当前') else match['when']
    query = '核对'+(name or '、'.join(codes))+when+'、'.join(aliases.get(label, label) for label in labels)
    return _latest_scopes(task, query, question_id, separate=bool(codes)) or []


def stated_mixed_latest_scopes(task):
    """Keep separately stated recent reads independent of the other questions."""
    if not mixed_latest_enabled(task) or not (question := _latest_question(task)):
        return []
    if latest_valuation_enabled(task) and len(direct := stated_direct_latest_scopes(task)) > 1:
        return direct
    from .draft_sources import _unquoted_local_scope, stated_access_restricted, stated_source_text
    from .subject_scope import scope_question

    text, excluded = valuation_exclusion_scope(task, stated_source_text(task, scope_question(task)))
    if stated_access_restricted(task, text, scoped_market=True):
        return []
    clauses = _latest_clauses(_unquoted_local_scope(text), valuation=latest_valuation_enabled(task))
    embedded = research_market_inputs_enabled(task)
    if embedded:
        clauses = [part.strip() for clause in clauses for part in _MARKET_INPUT_BOUNDARY.split(clause) if part.strip()]
    if len(clauses) < 2 and not embedded:
        return []
    ifind_only = bool(_IFIND_ONLY.search(text))
    scopes = []
    prior_codes, allowed = {}, set()
    if embedded:
        from .evidence import declared_security_codes, security_code_mentions

        allowed = set(declared_security_codes(task))
    for clause, antecedent in latest_clauses_with_subject(task, clauses):
        reads = _latest_clause_scopes(task, clause, question['id'], separate=True, antecedent=antecedent) or []
        if not reads and embedded:
            reads = _research_market_input(task, clause, question['id'], prior_codes, allowed)
        for scope in reads:
            if scope['indicator'] in excluded or _mixed_latest_excluded(scope, clauses, task):
                continue
            if ifind_only and scope['indicator'] == NAV_INDICATOR:
                if scope.get('nav_source') == 'official':
                    continue
                scope['nav_source'] = 'ifind'
            if scope not in scopes:
                scopes.append(scope)
        if embedded and not _LATEST_NEGATED_HEAD.match(clause):
            found = security_code_mentions(clause, encoding=task.get('security_declaration_encoding', 1))
            prior_codes.update((code, None) for code in found if code in allowed)

    def same_subject(left, right):
        if left.get('name'):
            return left['name'] == right.get('name') and left.get('market') == right.get('market')
        return not set(left.get('codes', ())).isdisjoint(right.get('codes', ()))

    return [scope for scope in scopes if not any(other is not scope and other['indicator'] == scope['indicator']
        and same_subject(scope, other) for other in scopes)]


def _mixed_latest_excluded(scope, clauses, task):
    from .evidence import security_code_mentions
    from .market_calendar import _CLOSE_NEGATION, _OTHER_QUOTE_BASIS

    if scope['indicator'] in LATEST_VALUATION_FIELDS:
        return any(_VALUATION_NEGATION.search(clause) and (
            not (codes := set(security_code_mentions(clause, encoding=task.get('security_declaration_encoding', 1))))
            or not codes.isdisjoint(scope.get('codes', ()))) for clause in clauses)
    nav = scope['indicator'] == NAV_INDICATOR
    negation, basis, label = (_EXPLICIT_NAV_EXCLUSION, _OTHER_NAV_MEASURE, '净值') if nav else (
        _CLOSE_NEGATION, _OTHER_QUOTE_BASIS, '收盘')
    for clause in clauses:
        codes = set(security_code_mentions(clause, encoding=task.get('security_declaration_encoding', 1)))
        if codes and codes.isdisjoint(scope.get('codes', ())):
            continue
        # An explicitly requested close in another subquery is not a change
        # to the NAV measure. Mask only the recognized latest-close metric.
        measure = (_LATEST_CLOSE.sub('', _LATEST_SESSION_CLOSE.sub('', clause))
                   if nav and task[STATED_LATEST_POLICY] >= STATED_LATEST_COMPOUND_VERSION else clause)
        if negation.search(clause) or basis.search(measure) and (
                label in clause or re.search(r'口径|为准|只(?:用|看|取)', clause)):
            return True
    return False


def stated_latest_cutoff(task, code, indicator, *, completed_session=False):
    """Use the market close for session cutoffs and a conservative NAV read window.

    Use the full-day auction boundary conservatively on Hong Kong half days.
    Daily NAV has no common intraday availability in this field contract. A
    requested session can end before its separate NAV observation is available.
    """
    cutoff = timepoint(task['cutoff']).astimezone(timezone(timedelta(hours=8)))
    if indicator == NAV_INDICATOR and not completed_session:
        return (cutoff.date()-timedelta(days=1)).isoformat()
    close = (16, 10) if code.endswith('.HK') else (15, 0)
    if (cutoff.hour, cutoff.minute) <= close:
        return (cutoff.date() - timedelta(days=1)).isoformat()
    return cutoff.date().isoformat()


def exact_session_nav(scope):
    return scope['indicator'] == NAV_INDICATOR and bool(scope.get('require_latest_session'))



VALUATION_HISTORY_POLICY = 'draft_valuation_history_version'
VALUATION_HISTORY_VERSION = 2
VALUATION_HISTORY_VERSIONS = (1, VALUATION_HISTORY_VERSION)
VALUATION_ATTRIBUTION_POLICY = 'draft_valuation_attribution_version'
VALUATION_ATTRIBUTION_VERSION = 1
VALUATION_HISTORY_MONTHS = 12
VALUATION_HISTORY_REQUEST_PREFIX = 'draft_valuation_history_'
_VALUATION_JUDGMENT = re.compile(
    r'估值.{0,9}(?:匹配|合理|高估|低估|偏高|偏低|分位|历史位置|贵不贵|什么水平|高吗|低吗|贵吗)'
    r'|(?:市盈率|PE(?:\s*[(（]TTM[)）])?).{0,8}(?:分位|历史位置|偏高|偏低|合理)'
    r'|市盈率在历史上(?:高|低|处于什么水平)|贵不贵', re.I)
_VALUATION_HISTORY_EXCLUSION = re.compile(
    r'(?:不必|无需|不用|不要|不查|不看|禁止|不得|不做|不分析|不评价|不判断|不考虑|别查|别看|请勿)'
    r'[^，,。；;！？!?\n]{0,9}(?:历史|估值|市盈率|(?<![A-Za-z])PE(?![A-Za-z]))', re.I)
_VALUATION_SPECIFIED_BASIS = re.compile(
    r'(?:仅|只)(?:查|核对|看|比较|用|按|取)|市净率|动态市盈率|静态市盈率|预测市盈率'
    r'|同行|同业|可比公司|股息率|DCF|PB(?:\b|[(（])', re.I)
_VALUATION_SPECIFIED_WINDOW = re.compile(
    r'(?:19|20)[0-9]{2}\s*(?:年|[-/.])'
    r'|(?:近|过去|最近|前)\s*(?:[0-9]+|[一二两三四五六七八九十]+)\s*(?:个)?(?:月|年|季|周|日)')


def valuation_history_scope(task):
    """Choose a default same-company benchmark for an actual valuation judgment."""
    if task.get(VALUATION_HISTORY_POLICY) not in VALUATION_HISTORY_VERSIONS or not (question := _latest_question(task)):
        return None
    from .draft_sources import _unquoted_local_scope, local_scope_text, stated_source_text
    from .evidence import declared_security_codes, identity_candidates
    from .subject_scope import scope_question

    text, excluded = valuation_exclusion_scope(task, stated_source_text(task, scope_question(task)))
    text = _unquoted_local_scope(local_scope_text(without_market_source_directives(task, text)))
    if PE_TTM_INDICATOR in excluded or _VALUATION_HISTORY_EXCLUSION.search(text):
        return None
    # A selected peer, model, period or other valuation basis stays with the
    # user's plan. The default history must not silently substitute for it.
    clauses = [part.strip() for clause in _latest_clauses(text, valuation=True)
               for part in _MARKET_INPUT_BOUNDARY.split(clause) if part.strip()]
    if (not any(_VALUATION_JUDGMENT.search(clause) and not _LATEST_NEGATED_HEAD.match(clause) for clause in clauses)
            or _VALUATION_SPECIFIED_BASIS.search(text)
            or any(_VALUATION_SPECIFIED_WINDOW.search(clause)
                   for clause in clauses if _VALUATION_JUDGMENT.search(clause))):
        return None
    codes = set(declared_security_codes(task))
    if not codes:
        codes = {code for item in identity_candidates(task) if item['business_lookup_authorized']
                 and item['query'] in text for code in item['exact_lookup_codes']}
    if len(codes) != 1:
        return None
    code, = codes
    if series_family(code) != '个股':
        return None
    return dict(code=code, question_id=question['id'], cutoff=stated_latest_cutoff(task, code, PE_TTM_INDICATOR))


def _month_start(day, offset=0):
    year, month = divmod(day.year*12 + day.month - 1 + offset, 12)
    return date(year, month + 1, 1)


def valuation_history_requests(task, scope):
    """Use a bounded recent window plus one older slice if the sample needs it."""
    from .draft_sources import stated_access_restricted

    if stated_access_restricted(task, task['question'], scoped_market=True):
        return []
    end = date.fromisoformat(scope['cutoff'])
    recent_start = end-timedelta(days=MAX_SERIES_WINDOW_DAYS-1)
    history_start = _month_start(end, -VALUATION_HISTORY_MONTHS-1)
    return [_valuation_history_request(task, scope, start, stop) for start, stop in (
        (recent_start, end), (history_start, recent_start-timedelta(days=1)))]


def _valuation_history_request(task, scope, start, end):
    code = scope['code']
    params = dict(codes=code, functionpara=series_settings(task, code),
                  indipara=[dict(indicator=PE_TTM_INDICATOR, indiparams=SERIES_FAMILIES['个股'][PE_TTM_INDICATOR][:])],
                  startdate=start.isoformat(), enddate=end.isoformat())
    questions = [scope['question_id']]
    return dict(id=VALUATION_HISTORY_REQUEST_PREFIX+fingerprint((questions, params))[:24],
                source='ifind', operation='date_sequence', parameters=params, question_ids=questions,
                selection_reason='取得同一标的历史PE(TTM)，比较当前读数在历史样本中的位置。')


def _positive_valuation_cell(rows):
    if not rows or len({(row.get('raw_value', row['value']), row['unit'], row.get('series_basis')) for row in rows}) != 1:
        return None
    row = rows[0]
    return row if type(row['value']) in (int, float) and row['value'] > 0 else None


def _valuation_history_cells(task, scope, facts, records):
    """Bind the latest cell and the last observed cells in each comparison month."""
    from .draft_sources import _sequence_day
    from .evidence import current_query_reference

    code = scope['code']
    if series_family(code) != '个股' or scope['cutoff'] != stated_latest_cutoff(task, code, PE_TTM_INDICATOR):
        return None
    cutoff = date.fromisoformat(scope['cutoff'])
    first = _month_start(cutoff, -VALUATION_HISTORY_MONTHS-1)
    permitted = {ident for ident, record in records.items()
                 if (record.get('source'), record.get('operation')) == ('ifind', 'date_sequence')
                 and scope['question_id'] in record.get('question_ids', ())
                 and current_query_reference(task, record) is not None}
    dates = {}
    for row in facts:
        if (row.get('artifact_id') not in permitted or row['code'] != code or row['field'] != PE_TTM_INDICATOR
                or type(row.get('value')) not in (int, float) or not math.isfinite(row['value'])):
            continue
        day = _sequence_day(row['period'])
        if day is not None and first <= day <= cutoff:
            dates.setdefault(day, []).append(row)

    if not dates or (cutoff - (latest := max(dates))).days >= 30:
        return None
    current = _positive_valuation_cell(dates[latest])
    if current is None:
        return None
    months = {}
    for day in sorted(dates):
        if day < _month_start(latest):
            months[_month_start(day)] = dates[day]
    return current, {_month_start(latest, offset): months.get(_month_start(latest, offset), ())
                     for offset in range(-VALUATION_HISTORY_MONTHS, 0)}


def valuation_history_sample(task, scope, facts, records):
    """Select one positive observation per prior month, using original, owned cells."""
    if not (cells := _valuation_history_cells(task, scope, facts, records)):
        return None
    current, months = cells
    history = [_positive_valuation_cell(rows) for rows in months.values()]
    if any(row is None or row['unit'] != current['unit'] or row.get('series_basis') != current.get('series_basis')
           for row in history):
        return None
    return current, history


def valuation_history_followup(task, scope, facts, records):
    """Fill the span actually missing from a short response, once and from iFinD."""
    from .draft_sources import stated_access_restricted

    if (stated_access_restricted(task, task['question'], scoped_market=True)
            or not (cells := _valuation_history_cells(task, scope, facts, records))):
        return None
    missing = [month for month, rows in cells[1].items() if not rows]
    if not missing:
        return None
    end = _month_start(missing[-1], 1)-timedelta(days=1)
    start = max(missing[0], end-timedelta(days=MAX_SERIES_WINDOW_DAYS-1))
    return _valuation_history_request(task, scope, start, end)



def valuation_attribution_enabled(task):
    return task.get(VALUATION_ATTRIBUTION_POLICY) == VALUATION_ATTRIBUTION_VERSION


def _valuation_recent_window(scope):
    end = date.fromisoformat(scope['cutoff'])
    return (end-timedelta(days=29)).isoformat(), end.isoformat()


def valuation_close_request(task, scope):
    """Reuse the usual recent quote window at full resolution for a valuation comparison."""
    from .draft_sources import stated_access_restricted
    from .evidence import IFIND_QUOTE_FUNCTION

    if not valuation_attribution_enabled(task) or stated_access_restricted(task, task['question'], scoped_market=True):
        return None
    code = scope['code']
    reads = {read['indicator'] for read in stated_mixed_latest_scopes(task)
             if code in read.get('codes', ()) and not read.get('require_latest_session')}
    if not {'close', PE_TTM_INDICATOR} <= reads:
        return None
    start, end = _valuation_recent_window(scope)
    parameters = dict(codes=code, startdate=start, enddate=end,
                      indicators='close', functionpara=copy.deepcopy(IFIND_QUOTE_FUNCTION))
    questions = [scope['question_id']]
    return dict(id='draft_valuation_close_'+fingerprint((questions, parameters))[:24],
                source='ifind', operation='cmd_history_quotation', parameters=parameters, question_ids=questions,
                selection_reason='对齐近期收盘价和滚动市盈率，分解同一期间价格变化。')


def valuation_attribution_points(task, scope, facts, records):
    """Use the first and last observed close of the bound window, with matching PE cells."""
    from .draft_sources import _sequence_day
    from .evidence import IFIND_QUOTE_FUNCTION, current_query_reference, current_snapshot_reference

    code = scope['code']
    first, cutoff = _valuation_recent_window(scope)
    if scope['cutoff'] != stated_latest_cutoff(task, code, PE_TTM_INDICATOR):
        return None
    sources = {}
    for ident, record in records.items():
        if record.get('source') != 'ifind' or scope['question_id'] not in record.get('question_ids', ()):
            continue
        params = record.get('parameters', {})
        codes = params.get('codes', '')
        if not isinstance(codes, str) or code not in codes.split(','):
            continue
        if record.get('operation') == 'cmd_history_quotation':
            if (params.get('functionpara') == IFIND_QUOTE_FUNCTION and params.get('enddate') == cutoff
                    and isinstance(params.get('startdate'), str) and params['startdate'] <= first
                    and current_snapshot_reference(task, record) and not latest_observation_selected(task, record)):
                sources[ident] = 'close'
        elif (record.get('operation') == 'date_sequence' and params.get('functionpara') == series_settings(task, code)
                and dict(indicator=PE_TTM_INDICATOR, indiparams=SERIES_FAMILIES['个股'][PE_TTM_INDICATOR])
                in (params.get('indipara') or ()) and current_query_reference(task, record)):
            sources[ident] = PE_TTM_INDICATOR
    dates = {'close': {}, PE_TTM_INDICATOR: {}}
    for row in facts:
        field = sources.get(row.get('artifact_id'))
        if (not field or row['field'] != field or row['code'] != code or row.get('previous_close_for')
                or type(row['value']) not in (int, float) or not math.isfinite(row['value'])):
            continue
        day = _sequence_day(row['period'])
        if day is not None and first <= day.isoformat() <= cutoff:
            dates[field].setdefault(day, []).append(row)
    closes, valuations = dates['close'], dates[PE_TTM_INDICATOR]
    if len(closes) < 2 or not valuations or max(closes) != max(valuations):
        return None
    start, end = min(closes), max(closes)

    def comparable_pair(rows):
        pair = []
        for day in (start, end):
            readings = rows.get(day, ())
            if not readings or len({(r['value'], r['unit'], r.get('series_basis')) for r in readings}) != 1:
                return None
            pair.append(readings[0])
        left, right = pair
        if (left['value'] <= 0 or right['value'] <= 0 or left['unit'] != right['unit']
                or left.get('series_basis') != right.get('series_basis')):
            return None
        return pair

    price = comparable_pair(closes)
    multiples = comparable_pair(valuations)
    return [*price, *multiples] if price and multiples else None


def valuation_history_writing_rows(record, rows):
    """Keep daily source cells in the workpaper and send monthly reads for automatic history."""
    needs = record.get('data_need_ids', ())
    if not needs or not all(ident.startswith(VALUATION_HISTORY_REQUEST_PREFIX) for ident in needs):
        return rows
    from .draft_sources import _sequence_day

    by_month = {}
    for row in sorted(rows, key=lambda item: item['period']):
        day = _sequence_day(row['period'])
        if day is not None and row.get('value') is not None:
            by_month[_month_start(day)] = row
    return list(by_month.values()) or rows[:1]


def stated_series_session_scope(task, need):
    """Return the requested session scope for a selected series or public NAV record."""
    if not latest_valuation_enabled(task) or not need['id'].startswith(STATED_LATEST_REQUEST_PREFIX):
        return None
    if (need.get('source'), need.get('operation')) == ('official', 'etf_pcf'):
        if task[STATED_LATEST_POLICY] >= STATED_LATEST_EXACT_NAV_VERSION:
            scope = _stated_metric_scope(task, NAV_INDICATOR, need['parameters']['ts_code'], exact_session=True)
            if scope is not None and not scope.get('no_calendar_lookup'):
                return scope
        return None
    if need.get('operation') != 'date_sequence' or not need.get('observation_scope'):
        return None
    field = _stated_observation_field(need)
    if (field in LATEST_VALUATION_FIELDS or
            field == NAV_INDICATOR and task[STATED_LATEST_POLICY] >= STATED_LATEST_EXACT_NAV_VERSION):
        return _stated_metric_scope(task, field, need['parameters']['codes'], exact_session=True)
    return None


def compile_observation_window(need, selection):
    """Resolve an explicit selector into ordinary provider parameters once."""
    result = copy.deepcopy(need)
    params = result.get('parameters')
    if not isinstance(params, dict) or 'observation_window' not in params:
        return result
    if (not isinstance(selection, dict) or selection.get('version') != 1
            or result.get('source') != 'ifind' or result.get('operation') not in OBSERVATION_OPERATIONS
            or 'startdate' in params or 'enddate' in params or 'observation_scope' in result):
        raise ContractError('最近观测须显式选择本任务登记窗口，不能混入区间日期')
    window = params.pop('observation_window')
    if (not isinstance(window, dict) or set(window) != {'mode', 'as_of', 'lookback_calendar_days'}
            or window['mode'] != 'latest_available' or window['as_of'] != selection['as_of']
            or type(window['lookback_calendar_days']) is not int
            or window['lookback_calendar_days'] not in selection['lookback_calendar_days']):
        raise ContractError('最近观测窗口未绑定本任务截止日或回看天数无效')
    end = date.fromisoformat(window['as_of'])
    params.update(startdate=(end-timedelta(days=window['lookback_calendar_days']-1)).isoformat(), enddate=end.isoformat())
    result['observation_scope'] = dict(version=1, **window)
    return result


def observation_request_selection(task, need, *, recorded=False):
    """Allow the typed direct route to use its actual market's daily boundary."""
    selection = observation_selection(task)
    params = need.get('parameters')
    ident = need.get('id')
    if (selection is None or type(task.get(STATED_LATEST_POLICY)) is not int
            or task[STATED_LATEST_POLICY] not in STATED_LATEST_VERSIONS
            or not isinstance(ident, str) or not ident.startswith(STATED_LATEST_REQUEST_PREFIX)
            or not isinstance(params,dict) or not isinstance(params.get('codes'),str)):
        return selection
    if latest_valuation_enabled(task):
        field = _stated_observation_field(need)
        supported = (field in LATEST_VALUATION_FIELDS
                     or task[STATED_LATEST_POLICY] >= STATED_LATEST_COMPOUND_VERSION and field == NAV_INDICATOR)
        if supported and (recorded or _stated_metric_scope(task, field, params['codes'])):
            selection['as_of'] = stated_latest_cutoff(task, params['codes'].split(',')[0], field)
            return selection
    scope = stated_latest_request(task)
    operation = need.get('operation')
    if scope is None and mixed_latest_enabled(task) and operation in OBSERVATION_OPERATIONS:
        from .subject_scope import subject_key

        requested = {subject_key(code) for code in params['codes'].split(',')}
        items = params.get('indipara')
        fields = ([item.get('indicator') for item in items if isinstance(item, dict)] if isinstance(items, list) else [])
        field = 'close' if operation == 'cmd_history_quotation' else NAV_INDICATOR
        matches = params.get('indicators') == field if field == 'close' else fields == [field]
        if matches:
            scope = next((row for row in stated_mixed_latest_scopes(task) if row['indicator'] == field
                          and requested <= {subject_key(code) for code in row['codes']}), None)
    if scope is None and not recorded:
        return selection
    codes = params['codes'].split(',')
    field = scope['indicator'] if scope is not None else (
        NAV_INDICATOR if need.get('operation') == 'date_sequence' else 'close')
    expected = 'date_sequence' if field == NAV_INDICATOR else 'cmd_history_quotation'
    if need.get('operation') == expected:
        limits = {stated_latest_cutoff(task, code, field) for code in codes}
        if len(limits) == 1:
            selection['as_of'], = limits
    return selection


def _stated_observation_field(need):
    if need.get('operation') != 'date_sequence':
        return None
    fields = need['parameters'].get('indipara')
    return fields[0].get('indicator') if isinstance(fields, list) and len(fields) == 1 and isinstance(fields[0], dict) else None


def _stated_metric_scope(task, field, codes, *, exact_session=False):
    from .evidence import identity_candidates
    from .subject_scope import subject_key

    requested = {subject_key(code) for code in codes.split(',')}
    scopes = stated_direct_latest_scopes(task) or stated_mixed_latest_scopes(task)
    for scope in scopes:
        if scope['indicator'] != field or exact_session and not (
                scope.get('require_valuation_session') or scope.get('require_latest_session')):
            continue
        candidates = {subject_key(code) for code in scope.get('codes', ())}
        if scope.get('name'):
            rows = [row for row in identity_candidates(task)
                    if row['query'] == scope['name'] and row['business_lookup_authorized']]
            market = scope.get('market')
            offered = {code for row in rows for code in row['codes']
                       if market is None or code.endswith(('.SH', '.SZ') if market == 'A股' else '.HK')}
            if len(offered) == 1:
                candidates.update(subject_key(code) for row in rows for code in offered.intersection(row['exact_lookup_codes']))
        if requested <= candidates:
            return scope
    return None


def validate_observation_scope(task, need, *, recorded=False, retained=False):
    scope = need.get('observation_scope')
    if scope is None:
        return
    params = need.get('parameters')
    if (not isinstance(scope, dict) or set(scope) != {'version','mode','as_of','lookback_calendar_days'}
            or type(scope['version']) is not int or scope['version'] != 1
            or not isinstance(params,dict)):
        raise ContractError('市场观测范围无效')
    if retained:
        # The bound end date was already resolved when this older request ran.
        # Reapplying the market-close or NAV rule would shift its window again.
        selection = observation_selection(task)
        if selection is not None:
            selection['as_of'] = scope['as_of']
    else:
        selection = observation_request_selection(task, need, recorded=recorded)
    proposed = copy.deepcopy(need);proposed.pop('observation_scope')
    window = {key:value for key,value in scope.items() if key != 'version'}
    proposed['parameters'] = {key:value for key,value in params.items() if key not in ('startdate','enddate')}
    proposed['parameters']['observation_window'] = window
    expected = compile_observation_window(proposed, selection)
    if expected != need:
        raise ContractError('最近观测范围与实际请求日期不一致')


def record_observation_scope(record, need, question_ids):
    """Scope belongs to an individual use, including reuse of an API snapshot."""
    scope = need.get('observation_scope')
    if scope is None:
        return
    item = dict(data_need_id=need['id'], question_ids=list(question_ids),
        task_id=record['task_id'], research_version=record['research_version'],
        request_hash=record['request_hash'], scope=copy.deepcopy(scope))
    item['binding_hash'] = fingerprint(item)
    rows = record.setdefault('observation_requests', [])
    if item not in rows:rows.append(item)


def latest_observation_selected(task, record):
    """Do not narrow a snapshot also requested as a complete date series."""
    if observation_selection(task) is None:
        return False
    rows = record.get('observation_requests', [])
    if not rows:
        return False
    from .contracts import fingerprint
    covered = set();prior_uses=set()
    for row in rows:
        if not isinstance(row,dict):raise ContractError('市场观测范围记录无效')
        body = {key:value for key,value in row.items() if key != 'binding_hash'}
        if (fingerprint(body) != row.get('binding_hash') or row.get('task_id') != task['id']
                or row.get('data_need_id') not in record.get('data_need_ids',[])
                or row.get('request_hash') != record.get('request_hash')):
            raise ContractError('市场观测范围记录不属于本次原始请求')
        need = dict(id=row['data_need_id'],source=record['source'],operation=record['operation'],
            parameters=record['parameters'],question_ids=row['question_ids'],observation_scope=row['scope'])
        version=row.get('research_version')
        if version!=task['version']:
            prior=task.get('update_from',{}).get('version')
            if (type(version) is not int or type(prior) is not int or not 1<=version<=prior<task['version']
                    or row['scope']['as_of']>market_date(task['cutoff'])):
                raise ContractError('市场观测范围未登记为本任务历史版本')
            # A reused original remains readable at its actual dates. Its old
            # latest view does not silently become latest for this new cutoff.
            validate_observation_scope(task, need, retained=True)
            prior_uses.add(row['data_need_id'])
            continue
        validate_observation_scope(task,need,recorded=True)
        covered.add(row['data_need_id'])
    return bool(covered) and (set(record.get('data_need_ids',[]))-prior_uses) <= covered
