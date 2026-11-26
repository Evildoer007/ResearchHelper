"""Source and local-session alignment for cash index event windows."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import re
from urllib.parse import urlencode
from urllib.request import Request
from zoneinfo import ZoneInfo

from .contracts import ContractError, unique_json_object
from .https_transport import open_verified_https


POLICY = 'draft_hk_event_market_version'
READING_POLICY = 'draft_event_reading_version'
READING_VERSION = 2
_FIRST_SESSION = r'首个(?:交易日|交易窗口|收盘窗口)|第一个交易日|下一交易日'
_SPOKEN_FIRST_SESSION = r'(?:开市|复市|恢复交易|开始交易)(?:后的?)?第[一1](?:个)?(?:交易)?天|交易首日'


OPERATION = 'hsi_close_history'
PUBLISHER = '恒生指数公司'
MAINLAND_POLICY = 'draft_mainland_event_market_version'
MAINLAND_OPERATION = 'mainland_index_close_history'
INDEXES = {
    'HSTECH.HI': ('02083.00', 'Hang Seng TECH Index', '恒生科技指数'),
    'HSI.HI': ('00001.00', 'Hang Seng Index', '恒生指数'),
}
INDEX_CODE_PATTERN = r'(?:HSTECH|HSI)\.HI'
MAINLAND_INDEXES = {
    '399975.SZ': ('中证全指证券公司指数',
                  r'中证全指证券公司|证券公司指数|(?:A股|境内|内地)\s*券商|券商(?:板块|行业|指数)|399975(?:\.SZ)?'),
    '000300.SH': ('沪深300指数', r'沪深300(?!\s*(?:ETF|基金))|000300\.SH'),
    '000001.SH': ('上证综指', r'上证综指|上证综合指数|上证指数|沪指|000001\.SH'),
}
INDEX_NAMES = {**{code: entry[2] for code, entry in INDEXES.items()},
               **{code: entry[0] for code, entry in MAINLAND_INDEXES.items()}}
PERIOD_BASIS = {**dict.fromkeys(INDEXES, '香港当地交易日'), **dict.fromkeys(MAINLAND_INDEXES, '境内当地交易日')}
URL = ('https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy'
       '/v1/product-data/pub/indexes/metadata/v2')
MAX_RESPONSE_BYTES = 300_000
CHINA_TIME = ZoneInfo('Asia/Shanghai')
_NO_QUOTES = re.compile(r'(?:不|别|无需|不要)(?:查|看|取|获取|做|使用|比较|对照)?(?:行情|收盘(?:数据|表现)?|市场数据)|'
                        r'(?:仅|只)(?:解释|分析)(?:政策)?(?:机制|传导)')
_NO_BENCHMARK = re.compile(r'(?:不|别|无需|不要)(?:与|用|比较|对照|使用)*(?:大盘|市场基准)')


def event_reading_enabled(task):
    return type(task.get(READING_POLICY)) is int and task[READING_POLICY] in (1, READING_VERSION)


def verified_event_sessions_enabled(task):
    return task.get(READING_POLICY) == READING_VERSION


def asks_first_event_session(task):
    text = task.get('question', '')
    return bool(re.search(_FIRST_SESSION, text)
                or event_reading_enabled(task) and re.search(_SPOKEN_FIRST_SESSION, text))


def _market_question(task):
    from .draft_sources import _unquoted_local_scope
    from .subject_scope import scope_question

    question = _unquoted_local_scope(scope_question(task))
    return '' if _NO_QUOTES.search(question) else question


def _hong_kong_codes(task, question):
    from .evidence import declared_security_codes
    from .subject_scope import subject_allowed

    tech = bool(re.search(r'港股科技|恒生科技|HSTECH\.HI', question, re.I))
    broad = bool(re.search(r'恒生指数|恒指|HSI\.HI', question, re.I))
    if not (tech or broad or '港股' in question):
        return []
    if not (tech or broad) and any(code.endswith('.HK') for code in declared_security_codes(task)):
        return []
    no_benchmark = _NO_BENCHMARK.search(question) or re.search(
        r'(?:不|别|无需|不要)(?:与|用|比较|对照|使用)*(?:恒生指数|恒指)', question)
    selected = ['HSTECH.HI'] if tech else []
    if not tech or not no_benchmark:
        selected.append('HSI.HI')
    return [code for code in selected
            if subject_allowed(task, code) and subject_allowed(task, INDEX_NAMES[code])]


def _mainland_codes(task, question):
    from .evidence import declared_security_codes
    from .subject_scope import subject_allowed

    if task.get(MAINLAND_POLICY) != 1:
        return []
    if '港股' in question and not re.search(r'A股|沪深|境内|内地|上证|399975|中证', question, re.I):
        return []
    sector, baseline = '399975.SZ', '000300.SH'
    selected = [code for code, (_, alias) in MAINLAND_INDEXES.items() if re.search(alias, question, re.I)]
    if (not selected and re.search(r'A股(?:市场|大盘|整体)(?!.*(?:单一|个股))', question, re.I)
            and not any(code.endswith(('.SH', '.SZ')) for code in declared_security_codes(task))):
        selected.append(baseline)
    if (sector in selected and not set(selected).intersection({'000001.SH', baseline}) and not _NO_BENCHMARK.search(question)
            and re.search(r'大盘|市场基准|宽基|相对表现', question)):
        selected.append(baseline)
    return [code for code in selected if subject_allowed(task, code) and subject_allowed(task, INDEX_NAMES[code])]


def requested_markets(task):
    """Return the price-index scope, even when the event clock is still absent."""
    question = _market_question(task)
    selections = {'hong_kong': _hong_kong_codes(task, question) if task.get(POLICY) == 1 else [],
                  'mainland': _mainland_codes(task, question)}
    return {market: codes for market, codes in selections.items() if codes}


def policy_release_time(task):
    from .evidence import current_original_reference
    from .official_sources import (fomc_rate_dates, fomc_rate_statement, fomc_statement_release_clock,
                                   research_fomc_rate_request)
    from .source_catalogue import official_record_label

    if not (request := research_fomc_rate_request(task)):
        return None
    dates = fomc_rate_dates(task, request)
    if not dates:
        return None
    clocks = set()
    sources = task.get('source_records', ())
    for record in sources:
        snapshot = record.get('snapshot', {})
        origin = snapshot.get('official_origin', {})
        if (origin.get('path_date_hint') != dates[-1] or official_record_label(record, sources) != '美联储'
                or not current_original_reference(task, record)):
            continue
        parts = snapshot['parts']
        statement = fomc_rate_statement(record, parts)
        if statement and (clock := fomc_statement_release_clock(record, statement, parts)):
            clocks.add(clock['scheduled_at'])
    return datetime.fromisoformat(clocks.pop()).astimezone(CHINA_TIME) if len(clocks) == 1 else None


def market_scopes(task):
    """Anchor each exchange's window to the same statement and its own cash sessions."""
    markets = requested_markets(task)
    if not markets or (release := policy_release_time(task)) is None:
        return {}
    cutoff = datetime.fromisoformat(task['cutoff']).astimezone(CHINA_TIME)
    # Only overnight release times are aligned automatically. Date-only closes
    # cannot serve as both the pre- and post-release price for a daytime event.
    if release.time() >= time(9) or release >= cutoff:
        return {}
    day = release.date()
    cutoffs = {'hong_kong': time(16, 15), 'mainland': time(15, 10)}
    scopes = {}
    for market, codes in markets.items():
        latest = cutoff.date() - timedelta(days=cutoff.time() < cutoffs[market])
        end = min(latest, day + timedelta(days=4))
        if end >= day:
            scopes[market] = dict(codes=codes, session_day=day.isoformat(),
                start=(day - timedelta(days=4)).isoformat(), end=end.isoformat())
    return scopes


def market_scope(task):
    return market_scopes(task).get('hong_kong')


def _codes(value):
    if (not isinstance(value, list) or not value or len(value) > len(INDEXES)
            or any(not isinstance(code, str) or code not in INDEXES for code in value)
            or len(set(value)) != len(value)):
        raise ContractError('恒生指数公司请求的价格指数未登记或重复')
    return value


def close_history_url(codes):
    query = urlencode(dict(data='valueHistoryPub', language='en-hk',
                           indexCodes=','.join(INDEXES[code][0] for code in _codes(codes))))
    return URL+'?'+query


def request_parameters(scope):
    return {key: scope[key] for key in ('codes', 'start', 'end')}


def request_shape(task):
    scope = market_scope(task)
    return dict(qualified_id_for_lookup_only='official.'+OPERATION, source='official', operation=OPERATION,
                parameter_keys=['codes', 'start', 'end'], numeric_fields=['close'], credential_required=False,
                available_parameter_sets=[request_parameters(scope)] if scope else [],
                selection_rule='使用已核对政策声明后的事件窗口参数；没有可选参数时先核对声明。')


def validate_request(task, parameters):
    scope = market_scope(task)
    if not scope or parameters != request_parameters(scope):
        raise ContractError('恒生收盘历史须匹配当前声明时点与事件窗口')


def mainland_parameters(scope, code):
    return dict(ts_code=code, start=scope['start'], end=scope['end'])


def mainland_request_shape(task):
    scope = market_scopes(task).get('mainland')
    return dict(qualified_id_for_lookup_only='official.'+MAINLAND_OPERATION, source='official',
                operation=MAINLAND_OPERATION, parameter_keys=['ts_code', 'start', 'end'],
                numeric_fields=['close'], credential_required=False,
                available_parameter_sets=[mainland_parameters(scope, code) for code in scope['codes']] if scope else [],
                selection_rule='使用已核对声明的事件窗口和登记的境内价格指数。')


def validate_mainland_request(task, parameters):
    scope = market_scopes(task).get('mainland')
    if not scope or parameters not in [mainland_parameters(scope, code) for code in scope['codes']]:
        raise ContractError('境内指数收盘历史须匹配当前声明时点、事件窗口和研究指数')


def mainland_endpoint(code):
    if not isinstance(code, str) or code not in MAINLAND_INDEXES:
        raise ContractError('境内价格指数未登记')
    number, exchange = code.split('.')
    if exchange == 'SH':
        url = 'https://yunhq.sse.com.cn:32042/v1/sh1/dayk/'+number+'?'+urlencode(
            dict(select='date,close', begin=-200, end=-1))
        return url, 'https://www.sse.com.cn/market/price/trends/', '上海证券交易所'
    url = 'https://www.szse.cn/api/market/ssjjhq/getHistoryData?'+urlencode(
        dict(marketId=1, code=number, cycleType=32))
    return url, 'https://www.szse.cn/market/trend/index.html?code='+number, '深圳证券交易所'


def parse_mainland_history(raw, ts_code, start, end):
    """Keep exchange row positions and never mix index closes with NAV or returns."""
    mainland_endpoint(ts_code)
    try:
        payload = json.loads(raw, object_pairs_hook=unique_json_object, parse_float=Decimal)
    except (ValueError, UnicodeError) as error:
        raise ContractError('境内指数历史响应无法解析') from error
    number, exchange = ts_code.split('.')
    if not isinstance(payload, dict):
        raise ContractError('境内指数历史响应结构不符')
    if exchange == 'SH':
        reported, rows, width, price_column = payload.get('code'), payload.get('kline'), 2, 1
    else:
        data = payload.get('data')
        if payload.get('code') != '0' or not isinstance(data, dict):
            raise ContractError('深交所指数历史未返回成功结果')
        reported, rows, width, price_column = data.get('code'), data.get('picupdata'), 9, 2
    if reported != number or not isinstance(rows, list) or not 1 <= len(rows) <= 300:
        raise ContractError('指数代码或日收盘序列不符')
    previous, result = '', []
    for offset, fields in enumerate(rows):
        if not isinstance(fields, list) or len(fields) != width:
            raise ContractError('境内指数日行情的列数不符')
        token, close = str(fields[0]), str(fields[price_column])
        if re.fullmatch(r'[0-9]{8}', token):
            token = token[:4]+'-'+token[4:6]+'-'+token[6:]
        try:
            dated = date.fromisoformat(token).isoformat() == token
        except ValueError:
            dated = False
        if not dated or token <= previous or not re.fullmatch(r'\d{1,9}(?:\.\d{1,8})?', close) or Decimal(close) <= 0:
            raise ContractError('境内指数历史含日期、顺序或点位异常')
        previous = token
        if start <= token <= end:
            result.append(dict(code=ts_code, date=token, close=close, table=0, row=offset))
    return result


def validated_mainland_history(snapshot, parameters):
    url, _, _ = mainland_endpoint(parameters['ts_code'])
    raw = snapshot['original_text'].encode('utf-8')
    if (not raw or len(raw) > MAX_RESPONSE_BYTES or snapshot.get('url') != url
            or sha256(raw).hexdigest() != snapshot.get('original_sha256')):
        raise ContractError('境内指数历史的地址或原始字节已改变')
    rows = parse_mainland_history(raw, **parameters)
    if rows != snapshot.get('rows'):
        raise ContractError('境内指数历史的留存读数与原始响应不符')
    return rows


def download_mainland_snapshot(parameters, timeout):
    url, referer, _ = mainland_endpoint(parameters['ts_code'])
    request = Request(url, headers={'Accept': 'application/json', 'Accept-Encoding': 'identity', 'Referer': referer})
    with open_verified_https(request, timeout=timeout, follow_redirects=False) as response:
        if response.headers.get_content_type() not in ('application/json', 'text/plain'):
            raise ContractError('境内指数日行情响应类型不符')
        raw = response.read(MAX_RESPONSE_BYTES+1)
    if not raw or len(raw) > MAX_RESPONSE_BYTES:
        raise ContractError('境内指数日行情响应超出登记长度')
    text = raw.decode('utf-8')
    return dict(url=url, original_text=text, original_sha256=sha256(raw).hexdigest(),
                rows=parse_mainland_history(text, **parameters))


def ifind_parameters(scope):
    from .evidence import IFIND_QUOTE_FUNCTION

    return dict(codes=','.join(scope['codes']), indicators='close', startdate=scope['start'],
                enddate=scope['end'], functionpara=dict(IFIND_QUOTE_FUNCTION))


def parse_close_history(raw, codes, start, end):
    """Read exact prices, retaining row offsets in the official JSON."""
    try:
        payload = json.loads(raw, object_pairs_hook=unique_json_object)
    except (ValueError, UnicodeError) as error:
        raise ContractError('恒生历史行情响应无法解析') from error
    expected = {INDEXES[code][0]: code for code in _codes(codes)}
    if (not isinstance(payload, dict) or type(payload.get('code')) not in (str, int)
            or str(payload['code']) != '0' or not isinstance(payload.get('data'), list)):
        raise ContractError('恒生历史行情未返回有效业务结果')
    series, seen = [], set()
    for table, item in enumerate(payload['data']):
        code = expected.get(item.get('indexCode')) if isinstance(item, dict) else None
        if code in seen or not code or item.get('indexName') != INDEXES[code][1]:
            raise ContractError('恒生历史行情指数身份不匹配')
        seen.add(code)
        history = item.get('valueHistory')
        if not isinstance(history, list) or not history:
            raise ContractError('恒生历史行情缺少实际收盘序列')
        last = ''
        for offset, row in enumerate(history):
            day = row.get('date') if isinstance(row, dict) else None
            value = row.get('close') if isinstance(row, dict) else None
            if (not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day)
                    or day <= last or not isinstance(value, str) or not re.fullmatch(r'\d{1,9}(?:\.\d{1,8})?', value)
                    or Decimal(value) <= 0):
                raise ContractError('恒生历史行情日期、点位或顺序不符')
            try:
                date.fromisoformat(day)
            except ValueError as error:
                raise ContractError('恒生历史行情交易日期无效') from error
            last = day
            if start <= day <= end:
                series.append(dict(code=code, date=day, close=value, table=table, row=offset))
    return series


def download_snapshot(codes, timeout):
    url = close_history_url(codes)
    request = Request(url, headers={'Accept': 'application/json', 'Accept-Encoding': 'identity'})
    with open_verified_https(request, timeout=timeout, follow_redirects=False) as response:
        if response.headers.get_content_type() != 'application/json':
            raise ContractError('恒生历史行情响应类型不符')
        raw = response.read(MAX_RESPONSE_BYTES+1)
    if not raw or len(raw) > MAX_RESPONSE_BYTES:
        raise ContractError('恒生历史行情响应超出登记长度')
    return dict(url=url, original_text=raw.decode('utf-8'), original_sha256=sha256(raw).hexdigest())


def retained_rows(task, record, scope):
    from .evidence import current_query_reference

    if not scope or not current_query_reference(task, record):
        return []
    try:
        parameters, snapshot = record['parameters'], record['snapshot']
        if record['source'] == 'ifind':
            if parameters != ifind_parameters(scope) or str(snapshot.get('errorcode')) != '0':
                return []
            result = []
            for index, table in enumerate(snapshot.get('tables', [])):
                code = table.get('thscode')
                days, prices = table.get('time'), table.get('table', {}).get('close')
                if (code not in scope['codes'] or not isinstance(days, list) or not isinstance(prices, list)
                        or len(days) != len(prices)):
                    continue
                for offset, (day, price) in enumerate(zip(days, prices, strict=True)):
                    if (isinstance(day, str) and scope['start'] <= day <= scope['end']
                            and date.fromisoformat(day).isoformat() == day and type(price) in (int, float, str)
                            and re.fullmatch(r'\d{1,9}(?:\.\d{1,8})?', str(price)) and Decimal(str(price)) > 0):
                        result.append(dict(code=code, date=day, close=str(price), table=index, row=offset))
            return result
        if record.get('operation') == MAINLAND_OPERATION:
            if parameters not in [mainland_parameters(scope, code) for code in scope['codes'] if code in MAINLAND_INDEXES]:
                return []
            return validated_mainland_history(snapshot, parameters)
        if parameters != request_parameters(scope) or record.get('operation') != OPERATION:
            return []
        raw = snapshot['original_text'].encode('utf-8')
        if (len(raw) > MAX_RESPONSE_BYTES or sha256(raw).hexdigest() != snapshot['original_sha256']
                or snapshot['url'] != close_history_url(parameters['codes'])):
            return []
        return parse_close_history(raw, **parameters)
    except (ContractError, KeyError, AttributeError, TypeError, ValueError):
        return []


def _event_endpoints(scope, code, calendars):
    """Prove session adjacency with a calendar or two adjacent dated closes."""
    from .market_calendar import MARKETS, verified_session_window

    anchor = date.fromisoformat(scope['session_day'])
    start, end = (date.fromisoformat(scope[key]) for key in ('start', 'end'))
    market = MARKETS.get(code.rsplit('.', 1)[-1])
    windows = [(record, response) for record, response in calendars if record['parameters']['marketcode'] == market]
    if any(record['parameters']['startdate'] <= scope['start'] and record['parameters']['enddate'] >= scope['end']
           for record, _ in windows):
        days = verified_session_window(code, start, end, windows)
        if days is None:
            return None
        before = next((day for day in reversed(days) if day < anchor), None)
        after = next((day for day in days if day >= anchor), None)
        return (before.isoformat(), after.isoformat()) if before and after else None
    proposed = ((anchor - timedelta(days=1)).isoformat(), anchor.isoformat())
    if any(record['parameters']['startdate'] <= day <= record['parameters']['enddate'] and day not in response['dates']
           for record, response in windows for day in proposed):
        return None
    return proposed


def matched_sessions(scope, rows, *, task=None):
    """Keep matching observed sessions, without estimating missing prices."""
    if not scope:
        return {}
    check = task is not None and verified_event_sessions_enabled(task)
    expected = {}
    if check:
        from .market_calendar import retained_calendars

        calendars = retained_calendars(task)
        expected = {code: _event_endpoints(scope, code, calendars) for code in scope['codes']}
    sources = {}
    for row in rows:
        if (row.get('code') not in scope['codes'] or row.get('field') != 'close' or row.get('unit') != '点'
                or row.get('source') not in ('ifind', 'official') or not scope['start'] <= row['period'] <= scope['end']
                or row.get('value') is None or row['value'] <= 0):
            continue
        sessions = sources.setdefault((row['code'], row['source'], row['artifact_id']), {})
        existing = sessions.setdefault(row['period'], row)
        if existing and existing['value'] != row['value']:
            sessions[row['period']] = None
    options = {}
    for (code, source, _), days in sources.items():
        if check:
            endpoints = expected[code]
            if not endpoints:
                continue
            before, after = endpoints
        else:
            if not all(days.values()):
                continue
            before = max((day for day in days if day < scope['session_day']), default='')
            after = min((day for day in days if day >= scope['session_day']), default='')
        if days.get(before) and days.get(after):
            options.setdefault((code, source), []).append((before, after, days))
    def unique_pair(code, source):
        variants = options.get((code, source), [])
        if len({(a, b, days[a]['raw_value'], days[b]['raw_value']) for a, b, days in variants}) == 1:
            return variants[0]
        return None

    if all(code in MAINLAND_INDEXES for code in scope['codes']):
        # Different target indexes may have different exchange publishers.
        # A comparison still requires matching local dates in the readout.
        result = {}
        for code in scope['codes']:
            pairs = [pair for source in ('ifind', 'official') if (pair := unique_pair(code, source))]
            if pairs:
                # Recover a nearer session if one publisher missed a row.
                result[code] = max(pairs, key=lambda pair: (-date.fromisoformat(pair[1]).toordinal(), pair[0]))
        return result
    chosen = {}
    for source in ('ifind', 'official'):
        matches = {}
        for code in scope['codes']:
            if pair := unique_pair(code, source):
                matches[code] = pair
        if len(matches) == len(scope['codes']) and len({(a, b) for a, b, _ in matches.values()}) == 1:
            return matches
        if len(matches) > len(chosen):
            chosen = matches
    # In a partially covered request, keep the target readout. A benchmark
    # with different endpoints can remain in the evidence without a spread.
    return chosen
