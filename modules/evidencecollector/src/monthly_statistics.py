"""Date-bound National Bureau of Statistics bulletins."""

import base64
from calendar import monthrange
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from html import unescape
import re

from .contracts import ContractError, market_date, timepoint
from .html_material import extract_html

POLICY = 'official_monthly_statistics_version'
VERSION = 2
RETAIL_STUDY = 'retail_demand'
PMI_RELEASE = 'dated_manufacturing_pmi'
PMI_FIELDS = ('manufacturing_pmi', 'pmi_production', 'pmi_new_orders')
CELL_KIND = 'nbs_monthly_bulletin_cell'
CHANNEL = 'nbs_release'
MAX_CATALOGUE_PAGE = 8
BASIS = '国家统计局当月同比'

PRICE = re.compile(r'国家统计局城市司[^，。]{0,32}解读(?P<year>20\d{2})年(?P<month>1[0-2]|[1-9])月份?CPI和PPI数据')
RETAIL = re.compile(r'(?P<year>20\d{2})年(?:1[—–－-])?(?P<month>1[0-2]|[1-9])月份?社会消费品零售总额(?:同比)?(?:增长|下降|持平).*')
PMI = re.compile(r'(?P<year>20\d{2})年(?P<month>1[0-2]|[1-9])月中国采购经理指数运行情况')
PMI_EVENT = re.compile(
    r'(?P<year>20\d{2})[年./-](?P<month>\d{1,2})[月./-](?P<day>\d{1,2})日?\s*(?:的)?\s*'
    r'国家统计局(?:官网)?(?:发布|公布)(?:的)?\s*(?P<period>\d{1,2})月(?:份)?'
    r'制造业(?:PMI|采购经理指数)', re.I)
PERIOD_READING = r'([+-]?\d+(?:\.\d+)?)'
CHANGE = (r'(?:由上月(?:上涨|增长|下降)\d+(?:\.\d+)?%转为|由上月持平转为)?'
          r'(?:(上涨|增长|下降|涨幅(?:回升|扩大|回落)至|降幅(?:收窄|扩大)至)' + PERIOD_READING + '%|(持平))')
FIELDS = {
    'cpi': ('CPI同比', '%'), 'core': ('核心CPI同比', '%'), 'ppi': ('PPI同比', '%'),
    'retail': ('社零名义同比', '%'), 'ex_auto': ('除汽车外社零名义同比', '%'),
    'goods': ('商品零售同比', '%'), 'catering': ('餐饮收入同比', '%'),
    'retail_amount': ('社会消费品零售总额', '亿元'), 'ex_auto_amount': ('除汽车外零售额', '亿元'),
    'goods_amount': ('商品零售额', '亿元'), 'catering_amount': ('餐饮收入', '亿元'),
    'ppi_up': ('涨价行业对PPI同比的拉动', '百分点'), 'ppi_down': ('降价行业对PPI同比的拖累', '百分点'),
    'manufacturing_pmi': ('制造业PMI', '%'), 'pmi_production': ('制造业生产指数', '%'),
    'pmi_new_orders': ('制造业新订单指数', '%'),
    'manufacturing_pmi_change': ('制造业PMI较上月', '百分点'),
    'pmi_production_change': ('生产指数较上月', '百分点'),
    'pmi_new_orders_change': ('新订单指数较上月', '百分点'),
}


def _retail_scope(task, text, question_id):
    """Resolve a literal range or the latest published single-month retail sample."""
    from .draft_sources import _unquoted_local_scope
    from .economic_sources import _stated_period_windows

    text = _unquoted_local_scope(text)
    if re.match(r'^\s*(?:请\s*)?(?:假设|假如|如果|假定|引用)', text):
        return None
    topic = re.search(r'国内(?:居民)?消费|(?:全国|中国|我国)消费|居民消费|社零|社会消费品零售|(?:最近|近期|当前)消费', text)
    effect = re.search(r'回暖|复苏|改善|转弱|放缓|拖累|支撑|变化|怎么样|如何|分化|现状|趋势|修复', text)
    if (task.get(POLICY) != VERSION or not topic or not effect
            or re.search(r'CPI|PPI|通胀|物价|实际消费|实际社零|行业配置|线上|旅游|汽车销量|只看|仅看', text, re.I)):
        return None
    cutoff = market_date(task['cutoff'])
    windows = _stated_period_windows(text.replace('今年', cutoff[:4]+'年'), cadence='month')
    if windows:
        if len(windows) != 1:
            return None
        (start, end), = windows
        months = (end.year-start.year)*12+end.month-start.month+1
        if (start.day != 1 or end.day != monthrange(end.year, end.month)[1] or not 2 <= months <= 4
                or start.year != end.year or start.month < 3 or end.strftime('%Y-%m') >= cutoff[:7]):
            return None
        periods = [f'{start.year:04d}-{month:02d}' for month in range(start.month, end.month+1)]
        return dict(study=RETAIL_STUDY, question_id=question_id, periods=periods, count=months)
    requested = re.search(r'(?:最近|近|过去)\s*([0-9]+|[一二两三四五六七八九十])\s*个?月', text)
    relative = text[:requested.start()] + text[requested.end():] if requested else text
    if (re.search(r'[0-9]{4}年|[0-9]{1,2}月|今年以来|本周|今天|本月截至|半(?:年|个月)|季度|(?:近|过去|最近)[^，。！？]{0,4}[周日天年]', relative)
            or not (requested or re.search(r'最近|近来|近期|当前|现在|眼下|这几个月|最新', text))):
        return None
    counts = {'2':2, '二':2, '两':2, '3':3, '三':3, '4':4, '四':4}
    if requested and requested[1] not in counts:
        return None
    count = counts[requested[1]] if requested else 3
    from .official_sources import release_choices

    latest = max((identity[1] for row in release_choices(task, with_details=True, html_only=True)
                  if row['channel'] == CHANNEL and (identity := release_identity(row['title']))
                  and row.get('path_date_hint', '9999-12-31') <= cutoff
                  and identity[0] == 'retail' and identity[1][-2:] >= '03' and identity[1] < cutoff[:7]),
                 default='')
    periods = []
    if latest:
        year, month = map(int, latest.split('-'))
        while len(periods) < count:
            if month >= 3:
                periods.append(f'{year:04d}-{month:02d}')
            year, month = (year-1, 12) if month == 1 else (year, month-1)
    return dict(study=RETAIL_STUDY, question_id=question_id, periods=list(reversed(periods)), count=count)


def study_scope(task):
    """Identify a dated domestic-demand hypothesis with the required readings."""
    from .draft_sources import historical_original_scope, local_scope_text, validate_stated_source
    from .economic_sources import _stated_period_windows
    from .draft_economic_scope import _series_name
    from .evidence import declared_security_codes

    if (task.get(POLICY) not in (1, VERSION) or task.get('draft_first_version') != 1
            or task.get('research_mode') != 'current' or task.get('entry') not in ('宏观', '混合')
            or task.get('intent') not in ('综合研究', '解释变化', '验证观点')
            or task.get('materials') or task.get('pending_materials') or task.get('calculation_specs')
            or historical_original_scope(task) or declared_security_codes(task)):
        return None
    questions = [row for row in task['questions'] if row['required']]
    if len(questions) != 1 or questions[0]['question'] != task['question']:
        return None
    question = questions[0]
    if any(question.get(key) for key in ('answer_scope', 'evidence_needs', 'method_needs', 'dependencies')):
        return None
    text = local_scope_text(task['question'])
    hypothesis = r'(?:内需|国内需求|消费需求|居民消费)(?!价格)[^。，；！？]{0,10}(?:改善|好转|回暖|复苏|增强|回升|转强)'
    if (_series_name(text).regions - {'中国'}
            or re.search(r'海外|境外|汇率|国债|收益率|股票|基金|估值|利润|交易|预测|回测|ETF|环比|实际社零|'
                         r'用iFinD|排除|不看|不查|不取|不要(?:考虑|用|看)|不用|仅看|只看|仅比较|只问|仅核对', text, re.I)):
        return None
    try:
        validate_stated_source(task, 'official')
    except ContractError:
        return None
    if retail := _retail_scope(task, text, question['id']):
        return retail
    if (not re.search(hypothesis, text) or not re.search(r'验证|检验|能否|是否|意味着|说明|支撑|支持', text)
            or not re.search(r'物价|通胀|CPI|PPI|价格', text, re.I)):
        return None
    explicit = text.replace('今年', market_date(task['cutoff'])[:4] + '年')
    windows = _stated_period_windows(explicit, cadence='month')
    if len(windows) != 1:
        return None
    (start, end), = windows
    months = (end.year - start.year) * 12 + end.month - start.month + 1
    if (start.day != 1 or end.day != monthrange(end.year, end.month)[1] or not 2 <= months <= 4
            or start.year != end.year or start.month < 3 or end.strftime('%Y-%m') >= market_date(task['cutoff'])[:7]):
        return None
    periods = [f'{start.year:04d}-{month:02d}' for month in range(start.month, end.month + 1)]
    return dict(question_id=question['id'], periods=periods,
                explicit_public_source=bool(re.search(r'(?:按|用|依据|根据|只查)\s*(?:国家)?统计局|公开数据|官方数据', text)))


def release_identity(title):
    """Resolve the observation period from the actual publisher's title."""
    for kind, pattern in (('price', PRICE), ('retail', RETAIL), (PMI_RELEASE, PMI)):
        if match := pattern.fullmatch(title):
            return kind, f'{match["year"]}-{int(match["month"]):02d}'
    return None


def dated_pmi_scope(task):
    """Select an explicit release day and the observation month independently."""
    from .draft_sources import _unquoted_local_scope, validate_stated_source

    if (task.get(POLICY) != VERSION or task.get('research_mode') != 'current'
            or task.get('intent') == '原件内容核对'):
        return None
    text = _unquoted_local_scope(task['question'])
    questions = [row for row in task['questions'] if row.get('required', True) and row['question'] == task['question']]
    if (len(questions) != 1 or re.match(r'^\s*(?:请\s*)?(?:假设|假如|如果|假定)', text)
            or re.search(r'(?:不要|不用|无需|不)\s*(?:查|取|引用|使用|下载)\s*(?:国家统计局|官方(?:原文|资料))', text)):
        return None
    matches = list(PMI_EVENT.finditer(text))
    if len(matches) != 1:
        return None
    match, = matches
    try:
        validate_stated_source(task, 'official')
        year, month, day, period = (int(match[key]) for key in ('year', 'month', 'day', 'period'))
        released = datetime(year, month, day).date()
    except (ContractError, ValueError):
        return None
    period_year = year - (period > month)
    if (not 1 <= period <= 12 or released.isoformat() > market_date(task['cutoff'])
            or (year-period_year)*12+month-period > 1):
        return None
    return dict(study=PMI_RELEASE, question_id=questions[0]['id'], release_day=released.isoformat(),
                periods=[f'{period_year:04d}-{period:02d}'])


def catalogue_request(page, scope):
    reason = {RETAIL_STUDY: '定位已公布的对应月份社零公报。',
              PMI_RELEASE: '按披露日和统计月份定位制造业PMI发布原文。'}.get(
                  scope.get('study'), '查找对应统计月份的国家统计局价格解读和零售公报。')
    return dict(id=f'nbs_monthly_page_{page}', source='official', operation='release_catalog',
                parameters={'channel': CHANNEL, 'page': str(page)}, question_ids=[scope['question_id']], selection_reason=reason)


def _meta(parts, name):
    values = []
    for part in parts:
        if part.get('metadata_field', '').casefold() != name:
            continue
        match = re.search(r'''\bcontent\s*=\s*(["'])(.*?)\1''', part['text'], re.I)
        if match:
            values.append(unescape(match[2]).strip())
    return values[0] if len(values) == 1 else ''


def _verified_body(task, record, scope):
    from .official_sources import article_url, validate_original

    try:
        validate_original(task, record)
        snapshot = record['snapshot']
        origin = snapshot['official_origin']
        article_url(origin['url'], channel=CHANNEL)
        identity = release_identity(origin['title'])
        if identity is None or identity[1] not in scope['periods'] or snapshot['extract_version'] != 'html-2':
            return None
        payload = base64.b64decode(snapshot['original_base64'], validate=True)
        if sha256(payload).hexdigest() != snapshot['hash']:
            return None
        parts = extract_html(payload, extract_version='html-2')
        if parts != snapshot['parts'] or _meta(parts, 'articletitle') != origin['title']:
            return None
        clock = _meta(parts, 'pubdate').replace('/', '-')
        published = datetime.strptime(clock, '%Y-%m-%d %H:%M')
        period_valid = (scope.get('study') == PMI_RELEASE and published.date().isoformat() == scope.get('release_day')
                        if identity[0] == PMI_RELEASE else clock[:7] > identity[1])
        if (published.date().isoformat() != origin['path_date_hint'] or not period_valid
                or timepoint(published.isoformat() + '+08:00') > timepoint(task['cutoff'])):
            return None
        return (*identity, parts)
    except (ContractError, KeyError, TypeError, ValueError, UnicodeError):
        return None


def _match(parts, pattern, *, paragraph_prefix=None):
    """Accept identical desktop/mobile body copies, reject different readings."""
    copies = {}
    for index, part in enumerate(parts):
        if not part['locator'].startswith('段落') or (paragraph_prefix and not part['text'].startswith(paragraph_prefix)):
            continue
        for match in re.finditer(pattern, part['text']):
            copies.setdefault(match[0], (index, part, match))
    return next(iter(copies.values())) if len(copies) == 1 else None


def _cell(hit, group, field, *, direction=None, industry_count=None):
    index, part, match = hit
    label, unit = FIELDS[field]
    value = match[group].replace(',', '') if match[group] is not None else '0'  # 持平 explicitly denotes zero.
    if direction and (match[direction] or '').startswith(('下降', '降幅')):
        value = '-' + value
    if field == 'ppi_down':
        value = '-' + value
    locator = dict(kind=CELL_KIND, part_index=index, part=part['locator'], start=match.start(),
                   end=match.end(), quote=match[0], field=field)
    if industry_count:
        locator['number_of_industries'] = int(match[industry_count])
    return dict(field='nbs_' + field, label=label, raw=value, unit=unit, locator=locator)


def _prices(parts, month):
    # The introductory paragraph joins the three aggregate price readings.
    # Specific month, subject and rate bounds prevent adopting later sector figures.
    lead = f'{month}月份，'
    for field, pattern in (
            ('cpi', r'居民消费价格指数[（(]CPI[）)][^。；;]*?环比[^。]*?[；;，,]同比' + CHANGE),
            ('core', r'扣除食品和能源价格的核心\s*CPI\s*同比' + CHANGE),
            ('ppi', r'工业生产者出厂价格指数[（(]PPI[）)][^。；;]*?环比[^。]*?[；;，,]同比' + CHANGE)):
        if hit := _match(parts, pattern, paragraph_prefix=lead):
            yield _cell(hit, 2, field, direction=1)
    contributions = (
        ('ppi_up', r'(\d{1,2})个行业[^。；;]{0,42}?合计影响PPI同比上涨约?' + PERIOD_READING + '个百分点'),
        ('ppi_down', r'(\d{1,2})个行业[^。；;]{0,155}?合计(?:影响PPI同比下降|下拉PPI同比)约?' + PERIOD_READING + '个百分点'),
    )
    for field, pattern in contributions:
        if hit := _match(parts, pattern, paragraph_prefix='从同比看，全国PPI'):
            yield _cell(hit, 2, field, industry_count=1)


def _retail(parts, month, *, include_composition=False):
    # Read actual monthly values separately from the year-to-date headline.
    amount = r'([\d,]+(?:\.\d+)?)'
    pattern = (rf'(?<![\d—–－-]){month}月份[，,]社会消费品零售总额' + amount + r'亿元[，,]同比'
               + CHANGE + r'。其中[，,]除汽车以外的消费品零售额' + amount + r'亿元[，,](?:同比)?' + CHANGE)
    if hit := _match(parts, pattern):
        yield _cell(hit, 3, 'retail', direction=2)
        yield _cell(hit, 7, 'ex_auto', direction=6)
        if include_composition:
            yield _cell(hit, 1, 'retail_amount')
            yield _cell(hit, 5, 'ex_auto_amount')
    if not include_composition:
        return
    types = (rf'(?<![\d—–－-]){month}月份[，,]商品零售额' + amount + r'亿元[，,](?:同比)?' + CHANGE
             + r'[；;]餐饮收入' + amount + r'亿元[，,](?:同比)?' + CHANGE)
    if hit := _match(parts, types):
        yield _cell(hit, 3, 'goods', direction=2)
        yield _cell(hit, 7, 'catering', direction=6)
        yield _cell(hit, 1, 'goods_amount')
        yield _cell(hit, 5, 'catering_amount')


def bulletin_cells(task, record, scope):
    if not (bound := _verified_body(task, record, scope)):
        return ()
    category, period, parts = bound
    month = int(period[5:])
    if category == PMI_RELEASE:
        cells = _manufacturing_pmi(parts, month)
    elif category == 'price':
        cells = _prices(parts, month)
    else:
        cells = _retail(parts, month, include_composition=scope.get('study') == RETAIL_STUDY)
    return [dict(cell, period=period, code='中国') for cell in cells]


def _manufacturing_pmi(parts, month):
    headings = [index for index, part in enumerate(parts)
                if re.match(r'^[一二三四五六七八九十]+[、.．]\s*中国(?:非制造业|制造业|综合PMI)', part['text'].strip())]
    if not headings:
        return
    manufacturing = {}
    for start, end in zip(headings, [*headings[1:], len(parts)], strict=True):
        if '中国制造业采购经理指数' in parts[start]['text']:
            for index in range(start + 1, end):
                manufacturing[index] = parts[index]
    # Keep original part indexes even when the page repeats its mobile body.
    scoped_parts = [manufacturing.get(index, {'locator': ''}) for index in range(len(parts))]
    names = (rf'{month}月份[，,]\s*制造业采购经理指数[（(]PMI[）)]', '生产指数', '新订单指数')
    for field, name in zip(PMI_FIELDS, names, strict=True):
        expression = r'^' + name + r'\s*为\s*' + PERIOD_READING + r'%'
        if hit := _match(scoped_parts, expression):
            if Decimal(hit[2][1]) <= 100 and Decimal(hit[2][1]) >= 0:
                yield _cell(hit, 1, field)
        change = expression + r'[，,]\s*比上月(上升|下降)' + PERIOD_READING + r'个百分点'
        if hit := _match(scoped_parts, change):
            yield _cell(hit, 3, field + '_change', direction=2)


def pmi_release_context(task, scope):
    """Expose page-declared release metadata only after verifying the retained article."""
    candidates = []
    for record in task.get('source_records', ()):
        if (record.get('source'), record.get('operation')) != ('official', 'release_original'):
            continue
        bound = _verified_body(task, record, scope)
        if bound and bound[0] == PMI_RELEASE:
            origin = record['snapshot']['official_origin']
            timestamp = _meta(bound[2], 'pubdate').replace('/', '-')
            candidates.append(dict(source_ref=record['id'], title=origin['title'], url=origin['url'],
                                   reference_month=bound[1], displayed_release_time=timestamp + ' +08:00'))
    return candidates[0] if len(candidates) == 1 else None


def pmi_market_inputs_ready(task, scope):
    """Skip a paid acquisition plan only for the fully covered dated comparison."""
    from .draft_sources import observations
    from .market_calendar import retained_calendars, stated_close_intervals, verified_session_window
    from .subject_scope import subject_key

    if (_ADDITIONAL_MARKET_DATA.search(task['question'])
            or not pmi_release_context(task, scope)):
        return False
    windows = stated_close_intervals(task, explicit=True)
    if not windows:
        return False
    facts = observations(task)
    fields = {row['field'] for row in facts if row['value'] is not None and row['source'] == 'official'}
    if not {f'nbs_{field}' for field in PMI_FIELDS} <= fields:
        return False
    calendars = retained_calendars(task)
    prices = {}
    for row in facts:
        if row['field'] == 'close' and row['value'] is not None:
            key = subject_key(row['code']).upper(), row['period']
            prices.setdefault(key, set()).add((row['value'], row['series_basis']))
    for code, ranges in windows.items():
        for start, end, _ in ranges:
            sessions = verified_session_window(code, start, end, calendars)
            if not sessions or not all(len(prices.get((code, day.isoformat()), ())) == 1 for day in sessions):
                return False
    return True


_ADDITIONAL_MARKET_DATA = re.compile(
    r'财务|估值|成交|资金流向|净值|折溢价|持仓|回测|期货|期权|市场预期数据|汇率|利率|分钟|盘中|异常收益')


def pmi_quote_readout_scope(task):
    """The bounded case uses a common closing window around a weekend release."""
    from .market_calendar import stated_close_intervals

    scope = dated_pmi_scope(task)
    text = task['question']
    if (not scope or datetime.fromisoformat(scope['release_day']).weekday() < 5
            or _ADDITIONAL_MARKET_DATA.search(text) or not all(word in text for word in ('首日', '次日', '收盘'))):
        return None
    windows = stated_close_intervals(task, explicit=True)
    ranges = {bounds for values in windows.values() for bounds in values}
    if len(ranges) != 1 or len({code.rsplit('.', 1)[-1] for code in windows}) < 2:
        return None
    (first, last, question), = ranges
    released = datetime.fromisoformat(scope['release_day']).date()
    if question == scope['question_id'] and first < released < last and (last-first).days <= 31:
        return dict(scope, codes=sorted(windows), start=first.isoformat(), end=last.isoformat())
    return None


def frozen_retail_refs(content, facts):
    """Check each plotted retail cell against its bound release bytes."""
    scope = content.get('monthly_retail_scope')
    if (content.get(POLICY) != VERSION or not isinstance(scope, dict) or scope.get('study') != RETAIL_STUDY
            or not scope.get('periods')):
        return set()
    task = dict(content, id=content['task_id'], version=content['research_version'])
    dispatch = content.get('monthly_catalogue_dispatch')
    if isinstance(dispatch, dict):
        task['ledger'] = {'dispatches': [dispatch]}
    verified = {}
    for record in content.get('source_records', []):
        if record.get('operation') == 'release_original':
            verified[record['id']] = {cell['field']: cell for cell in bulletin_cells(task, record, scope)}
    keys = ('field', 'period', 'unit', 'code', 'locator')
    refs = set()
    for row in facts:
        match = verified.get(row.get('artifact_id'), {}).get(row.get('field'))
        if (match and all(row.get(key) == match.get(key) for key in keys)
                and row.get('raw_value') == match['raw'] and row.get('value') == float(match['raw'])):
            refs.add(row['id'])
    return refs


def selected_series(facts):
    """Return one reading per field and month, omitting any conflict."""
    groups = {}
    for row in facts:
        locator = row.get('locator', {})
        field = row.get('field', '').removeprefix('nbs_')
        if locator.get('kind') != CELL_KIND or field not in FIELDS or (row['label'], row['unit']) != FIELDS[field]:
            continue
        groups.setdefault((field, row['period']), []).append(row)
    selected = {}
    for (field, period), rows in groups.items():
        values = {(Decimal(row['raw_value']), row['locator'].get('number_of_industries')) for row in rows}
        if len(values) == 1:
            selected.setdefault(field, {})[period] = rows[0]
    return selected
