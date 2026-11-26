"""Public LPR observations and their task-bound publication periods."""

from datetime import date, time, timedelta
from decimal import Decimal
from functools import lru_cache
from hashlib import sha256
import json
import re
from urllib.parse import urlencode
from urllib.request import Request
from zoneinfo import ZoneInfo

from .contracts import ContractError, timepoint
from .https_transport import open_verified_https

POLICY = 'lpr_research_version'
OPERATION = 'lpr_history'
PUBLISHER = '全国银行间同业拆借中心'
HISTORY_PAGE = 'https://www.chinamoney.com.cn/chinese/bklpr/?tab=5'
ENDPOINT = 'https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/LprHis'
FIELD = 'lpr_quote'
NAMES = {'1Y': '1年期LPR', '5Y': '5年期以上LPR'}
SHANGHAI = ZoneInfo('Asia/Shanghai')
MAX_BYTES = 96_000

_TOPIC = r'(?:(?<![A-Za-z])LPR(?![A-Za-z])|贷款(?:市场)?报价利率)'
_MONTH = re.compile(r'(?<!\d)(?:(20\d{2})[年/-])?(1[0-2]|0?[1-9])月(?!\s*\d{1,2}日)')
_OUTSIDE_STUDY = re.compile(r'降准|MLF|逆回购|联邦基金|美联储|港股|A股|股票|估值|ETF|预测|下一次|下(?:月|季度)|'
                            r'回测|超额收益|归因|具体银行|招商银行|工商银行|建设银行|农业银行|'
                            r'算.{0,8}(?:月供|利息|息差)|(?:月供|利息|息差).{0,8}(?:多少|测算|[上下]降几个)|'
                            r'公积金|提前还款.{0,6}(?:划算|应该)|我(?:的|家)房贷|何时会|'
                            r'累计|一共|今年|去年|明年|为什么.{0,8}(?:维持|不降息|降息|报价未调)', re.I)


def _month_before(month):
    return (month.replace(day=1) - timedelta(days=1)).replace(day=1)


def study_scope(task):
    """Resolve the requested benchmark and window without inferring private contracts."""
    from .draft_sources import _unquoted_local_scope, local_sources_only, validate_stated_source

    if (task.get(POLICY) != 1 or task.get('draft_first_version') != 1 or task.get('research_mode') != 'current'
            or task.get('intent') not in ('综合研究', '解释变化', '验证观点', '比较对象')
            or local_sources_only(task)):
        return None
    text = _unquoted_local_scope(task['question'])
    if (not re.search(_TOPIC, text, re.I) or re.match(r'^\s*(?:请\s*)?(?:假设|假如|如果|假定|若)', text)
            or re.search(rf'(?:别|不|无需|不要|不必)(?:查|看|取|用|引用|下载)\s*{_TOPIC}', text, re.I)
            or re.search(r'不(?:使用|用|查|看)(?:官方|中国货币网|全国银行间同业拆借中心)', text)
            or re.search(r'(?:过去|近|最近)(?:半|[3-9一三四五六七八九十]|\d{2,})\s*(?:个?月|年|期)|'
                         r'季度|今年以来|以来|[0-9]{1,2}月\s*\d{1,2}日|20\d{2}-\d{2}-\d{2}', text)):
        return None
    try:
        validate_stated_source(task, 'official')
    except ContractError:
        return None
    cutoff = timepoint(task['cutoff']).astimezone(SHANGHAI)
    end = cutoff.date()
    months = {date(int(year or end.year), int(month), 1) for year, month in _MONTH.findall(text)}
    if len(months) > 1 or (not months and re.search(r'(?:19|20)\d{2}年', text)):
        return None
    selected = next(iter(months), end.replace(day=1) if re.search(r'本月|这个月|当月', text) else None)
    if selected is not None and not date(2019, 9, 1) <= selected <= end:
        return None
    anchor = selected or end.replace(day=1)
    last = date(anchor.year + (anchor.month == 12), anchor.month % 12 + 1, 1) - timedelta(days=1)
    return dict(parameters=dict(start=_month_before(_month_before(anchor)).isoformat(), end=min(end, last).isoformat()),
                month=selected.isoformat()[:7] if selected else '',
                mortgage=bool(re.search(r'房贷|住房贷款|房屋按揭', text)),
                banks=bool(re.search(r'银行|息差|资金成本|净利息', re.sub(r'全国银行间同业拆借中心|中国人民银行', '', text))))


def direct_scope(task):
    from .draft_sources import _unquoted_local_scope
    from .evidence import declared_security_codes

    scope = study_scope(task)
    if not scope or any(task.get(key) for key in ('materials', 'pending_materials', 'calculation_specs')):
        return None
    questions = task['questions']
    if (len(questions) != 1 or not questions[0]['required'] or questions[0]['question'] != task['question']
            or any(questions[0].get(key) for key in ('answer_scope', 'evidence_needs', 'method_needs', 'dependencies'))
            or declared_security_codes(task)):
        return None
    text = _unquoted_local_scope(task['question'])
    if (_OUTSIDE_STUDY.search(text) or re.search(r'汇率|人民币走势|地产销量|房地产成交|城投|国债|债市|基准利率改革', text)
            or re.search(r'不看|不比较|只(?:看|查|比较)|仅(?:看|比较)|不用上期|第[三四五]期|\d{1,2}年期存款', text)
            or not re.search(r'调|变化|变动|降息|上期|两期|报价|最新|最近|多少|影响|怎么看|怎么理解|读数', text)):
        return None
    return dict(scope, question_id=questions[0]['id'])


def request_shape(scope):
    return dict(qualified_id_for_lookup_only='official.' + OPERATION, source='official', operation=OPERATION,
                parameter_keys=['start', 'end'], numeric_fields=[FIELD], credential_required=False,
                available_parameter_sets=[scope['parameters']],
                selection_rule='中国货币网正式发布的1年期和5年期以上LPR，保留各期发布日期。')


def validate_request(task, parameters):
    if not (scope := study_scope(task)) or parameters != scope['parameters']:
        raise ContractError('LPR查询期间须与本任务所问期间一致。')


def source_url(parameters):
    return ENDPOINT + '?' + urlencode(dict(lang='CN', strStartDate=parameters['start'], strEndDate=parameters['end']))


@lru_cache(maxsize=6)
def _cells(original, start, end):
    payload = json.loads(original)
    head, data, records = (payload[key] for key in ('head', 'data', 'records'))
    if ((head['rep_code'], data['startDateCN'], data['endDateCN'], data['baseCurveCfgList'])
            != ('200', start, end, list(NAMES)) or not isinstance(records, list) or len(records) > 12):
        raise ContractError('中国货币网LPR响应与查询窗口或期限不一致。')
    cells, months = [], set()
    for index, record in enumerate(records):
        day = record['showDateCN']
        if day != date.fromisoformat(day).isoformat() or not start <= day <= end or day[:7] in months:
            raise ContractError('中国货币网LPR发布日期越界或月份重复。')
        months.add(day[:7])
        for tenor in NAMES:
            value = record.get(tenor)
            if value in (None, ''):
                continue
            if not isinstance(value, str) or not re.fullmatch(r'\d{1,2}(?:\.\d{1,4})?', value) or not Decimal(value) > 0:
                raise ContractError('中国货币网LPR读数须为正的百分数。')
            cells.append((tenor, day, value, index))
    return tuple(cells)


def validated_snapshot(snapshot, parameters):
    original = snapshot['original_text']
    raw = original.encode('utf-8')
    if (not raw or len(raw) > MAX_BYTES or snapshot['url'] != source_url(parameters)
            or snapshot['original_sha256'] != sha256(raw).hexdigest()):
        raise ContractError('中国货币网LPR原始响应地址或摘要不一致。')
    return _cells(original, parameters['start'], parameters['end'])


def download_snapshot(parameters, timeout):
    url = source_url(parameters)
    request = Request(url, data=b'', headers={'Accept': 'application/json', 'Accept-Encoding': 'identity'})
    with open_verified_https(request, timeout=timeout, follow_redirects=False) as response:
        if response.headers.get_content_type() != 'application/json':
            raise ContractError('中国货币网未返回LPR历史JSON。')
        raw = response.read(MAX_BYTES + 1)
    return dict(url=url, original_text=raw.decode('utf-8'), original_sha256=sha256(raw).hexdigest())


def cell_locator(tenor, row):
    return dict(kind=FIELD, field=tenor, row=row)


def _select_periods(cells, cutoff, month):
    as_of = timepoint(cutoff).astimezone(SHANGHAI)
    available = {day[:7] for _, day, _, _ in cells if day < as_of.date().isoformat()
                 or (day == as_of.date().isoformat() and day >= '2024-07-22' and as_of.time() >= time(9))}
    target = month or max(available, default='')
    if not target or target not in available:
        return ()
    before = _month_before(date.fromisoformat(target + '-01')).isoformat()[:7]
    # A failed or missing release cannot silently turn a monthly comparison into a multi-month one.
    return tuple(sorted((cell for cell in cells if cell[1][:7] in {before, target}), key=lambda cell: (cell[1], cell[0])))


def retained_cells(task, record, scope):
    from .evidence import current_query_reference

    if record.get('parameters') != scope['parameters'] or not current_query_reference(task, record):
        return ()
    try:
        cells = validated_snapshot(record['snapshot'], scope['parameters'])
        return _select_periods(cells, task['cutoff'], scope['month'])
    except (ContractError, KeyError, TypeError, ValueError, AttributeError):
        return ()


def quote_pairs(facts):
    """Current and previous calendar-month quotes for each tenor."""
    quotes = {tenor: [] for tenor in NAMES}
    for row in facts:
        locator = row.get('locator', {})
        if row.get('source') == 'official' and locator.get('kind') == FIELD and row.get('unit') == '%':
            if locator.get('field') in quotes:
                quotes[locator['field']].append(row)
    pairs = {}
    for tenor, rows in quotes.items():
        if len(rows) != 2:
            continue
        prior, current = sorted(rows, key=lambda row: row['period'])
        previous_month = _month_before(date.fromisoformat(current['period'])).isoformat()[:7]
        if prior['period'][:7] == previous_month and prior['artifact_id'] == current['artifact_id']:
            pairs[tenor] = (prior, current)
    return pairs


def frozen_quotes(content, facts):
    """Reconstruct original cells before deriving any financial comparisons."""
    from .contracts import fingerprint

    scope = content.get('lpr_study')
    if content.get(POLICY) != 1 or not scope:
        return []
    parameters = scope['parameters']
    request_hash = fingerprint(dict(source='official', operation=OPERATION, parameters=parameters))
    values = {}
    for record in content.get('source_records', []):
        if ((record.get('source'), record.get('operation'), record.get('task_id'), record.get('research_version'))
                != ('official', OPERATION, content.get('task_id'), content.get('research_version'))
                or record.get('parameters') != parameters or record.get('request_hash') != request_hash
                or record.get('status') != '成功待核验' or fingerprint(record.get('snapshot')) != record.get('hash')):
            continue
        try:
            cells = validated_snapshot(record['snapshot'], parameters)
            for tenor, day, raw, row in _select_periods(cells, content['cutoff'], scope['month']):
                values[record['id'], tenor, day, row] = raw
        except (ContractError, KeyError, TypeError, ValueError, AttributeError):
            continue
    return [row for row in facts if row['field'] == FIELD and row.get('unit') == '%'
            and (raw := values.get((row['artifact_id'], row['locator'].get('field'), row['period'],
                                    row['locator'].get('row')))) is not None
            and row['raw_value'] == raw and row['value'] == float(raw)]
