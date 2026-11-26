"""Independent exchange sessions, never invented prices or security status.

Uses OH's separate-calendar principle and inspected HTTP request convention,
without importing its code, Store or credentials. Dates remain exact source
values. Missing prices may reflect suspension, listing scope or missing data;
an exchange calendar cannot decide which cause applies.
"""
import copy
from collections import Counter
from datetime import date, timedelta
from itertools import chain
import math
import re

from .contracts import ContractError, DOCUMENT_CONTENT_INTENT, fingerprint, market_date

POLICY = 'market_calendar_coverage_version'
BASIS_POLICY = 'market_coverage_basis_version'
QUOTE_ENDPOINT_POLICY = 'draft_quote_interval_endpoints_version'
LIQUIDITY_POLICY = 'ifind_daily_liquidity_version'
LITERAL_MARKET_SCOPE_VERSION = 12
SAME_DAY_MARKET_SCOPE_VERSION = 13
QUOTE_ENDPOINT_VERSION = SAME_DAY_MARKET_SCOPE_VERSION
NAMED_INDEX_MARKET_POLICY = 'draft_named_index_market_version'
NAMED_INDEX_MARKET_VERSION = 1
LATEST_SESSION_CALCULATION_POLICY = 'draft_latest_session_calculation_version'
LATEST_SESSION_CALCULATION_VERSION = 1
LITERAL_MARKET_SCOPE_VERSIONS = (LITERAL_MARKET_SCOPE_VERSION, SAME_DAY_MARKET_SCOPE_VERSION)
MARKET_DATE_CLAUSE_VERSIONS = (11, *LITERAL_MARKET_SCOPE_VERSIONS)
EXACT_MARKET_DAY_VERSIONS = (8, 9, 10, *MARKET_DATE_CLAUSE_VERSIONS)
DAILY_CLOSE_RETURN_VERSIONS = (9, 10, *MARKET_DATE_CLAUSE_VERSIONS)
MULTI_MARKET_DAY_VERSIONS = (10, *MARKET_DATE_CLAUSE_VERSIONS)
NEED_SCOPE = 'market_calendar_coverage'
DOCUMENTATION = 'https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/manual.html'
HTTP_EXAMPLE = 'https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/example.html'
MARKETS = {'SH': '212001', 'SZ': '212100', 'HK': '212200'}
EXCHANGES = {'212001': 'SSE', '212100': 'SZSE', '212200': 'HKEX'}
FUNCTION = {'mode': '1', 'dateType': '0', 'period': 'D', 'dateFormat': '0'}
REGISTRY = {'ifind.get_trade_dates': dict(version=1, kind='独立交易所交易日历',
    raw_only=True, fields={}, documentation=DOCUMENTATION)}


def enabled(task):
    return type(task.get(POLICY)) is int and task[POLICY] == 1 and task.get('intent') != DOCUMENT_CONTENT_INTENT


def quote_interval_enabled(task):
    version = task.get(QUOTE_ENDPOINT_POLICY)
    return type(version) is int and version in (2, 3, 4, 5, 6, 7, *EXACT_MARKET_DAY_VERSIONS)


def stated_quote_enabled(task):
    version = task.get(QUOTE_ENDPOINT_POLICY)
    return type(version) is int and version in (3, 4, 5, 6, 7, *EXACT_MARKET_DAY_VERSIONS)


def stated_session_gaps_enabled(task):
    version = task.get(QUOTE_ENDPOINT_POLICY)
    return type(version) is int and version in (4, 5, 6, 7, *EXACT_MARKET_DAY_VERSIONS)


def market_metric_scope_enabled(task):
    version = task.get(QUOTE_ENDPOINT_POLICY)
    return type(version) is int and version in (5, 6, 7, *EXACT_MARKET_DAY_VERSIONS)


_CLOSE_COMPARISON = re.compile(r'收盘(?:价|点位)?|涨跌幅|(?:区间|累计)(?:涨幅|跌幅|收益率)')
_CLOSE_MEASURE = re.compile(r'收盘(?:价|点位|读数|涨跌幅|涨幅|跌幅|收益率|变化|表现)')
_OTHER_MEASURE = re.compile(r'净值|市值|市盈率|市净率|估值|股息率|成交[额量]|换手率|营收|营业收入|净利润|现金流|'
                            r'(?<![A-Za-z0-9])(?:PE|PB|ROE|ROA)(?![A-Za-z0-9])', re.I)
_OTHER_QUOTE_BASIS = re.compile(
    r'(?<!不)(?<!未)复权|港元|港币|美元|(?<![A-Za-z])(?:HKD|USD)(?![A-Za-z])'
    r'|分钟(?:线|收盘)|盘中(?:报价|行情)|含分红|再投资|(?:持有(?:人|期))?总回报', re.I)
_CLOSE_NEGATION = re.compile(
    r'(?:不要|不必|不用|无需|不得|禁止|不)\s*(?:查(?:询|看)?|取|计算|比较|核对)?\s*收盘(?:价|点位)?')
_QUOTE_QUALIFIER = re.compile(r'口径|复权|币种|用(?:港元|港币|美元|HKD|USD)', re.I)
_NAV_QUALIFIER = re.compile(r'只(?:要|看|核对|用)|以.*为准')
_MARKET_COMMA = re.compile(r'[，,]')
_MARKET_SENTENCE_BREAK = re.compile(r'[。；;！？?!\n]')
_SAME_DAY = re.compile(r'同日')
_SAME_DAY_READ_HEAD = re.compile(
    r'^(?:(?:请|帮我|再|同时|另外|也|并)\s*)*'
    r'(?:(?:核对|查询|查一下|查看|查|取|给出|列出)\s*)?')
_SAME_DAY_IMPLICIT_SUBJECT = re.compile(r'^(?:(?:它|其|该(?:ETF|基金|指数|股|标的))的?)?\s*$', re.I)
_SAME_DAY_NAV_LABEL = re.compile(r'^(\s*的?\s*)净值(?=$|[\s。！？!?，,]|是多少|多少)')
_MARKET_SUBJECT_LIST = re.compile(r'\s*(?:、|以及|与|和|及|或)\s*(?:(?:ETF|股票|指数)(?:代码)?)?\s*', re.I)
_MARKET_CODE_JOIN = re.compile(r'\s*(?:以及|与|和|及|、|[，,])')
_MARKET_METRIC = re.compile(r'(?P<close>收盘(?:价|点位))|(?P<unit_nav>单位净值)')
_MARKET_DATE_REFERENCE = re.compile(
    r'(?:按(?:照)?|沿用|继续用|仍用|使用|用|保持)\s*'
    r'(?:(?:上(?:一)?轮|上一条|刚才(?:改过|调整过|选定)?|此前|之前|前面|上面|原先|原来)的?|'
    r'(?:同一|相同)(?:个|段)?)\s*(?:日期(?:区间|范围)?|观察区间|时间(?:区间|范围)|区间|期间|时段)')
_MARKET_REFERENCE_NEGATION = re.compile(r'(?:不|不要|不必|不再|无需|不得|没|别)(?:再|继续)?\s*$')
_MARKET_REFERENCE_EXCLUSION = re.compile(
    r'(?:不|不要|不必|不用|无需|不得|禁止|别)\s*(?:看|查看|查询|核对|计算|取|比较|使用)?\s*'
    rf'(?:{_MARKET_METRIC.pattern})')
_AS_OF_OR_RELEASE = re.compile(
    r'(?:截至|截止(?:时点|时间|日期)?(?:为|至)?|(?:公告|发布|披露|生效)(?:于|日期|时间)?|排除|剔除|不看)\s*[:：]?\s*$')
_RELATIVE_OR_RELEASE = re.compile(
    r'\s*[日号]?\s*(?:(?:的?(?:公司|官方|该公司|基金))?(?:公告|发布|披露|生效)|以来|之前|之后|为止|起|前|后(?!者))')
_DIRECT_DAY_SUBJECT = re.compile(
    r'(?:(?:请|帮我|麻烦)\s*)?(?:(?:核对|查询|查(?:一下|看)?|看(?:一下)?|给出|列出)\s*)?'
    r'[\u3400-\u9fffA-Za-z0-9·（）()\s]{0,24}')
_DIRECT_DAY_TAIL = re.compile(r'\s*(?:是什么|是多少|多少|的读数|读数|数值)?\s*[。！？?!]?\s*')
_DAILY_RETURN_LABEL = (r'(?:(?:(?:较|比|相比|相对)上(?:一|一个)交易日的?|当天的?|当日的?|日)\s*)?'
                       r'(?:收盘)?(?:涨跌幅|涨幅|跌幅)')
_DIRECT_DAILY_RETURN = re.compile(r'\s*(?:和|与|及|以及|、|[，,]\s*(?:及|和)?)\s*'
                                  + _DAILY_RETURN_LABEL + _DIRECT_DAY_TAIL.pattern)
_DAILY_RETURN = re.compile(_DAILY_RETURN_LABEL)
_RETURN_NEGATION = re.compile(r'(?:不要|不需要|无需|不用|不必|不查|不看|不要求|不|别)\s*'
                              r'(?:算|看|核对|计算|查(?:询)?)?\s*' + _DAILY_RETURN_LABEL)
_OTHER_RETURN = re.compile(r'(?:区间|累计|净值|市值|年初至今)的?\s*' + _DAILY_RETURN_LABEL)
_CLOSE_PAIR_QUALIFIER = r'这?(?:两日|两天|两个交易日)(?:之间|间)?|区间|期间|累计'
_CLOSE_PAIR_COMPARE = re.compile(r'比较|对比|对照|之间|' + _CLOSE_PAIR_QUALIFIER)
_CLOSE_PAIR_WORDS = re.compile(r'对照|之间|并|计算|算出|' + _CLOSE_PAIR_QUALIFIER)
_CLOSE_PAIR_RATE = re.compile(
    rf'(?:(?:{_CLOSE_PAIR_QUALIFIER})的?)?(?:收盘(?:价|点位)?的?)?(?:涨跌幅|涨幅|跌幅|变化率)')
_CLOSE_PAIR_NEGATION = re.compile(
    r'(?:不要|不需要|无需|不用|不必|不|别)\s*(?:计算|比较|核对|查(?:询)?|列出|算)?\s*'
    + _CLOSE_PAIR_RATE.pattern)
_NON_READING_SUBJECT = re.compile(r'分析|解释|为什么|研究|预测|回测|涨跌|影响|推荐|建议|判断|配置|估值')
_SEPARATE_DAILY_RATE = re.compile(
    r'各自|各(?:个交易)?日|逐日|每日|每个交易日|单日|'
    r'(?:的|及|与|和|核对|列出|计算)\s*日(?:收盘)?(?:涨跌幅|涨幅|跌幅)|'
    r'(?:上(?:一|一个)|前一|前一个)交易日|当(?:日|天)的?(?:收盘)?(?:涨跌幅|涨幅|跌幅)|'
    r'分别(?:计算|核对|列出|给出|查看|算)?(?:两日|两天|两个交易日)?的?(?:收盘)?(?:涨跌幅|涨幅|跌幅)')
_POINT_METRIC = re.compile(
    _MARKET_METRIC.pattern + rf'|(?P<daily_return>(?:(?:各日|逐日|每日|每个交易日)的?)?{_DAILY_RETURN_LABEL})')
_DAILY_NAV_PREMIUM = re.compile(r'(?:收盘(?:价)?的?|同(?:一估值)?日的?|当[日天]的?|日度的?)?\s*(?:折溢价率|溢价率|折价率)')
_BARE_PREMIUM = re.compile(r'折溢价(?=$|[\s，,。；;！!？?])')
_DAILY_NAV_PREMIUM_WORDS = re.compile(r'计算|核算|算出|用|使用|各自的?|同日的?')
_DATE_LIST_JOIN = re.compile(r'\s*(?:以及|和|与|及|、|[，,])\s*')
_DATED_CLAUSE_JOIN = re.compile(
    r'(?:[，,、]\s*(?:以及|和|与|及|并|再)?|以及|和|与|及|并)'
    r'\s*(?:(?:再)?(?:核对|查询|列出|查))?\s*(?:在|于|从|自)?\s*$')
_SHORT_DATE_SEPARATOR = r'\s*(?:[，,、]\s*(?:以及|和|与|及)?|以及|和|与|及)\s*'
_SHORT_DATE_JOIN = re.compile(_SHORT_DATE_SEPARATOR)
_SHORT_DAY_ITEM = re.compile(_SHORT_DATE_SEPARATOR +
    r'(?P<date>(?:(?P<month>\d{1,2})月)?(?P<day>\d{1,2})[日号])')
_SHORT_DAY_TAIL = re.compile(r'\s*(?:[至到—–~～-]|内|外|均线|均值|窗口|移动平均|滚动)')
_POINT_QUERY_WORDS = re.compile(
    r'帮我|麻烦|请|核对|查询|查一下|查看|给出|列出|比较|对比|分别|各日|逐日|各自|两天|当天|当日|'
    r'是多少|是什么|多少|数值|只看|以及|并列示|并列|列示|和|与|及|在|于|的|日|[\s，,、。！？!?；;]')
_POINT_REPORT_TAIL = re.compile(
    r'(?:(?:[，,。；;]\s*(?:并\s*)?|并\s*)(?:请\s*)?'
    r'(?:(?:给出?|生成|输出|写|出)(?:一[份篇])?(?:(?:简短|简要|完整|详细)的?)?(?:研究)?报告'
    r'|(?:保留|注明|列明|标明)缺项)\s*)+$')
_CONTEXTUAL_POINT_WORDS = re.compile(
    r'这个标的|该标的|原标的|前者|后者|按|还是|继续|仍然|只|仅|'
    r'核对|查询|查一下|查|查看|看一下|看|给出|列出|能否|是什么|是多少|多少|数值|'
    r'当日|当天|在|于|的|日|请|帮我|本轮|本次|[\s，,、。！？!?；;]')
_POINT_REMARK_TAIL = re.compile(r'[。！？!?；;]\s*(?:只作|仅作|作为)备注\s*$')
POINT_RETURN_LOOKBACK = 35
_MARKET_METRIC_JOIN = re.compile(
    r'\s*(?:的?(?:区间)?(?:涨跌幅|变化|涨幅|跌幅|读数|走势))?\s*'
    r'(?P<join>以及|和|与|及|、|[，,])\s*(?:(?:未|不|前|后)复权\s*)?')


def stated_close_intervals(task, *, explicit=False, clause=None):
    """Read exact daily windows from the question with an explicit security."""
    if not stated_quote_enabled(task) or task.get('intent') == DOCUMENT_CONTENT_INTENT:
        return {}
    if market_metric_scope_enabled(task):
        return stated_market_intervals(task, 'close', explicit=explicit, clause=clause)
    if clause is not None:
        return {}
    from .evidence import declared_security_codes, security_code_mentions
    from .subject_scope import scope_question
    from .market_series import daily_market_as_of

    cutoff = date.fromisoformat(daily_market_as_of(task))
    declared = set(declared_security_codes(task))
    result = {}
    for question in task.get('questions', []):
        if not question.get('required', True) or question.get('answer_scope') == 'general_definition':
            continue
        text = scope_question(task) if question['question'] == task.get('question') else question['question']
        if not _CLOSE_COMPARISON.search(text):
            continue
        needs = question.get('evidence_needs', [])
        scopes = question.get('evidence_need_scopes', {})
        if needs and all(scopes.get(need) in ('query_provenance', NEED_SCOPE) for need in needs):
            continue
        encoding = task.get('security_declaration_encoding', 1)
        codes = set(security_code_mentions(text, encoding=encoding)) & declared
        if not codes and len(declared) == 1:
            codes = declared
        if not codes:
            continue
        match_measure = stated_session_gaps_enabled(task)
        # Each explicitly named subject owns the periods that follow it.
        for sentence in re.split(r'[。；;！？?!\n]', text):
            if not _CLOSE_COMPARISON.search(sentence):
                continue
            _add_stated_intervals(result, sentence, codes, encoding, cutoff, question['id'],
                                  match_measure=match_measure)
    return result


def stated_day_intervals(sentence):
    from .numeric_integrity import draft_day_intervals
    return draft_day_intervals(sentence)


def _following_short_days(text, offset, anchor, intervals):
    while match := _SHORT_DAY_ITEM.match(text, offset):
        start, end = match.span('date')
        if (_SHORT_DAY_TAIL.match(text[end:]) or _RELATIVE_OR_RELEASE.match(text[end:])
                or any(left < end and start < right for left, right, _, _ in intervals)):
            break
        try:
            day = date(anchor.year, int(match['month'] or anchor.month), int(match['day']))
        except ValueError:
            break
        yield start, end, day, day
        offset, anchor = end, day


def _market_windows(task, text):
    if task.get(QUOTE_ENDPOINT_POLICY) not in EXACT_MARKET_DAY_VERSIONS:
        return stated_day_intervals(text)
    from .draft_sources import _unquoted_local_scope
    from .numeric_integrity import draft_day_points

    text = _unquoted_local_scope(text)
    windows = stated_day_intervals(text)
    dated_clauses = task.get(QUOTE_ENDPOINT_POLICY) in MARKET_DATE_CLAUSE_VERSIONS
    for start, end, day in draft_day_points(text, windows, adjacent_security_code=dated_clauses):
        if not _AS_OF_OR_RELEASE.search(text[:start]) and not _RELATIVE_OR_RELEASE.match(text[end:]):
            windows.append((start, end, day, day))
            if dated_clauses:
                windows.extend(_following_short_days(text, end, day, windows))
    return sorted(windows)


_DAILY_LIQUIDITY = re.compile(r'(?P<volume>成交量)|(?P<amount>成交金额|成交额)|(?P<price_volume>日线量价)')
_LIQUIDITY_LABELS = r'成交量|成交金额|成交额|日线量价'
_LIQUIDITY_EXCLUSION = re.compile(r'(?:不(?:要|用|必|查|取|看|需要)|无需|别|排除)\s*(?:查询|查|获取|取|看)?\s*'
                                  rf'(?:{_LIQUIDITY_LABELS})(?:\s*(?:、|和|及|与|以及)\s*(?:{_LIQUIDITY_LABELS}))*')
_DAILY_LIQUIDITY_BASIS = re.compile(r'分钟(?:线|行情|收盘价)?|分时|盘中|实时|逐笔|分笔|周线|月线|'
                                    r'总成交|累计成交|平均成交|日均|前复权|后复权')
_DIRECT_LIQUIDITY_UNRELATED = re.compile(r'分析|解释|研究(?:一下)?(?!报告)|为什么|判断|策略|未来|推荐|回测|归因|'
                                        r'涨跌幅|开盘价|最高价|最低价|换手率|市盈率|市净率|净值|财务|公告|资金流|估值')


def stated_liquidity_intervals(task):
    """Bind requested daily turnover fields to the subject and dates of each clause."""
    from .draft_sources import _unquoted_local_scope, stated_access_restricted
    from .evidence import available_security_codes, security_code_matches
    from .market_series import daily_market_as_of
    from .subject_scope import scope_question

    result = {}
    if (task.get(LIQUIDITY_POLICY) != 1 or task.get('research_mode') != 'current'
            or task.get('intent') == DOCUMENT_CONTENT_INTENT):
        return result
    main = scope_question(task)
    if not _DAILY_LIQUIDITY.search(main) or stated_access_restricted(task, main, scoped_market=True):
        return result
    declared = set(available_security_codes(task))
    encoding = task.get('security_declaration_encoding', 1)
    cutoff = date.fromisoformat(daily_market_as_of(task))
    for question in task['questions']:
        if not question.get('required', True) or question.get('answer_scope') == 'general_definition':
            continue
        text = main if question['question'] == task['question'] else question['question']
        text = _unquoted_local_scope(text)
        excluded = {match.lastgroup for exclusion in _LIQUIDITY_EXCLUSION.finditer(text)
                    for match in _DAILY_LIQUIDITY.finditer(exclusion[0])}
        question_codes = {match[1] for match in security_code_matches(text, encoding=encoding)}
        for sentence in _MARKET_SENTENCE_BREAK.split(text):
            if not _DAILY_LIQUIDITY.search(sentence):
                continue
            windows = _market_windows(task, sentence)
            mentions = list(security_code_matches(sentence, encoding=encoding))
            for fragment in _market_window_fragments(sentence, windows, mentions, join_points=True,
                                                     split_dated_clauses=True):
                if _DAILY_LIQUIDITY_BASIS.search(fragment):
                    continue
                fields = {match.lastgroup for match in _DAILY_LIQUIDITY.finditer(fragment)} - excluded
                if 'price_volume' in fields:
                    fields = (fields - {'price_volume'}) | {'close', 'volume'}
                if not fields:
                    continue
                codes = list(security_code_matches(fragment, encoding=encoding))
                # Only bind a shared metric list to an explicit subject list.
                if len(codes) > 1 and (re.search(r'分别|前者|后者|其中', fragment)
                        or any(not _MARKET_SUBJECT_LIST.fullmatch(fragment[one.end(1):two.start(1)])
                               for one, two in zip(codes, codes[1:]))):
                    continue
                subjects = {match[1] for match in codes}
                if not subjects and len(question_codes) == 1:
                    subjects = question_codes
                if not subjects or not subjects <= declared:
                    continue
                if len(subjects) > 1 and _CLOSE_MEASURE.search(fragment) and not _CLOSE_NEGATION.search(fragment):
                    fields.add('close')  # Daily close shares this explicit subject and metric list.
                for _, _, start, end in _market_windows(task, fragment):
                    if end > cutoff:
                        continue
                    for code in subjects:
                        if re.fullmatch(r'\d{6}\.(?:SH|SZ)|\d{5}\.HK', code):
                            for field in fields:
                                result.setdefault((code, field), set()).add((start, end, question['id']))
    return result


def direct_liquidity_scope(task):
    """A literal liquidity readout can use the shared provider path without a model."""
    from .subject_scope import scope_question

    if task.get(LIQUIDITY_POLICY) != 1:
        return None
    text = scope_question(task)
    required = [row for row in task.get('questions', ()) if row.get('required', True)]
    if (len(required) != 1 or required[0]['question'] != task['question']
            or required[0].get('answer_scope') == 'general_definition'
            or task.get('materials') or task.get('pending_materials') or _DIRECT_LIQUIDITY_UNRELATED.search(text)):
        return None
    intervals = stated_liquidity_intervals(task)
    if not intervals:
        return None
    requested = {match.lastgroup for match in _DAILY_LIQUIDITY.finditer(
        _LIQUIDITY_EXCLUSION.sub('', text))}
    fields = {field for _, field in intervals}
    expected = (requested - {'price_volume'}) | ({'close', 'volume'} if 'price_volume' in requested else set())
    closes = stated_close_intervals(task, explicit=True)
    if not expected <= fields or _CLOSE_MEASURE.search(_CLOSE_NEGATION.sub('', text)) and (
            'close' not in fields and not closes):
        return None
    return intervals


def stated_direct_market_day(task, *, for_report=False, clause=None):
    """The complete question asks for literal market readings."""
    from .draft_sources import local_scope_text, stated_access_restricted, stated_source_text
    from .evidence import security_code_matches
    from .market_series import _latest_question, stated_nav_intervals
    from .subject_scope import scope_question

    if (task.get(QUOTE_ENDPOINT_POLICY) not in EXACT_MARKET_DAY_VERSIONS
            or task.get('materials') or task.get('pending_materials')):
        return False
    def reading_question(value):
        text = stated_source_text(task, value).strip(' ，,')
        if for_report:
            text = local_scope_text(text)
        text = _POINT_REPORT_TAIL.sub('', text.strip(' ，,；;。！？!?\n')).strip()
        return resolve_named_index_points(task, resolve_latest_session_market_text(task, text))[0]

    text = reading_question(scope_question(task) if clause is None else clause)
    if stated_access_restricted(task, text, scoped_market=True, for_local_report=for_report):
        return False
    from .knowledge import GENERAL_DEFINITION_SCOPE, draft_definitions_enabled
    questions = task.get('questions', [])
    if draft_definitions_enabled(task) and any(q.get('answer_scope') == GENERAL_DEFINITION_SCOPE for q in questions):
        readouts = [q for q in questions if q.get('answer_scope') != GENERAL_DEFINITION_SCOPE]
        return task.get('research_mode') == 'current' and bool(readouts) and all(
            q['kind'] in ('fact', 'comparison')
            and not any(q.get(key) for key in ('dependencies', 'evidence_needs', 'method_needs'))
            and _direct_market_points(task, reading_question(q['question']), q['id'])
            for q in readouts)
    if not _latest_question(task):
        return False
    if (_direct_market_points(task, text, clause=clause)
            or clause is None and _contextual_market_point(task, text)):
        return True
    if for_report:
        from .market_series import without_market_source_directives

        text = without_market_source_directives(task, text).strip()
    codes = list(security_code_matches(text, encoding=task.get('security_declaration_encoding', 1)))
    windows = _market_windows(task, text)
    metrics = list(_MARKET_METRIC.finditer(text))
    return_only = not metrics and task.get(QUOTE_ENDPOINT_POLICY) in DAILY_CLOSE_RETURN_VERSIONS
    if return_only:
        metrics = list(_DAILY_RETURN.finditer(text))
    if len(codes) != 1 or len(windows) != 1 or len(metrics) != 1:
        return False
    code, (start, end, day, last), metric = codes[0], windows[0], metrics[0]
    if day != last or not code[1].endswith(('.SH', '.SZ')) or not code.end(1) <= start < end <= metric.start():
        return False
    subject = text[:code.start(1)]
    tail = text[metric.end():]
    daily_return = (task.get(QUOTE_ENDPOINT_POLICY) in DAILY_CLOSE_RETURN_VERSIONS and metric.lastgroup == 'close'
                    and _DIRECT_DAILY_RETURN.fullmatch(tail) is not None)
    if (not _DIRECT_DAY_SUBJECT.fullmatch(subject)
            or re.search(r'分析|解释|比较|对比|研究|预测|回测|涨跌|影响', subject)
            or text[code.end(1):start].strip() not in ('', '在', '于')
            or text[end:metric.start()].strip() not in ('', '的', '当日', '当日的', '当天', '当天的', '日', '日的')
            or not (_DIRECT_DAY_TAIL.fullmatch(tail) or daily_return)):
        return False
    if return_only or metric.lastgroup == 'close':
        intervals = stated_close_intervals(task, explicit=True, clause=clause).get(code[1], ())
        return len(intervals) == 1 and (not (return_only or daily_return) or any(
            target_code == code[1] and target_day == day
            for target_code, target_day, _ in stated_close_returns(task, clause=clause)))
    return task.get('research_mode') == 'current' and len(stated_nav_intervals(task, clause=clause).get(code[1], ())) == 1


def _contextual_market_point(task, text):
    """Recognize a single readout after the conversation has bound its subject and date."""
    from .evidence import security_code_matches
    from .market_series import without_market_source_directives

    scope = task.get('subject_scope') or {}
    if not scope and not task.get('conversation_context'):
        return False
    if scope.get('question_spans'):
        text = _POINT_REMARK_TAIL.sub('', text)
    text = _market_question_dates(task, without_market_source_directives(task, text))
    windows = _market_windows(task, text)
    metrics = list(_MARKET_METRIC.finditer(text))
    if len(windows) != 1 or len(metrics) != 1:
        return False
    start, end, first, last = windows[0]
    metric = metrics[0]
    points = stated_market_points(task)
    if len(points) != 1:
        return False
    ((code, day, field, _),) = points
    if first != last or day != first or field != metric.lastgroup or not code.endswith(('.SH', '.SZ')):
        return False
    codes = list(security_code_matches(text, encoding=task.get('security_declaration_encoding', 1)))
    if any(match[1] != code for match in codes):
        return False
    tail = list(text)
    for left, right in [*(match.span(1) for match in codes), (start, end), metric.span()]:
        tail[left:right] = ' ' * (right-left)
    return not _CONTEXTUAL_POINT_WORDS.sub('', ''.join(tail))


def _market_date_mention(text):
    from .draft_sources import _unquoted_local_scope

    unquoted = _unquoted_local_scope(text)
    for match in _MARKET_DATE_REFERENCE.finditer(unquoted):
        if not _MARKET_REFERENCE_NEGATION.search(unquoted[:match.start()]):
            return match
    return None


def _market_date_fields(text):
    from .draft_sources import _unquoted_local_scope

    requested = _MARKET_REFERENCE_EXCLUSION.sub('', _unquoted_local_scope(text))
    return {match.lastgroup for match in _MARKET_METRIC.finditer(requested)}


def _delivered_latest_day(previous, subjects, read_report):
    """Read the selected observation date from the delivery the user refers to."""
    from .market_series import NAV_INDICATOR, stated_direct_latest_scopes, stated_mixed_latest_scopes
    from .subject_scope import subject_key

    delivery = previous.get('delivery') or {}
    if not subjects or delivery.get('version') != previous['version'] or not delivery.get('digest'):
        return None
    scopes = stated_direct_latest_scopes(previous, for_report=True) or stated_mixed_latest_scopes(previous)
    fields = {}
    for scope in scopes:
        identifiers = scope['codes'] or ([scope['name']] if scope.get('name') else [])
        metric = scope['indicator']
        for identifier in identifiers:
            allowed = fields.setdefault(subject_key(identifier).upper(), set())
            allowed.add(metric)
            if metric == NAV_INDICATOR:
                allowed.add('unit_nav')

    session_day = None
    if not fields and (scope := stated_latest_session_calculation(previous, for_report=True)):
        sessions = latest_session_calculation_sessions(previous, scope)
        if sessions:
            session_day = sessions[-1].isoformat()
            fields[subject_key(scope['code']).upper()] = {'close', NAV_INDICATOR, 'unit_nav'}

    targets = subjects & fields.keys()
    if not targets:
        return None
    try:
        report = read_report(previous['id'], delivery['version'])
    except ContractError:
        return None
    if report['digest'] != delivery['digest']:
        return None

    observed = {}
    for row in (report['content'].get('draft') or {}).get('facts', ()):
        if row.get('previous_close_for') or row.get('value') is None:
            continue
        aliases = {subject_key(row.get(key) or '').upper() for key in ('code', 'object_label')}
        day = row.get('period', '')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) or session_day and day != session_day:
            continue
        for subject in targets & aliases:
            if row.get('field') in fields[subject]:
                observed.setdefault(subject, set()).add(date.fromisoformat(day))
    if observed.keys() != targets:
        return None
    unique_days = set().union(*observed.values())
    return next(iter(unique_days)) if len(unique_days) == 1 else None


def resolve_market_date_reference(task, previous, objects, *, read_report=None):
    """Bind one explicit window or one delivered latest date for the same subject."""
    from .draft_sources import _unquoted_local_scope
    from .market_series import daily_market_as_of, stated_nav_intervals
    from .subject_scope import scope_question, subject_key

    if task.get(QUOTE_ENDPOINT_POLICY) not in (7, *EXACT_MARKET_DAY_VERSIONS) or previous is None:
        return None
    text = _unquoted_local_scope(scope_question(task))
    if not _market_date_fields(text) or not _market_date_mention(text) or _market_windows(task, text):
        return None
    current_subjects = {subject_key(code).upper() for code in objects}
    prior_scopes = (stated_close_intervals(previous), stated_nav_intervals(previous))
    matching, all_windows = set(), set()
    for scopes in prior_scopes:
        for code, bindings in scopes.items():
            windows = {(first, last) for first, last, _ in bindings}
            all_windows.update(windows)
            if subject_key(code).upper() in current_subjects:
                matching.update(windows)
    choices = matching or all_windows
    if not choices:
        history = previous.get('conversation_context') or {}
        edit = history.get('observation_request')
        candidates = [edit] if edit else [history.get('topic'), *(history.get('comparison_requests') or ())]
        for candidate in candidates:
            if not candidate or candidate.get('omitted_characters') or not isinstance(candidate.get('text'), str):
                continue
            if edit or _MARKET_METRIC.search(candidate['text']):
                choices.update((first, last) for _, _, first, last in _market_windows(task, candidate['text']))
    if not choices and read_report is not None:
        day = _delivered_latest_day(previous, current_subjects, read_report)
        if day is not None:
            choices.add((day, day))
    if len(choices) == 1:
        first, last = next(iter(choices))
        if first <= last <= date.fromisoformat(daily_market_as_of(task)):
            return dict(startdate=first.isoformat(), enddate=last.isoformat())
    return {}


def market_date_reference(task):
    if task.get(QUOTE_ENDPOINT_POLICY) not in (7, *EXACT_MARKET_DAY_VERSIONS):
        return None
    context = task.get('conversation_context')
    return (context if context is not None else task).get('market_date_reference')


def _market_question_dates(task, text):
    window = market_date_reference(task)
    if not window or not (match := _market_date_mention(text)):
        return text
    before, after = text[:match.start()], text[match.end():]
    resolved = f"按{window['startdate']}至{window['enddate']}"
    leading = _MARKET_SENTENCE_BREAK.split(before)
    following = _MARKET_SENTENCE_BREAK.split(after, maxsplit=2)
    if not _MARKET_METRIC.search(leading[-1] + following[0]):
        # A stand-alone period instruction qualifies its adjacent market clause.
        if len(following) > 1 and _MARKET_METRIC.search(following[1]):
            offset = len(following[0]) + 1
            return before + after[:offset] + resolved + after[offset:]
        if len(leading) > 1 and _MARKET_METRIC.search(leading[-2]):
            offset = len(before) - len(leading[-1]) - 1
            return before[:offset] + resolved + before[offset:] + after
    return before + resolved + after


def _market_window_fragments(sentence, windows, mentions, *, join_points=False, split_dated_clauses=False):
    """Keep a new subject or comma-separated dated clause with its own range."""
    boundaries = [0]
    date_join = _SHORT_DATE_JOIN if split_dated_clauses else _DATE_LIST_JOIN
    for (_, previous_end, first, last), (next_start, next_end, next_first, next_last) in zip(windows, windows[1:]):
        if (join_points and first == last and next_first == next_last
                and date_join.fullmatch(sentence[previous_end:next_start])):
            continue
        if split_dated_clauses:
            join = _DATED_CLAUSE_JOIN.search(sentence, previous_end, next_start)
            if (join and _MARKET_METRIC.search(sentence, previous_end, join.start())
                    and _MARKET_METRIC.search(sentence, next_end)):
                # A completed date/metric phrase owns its own subject, even
                # when the code followed the date. Start the next clause here.
                boundaries.append(join.start())
                continue
        next_subjects = [match for match in mentions if previous_end <= match.start(1) < next_start]
        # A subject after its date range can finish the first clause.
        # When intervening subjects aren't a list, the last one owns the next date range.
        boundary = next_subjects[-1].start(1) if next_subjects else None
        for left, right in reversed(list(zip(next_subjects, next_subjects[1:]))):
            if not _MARKET_SUBJECT_LIST.fullmatch(sentence[left.end(1):right.start(1)]):
                break
            boundary = left.start(1)
        if boundary is not None:
            # A comma before the new subject also carries its leading basis.
            separator = re.search(r'[，,][^，,]*$', sentence[previous_end:boundary])
            if separator:
                boundary = previous_end + separator.start() + 1
        if boundary is None:
            separator = _MARKET_COMMA.search(sentence, previous_end, next_start)
            if separator:
                boundary = separator.end()
        if boundary is not None:
            boundaries.append(boundary)
    boundaries.append(len(sentence))
    return (sentence[first:last] for first, last in zip(boundaries, boundaries[1:]))


def _daily_return_requested(fragment):
    return bool(_DAILY_RETURN.search(_OTHER_RETURN.sub('', _RETURN_NEGATION.sub('', fragment))))


def _market_measure(fragment, field, *, explicit, shared, negation, other_basis, daily_return=False):
    if negation.search(fragment) or other_basis.search(fragment):
        return False
    if field == 'unit_nav':
        return '单位净值' in fragment
    if '净值' in fragment:
        return False
    if '收盘' in fragment:
        return not (shared and _OTHER_MEASURE.search(fragment))
    return (daily_return or not explicit and bool(_CLOSE_COMPARISON.search(fragment))) and not _OTHER_MEASURE.search(fragment)


def _close_pair_requested(task, fragment, windows):
    if (task.get(QUOTE_ENDPOINT_POLICY) not in LITERAL_MARKET_SCOPE_VERSIONS or len(windows) != 2
            or any(first != last for _, _, first, last in windows)
            or windows[0][2] == windows[1][2]):
        return False
    affirmative = _RETURN_NEGATION.sub('', _CLOSE_PAIR_NEGATION.sub('', fragment))
    return bool(_CLOSE_MEASURE.search(affirmative)
                and not _OTHER_MEASURE.search(affirmative)
                and _CLOSE_PAIR_COMPARE.search(affirmative) and _CLOSE_PAIR_RATE.search(affirmative)
                and not _SEPARATE_DAILY_RATE.search(affirmative))


def _point_metric_matches(text):
    exclusions = [match.span() for pattern in (_OTHER_RETURN, _RETURN_NEGATION) for match in pattern.finditer(text)]
    return [match for match in _POINT_METRIC.finditer(text) if match.lastgroup != 'daily_return'
            or not any(first < match.end() and match.start() < last for first, last in exclusions)]


def _literal_market_points(task, text):
    """Recognize fully stated date and metric questions without a model."""
    from .draft_sources import _unquoted_local_scope, local_scope_text
    from .evidence import security_code_matches

    if task.get(QUOTE_ENDPOINT_POLICY) not in MULTI_MARKET_DAY_VERSIONS or task.get('intent') == DOCUMENT_CONTENT_INTENT:
        return None
    from .market_series import nav_source_scope_enabled, series_family, without_market_source_directives
    text = _unquoted_local_scope(without_market_source_directives(task, local_scope_text(text)))
    if nav_source_scope_enabled(task):
        text = re.sub(r'(?:基金)?份额净值', '单位净值', text)
    codes = list(security_code_matches(text, encoding=task.get('security_declaration_encoding', 1)))
    windows = _market_windows(task, text)
    metrics = _point_metric_matches(text)
    close_pair = _close_pair_requested(task, text, windows)
    premiums = list(_DAILY_NAV_PREMIUM.finditer(text)) if (
        nav_source_scope_enabled(task) and task.get('research_mode') == 'current' and len(codes) == 1
        and series_family(codes[0][1]) == 'ETF' and '收盘' in text
        and (len(windows) == 1 or re.search(r'各自|分别|逐日|各日', text))) else []
    if not (codes and windows and (metrics or close_pair or premiums)) or any(first != last for _, _, first, last in windows):
        return None
    spans = [*(code.span(1) for code in codes), *((start, end) for start, end, _, _ in windows),
             *(metric.span() for metric in metrics)]
    fields = {metric.lastgroup for metric in metrics}
    if premiums:
        spans.extend(match.span() for match in premiums)
        fields.update(('close', 'unit_nav', 'daily_nav_premium'))
    if close_pair:
        spans.extend(match.span() for match in _CLOSE_PAIR_RATE.finditer(text))
        fields.discard('daily_return')
        fields.add('close_pair_return')
        if len(codes) == 1 and codes[0].end(1) < windows[0][0]:
            label_end = codes[0].start(1)
            prefix = text[:label_end]
            if _DIRECT_DAY_SUBJECT.fullmatch(prefix) and not _NON_READING_SUBJECT.search(prefix):
                spans.append((0, label_end))
    tail = list(text)
    for start, end in spans:
        tail[start:end] = ' ' * (end-start)
    remaining = ''.join(tail)
    if close_pair:
        remaining = _CLOSE_PAIR_WORDS.sub('', remaining)
    if premiums:
        remaining = _DAILY_NAV_PREMIUM_WORDS.sub('', remaining)
    if _POINT_QUERY_WORDS.sub('', remaining):
        return None
    return {code[1] for code in codes}, {first for _, _, first, _ in windows}, fields


def _direct_market_points(task, text, question_id=None, *, clause=None):
    literal = _literal_market_points(task, text)
    if not literal or not _complete_market_points(task, literal, question_id, clause=clause):
        return False
    codes, _, fields = literal
    return (all(code.endswith(('.SH', '.SZ')) for code in codes)
            and ('unit_nav' not in fields or task.get('research_mode') == 'current'))


def _complete_market_points(task, literal, question_id=None, *, clause=None):
    codes, dates, fields = literal
    bound = {(code, day, field) for code, day, field, ident in stated_market_points(task, clause=clause)
             if question_id is None or ident == question_id}
    if 'daily_nav_premium' in fields:
        bound.update((code, day, 'daily_nav_premium') for code, day, ident in stated_daily_nav_premiums(task, clause=clause)
                     if question_id is None or ident == question_id)
    if 'daily_return' in fields:
        bound.update((code, day, 'daily_return') for code, day, ident in stated_close_returns(task, clause=clause)
                     if question_id is None or ident == question_id)
    if 'close_pair_return' in fields:
        bound.update((code, day, 'close_pair_return') for code, pairs in stated_close_pairs(task, clause=clause).items()
                     for first, last, ident in pairs if question_id is None or ident == question_id
                     for day in (first, last))
    return ({code for code, _, _ in bound} == codes and {day for _, day, _ in bound} == dates
            and fields <= {field for _, _, field in bound})


def unresolved_market_points(task):
    from .subject_scope import scope_question

    text, _ = resolve_named_index_points(task, scope_question(task))
    literal = _literal_market_points(task, text)
    return literal is not None and not _complete_market_points(task, literal)


def _metric_field(match):
    return 'close' if match.lastgroup == 'daily_return' else match.lastgroup


def _market_metric_items(task, text, *, points=False):
    """Split an explicit list while keeping each field's own qualifiers."""
    from .market_series import without_market_source_directives

    syntax = without_market_source_directives(task, text)
    metrics = _point_metric_matches(text) if points else list(_MARKET_METRIC.finditer(text))
    boundaries = [0]
    for left, right in zip(metrics, metrics[1:]):
        join = _MARKET_METRIC_JOIN.fullmatch(syntax[left.end():right.start()])
        if not join:
            return []
        boundaries.append(left.end() + join.end('join'))
    boundaries.append(len(text))
    items = []
    for metric, start, end in zip(metrics, boundaries, boundaries[1:]):
        field, clause = _metric_field(metric), text[start:end]
        if points and items and items[-1][0] == field:
            field, preceding = items.pop()
            clause = preceding + clause
        items.append((field, clause))
    return items


def _bound_market_metrics(task, fragment, mentions, *, points=False):
    """Assign mixed measures only to adjacent codes or an ordered explicit list."""
    metrics = _point_metric_matches(fragment) if points else list(_MARKET_METRIC.finditer(fragment))
    if not mentions or len({_metric_field(metric) for metric in metrics}) < 2:
        return []
    prefix, respectively, listed = fragment.partition('分别')
    if points and respectively and mentions[-1].end(1) <= metrics[0].start():
        prefix, listed = fragment[:metrics[0].start()], fragment[metrics[0].start():]
    if (respectively and len(mentions) > 1 and mentions[-1].end(1) <= len(prefix)
            and not _MARKET_METRIC.search(prefix)):
        items = _market_metric_items(task, listed, points=points)
        if (len(items) != len(mentions) or len(mentions) != len({code[1] for code in mentions})
                or not all(_MARKET_CODE_JOIN.match(prefix[left.end(1):right.start(1)])
                           for left, right in zip(mentions, mentions[1:]))):
            return []
        return [(code[1], field, prefix + text) for code, (field, text) in zip(mentions, items, strict=True)]

    prefix = fragment[:mentions[0].start(1)]
    boundaries = [code.start(1) for code in mentions[1:]] + [len(fragment)]
    bindings = []
    for code, end in zip(mentions, boundaries, strict=True):
        items = _market_metric_items(task, fragment[code.end(1):end], points=points)
        if not items:
            return []
        bindings.extend((code[1], field, prefix + text) for field, text in items)
    return bindings


def _same_day_enabled(task):
    return task.get(QUOTE_ENDPOINT_POLICY) == SAME_DAY_MARKET_SCOPE_VERSION


def resolve_same_day_market_clauses(task, clauses):
    """Resolve a literal same-day market lookup against its adjacent market clause."""
    if not _same_day_enabled(task) or not any(_SAME_DAY.search(clause) for clause in clauses):
        return clauses
    from .evidence import declared_security_codes, security_code_mentions
    from .index_sources import direct_weight_scope
    from .market_series import daily_market_as_of, series_family

    cutoff = date.fromisoformat(daily_market_as_of(task))
    declared = set(declared_security_codes(task))
    encoding = task.get('security_declaration_encoding', 1)
    seen = set()
    anchor = None
    result = []
    for clause in clauses:
        codes = set(security_code_mentions(clause, encoding=encoding))
        seen.update(codes)
        references = list(_SAME_DAY.finditer(clause))
        if anchor and len(references) == 1 and not _market_windows(task, clause):
            day, previous_codes = anchor
            reference, = references
            head = _SAME_DAY_READ_HEAD.sub('', clause[:reference.start()], count=1).strip()
            tail = clause[reference.end():]
            if not codes and len(seen) == 1 and seen == previous_codes and _SAME_DAY_IMPLICIT_SUBJECT.fullmatch(head):
                codes = previous_codes
                head = next(iter(codes))
            if codes and codes <= declared:
                if all(series_family(code) == 'ETF' for code in codes):
                    tail = _SAME_DAY_NAV_LABEL.sub(r'\1单位净值', tail)
                candidate = f'核对{head} {day.isoformat()}{tail}'
                literal = _literal_market_points(task, candidate)
                if literal and literal[0] == codes and literal[1] == {day}:
                    clause = candidate
        result.append(clause)
        literal = _literal_market_points(task, clause)
        anchor = None
        if literal and literal[0] <= declared and len(literal[1]) == 1:
            day, = literal[1]
            if day <= cutoff:
                anchor = day, literal[0]
        elif not references and (weight := direct_weight_scope(task, clause=clause)) and weight['day']:
            day = date.fromisoformat(weight['day'])
            if day <= cutoff:
                codes = {weight['code']}
                if codes <= declared:
                    seen.update(codes)
                    anchor = day, codes
    return result


def _split_market_queries(task, text, *, split_mixed):
    """Keep separate recent or premium lookups from inheriting another date."""
    from .market_series import _LATEST_MARKET_FIRST, _latest_clauses

    clauses = []
    for sentence in _MARKET_SENTENCE_BREAK.split(text):
        if not sentence.strip():
            continue
        separated = (_LATEST_MARKET_FIRST.search(sentence) or _DAILY_NAV_PREMIUM.search(sentence)
                     or _same_day_enabled(task) and _SAME_DAY.search(sentence))
        clauses.extend(_latest_clauses(sentence) if split_mixed and separated else [sentence])
    return clauses


def resolve_named_index_points(task, text):
    """Translate a fully specified dated index lookup to existing code-based grammar."""
    version = task.get(NAMED_INDEX_MARKET_POLICY)
    if type(version) is not int or version != NAMED_INDEX_MARKET_VERSION or '收盘' not in text:
        return text, {}
    from .draft_sources import _unquoted_local_scope
    from .index_sources import registered_market_index_names

    names = registered_market_index_names(task)
    if not names:
        return text, {}
    aliases = '|'.join(re.escape(alias) for alias in sorted(names, key=len, reverse=True))
    code = r'\d{6}\.(?:SH|SZ)'
    pattern = re.compile(
        rf'(?<![A-Za-z0-9])(?P<alias>{aliases})(?:指数)?'
        rf'(?:\s*[（(]\s*(?P<wrapped>{code})\s*[）)]'
        rf'|\s*(?P<bare>{code})(?:(?![A-Za-z0-9])|(?=\d{{4}}年))'
        rf'|(?:(?![A-Za-z0-9])|(?=\d{{4}}年)))')
    # A code can immediately follow a Chinese alias. It disambiguates the
    # identity only when it is the registered code for that exact index.
    masked = _unquoted_local_scope(text)
    matches = list(pattern.finditer(masked))
    if not matches:
        return text, {}
    resolved = {}
    conflicts = False
    normalized = text
    for match in reversed(matches):
        expected, label = names[match['alias']]
        explicit = match['wrapped'] or match['bare']
        if explicit and explicit != expected:
            conflicts = True
        normalized = normalized[:match.start()] + ' ' + expected + ' ' + normalized[match.end():]
        resolved[expected] = label
    fragment = _POINT_REPORT_TAIL.sub('', normalized.strip(' ，,；;。！？!?\n'))
    parsed = _literal_market_points(task, fragment)
    if not parsed or 'unit_nav' in parsed[2] or not set(resolved) <= parsed[0]:
        return text, {}
    return ('', {}) if conflicts else (normalized, resolved)


def named_index_point_subjects(task, text):
    """Expose registered identities only for matching dated index quote clauses."""
    if task.get(NAMED_INDEX_MARKET_POLICY) != NAMED_INDEX_MARKET_VERSION or '收盘' not in text:
        return {}
    from .draft_sources import _unquoted_local_scope
    from .market_series import _latest_clauses

    resolved = {}
    for clause in _latest_clauses(_unquoted_local_scope(text), valuation=True):
        _, labels = resolve_named_index_points(task, clause)
        resolved.update(labels)
    return resolved


def stated_latest_session_calculation(task, *, for_report=False):
    """Bind a literal market calculation to the latest completed session request."""
    version = task.get(LATEST_SESSION_CALCULATION_POLICY)
    text = task.get('question', '')
    if (type(version) is not int or version != LATEST_SESSION_CALCULATION_VERSION
            or '交易日' not in text or not ('最近' in text or '最新' in text)
            or task.get('research_mode') != 'current' or task.get('intent') not in ('综合研究', '比较对象')
            or task.get('materials') or task.get('pending_materials') or not enabled(task)):
        return None
    from .draft_sources import _NO_PDF, _unquoted_local_scope, local_scope_text, stated_access_restricted, stated_source_text
    from .market_series import (_LATEST_SESSION, _latest_question, observation_selection, only_public_etf_source,
                                series_family, stated_latest_cutoff)
    from .evidence import declared_security_codes
    from .subject_scope import scope_question

    question, selection = _latest_question(task), observation_selection(task)
    if not question or not selection or only_public_etf_source(task):
        return None
    text = _NO_PDF.sub('', stated_source_text(task, scope_question(task)))
    if for_report:
        text = local_scope_text(text)
    if stated_access_restricted(task, text, scoped_market=True, for_local_report=for_report):
        return None
    text = _POINT_REPORT_TAIL.sub('', _unquoted_local_scope(text).strip(' ，,；;。！？!?\n'))
    if len(list(re.finditer(_LATEST_SESSION, text))) != 1:
        return None
    if _market_windows(task, re.sub(_LATEST_SESSION, '', text)):
        return None
    # A date is filled here only to match the existing literal grammar. The
    # requested market day is subsequently selected from a real calendar.
    example = re.sub(_LATEST_SESSION, ' ' + market_date(task['cutoff']) + ' ', text)
    literal = _literal_market_points(task, _BARE_PREMIUM.sub('折溢价率', example))
    if not literal or len(literal[0]) != 1:
        return None
    (code,), _, fields = literal
    if code not in declared_security_codes(task) or code.rsplit('.',1)[-1] not in MARKETS:
        return None
    if not fields.intersection(('daily_return', 'daily_nav_premium')):
        return None
    if 'unit_nav' in fields and series_family(code) != 'ETF':
        return None
    last = date.fromisoformat(stated_latest_cutoff(task, code, 'close', completed_session=True))
    lookback = min(day for day in selection['lookback_calendar_days'] if day >= 14)
    first = last - timedelta(days=lookback-1)
    return dict(code=code, question_id=question['id'], fields=sorted(fields),
                lookback=lookback, first=first, last=last)


def latest_session_calculation_sessions(task, scope):
    calendars = [(record, details) for record, details in retained_calendars(task)
                 if scope['question_id'] in record.get('question_ids', ())]
    return verified_session_window(scope['code'], scope['first'], scope['last'], calendars)


def latest_session_calendar_requests(task, scope):
    params = dict(marketcode=MARKETS[scope['code'].rsplit('.',1)[-1]], functionpara=copy.deepcopy(FUNCTION),
                  startdate=scope['first'].isoformat(), enddate=scope['last'].isoformat())
    if _retains_calendar_window(task, params):
        return []
    return [dict(id='calendar_'+fingerprint(params)[:24], source='ifind',
                 operation='get_trade_dates', parameters=params, question_ids=[scope['question_id']],
                 selection_reason='按研究截止时点核对最近一个已经结束的交易日。')]


def resolve_latest_session_market_text(task, text):
    if '交易日' not in text or not ('最近' in text or '最新' in text):
        return text
    from .draft_sources import _NO_PDF
    from .market_series import _LATEST_SESSION

    scope = stated_latest_session_calculation(task, for_report=True)
    if scope and (sessions := latest_session_calculation_sessions(task, scope)):
        day = sessions[-1]
        dated = re.sub(_LATEST_SESSION, ' ' + day.isoformat() + ' ', _NO_PDF.sub('', text), count=1)
        return _POINT_REPORT_TAIL.sub('', _BARE_PREMIUM.sub('折溢价率', dated).strip(' ，,；;。！？!?\n'))
    return text


def _market_query_clauses(task, text, *, split_mixed):
    clauses = _split_market_queries(task, resolve_latest_session_market_text(task, text), split_mixed=split_mixed)
    return resolve_same_day_market_clauses(task, [resolve_named_index_points(task, part)[0] for part in clauses])


def stated_market_intervals(task, field, *, explicit=False, returns_only=False, pairs_only=False, source=None, clause=None):
    """Associate explicit daily ranges with the metric and subjects in their clauses."""
    from .draft_sources import _unquoted_local_scope
    from .evidence import declared_security_codes, security_code_matches, security_code_mentions
    from .market_series import (_EXPLICIT_NAV_EXCLUSION, _OTHER_NAV_MEASURE,
                                dated_nav_source_allowed, daily_market_as_of, nav_source_scope_enabled, series_family)
    from .subject_scope import scope_question, subject_key

    cutoff = date.fromisoformat(daily_market_as_of(task))
    declared = set(declared_security_codes(task))
    if not declared:
        return {}
    encoding = task.get('security_declaration_encoding', 1)
    version = task.get(QUOTE_ENDPOINT_POLICY)
    multi = version in MULTI_MARKET_DAY_VERSIONS
    daily_returns = version in DAILY_CLOSE_RETURN_VERSIONS and field == 'close'
    other_basis = _OTHER_QUOTE_BASIS if field == 'close' else _OTHER_NAV_MEASURE
    negation = _CLOSE_NEGATION if field == 'close' else _EXPLICIT_NAV_EXCLUSION
    qualifier = _QUOTE_QUALIFIER if field == 'close' else _NAV_QUALIFIER
    result = {}
    for question in task.get('questions', []):
        needs, scopes = question.get('evidence_needs', ()), question.get('evidence_need_scopes', {})
        if (not question.get('required', True) or question.get('answer_scope') == 'general_definition'
                or needs and all(scopes.get(need) in ('query_provenance', NEED_SCOPE) for need in needs)):
            continue
        original = question['question'] == task.get('question')
        if clause is not None and not original:
            continue
        text = (_market_question_dates(task, clause if clause is not None else scope_question(task))
                if original else question['question'])
        if daily_returns or multi:
            text = _unquoted_local_scope(text)
        if nav_source_scope_enabled(task):
            text = re.sub(r'(?:基金)?份额净值', '单位净值', text)
        question_codes = set(security_code_mentions(text, encoding=encoding)) or declared
        sentences = [(sentence, _market_windows(task, sentence))
                     for sentence in _market_query_clauses(task, text, split_mixed=multi)]
        # A separate basis or exclusion clause may qualify the whole question.
        excluded = set()
        for sentence, windows in sentences:
            rejected = (negation.search(sentence) or (qualifier.search(sentence) and other_basis.search(sentence))
                        or returns_only and _RETURN_NEGATION.search(sentence))
            if not rejected or windows:
                continue
            excluded.update(security_code_mentions(sentence, encoding=encoding) or question_codes)
        for sentence, windows in sentences:
            if not windows:
                continue
            if (multi and not (returns_only or pairs_only) and _DAILY_NAV_PREMIUM.search(sentence)
                    and (literal := _literal_market_points(task, sentence)) and 'daily_nav_premium' in literal[2]):
                if field != 'unit_nav' or dated_nav_source_allowed(task, sentence, source):
                    for code in (literal[0] & declared) - excluded:
                        result.setdefault(code, set()).update((day, day, question['id']) for day in literal[1])
                continue
            mentions = list(security_code_matches(sentence, encoding=encoding))
            sentence_codes = {match[1] for match in mentions} or question_codes
            for fragment in _market_window_fragments(
                    sentence, windows, mentions, join_points=multi,
                    split_dated_clauses=version in MARKET_DATE_CLAUSE_VERSIONS):
                local_mentions = list(security_code_matches(fragment, encoding=encoding))
                codes = {match[1] for match in local_mentions}
                if not codes and len(sentence_codes) == 1:
                    codes = sentence_codes
                if not codes or not codes <= declared:
                    continue
                fragment_windows = windows if fragment == sentence else _market_windows(task, fragment)
                points = bool(fragment_windows) and all(first == last for _, _, first, last in fragment_windows)
                pair = field == 'close' and _close_pair_requested(task, fragment, fragment_windows)
                rate_text = _CLOSE_PAIR_NEGATION.sub('', fragment) if version in LITERAL_MARKET_SCOPE_VERSIONS else fragment
                daily = (daily_returns and points and not pair and (multi or len(fragment_windows) == 1)
                         and _daily_return_requested(rate_text))
                if (returns_only and not daily) or (pairs_only and not pair):
                    continue
                bindings = (_bound_market_metrics(task, fragment, local_mentions, points=multi and points)
                            if len(fragment_windows) == 1 or multi and points else [])
                if bindings:
                    codes = {code for code, metric, clause in bindings if metric == field
                             and (not returns_only or _daily_return_requested(clause))
                             and _market_measure(clause, field, explicit=explicit, shared=False,
                                                 negation=negation, other_basis=other_basis,
                                                 daily_return=multi and daily and _daily_return_requested(clause))}
                elif not _market_measure(
                        fragment, field, explicit=explicit, shared=len(codes)>1,
                        negation=negation, other_basis=other_basis, daily_return=daily):
                    continue
                elif returns_only and len(codes) > 1:
                    # A common subject list applies to each code. An unbound
                    # sequence of separate clauses does not.
                    if (len(local_mentions) < 2 or any(not _MARKET_SUBJECT_LIST.fullmatch(fragment[left.end(1):right.start(1)])
                            for left, right in zip(local_mentions, local_mentions[1:]))
                            or re.search(r'前者|后者|其中', fragment)):
                        continue
                codes = codes - excluded
                if field == 'unit_nav':
                    if not dated_nav_source_allowed(task, fragment, source):
                        continue
                    etfs = {code for code in codes if series_family(code) == 'ETF'}
                    if not bindings and etfs != codes:
                        continue
                    codes = etfs
                if pairs_only:
                    first, last = sorted(window[2] for window in fragment_windows)
                    for code in codes:
                        result.setdefault(subject_key(code).upper(), set()).add((first, last, question['id']))
                    continue
                for _, _, first, last in fragment_windows:
                    point = (version in EXACT_MARKET_DAY_VERSIONS and first == last
                             and (daily or pair or field in _market_date_fields(fragment)))
                    if point or first < last <= cutoff:
                        for code in codes:
                            key = subject_key(code).upper() if field == 'close' else code
                            result.setdefault(key, set()).add((first, last, question['id']))
    return result


def stated_close_pairs(task, *, clause=None):
    """Return the two literal endpoint dates for each requested close comparison."""
    if (task.get(QUOTE_ENDPOINT_POLICY) not in LITERAL_MARKET_SCOPE_VERSIONS
            or task.get('intent') == DOCUMENT_CONTENT_INTENT):
        return {}
    return stated_market_intervals(task, 'close', explicit=True, pairs_only=True, clause=clause)


def stated_close_returns(task, *, clause=None):
    """Attach daily return only to an explicit, question-bound close date."""
    if task.get(QUOTE_ENDPOINT_POLICY) not in DAILY_CLOSE_RETURN_VERSIONS or task.get('intent') == DOCUMENT_CONTENT_INTENT:
        return set()
    scopes = stated_market_intervals(task, 'close', explicit=True, returns_only=True, clause=clause)
    return {(code, day, question) for code, windows in scopes.items() for day, _, question in windows}


def stated_market_points(task, *, clause=None):
    from .market_series import stated_nav_intervals

    if task.get(QUOTE_ENDPOINT_POLICY) not in MULTI_MARKET_DAY_VERSIONS:
        return set()
    return {(code, day, field, ident)
            for field, scopes in (('close', stated_close_intervals(task, clause=clause)),
                                  ('unit_nav', stated_nav_intervals(task, clause=clause)))
            for code, windows in scopes.items() for day, end, ident in windows if day == end}


def stated_daily_nav_premiums(task, *, clause=None):
    from .draft_sources import _unquoted_local_scope
    from .subject_scope import scope_question

    requested = set()
    for question in task.get('questions', []):
        if not question.get('required', True) or question.get('answer_scope') == 'general_definition':
            continue
        original = question['question'] == task['question']
        if clause is not None and not original:
            continue
        text = (scope_question(task) if clause is None else clause) if original else question['question']
        for part in _market_query_clauses(task, _unquoted_local_scope(text), split_mixed=True):
            if not _DAILY_NAV_PREMIUM.search(part) or not (literal := _literal_market_points(task, part)):
                continue
            codes, days, fields = literal
            if 'daily_nav_premium' in fields:
                requested.update((code, day, question['id']) for code in codes for day in days)
    return requested


def stated_market_calculations(task, *, clause=None):
    """Identify the exact dates, security and question behind each calculation."""
    daily = {(code, day, day, 'daily_return', qid)
             for code, day, qid in stated_close_returns(task, clause=clause)}
    intervals = {(code, start, end, 'close_pair_return', qid)
                 for code, pairs in stated_close_pairs(task, clause=clause).items()
                 for start, end, qid in pairs}
    premiums = {(code, day, day, 'daily_nav_premium', qid)
                for code, day, qid in stated_daily_nav_premiums(task, clause=clause)}
    return daily | intervals | premiums


def close_return_previous_session(code, day, calendars):
    if day == date.min:
        return None
    return previous_session_dates(code, _return_calendar_start(day), day,
                                  calendars, observed_end=True)


def _return_calendar_start(day):
    return date.fromordinal(max(1, day.toordinal()-POINT_RETURN_LOOKBACK))


def close_return_predecessors(task, record, calendars, ranges):
    """Date-indexed calculation dependencies kept outside the displayed window."""
    if not ranges or calendars is None:
        return {}
    linked = record.get('question_ids') or ()
    result = {}
    for code, day, question in sorted(stated_close_returns(task)):
        if (linked and question not in linked or (code, 'close') not in ranges
                or not any(first <= day <= last for first, last in ranges[code, 'close'])):
            continue
        resolved = close_return_previous_session(code, day, calendars)
        if resolved:
            (previous, target), _ = resolved
            result.setdefault(code, {})[date.fromisoformat(previous)] = target
    return result


def close_return_calendar_requests(task):
    from .draft_sources import stated_access_restricted
    from .economic_sources import _merged_date_windows
    from .market_series import daily_market_as_of

    if (not enabled(task) or task.get('research_mode') not in ('current', 'historical')
            or stated_access_restricted(task, task['question'], scoped_market=True)):
        return []
    targets = stated_close_returns(task)
    if not targets:
        return []
    calendars = retained_calendars(task)
    questions = {row['id']: row['question'] for row in task['questions']}
    windows = {}
    for code, day, question in sorted(targets):
        market = MARKETS.get(code.rsplit('.',1)[-1])
        if (market is None or day.isoformat() > daily_market_as_of(task)
                or stated_access_restricted(task, questions[question], scoped_market=True)
                or verified_session_window(code,day,day,calendars) == ()):
            continue
        resolved = close_return_previous_session(code,day,calendars)
        if resolved and resolved[1]['kind']=='exchange_calendar':
            continue
        windows.setdefault(market, {}).setdefault((_return_calendar_start(day), day), set()).add(question)
    requests = []
    for market, requested in windows.items():
        merged = _merged_date_windows(requested) if task.get(QUOTE_ENDPOINT_POLICY) in MULTI_MARKET_DAY_VERSIONS else requested
        for start, end in merged:
            params = dict(marketcode=market, functionpara=copy.deepcopy(FUNCTION),
                          startdate=start.isoformat(), enddate=end.isoformat())
            if _retains_calendar_window(task, params):
                continue
            question_ids = sorted({question for (first,last), ids in requested.items() if start<=first<=last<=end
                                   for question in ids})
            requests.append(dict(id='calendar_'+fingerprint(params)[:24], source='ifind', operation='get_trade_dates',
                parameters=params, question_ids=question_ids, selection_reason='核对指定日及其上一交易日。'))
    return requests


def stated_interval_calendar_requests(task, question_id):
    """Verify explicitly stated closing windows even when their quotes were reused."""
    from .draft_sources import stated_access_restricted
    from .market_series import daily_market_as_of

    if not enabled(task) or stated_access_restricted(task, task['question'], scoped_market=True):
        return []
    calendars = retained_calendars(task)
    windows = set()
    for code, intervals in stated_close_intervals(task, explicit=True).items():
        market = MARKETS.get(code.rsplit('.', 1)[-1])
        if market is None:
            continue
        for start, end, ident in intervals:
            if (ident == question_id and start < end and end.isoformat() <= daily_market_as_of(task)
                    and verified_session_window(code, start, end, calendars) is None):
                windows.add((market, start, end))
    requests = []
    for market, start, end in sorted(windows):
        params = dict(marketcode=market, functionpara=copy.deepcopy(FUNCTION),
                      startdate=start.isoformat(), enddate=end.isoformat())
        requests.append(dict(id='calendar_'+fingerprint(params)[:24], source='ifind', operation='get_trade_dates',
                             parameters=params, question_ids=[question_id],
                             selection_reason='核对指定收盘区间两地交易日，不以行情缺日代替休市。'))
    return requests


def fund_share_requested(task):
    from .subject_scope import scope_question

    from .market_series import _SSE_PCF, nav_source_scope_enabled
    text = '\n'.join([scope_question(task), *(row['question'] for row in task.get('questions', []))])
    if nav_source_scope_enabled(task):
        text = re.sub(r'(?:基金)?份额净值', '单位净值', re.sub(_SSE_PCF, '', text, flags=re.I))
    return bool(re.search(r'份额|申赎|申购|赎回|基金规模|ths_fund_shares_fund', text))


def market_question_ranges(task, record):
    """Select dated cells for the questions served by this response."""
    from .market_series import NAV_INDICATOR, nav_source_scope_enabled, stated_nav_intervals
    from .subject_scope import subject_key

    version = task.get(QUOTE_ENDPOINT_POLICY)
    if type(version) is not int or version not in (6, 7, *EXACT_MARKET_DAY_VERSIONS):
        return {}
    question_ids = record.get('question_ids') or ()
    if not isinstance(question_ids, (list, tuple)) or not all(isinstance(ident, str) for ident in question_ids):
        return {}
    linked = set(question_ids)
    result = {}
    nav_windows = stated_nav_intervals(task)
    source_nav_windows = (stated_nav_intervals(task, source=record.get('source'))
                          if nav_windows and nav_source_scope_enabled(task) else nav_windows)
    for field, scopes in (('close', stated_close_intervals(task)), (NAV_INDICATOR, nav_windows)):
        for code, intervals in scopes.items():
            # A response may also serve another question with a different or
            # unstated window. Keep its independently requested observations.
            if linked and not linked <= {ident for _, _, ident in intervals}:
                continue
            selected = source_nav_windows.get(code, ()) if field == NAV_INDICATOR else intervals
            code = subject_key(code).upper()
            result[code, field] = tuple((start, end) for start, end, ident in sorted(selected)
                                        if not linked or ident in linked)
            if field == NAV_INDICATOR:
                result[code, 'unit_nav'] = result[code, field]
    if (record.get('source'), record.get('operation')) == ('ifind', 'cmd_history_quotation'):
        for (code, field), intervals in stated_liquidity_intervals(task).items():
            selected = [(first, last) for first, last, ident in sorted(intervals) if not linked or ident in linked]
            if selected:
                key = subject_key(code).upper(), field
                result[key] = tuple(dict.fromkeys((*result.get(key, ()), *selected)))
    if version in MULTI_MARKET_DAY_VERSIONS:
        from .subject_scope import scope_question

        main = {row['id'] for row in task.get('questions', []) if row['question'] == task['question']}
        literal = _literal_market_points(task, resolve_latest_session_market_text(task, scope_question(task)))
        if literal is not None and (not linked or linked <= main):
            excluded_by_default = ('close', 'unit_nav', NAV_INDICATOR, 'ths_fund_shares_fund')
            if version in LITERAL_MARKET_SCOPE_VERSIONS:
                from .evidence import FIELD_REGISTRY

                operation = f"{record.get('source')}.{record.get('operation')}"
                if operation in ('ifind.cmd_history_quotation', 'tinyshare.daily', 'tinyshare.fund_daily'):
                    excluded_by_default += tuple(FIELD_REGISTRY.get(operation, {}).get('fields', {}))
            for code in literal[0]:
                for field in excluded_by_default:
                    result.setdefault((subject_key(code).upper(), field), ())
    if market_date_reference(task) == {}:
        from .evidence import declared_security_codes
        from .subject_scope import scope_question

        text = scope_question(task)
        questions = {row['id'] for row in task.get('questions', []) if row['question'] == task['question']}
        if not linked or linked <= questions:
            fields = _market_date_fields(text)
            for code in declared_security_codes(task):
                code = subject_key(code).upper()
                for field in fields:
                    result[code, field] = ()
                if 'unit_nav' in fields:
                    result[code, NAV_INDICATOR] = ()
    if any(field == NAV_INDICATOR for _, field in result) and not fund_share_requested(task):
        for code, field in tuple(result):
            if field == NAV_INDICATOR:
                result[code, 'ths_fund_shares_fund'] = ()
    return result


def market_date_ranges(ranges, code, field):
    from .subject_scope import subject_key

    return ranges.get((subject_key(code).upper(), field)) if ranges and isinstance(code, str) else None


def _add_stated_intervals(result, sentence, codes, encoding, cutoff, question_id, *, match_measure):
    from .evidence import security_code_matches
    from .subject_scope import subject_key

    mentions = [match for match in security_code_matches(sentence, encoding=encoding) if match[1] in codes]
    windows = stated_day_intervals(sentence)
    match_measure = match_measure and _OTHER_MEASURE.search(sentence) is not None
    active, boundary = {match[1] for match in mentions} or codes, -1
    for index, (offset, end, first, last) in enumerate(windows):
        new_subjects = {match[1] for match in mentions if boundary < match.start(1) and match.end(1) <= offset}
        if new_subjects:
            active = new_subjects
        boundary = offset
        if match_measure:
            prior_end = windows[index-1][1] if index else 0
            next_start = windows[index+1][0] if index+1 < len(windows) else len(sentence)
            # A metric after the range normally labels it. A nearby metric
            # before it also works, for example “单位净值在…的涨跌幅”.
            measure = next((value for fragment in (sentence[end:next_start], sentence[prior_end:offset], sentence)
                            if (value := _stated_measure(fragment)) is not None), None)
            if measure is False:
                continue
        if first < last <= cutoff:
            for code in active:
                result.setdefault(subject_key(code).upper(), set()).add((first,last,question_id))


def _stated_measure(text):
    if _CLOSE_MEASURE.search(text):
        return True
    if _OTHER_MEASURE.search(text):
        return False
    return None


def scope_basis(task):
    """Verified technical metadata, never scalar financial-evidence approval.

    Coverage can satisfy only explicitly typed calendar needs. There is no
    keyword classifier and no migration of a previously adopted need type.
    """
    if type(task.get(BASIS_POLICY)) is not int or task[BASIS_POLICY] != 1 or not enabled(task):
        return None
    from .evidence import current_query_reference
    panels = copy.deepcopy(coverage(task)['panels'])
    records = {record['id']: record for record in task.get('source_records', [])}
    def metadata(ident):
        record = records.get(ident)
        if record is None or current_query_reference(task, record) is None:
            return None
        return {key: copy.deepcopy(record[key]) for key in
            ('id', 'source', 'operation', 'parameters', 'request_hash', 'hash', 'task_id', 'research_version')}
    for panel in panels:
        panel['query_source'] = metadata(panel['artifact_id'])
        reference = panel['calendar_ref']
        panel['calendar_source'] = metadata(reference['artifact_id']) if reference else None
        if panel['calendar_source']:
            record = records[reference['artifact_id']]
            panel['calendar_original_dates'] = sessions(record['parameters'], record['snapshot'])['dates']
    covered = {}
    for question in task['questions']:
        related = [panel for panel in panels if question['id'] in panel['question_ids']]
        valid = bool(related) and all(panel['query_source'] and panel['calendar_source']
            and panel['status'] in ('日历覆盖一致', '存在未观测交易日', '存在非日历日期', '日历明确无交易日')
            for panel in related)
        covered[question['id']] = sorted(fingerprint(text) for text, scope in
            question.get('evidence_need_scopes', {}).items() if scope == NEED_SCOPE) if valid else []
    return dict(version=1, panels=panels, covered_need_ids=covered, financial_approval=False,
        rule='仅证明本次实际取数的来源、窗口、独立日历原日期及与价格日期的比对。明确登记market_calendar_coverage的需求才由此覆盖；异常或缺数必须照实解释。不能替代价格、净值、收益、主体事实、证券成交状态或历史可得性。iFinD日历不因未另下交易所PDF就成为缺失，但不冒充交易所网站原件。')


def covered_need_ids(task, question):
    basis = scope_basis(task)
    return basis['covered_need_ids'].get(question['id'], []) if basis else []


def markets_for(task):
    from .event_market import market_scopes, verified_event_sessions_enabled
    from .evidence import available_security_codes

    codes = available_security_codes(task)
    if verified_event_sessions_enabled(task) and (scope := market_scopes(task).get('mainland')):
        codes = [*codes, *scope['codes']]
    return sorted({MARKETS[code.rsplit('.', 1)[-1]] for code in codes
                   if re.fullmatch(r'(?:\d{6}\.(?:SH|SZ)|\d{4,5}\.HK)', code)})


def validate_request(task, parameters):
    if not enabled(task):
        raise ContractError('本任务未登记独立交易日历，不自动升级旧任务')
    return _validate_window(parameters, task['cutoff'], markets_for(task))


def _validate_window(parameters, cutoff, markets):
    if (not isinstance(parameters, dict)
            or set(parameters) != {'marketcode', 'functionpara', 'startdate', 'enddate'}
            or parameters['marketcode'] not in markets
            or parameters['functionpara'] != FUNCTION):
        raise ContractError('日历须属于本任务明确市场，保持独立日度交易日口径，不取价格或偏移日期')
    dates = []
    for key in ('startdate', 'enddate'):
        value = parameters[key]
        try:
            if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        except ValueError:
            raise ContractError('交易日历窗口须为YYYY-MM-DD，不修复日期') from None
        dates.append(value)
    if dates[0] > dates[1] or dates[1] > market_date(cutoff):
        raise ContractError('交易日历窗口倒置或越过截止日，不自动扩展')
    return parameters


def request_shapes(task):
    if not enabled(task) or not markets_for(task):
        return []
    return [dict(qualified_id_for_lookup_only='ifind.get_trade_dates', source='ifind',
        operation='get_trade_dates', parameter_keys=['marketcode', 'functionpara', 'startdate', 'enddate'],
        parameter_options={'marketcode': markets_for(task)}, fixed_parameters={'functionpara': copy.deepcopy(FUNCTION)},
        numeric_fields=[], raw_only=True, date_format='YYYY-MM-DD', example=None,
        coverage_role='独立核对交易所交易日，不用行情返回日期、工作日或其他标的日期代替；多日行情由内核按需补取并计原预算。',
        limitations='日历只核对已登记交易所的开市日期，证券上市和停牌须另查。')]


def sessions(parameters, payload):
    """One unambiguous explicit date column, including an explicit empty one."""
    if (not isinstance(payload, dict) or type(payload.get('errorcode')) is bool
            or payload.get('errorcode') not in (0, '0')):
        raise ContractError('交易日历业务响应未成功')
    echoed = payload.get('inputParams', payload.get('inputparams', {}))
    if isinstance(echoed, dict):
        market = echoed.get('marketcode', echoed.get('marketCode'))
        if market is not None and str(market) not in (parameters['marketcode'], EXCHANGES[parameters['marketcode']]):
            raise ContractError('交易日历返回市场与请求不符')
    columns = []

    def column(parent, path):
        if isinstance(parent, dict):
            for field in ('sequencedate', 'sequenceDate', 'date', 'time'):
                if field in parent:
                    values = parent[field]
                    if not isinstance(values, list):
                        raise ContractError('交易日历日期列须保留数组结构')
                    columns.append((path + [field], values))

    tables = payload.get('tables')
    if isinstance(tables, dict):
        tables = [(tables, ['tables'])]
    elif isinstance(tables, list):
        tables = [(table, ['tables', index]) for index, table in enumerate(tables)]
    else:
        tables = []
    for table, path in tables:
        if not isinstance(table, dict):
            raise ContractError('交易日历表结构异常')
        for values in (table.get('table'), table.get('data')):
            if isinstance(values, dict) and {'open', 'high', 'low', 'close', 'volume', 'amount'}.intersection(values):
                raise ContractError('行情日期列不是独立日历，不接受价格表冒充日历响应')
        column(table, path)
        column(table.get('table'), path + ['table'])
        column(table.get('data'), path + ['data'])
    column(payload.get('data'), ['data'])
    if not columns or len(columns) > 16:
        raise ContractError('交易日历缺少明确日期列，不把空表当作无交易日')
    # Repeated layouts must agree, not concatenate dates or silently prefer
    # a convenient column. Original paths remain available for inspection.
    values = columns[0][1]
    if any(value != values for _, value in columns):
        raise ContractError('交易日历多个日期列冲突，不能择一或合并')
    if len(values) > 100000:
        raise ContractError('交易日历超过有界响应容量，不截断后核验')
    for value in values:
        try:
            valid = (isinstance(value, str) and date.fromisoformat(value).isoformat() == value
                     and parameters['startdate'] <= value <= parameters['enddate'])
        except ValueError:
            valid = False
        if not valid:
            raise ContractError('交易日须为窗口内的精确日期，不截时刻、去空白或补工作日')
    if values != sorted(set(values)):
        raise ContractError('交易日须严格递增且不重复，不排序修复原始响应')
    return dict(exchange=EXCHANGES[parameters['marketcode']], dates=copy.deepcopy(values),
        column_paths=[path for path, _ in columns], explicit_empty=not values)


def dependency_requests(task, prepared):
    """Check the exchange calendar after acquiring market data."""
    if not enabled(task):
        return []
    from .evidence import current_query_reference
    from .market_series import (NAV_INDICATOR, daily_market_as_of, exact_session_nav, observation_selection,
                               stated_latest_cutoff, stated_series_session_scope)
    from .subject_scope import subject_key
    declared_windows = stated_close_intervals(task)
    return_dates = {(code, day.isoformat(), question) for code, day, question in stated_close_returns(task)}
    result = {}
    for need, source, operation, parameters in prepared:
        is_pcf = (source, operation) == ('official', 'etf_pcf')
        is_quote = (source, operation) == ('ifind', 'cmd_history_quotation')
        scope = None if is_quote else stated_series_session_scope(task, need)
        if not (is_quote or scope):
            continue
        request_hash = fingerprint(dict(source=source, operation=operation, parameters=parameters))
        record = next((item for item in task.get('source_records', [])
                       if item.get('request_hash') == request_hash), None)
        if record is None or current_query_reference(task, record) is None:
            continue
        codes = [parameters['ts_code']] if is_pcf else parameters['codes'].split(',')
        for code in codes:
            market = MARKETS.get(code.rsplit('.', 1)[-1])
            if market is None:
                continue  # Never infer an unregistered market calendar.
            if is_pcf:
                upper = stated_latest_cutoff(task, code, NAV_INDICATOR)
                lookback = observation_selection(task)['lookback_calendar_days'][-1]
                lower = (date.fromisoformat(upper)-timedelta(days=lookback-1)).isoformat()
            else:
                lower, upper = parameters['startdate'], parameters['enddate']
            if scope and exact_session_nav(scope):
                upper = stated_latest_cutoff(task, code, NAV_INDICATOR, completed_session=True)
            declared = sorted((first.isoformat(),last.isoformat())
                for first,last,question in declared_windows.get(subject_key(code).upper(), ())
                if question in need['question_ids'] and first.isoformat() <= upper and lower <= last.isoformat()
                and operation == 'cmd_history_quotation' and 'close' in parameters['indicators'].split(',')
                and not need.get('observation_scope'))
            bounds = declared or ([(lower,upper)] if lower != upper else [])
            if is_quote:
                public_code = subject_key(code).upper()
                # An explicit daily return has its own preceding-session lookup.
                bounds = [(first, last) for first, last in bounds if first != last or not any(
                    (public_code, last, ident) in return_dates for ident in need['question_ids'])]
                if bounds and all(first == last for first, last in bounds) and declared:
                    # A short sequence of requested dates needs one exchange
                    # calendar across the gaps, including any intervening weekend.
                    points = {first for first, last, question in declared_windows.get(public_code, ())
                              if first == last and question in need['question_ids']
                              and (public_code, first.isoformat(), question) not in return_dates}
                    if len(points) >= 3:
                        first, last = min(points), max(points)
                        if last <= date.fromisoformat(daily_market_as_of(task)) and (last-first).days <= 31:
                            bounds = [(first.isoformat(), last.isoformat())]
                if task.get(QUOTE_ENDPOINT_POLICY) in LITERAL_MARKET_SCOPE_VERSIONS and len(bounds) > 1:
                    from .economic_sources import _merged_date_windows

                    windows = _merged_date_windows((date.fromisoformat(first), date.fromisoformat(last))
                                                   for first, last in bounds)
                    bounds = [(first.isoformat(), last.isoformat()) for first, last in windows]
            for lower, upper in bounds:
                params = dict(marketcode=market, functionpara=copy.deepcopy(FUNCTION),
                    startdate=lower, enddate=upper)
                identity = fingerprint(params)
                if identity not in result:
                    result[identity] = dict(id='calendar_' + identity[:24], source='ifind',
                        operation='get_trade_dates', parameters=params, question_ids=[],
                        selection_reason='核对本任务行情区间的交易所日期覆盖；不增加研究对象、预测或价格字段。')
                result[identity]['question_ids'] = list(dict.fromkeys(
                    result[identity]['question_ids'] + need['question_ids']))
    return [need for need in result.values() if not _retains_calendar_window(task, need['parameters'])]


def _retains_calendar_window(task, parameters):
    from .evidence import calendar_query_reference

    for calendar in task.get('source_records', []):
        if (calendar.get('source') != 'ifind' or calendar.get('operation') != 'get_trade_dates'
                or calendar_query_reference(task, calendar) is None):
            continue
        existing = calendar['parameters']
        if (existing['marketcode'] == parameters['marketcode'] and existing['startdate'] <= parameters['startdate']
                and existing['enddate'] >= parameters['enddate']):
            try:
                validate_request(task, existing)
                sessions(existing, calendar['snapshot'])
                return True
            except ContractError:
                continue
    return False


def retained_calendars(task):
    """Read independently acquired calendar windows without dispatching requests."""
    if not enabled(task):
        return []
    from .evidence import calendar_query_reference
    calendars = []
    for item in task.get('source_records', []):
        if item.get('source') != 'ifind' or item.get('operation') != 'get_trade_dates':
            continue
        try:
            if calendar_query_reference(task, item) is not None:
                validate_request(task, item['parameters'])
                calendars.append((item, sessions(item['parameters'], item['snapshot'])))
        except (ContractError, KeyError, TypeError, ValueError):
            continue
    return calendars


CALENDAR_EVENT_FIELDS = ('kind', 'source', 'operation', 'fingerprint', 'batch', 'status',
    'response_snapshot_hash', 'response_obtained_at', 'transport_isolated', 'network_request_started',
    'network_worker_reaped')


def freeze_calendar_replay(task):
    """Freeze the minimum verified transport fields needed to replay calendar evidence."""
    events = {}
    for record, _ in retained_calendars(task):
        index = record['acquisition']['dispatch_index']
        event = task['ledger']['dispatches'][index]
        events[record['id']] = dict(dispatch_index=index, event={key:event[key] for key in CALENDAR_EVENT_FIELDS})
    return dict(version=1, task_id=task['id'], research_version=task['version'], input_hash=task['input_hash'],
        cutoff=task['cutoff'], research_mode=task['research_mode'], created_at=task['created_at'],
        markets=markets_for(task), events=events) if events else None


def replay_calendar_windows(content):
    """Verify the same queries from a detached report, with no access to the Store."""
    from .evidence import calendar_query_reference

    replay = content.get('calendar_replay')
    keys = ('task_id', 'research_version', 'input_hash', 'cutoff')
    if (not isinstance(replay, dict) or type(replay.get('version')) is not int or replay['version'] != 1
            or any(replay.get(key) != content.get(key) for key in keys)
            or not isinstance(replay.get('markets'), list) or not isinstance(replay.get('events'), dict)):
        return []
    task = dict(id=content['task_id'], version=content['research_version'], intent=content.get('intent'),
        research_mode=replay.get('research_mode'), created_at=replay.get('created_at'))
    calendars = []
    sources = content.get('source_records', [])
    counts = Counter(record.get('id') for record in sources)
    for record in sources:
        item = replay['events'].get(record.get('id'))
        if not isinstance(item, dict):
            continue
        try:
            event, receipt = item['event'], record['acquisition']
            if (not isinstance(event, dict) or set(event) != set(CALENDAR_EVENT_FIELDS)
                    or not isinstance(receipt, dict) or type(receipt.get('dispatch_index')) is not int
                    or type(item.get('dispatch_index')) is not int
                    or item['dispatch_index'] != receipt['dispatch_index']
                    or counts[record['id']] != 1):
                continue
            # Verify the original record against its one retained event in a
            # detached ledger. Index zero belongs only to this replay view.
            task['ledger'] = dict(dispatches=[event])
            detached = dict(record, acquisition=dict(receipt, dispatch_index=0))
            if calendar_query_reference(task, detached) is not None:
                _validate_window(record['parameters'], replay['cutoff'], replay['markets'])
                calendars.append((record, sessions(record['parameters'], record['snapshot'])))
        except (ContractError, KeyError, TypeError, ValueError):
            continue
    return calendars


def verified_session_window(code, start, end, calendars):
    """Require one full calendar window and agreement on all overlaps."""
    low, high = start.isoformat(), end.isoformat()
    market = MARKETS.get(code.rsplit('.', 1)[-1])
    windows = [(record['parameters'], details['dates']) for record, details in calendars
               if record['parameters']['marketcode'] == market]
    full = next((days for params, days in windows if params['startdate'] <= low and params['enddate'] >= high), None)
    if full is None:
        return None
    expected = tuple(day for day in full if low <= day <= high)
    for params, days in windows:
        first, last = max(low, params['startdate']), min(high, params['enddate'])
        if first <= last and tuple(day for day in days if first <= day <= last) != tuple(
                day for day in expected if first <= day <= last):
            return None
    return tuple(date.fromisoformat(day) for day in expected)


def stated_close_coverage(task, requests, records, facts, *, calendars=None):
    """Compare the stated dates with both queried windows and actual closes."""
    scopes = stated_close_intervals(task)
    if not scopes:
        return []
    from .evidence import validate_market_request
    from .market_series import daily_market_as_of, latest_observation_selected
    from .subject_scope import subject_key
    from .draft_sources import _sequence_day
    check_sessions = stated_session_gaps_enabled(task)

    def eligible(row):
        source, operation = row.get('source'), row.get('operation')
        if (row.get('observation_scope')
                or (row.get('observation_requests') and latest_observation_selected(task, row))):
            return ()
        params = row.get('parameters', {})
        if (source, operation) == ('ifind', 'cmd_history_quotation'):
            codes, fields = params.get('codes'), params.get('indicators')
        elif check_sessions and source == 'tinyshare' and operation in ('daily', 'fund_daily'):
            codes, fields = params.get('ts_code'), params.get('fields')
        else:
            return ()
        if not isinstance(fields,str) or 'close' not in fields.split(','):
            return ()
        try:
            dates = tuple(map(date.fromisoformat, validate_market_request(task, source, operation, params)))
        except (ContractError, ValueError, TypeError):
            return ()
        return [(subject_key(code).upper(), question_id, *dates) for code in codes.split(',')
                for question_id in (row.get('question_ids') or ())]

    queried, observed = {}, {}
    record_scopes = {ident: eligible(row) for ident, row in records.items()}
    for matches in chain(map(eligible, requests), record_scopes.values()):
        for code, question_id, first, last in matches:
            if code in scopes:
                queried.setdefault((code,question_id), set()).add((first,last))
    if check_sessions:
        for row in facts:
            matches = record_scopes.get(row['artifact_id'])
            if not matches or row['field'] != 'close' or row.get('value') is None or row.get('previous_close_for'):
                continue
            code, day = subject_key(row['code']).upper(), _sequence_day(row['period'])
            if day is None:
                continue
            for candidate, question_id, first, last in matches:
                if candidate == code and first <= day <= last:
                    observed.setdefault((code,question_id), set()).add(day)
    from .economic_sources import _merged_date_windows, unqueried_date_windows
    if calendars is None:
        calendars = retained_calendars(task)
    return_dates = stated_close_returns(task)
    daily_cutoff = date.fromisoformat(daily_market_as_of(task)) if return_dates else None
    coverage = []
    for code, windows in sorted(scopes.items()):
        for start, end, question_id in sorted(windows):
            queries = _merged_date_windows(queried.get((code,question_id), ()))
            unqueried = unqueried_date_windows(start, end, queries)
            days = verified_session_window(code, start, end, calendars)
            if days is not None:
                unqueried = [(first,last) for first,last in unqueried if any(first<=day<=last for day in days)]
            dates = [day.isoformat() for day in days or () if any(first<=day<=last for first,last in unqueried)]
            if (start == end and (code, start, question_id) in return_dates
                    and end <= daily_cutoff and days != ()):
                resolved = close_return_previous_session(code, end, calendars)
                if resolved:
                    (base, _), proof = resolved
                    previous = date.fromisoformat(base)
                    if not any(first <= previous <= last for first, last in queries):
                        # One bounded price request can retrieve both dates.
                        unqueried = [(previous, end if unqueried else previous)]
                        if proof['kind'] == 'exchange_calendar':
                            dates.insert(0, base)
            item = dict(code=code, question_id=question_id, startdate=start.isoformat(), enddate=end.isoformat(),
                unqueried_windows=[dict(startdate=first.isoformat(),enddate=last.isoformat()) for first,last in unqueried],
                unqueried_session_dates=dates[:12])
            if check_sessions and days is not None:
                closes = observed.get((code,question_id), ())
                missing = [day.isoformat() for day in days if day not in closes]
                item.update(missing_session_dates=missing[:12], missing_session_count=len(missing))
            coverage.append(item)
    return coverage


def previous_session_dates(code, start, end, calendars, *, observed_end=False):
    """Resolve a pair inside a request window or ending at an actual close."""
    start_day, end_day = start.isoformat(), end.isoformat()
    windows, matching = [], []
    market = MARKETS.get(code.rsplit('.', 1)[-1])
    for record, details in calendars:
        params = record['parameters']
        if params['marketcode'] != market:
            continue
        windows.append((params, details['dates']))
        if params['enddate'] >= end_day:
            sessions = tuple(day for day in details['dates'] if start_day <= day <= end_day)
            if len(sessions) >= 2:
                matching.append((record['id'], sessions[-2:]))
    if matching:
        pair = matching[0][1]
        if (observed_end and pair[-1] != end_day) or any(other != pair for _, other in matching):
            return None
        proof = dict(kind='exchange_calendar', source_refs=[ident for ident, _ in matching])
    else:
        # Actual consecutive calendar dates cannot hide a session between them.
        pair = ((end - timedelta(days=1)).isoformat(), end_day)
        proof = dict(kind='consecutive_observed_dates', source_refs=[])
    for params, sessions in windows:
        low, high = max(pair[0], params['startdate']), min(end_day, params['enddate'])
        if low <= high and tuple(day for day in sessions if low <= day <= high) != tuple(
                day for day in pair if low <= day <= high):
            return None
    return pair, proof


def coverage(task):
    """Task-bound coverage diagnostics, separate from financial approval."""
    if not enabled(task):
        return None
    from .evidence import current_query_reference
    from .draft_sources import bound_quote_days, market_cell_binding_enabled
    records = task.get('source_records', [])
    calendars = retained_calendars(task)
    panels = []
    for record in records:
        if (record.get('source') != 'ifind' or record.get('operation') != 'cmd_history_quotation'
                or current_query_reference(task, record) is None):
            continue
        params = record['parameters']
        if params['startdate'] == params['enddate']:
            continue
        tables = record['snapshot'].get('tables')
        if not isinstance(tables, list):
            continue
        bound_dates = bound_quote_days(task, record) if market_cell_binding_enabled(task) else None
        for code in params['codes'].split(','):
            item = dict(artifact_id=record['id'], snapshot_hash=record['hash'], object=code,
                start_date=params['startdate'], end_date=params['enddate'],
                question_ids=list(record.get('question_ids', [])), status='日历未核验',
                calendar_ref=None, missing_dates=[], unexpected_dates=[], observed_dates=[],
                calendar_dates=[], financial_approval=False, historical_availability_approved=False)
            matching = [(calendar, details) for calendar, details in calendars
                if calendar['parameters']['marketcode'] == MARKETS.get(code.rsplit('.', 1)[-1])
                and calendar['parameters']['startdate'] <= params['startdate']
                and calendar['parameters']['enddate'] >= params['enddate']]
            try:
                if bound_dates is not None:
                    if not bound_dates.get(code):
                        raise ContractError('行情没有可核对的收盘单元')
                    dates = sorted(bound_dates[code])
                    observed = [day for day in dates if bound_dates[code][day]]
                else:
                    rows = [table for table in tables if isinstance(table, dict) and table.get('thscode') == code]
                    if len(rows) != 1 or not isinstance(rows[0].get('time'), list):
                        raise ContractError('行情日期表不唯一或缺失')
                    dates = rows[0]['time']; values = rows[0].get('table', {}).get('close')
                    if (not isinstance(values, list) or len(values) != len(dates)
                            or dates != sorted(set(dates))
                            or any(not isinstance(day, str) or date.fromisoformat(day).isoformat() != day
                                   or not params['startdate'] <= day <= params['enddate'] for day in dates)):
                        raise ContractError('行情日期、价格列或窗口不一致')
                    observed = [day for day, value in zip(dates, values)
                                if type(value) in (int, float) and math.isfinite(value)]
                item['observed_dates'] = observed
                if matching:
                    calendars_for_window = [tuple(day for day in details['dates']
                        if params['startdate'] <= day <= params['enddate']) for _, details in matching]
                    if len(set(calendars_for_window)) != 1:
                        raise ContractError('同窗口已取得交易日历相互冲突')
                    calendar, _ = matching[0]
                    expected = list(calendars_for_window[0])
                    item.update(calendar_dates=expected,
                        calendar_ref=dict(artifact_id=calendar['id'], snapshot_hash=calendar['hash'],
                            request_hash=calendar['request_hash']),
                        missing_dates=sorted(set(expected) - set(observed)),
                        unexpected_dates=sorted(set(dates) - set(expected)))
                    item['status'] = ('存在非日历日期' if item['unexpected_dates'] else
                        '存在未观测交易日' if item['missing_dates'] else
                        '日历覆盖一致' if expected else '日历明确无交易日')
            except (ContractError, ValueError, TypeError):
                item['status'] = '日期覆盖不可核对'
            panels.append(item)
    return dict(version=1, panels=panels,
        limitations='交易所交易日不证明个券应当成交。缺少价格的原因可能是停牌、上市范围或缺数，未单独识别；不补值，不证明历史当时可得。')
