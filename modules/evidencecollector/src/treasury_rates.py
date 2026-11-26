"""FOMC-day U.S. constant-maturity yields from the Board's H.15 via FRED."""

import csv
from datetime import date, time, timedelta
from decimal import Decimal
from functools import lru_cache
from hashlib import sha256
from io import StringIO
import re
from urllib.parse import urlencode
from urllib.request import Request
from zoneinfo import ZoneInfo

from .contracts import ContractError, fingerprint, timepoint
from .https_transport import open_verified_https

POLICY = 'draft_fomc_treasury_yields_version'
OPERATION = 'fed_h15_treasury_yields'
PUBLISHER = '美联储H.15（FRED）'
URL = 'https://fred.stlouisfed.org/graph/fredgraph.csv'
METHOD_SOURCE = 'https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/treasury-yield-curve-methodology'
FIELD = 'treasury_yield'
PERIOD_BASIS = '美东日期固定期限收益率'
NAMES = {'DGS2': '2年期美债', 'DGS10': '10年期美债'}
EASTERN = ZoneInfo('America/New_York')
MAX_BYTES = 750_000
_TREASURY = r'(?:美债|美国国债|美国公债)'
_TENOR = re.compile(r'(?<!\d)(\d{1,2}|[一二两三四五六七八九十]{1,3})(?:年期|年(?=及|和|与|、)|[Yy](?![A-Za-z]))')
_SHORT_TENOR = re.compile(r'(?:\d+|[一二两三四五六七八九十]+)个月(?:期|美债|收益率)')
_BASIS_OUTSIDE_SCOPE = re.compile(r'通胀保值|实际收益率|真实利率|TIPS|盘中|分时|分钟|即时|期货|'
                                  r'(?:第二|第三|第[2-9]|[2-9]个?)交易日|(?:一周|一月|一个月)后', re.I)
_UNRESOLVED_QUESTION = re.compile(r'归因|因果|为何|为什么|是不是.{0,12}导致|有多大影响|'
                                  r'预测|未来|接下来|能否持续|买入|卖出|推荐|持仓|收益策略|估值|'
                                  r'美元指数|汇率|美股|标普|纳指|大宗|期权|原油|黄金|通胀预期|期限溢价|'
                                  r'前一?次.{0,12}(?:收益率|美债反应|走势)|近两次', re.I)


def requested_series(task):
    """Adopt explicit nominal yields, with 2Y/10Y representing an unqualified curve."""
    from .draft_sources import _unquoted_local_scope, local_sources_only, validate_stated_source
    from .subject_scope import scope_question

    if task.get(POLICY) != 1 or task.get('research_mode') != 'current' or local_sources_only(task):
        return ()
    text = _unquoted_local_scope(scope_question(task))
    if (not re.search(_TREASURY, text) or not re.search(r'收益率|利差|曲线|长短端|短端|长端', text)
            or re.search(rf'(?:别|不|无需|不要)(?:查|看|取|使用|比较)?\s*{_TREASURY}', text)
            or _BASIS_OUTSIDE_SCOPE.search(text) or _SHORT_TENOR.search(text)):
        return ()
    try:
        validate_stated_source(task, 'official')
    except ContractError:
        return ()
    aliases = {'两': '2', '二': '2', '十': '10'}
    requested = {aliases.get(tenor, tenor) for tenor in _TENOR.findall(text)}
    if not requested <= {'2', '10'}:
        return ()
    if re.search(r'期限利差|长短端利差', text):
        return tuple(NAMES)
    if not requested:
        if '短端' in text and not re.search(r'长短端|长端|期限利差|曲线', text):
            return ('DGS2',)
        if '长端' in text and not re.search(r'长短端|短端|期限利差|曲线', text):
            return ('DGS10',)
    return tuple(code for code in NAMES if not requested or code[3:] in requested)


def bounded_readout(task):
    """An observed rates question can run on actual data without a model."""
    from .event_market import requested_markets
    from .evidence import declared_security_codes

    return (bool(requested_series(task)) and not _UNRESOLVED_QUESTION.search(task['question'])
            and not requested_markets(task) and not declared_security_codes(task)
            and not re.search(r'港股|A股|上证|沪深|恒生|股票|成交额|两融|通胀和|就业和', task['question'])
            and not task.get('materials') and not task.get('pending_materials') and not task.get('calculation_specs')
            and len(task['questions']) == 1
            and not any(task['questions'][0].get(key) for key in ('answer_scope', 'evidence_needs', 'method_needs')))


def _window(release, cutoff, series):
    event = release.astimezone(EASTERN)
    as_of = timepoint(cutoff).astimezone(EASTERN)
    # H.15 is published after US trading. Give current-day reads time to become available.
    if event.time() >= time(15, 30) or event >= as_of or (event.date() == as_of.date() and as_of.time() < time(18)):
        return None
    day = event.date()
    return dict(series=list(series), start=(day - timedelta(days=4)).isoformat(), end=day.isoformat())


def study_scope(task):
    from .event_market import policy_release_time

    if (series := requested_series(task)) and (release := policy_release_time(task)):
        return _window(release, task['cutoff'], series)
    return None


def request_shape(scope):
    return dict(qualified_id_for_lookup_only='official.'+OPERATION, source='official', operation=OPERATION,
                parameter_keys=['series', 'start', 'end'], numeric_fields=[FIELD], credential_required=False,
                available_parameter_sets=[scope], selection_rule='以FOMC原声明的美东日期为目标日，核对H.15的同口径名义收益率。')


def validate_request(task, parameters):
    if not (scope := study_scope(task)) or parameters != scope:
        raise ContractError('美债收益率查询须使用本任务已核对的FOMC日期和要求的期限。')
    return scope


def source_url(parameters):
    return URL+'?'+urlencode(dict(id=','.join(parameters['series']), cosd=parameters['start'], coed=parameters['end']))


@lru_cache(maxsize=6)
def _parse(source_text, series, start, end):
    rows = csv.reader(StringIO(source_text.removeprefix('\ufeff')))
    if next(rows, []) != ['observation_date', *series]:
        raise ContractError('H.15日期或收益率列与请求期限不一致。')
    cells = []
    previous = ''
    for offset, row in enumerate(rows, 1):
        if len(row) != len(series)+1 or row[0] <= previous or date.fromisoformat(row[0]).isoformat() != row[0]:
            raise ContractError('H.15日期无效、重复或列数不符。')
        previous = day = row[0]
        if start <= day <= end:
            for code, token in zip(series, row[1:], strict=True):
                if token in ('', '.'):
                    continue
                if not re.fullmatch(r'-?\d{1,2}(?:\.\d{1,4})?', token):
                    raise ContractError('H.15收益率栏不是有效百分比。')
                cells.append((code, day, token, offset))
    return tuple(cells)


def validated_snapshot(snapshot, parameters):
    text = snapshot['original_text']
    raw = text.encode('utf-8')
    if (not raw or len(raw) > MAX_BYTES or snapshot['url'] != source_url(parameters)
            or snapshot['original_sha256'] != sha256(raw).hexdigest()):
        raise ContractError('H.15原始响应地址或摘要不一致。')
    return _parse(text, tuple(parameters['series']), parameters['start'], parameters['end'])


def download_snapshot(parameters, timeout):
    url = source_url(parameters)
    request = Request(url, headers={'Accept': 'text/csv,application/csv', 'Accept-Encoding': 'identity'})
    with open_verified_https(request, timeout=timeout, follow_redirects=False) as response:
        if response.headers.get_content_type() not in ('text/csv', 'application/csv'):
            raise ContractError('H.15未返回登记的CSV。')
        raw = response.read(MAX_BYTES+1)
    if not raw or len(raw) > MAX_BYTES:
        raise ContractError('H.15响应为空或超出登记长度。')
    return dict(url=url, original_text=raw.decode('utf-8'), original_sha256=sha256(raw).hexdigest())


def cell_locator(code, row):
    return dict(kind=FIELD, field=code, row=row)


def retained_cells(task, record, scope):
    from .evidence import current_query_reference

    if scope and record.get('parameters') == scope and current_query_reference(task, record):
        try:
            cells = validated_snapshot(record['snapshot'], scope)
            prior = {code: day for code, day, _, _ in cells if day < scope['end']}
            return [cell for cell in cells if cell[1] in (scope['end'], prior.get(cell[0]))]
        except (ContractError, KeyError, TypeError, ValueError):
            return ()
    return ()


def _pairs(facts, scope):
    groups = {}
    for row in facts:
        if (row.get('source') == 'official' and row.get('locator', {}).get('kind') == FIELD
                and row['code'] in scope['series'] and row['unit'] == '%'
                and scope['start'] <= row['period'] <= scope['end'] and row['value'] is not None):
            groups.setdefault((row['code'], row['artifact_id']), {}).setdefault(row['period'], []).append(row)
    options = {}
    for (code, _), days in groups.items():
        current = scope['end']
        prior = max((day for day in days if day < current), default=None)
        if prior and len(days[prior]) == len(days.get(current, ())) == 1:
            options.setdefault(code, []).append((days[prior][0], days[current][0]))
    return {code: candidates[0] for code, candidates in options.items()
            if len({tuple((row['period'], Decimal(row['raw_value'])) for row in pair) for pair in candidates}) == 1}


def selected_pairs(task, facts):
    scope = study_scope(task)
    return _pairs(facts, scope) if scope else {}


def frozen_pairs(content, facts):
    """Restore only pairs still backed by the frozen rate feed and dated statement."""
    from .official_sources import fomc_rate_statement, fomc_statement_release_clock
    from .source_catalogue import official_record_label

    scope = content.get('fomc_treasury_scope')
    if content.get(POLICY) != 1 or not scope or scope.get('series') not in ([*NAMES], ['DGS2'], ['DGS10']):
        return {}
    sources = [record for record in content['source_records'] if record.get('task_id') == content['task_id']
               and record.get('research_version') == content['research_version']]
    clocks = set()
    for record in sources:
        if record.get('operation') == 'release_original' and official_record_label(record, sources) == '美联储':
            parts = record['snapshot']['parts']
            statement = fomc_rate_statement(record, parts)
            if statement and statement['date'] == scope['end'] and (clock := fomc_statement_release_clock(record, statement, parts)):
                clocks.add(clock['scheduled_at'])
    if len(clocks) != 1 or scope != _window(timepoint(clocks.pop()), content['cutoff'], scope['series']):
        return {}
    admitted = set()
    for record in sources:
        if (record.get('source'), record.get('operation'), record.get('parameters')) != ('official', OPERATION, scope):
            continue
        snapshot = record['snapshot']
        if record.get('hash') != fingerprint(snapshot):
            continue
        try:
            admitted.update((record['id'], code, day, token, row) for code, day, token, row in validated_snapshot(snapshot, scope))
        except (ContractError, KeyError, TypeError, ValueError):
            continue
    return _pairs([row for row in facts if row.get('field') == FIELD
                   and row['locator'] == cell_locator(row['code'], row['locator'].get('row'))
                   and (row['artifact_id'], row['code'], row['period'], row['raw_value'], row['locator']['row']) in admitted], scope)
