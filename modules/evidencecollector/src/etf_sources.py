"""Public SSE ETF PCF observations with explicit previous-trading-day NAV.

The exchange client labels NAV in yuan and places it under PRE_TRADING_DAY.
This adapter retains the actual JSON bytes. It neither re-labels the PCF date
as a valuation date nor invents a historical endpoint or an intraday NAV.
"""
from __future__ import annotations

import base64
import copy
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import time
import urllib.error
from urllib.parse import urlencode, urlsplit

from .contracts import ContractError, DuplicateJSONField, fingerprint, market_date, unique_json_object

POLICY = 'official_etf_pcf_version'
OPERATION = 'etf_pcf'
QUALIFIED = 'official.' + OPERATION
DOCUMENTATION = 'https://www.sse.com.cn/disclosure/fund/etflist/index.shtml'
CLIENT = 'https://www.sse.com.cn/xhtml/home/2021public/querySearch/search_fundInformation_2021.js'
SQL_ID = 'COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C'
MAX_BYTES = 2*1024*1024
FIELD = dict(raw_unit='元', unit='元', factor=1,
    definition='上交所申赎清单NAV字段，基金份额净值；所属日为PRE_TRADING_DAY，不是公告日TRADING_DAY、实时IOPV或累计净值')
REGISTRY = {QUALIFIED: dict(version=1, kind='上交所ETF申赎清单前一交易日净值',
    raw_only=False, fields={'unit_nav': FIELD}, parameters=['ts_code'], documentation=DOCUMENTATION)}


def enabled(task):
    return type(task.get(POLICY)) is int and task[POLICY] in (1, 2)


def share_context_enabled(task):
    return isinstance(task, dict) and type(task.get(POLICY)) is int and task[POLICY] == 2


def request_url(code):
    if not isinstance(code, str) or not re.fullmatch(r'5\d{5}\.SH', code):
        raise ContractError('上交所ETF渠道仅接受明确的沪市基金代码，不转换其他市场')
    return 'https://query.sse.com.cn/commonQuery.do?' + urlencode(dict(
        isPagination='false', FUNDID2=code[:6], sqlId=SQL_ID))


def validate_request(task, parameters, *, require_identity=True):
    if not enabled(task):
        raise ContractError('本任务未登记上交所ETF申赎清单渠道')
    if not isinstance(parameters, dict) or set(parameters) != {'ts_code'}:
        raise ContractError('ETF清单须选择单个完整代码，不接受地址、日期或自定义查询')
    url = request_url(parameters['ts_code'])
    if require_identity:
        from .evidence import available_security_codes
        if parameters['ts_code'] not in available_security_codes(task):
            raise ContractError('ETF清单代码须由本任务明确声明或核验映射')
    return url


def request_shapes(task):
    if not enabled(task):
        return []
    from .evidence import available_security_codes
    codes = [code for code in available_security_codes(task) if re.fullmatch(r'5\d{5}\.SH', code)]
    if not codes:
        return []
    return [dict(source='official', operation=OPERATION, qualified_id_for_lookup_only=QUALIFIED,
        parameter_keys=['ts_code'], available_parameter_sets=[dict(ts_code=code) for code in codes],
        credential_required=False, raw_only=False, numeric_fields=['unit_nav'],
        selection_rule='iFinD优先。可按问题选用上交所清单核对净值所属日、份额与单位。当前沪市ETF的最新净值遇到iFinD限额时，报告注明公开清单来源。',
        limitation='仅取得站点实际返回的最近一份清单；不能请求历史日期。NAV属于PRE_TRADING_DAY，不能用TRADING_DAY代替，也不证明历史当时可得。')]


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{8}', value):
        raise ContractError('ETF清单缺少明确的实际日期')
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:])).isoformat()
    except ValueError:
        raise ContractError('ETF清单日期无效') from None


def parse_snapshot(payload, code, cutoff):
    request_url(code)
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_BYTES:
        raise ContractError('ETF清单原始字节为空或超出2MB')
    try:
        response = json.loads(payload.decode('utf-8-sig'), object_pairs_hook=unique_json_object)
    except DuplicateJSONField:
        raise
    except (UnicodeError, ValueError):
        raise ContractError('ETF清单没有返回可解析JSON，不把登录页或错误页作为原文') from None
    rows = response.get('result') if isinstance(response, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ContractError('ETF清单须返回唯一基金记录，空结果或多结果不能取第一条')
    row = rows[0]
    if row.get('TRADE_CODE') != code[:6] or not isinstance(row.get('FUND_NAME'), str) or not row['FUND_NAME'].strip():
        raise ContractError('ETF清单交易代码或基金名称与请求不符')
    announced, valuation = _day(row.get('TRADING_DAY')), _day(row.get('PRE_TRADING_DAY'))
    if not valuation < announced <= market_date(cutoff):
        raise ContractError('ETF清单两类日期倒置或晚于研究截止日，不改写为所需日期')
    token = row.get('NAV')
    if not isinstance(token, str) or not re.fullmatch(r'￥[0-9]+(?:\.[0-9]+)?', token):
        raise ContractError('ETF净值缺少已登记的人民币原值格式，不补单位或数值')
    try:
        nav = Decimal(token[1:])
        if not nav.is_finite() or nav <= 0:
            raise InvalidOperation
    except InvalidOperation:
        raise ContractError('ETF净值原值不是有效正数') from None
    return dict(code=0, data=[dict(ts_code=code, trade_date=valuation, unit_nav=str(nav))],
        exchange_origin=dict(version=1, url=request_url(code), documentation=DOCUMENTATION,
            field_presentation=CLIENT, announcement_date=announced, valuation_date=valuation,
            fund_name=row['FUND_NAME'], raw_token=token),
        original_sha256=hashlib.sha256(payload).hexdigest(),
        original_base64=base64.b64encode(payload).decode('ascii'))


def validate_snapshot(record, cutoff):
    snapshot = record.get('snapshot')
    try:
        payload = base64.b64decode(snapshot['original_base64'], validate=True)
        actual = parse_snapshot(payload, record['parameters']['ts_code'], cutoff)
    except (KeyError, TypeError, ValueError):
        raise ContractError('ETF清单缺少可重读的完整原始响应') from None
    if snapshot != actual or fingerprint(snapshot) != record.get('snapshot_hash', record.get('hash')):
        raise ContractError('ETF清单原始响应、字段或日期角色被改变')
    return actual


def calculation_context(record, result):
    """Describe only roles proven by this exact retained exchange record."""
    try:
        if (record.get('kind') != 'api' or record.get('status') != '成功待核验'
                or fingerprint(dict(source='official', operation=OPERATION,
                    parameters=record.get('parameters'))) != record.get('request_hash')):
            raise ContractError('ETF清单请求或状态无效')
        # This check has no task and therefore only verifies the response's own
        # chronology. The evidence gate separately enforces the task cutoff.
        raw = record.get('snapshot', {})
        snapshot = validate_snapshot(record, raw['exchange_origin']['announcement_date'])
        code = record['parameters']['ts_code']
        fixed = dict(currency='CNY', valuation_date=snapshot['exchange_origin']['valuation_date'],
            nav_kind='official')
        if '交易型开放式' in snapshot['exchange_origin']['fund_name']:
            fixed['share_class'] = 'listed_etf_unit:'+code
        rows = ['security_id', 'fund_id', 'entity_id', 'observation_date']
        fields = set(rows) | set(fixed)
        basis = dict(kind='api', artifact_id=record['id'],
            snapshot_hash=record.get('snapshot_hash', record.get('hash')))
        result.update(status='registered_constraints', registered_numeric_fields=['unit_nav'],
            row_fields=rows, fixed_fields=fixed, column_fields={}, unavailable_fields=[],
            basis_templates={field: dict(basis, field=field) for field in sorted(fields)},
            identity_rule='仅对应本响应的实际交易代码，不证明名称映射、A/C份额或历史可得。',
            rule='净值所属日来自PRE_TRADING_DAY；公告日来自TRADING_DAY。仅登记人民币单位净值和上市交易份额，不是实时IOPV、收盘价或累计净值。')
    except (ContractError, KeyError, TypeError, ValueError):
        result['reason'] = 'ETF清单原始字节、请求、单位或日期角色未通过一致性核对。'
    return result


def validate_share_context(task, item, record, anchor):
    """One same-code, same-day share identity, not arbitrary cross-API context."""
    from .evidence import _api_cell, _api_context_date, current_snapshot_reference
    artifact = item.get('artifact', {})
    if (not share_context_enabled(task) or task.get('research_mode') != 'current'
            or anchor != dict(kind='api', artifact_id=record.get('id'),
                snapshot_hash=record.get('hash'), field='share_class')
            or record.get('source') != 'official' or record.get('operation') != OPERATION
            or artifact.get('source') != 'ifind' or artifact.get('operation') != 'cmd_history_quotation'
            or current_snapshot_reference(task, record) is None
            or current_snapshot_reference(task, artifact) is None):
        raise ContractError('跨接口份额口径未绑定本任务的两次实际取数，不接受任意借用')
    validate_request(task, record['parameters'])
    snapshot = validate_snapshot(record, task['cutoff'])
    _, code, observed, column = _api_cell(artifact, item.get('record_locator', {}))
    observed = _api_context_date(observed)
    if (column != 'close' or code != record['parameters']['ts_code'] or item.get('object') != code
            or item.get('period') != observed or observed != snapshot['exchange_origin']['valuation_date']):
        raise ContractError('份额口径须来自同代码、同观测日的ETF清单，不跨日或跨对象借用')
    expected = calculation_context(record, dict(status='unavailable')).get('fixed_fields', {}).get('share_class')
    if expected is None or item.get('calculation_context', {}).get('share_class') != expected:
        raise ContractError('上市份额口径与实际清单不一致，不推断A/C类别')
    return True


def share_context_choices(task, artifact, locator, object_code, *, allowed_ids=None):
    """Finite explicit anchors; no facts, numeric fields or context are adopted."""
    if not share_context_enabled(task):
        return []
    from .evidence import _api_cell, _api_context_date
    try:
        _, code, observed, column = _api_cell(artifact, locator)
        observed = _api_context_date(observed)
    except (ContractError, KeyError, TypeError):
        return []
    if code != object_code or column != 'close':
        return []
    result = []
    for record in task.get('source_records', []):
        if allowed_ids is not None and record.get('id') not in allowed_ids:
            continue
        if record.get('source') != 'official' or record.get('operation') != OPERATION:
            continue
        anchor = dict(kind='api', artifact_id=record['id'], snapshot_hash=record.get('hash'), field='share_class')
        control = dict(artifact=artifact, object=code, period=observed, record_locator=locator,
            calculation_context=dict(share_class='listed_etf_unit:'+code))
        try:
            validate_share_context(task, control, record, anchor)
        except (ContractError, KeyError, TypeError):
            continue
        binding = dict(task_id=task['id'], research_version=task['version'], input_hash=task['input_hash'],
            price_id=artifact['id'], price_hash=artifact.get('hash'), locator=locator, anchor=anchor)
        result.append(dict(context_ref='etf_share_'+fingerprint(binding)[:24], field='share_class',
            value='listed_etf_unit:'+code, basis=anchor,
            limitation='仅本次同代码、同净值所属日的上市份额口径，不批准数字、历史可得或投资判断。'))
    return result


def join_price_share_context(task, artifact, spec):
    """Join a unique registered same-day share identity, never infer a class.

    The original price snapshot remains unchanged. Both sources must be in
    this task's readable inputs; the ordinary source and financial checks run
    afterwards. An explicit or ambiguous context is never overwritten.
    """
    version = task.get('registered_etf_share_join_version')
    if version is None:
        return spec
    if type(version) is not int or version != 1:
        raise ContractError('已登记ETF份额关联版本无效')
    if (task.get('research_mode') != 'current' or task.get('intent') == '原件内容核对'
            or task.get('run_mode') not in ('model', 'host')
            or not share_context_enabled(task) or artifact.get('source') != 'ifind'
            or artifact.get('operation') != 'cmd_history_quotation'):
        return spec
    methods = {m for q in task.get('questions', []) for m in q.get('method_needs', [])}
    if 'etf_daily_nav_premium_v1' not in methods:
        return spec
    values, bases = spec.get('calculation_context'), spec.get('calculation_context_basis')
    if (not isinstance(values, dict) or not isinstance(bases, dict)
            or 'share_class' in values or 'share_class' in bases
            or not {'fund_id', 'currency', 'observation_date', 'price_kind'} <= set(values)):
        return spec
    from .reasoning import extractable_source_views
    available = {view['id'] for view in extractable_source_views(task)}
    bundle = task.get('reasoning_state', {}).get('reading', {}).get('bundle')
    if bundle is not None:
        from .source_reader import validate_reading
        validate_reading(task, bundle)
        available.intersection_update(view['id'] for view in bundle['views'])
    choices = share_context_choices(task, artifact, spec.get('record_locator', {}), spec.get('object'),
        allowed_ids=available)
    if len(choices) != 1:
        return spec
    choice = choices[0]
    # This registry supplies only share_class. Currency, dates and financial
    # measurements must already belong to the price's own original.
    result = copy.deepcopy(spec)
    result['calculation_context']['share_class'] = choice['value']
    result['calculation_context_basis']['share_class'] = copy.deepcopy(choice['basis'])
    record = next(row for row in task['source_records'] if row['id'] == choice['basis']['artifact_id'])
    validate_share_context(task, dict(result, artifact=artifact), record, choice['basis'])
    result['calculation_context_joins'] = [dict(version=1, field='share_class',
        registry='same_code_same_valuation_date_etf_share', context_ref=choice['context_ref'],
        basis=copy.deepcopy(choice['basis']), financial_approval=False)]
    return result


def has_api_share_context(task, views):
    if not share_context_enabled(task) or not views or any(view.get('kind') != 'api' for view in views):
        return False
    from .evidence import api_cell_candidates
    ids = {view['id'] for view in views}
    for record in task.get('source_records', []):
        if (record.get('id') not in ids or record.get('source') != 'ifind'
                or record.get('operation') != 'cmd_history_quotation'):
            continue
        for cell in api_cell_candidates(record, 64, task=task)['cells']:
            if share_context_choices(task, record, cell['proposal_fields']['record_locator'],
                    cell['source_metadata']['provider_code'], allowed_ids=ids):
                return True
    return False


def download(code, timeout):
    """One pinned public GET, no authentication, redirects or fallback host."""
    from .evidence import _PublicFilingConnection
    url = request_url(code)
    parsed = urlsplit(url)
    connection = _PublicFilingConnection(parsed.hostname, timeout=timeout, use_local_proxy=True)
    deadline = time.monotonic() + timeout
    try:
        connection.request('GET', parsed.path+'?'+parsed.query, headers={
            'Accept': 'application/json', 'Accept-Encoding': 'identity',
            'Referer': 'https://www.sse.com.cn/disclosure/fund/etflist/detail.shtml?fundid='+code[:6]})
        response = connection.getresponse()
        if response.status != 200:
            raise urllib.error.HTTPError(url, response.status, '上交所未返回成功清单', response.headers, None)
        declared = response.getheader('Content-Length')
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_BYTES):
            raise ContractError('ETF清单原文长度异常')
        chunks, size = [], 0
        while True:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise ContractError('ETF清单读取超过本次时限')
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            part = response.read1(min(65536, MAX_BYTES+1-size))
            if not part:
                break
            size += len(part)
            if size > MAX_BYTES:
                raise ContractError('ETF清单原文超出2MB')
            chunks.append(part)
        if declared is not None and size != int(declared):
            raise ContractError('ETF清单原文未完整接收')
        if time.monotonic() >= deadline:
            raise ContractError('ETF清单读取超过本次时限')
        return b''.join(chunks)
    finally:
        connection.close()
