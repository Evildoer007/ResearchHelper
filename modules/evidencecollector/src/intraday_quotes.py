"""Bounded iFinD quotes. Callers authorize intent, market identity and cutoff.

Official HTTP examples and output fields:
https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/manual.html
https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/example.html
"""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
from zoneinfo import ZoneInfo

from .contracts import ContractError, DOCUMENT_CONTENT_INTENT, fingerprint, timepoint

REALTIME = 'real_time_quotation'
MINUTE = 'high_frequency'
POLICY = 'ifind_intraday_quotes_version'
LABELS = {'latest': '最新价', 'minute_close': '分钟收盘价'}
REGISTRY = {
    f'ifind.{REALTIME}': dict(version=1, kind='实时行情', raw_only=True, fields={'latest': {}}),
    f'ifind.{MINUTE}': dict(version=1, kind='分钟行情', raw_only=True, fields={'close': {}}),
}
_CODE = re.compile(r'(?:[0-9]{6}\.(?:SH|SZ)|[0-9]{5}\.HK)')
_STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?'
                    r'(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])?')
_WINDOW_STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}')
_PRICE = re.compile(r'-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')
_LIVE_READING = re.compile(
    r'实时(?:行情|报价|价格|股价|成交价|最新价)|盘中(?:最新价|现价|报价)'
    r'|(?:当[前下]|现在)(?:的)?(?:最新价|股价|成交价|报价|现价)|最新报价|最新价|现价')
_MINUTE_READING = re.compile(r'(?:1|一)?分钟(?:线|行情|收盘价)|分时(?:行情|收盘价)')
_OTHER_FREQUENCY = re.compile(r'(?<!\d)(?:[2-9][0-9]*|1[0-9]+|[二两三四五六七八九十百]+)\s*分钟')
_CLOCK = r'(?:[01]?[0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9])?'
_DATE_MENTION = re.compile(r'(?<!\d)(?:\d{4}-\d{2}-\d{2}|(?:\d{4}年)?\d{1,2}月\d{1,2}[日号])')
_LOCAL_WINDOW = re.compile(r'(?<!\d)(?P<day>\d{4}-\d{2}-\d{2}|\d{4}年\d{1,2}月\d{1,2}日)'
                           rf'\s*(?:从|的)?\s*(?P<start>{_CLOCK})\s*(?:至|到|[-—–~～])'
                           rf'\s*(?P<end>{_CLOCK})(?![:\d])')
_NEGATION = re.compile(r'(?:不(?:要|用|必|查|取|看|需要)|无需|别|排除)\s*(?:查(?:询)?|获取|取|看)?'
                       r'(?:[^。！？；\n，,]{0,56})?'
                       rf'(?:{_LIVE_READING.pattern}|{_MINUTE_READING.pattern})')
_ANALYSIS = re.compile(r'为什么|如何|怎么|分析|解释|预测|判断|原因|影响|策略|回测|归因|买入|卖出|还是|比较|对比')
_OTHER_DATA = re.compile(r'开盘价|最高价|最低价|成交量|成交额|涨幅|涨跌幅|日线|财报|净值|估值|市值|行业|公告')
_DAILY_OR_HISTORICAL = re.compile(r'收盘(?:价|点位)|前日|前天|昨天|昨日|截至|历史时点|(?<!\d)\d{4}[-年]\d{1,2}')



def _code(code):
    if not isinstance(code, str) or not _CODE.fullmatch(code):
        raise ContractError('盘中行情须提供一只沪深或香港上市证券的完整代码')
    return code


def market_timezone(code):
    return 'Asia/Hong_Kong' if _code(code).endswith('.HK') else 'Asia/Shanghai'


def realtime_parameters(code):
    return dict(codes=_code(code), indicators='latest')


def _datetime(value, pattern):
    if isinstance(value, str) and pattern.fullmatch(value):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return None


def minute_parameters(code, starttime, endtime):
    start = _datetime(starttime, _WINDOW_STAMP)
    end = _datetime(endtime, _WINDOW_STAMP)
    if start is None or end is None or start.date() != end.date() or end < start:
        raise ContractError('分钟行情须明确同一天内先后有序的起止时间')
    return dict(codes=_code(code), indicators='close', starttime=starttime, endtime=endtime,
                functionpara=dict(Interval='1', Fill='Original', CPS='no', Timeformat='LocalTime'))


def validate_parameters(operation, parameters):
    """Return the canonical POST body, rejecting expansions and alternative defaults."""
    if not isinstance(parameters, dict):
        raise ContractError('盘中行情缺少请求参数')
    if operation == REALTIME:
        expected = realtime_parameters(parameters.get('codes'))
    elif operation == MINUTE:
        expected = minute_parameters(parameters.get('codes'), parameters.get('starttime'), parameters.get('endtime'))
    else:
        raise ContractError('未登记的盘中行情接口')
    if parameters != expected:
        raise ContractError('盘中行情请求的指标、频率或取数设置与当前能力不一致')
    return expected


def question_request(task, *, direct=False):
    """Resolve a single, explicit price request without model inference."""
    from .draft_sources import stated_access_restricted, stated_source_text
    from .evidence import available_security_codes, security_code_mentions
    from .subject_scope import scope_question

    if (task.get(POLICY) != 1 or task.get('research_mode') != 'current'
            or task.get('intent') == DOCUMENT_CONTENT_INTENT):
        return None
    text = scope_question(task)
    text = stated_source_text(task, text)
    if not (_MINUTE_READING.search(text) or _LIVE_READING.search(text)):
        return None
    if stated_access_restricted(task, text, scoped_market=True) or _NEGATION.search(text):
        return None
    if direct and (_ANALYSIS.search(text) or _OTHER_DATA.search(text)
                   or task.get('materials') or task.get('pending_materials')):
        return None
    codes = set(security_code_mentions(text, encoding=task.get('security_declaration_encoding', 1)))
    if len(codes) != 1:
        return None
    code, = codes
    if not _CODE.fullmatch(code) or code not in available_security_codes(task):
        return None
    required = [row for row in task['questions'] if row.get('required', True)]
    questions = [row['id'] for row in required if row['question'] == task['question']]
    if not questions or direct and (len(required) != 1 or required[0].get('answer_scope') == 'general_definition'):
        return None
    if _MINUTE_READING.search(text):
        if _OTHER_FREQUENCY.search(text):
            return None
        windows = list(_LOCAL_WINDOW.finditer(text))
        if len(windows) != 1 or _LIVE_READING.search(text):
            return None
        window = windows[0]
        remainder = text[:window.start()] + ' ' + text[window.end():]
        if _DATE_MENTION.search(remainder) or re.search(_CLOCK, remainder):
            return None
        day = window['day']
        try:
            if '年' in day:
                day = datetime.strptime(day, '%Y年%m月%d日').date().isoformat()
            times = []
            for name in ('start', 'end'):
                clock = window[name]
                if clock.count(':') == 1:
                    clock += ':00'
                times.append(f'{day} {clock.zfill(8)}')
            parameters = minute_parameters(code, *times)
            end = datetime.fromisoformat(parameters['endtime']).replace(tzinfo=ZoneInfo(market_timezone(code)))
            if end > timepoint(task['cutoff']):
                return None
        except (ContractError, ValueError):
            return None
        operation, reason = MINUTE, '查询问题指定的本地日期与分钟收盘区间。'
    elif _LIVE_READING.search(text) and not _DAILY_OR_HISTORICAL.search(text):
        parameters = realtime_parameters(code)
        operation, reason = REALTIME, '读取该证券带供应商时间戳的最新报价。'
    else:
        return None
    return dict(id='ifind_intraday_'+operation, source='ifind', operation=operation,
                parameters=parameters, question_ids=questions, selection_reason=reason)


def validate_request(task, operation, parameters):
    validate_parameters(operation, parameters)
    request = question_request(task)
    if request is None or request['operation'] != operation or request['parameters'] != parameters:
        raise ContractError('盘中报价接口须与本轮明确的证券和时间范围一致')


def retained_observations(task, record):
    """Admit observations only from the exact retained request and its acquisition time."""
    request = question_request(task)
    if (request is None or record.get('operation') != request['operation']
            or record.get('parameters') != request['parameters']
            or not set(request['question_ids']).intersection(record.get('question_ids', ()))):
        return []
    try:
        acquired = timepoint(record['obtained_at'])
        zone = ZoneInfo(market_timezone(request['parameters']['codes']))
        result = []
        for row in quote_observations(record):
            observed = datetime.fromisoformat(row['quote_time'])
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=zone)
            if observed <= acquired:
                # Preserve the supplier timestamp as well as its market-local display time.
                result.append(dict(row, local_time=observed.astimezone(zone).isoformat(timespec='seconds')))
        return result
    except (ContractError, KeyError, ValueError, OverflowError):
        return []


def quote_observations(record):
    """Read dated price cells from one intact business record, without inferring a quote time."""
    if not isinstance(record, dict) or record.get('source') != 'ifind':
        return []
    operation = record.get('operation')
    payload = record.get('snapshot')
    if not isinstance(payload, dict):
        return []
    status = payload.get('errorcode')
    if type(status) not in (int, str) or status not in (0, '0'):
        return []
    try:
        params = validate_parameters(operation, record.get('parameters'))
        request = dict(source='ifind', operation=operation, parameters=params)
        if record.get('request_hash') != fingerprint(request) or record.get('hash') != fingerprint(payload):
            return []
    except (ContractError, TypeError, ValueError):
        return []
    tables = payload.get('tables')
    if not isinstance(tables, list) or len(tables) != 1 or not isinstance(tables[0], dict):
        return []
    table = tables[0]
    field = params['indicators']
    if table.get('thscode') != params['codes'] or not isinstance(table.get('table'), dict):
        return []
    times, prices = table.get('time'), table['table'].get(field)
    if not isinstance(times, list) or not isinstance(prices, list) or len(times) != len(prices):
        return []
    if operation == REALTIME and len(times) != 1:
        return []
    if operation == MINUTE:
        start = datetime.fromisoformat(params['starttime'])
        end = datetime.fromisoformat(params['endtime'])
        if len(times) > (end - start) // timedelta(minutes=1) + 1:
            return []
    timezone = ZoneInfo(market_timezone(params['codes']))
    seen = set()
    observations = []
    for row, (stamp, price) in enumerate(zip(times, prices)):
        parsed = _datetime(stamp, _STAMP)
        if parsed is None:
            return []
        try:
            local = parsed.astimezone(timezone).replace(tzinfo=None) if parsed.tzinfo is not None else parsed
        except OverflowError:
            return []
        if local in seen or (operation == MINUTE and (local.second or local.microsecond or not start <= local <= end)):
            return []
        seen.add(local)
        if type(price) not in (int, float, str) or not _PRICE.fullmatch(str(price)):
            continue
        try:
            if Decimal(str(price)) <= 0:
                continue
        except InvalidOperation:
            continue
        observations.append(dict(code=params['codes'], field=field, value=price, quote_time=stamp,
                                 record_locator=dict(table=0, row=row, field=field)))
    return observations
