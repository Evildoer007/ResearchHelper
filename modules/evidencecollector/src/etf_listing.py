"""Listed ETF names from the exchanges' own current product directories."""
from collections import defaultdict
from hashlib import sha256
import json
import re
import time
from unicodedata import normalize
from urllib.parse import urlencode, urlsplit

from .contracts import ContractError, unique_json_object
from .subject_scope import subject_allowed

OPERATION = 'etf_listing'
POLICY = 'official_etf_listing_version'
REGISTRY = {'official.'+OPERATION: dict(version=1, kind='ETF证券名录', fields={}, raw_only=True)}
MARKET_NAMES = {'SH': '上交所基金名录', 'SZ': '深交所ETF名录'}
_SH_URL = 'https://query.sse.com.cn/commonSoaQuery.do?'+urlencode(
    dict(sqlId='FUND_LIST', fundType='00', isPagination='false'))
_SZ_URL = 'https://www.szse.cn/api/report/ShowReport?'+urlencode(
    dict(SHOWTYPE='xls', CATALOGID='1945', TABKEY='tab1'))
_PAGES = {'SH': 'https://www.sse.com.cn/assortment/fund/list/?type=5',
          'SZ': 'https://www.szse.cn/market/product/list/etfList/index.html'}
_URLS = {'SH': _SH_URL, 'SZ': _SZ_URL}
_LIMIT = 2 * 1024 * 1024
_MAX_ROWS = 2000
_CODE = re.compile(r'\d{6}')
_MANAGER_SUFFIX = re.compile(r'(?:基金管理|资产管理)(?:股份有限公司|有限责任公司|有限公司|公司)\Z')


def name_key(name):
    return re.sub(r'\s+', '', normalize('NFKC', name)).casefold()


def _name_field(raw, *, allow_empty=False):
    if not isinstance(raw, str) or len(raw) > 100 or any(ord(char) < 32 for char in raw):
        raise ContractError('交易所ETF名录的名称字段不完整')
    name = raw.strip()
    if not name and not allow_empty:
        raise ContractError('交易所ETF名录的名称字段为空')
    return name


def _sse_rows(payload):
    data = json.loads(payload.decode('utf-8-sig'), object_pairs_hook=unique_json_object)
    if not isinstance(data, dict) or data.get('sqlId') != 'FUND_LIST':
        raise ContractError('上交所基金名录未返回登记的基金列表')
    result = data.get('result')
    pagination = data.get('pageHelp') or {}
    total = pagination.get('total')
    if (not isinstance(result, list) or type(total) is not int or len(result) != total
            or not 1 <= total <= _MAX_ROWS):
        raise ContractError('上交所基金名录未返回完整列表')
    if any(not isinstance(row, dict) or row.get('fundType') != '00' for row in result):
        raise ContractError('上交所基金名录的ETF分类不符')
    return [(row.get('fundCode'), row.get('secNameFull'), row.get('companyName'), row.get('fundAbbr'))
            for row in result]


def _szse_rows(payload):
    from xlrd import XLRDError, open_workbook

    if not payload.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):
        raise ContractError('深交所ETF名录未返回登记的工作簿格式')
    try:
        workbook = open_workbook(file_contents=payload, on_demand=True)
        try:
            sheet = workbook.sheet_by_name('ETF列表')
            if (not 2 <= sheet.nrows <= _MAX_ROWS+1 or sheet.ncols != 5
                    or sheet.row_values(0) != ['证券代码', '证券简称', '拟合指数', '当前规模(份)', '基金管理人']):
                raise ContractError('深交所ETF名录的表头或行数不符')
            return [(code, name, manager, '') for code, name, _, _, manager in
                    (sheet.row_values(index) for index in range(1, sheet.nrows))]
        finally:
            workbook.release_resources()
    except (XLRDError, KeyError, IndexError, ValueError, OverflowError) as error:
        raise ContractError('深交所ETF名录的工作簿不可读') from error


def parse_listing(market, payload):
    if market not in _URLS or not isinstance(payload, bytes) or len(payload) > _LIMIT:
        raise ContractError('ETF名录来源或响应大小不符')
    entries = _sse_rows(payload) if market == 'SH' else _szse_rows(payload)
    rows = {}
    for code, name, manager, short in entries:
        if not isinstance(code, str) or _CODE.fullmatch(code) is None:
            raise ContractError('交易所ETF名录的证券代码不符')
        full_code = code+'.'+market
        row = dict(code=full_code, name=_name_field(name), manager=_name_field(manager),
                   short=_name_field(short, allow_empty=True))
        if full_code in rows:
            raise ContractError('交易所ETF名录中有重复代码')
        rows[full_code] = row
    return dict(version=1, market=market, url=_URLS[market], directory=_PAGES[market],
                response_sha256=sha256(payload).hexdigest(), rows=list(rows.values()))


def download_listing(market, timeout):
    from .evidence import _PublicFilingConnection

    if market not in _URLS:
        raise ContractError('ETF名录市场未登记')
    parsed = urlsplit(_URLS[market])
    deadline = time.monotonic()+timeout
    connection = _PublicFilingConnection(parsed.hostname, timeout=timeout, use_local_proxy=True)
    mime = 'application/json' if market == 'SH' else 'application/vnd.ms-excel'
    try:
        connection.request('GET', parsed.path+'?'+parsed.query,
            headers={'Accept': mime, 'Referer': _PAGES[market], 'Accept-Encoding': 'identity'})
        response = connection.getresponse()
        if (response.status != 200
                or response.getheader('Content-Type', '').split(';')[0].strip().lower() != mime):
            raise ContractError('交易所ETF名录未返回登记格式')
        declared = response.getheader('Content-Length')
        if declared and (not declared.isdigit() or int(declared) > _LIMIT):
            raise ContractError('交易所ETF名录响应长度不符')
        payload = bytearray()
        while time.monotonic() < deadline:
            if connection.sock is not None:
                connection.sock.settimeout(max(0.001, deadline-time.monotonic()))
            chunk = response.read1(min(65536, _LIMIT+1-len(payload)))
            if not chunk:
                if declared and len(payload) != int(declared):
                    raise ContractError('交易所ETF名录接收不完整')
                return parse_listing(market, bytes(payload))
            payload.extend(chunk)
            if len(payload) > _LIMIT:
                raise ContractError('交易所ETF名录响应过大')
        raise ContractError('交易所ETF名录获取超时')
    finally:
        connection.close()


def validate_request(task, parameters):
    from .draft_sources import local_sources_only, validate_stated_source
    from .etf_comparison import named_targets

    if (task.get(POLICY) != 1 or task.get('research_mode') != 'current' or local_sources_only(task)
            or not isinstance(parameters, dict) or set(parameters) != {'market'}
            or parameters['market'] not in _URLS or not named_targets(task)):
        raise ContractError('ETF名录仅用于当前问题中需要核对的产品名称')
    validate_stated_source(task, 'official')


def listings(task):
    """Only current, same-task responses with a recorded actual dispatch."""
    from .evidence import current_query_reference

    results = {}
    for row in task.get('source_records', ()):
        if (row.get('source'), row.get('operation')) != ('official', OPERATION):
            continue
        market = row.get('parameters', {}).get('market')
        snapshot = row.get('snapshot', {})
        if (market in _URLS and snapshot.get('market') == market and snapshot.get('url') == _URLS[market]
                and current_query_reference(task, row) is not None):
            if market in results and results[market]['hash'] != row['hash']:
                return {}
            results[market] = row
    return results


def _aliases(item):
    name = name_key(item['name'])
    short = name_key(item['short'])
    manager = name_key(_MANAGER_SUFFIX.sub('', item['manager']))
    aliases = {name, short, item['code'].split('.')[0]} - {''}
    if manager and (name.endswith(manager) or name.startswith(manager)):
        stem = name.removesuffix(manager) if name.endswith(manager) else name.removeprefix(manager)
        if 'etf' in stem:
            aliases.update((stem, manager+stem, stem+manager))
            # The legal long name uses this suffix. The manager and benchmark
            # still have to match the actual exchange row exactly.
            suffix = 'etf'
            if stem.endswith(suffix):
                aliases.add(manager+stem.removesuffix(suffix)+'交易型开放式指数证券投资基金')
    return aliases


def resolve_names(task, names):
    """A unique exact alias across both current directories can select one ETF."""
    current = listings(task)
    candidates = defaultdict(dict)
    names_by_key = {name_key(name): name for name in names}
    for record in current.values():
        for index, item in enumerate(record['snapshot']['rows']):
            matched = _aliases(item) & names_by_key.keys()
            if not matched or not subject_allowed(task, item['code']) or not subject_allowed(task, item['name']):
                continue
            for alias in matched:
                if not subject_allowed(task, names_by_key[alias]):
                    continue
                candidates[alias][item['code']] = dict(
                    code=item['code'], name=item['name'], market=record['parameters']['market'],
                    source_ref=record['id'], row=index)
    resolved, unresolved = [], []
    for key, name in names_by_key.items():
        matches = list(candidates[key].values())
        if len(current) == len(_URLS) and len(matches) == 1:
            resolved.append(dict(matches[0], query=name))
        else:
            status = '名录未齐' if len(current) < len(_URLS) else '简称有歧义' if matches else '无精确匹配'
            unresolved.append(dict(query=name, status=status, count=len(matches), examples=matches[:4]))
    return resolved, unresolved


def listing_requests(task, names):
    if not names:
        return []
    requests = []
    known = listings(task)
    from .parallel_study import FUND_GROUP, branch_question

    question = branch_question(task, FUND_GROUP) or task['questions'][0]
    for market in _URLS.keys() - known.keys():
        parameters = dict(market=market)
        try:
            validate_request(task, parameters)
        except ContractError:
            continue
        requests.append(dict(id='etf_listing_'+market.lower(), source='official', operation=OPERATION,
            parameters=parameters, question_ids=[question['id']],
            selection_reason='按当前交易所名录核对问题中所指ETF的简称与证券代码。'))
    return sorted(requests, key=lambda item: item['parameters']['market'])
