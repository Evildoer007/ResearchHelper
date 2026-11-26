"""Public exchange ETF turnover, unadjusted closes, and published NAV."""
from base64 import b64decode, b64encode
from collections import defaultdict
from datetime import datetime, time as clock, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
import time
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from .contracts import ContractError, market_date, unique_json_object

POLICY = 'official_etf_trading_version'
DAILY = 'etf_daily_quotes'
NAV_HISTORY = 'etf_nav_history'
REGISTRY = {
    'official.'+DAILY: dict(version=1, kind='ETF日成交与收盘价', fields={}, raw_only=False),
    'official.'+NAV_HISTORY: dict(version=1, kind='深交所ETF单位净值', fields={}, raw_only=False),
}
TURNOVER = 'etf_exchange_turnover'
CLOSE = 'etf_exchange_close'
NAV = 'etf_exchange_nav'
CELL_KIND = 'exchange_etf_trading_cell'
FIELDS = {TURNOVER: ('成交额', '元'), CLOSE: ('未复权收盘价', '元/份'), NAV: ('单位净值', '元/份')}
AVERAGE = 'etf_daily_turnover_mean_v1'
_CODE = re.compile(r'(?:5[0-9]{5}\.SH|15[0-9]{4}\.SZ)')
TURNOVER_WORDS = re.compile(r'日均成交额|成交金额|成交额|成交活跃度|交投活跃度|流动性')
PREMIUM_WORDS = re.compile(r'收盘折溢价率?|折溢价率?|折价率|溢价率')
EXECUTION_WORDS = re.compile(r'大额(?:成交|交易|买单|订单|执行)?|执行(?:能力|条件|成本|风险)|'
                             r'盘口|买卖价差|滑点|冲击成本|报价深度|可成交')
_WINDOW = re.compile(r'(?:近|过去|最近|前)\s*(?P<n>[0-9]{1,3}|十|二十|三十)\s*个?\s*(?:共同|可比)?交易日')
_CALENDAR_WINDOW = re.compile(r'上半年|下半年|上季度|下季度|年初|今年以来|年内|\d{4}年|'
                              r'(?:近|过去|最近|前)[0-9一二三四五六七八九十半]+个?(?:周|个月|月|季|日|天|年)')
_TICKET = re.compile(r'(?<![0-9.])([0-9]+(?:\.[0-9]{1,4})?)\s*(亿元|万元|元|亿|万)(?=\s*(?:的)?(?:买单|卖单|委托|订单|买入|卖出|交易|下单))')
_LIMIT = 1024*1024
_TZ = ZoneInfo('Asia/Shanghai')


def trading_terms(task, question):
    if task.get(POLICY) != 1:
        return None
    turnover, premium, execution = (bool(pattern.search(question))
                                    for pattern in (TURNOVER_WORDS, PREMIUM_WORDS, EXECUTION_WORDS))
    if not (turnover or premium or execution):
        return None
    counts = {int(text) if text.isdigit() else {'十': 10, '二十': 20, '三十': 30}[text]
              for text in _WINDOW.findall(question)}
    # Multiple explicitly requested lookback lengths need separate measurements.
    if len(counts) > 1 or any(not 1 <= count <= 200 for count in counts):
        return None
    amount = set()
    if execution:
        for number, unit in _TICKET.findall(question):
            value = Decimal(number) * (100000000 if unit.startswith('亿') else 10000 if unit.startswith('万') else 1)
            if 0 < value <= 1000000000000:
                amount.add(format(value.normalize(), 'f'))
    exact_dates = re.findall(r'\d{4}-\d{2}-\d{2}', question)
    explicit_window = ((turnover or execution) and not counts
                       and (_CALENDAR_WINDOW.search(question) or len(exact_dates) >= 2))
    return dict(turnover=turnover or execution, premium=premium or execution, execution=execution,
                sessions=next(iter(counts), 10), ticket=next(iter(amount)) if len(amount) == 1 else None,
                unresolved_window=bool(explicit_window))


def eligible_close_day(cutoff):
    local = datetime.fromisoformat(cutoff).astimezone(_TZ)
    return (local.date() - timedelta(days=local.time() < clock(15))).isoformat()


def endpoint(operation, code):
    if not isinstance(code, str) or _CODE.fullmatch(code) is None:
        raise ContractError('ETF行情须指定沪深上市基金完整代码')
    number, market = code.split('.')
    sz = 'https://www.szse.cn/market/trend/index.html?code='+number
    if operation == DAILY and market == 'SH':
        url = f'https://yunhq.sse.com.cn:32042/v1/sh1/dayk/{number}?'+urlencode(
            {'select': 'date,close,amount', 'begin': '-100', 'end': '-1'})
        return url, 'https://www.sse.com.cn/market/price/trends/', 'application/json'
    if operation == DAILY and market == 'SZ':
        url = 'https://www.szse.cn/api/market/ssjjhq/getHistoryData?'+urlencode(
            dict(marketId=1, code=number, cycleType=32))
        return url, sz, 'application/json'
    if operation == NAV_HISTORY and market == 'SZ':
        url = 'https://www.szse.cn/api/report/ShowReport?'+urlencode(
            dict(SHOWTYPE='xls', CATALOGID='1785_child', TABKEY='tab1', txtDm=number))
        return url, 'https://www.szse.cn/market/product/list/etfList/index.html', 'application/vnd.ms-excel'
    raise ContractError('该市场的ETF交易资料接口未登记')


def validate_request(task, operation, parameters):
    from .draft_sources import local_sources_only, validate_stated_source
    from .etf_comparison import comparison_scope

    if not isinstance(parameters, dict) or set(parameters) != {'ts_code'} or local_sources_only(task):
        raise ContractError('ETF行情请求须指定当前问题中的一只基金')
    scope = comparison_scope(task)
    code = parameters['ts_code']
    if (not scope or not scope.get('trading') or code not in scope['codes']
            or operation == NAV_HISTORY and not scope['trading']['premium']):
        raise ContractError('本轮未授权该ETF交易资料请求')
    validate_stated_source(task, 'official')
    return endpoint(operation, code)


def trading_requests(task, scope):
    if not scope.get('trading'):
        return []
    from .draft_sources import local_sources_only, validate_stated_source

    if local_sources_only(task):
        return []
    try:
        validate_stated_source(task, 'official')
    except ContractError:
        return []
    requests = []
    question_id = scope.get('question_id', task['questions'][0]['id'])
    for code in scope['codes']:
        operations = [DAILY]
        if scope['trading']['premium']:
            operations.append('etf_pcf' if code.endswith('.SH') else NAV_HISTORY)
        for operation in operations:
            if not any(row.get('source') == 'official' and row.get('operation') == operation
                       and row.get('parameters') == {'ts_code': code} for row in task.get('source_records', ())):
                requests.append(dict(id=operation+'_'+code.replace('.', '_'), source='official',
                     operation=operation, parameters={'ts_code': code}, question_ids=[question_id],
                     selection_reason='核对同日的交易所成交、收盘价和适用单位净值。'))
    return requests


def _day(value):
    text = str(value)
    if re.fullmatch(r'[0-9]{8}', text):
        text = text[:4]+'-'+text[4:6]+'-'+text[6:]
    try:
        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', text) or market_date(text) != text:
            raise ValueError
    except (ContractError, ValueError) as error:
        raise ContractError('ETF日度资料含无效日期') from error
    return text


def _positive(raw, *, allow_zero=False):
    try:
        value = Decimal(str(raw).replace(',', ''))
        if not value.is_finite() or not 0 <= value <= Decimal('1e14') or not value and not allow_zero:
            raise ValueError
        return format(value, 'f')
    except (InvalidOperation, ValueError) as error:
        raise ContractError('ETF日度资料数值或单位不符') from error


def _daily_records(code, raw):
    data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_json_object, parse_float=Decimal)
    if not isinstance(data, dict):
        raise ContractError('ETF日行情没有登记的对象结构')
    if code.endswith('.SH'):
        if data.get('code') != code[:6] or not isinstance(data.get('kline'), list):
            raise ContractError('上交所日行情代码或时间序列不符')
        rows = data['kline']
        if any(not isinstance(row, list) or len(row) != 3 for row in rows):
            raise ContractError('上交所日行情未返回请求的三列')
        return [(day, close, amount) for day, close, amount in rows]
    table = data.get('data')
    if (data.get('code') != '0' or not isinstance(table, dict) or table.get('code') != code[:6]
            or not isinstance(table.get('picupdata'), list)):
        raise ContractError('深交所日行情代码或时间序列不符')
    rows = table['picupdata']
    if any(not isinstance(row, list) or len(row) != 9 for row in rows):
        raise ContractError('深交所日行情未返回登记的九列')
    return [(row[0], row[2], row[8]) for row in rows]


def _nav_records(code, raw):
    from xlrd import XLRDError, open_workbook

    try:
        workbook = open_workbook(file_contents=raw, on_demand=True)
        try:
            sheet = workbook.sheet_by_index(0)
            if (not 1 <= sheet.nrows <= 100 or sheet.ncols != 4
                    or sheet.row_values(0) != ['净值日期', '基金代码', '基金简称', '单位基金净值(元)']):
                raise ContractError('深交所单位净值表头或行数不符')
            rows = [sheet.row_values(index) for index in range(1, sheet.nrows)]
            if any(row[1] != code[:6] for row in rows):
                raise ContractError('深交所单位净值基金代码不符')
            return [(day, value) for day, _, _, value in rows]
        finally:
            workbook.release_resources()
    except (XLRDError, OSError, KeyError, IndexError) as error:
        raise ContractError('深交所单位净值原表无法读取') from error


def parse_snapshot(operation, code, raw):
    url, documentation, _ = endpoint(operation, code)
    if operation == DAILY:
        records = _daily_records(code, raw)
        entries = [dict(day=_day(day), close=_positive(close), turnover=_positive(amount, allow_zero=True), row=index)
                   for index, (day, close, amount) in enumerate(records)]
    else:
        entries = [dict(day=_day(day), nav=_positive(nav), row=index)
                   for index, (day, nav) in enumerate(_nav_records(code, raw), start=1)]
    if not entries or len(entries) > 300 or len({row['day'] for row in entries}) != len(entries):
        raise ContractError('ETF日度原表为空、超限或日期重复')
    return dict(code=code, operation=operation, url=url, documentation=documentation,
                response_sha256=sha256(raw).hexdigest(), original_base64=b64encode(raw).decode('ascii'), rows=entries)


def _validated_rows(record, operation, code):
    snapshot = record['snapshot']
    try:
        raw = b64decode(snapshot['original_base64'], validate=True)
        if len(raw) > _LIMIT or snapshot != parse_snapshot(operation, code, raw):
            return ()
    except (ContractError, KeyError, TypeError, ValueError):
        return ()
    return snapshot['rows']


def download(operation, code, timeout):
    from .evidence import _PublicFilingConnection

    url, referer, mime = endpoint(operation, code)
    parsed = urlsplit(url)
    deadline = time.monotonic()+timeout
    conn = _PublicFilingConnection(parsed.hostname, port=parsed.port or 443, timeout=timeout, use_local_proxy=True)
    try:
        conn.request('GET', parsed.path+'?'+parsed.query, headers={
            'Accept': mime, 'Referer': referer, 'Accept-Encoding': 'identity'})
        response = conn.getresponse()
        received_type = response.getheader('Content-Type', '').partition(';')[0].strip().lower()
        if response.status != 200 or received_type not in (mime, 'text/plain' if mime == 'application/json' else mime):
            raise ContractError('交易所ETF行情或净值未返回登记格式')
        length = response.getheader('Content-Length')
        if length and (not length.isdigit() or int(length) > _LIMIT):
            raise ContractError('交易所ETF交易资料响应长度异常')
        raw = bytearray()
        while time.monotonic() < deadline:
            if conn.sock is not None:
                conn.sock.settimeout(max(0.001, deadline-time.monotonic()))
            piece = response.read1(min(65536, _LIMIT+1-len(raw)))
            if not piece:
                if length and len(raw) != int(length):
                    raise ContractError('交易所ETF交易资料接收不完整')
                return parse_snapshot(operation, code, bytes(raw))
            raw.extend(piece)
            if len(raw) > _LIMIT:
                raise ContractError('交易所ETF交易资料超限')
        raise ContractError('交易所ETF交易资料读取超时')
    finally:
        conn.close()


def source_cells(task, scope):
    from .evidence import current_query_reference, current_snapshot_reference
    from .etf_sources import validate_snapshot

    quotes, navs = defaultdict(dict), defaultdict(dict)
    closing = eligible_close_day(task['cutoff'])
    for record in task['source_records']:
        if record.get('source') != 'official':
            continue
        operation = record.get('operation')
        code = record.get('parameters', {}).get('ts_code')
        if code not in scope['codes']:
            continue
        if operation in (DAILY, NAV_HISTORY) and current_query_reference(task, record):
            for row in _validated_rows(record, operation, code):
                if row['day'] <= closing:
                    (quotes if operation == DAILY else navs)[code][row['day']] = (record, row)
        elif operation == 'etf_pcf' and current_snapshot_reference(task, record):
            try:
                snapshot = validate_snapshot(record, task['cutoff'])
                row, = snapshot['data']
            except (ContractError, ValueError):
                continue
            if row['trade_date'] <= closing:
                navs[code][row['trade_date']] = (record, dict(day=row['trade_date'], row=0, nav=row['unit_nav']))
    codes = scope['codes']
    terms = scope['trading']
    common_days = sorted(set.intersection(*(set(quotes[code]) for code in codes))) if codes else []
    turnover_days = common_days[-terms['sessions']:] or None
    if terms['premium']:
        valuation_days = [day for day in common_days if all(day in navs[code] for code in codes)]
        premium_day = valuation_days[-1] if valuation_days else None
    else:
        premium_day = None
    names = {row['code']: row['name'] for row in scope.get('resolved', ())}
    cells = []
    for code in codes:
        selected = set(turnover_days or sorted(quotes[code])[-terms['sessions']:]) if terms['turnover'] else set()
        for day in sorted(selected):
            record, row = quotes[code][day]
            cells.append((record, code, names.get(code, code), TURNOVER, day, row['turnover'], row['row']))
        if premium_day:
            quote, row = quotes[code][premium_day]
            nav, value = navs[code][premium_day]
            cells.extend(((quote, code, names.get(code, code), CLOSE, premium_day, row['close'], row['row']),
                          (nav, code, names.get(code, code), NAV, premium_day, value['nav'], value['row'])))
    return cells
