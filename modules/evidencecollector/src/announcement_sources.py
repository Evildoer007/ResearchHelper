"""Independent official announcement directories and their selected originals.

Issuer keys come from the publisher, never from a generated stock-code rule.
Directory rows are discovery results, not the body or verified publication time.
The collector owns task scope, caching, cancellation and each HTTP reservation.
"""
import base64
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
from html import unescape
import json
from math import ceil
import re
import time
import urllib.error
from urllib.parse import urlencode, urlsplit

from .contracts import ContractError, DuplicateJSONField, fingerprint, market_date, timepoint, unique_json_object

_CNINFO = dict(lookup='https://www.cninfo.com.cn/new/information/topSearch/query',
               query='https://www.cninfo.com.cn/new/hisAnnouncement/query',
               original_host='static.cninfo.com.cn', referer='https://www.cninfo.com.cn/')
_SSE_QUERY = 'https://query.sse.com.cn/commonQuery.do'
_SSE_FUND_LOOKUP = 'COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C'
_SSE_FUND_FILINGS = 'COMMON_PL_JJXX_JJGG_L'
CHANNELS = {
    'cninfo': dict(_CNINFO, name='巨潮资讯', market='沪深A股', code_pattern=r'\d{6}\.(SH|SZ)'),
    'cninfo_fund': dict(_CNINFO, name='巨潮资讯基金公告', market='深市ETF', code_pattern=r'15\d{4}\.SZ'),
    'sse_fund': dict(name='上交所基金公告', market='沪市ETF', code_pattern=r'5\d{5}\.SH',
                     lookup=_SSE_QUERY, query=_SSE_QUERY, original_host='www.sse.com.cn',
                     referer='https://www.sse.com.cn/'),
    'hkex': dict(name='披露易', market='香港当前上市证券', lookup='https://www1.hkexnews.hk/ncms/script/eds/activestock_sehk_c.json',
                 query='https://www1.hkexnews.hk/search/titleSearchServlet.do', original_host='www1.hkexnews.hk',
                 referer='https://www1.hkexnews.hk/', code_pattern=r'\d{4,5}\.HK'),
}
CNINFO_CHANNELS = frozenset(('cninfo', 'cninfo_fund'))
ETF_CHANNELS = {'SH': 'sse_fund', 'SZ': 'cninfo_fund'}
ISSUER_NAME = r'[\u3400-\u9fffA-Za-z][\u3400-\u9fffA-Za-z0-9（）()·-]{1,29}'
_A_SHARE_COLUMNS = {
    'SHZB': 'SH', 'SHKCB': 'SH',
    'SZZB': 'SZ', 'SZZXB': 'SZ', 'SZCYB': 'SZ',
}
_SSE_FUND_PDF = re.compile(r'/disclosure/fund/announcement/c/new/(?P<date>\d{4}-\d{2}-\d{2})/'
                           r'(?P<code>5\d{5})_(?P<file_date>\d{8})_[A-Za-z0-9]+\.pdf')
HONG_KONG = timezone(timedelta(hours=8))
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_PDF_BYTES = 25 * 1024 * 1024
HKEX_MAX_PAGES = 10
_ALL_ANNOUNCEMENTS = re.compile(r'查齐|查全|找全|(?:全部|所有)(?:公告|披露)')
_NO_OFFICIAL = re.compile(
    r'(?:不|不要|无需|不用|禁止|别)(?:使用|用|查询|查)?'
    r'(?P<source>官方(?:渠道|公告|目录|来源)?|巨潮(?:资讯)?|披露易)')


def exhaustive_directory(task, code):
    """Only explicit requests for the whole security/date directory expand."""
    from .draft_sources import _unquoted_local_scope
    from .evidence import security_code_mentions
    from .subject_scope import scope_question, subject_key

    text = _unquoted_local_scope(scope_question(task))
    return any(_ALL_ANNOUNCEMENTS.search(clause) and subject_key(code) in {
        subject_key(value) for value in security_code_mentions(
            clause, encoding=task.get('security_declaration_encoding', 1))}
        for clause in re.split(r'[，,。；;！？!?\n]', text))


def directory_query(task, parameters):
    from .draft_sources import _unquoted_local_scope, validate_stated_source

    code = parameters['codes']
    if task.get('official_source_expansion_version') != 1 or not exhaustive_directory(task, code):
        return None
    channel = 'hkex' if code.endswith('.HK') else 'cninfo'
    aliases = ('巨潮', '巨潮资讯') if channel == 'cninfo' else ('披露易',)
    if any(match['source'].startswith('官方') or match['source'] in aliases
           for match in _NO_OFFICIAL.finditer(_unquoted_local_scope(task['question']))):
        return None
    query = dict(channel=channel, code=code, start_date=parameters['beginrDate'],
                 end_date=parameters['endrDate'], page='1', keyword='')
    try:
        validate_stated_source(task, 'official')
        validate_parameters(query, task['cutoff'])
    except ContractError:
        return None
    return query


def directory_coverage(task, query):
    """Read the consecutive pages of one query without extrapolating coverage."""
    from .official_sources import verified_catalogue

    pages, entries = {}, {}
    channel = query['channel']
    for record in task['source_records']:
        parameters = record.get('parameters') or {}
        page = parameters.get('page')
        if page is None or dict(parameters, page='1') != query:
            continue
        snapshot = verified_catalogue(task, record)
        response = retained_response(task, parameters, 'catalogue')
        if snapshot and response and snapshot['original_sha256'] == response['checkpoint']['sha256']:
            pages.setdefault(int(page), []).append((record['id'], snapshot))

    result = dict(channel=channel, name=CHANNELS[channel]['name'], complete=False,
                  source_refs=[], rows=[], next_page=1, gap='第1页尚未取得。')
    total = None
    while (page := result['next_page']) in pages:
        if len(pages[page]) != 1:
            result.update(next_page=None, gap=f'第{page}页有多份返回，覆盖范围待核对。')
            break
        source_ref, snapshot = pages[page][0]
        result['source_refs'].append(source_ref)
        for row in snapshot['entries']:
            entries.setdefault(row['url'], dict(date=row['listed_date'], title=row['title'],
                                                source_id=row['provider_id']))
        coverage = snapshot['coverage']
        if total is None:
            total = coverage['publisher_reported_total']
        size = coverage['maximum_entries']
        expected_rows = min(size, max(0, total - (page - 1) * size))
        expected_more = total > page * size
        if (coverage['publisher_reported_total'] != total or coverage['returned_rows'] != expected_rows
                or coverage['has_more'] != expected_more
                or channel == 'cninfo' and coverage['publisher_reported_pages'] != ceil(total / size)):
            result.update(next_page=None, gap=f'第{page}页的返回条数或分页状态不一致。')
            break
        if not expected_more:
            complete = len(entries) == total
            result.update(next_page=None, complete=complete, gap='' if complete else '目录存在重复或无法定位的记录。')
            break
        if channel == 'hkex' and page == HKEX_MAX_PAGES:
            result.update(next_page=None, gap='已到披露易1000条检索上限。')
            break
        result.update(next_page=page + 1, gap=f'第{page + 1}页尚未取得。')
    result['rows'] = sorted(entries.values(), key=lambda row: row['date'], reverse=True)
    return result


CHECKPOINT_FIELDS = ('task_id', 'research_version', 'channel', 'code', 'role',
                     'sha256', 'original_base64', 'dispatch_fingerprint')


def response_checkpoint_payload(task, checkpoint):
    """Validate retained bytes against their task and actual dispatch record.

    Retention is not adoption. A cancelled run may keep a complete response;
    only a later active run may parse and use it, without inventing a new send.
    """
    if (not isinstance(checkpoint, dict) or checkpoint.get('task_id') != task['id']
            or checkpoint.get('research_version') != task['version']
            or checkpoint.get('channel') not in CHANNELS
            or checkpoint.get('role') not in ('issuer_lookup', 'catalogue')):
        raise ContractError('官方响应检查点不属于当前任务和版本')
    try:
        identity={key:checkpoint[key] for key in CHECKPOINT_FIELDS}
        timepoint(checkpoint['obtained_at'])
        if fingerprint(identity) != checkpoint.get('id'):
            raise ContractError('官方响应检查点身份已变化')
        raw=base64.b64decode(checkpoint['original_base64'], validate=True)
        if len(raw)>MAX_JSON_BYTES or hashlib.sha256(raw).hexdigest()!=checkpoint['sha256']:
            raise ContractError('官方响应检查点原始字节已变化')
    except (ValueError, TypeError, KeyError) as error:
        raise ContractError('官方响应检查点字节或身份无效') from error
    role='issuer_lookup' if checkpoint['role']=='issuer_lookup' else None
    matching=[event for event in task.get('ledger',{}).get('dispatches',[])
        if event.get('kind')=='data_requests' and event.get('source')=='official'
        and event.get('operation')=='release_catalog' and event.get('role')==role
        and event.get('fingerprint')==checkpoint['dispatch_fingerprint']
        and event.get('response_sha256')==checkpoint['sha256']]
    if not matching:
        raise ContractError('官方响应检查点缺少原请求的字节记录')
    return raw, matching


def retained_response(task, parameters, role):
    """Find a complete task-local response; never spend or rewrite its ledger."""
    if task.get('official_source_expansion_version') != 1 or parameters.get('channel') not in CHANNELS:
        return None
    digest=fingerprint(dict(source='official',operation='release_catalog',parameters=parameters))
    rows=task.get('official_response_checkpoints',[])
    if not isinstance(rows,list):return None
    if role == 'issuer_lookup' and 'name' in parameters:
        digest = fingerprint(dict(parent_request_hash=digest, role=role))
    require_exact_dispatch = role == 'catalogue' or 'name' in parameters
    for checkpoint in reversed(rows):
        if (not isinstance(checkpoint,dict) or checkpoint.get('role')!=role
                or checkpoint.get('channel')!=parameters['channel'] or checkpoint.get('code')!=parameters.get('code')
                or require_exact_dispatch and checkpoint.get('dispatch_fingerprint')!=digest):continue
        try:raw, events=response_checkpoint_payload(task,checkpoint)
        except ContractError:continue
        complete=any(event.get('status')=='成功' or all(event.get(key) is True for key in
            ('transport_isolated','network_request_started','network_worker_reaped')) for event in events)
        if complete:return dict(checkpoint=checkpoint,payload=raw)
    return None


def retained_issuer_payload(task, parameters):
    """Read a validated issuer mapping from this task, without any I/O."""
    if task.get('official_source_expansion_version') != 1 or parameters.get('channel') not in CHANNELS:
        return None
    from .official_sources import release_choices
    valid_catalogues = {choice['catalog_id'] for choice in release_choices(task)}
    selector = 'name' if 'name' in parameters else 'code'
    for record in reversed(task.get('source_records', [])):
        saved = record.get('parameters') or {}
        if (record.get('id') in valid_catalogues and saved.get('channel') == parameters['channel']
                and saved.get(selector) == parameters[selector]):
            return base64.b64decode(record['snapshot']['issuer_original_base64'], validate=True)
    retained = retained_response(task, parameters, 'issuer_lookup')
    if retained is not None:
        try:
            request_issuer(retained['payload'], parameters)
            return retained['payload']
        except ContractError:
            pass
    return None


def catalogue_recovery(task, parameters, *, main_dispatched=False):
    """Continue a complete local stage, never repeat an interrupted query."""
    if retained_issuer_payload(task, parameters) is None:
        return None
    if retained_response(task, parameters, 'catalogue') is not None:
        return 'catalogue'
    if not main_dispatched:
        return 'issuer_lookup'
    return None


def validate_parameters(parameters, cutoff):
    if not isinstance(parameters, dict):
        raise ContractError('公告检索参数须为对象')
    selector = 'name' if 'name' in parameters else 'code'
    required = {'channel', selector, 'start_date', 'end_date', 'page', 'keyword'}
    if set(parameters) != required:
        raise ContractError('公告检索须指定渠道、标的、起止日期、页码和标题关键词')
    channel, value = parameters['channel'], parameters[selector]
    if not isinstance(channel, str) or channel not in CHANNELS or not isinstance(value, str):
        raise ContractError('该公告渠道或标的尚未登记')
    if selector == 'name':
        if channel != 'cninfo' or re.fullmatch(ISSUER_NAME, value) is None:
            raise ContractError('名称查询只支持巨潮沪深A股证券简称')
    elif re.fullmatch(CHANNELS[channel]['code_pattern'], value) is None:
        raise ContractError('该公告渠道不支持所选证券市场或代码格式')
    for name in ('start_date', 'end_date'):
        value = parameters[name]
        if not isinstance(value, str) or re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) is None:
            raise ContractError('公告检索日期须使用年月日')
        timepoint(value)
    if not parameters['start_date'] <= parameters['end_date'] <= market_date(cutoff):
        raise ContractError('公告检索区间倒置或晚于研究截止日期')
    page = parameters['page']
    if not isinstance(page, str) or re.fullmatch(r'[1-9]\d{0,3}', page) is None:
        raise ContractError('公告页码须为从1开始的十进制文字')
    if channel == 'hkex' and int(page) > HKEX_MAX_PAGES:
        raise ContractError('披露易最多显示1000条，请缩小日期区间，不声称已查完全部公告')
    keyword = parameters['keyword']
    if not isinstance(keyword, str) or len(keyword) > 100 or any(ord(c) < 32 for c in keyword):
        raise ContractError('公告标题关键词无效')
    return parameters


def original_url(value, channel):
    if channel not in CHANNELS or not isinstance(value, str):
        raise ContractError('公告原件渠道或地址未登记')
    parsed = urlsplit(value)
    patterns = {
        'cninfo': r'/finalpage/\d{4}-\d{2}-\d{2}/\d+\.[Pp][Dd][Ff]',
        'sse_fund': _SSE_FUND_PDF,
        'hkex': r'/listedco/listconews/sehk/\d{4}/\d{4}/[A-Za-z0-9_-]+\.pdf',
    }
    pattern = patterns['cninfo' if channel in CNINFO_CHANNELS else channel]
    if (parsed.scheme != 'https' or parsed.netloc != CHANNELS[channel]['original_host']
            or parsed.query or parsed.fragment or re.fullmatch(pattern, parsed.path) is None):
        raise ContractError('公告原件须来自所选渠道留存目录中的公开PDF地址')
    return value


def _json(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_JSON_BYTES:
        raise ContractError('公告目录响应超过4MB或字节格式无效')
    try:
        return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_json_object)
    except DuplicateJSONField:
        raise
    except (UnicodeError, ValueError) as error:
        raise ContractError('公告目录未返回可解析的JSON，不把拦截页面当作空结果') from error


def request_issuer(raw, parameters):
    """Name queries are a separate selector within the same public channel."""
    if 'name' in parameters:
        return issuer_from_response(raw, parameters['channel'], parameters['name'], named=True)
    return issuer_from_response(raw, parameters['channel'], parameters['code'])


@lru_cache(maxsize=8)
def issuer_from_response(raw, channel, code, *, named=False):
    data = _json(raw)
    literal = code.split('.')[0]
    if channel == 'sse_fund':
        rows = data.get('result') if isinstance(data, dict) and data.get('sqlId') == _SSE_FUND_LOOKUP else None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ContractError('上交所未返回唯一ETF基本信息')
        row = rows[0]
        name = row.get('FUND_NAME')
        if row.get('TRADE_CODE') != literal or not isinstance(name, str) or '交易型开放式' not in name:
            raise ContractError('上交所ETF基本信息未能核对交易代码与产品名称')
        return dict(code=literal, name=name, issuer_id=literal, exchange='SH', publisher_type='ETF')
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise ContractError('证券目录结构变化，不能推测发行人标识')
    if named:
        matches = {
            (row['code'], row['orgId']) for row in data
            if channel == 'cninfo' and row.get('zwjc') == code and row.get('category') == 'A股'
            and (row.get('delisted') is False or row.get('delisted') == 'false')
            and isinstance(row.get('code'), str) and re.fullmatch(r'\d{6}', row['code'])
            and isinstance(row.get('orgId'), str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', row['orgId'])
        }
        if len(matches) != 1:
            raise ContractError('巨潮未返回唯一在市A股简称，无法确认发行人')
        (literal, org_id), = matches
        return dict(code=literal, name=code, issuer_id=org_id, publisher_type='A股',
                    exchange=None, exchange_basis='交易所依据所选发行人公告的pageColumn核对')
    if channel in CNINFO_CHANNELS:
        category = 'ETF' if channel == 'cninfo_fund' else 'A股'
        matches = [item for item in data if item.get('code') == literal and item.get('category') == category
                   and (channel != 'cninfo_fund' or item.get('type') == 'fund')]
        if len(matches) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(matches[0].get('orgId', ''))):
            raise ContractError('巨潮未返回唯一匹配的发行人，不能由证券代码拼造orgId')
        row = matches[0]
        result = dict(code=row['code'], name=row.get('zwjc'), issuer_id=row['orgId'],
            exchange=code.split('.')[1], exchange_basis='请求市场，目录逐行核对pageColumn', publisher_type=row.get('type'))
    else:
        matches = [item for item in data if isinstance(item.get('c'), str) and item['c'].isdigit()
                   and int(item['c']) == int(literal)]
        if (len(matches) != 1 or type(matches[0].get('i')) is not int or matches[0]['i'] <= 0):
            raise ContractError('披露易未返回唯一证券标识，不能把股票代码当作stockId')
        row = matches[0]
        result = dict(code=row['c'], name=row.get('n'), issuer_id=row['i'], exchange='SEHK')
    if not isinstance(result['name'], str) or not result['name'].strip():
        raise ContractError('官方证券目录缺少名称')
    return result


def request_spec(parameters, phase, issuer=None):
    """Build only registered public requests; no URL or credentials from a model."""
    channel = parameters['channel']
    profile = CHANNELS[channel]
    if phase == 'lookup':
        if channel in CNINFO_CHANNELS:
            selector = parameters['name'] if 'name' in parameters else parameters['code'].split('.')[0]
            return dict(url=profile['lookup']+'?'+urlencode(dict(keyWord=selector, maxNum=10)),
                        method='POST', body=b'')
        if channel == 'sse_fund':
            query = dict(isPagination='false', FUNDID2=parameters['code'].split('.')[0], sqlId=_SSE_FUND_LOOKUP)
            return dict(url=profile['lookup']+'?'+urlencode(query), method='GET', body=None)
        return dict(url=profile['lookup'], method='GET', body=None)
    if phase != 'catalogue' or not isinstance(issuer, dict):
        raise ContractError('公告查询阶段缺少已取得的发行人映射')
    if channel in CNINFO_CHANNELS:
        column = '' if 'name' in parameters else ('fund' if channel == 'cninfo_fund'
                                                 else {'SH': 'sse', 'SZ': 'szse'}[issuer['exchange']])
        form = dict(pageNum=parameters['page'], pageSize='30', tabName='fulltext',
            column=column,
            stock=issuer['code']+','+issuer['issuer_id'], searchkey=parameters['keyword'], secid='', plate='',
            category='', trade='', seDate=parameters['start_date']+'~'+parameters['end_date'],
            sortName='time', sortType='desc', isHLtitle='false')
        return dict(url=profile['query'], method='POST', body=urlencode(form).encode())
    if channel == 'sse_fund':
        page = parameters['page']
        pagination = {'pageHelp.pageSize': '30', 'pageHelp.beginPage': page, 'pageHelp.cacheSize': '1',
                      'pageHelp.endPage': page, 'pageHelp.pageNo': page}
        query = dict(pagination, isPagination='true', type='inParams', sqlId=_SSE_FUND_FILINGS,
                     SECURITY_CODE=issuer['code'], TITLE=parameters['keyword'], ORG_BULLETIN_TYPE='', OTHER_TYPE='',
                     START_DATE=parameters['start_date'], END_DATE=parameters['end_date'])
        return dict(url=profile['query']+'?'+urlencode(query), method='GET', body=None)
    query = dict(lang='ZH', category='0', market='SEHK', stockId=str(issuer['issuer_id']), searchType='0',
        t1code='-2', t2Gcode='-2', t2code='-2', documentType='-1',
        fromDate=parameters['start_date'].replace('-', ''), toDate=parameters['end_date'].replace('-', ''),
        rowRange=str(int(parameters['page'])*100), sortByOptions='DateTime', sortDir='0')
    return dict(url=profile['query']+'?'+urlencode(query), method='GET', body=None)


def _text(value):
    return ' '.join(unescape(re.sub(r'<[^>]*>', ' ', value)).split()) if isinstance(value, str) else ''


def _integer(value, label):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r'\d{1,9}', value):
        return int(value)
    raise ContractError(label+'未返回非负整数')


def parse_catalogue(raw, issuer_raw, parameters, cutoff):
    validate_parameters(parameters, cutoff)
    channel = parameters['channel']
    issuer = request_issuer(issuer_raw, parameters)
    named = 'name' in parameters
    data = _json(raw)
    if not isinstance(data, dict):
        raise ContractError('公告目录结构变化')
    if channel in CNINFO_CHANNELS:
        total = _integer(data.get('totalAnnouncement'), '公告总数')
        pages = _integer(data.get('totalpages'), '公告页数')
        rows = data.get('announcements')
        if rows is None and total == 0:
            rows = []
        more = data.get('hasMore')
        if type(more) is not bool:
            raise ContractError('巨潮目录缺少分页状态')
    elif channel == 'sse_fund':
        page = data.get('pageHelp')
        if data.get('sqlId') != _SSE_FUND_FILINGS or not isinstance(page, dict):
            raise ContractError('上交所基金目录结构变化')
        total = _integer(page.get('total'), '公告总数')
        pages = _integer(page.get('pageCount'), '公告页数')
        rows = page.get('data')
        if rows is None and total == 0:
            rows = []
        more = int(parameters['page']) < pages
    else:
        rows = data.get('result')
        if isinstance(rows, str):
            try:
                rows = json.loads(rows, object_pairs_hook=unique_json_object)
            except DuplicateJSONField:
                raise
            except ValueError as error:
                raise ContractError('披露易结果结构变化') from error
        total = _integer(data.get('recordCnt'), '公告总数')
        raw_more = data.get('hasNextRow')
        if raw_more not in (True, False, 'true', 'false'):
            raise ContractError('披露易目录缺少分页状态')
        more = raw_more in (True, 'true')
        pages = None  # rowRange is a prefix length, never a verified total page count.
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows) or total and not rows:
        raise ContractError('公告目录结构或数量不一致，不把失败当作空结果')
    if channel == 'hkex':
        start = (int(parameters['page'])-1)*100
        rows = rows[start:int(parameters['page'])*100]
    entries, seen, excluded = [], set(), {'subject': 0, 'date': 0, 'keyword': 0, 'unsupported_original': 0, 'invalid_row': 0}
    for index, row in enumerate(rows):
        row_issuer, code = issuer, parameters.get('code')
        if channel in CNINFO_CHANNELS:
            column = str(row.get('pageColumn', ''))
            exchange = _A_SHARE_COLUMNS.get(column) if named else issuer['exchange']
            if (row.get('secCode') != issuer['code'] or row.get('orgId') != issuer['issuer_id']
                    or not exchange or not column.startswith(exchange)
                    or channel == 'cninfo_fund' and column != 'SZJJ'):
                excluded['subject'] += 1; continue
            if named:
                row_issuer = dict(issuer, exchange=exchange)
                code = issuer['code'] + '.' + exchange
            value = row.get('announcementTime')
            if type(value) is not int or value < 0:
                excluded['invalid_row'] += 1; continue
            try: dt = datetime.fromtimestamp(value/1000, HONG_KONG)
            except (ValueError, OverflowError, OSError):
                excluded['invalid_row'] += 1; continue
            date = dt.date().isoformat(); listed_at = None  # provider supplies a date bucket, not a publication clock.
            url = 'https://static.cninfo.com.cn/'+str(row.get('adjunctUrl', ''))
            identity = row.get('announcementId'); title = _text(row.get('announcementTitle'))
            reported = dict(announcementTime=value, adjunctUrl=row.get('adjunctUrl'), pageColumn=row.get('pageColumn'))
        elif channel == 'sse_fund':
            if row.get('SECURITY_CODE') != issuer['code']:
                excluded['subject'] += 1; continue
            date = row.get('SSEDATE')
            path = row.get('URL')
            address = _SSE_FUND_PDF.fullmatch(path) if isinstance(path, str) else None
            if (not address or date != address['date'] or date.replace('-', '') != address['file_date']
                    or issuer['code'] != address['code']):
                excluded['unsupported_original'] += 1; continue
            try:
                timepoint(date)
            except ContractError:
                excluded['invalid_row'] += 1; continue
            url = 'https://www.sse.com.cn'+path
            identity = path
            title = _text(row.get('TITLE'))
            listed_at = None
            reported = {key: row.get(key) for key in ('SECURITY_CODE', 'URL', 'SSEDATE', 'BULLETIN_TYPE')}
        else:
            codes = [value.strip() for value in re.split(r'<br\s*/?>', str(row.get('STOCK_CODE', '')), flags=re.I)]
            if issuer['code'] not in codes:
                excluded['subject'] += 1; continue
            try: dt = datetime.strptime(row['DATE_TIME'], '%d/%m/%Y %H:%M').replace(tzinfo=HONG_KONG)
            except (ValueError, TypeError, KeyError):
                excluded['invalid_row'] += 1; continue
            date = dt.date().isoformat(); listed_at = dt.isoformat()
            url = 'https://www1.hkexnews.hk'+str(row.get('FILE_LINK', ''))
            identity = row.get('NEWS_ID'); title = _text(row.get('TITLE'))
            reported = {key: row.get(key) for key in ('DATE_TIME', 'STOCK_CODE', 'FILE_LINK', 'FILE_TYPE')}
        if (not parameters['start_date'] <= date <= parameters['end_date'] or date > market_date(cutoff)
                or listed_at is not None and timepoint(listed_at) > timepoint(cutoff)):
            excluded['date'] += 1; continue
        if parameters['keyword'] and parameters['keyword'].casefold() not in title.casefold():
            excluded['keyword'] += 1; continue
        try: original_url(url, channel)
        except ContractError:
            excluded['unsupported_original'] += 1; continue
        if not isinstance(identity, str) or not identity or not title:
            excluded['invalid_row'] += 1; continue
        if url in seen: continue
        seen.add(url)
        entries.append(dict(id=channel+'_'+hashlib.sha256((identity+url).encode()).hexdigest()[:24],
            channel=channel, title=title, url=url, format='pdf', code=code,
            issuer=dict(row_issuer), provider_id=identity, listed_date=date, listed_at=listed_at,
            path_date_hint=None, published_at=None, response_row=index, reported=reported,
            limitation='目录日期为官方列表所载日期，不证明首次公开时刻；正文须另取所选原件。'))
    return dict(entries=entries, issuer=dict(issuer), query_scope=dict(parameters),
        coverage=dict(completeness='未证实', returned_rows=len(rows), publisher_reported_total=total,
            publisher_reported_pages=pages, has_more=more, maximum_entries=100 if channel == 'hkex' else 30,
            excluded=excluded, keyword_scope='标题，不检索正文', automatic_traversal=False),
        original_base64=base64.b64encode(raw).decode(), original_sha256=hashlib.sha256(raw).hexdigest(),
        issuer_original_base64=base64.b64encode(issuer_raw).decode(),
        issuer_original_sha256=hashlib.sha256(issuer_raw).hexdigest())


def download(parameters, phase, timeout, *, issuer_raw=None, selected=None):
    """One bounded public HTTPS send. The caller reserves each wire request."""
    if phase == 'original':
        if not isinstance(selected, dict): raise ContractError('缺少目录选择')
        channel = selected['channel']
        spec = dict(url=original_url(selected['url'], channel), method='GET', body=None)
        limit, types = MAX_PDF_BYTES, {'application/pdf', 'application/octet-stream'}
    else:
        validate_parameters(parameters, parameters['end_date'])
        channel = parameters['channel']
        issuer = request_issuer(issuer_raw, parameters) if phase == 'catalogue' else None
        spec = request_spec(parameters, phase, issuer)
        limit, types = MAX_JSON_BYTES, {'application/json', 'text/json', 'text/plain'}
    from .evidence import _PublicFilingConnection
    url = urlsplit(spec['url']); deadline = time.monotonic()+timeout
    connection = _PublicFilingConnection(url.hostname, timeout=timeout, use_local_proxy=True)
    try:
        target = url.path+('?' + url.query if url.query else '')
        headers = {'Accept': 'application/pdf' if phase == 'original' else 'application/json',
            'Accept-Encoding': 'identity', 'User-Agent': 'ResearchHelper/0.10',
            'Referer': CHANNELS[channel]['referer']}
        if spec['method'] == 'POST': headers['Content-Type'] = 'application/x-www-form-urlencoded'
        connection.request(spec['method'], target, body=spec['body'], headers=headers)
        response = connection.getresponse()
        if response.status != 200:
            raise urllib.error.HTTPError(spec['url'], response.status, '官方公告请求失败', response.headers, None)
        if response.getheader('Content-Type', '').split(';')[0].strip().lower() not in types:
            raise ContractError('官方响应类型与公告目录或原件不符')
        length = response.getheader('Content-Length')
        if length is not None and (not length.isdigit() or int(length) > limit):
            raise ContractError('官方公告响应长度异常')
        chunks, size = [], 0
        while True:
            remaining = deadline-time.monotonic()
            if remaining <= 0: raise ContractError('官方公告读取超时')
            if connection.sock is not None: connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, limit+1-size))
            if not chunk: break
            chunks.append(chunk); size += len(chunk)
            if size > limit: raise ContractError('官方公告响应超出大小限制')
        if length is not None and size != int(length): raise ContractError('官方公告响应未接收完整')
        if time.monotonic() >= deadline: raise ContractError('官方公告读取超时')
        raw = b''.join(chunks)
        if phase == 'original':
            if not raw.lstrip().startswith(b'%PDF-'): raise ContractError('公告未返回PDF原件，不把HTML提示页当正文')
        else: _json(raw)
        return raw
    finally:
        connection.close()
