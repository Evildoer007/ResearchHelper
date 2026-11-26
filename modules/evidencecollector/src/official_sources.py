"""Fixed official release navigation; downloaded text is never approval.

Only retained registered directory links can be fetched. No web search, credentials,
redirects, active content or guessed document URLs enter this adapter.
"""
import base64
from datetime import date, datetime, time as clock_time
from fractions import Fraction
import hashlib
from functools import lru_cache
from html.parser import HTMLParser
import re
import time
import urllib.error
from urllib.parse import urljoin, urlsplit

from .contracts import ContractError, fingerprint, market_date, timepoint
from . import miit_releases

ROOT_URL = 'https://www.stats.gov.cn/sj/zxfbhjd/'
FED_CALENDAR_URL = 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'
POLICY_CHANNELS = {
    'pbc_news': dict(name='中国人民银行', root='https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html',
        host=r'www\.pbc\.gov\.cn', article=r'/goutongjiaoliu/113456/113469/(\d{8})\d{6,16}/index\.html'),
    'mof_news': dict(name='财政部', root='https://www.mof.gov.cn/zhengwuxinxi/caizhengxinwen/',
        host=r'[a-z][a-z0-9-]{0,30}\.mof\.gov\.cn', article=r'/[A-Za-z0-9/_-]+/\d{6}/t(\d{8})_\d+\.htm'),
}

MAX_BYTES = 4*1024*1024
REGISTRY = {
    'official.release_catalog': dict(version=1,kind='已登记官方发布目录',raw_only=True,fields={}),
    'official.release_original': dict(version=1,kind='目录绑定的官方原文',raw_only=False,fields={}),
}
ARTICLE = re.compile(r'/sj/(?:zxfbhjd|zxfb|sjjd)/\d{6}/t(\d{8})_\d+\.html')
FED_STATEMENT = re.compile(r'/newsevents/pressreleases/monetary(\d{8})a\.htm')
ENGLISH_MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July',
                  'August', 'September', 'October', 'November', 'December')
FED_HEADER_DATE = re.compile(r'('+'|'.join(ENGLISH_MONTHS)+r') ([0-9]{1,2}), ([0-9]{4})', re.I)
FED_RATE_NUMBER = r'(?:\d{1,3}[- ](?:1/4|1/2|3/4)|(?:1/4|1/2|3/4)|\d{1,3}(?:\.\d{1,4})?)'
FED_RATE_DECISION = re.compile(
    r'\AThe Committee decided to (?:maintain|raise|lower) the target range for the federal funds rate '
    r'(?:at |to |by (?:' + FED_RATE_NUMBER + r') percentage points? to )'
    r'(' + FED_RATE_NUMBER + r') (?:to|[–—]) (' + FED_RATE_NUMBER + r') percent(?=[,.; ]|$)', re.I)
STATISTICAL_MONTH = re.compile(r'(?<!\d)(\d{4})年(0?[1-9]|1[0-2])月(?:份)?(?!\s*\d{1,2}日)')
_FED_NAME = r'(?:美联储(?:官网)?(?:的)?(?:FOMC)?|FOMC)'
_FED_DECISION = r'(?:会议|(?:利率)?决议|(?:政策)?声明)'
_FED_DAY = r'\d{4}[年/-]\d{1,2}[月/-]\d{1,2}日?'
_FED_WHEN = rf'(?:{_FED_DAY}|最新(?:一次)?|最近(?:两次|2次|一次)?|近(?:两|2)次|本次)'
_FED_METRIC = r'(?:联邦基金(?:利率)?(?:的)?(?:目标利率区间|利率目标区间|目标区间)|目标利率区间|利率目标区间)'
_FED_DIRECT_QUESTION = re.compile(
    rf'(?:请(?:帮我)?\s*|帮我\s*)?(?:(?:仅|只)?(?:使用|用|按|根据|依据)\s*美联储(?:官网)?(?:的)?(?:FOMC)?'
    rf'(?:官方)?(?:声明|原文)(?:原文)?[，,\s]*)?'
    rf'(?:核对|查询|查一下|查|查看|列出|比较|对比)?\s*'
    rf'(?:{_FED_WHEN}\s*(?:的)?\s*{_FED_NAME}|{_FED_NAME}\s*{_FED_WHEN}(?:的)?(?:FOMC)?)\s*'
    rf'(?:{_FED_DECISION})?\s*(?:的)?\s*{_FED_METRIC}'
    rf'(?:\s*(?:和|与|及|、)\s*(?:{_FED_DAY}(?:的)?{_FED_NAME}?(?:{_FED_DECISION})?'
    rf'(?:的)?{_FED_METRIC}|(?:相对|相比|较)?(?:前一?次|上一次|上次)(?:会议|声明|决议)?(?:的)?(?:变化|变动)))?'
    rf'(?:\s*[，,]?\s*(?:并)?(?:和|与|及)?(?:比较|对比)(?:前一?次|上一次|上次)(?:会议|声明|决议)?(?:的)?(?:变化|变动))?'
    r'(?:是多少|多少|是什么|有何变化)?(?:[\s，,。；;]+(?:并)?(?:生成|给出|出)(?:一份)?(?:简短)?(?:研究)?报告)?', re.I)
_FED_ANALYSIS_BOUNDARY = re.compile(
    r'(?:[，,。；;]\s*(?:(?:并|再|然后|同时)\s*)?|(?:并|再|同时|以及|及)\s*)'
    r'(?=分析|解释|研究|评估|判断|讨论|测算|检验|对)', re.I)
_FED_RATE_EXCLUSION = re.compile(
    r'(?:不要|无需|不用|禁止|不)\s*(?:查(?:询)?|核对|比较|对比|使?用|读取|下载|引用)\s*'
    r'(?:美联储|fomc|联邦基金|目标利率|前一?次|上一次|上次)', re.I)
_FED_RESEARCH_EVENT = r'(?:议息(?:会议)?|利率(?:决议|决定|声明|会议)|货币政策(?:决议|决定|声明)|(?:宣布)?(?:加息|降息|维持利率))'
_FED_RESEARCH_DATE = rf'(?:{_FED_DAY}|最新(?:一次)?|最近(?:一次)?|本次|这次)'
_FED_DATED_EVENT = (
    re.compile(rf'(?P<when>{_FED_RESEARCH_DATE})\s*(?:的)?\s*{_FED_NAME}\s*(?:的)?\s*{_FED_RESEARCH_EVENT}', re.I),
    re.compile(rf'{_FED_NAME}\s*(?:于|在)?\s*(?P<when>{_FED_RESEARCH_DATE})\s*(?:的)?\s*{_FED_RESEARCH_EVENT}', re.I),
)


def _natural_fomc_scope(text):
    if (re.match(r'^\s*(?:请)?\s*(?:假设|假如|如果|若)', text)
            or not re.search(r'影响|传导|估值|反应|市场表现|收盘(?:表现|变化|涨跌)|怎么走|走势', text)):
        return None
    windows = set()
    for clause in re.split(r'[，,。；;！？!？\n]', text):
        if re.search(r'假设|假如|如果|下次|下一次', clause):
            continue
        for pattern in _FED_DATED_EVENT:
            windows.update(match['when'] for match in pattern.finditer(clause))
    if len(windows) != 1:
        return None
    when, = windows
    selection = _fomc_rate_selection('本次' if when == '这次' else when)
    if selection:
        selection.update(previous=True, compare=True)
    return selection


def statistical_period_anchors(task, record, parts=None):
    """Literal completed statistical months, not inferred month-end dates."""
    if (type(task.get('statistical_period_version')) is not int
            or task['statistical_period_version'] != 1 or task.get('research_mode') != 'current'
            or task.get('intent') == '原件内容核对' or not isinstance(record, dict)
            or record.get('source') != 'official' or record.get('operation') != 'release_original'):
        return []
    try:
        article_url(record.get('snapshot', {}).get('official_origin', {}).get('url'), channel='nbs_release')
        validate_original(task, record)
    except (ContractError, KeyError, TypeError):
        return []
    actual = {part['locator']: part['text'] for part in record['snapshot'].get('parts', [])}
    selected = record['snapshot'].get('parts', []) if parts is None else parts
    cutoff_month = market_date(task['cutoff'])[:7]
    result, seen = [], set()
    for part in selected:
        text = part.get('text')
        if (not isinstance(text, str) or not text or part.get('locator') not in actual
                or text not in actual[part['locator']]):
            continue
        for match in STATISTICAL_MONTH.finditer(text):
            if int(match[1]) < 1:
                continue
            value = f'{int(match[1]):04d}-{int(match[2]):02d}'
            if value >= cutoff_month:
                continue  # Current partial months need another explicit contract.
            key = (value, part['locator'], match.group())
            if key not in seen:
                result.append(dict(observation_period=dict(frequency='month', value=value),
                    part=part['locator'], quote=match.group()))
                seen.add(key)
    return result


def validate_statistical_period(task, record, item):
    period = item.get('observation_period')
    if (not isinstance(period, dict) or set(period) != {'frequency', 'value'}
            or period.get('frequency') != 'month' or 'observation_date' in item
            or item.get('period') != period.get('value')):
        raise ContractError('月度统计须保留同一所属月，不填写或推算日度观测日期')
    locator = item.get('record_locator', {})
    matches = [anchor for anchor in statistical_period_anchors(task, record)
               if anchor['observation_period'] == period and anchor['part'] == locator.get('period_part')
               and anchor['quote'] == locator.get('period_quote')]
    if len(matches) != 1:
        raise ContractError('统计所属月缺少同任务统计局原文的明确位置，未批准月度用途')
    if 'value' in item:
        text = str(item.get('text', ''))
        explicit = {f'{int(match[1]):04d}-{int(match[2]):02d}'
                    for match in STATISTICAL_MONTH.finditer(text)}
        if explicit and explicit != {period['value']}:
            raise ContractError('数字摘录包含其他或多个统计月份，须明确选择对应原值范围')
        if not explicit:
            # A bare monthly paragraph inherits its own actual article title,
            # not an unrelated historical row elsewhere in the same table.
            title = record['snapshot']['official_origin']['title']
            headings = [part['text'] for part in record['snapshot']['parts']
                        if part['locator'].startswith('标题') and title in part['text']]
            declared = {f'{int(match[1]):04d}-{int(match[2]):02d}'
                        for heading in headings for match in STATISTICAL_MONTH.finditer(heading)}
            short = {int(match[1]) for match in re.finditer(
                r'(?<![\d年])(0?[1-9]|1[0-2])月(?:份)?(?!\s*\d{1,2}日)', text)}
            if declared != {period['value']} or short and short != {int(period['value'][-2:])}:
                raise ContractError('数字所在段落月份与实际原文标题或所选月份不一致，不能借用其他表行的日期')


def fomc_statement_date_match(record, text, quote, observed):
    """A literal dated statement header, not first publication or effect time.

    Keep the generic publication grammar unchanged. Only an already-bound
    FOMC original can use this representation; last-update/implementation
    paragraphs and dates inferred from paths alone are not accepted.
    """
    if (record.get('source')!='official' or record.get('operation')!='release_original'
            or not isinstance(quote,str) or text!=quote):
        return None
    url=record.get('snapshot',{}).get('official_origin',{}).get('url')
    try:
        article_url(url,channel='fed_fomc')
    except ContractError:
        return None
    match=FED_HEADER_DATE.fullmatch(quote)
    if match is None:
        return None
    month=next(i for i,name in enumerate(ENGLISH_MONTHS,1) if name.lower()==match[1].lower())
    value=f'{int(match[3]):04d}-{month:02d}-{int(match[2]):02d}'
    raw=FED_STATEMENT.fullmatch(urlsplit(url).path)[1]
    path_date=raw[:4]+'-'+raw[4:6]+'-'+raw[6:]
    return match if value==observed==path_date else None


def fomc_rate_request(task):
    """Recognize a request to read dated FOMC target ranges, without broadening scope."""
    from .parallel_study import POLICY_GROUP, branch_question
    from .subject_scope import scope_question

    branch = branch_question(task, POLICY_GROUP)
    question = scope_question(task, branch['question'] if branch else None).lower()
    if (task.get('research_mode') != 'current'
            or not re.search(r'美联储|fomc|federal reserve', question)
            or not re.search(r'联邦基金.{0,12}利率|目标利率区间|利率目标区间|federal funds rate', question)
            or _FED_RATE_EXCLUSION.search(question)):
        return None
    return _fomc_rate_selection(question)


def _fomc_rate_selection(question):
    dates = []
    for year, month, day in re.findall(r'(?<!\d)(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})日?(?!\d)', question):
        try:
            dates.append(date(int(year), int(month), int(day)).isoformat())
        except ValueError:
            return None
    dates = tuple(sorted(set(dates)))
    if len(dates) > 2 or not dates and not re.search(r'最新|最近|近两次|近2次|本次|latest|most recent', question):
        return None
    previous = len(dates) < 2 and bool(re.search(
        r'前一?次|上一次|上次|(?:最新|最近|近)(?:两|2)次|previous|prior', question))
    return dict(dates=dates, previous=previous,
                compare=previous or len(dates) == 2 and bool(re.search(r'变化|变动|比较|对比|相差|相对|compare|change', question)))


def direct_fomc_rate_request(task):
    """Recognize only questions fully answered by the registered decision readout."""
    from .draft_sources import local_sources_only

    if (task.get('draft_first_version') != 1 or task.get('official_channels_version') != 1
            or task.get('official_policy_channels_version') != 1 or task.get('plan_version', 1) != 1
            or task.get('intent') not in ('综合研究', '比较对象') or task.get('objects')
            or task.get('materials') or task.get('pending_materials') or task.get('calculation_specs') or task.get('update_from')
            or local_sources_only(task)):
        return None
    required = [row for row in task['questions'] if row.get('required', True)]
    if len(required) != 1:
        return None
    question, = required
    if (question['question'] != task['question'] or question.get('answer_scope')
            or question.get('evidence_needs') or question.get('method_needs')
            or (question.get('kind') not in ('fact', 'comparison') and task.get('question_origin') != 'default')):
        return None
    text = re.sub(r'\s+', '', task['question']).strip('，,。；;！？!?')
    text = re.sub(r'[，,。；;](?:请)?(?:不|不要|无需)(?:下载|使用|用)?PDF$', '', text, flags=re.I)
    if not _FED_DIRECT_QUESTION.fullmatch(text):
        return None
    request = fomc_rate_request(task)
    if request and all(day <= market_date(task['cutoff']) for day in request['dates']):
        return request
    return None


def research_fomc_rate_request(task):
    """Select a stated meeting before discussing its market transmission."""
    from .draft_sources import (_NO_PDF, _unquoted_local_scope, stated_access_restricted,
                                validate_stated_source)
    from .parallel_study import POLICY_GROUP, branch_question
    from .subject_scope import scope_question

    if (task.get('draft_first_version') != 1 or task.get('official_channels_version') != 1
            or task.get('official_policy_channels_version') != 1 or task.get('research_mode') != 'current'
            or task.get('intent') == '原件内容核对' or task.get('plan_version', 1) != 1):
        return None
    branch = branch_question(task, POLICY_GROUP)
    text = _NO_PDF.sub('', _unquoted_local_scope(scope_question(task, branch['question'] if branch else None))).strip()
    if _FED_RATE_EXCLUSION.search(text) or stated_access_restricted(task, text):
        return None
    try:
        validate_stated_source(task, 'official')
    except ContractError:
        return None
    request = None
    halves = _FED_ANALYSIS_BOUNDARY.split(text, maxsplit=1)
    if len(halves) == 2:
        primary = re.sub(r'^(?:请(?:帮我)?\s*)?(?:研究|分析|评估)\s*', '', halves[0]).strip(' ，,。；;')
        implied_previous = not _FED_DIRECT_QUESTION.fullmatch(primary) and bool(re.search(r'(?:变化|变动)$', primary))
        if implied_previous:
            primary = re.sub(r'的?(?:变化|变动)$', '', primary)
        if _FED_DIRECT_QUESTION.fullmatch(primary):
            request = _fomc_rate_selection(primary.lower())
            if request and implied_previous and len(request['dates']) < 2:
                request.update(previous=True, compare=True)
    request = request or _natural_fomc_scope(text)
    if not request or any(day > market_date(task['cutoff']) for day in request['dates']):
        return None
    return request


def fomc_statement_requests(task, request):
    """Return the next registered acquisition step for the stated decision scope."""
    from .parallel_study import POLICY_GROUP, branch_question

    branch = branch_question(task, POLICY_GROUP)
    def completed(operation, parameters):
        expected = fingerprint(dict(source='official', operation=operation, parameters=parameters))
        return any(row.get('source') == 'official' and row.get('operation') == operation
                   and row.get('task_id') == task['id'] and row.get('research_version') == task['version']
                   and row.get('request_hash') == expected and row.get('status') == '成功待核验'
                   and fingerprint(row.get('snapshot')) == row.get('snapshot_hash', row.get('hash'))
                   for row in task.get('source_records', []))

    def need(ident, operation, parameters, reason):
        return dict(id=ident, source='official', operation=operation, parameters=parameters,
                    question_ids=[branch['id']] if branch else [row['id'] for row in task['questions'] if row.get('required', True)],
                    selection_reason=reason)

    choices = _fomc_choices(task)
    if not choices:
        parameters = dict(channel='fed_fomc', page='0')
        if completed('release_catalog', parameters):
            return []
        return [need('stated_fomc_catalogue', 'release_catalog', parameters,
                     '联邦基金目标利率区间以美联储会议声明为准，先核对官方日历。')]
    result = []
    for day in _fomc_dates_from_choices(task, request, choices):
        selected = [row for row in choices if row.get('path_date_hint') == day]
        if len(selected) != 1:
            continue
        choice, = selected
        parameters = {key: choice[key] for key in ('catalog_id', 'release_id')}
        if completed('release_original', parameters):
            continue
        result.append(need('stated_fomc_' + day.replace('-', ''), 'release_original', parameters,
                           f'核对{day}的目标利率区间，读取会议日历中的对应官方声明。'))
    return result


def _fomc_choices(task):
    return [row for row in release_choices(task, with_details=True, html_only=True)
            if row.get('channel') == 'fed_fomc']


def fomc_rate_dates(task, request):
    """Select explicit dates or resolve a relative date in the validated directory."""
    if request['dates'] and not request['previous']:
        return request['dates']
    return _fomc_dates_from_choices(task, request, _fomc_choices(task))


def _fomc_dates_from_choices(task, request, choices):
    if request['dates'] and not request['previous']:
        return request['dates']
    cutoff = market_date(task['cutoff'])
    by_catalogue = {}
    for choice in choices:
        if choice['path_date_hint'] <= cutoff:
            by_catalogue.setdefault(choice['catalog_id'], set()).add(choice['path_date_hint'])
    if len(by_catalogue) != 1:
        return request['dates']
    listed = sorted(next(iter(by_catalogue.values())))
    dates = request['dates'] or tuple(listed[-1:])
    if request['previous'] and dates and dates[0] in listed:
        earlier = [day for day in listed if day < dates[0]]
        dates = (*earlier[-1:], *dates)
    return dates


def fomc_rate_statement(record, parts):
    """Read a unique action sentence and literal statement date from bound parts."""
    day = record['snapshot']['official_origin'].get('path_date_hint')
    if not any(fomc_statement_date_match(record, part.get('text'), part.get('text'), day)
               for part in parts if isinstance(part, dict)):
        return None
    decisions = []
    for index, part in enumerate(parts):
        text = part.get('text') if isinstance(part, dict) else None
        match = FED_RATE_DECISION.match(text) if isinstance(text, str) else None
        if not match:
            continue
        rates = []
        for value in match.groups():
            whole, *fraction = re.split(r'[- ]', value)
            rates.append(Fraction(whole) + (Fraction(fraction[0]) if fraction else 0))
        if rates[0] > rates[1]:
            return None
        # Isolate the policy decision itself from sentences about other tools.
        period = re.search(r'\.(?=\s|$)', text[match.end():])
        end = match.end() + period.end() if period else len(text)
        decisions.append(dict(date=day, rates=tuple(rates), part_index=index, start=0, end=end))
    return decisions[0] if len(decisions) == 1 else None


def fomc_statement_release_clock(record, statement, parts):
    """Bind the statement's printed release schedule, not first availability."""
    from zoneinfo import ZoneInfo

    day = statement['date']
    preceding = parts[max(0, statement['part_index']-12):statement['part_index']]
    dated = [index for index, part in enumerate(preceding)
             if fomc_statement_date_match(record, part.get('text'), part.get('text'), day)]
    if len(dated) != 1:
        return None
    candidates = []
    for part in preceding[dated[0]+1:dated[0]+4]:
        line = part.get('text', '').strip()
        match = re.fullmatch(r'For release at (1[0-2]|[1-9]):([0-5]\d) ([ap])\.m\. (EDT|EST)', line)
        if match:
            hour = int(match[1]) % 12 + (12 if match[3] == 'p' else 0)
            declared = datetime.combine(date.fromisoformat(day), clock_time(hour, int(match[2])), ZoneInfo('America/New_York'))
            if declared.tzname() == match[4]:
                candidates.append(dict(scheduled_at=declared.isoformat(), part=part['locator'], quote=line))
    return candidates[0] if len(candidates) == 1 else None


def current_statement_temporal_contract(task, record, view, availability):
    """Expose the fixed adapter's supported use, not a publication receipt.

    This adapter retains the current statement but does not implement a
    first-publication clock verifier. Only new tasks opt into its bounded
    extraction grammar; other documents and legacy tasks are unchanged.
    """
    if (task.get('official_temporal_contract_version') != 1
            or task.get('official_policy_channels_version') != 1
            or record.get('source') != 'official' or record.get('operation') != 'release_original'
            or not isinstance(availability, dict) or availability.get('kind') != 'current_original'):
        return None
    url = record.get('snapshot', {}).get('official_origin', {}).get('url')
    try:
        article_url(url, channel='fed_fomc')
    except ContractError:
        return None
    validate_original(task, record)
    raw_date = FED_STATEMENT.fullmatch(urlsplit(url).path)[1]
    observed = raw_date[:4] + '-' + raw_date[4:6] + '-' + raw_date[6:]
    actual = {part['locator']: part['text'] for part in record['snapshot']['parts']}
    candidates = []
    for part in view.get('parts', []):
        text = part.get('text')
        if actual.get(part.get('locator')) != text:
            continue
        if fomc_statement_date_match(record, text, text, observed):
            candidate = dict(date=observed, part=part['locator'], quote=text)
            if candidate not in candidates:
                candidates.append(candidate)
    return dict(version=1, allowed_modes=['unknown_current_original'], date_candidates=candidates,
        first_publication_verified=False, source_or_financial_approval=False,
        rule='本适配仅登记当前声明用途；period是独立标题所载声明日期，不能填写为已核验首发。'
             '按date_candidates显式选择日期及定位，availability保持原样，temporal_use为current_version。'
             '没有读到标题时先读取，不从路径、Last Update或取得时间补造；历史及因果需求仍保留缺口。')


def article_url(value, *, channel='nbs_release'):
    if not isinstance(value,str): raise ContractError('官方原文地址必须为文字')
    parsed=urlsplit(value)
    from .announcement_sources import CHANNELS as ANNOUNCEMENT_CHANNELS, original_url
    if channel in ANNOUNCEMENT_CHANNELS:
        return original_url(value, channel)
    if channel in miit_releases.CHANNELS:
        return miit_releases.article_address(value, channel)
    if channel in POLICY_CHANNELS:
        profile = POLICY_CHANNELS[channel]
        if (parsed.scheme != 'https' or re.fullmatch(profile['host'], parsed.netloc) is None
                or parsed.query or parsed.fragment or re.fullmatch(profile['article'], parsed.path) is None):
            raise ContractError('原文不属于所选官方政策栏目')
        return value
    if channel not in ('nbs_release', 'fed_fomc'):
        raise ContractError('官方原文渠道未登记')
    host, pattern = (('www.stats.gov.cn', ARTICLE) if channel == 'nbs_release'
                     else ('www.federalreserve.gov', FED_STATEMENT))
    if (parsed.scheme!='https' or parsed.netloc!=host or parsed.query
            or parsed.fragment or not pattern.fullmatch(parsed.path)):
        raise ContractError('仅允许已登记官方栏目中的无凭据HTTPS原文')
    return value


def catalogue_url(page, *, channel='nbs_release', maximum_page=3):
    if channel in miit_releases.CHANNELS:
        return miit_releases.catalogue_address(channel, page)
    if channel in POLICY_CHANNELS:
        if page != '0':
            raise ContractError('该政策渠道只登记当前官方栏目，不猜分页或历史覆盖')
        return POLICY_CHANNELS[channel]['root']
    if channel == 'fed_fomc':
        if page != '0':
            raise ContractError('FOMC仅登记固定会议日历，不猜年度目录或自动翻页')
        return FED_CALENDAR_URL
    if channel != 'nbs_release':
        raise ContractError('官方目录渠道未登记')
    if (type(maximum_page) is not int or not 0<=maximum_page<999999999
            or not isinstance(page,str) or re.fullmatch(r'0|[1-9][0-9]{0,8}',page) is None
            or int(page)>maximum_page):
        raise ContractError('官方目录页码超出本任务已登记或实际声明的范围，不自动翻页')
    return ROOT_URL+('' if page=='0' else 'index_'+page+'.html')


def catalogue_original_name(parameters, request_hash):
    """Canonical HTML catalogue filename, shared with archive readback."""
    channel = parameters['channel']
    if channel == 'nbs_release':
        return 'nbs-catalogue-page-' + parameters['page'] + '.html'
    if channel == 'fed_fomc':
        return 'fed-fomc-calendar.html'
    if channel in miit_releases.CHANNELS:
        return channel + '-catalogue-' + request_hash[:12] + '.json'
    if channel in POLICY_CHANNELS:
        return channel + '-catalogue-' + request_hash[:12] + '.html'
    raise ContractError('该渠道不使用HTML目录原件')


def pagination_enabled(task):
    version=task.get('official_catalogue_pagination_version')
    if version is None:return False
    if type(version) is not int or version!=1:raise ContractError('官方目录分页版本未登记')
    return True


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__();self.active=False;self.parts=[];self.current=[]
    def handle_starttag(self,tag,attrs):
        if tag=='script':self.active=True;self.current=[]
    def handle_data(self,text):
        if self.active:self.current.append(text)
    def handle_endtag(self,tag):
        if tag=='script' and self.active:
            self.parts.append(''.join(self.current));self.active=False


_PAGINATION_CALL=re.compile(r'''(?m)^[ \t]*(?P<call>createPageHTML)\([ \t]*(?P<count>[1-9][0-9]{0,8})[ \t]*,[ \t]*(?P<page>0|[1-9][0-9]{0,8})[ \t]*,[ \t]*(['"])index\4[ \t]*,[ \t]*(['"])html\5[ \t]*\)[ \t]*;?[ \t]*$''')


def _top_level_calls(script):
    """Read literal metadata without running JavaScript or its instructions."""
    candidates={match.start('call'):match for match in _PAGINATION_CALL.finditer(script)}
    found=[];index=0;braces=0;parentheses=0
    while index<len(script):
        if script.startswith('//',index):
            end=script.find('\n',index+2);index=len(script) if end<0 else end+1;continue
        if script.startswith('/*',index):
            end=script.find('*/',index+2);index=len(script) if end<0 else end+2;continue
        char=script[index]
        if char in ('"',"'",'`'):
            quote=char;index+=1
            while index<len(script):
                if script[index]=='\\':index+=2;continue
                if script[index]==quote:index+=1;break
                index+=1
            continue
        if index in candidates and braces==parentheses==0:found.append(candidates[index])
        if char=='{':braces+=1
        elif char=='}':braces-=1
        elif char=='(':parentheses+=1
        elif char==')':parentheses-=1
        if braces<0 or parentheses<0:return []
        index+=1
    return found


@lru_cache(maxsize=4)
def _pagination_declaration(payload):
    reader=_Scripts();reader.feed(payload.decode('utf-8-sig'));reader.close()
    declarations=[(int(match['count']),int(match['page'])) for script in reader.parts
                  for match in _top_level_calls(script)]
    if len(declarations)!=1:return None
    count,page=declarations[0]
    return declarations[0] if 0<=page<count else None


_CATALOGUE_TRANSPORT = ('transport_isolated', 'network_request_started', 'network_worker_reaped')


def _catalogue_dispatch(task, record):
    if (record.get('source') != 'official' or record.get('operation') != 'release_catalog'
            or record.get('kind') != 'api' or record.get('status') != '成功待核验'
            or record.get('task_id') != task['id'] or record.get('research_version') != task['version']
            or record.get('request_hash') != fingerprint(dict(source='official', operation='release_catalog',
                parameters=record.get('parameters')))):
        return None
    return next((event for event in task.get('ledger', {}).get('dispatches', [])
                 if (event.get('kind'), event.get('source'), event.get('operation'), event.get('status'))
                 == ('data_requests', 'official', 'release_catalog', '成功')
                 and event.get('fingerprint') == record['request_hash']
                 and event.get('response_snapshot_hash') == record.get('hash')
                 and all(event.get(key) is True for key in _CATALOGUE_TRANSPORT)), None)


def catalogue_pagination(task):
    """Derive page bounds from the task's actual metered homepage response.

    Imported snapshots, failed/cancelled dispatches and changed bytes cannot
    expand the registered four bootstrap pages. No completeness is approved.
    """
    if not pagination_enabled(task):return None
    for record in reversed(task.get('source_records',[])):
        parameters=dict(channel='nbs_release',page='0')
        if (record.get('source')!='official' or record.get('operation')!='release_catalog'
                or record.get('kind')!='api' or record.get('status')!='成功待核验'
                or record.get('task_id')!=task['id'] or record.get('research_version')!=task['version']
                or record.get('parameters')!=parameters
                or fingerprint(record.get('snapshot'))!=record.get('hash')):continue
        request_hash=fingerprint(dict(source='official',operation='release_catalog',parameters=parameters))
        if record.get('request_hash')!=request_hash:continue
        if not _catalogue_dispatch(task, record):continue
        try:
            raw=base64.b64decode(record['snapshot']['original_base64'],validate=True)
            if len(raw)>MAX_BYTES or parse_catalogue(raw,task['cutoff'])!=record['snapshot']:continue
            declared=_pagination_declaration(raw)
        except (ContractError,ValueError,KeyError,UnicodeError):continue
        if declared is None or declared[1]!=0:continue
        return dict(version=1,root_artifact_id=record['id'],root_snapshot_hash=record['hash'],
            root_request_hash=request_hash,page_count=declared[0],minimum_page=0,maximum_page=declared[0]-1,
            automatic_traversal=False,completeness_verified=False,financial_approval=False)
    return None


def freeze_catalogue_root_dispatch(task):
    """Retain the verified homepage receipt needed to re-open a later page."""
    if not (pagination := catalogue_pagination(task)):
        return None
    home = next(row for row in task['source_records'] if row['id'] == pagination['root_artifact_id'])
    event = _catalogue_dispatch(task, home)
    fields = ('kind', 'source', 'operation', 'status', 'fingerprint', 'response_snapshot_hash', *_CATALOGUE_TRANSPORT)
    return {key: event[key] for key in fields} if event else None


def _catalogue_maximum(task):
    declared=catalogue_pagination(task)
    return declared['maximum_page'] if declared is not None else 3


def _is_catalogue_address(value):
    """Transport syntax only; task authority is checked before dispatch."""
    if not isinstance(value,str):return False
    address=urlsplit(value)
    if (address.scheme!='https' or address.netloc!='www.stats.gov.cn' or address.query or address.fragment):return False
    match=re.fullmatch(r'/sj/zxfbhjd/(?:index_([1-9][0-9]{0,8})\.html)?',address.path)
    return match is not None


class ReleaseLinks(HTMLParser):
    def __init__(self, channel='nbs_release'):
        super().__init__(convert_charrefs=True)
        self.channel=channel
        self.root = catalogue_url('0', channel=channel)
        self.href=None;self.text=[];self.links=[];self.skipped=0
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            self.href=dict(attrs).get('href');self.text=[]
    def handle_data(self,value):
        if self.href is not None:self.text.append(value)
    def handle_endtag(self,tag):
        if tag!='a' or self.href is None:return
        title=' '.join(''.join(self.text).split())
        try:
            value = urljoin(self.root, self.href)
            # The MOF directory itself retains HTTP links to its bureaus. Only
            # upgrade that literal official link; never downgrade failed TLS.
            if self.channel == 'mof_news' and value.startswith('http://'):
                value = 'https://'+value[len('http://'):]
            url=article_url(value, channel=self.channel)
            if self.channel == 'fed_fomc':
                raw=FED_STATEMENT.fullmatch(urlsplit(url).path)[1]
                title='FOMC声明 '+raw[:4]+'-'+raw[4:6]+'-'+raw[6:]
            if title and len(title)<=300:self.links.append((url,title))
        except ContractError:self.skipped+=1
        self.href=None;self.text=[]


def _release_links(payload, channel):
    if channel in miit_releases.CHANNELS:
        return miit_releases.dated_links(payload, channel), 0
    reader = ReleaseLinks(channel)
    reader.feed(payload.decode('utf-8-sig'))
    reader.close()
    return tuple(reader.links), reader.skipped


@lru_cache(maxsize=4)
def _cached_release_links(payload, channel):
    """Cache immutable syntax only, never task scope or source approval."""
    return _release_links(payload, channel)


def parse_catalogue(payload, cutoff, *, channel='nbs_release', parameters=None, issuer_payload=None):
    from .announcement_sources import CHANNELS as ANNOUNCEMENT_CHANNELS, parse_catalogue as parse_announcements
    if channel in ANNOUNCEMENT_CHANNELS:
        return parse_announcements(payload, issuer_payload, parameters, cutoff)
    catalogue_url('0', channel=channel)  # Validate the channel independently.
    # Repeated provenance checks often inspect identical retained bytes. Every
    # check still validates channel, cutoff, snapshot and task independently.
    # Bound memory by both entry count and the existing transport byte cap.
    links, skipped = (_cached_release_links(payload, channel) if len(payload) <= MAX_BYTES
                      else _release_links(payload, channel))
    rows=[];seen=set();skipped_future=0
    miit = channel in miit_releases.CHANNELS
    for url, title, *listed_dates in links:
        if miit:
            day, = listed_dates
            limitation = '目录日期供定位，正文标示日期须另行核对。'
        else:
            pattern = (re.compile(POLICY_CHANNELS[channel]['article']) if channel in POLICY_CHANNELS
                       else ARTICLE if channel == 'nbs_release' else FED_STATEMENT)
            raw = pattern.fullmatch(urlsplit(url).path)[1]
            day = raw[:4] + '-' + raw[4:6] + '-' + raw[6:]
            limitation = '路径日期仅供定位，不证明首发时刻或历史版本。'
        try:timepoint(day)
        except ContractError:continue
        if day>market_date(cutoff):skipped_future+=1;continue
        if url in seen:continue
        seen.add(url)
        if len(rows)>=100:break
        prefix = 'nbs_' if channel == 'nbs_release' else channel + '_'
        rows.append(dict(id=prefix+hashlib.sha256(url.encode()).hexdigest()[:24],
            title=title,url=url,path_date_hint=day,published_at=None,limitation=limitation))
    if not links:raise ContractError('官方目录未识别到已登记原文链接，不把结构变化当作空目录')
    return dict(entries=rows,coverage=dict(completeness='未证实',maximum_entries=100,
        skipped_future=skipped_future,skipped_links=skipped),
        original_base64=base64.b64encode(payload).decode(),original_sha256=hashlib.sha256(payload).hexdigest())


def verified_catalogue(task, record):
    """Read the exact parsed directory associated with this task and version."""
    if ((record.get('source'), record.get('operation')) != ('official', 'release_catalog')
            or record.get('task_id') != task['id'] or record.get('research_version') != task['version']
            or record.get('status') != '成功待核验' or fingerprint(record.get('snapshot')) != record.get('hash')):
        return None
    parameters, snapshot = record.get('parameters'), record.get('snapshot')
    try:
        validate_request(task, 'release_catalog', parameters)
        if record.get('request_hash') != fingerprint(dict(source='official', operation='release_catalog',
                                                           parameters=parameters)):
            return None
        payload = base64.b64decode(snapshot['original_base64'], validate=True)
        issuer = (base64.b64decode(snapshot['issuer_original_base64'], validate=True)
                  if 'issuer_original_base64' in snapshot else None)
        parsed = parse_catalogue(payload, task['cutoff'], channel=parameters['channel'],
                                 parameters=parameters, issuer_payload=issuer)
    except (ContractError, ValueError, KeyError, TypeError):
        return None
    return snapshot if parsed == snapshot else None


def release_choices(task, *, with_details=False, html_only=False):
    choices=[]
    for record in task.get('source_records',[]):
        snapshot = verified_catalogue(task, record)
        if snapshot is None:
            continue
        channel = record['parameters']['channel']
        for row in snapshot['entries']:
            if html_only and row.get('format') == 'pdf':
                continue
            choice = dict(catalog_id=record['id'], release_id=row['id'], title=row['title'])
            if with_details:
                choice['channel'] = channel
                if row.get('code'):
                    choice.update(issuer_code=row['code'], issuer_name=row['issuer']['name'],
                                  listed_date=row['listed_date'], listed_at=row['listed_at'])
                else:
                    choice['path_date_hint'] = row['path_date_hint']
            choices.append(choice)
    return choices if pagination_enabled(task) else choices[:100]


def selection_references(task, *, with_details=False, html_only=False):
    """One stable reference for a validated catalogue-entry pair, not a URL.

    The original byte validation remains in release_choices. Task identity,
    declared scope and catalogue content bind each reference; adding another
    page never renumbers an existing selection or grants financial approval.
    """
    if (type(task.get('official_selection_reference_version')) is not int
            or task['official_selection_reference_version'] != 1):
        return []
    records = {record['id']: record for record in task.get('source_records', [])}
    result = []
    for choice in release_choices(task, with_details=with_details, html_only=html_only):
        # Plan questions and request lists can be refined after this selection.
        # Bind the immutable user scope, not its derived plan's input_hash:
        # adopting a reference request must not invalidate that same reference.
        binding = dict(task_id=task['id'], research_version=task['version'],
            scope_hash=fingerprint({key: task[key] for key in ('question', 'entry', 'intent', 'objects', 'cutoff')}),
            catalog_id=choice['catalog_id'], release_id=choice['release_id'],
            catalog_hash=records[choice['catalog_id']]['hash'])
        result.append(dict(choice, selection_ref='rh_release_' + fingerprint(binding)[:24]))
    if len({item['selection_ref'] for item in result}) != len(result):
        raise ContractError('官方原文引用不唯一，未选择任何条目')
    return result


def _release_parameters(task, parameters):
    if isinstance(parameters, dict) and set(parameters) == {'selection_ref'}:
        matches = [item for item in selection_references(task)
                   if item['selection_ref'] == parameters['selection_ref']]
        if len(matches) != 1:
            raise ContractError('官方原文引用不属于本任务当前目录，请读取当前可选条目')
        return {key: matches[0][key] for key in ('catalog_id', 'release_id')}
    return parameters


def canonical_request_parameters(task, operation, parameters):
    """Resolve selection syntax before cache, ledger or provider dispatch.

    Saved acquisition records always retain the existing exact catalogue and
    release IDs. A reference does not replace their provenance contract.
    """
    canonical = _release_parameters(task, parameters) if operation == 'release_original' else parameters
    if (operation=='release_catalog' and pagination_enabled(task) and isinstance(canonical,dict)
            and 'page' in canonical and type(canonical['page']) is int):
        canonical=dict(canonical,page=str(canonical['page']))
    validate_request(task, operation, canonical)
    return canonical


def validate_request(task,operation,parameters):
    if task.get('official_channels_version')!=1:raise ContractError('本任务未登记官方发布渠道')
    if operation=='release_catalog':
        from .announcement_sources import CHANNELS as ANNOUNCEMENT_CHANNELS, validate_parameters
        if isinstance(parameters, dict) and parameters.get('channel') in ANNOUNCEMENT_CHANNELS:
            if task.get('official_source_expansion_version') != 1:
                raise ContractError('本任务未登记独立公告渠道')
            validate_parameters(parameters, task['cutoff'])
            if 'name' in parameters:
                from .stock_quality import catalogue_request, study_scope

                scope = study_scope(task)
                if (not scope or not scope.get('filing_allowed', True)
                        or catalogue_request(task, scope)['parameters'] != parameters):
                    raise ContractError('公告名称、报告期间或目录范围超出当前研究')
            else:
                from .evidence import available_security_codes

                if parameters['code'] not in available_security_codes(task):
                    raise ContractError('公告标的不属于本任务已声明或实际查得的证券代码')
            return dict(channel=parameters['channel'], parameters=dict(parameters))
        if not isinstance(parameters,dict) or set(parameters)!={'channel','page'}:
            raise ContractError('官方目录须明确固定栏目及页码')
        if parameters['channel'] in POLICY_CHANNELS and task.get('official_source_expansion_version') != 1:
            raise ContractError('本任务未登记国内政策官方渠道')
        if parameters['channel'] in miit_releases.CHANNELS and task.get(miit_releases.POLICY) != 1:
            raise ContractError('本任务未登记工信部统计渠道')
        if (parameters['channel']=='fed_fomc' and task.get('official_policy_channels_version')!=1):
            raise ContractError('本任务未登记FOMC固定官方渠道')
        maximum=_catalogue_maximum(task) if pagination_enabled(task) else 3
        return catalogue_url(parameters['page'],channel=parameters['channel'],maximum_page=maximum)
    if operation!='release_original':raise ContractError('官方发布操作未登记')
    parameters = _release_parameters(task, parameters)
    if not isinstance(parameters,dict) or set(parameters)!={'catalog_id','release_id'}:
        raise ContractError('官方原文须选择已留存目录，不接受任意URL')
    matches=[item for item in release_choices(task) if all(item[key]==parameters[key] for key in parameters)]
    if len(matches)!=1:raise ContractError('官方原文选择不属于本任务当前版本目录')
    record=next(record for record in task['source_records'] if record['id']==parameters['catalog_id'])
    return next(row for row in record['snapshot']['entries'] if row['id']==parameters['release_id'])


def request_shapes(task, *, html_only=False):
    if task.get('official_channels_version')!=1:return []
    common=dict(source='official',credential_required=False,
        selection_rule='iFinD仍为优先来源；选择官方原文须说明问题需要，不因权限失败静默换源。')
    result = [dict(common,qualified_id_for_lookup_only='official.release_catalog',
        operation='release_catalog',parameter_keys=['channel','page'],
        parameter_options=dict(channel=['nbs_release'],page=['0','1','2','3']),raw_only=True,
        limitation='每次只取指定一页，不自动翻页，目录不证明覆盖完整，也不是正文证据。'),
        dict(common,qualified_id_for_lookup_only='official.release_original',
             operation='release_original',parameter_keys=['catalog_id','release_id'],
             available_selections=release_choices(task, html_only=html_only),raw_only=False,
             limitation='保留原始HTML字节及静态摘录；下载不等于来源、时点或适用性核验。')]
    if task.get('official_policy_channels_version')==1:
        catalogue=result[0]
        catalogue.pop('parameter_options')
        catalogue['available_parameter_sets']=[dict(channel='nbs_release',page=str(page)) for page in range(4)] + [
            dict(channel='fed_fomc',page='0')]
        catalogue['channel_rule']='nbs_release为统计局目录；fed_fomc只取固定会议日历中的声明，不含会议纪要、实施说明或其他栏目，不证明目录完整或首发时刻。'
    if pagination_enabled(task):
        catalogue=result[0];declared=catalogue_pagination(task)
        catalogue.pop('parameter_options',None);catalogue.pop('available_parameter_sets',None)
        catalogue['page_number_profiles']=[dict(channel='nbs_release',minimum=0,
            maximum=declared['maximum_page'] if declared else 3)]
        if task.get('official_policy_channels_version')==1:
            catalogue['page_number_profiles'].append(dict(channel='fed_fomc',minimum=0,maximum=0))
        catalogue['pagination_proof']=declared
        catalogue['model_page_encoding']=dict(version=1,rule='模型按登记范围选择整数page或对应的十进制页码文字；内核只将整数序列化为实际请求文字。'
            '既有请求记录保留实际文字参数，不重复查询；每次仅下载所选一页，不自动遍历。'
            '页码不代表固定日期区间；按实际条目日期与问题选择，不把目录声明页数视为完整性核验。')
    if (type(task.get('official_selection_reference_version')) is int
            and task['official_selection_reference_version'] == 1):
        choices = selection_references(task, with_details=True, html_only=html_only)
        details = [dict({key: value for key, value in item.items()
                         if key not in ('catalog_id', 'release_id', 'selection_ref')},
                        parameters={'selection_ref': item['selection_ref']}) for item in choices]
        original = result[1]
        original.update(parameter_keys=['selection_ref'],
            available_parameter_sets=[{'selection_ref': item['selection_ref']} for item in choices],
            available_selections=[{key: item[key] for key in ('selection_ref', 'title')} for item in choices],
            selection_details=details, selection_reference_version=1,
            selection_rule='先按selection_details核对渠道、发行人和日期，再复制一个selection_ref。内核将其解析为同一实际目录的条目，不拼接catalog_id、release_id或URL。'
                           '尚无条目时先取所需目录，不猜引用；引用不批准最新性、数值或首发时间。')
    if task.get('official_source_expansion_version') == 1:
        _expanded_request_shapes(task, result)
    if html_only:
        result[1]['limitation'] = '本次只提供官方HTML原文条目。保留已选原文的字节和摘录，首发时间单独核验。'
    return result


def _expanded_request_shapes(task, result):
    from .announcement_sources import CHANNELS
    from .evidence import available_security_codes
    from .output_schema import _decimal_page_pattern
    def shape(properties):
        return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)
    profiles = result[0].get('page_number_profiles', [dict(channel='nbs_release', minimum=0, maximum=3)])
    if task.get('official_policy_channels_version') == 1 and not any(p['channel']=='fed_fomc' for p in profiles):
        profiles = profiles+[dict(channel='fed_fomc', minimum=0, maximum=0)]
    profiles = profiles+[dict(channel=channel, minimum=0, maximum=0) for channel in POLICY_CHANNELS]
    if task.get(miit_releases.POLICY) == 1:
        profiles += [dict(channel=channel, minimum=0, maximum=miit_releases.MAX_PAGE)
                     for channel in miit_releases.CHANNELS]
    alternatives = [shape(dict(channel=dict(type='string', enum=[p['channel']]),
        page=dict(anyOf=[dict(type='integer', minimum=p['minimum'], maximum=p['maximum']),
            dict(type='string', pattern=_decimal_page_pattern(p['maximum']))]))) for p in profiles]
    available = available_security_codes(task)
    for channel, profile in CHANNELS.items():
        codes = [code for code in available if re.fullmatch(profile['code_pattern'], code)]
        if not codes: continue
        alternatives.append(shape(dict(channel=dict(type='string', enum=[channel]),
            code=dict(type='string', enum=codes), start_date=dict(type='string', pattern=r'^\d{4}-\d{2}-\d{2}$'),
            end_date=dict(type='string', pattern=r'^\d{4}-\d{2}-\d{2}$'),
            page=dict(anyOf=[dict(type='integer', minimum=1, maximum=10 if channel=='hkex' else 9999),
                dict(type='string', pattern=r'^(?:[1-9]|10)$' if channel=='hkex' else r'^[1-9]\d{0,3}$')]),
            keyword=dict(type='string', maxLength=100))))
    from .stock_quality import catalogue_request, study_scope

    if (scope := study_scope(task)) and scope.get('filing_allowed', True):
        named = catalogue_request(task, scope)['parameters']
        alternatives.append(shape({key: dict(type='string', enum=[value]) for key, value in named.items()}))
    result[0]['metadata_parameter_schema'] = dict(anyOf=alternatives)
    result[0]['channel_rule'] = ('数值优先iFinD。nbs_release为统计局、fed_fomc为FOMC声明，pbc_news为央行、mof_news为财政部当前栏目。'
        'cninfo查询沪深A股公告，cninfo_fund查询深市ETF定期报告，sse_fund查询沪市ETF定期报告，hkex查询香港证券公告。'
        '需给定起止日期、从1开始的page及keyword，空关键词查所选期间全部标题。'
        '每次只查所选页，内核先取得官方发行人映射，实际每次HTTP发送分别记账；未知标题、目录或证券标识不能充当正文。')
    if task.get(miit_releases.POLICY) == 1:
        result[0]['channel_rule'] += 'miit_electronics为工信部电子信息、miit_vehicles为汽车工业，按登记页码读取实际目录。'
    result[1]['limitation'] = '只下载所选条目并复用同版原件；政策保留HTML，公告确需核对原件时读取PDF；不批量下载，不证明首发时刻。'


def validate_original(task,record):
    selected=validate_request(task,'release_original',record.get('parameters'))
    if (record.get('task_id')!=task['id'] or record.get('research_version')!=task['version']
            or record.get('source')!='official' or record.get('operation')!='release_original'
            or record.get('request_hash')!=fingerprint(dict(source='official',operation='release_original',
                                                           parameters=record.get('parameters')))
            or fingerprint(record.get('snapshot'))!=record.get('snapshot_hash')
            or record.get('snapshot',{}).get('official_origin')!=selected):
        raise ContractError('官方原件的来源、主体或目录绑定已变化')


def download_html(value,timeout):
    """One pinned public HTTPS GET, with a shared read deadline and byte cap."""
    registered_policy = any(value == profile['root'] for profile in POLICY_CHANNELS.values())
    miit_directory = miit_releases.is_catalogue_address(value)
    if not (_is_catalogue_address(value) or value == FED_CALENDAR_URL or registered_policy or miit_directory):
        parsed = urlsplit(value)
        if parsed.netloc == miit_releases.HOST:
            channel = next((key for key, profile in miit_releases.CHANNELS.items()
                            if parsed.path.startswith(profile['path'] + '/')), '')
        else:
            host = parsed.netloc
            channel = ('fed_fomc' if host == 'www.federalreserve.gov' else 'pbc_news' if host == 'www.pbc.gov.cn'
                       else 'mof_news' if re.fullmatch(POLICY_CHANNELS['mof_news']['host'], host) else 'nbs_release')
        article_url(value, channel=channel)
    from .evidence import _PublicFilingConnection
    parsed=urlsplit(value);deadline=time.monotonic()+timeout
    connection=_PublicFilingConnection(parsed.hostname,timeout=timeout,use_local_proxy=True)
    try:
        target = parsed.path + ('?' + parsed.query if miit_directory else '')
        media_type = 'application/json' if miit_directory else 'text/html'
        headers = {'Accept': media_type, 'Accept-Encoding': 'identity'}
        if parsed.netloc == miit_releases.HOST:
            headers['User-Agent'] = 'ResearchHelper/0.1'
        connection.request('GET', target, headers=headers)
        response=connection.getresponse()
        if response.status!=200:
            raise urllib.error.HTTPError(value,response.status,'官方站点未返回成功原文',response.headers,None)
        expected_types = ('application/json',) if miit_directory else ('text/html', 'application/xhtml+xml')
        if response.getheader('Content-Type', '').split(';')[0].strip().lower() not in expected_types:
            raise ContractError('官方响应类型与登记渠道不符')
        declared=response.getheader('Content-Length')
        if declared is not None and (not declared.isdigit() or int(declared)>MAX_BYTES):
            raise ContractError('官方原文长度异常或超出4MB')
        chunks=[];size=0
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ContractError('官方原文读取超过本次时限')
            if connection.sock is not None:connection.sock.settimeout(remaining)
            data=response.read1(min(65536,MAX_BYTES+1-size))
            if not data:break
            size+=len(data)
            if size>MAX_BYTES:raise ContractError('官方原文超出4MB')
            chunks.append(data)
        if declared is not None and size!=int(declared):raise ContractError('官方原文未完整接收')
        if time.monotonic()>=deadline:raise ContractError('官方原文读取超过本次时限')
        payload=b''.join(chunks);payload.decode('utf-8-sig')
        return payload
    finally:connection.close()
