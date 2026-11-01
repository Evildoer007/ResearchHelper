"""OH-compatible, task-bound iFinD research panels, not numeric approval.

The field bindings mirror inspected OH dashboard source. They are copied
constants, not runtime imports. Account permission, field units and historical
vintages remain separate acceptance gates.
"""
from __future__ import annotations

import copy
import math
from datetime import date
import re

from .contracts import ContractError, market_date, timepoint

SERIES_DOCUMENTATION = "https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/manual.html"
SERIES_FAMILIES = {
    "个股": {"ths_pe_ttm_stock": ["", "100"], "ths_pb_mrq_stock": [""],
        "ths_dividend_yield_ttm_ex_sd_stock": [""], "ths_total_shares_stock": [""],
        "ths_market_value_stock": [""], "ths_turnover_ratio_stock": [""]},
    "指数": {"ths_pe_index": ["", "100", "100"], "ths_pb_index": ["", "100", "100"],
        "ths_ps_index": ["", "100", "100"]},
    "ETF": {"ths_unit_nvnf_fund": [""], "ths_fund_shares_fund": [""]},
}
SERIES_LABELS = {"ths_pe_ttm_stock": "PE(TTM)", "ths_pb_mrq_stock": "PB(MRQ)",
    "ths_dividend_yield_ttm_ex_sd_stock": "股息率(TTM)", "ths_total_shares_stock": "总股本",
    "ths_market_value_stock": "总市值", "ths_turnover_ratio_stock": "换手率",
    "ths_pe_index": "指数PE", "ths_pb_index": "指数PB", "ths_ps_index": "指数PS",
    "ths_unit_nvnf_fund": "单位净值", "ths_fund_shares_fund": "基金份额"}
SERIES_ROUTING_VERSION = 2
BATCH_POLICY = 'series_batch_version'
BATCH_MAX_CODES = 20
BATCH_MAX_CELLS = 50000


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
    if (date.fromisoformat(dates[1]) - date.fromisoformat(dates[0])).days > 364:
        raise ContractError("日期序列单次窗口最多365个日历日，不自动拆分并扩大请求预算")
    if len(codes) > 1:
        capacity = batch_capacity(task)
        days = (date.fromisoformat(dates[1])-date.fromisoformat(dates[0])).days+1
        if capacity is None or len(codes)*len(fields)*days > capacity['max_cells']:
            raise ContractError('批量日期序列须先取得本任务账号策略，并满足单次单元格上限')
    return dates


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
                elif type(value) not in (int, float) or not math.isfinite(value):
                    raise ContractError('日期序列含非有限数值或占位符，不替换为零')
                else:
                    numeric += 1
        cells += numeric
        tables.append(dict(code=code, rows=len(days), numeric_cells=numeric, missing_fields=missing_fields))
    return dict(version=1, requested_codes=requested, returned_codes=[row['code'] for row in tables],
        missing_codes=[code for code in requested if code not in seen], tables=tables,
        numeric_cells=cells, missing_cells=missing, raw_only=True, financial_approval=False,
        continuous_calendar_approved=False, absence_proved=False)
