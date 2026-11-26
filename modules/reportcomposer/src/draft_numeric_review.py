"""Keep uncorroborated factual quantities out of the composed first draft."""
from decimal import Decimal, InvalidOperation
from operator import ge, gt, le, lt
import re
import unicodedata

from .contracts import ContractError, DOCUMENT_CONTENT_INTENT, fingerprint, market_date
from .draft_sources import (CLIENT_COPY_ATTRIBUTION_VERSION, CLIENT_COPY_MARKET_DAY_VERSION,
                            CLIENT_COPY_MARKET_LEVEL_VERSION, CLIENT_COPY_POLICY, CLIENT_COPY_VERSIONS,
                            _sequence_day, client_observation_links_enabled,
                            disclosure_excerpt_hash, historical_original_scope, writing_context)
from .draft_analysis import FINANCIAL, NAV_FIELDS, VALUATION_ATTRIBUTION, VALUATION_POSITION, _day
from .draft_economic_scope import (DraftEconomicScope, continuation_window, generic_reading, preceding_comparison_window,
                                  observation_window, reading_change, reading_pair)
from .draft_filing_amounts import is_filing_amount, segment_field
from .draft_financial_scope import DraftFinancialScope, _SCOPE_LIMITATION, financial_document_matches, has_financial_scope
from .market_series import VALUATION_HISTORY_POLICY, VALUATION_HISTORY_VERSIONS, valuation_attribution_enabled
from .numeric_integrity import (DRAFT_CLAUSE_BOUNDARY, _DRAFT_CODE, _DRAFT_FULL_DAY, _OTHER_RETURN_LABEL, _RETURN_LABEL,
                                _draft_periods, _matches, _same_unit, draft_day_intervals, draft_quantities,
                                draft_quantity_scope, draft_quantity_scopes_match,
                                draft_quantity_source_matches, literal_quantity_qualifiers,
                                literal_quantity_ranges, quantity_qualifiers_enabled,
                                quantity_ranges_enabled, split_draft_clauses)
from .tinyshare_sources import analysis_field


_VALUATION_PERCENTILE = re.compile(r'分位(?:数|点)?(?:约|为|在|处于|达到|升至|降至|的|第|\s)*$')
_VALUATION_PREFIX = re.compile(r'市盈率|滚动估值|(?<![A-Za-z])PE(?:\s*[（(]\s*TTM\s*[）)])?(?![A-Za-z])', re.I)
_VALUATION_CONTRIBUTION = re.compile(
    r'(?:(?P<multiple>估值倍数|滚动市盈率|市盈率|PE(?:\s*[(（]TTM[)）])?)|'
    r'隐含(?:的)?滚动(?:每股)?盈利|隐含盈利)[^，,。；;]{0,10}?'
    r'(?P<action>贡献|拉动|拖累)(?:了|为|约|达|\s)*$', re.I)
_VALUATION_HISTORY_LENGTH = re.compile(r'(?:近|过去|此前)\s*(\d+|[一二两三四五六七八九十]+)\s*个?(月|年)')
_ASSUMPTION = re.compile(r'^\s*(?:若|假如|假定|假设|如果|倘若|压力情景|情景假设|用户(?:设定|要求)|观察阈值)')
_CONSEQUENCE = re.compile(r'则|那么|预计|预测|可推得|将(?=上涨|下跌|升至|降至|达到|获得)')
_CONDITION_END = re.compile(r'\s*(?:时|的情况下|的情景)(?:$|[的为可需则])')
_PROJECTED_READING = re.compile(r'预计|预估|预测|预期(?:将|会)|假设|假定|情景假设|目标(?:为|值)')
_STATEMENT_END = re.compile(r'[；。！？;!?\n]')
_ATTRIBUTION = re.compile(
    r'贡献|归因(?:于)?|拉动|拖累|推升|压低|带动|推动|'
    r'(?:由|因)[^，,]{1,24}(?:导致|带来|造成|所致)|'
    r'影响(?=[^，,]{0,18}(?:上涨|上升|下降|下跌|扩大|收窄|回升|回落|增长|减少|为))'
)
_ATTRIBUTION_TAIL = re.compile(
    r'^\s*(?:(?:主要|部分|完全|全部)?(?:来自|源于|源自|归因于)|'
    r'由[^，,]{1,24}(?:拉动|拖累|贡献|导致|带来|造成)|[（(][^（）()，,]{0,24}(?:贡献|拉动|拖累))'
)
_ATTRIBUTION_PREFACE = re.compile(r'^\s*(?:主要)?(?:由于\S+|受\S+(?:影响|推动|拖累))\s*$')
_OBSERVED_READING = re.compile(r'读数|观测值|总指数|整体指数|价格(?:同比|环比)?(?:下降|上涨|回落|上升|下跌|为)')
_ATTRIBUTION_UNITS = {'%', '百分点', '基点', '%/年'}
_ATTRIBUTION_GAP = '分项贡献缺少可核对的读数'
_ECONOMIC_GAP = '该口径的宏观读数缺少可核对的依据'
_CAUSAL_ASSERTION = re.compile(
    r'导致|造成|致使|引发|拉动|拖累|推升|压低|带动|归因于|所致|'
    r'(?:推动|驱动)(?!力|因素|机制|路径|变量|假设)|'
    r'(?:完全|全部|主要|直接)(?:源于|源自|来自)'
)
_CAUSAL_QUALIFIER = re.compile(
    r'可能|或许|或将|预计|预测|有望|待验证|待核对|有待|是否|'
    r'不能|无法|不足以|未能|尚不能|不意味着|未必|尚无|仍需|一般而言|理论上|'
    r'资料不足|证据不足|缺少(?:数据|资料|权重|证据)'
)
_MARKET_OUTCOME = re.compile(r'股价|收盘|涨跌|涨幅|跌幅|上涨|下跌|回升|回落|市场表现')
_RESEARCH_OUTCOME = re.compile(
    r'板块|行业|产业链|股价|收盘|涨跌|涨幅|跌幅|上涨|下跌|走强|走弱|市场表现|回升|回落|'
    r'收益率?|估值|市盈率|市净率|风险溢价|利率|利差|波动率|流动性|'
    r'营业收入|营收|收入|利润|盈利|现金流|毛利率|净利率|成本|回款|销量|订单|需求|产能|份额|业绩')
_EXPLICIT_CAUSAL_ATTRIBUTION = re.compile(
    _CAUSAL_ASSERTION.pattern + r'|(?:因|受)[^，,。；！？]{1,30}(?=而(?:上升|上涨|下跌|下降|增长|改善|回落|转弱|承压))'
    r'|是[^，,。；！？]{1,36}(?=的(?:主要|根本|直接|唯一|关键)?(?:原因|成因|主因|结果))'
    r'|(?:由|因)[^，,。；！？]{1,28}(?:带来|带动|促成)|归功于|得益于')
_CONDITIONAL_LEAD = re.compile(r'(?:或可|有助于|推测|情景下|条件下|前提是|取决于|若|如果|倘若)')
_CAUSAL_NEGATION = re.compile(r'(?:并非|并不是|不是|不能说是|否认|不(?:会|足以|一定|必然)?\s*$)')
_SOURCE_QUOTE = re.compile(r'(?:原文|公告|文件|声明)(?:称|写道|指出|披露|提到)[:：]?\s*[“「](?P<quote>[^”」]+)[”」]')
_CAUSAL_GAP = '该变化的原因仍需验证'
_PATH_CAUSAL_GAP = '影响待验证'
_DISCLOSED_VALUE = re.compile(r'(?:读数|数值)?(?:为|约为|约|是|报|录得|[:：=])?\s*$')
_DECLINE = re.compile(r'(?:(?<!涨)跌|(?<!升)降|回落|回撤|下滑|下行|(?<!增)减(?:少)?|低)'
                      r'(?:幅)?(?:了|约|近|于|为)?\s*$')
_INCREASE = re.compile(r'(?:涨|升|提升|回升|上行|增(?:长|加)?)(?:幅)?(?:了|约|近|为)?\s*$')
_AMOUNT_WORDS = re.compile(r'(?:读数|数值)?(?:为|约为|约|是|报|达到|同比(?:增长|下降|上升|减少)|上涨|下跌|增长|下降)?\s*$')
_SCALE = {unit: Decimal(10) ** places for unit, places in (
    ('人民币元', 0), ('元', 0), ('千元', 3), ('百万元', 6), ('千万元', 7), ('万元', 4),
    ('亿元', 8), ('十亿元', 9), ('百亿元', 10), ('千亿元', 11), ('万亿元', 12),
    ('股', 0), ('万股', 4), ('百万股', 6), ('亿股', 8),
    ('份', 0), ('万份', 4), ('亿份', 8), ('吨', 0), ('万吨', 4), ('亿吨', 8),
    ('美元', 0), ('万美元', 4), ('亿美元', 8),
)}
_SCALE_FAMILY = {
    unit: ('美元' if unit.endswith('美元') else unit[-1]) for unit in _SCALE
}
_DISTINCT_LABEL = _OTHER_RETURN_LABEL + r'|(?:概率|目标价|波动率|分位|扣非(?:归母)?净利润)'
_DISTINCT_MEASURE = re.compile(_DISTINCT_LABEL)
_MEASURE_SUFFIX = re.compile(r'^\s*(?:的|[（(]\s*)?(?P<label>' + _DISTINCT_LABEL + ')')
_RETURN_MEASURES = re.compile(_RETURN_LABEL)
_RECENT = r'(?:最新|最近|当前)'
_ONE_SESSION = r'\s*(?:[一1]个?)?(?:交易日|日)'
_COMPARISON = re.compile(
    r'(?P<year>同比|(?:较|比|相对|与)(?:去年|上一?年)(?:同期|同月|同季|同日))'
    r'|(?P<month>(?:当月|月度)环比|(?:较|比|相对|与)上(?:一?个|一)?月)'
    r'|(?P<quarter>(?:当季|季度)环比|(?:较|比|相对|与)上(?:一?个|一)?季(?:度)?)'
    r'|(?P<sequential>环比|(?:较|比|相对|与)上一?期)'
    r'|(?P<reported_session>(?:披露|发布)(?:的)?(?:当日|单日|日度|日)?(?:涨跌幅|涨幅|跌幅))'
    r'|(?P<session>(?:较|比|相对|与)(?:(?:上|前)(?:一?个|一|1个?)?交易日|前收盘(?:价|点位)?|昨收(?:盘价)?)'
    r'|(?:当日|今日|本日|当天|单日|' + _RECENT + _ONE_SESSION + r')(?:的)?(?:收盘价?|收盘点位|股价|指数)?'
    r'(?:涨跌幅|涨幅|跌幅|上涨|下跌|收涨|收跌)|日(?:度)?(?:涨跌幅|涨幅|跌幅))'
)
_MARKET_LEVELS = {'收盘价':'close', '收盘点位':'close', '收盘':'close', '股价':'close',
                  '开盘价':'open', '最高价':'high', '最低价':'low'}
_MARKET_LEVEL_FIELDS = set(_MARKET_LEVELS.values()) | NAV_FIELDS
_MARKET_STAMP = (r'(?P<stamp>今日|本日|当天|当日|' + _RECENT
                 + r'(?:' + _ONE_SESSION + r'|\s*(?:一次|一期))?(?:\s*(?:可核对|已取得|可用))?)')
_MARKET_STAMP_BEFORE = re.compile(_MARKET_STAMP + r'\s*(?:估值|行情)?\s*的?\s*$')
_MARKET_STAMP_AFTER = re.compile(r'\s*' + _MARKET_STAMP + r'\s*')
_MARKET_LEVEL_LEAD = (r'\s*(?:[（(]\s*(?:TTM|MRQ|LYR)\s*[）)]|TTM|MRQ|LYR)?\s*'
                      r'(?:读数|数值)?\s*(?:约为|为|约|是|报收于|报|收于|录得|[:：=]|'
                      r'(?:涨|跌|回落|回升|上升|下降)至)?\s*')
_LEVEL_LEAD = re.compile(_MARKET_LEVEL_LEAD, re.I)
_OTHER_MARKET_LEVEL = re.compile(r'(?:目标|合理|预测|预期|估算|参考|理论|平均|实时|盘中|(?<!未)(?<!不)复权|累计)\s*的?\s*$')
_VALUATION_BASIS = re.compile(r'(?<![A-Za-z])(?:LYR|MRQ|TTM)(?![A-Za-z])', re.I)
_AS_OF_CLAUSE = re.compile(r'(?:截至|截止(?:到|至)?)\s*' + _DRAFT_FULL_DAY)
_DAY_CLAUSE = re.compile(r'(?:在|于)?\s*' + _DRAFT_FULL_DAY)
_DAILY_STAMP = re.compile(_MARKET_STAMP)
_MARKET_PREFACE = re.compile(r'(?:(?:截至|截止(?:到|至)?)\s*)?' + _MARKET_STAMP)
_BARE_MARKET_LEVEL = re.compile(r'(?P<label>' + '|'.join(sorted((*_MARKET_LEVELS, '单位净值'), key=len, reverse=True))
                                + r')(?:\s*' + _MARKET_STAMP + r')?' + _MARKET_LEVEL_LEAD
                                + r'(?P<value>\d+(?:\.\d+)?)(?![\d.,%％A-Za-z])')


def _market_measure_span(prefix, label, family):
    if family:
        from .publication_content import _VALUATION_ALIASES
        aliases = list(re.finditer(_VALUATION_ALIASES[family], prefix, re.I))
        if not aliases:
            return None
        start, end = aliases[-1].span()
    else:
        start = prefix.rfind(label) if label else -1
        if start < 0:
            return None
        end = start + len(label)
    return start, end


def _market_marker(prefix, span):
    if span is None:
        return None
    start, end = span
    mark = _MARKET_STAMP_BEFORE.search(prefix[:start])
    if mark and _LEVEL_LEAD.fullmatch(prefix[end:]):
        return mark, False
    mark = _MARKET_STAMP_AFTER.match(prefix, end)
    if mark and _LEVEL_LEAD.fullmatch(prefix[mark.end():]):
        return mark, True
    return None


def _period_day(period):
    if period is not None and period.year and len(period.end) == 4:
        return _sequence_day(period.year + period.end)
    return None


def _market_point_day(scope):
    period = scope.period
    return _period_day(period) if period and not (period.start or period.statistic or scope.base) else None


def _market_day_header(previous):
    previous = previous.strip()
    as_of = _AS_OF_CLAUSE.fullmatch(previous) is not None
    if as_of or _DAY_CLAUSE.fullmatch(previous):
        return draft_quantity_scope(previous, len(previous)).period, as_of
    return None, False


def _market_as_of(text, clause_start, prefix, offset, scope):
    local = bool(_AS_OF_CLAUSE.search(prefix[:offset]))
    if scope.period is not None or not clause_start or text[clause_start - 1] not in '，,':
        return scope, local, local
    prior = DRAFT_CLAUSE_BOUNDARY.split(text[:clause_start - 1])[-1].strip()
    if _AS_OF_CLAUSE.fullmatch(prior):
        return scope._replace(period=draft_quantity_scope(prior, len(prior)).period), True, False
    return scope, False, False


def _named_market_codes(text, rows):
    return {row['code'].upper() for row in rows if row.get('code')
            and isinstance(row.get('object_label'), str) and len(row['object_label']) >= 2
            and row['object_label'] in text}


def _market_subject(scope, prefix, rows):
    if scope.code:
        return scope
    codes = _named_market_codes(prefix, rows)
    return scope._replace(code=next(iter(codes))) if len(codes) == 1 else scope


def _market_field(row):
    field = analysis_field(row)
    return 'unit_nav' if field in NAV_FIELDS else field


def _market_rows(facts, valuation_fields):
    dated = {}
    for row in facts:
        field = _market_field(row)
        if field not in _MARKET_LEVEL_FIELDS and field not in valuation_fields:
            continue
        day = _sequence_day(row.get('period'))
        if day is None or not row.get('code') or type(row.get('value')) not in (int, float):
            continue
        try:
            value = Decimal(str(row.get('raw_value', row['value'])))
        except InvalidOperation:
            continue
        if value.is_finite() and (field not in _MARKET_LEVEL_FIELDS or value > 0):
            dated.setdefault((row['code'].upper(), field), []).append((day, row, value))
    return dated


def _market_date_match(dated, candidates, scope, stamp, cutoff, *, as_of=False):
    """Find the last admitted observation independently of the cited subset."""
    keys = {(row['code'].upper(), _market_field(row)) for row in candidates if row.get('code')
            and (not scope.code or row['code'].upper() == scope.code)}
    if cutoff is None or len(keys) != 1 or scope.base is not None:
        return None, []
    explicit_day = _period_day(scope.period)
    if explicit_day is not None and explicit_day > cutoff:
        return None, []
    if stamp in ('今日', '本日'):
        if explicit_day is not None and explicit_day != cutoff:
            return None, []
        target = cutoff
    elif stamp in ('当天', '当日'):
        if explicit_day is None:
            return None, []
        target = explicit_day
    else:
        target = explicit_day if not as_of else None
    limit = explicit_day or cutoff
    observations = []
    for day, row, value in dated.get(next(iter(keys)), ()):
        if day > limit or (target is not None and day != target):
            continue
        if target is None and not (as_of and explicit_day) and not draft_quantity_source_matches(row, scope):
            continue
        observations.append((day, row, value))
    last_day = max((day for day, _, _ in observations), default=None)
    rows = [(row, value) for day, row, value in observations if day == last_day]
    if len({(value, row.get('unit'), row.get('currency')) for row, value in rows}) != 1:
        return None, []
    selected_ids = {row['id'] for row in candidates}
    return last_day, [row for row, _ in rows if row['id'] in selected_ids]


def _refs(items):
    return {ref for ref in items if isinstance(ref, str)} if isinstance(items, list) else set()


def _compact(text):
    return ''.join(unicodedata.normalize('NFKC', text).split())


def _has_attribution(text):
    return any(not _SCOPE_LIMITATION.search(text[:match.start()]) for match in _ATTRIBUTION.finditer(text))


def _attributed_quantity(number, prefix, suffix, previous_clause, title=''):
    return number['unit'] in _ATTRIBUTION_UNITS and (
        _has_attribution(prefix) or _ATTRIBUTION_TAIL.match(suffix) is not None
        or _ATTRIBUTION_PREFACE.fullmatch(previous_clause) is not None
        or isinstance(title, str) and _has_attribution(title) and not _OBSERVED_READING.search(prefix)
    )


def _attribution_series_matches(row, prefix):
    # Providers can use a single generic field (e.g. EDB `value`) for the
    # headline and every component. Require the explicit metric label first.
    label = row.get('label') or ''
    if not isinstance(label, str) or not _has_attribution(label):
        return False
    periods = _draft_periods(prefix)
    if periods and prefix[:periods[0][0]].strip() in ('', '截至'):
        prefix = prefix[periods[0][1]:].lstrip().removeprefix('的')
    label, prefix = _compact(label), _compact(prefix)
    return prefix.startswith(label) and _DISCLOSED_VALUE.fullmatch(prefix[len(label):]) is not None


def _statement_excerpt(text, start, number_end):
    boundary = _STATEMENT_END.search(text, number_end)
    end = boundary.start() if boundary else len(text)
    return start, end, _compact(text[start:end])


def _projected(text):
    return bool(_ASSUMPTION.match(text) or _PROJECTED_READING.search(text))


def _comparison_basis(text):
    basis = None
    for match in _COMPARISON.finditer(text):
        basis = match.lastgroup
    return basis


def _comparison_matches(claim, evidence):
    return claim == evidence or claim == 'sequential' and evidence in ('month', 'quarter')


def _calculation_basis(calculation, rows):
    if calculation.get('comparison_kind') == 'previous_session':
        return 'session'
    if calculation['method'] not in ('raw_change_v1', 'raw_difference_v1') or len(calculation['data_refs']) != 2:
        return None
    first, last = (rows.get(ref, {}) for ref in calculation['data_refs'])
    if analysis_field(first) not in FINANCIAL or analysis_field(first) != analysis_field(last):
        return None
    start, end = _day(first.get('period')), _day(last.get('period'))
    if start and end and (start.year + 1, start.month, start.day) == (end.year, end.month, end.day):
        return 'year'
    return None


def _daily_record(rows, selected, scope, stamp, cutoff, *, as_of, closing_points):
    """Date a recomputed change by the latest close before considering citations."""
    if cutoff is None:
        return None, []
    explicit = _period_day(scope.period)
    if explicit and explicit > cutoff:
        return None, []
    if stamp in ('今日', '本日'):
        if explicit and explicit != cutoff:
            return None, []
        target = cutoff
    elif stamp in ('当天', '当日'):
        if explicit is None:
            return None, []
        target = explicit
    else:
        target = explicit if not as_of else None
    limit = explicit or cutoff
    period_scope = scope._replace(period=None) if as_of and explicit and target is None else scope
    code_set = {row['code'] for row, _ in rows if row['id'] in selected
                and (not scope.code or scope.code == row['code'])}
    code_set.update(code for code, points in closing_points.items() if (not scope.code or scope.code == code)
                    and any(row['id'] in selected for _, row, _ in points))
    if len(code_set) != 1:
        return None, []
    code = next(iter(code_set))
    if target is None and closing_points:
        end_scope = period_scope._replace(base=None)
        prices = [(day, value) for day, row, value in closing_points.get(code, ()) if day <= limit
                  and draft_quantity_source_matches(row, end_scope)]
        target = max((day for day, _ in prices), default=None)
        if len({value for day, value in prices if day == target}) > 1:
            return None, []
    dated = []
    for row, inputs in rows:
        day = _sequence_day(row.get('window_end', row.get('period')))
        if (row['code'] == code and day is not None and day <= limit and (target is None or target == day)
                and draft_quantity_source_matches(row, period_scope, inputs=inputs)):
            dated.append((day, row))
    day = max((day for day, _ in dated), default=None)
    matches = [row for actual, row in dated if actual == day]
    if len({(row.get('window_start'), str(row.get('exact', row.get('raw_value', row['value']))))
            for row in matches}) != 1:
        return None, []
    return day, [row for row in matches if row['id'] in selected]


def _direction_prefix(number, prefix):
    if not number.get('qualifiers'):
        return prefix
    # 低于 is a comparison against the displayed level. Only separate
    # direction words such as 下降 describe the sign of a change.
    for qualifier in reversed(literal_quantity_qualifiers(prefix, len(prefix), len(prefix))):
        prefix = prefix[:qualifier.start] + prefix[qualifier.end:]
    return prefix


def _negative_reading(number, prefix):
    return number['value'] < 0 or bool(_DECLINE.search(_direction_prefix(number, prefix)))


_QUANTITY_COMPARISONS = {'minimum': ge, 'greater': gt, 'maximum': le, 'less': lt}


def _quantity_kinds(number):
    return frozenset(qualifier.kind for qualifier in number.get('qualifiers', ()))


def _quantity_literal(number):
    if interval := number.get('interval'):
        return interval.first, interval.first_unit, interval.last, interval.last_unit
    token = _compact(number['text'])
    if 'qualifiers' in number:
        token = re.sub(r'(?<=\d),(?=\d{3}(?!\d))', '', token)
    return token


def _quantity_prose(text, number):
    # Compare the wording separately from the relation. For example, 超 and
    # 超过 have the same relation, while 以上 may be written after the unit.
    literal = '\0' if 'interval' in number else _quantity_literal(number)
    edits = [(number['start'], number['end'], literal)]
    edits.extend((qualifier.start, qualifier.end, '') for qualifier in number.get('qualifiers', ()))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return _compact(text)


def _qualified_span(number):
    qualifiers = number.get('qualifiers', ())
    return (min([number['start'], *(item.start for item in qualifiers)]),
            max([number['end'], *(item.end for item in qualifiers)]))


def _document_quantity_matches(source, claim):
    if source == claim:
        return True
    if not source:
        return len(claim) == 1 and claim <= {'approximate', 'minimum', 'maximum'}
    return ((source == {'greater'} and claim == {'minimum'}) or
            (source == {'less'} and claim == {'maximum'}))


def _measure_matches(value, unit, number, prefix):
    if 'interval' in number:
        return False
    try:
        value = Decimal(str(value).replace(',', '').replace('−', '-'))
        if not value.is_finite():
            return False
        shown = number['unit']
        if shown is not None and not _same_unit(unit, shown):
            if shown not in _SCALE or unit not in _SCALE or _SCALE_FAMILY[shown] != _SCALE_FAMILY[unit]:
                return False
            # Different display scale within one unit family, never a currency or ratio conversion.
            if shown == '人民币元' and unit != '人民币元':
                return False
            value = value * _SCALE[unit] / _SCALE[shown]
        direction = _direction_prefix(number, prefix)
        if _DECLINE.search(direction):
            if value > 0:
                return False
            if value < 0 and number['value'] > 0:
                value = -value
        if value < 0 and _INCREASE.search(direction):
            return False
        qualifiers = _quantity_kinds(number)
        if qualifiers and qualifiers != {'approximate'}:
            compare = _QUANTITY_COMPARISONS.get(next(iter(qualifiers))) if len(qualifiers) == 1 else None
            return compare is not None and compare(value, number['value'])
        if qualifiers == {'approximate'} and number['digits'] is None and number['value'] == number['value'].to_integral_value():
            number = dict(number, digits=0)
        return _matches(value, number)
    except (InvalidOperation, OverflowError, TypeError, ValueError):
        return False


def _draft_numbers(clause, *, market_preface=False, dated_market_levels=False, market_day=None,
                   check_qualifiers=False, check_ranges=False):
    from .publication_content import _VALUATION_QUOTE

    found = draft_quantities(clause, scaled_counts=check_qualifiers)
    intervals = list(literal_quantity_ranges(clause)) if check_ranges else []
    if intervals:
        found = [number for number in found if not any(
            number['start'] < span.end and span.start < number['end'] for span in intervals)]
        found.extend(dict(start=span.start, end=span.end, text=clause[span.start:span.end], value=span.first,
                          unit=span.last_unit, digits=None, interval=span) for span in intervals)
    # A valuation may be written as a plain reading or with x instead of 倍.
    for quote in _VALUATION_QUOTE.finditer(clause):
        family = 'pe' if quote['pe'] else 'pb'
        for group in ('first', 'last'):
            if not quote[group]:
                continue
            start, end = quote.span(group)
            seen = next((number for number in found if number['start'] == start and number['end'] == end), None)
            if seen is None:
                seen = next((number for number in found if 'interval' in number
                             and number['start'] <= start and end <= number['end']), None)
            if seen is not None:
                seen['valuation'] = family
                continue
            if any(number['start'] < end and start < number['end'] for number in found):
                continue
            token = quote[group]
            suffix = token[-1]
            literal = token[:-1].rstrip() if suffix in '倍xX％%' else token
            digits = len(literal.partition('.')[2]) if '.' in literal else None
            found.append(dict(start=start, end=end, text=token, valuation=family, digits=digits,
                value=Decimal(literal.replace(',', '').replace('−', '-')),
                unit='倍' if suffix in '倍xX' else '%' if suffix in '％%' else None))
    for match in _BARE_MARKET_LEVEL.finditer(clause):
        start, end = match.span('value')
        dated_nav = (dated_market_levels and match['label'] == '单位净值' and not clause[end:].strip()
                     and (market_day or _market_point_day(draft_quantity_scope(clause, start))))
        if (not any(number['start'] < end and start < number['end'] for number in found)
                and (market_preface or dated_nav or _market_marker(clause[:start], match.span('label')))):
            token = match['value']
            found.append(dict(start=start, end=end, text=token, value=Decimal(token), unit=None,
                digits=len(token.partition('.')[2]) if '.' in token else None))
    if check_qualifiers or check_ranges:
        for number in found:
            number['qualifiers'] = literal_quantity_qualifiers(clause, number['start'], number['end'])
    return sorted(found, key=lambda number: number['start'])


def _shift_quantity_links(links, start, end, replacement):
    """Keep exact spans as surrounding prose is edited from right to left."""
    shift = len(replacement) - (end - start)
    return [item if item['end'] <= start else dict(item, start=item['start'] + shift, end=item['end'] + shift)
            for item in links if item['end'] <= start or item['start'] >= end]


def _link_market_level(links, number, row, day, code, dated, evidence_scope):
    key = row['code'].upper(), _market_field(row)
    if key[0] != code or sum(observed == day for observed, _, _ in dated.get(key, ())) != 1:
        return
    links.append(dict(start=number['start'], end=number['end'], original=number['text'], kind='market_level',
                      code=row['code'], date=day.isoformat(), data_refs=[row['id']], calculation_refs=[],
                      row_hash=fingerprint(row)))
    if evidence_scope is not None:
        evidence_scope['observation_refs'].append(row['id'])


def _point_subject(prefix, suffix, rows, market_codes):
    text = prefix + ' ' + suffix
    codes = {code.upper() for code in _DRAFT_CODE.findall(text)} | _named_market_codes(text, rows)
    codes = codes or market_codes
    return next(iter(codes)) if len(codes) == 1 else None


def review_draft_quantities(task, proposal, facts, calculations, *, financial_scope=None, quantity_links=None,
                            logic_exclusions=None):
    """Review new prose only. Retain the original in the submission and adjustment log."""
    from .publication_content import _FINANCIAL_NAMES, _VALUATION_FIELDS, draft_logic_data_refs, observation_unit

    check_qualifiers = quantity_qualifiers_enabled(task)
    check_ranges = quantity_ranges_enabled(task)
    version = task.get(CLIENT_COPY_POLICY)
    market_level_links = version in CLIENT_COPY_VERSIONS[CLIENT_COPY_VERSIONS.index(CLIENT_COPY_MARKET_LEVEL_VERSION):]
    market_day_headers = version in CLIENT_COPY_VERSIONS[CLIENT_COPY_VERSIONS.index(CLIENT_COPY_MARKET_DAY_VERSION):]
    history_review = task.get(VALUATION_HISTORY_POLICY) in VALUATION_HISTORY_VERSIONS
    attribution_review = valuation_attribution_enabled(task)
    rows = {row['id']: row for row in facts if row.get('value') is not None}
    financial_scope = financial_scope or DraftFinancialScope(task, facts)
    economic_scope = DraftEconomicScope(task, facts)
    aliases = {
        **{name: field for field, names in _FINANCIAL_NAMES.items() for name in names},
        '收入': 'ths_revenue_stock', '归属母公司净利润': 'ths_np_atoopc_stock',
        '经营活动现金流量净额': 'ths_ncf_from_oa_stock',
        **_MARKET_LEVELS,
    }
    for row in rows.values():
        label = row.get('label')
        if isinstance(label, str) and len(label) >= 2:
            field = analysis_field(row)
            aliases.setdefault(label, field)
            if segment_field(field) and label.endswith('收入'):
                aliases.setdefault(label[:-2]+'营业收入', field)
    aliases.setdefault('单位净值', 'unit_nav')
    metric_names = re.compile('|'.join(re.escape(name) for name in sorted(aliases, key=len, reverse=True)))
    row_bases = {ident: _comparison_basis(row.get('label') or '') for ident, row in rows.items()}
    attribution_rows = {ident for ident, row in rows.items() if _has_attribution(str(row.get('label') or ''))}
    calculation_bases = {calc['id']: _calculation_basis(calc, rows) for calc in calculations}
    calculation_inputs = {calc['id']: [rows[ref] for ref in calc['data_refs'] if ref in rows] for calc in calculations}
    financial_calculations = {ident for ident, inputs in calculation_inputs.items()
                              if any(analysis_field(row) in FINANCIAL for row in inputs)}
    market_rows = _market_rows([row for row in facts if not row.get('previous_close_for')], _VALUATION_FIELDS)
    implicit_market_codes = ({code for code, _ in market_rows} if task.get('entry') in ('个股', 'ETF', '指数')
                             and len(task.get('objects', ())) == 1 else set())
    shared_market_day = False
    if market_level_links and not implicit_market_codes:
        from .evidence import declared_security_codes
        from .market_series import research_market_inputs_enabled, stated_mixed_latest_scopes

        if research_market_inputs_enabled(task):
            declared = set(declared_security_codes(task))
            requested = {code for scope in stated_mixed_latest_scopes(task) for code in scope.get('codes', ())}
            if len(declared) == 1 and declared == requested == {code for code, _ in market_rows}:
                implicit_market_codes = declared
                shared_market_day = True
    closing_points = {code: points for (code, field), points in market_rows.items() if field == 'close'}
    daily_rows = {
        'session': [(calc, calculation_inputs[calc['id']]) for calc in calculations
                    if calc.get('comparison_kind') == 'previous_session'],
        'reported_session': [(row, ()) for row in rows.values() if row.get('source') == 'tinyshare'
                             and row.get('source_operation') in ('daily', 'fund_daily')
                             and row.get('field') == 'pct_chg' and row.get('unit') == '%'],
    }
    try:
        cutoff_day = _sequence_day(market_date(task.get('cutoff')))
    except (ContractError, TypeError, ValueError):
        cutoff_day = None

    def measure(prefix, number, suffix='', title=''):
        family = number.get('valuation')
        if family:
            return ('市盈率' if family == 'pe' else '市净率'), family
        if trailing := _MEASURE_SUFFIX.match(suffix):
            name = trailing['label']
            return name, aliases.get(name, name)
        named = list(metric_names.finditer(prefix))
        special = list(_DISTINCT_MEASURE.finditer(prefix))
        if special and (not named or special[-1].end() >= named[-1].end()):
            name = special[-1].group()
            # A known field label can provide this exact measure. A similar
            # number from a price, ratio or another output cannot.
            field = next((aliases[label.group()] for label in reversed(named)
                          if label.start() <= special[-1].start() and label.end() >= special[-1].end()), None)
            return name, field or name
        if named:
            name = named[-1].group()
            return name, aliases[name]
        if number['unit'] == '%' and isinstance(title, str):
            labels = set(_RETURN_MEASURES.findall(title))
            if len(labels) == 1 and not metric_names.search(_RETURN_MEASURES.sub('', title)):
                name = next(iter(labels))
                return name, aliases.get(name, name)
        return '', None

    def qualified_numbers(clause, *, market_preface=False, market_day=None):
        previous_end, basis = 0, None
        for number in _draft_numbers(clause, market_preface=market_preface, check_qualifiers=check_qualifiers,
                                     check_ranges=check_ranges, dated_market_levels=market_level_links,
                                     market_day=market_day):
            phrase = clause[previous_end:number['start']]
            explicit = _comparison_basis(phrase)
            if explicit or metric_names.search(phrase) or _DISTINCT_MEASURE.search(phrase):
                basis = explicit
            yield number, basis, previous_end
            previous_end = number['end']

    def document_parts(text, heading):
        result = []
        clauses = split_draft_clauses(text, protect_ranges=check_ranges)
        offset = 0
        statement_start = 0
        for index in range(0, len(clauses), 2):
            if index and _STATEMENT_END.search(clauses[index-1]):
                statement_start = offset
            clause = clauses[index]
            previous = clauses[index-2] if index and not _STATEMENT_END.search(clauses[index-1]) else ''
            tokens = []
            for number, basis, phrase_start in qualified_numbers(clause):
                scope = draft_quantity_scope(text, offset + number['start'])
                prefix = scope.list_context or clause[:number['start']]
                if scope.list_context:
                    basis = _comparison_basis(prefix)
                field = measure(prefix, number, clause[number['end']:])[1]
                qualifier = None
                if has_financial_scope(field):
                    qualifier = financial_scope.read(text, offset+number['start'], offset+number['end'], scope,
                                                     phrase_start=offset+phrase_start, heading=heading)
                    scope = scope._replace(code=qualifier.code)
                suffix = clause[number['end']:]
                local = scope.list_context or clause[phrase_start:number['start']]
                attribution = (_statement_excerpt(text, statement_start, offset+number['end'])
                               if _attributed_quantity(number, local, suffix, previous) else None)
                tokens.append((_quantity_literal(number), field, qualifier,
                               scope, basis, _negative_reading(number, prefix),
                               _projected(text[statement_start:offset+number['start']]), attribution,
                               _quantity_kinds(number), _quantity_prose(clause, number)))
            if tokens:
                result.append((offset, offset+len(clause), statement_start, tokens))
            offset += sum(len(part) for part in clauses[index:index + 2])
        return result

    def document_fragments(part, heading):
        text = part['text']
        for start, end, statement_start, tokens in document_parts(text, heading):
            if part.get('partial_start') and statement_start == 0:
                continue
            if part.get('partial_end') and not _STATEMENT_END.search(text, end):
                continue
            yield start, end, statement_start, tokens

    def complete_statements(part):
        text = part['text']
        for match in re.finditer(r'[^；。！？;!?\n]+', text):
            if (match.start() == 0 and part.get('partial_start')
                    or match.end() == len(text) and part.get('partial_end')):
                continue
            if value := match.group().strip():
                start = match.start()+len(match.group())-len(match.group().lstrip())
                yield value.strip('“”「」'), start, match.end()

    logics = proposal.get('logics', proposal.get('strategies', []))
    logics = [(index, logic) for index, logic in enumerate(logics) if isinstance(logic, dict)] if isinstance(logics, list) else []
    cited = set().union(*(_refs(logic.get('source_refs')) for _, logic in logics))
    available_documents = writing_context(task, facts)['documents'] if cited else []
    document_context = [row for row in available_documents if row['source_ref'] in cited]
    document_sources = {row['source_ref']: row for row in document_context}
    disclosures = {ref: row['disclosure'] for ref, row in document_sources.items() if 'disclosure' in row}
    financial_scope.include_document_issuers(row['disclosure'] for row in available_documents
                                              if 'disclosure' in row)
    documents = {}
    for ref, row in document_sources.items():
        publisher = financial_scope.heading(disclosures.get(ref, {}).get('issuer_code'))
        documents[ref] = [(row['name'], index, part, fragment)
                          for index, part in enumerate(row['parts'])
                          for fragment in document_fragments(part, publisher)]
    # A task-bound passage can corroborate what a source said. This does
    # not certify its causal assertion as an independent research result.
    extended_causality = version in CLIENT_COPY_VERSIONS[CLIENT_COPY_VERSIONS.index(CLIENT_COPY_ATTRIBUTION_VERSION):]
    review_causality = (version == 3 or client_observation_links_enabled(task)) and task.get('intent') != DOCUMENT_CONTENT_INTENT
    historical = historical_original_scope(task)
    document_statements = {}
    if review_causality or disclosures or historical:
        for ref, document in document_sources.items():
            statements = document_statements[ref] = {}
            for index, part in enumerate(document['parts']):
                for statement, start, end in complete_statements(part):
                    statements.setdefault(statement, []).append((index, part, start, end))

    def excerpt_location(ref, part_index, part, start, end):
        source = document_sources[ref]
        index = part.get('source_part_index', part_index)
        offset = part.get('source_offset', 0)
        location = dict(source_ref=ref, part_index=index, start=offset+start, end=offset+end,
            excerpt_hash=disclosure_excerpt_hash(ref, source['name'], index, part.get('locator'),
                                                 offset+start, offset+end, part['text'][start:end]))
        if source.get('snapshot_hash'):
            location['snapshot_hash'] = source['snapshot_hash']
        return location

    def quote_links(text, source_ids, *, link=False):
        matches, links = [], []
        for quote in _SOURCE_QUOTE.finditer(text):
            value = quote['quote'].strip().rstrip('。；！？;!?')
            locations = [(ref, *span) for ref, statements in document_statements.items()
                         if source_ids is None or ref in source_ids for span in statements.get(value, ())]
            if not locations:
                continue
            start, end = quote.span('quote')
            matches.append((start, end))
            if link and len(locations) == 1 and not DRAFT_CLAUSE_BOUNDARY.search(text, start, end):
                links.append(dict(kind='document_excerpt', start=start, end=end, original=text[start:end],
                    data_refs=[], calculation_refs=[], document=excerpt_location(*locations[0])))
        return matches, links
    edits = []
    retrospective_calendar_used = False

    def revise(text, path, row_ids=None, calculation_ids=None, source_ids=None, bound_calculations=None,
               heading=None, title='', evidence_scope=None, *, causal_only=False):
        nonlocal retrospective_calendar_used
        if not isinstance(text, str):
            return text
        if evidence_scope is not None and (_ATTRIBUTION_GAP in text or _ECONOMIC_GAP in text):
            evidence_scope['restricted'] = True
        row_ids, calculation_ids = row_ids or set(), calculation_ids or set()
        limited = bool(row_ids or calculation_ids)
        chosen = [row for ident, row in rows.items() if not row.get('previous_close_for')
                  and (not limited or ident in row_ids)]
        calcs = [calc for calc in calculations if not limited or calc['id'] in calculation_ids
                 or bool(set(calc['data_refs']) & row_ids)]
        if calculation_ids:
            chosen += [rows[ref] for calc in calcs if calc['id'] in calculation_ids
                       for ref in calc['data_refs'] if ref in rows and ref not in row_ids
                       and not rows[ref].get('previous_close_for')]
        originals = [(ident, *fragment) for ident, parts in documents.items()
                     if source_ids is None or ident in source_ids for fragment in parts]
        public_text = 'workpaper_notes' not in path
        check_causality = review_causality and public_text
        link_observations = client_observation_links_enabled(task) and quantity_links is not None and (
            path == 'conclusion' or path.startswith('logics.') and path.rsplit('.', 1)[-1]
            in ('analysis', 'logic', 'claim', 'evidence', 'reasoning', 'implication', 'detail_analysis'))
        if (disclosures or historical or extended_causality and check_causality) and public_text:
            available = {ref: statements for ref, statements in document_statements.items()
                         if source_ids is None or ref in source_ids}
            publishers = {ref: disclosures[ref]['issuer_code'] for ref in available
                          if disclosures.get(ref, {}).get('issuer_code')}
            replacements = []
            for match in _SOURCE_QUOTE.finditer(text):
                quote = match['quote'].strip().rstrip('。；！？;!?')
                prefix = DRAFT_CLAUSE_BOUNDARY.split(text[:match.start()])[-1]
                publisher = financial_scope.heading(prefix)
                missing_history = historical and not any(quote in statements for statements in available.values())
                wrong_publisher = bool(publishers) and (publisher.code or publisher.ambiguous) and not any(
                    code == publisher.code and quote in available[ref] for ref, code in publishers.items())
                unbound = (extended_causality and check_causality and not causal_only
                           and not any(quote in items for items in available.values()))
                if not (missing_history or wrong_publisher or unbound):
                    continue
                start, end = match.start() - len(prefix), match.end()
                replacement = ('该引文的历史原件待核对' if missing_history else
                               '该引文仍待核对' if unbound and not wrong_publisher else '该公告引文仍待核对')
                replacements.append((start, end, replacement))
                reason = 'draft_historical_original_unavailable' if missing_history else 'draft_disclosure_issuer_unsubstantiated'
                edits.append(dict(path=path, original=text[start:end], replacement=replacement,
                    data_refs=[], calculation_refs=[], reason=reason, financial_approval=False))
            for start, end, replacement in reversed(replacements):
                text = text[:start] + replacement + text[end:]
        verified_quotes, quoted_links = quote_links(text, source_ids,
            link=extended_causality and link_observations and not causal_only) if check_causality else ([], [])
        clauses = split_draft_clauses(text, protect_ranges=check_ranges)
        linked_clauses = {}
        removed_attribution = set()
        omitted_valuation_claims = set()
        offset = 0
        statement_start = 0
        preface = None
        for index in range(0, len(clauses), 2):
            if index and _STATEMENT_END.search(clauses[index-1]):
                statement_start = offset
            clause = clauses[index]
            previous = ('' if index - 2 in removed_attribution else
                        DRAFT_CLAUSE_BOUNDARY.split(text[statement_start:offset].rstrip('，, '))[-1])
            clause_start = offset
            offset += sum(len(part) for part in clauses[index:index + 2])
            if index and text[clause_start - 1:clause_start] not in ('，', ','):
                preface = None
            intro = _MARKET_PREFACE.fullmatch(clause.strip())
            if intro and index + 1 < len(clauses) and clauses[index + 1] in ('，', ','):
                preface = index, intro['stamp']
            prior_period, prior_as_of = (_market_day_header(previous) if market_day_headers and index
                                         and clauses[index-1] in ('，', ',') else (None, False))
            if shared_market_day and prior_period is None and index and clauses[index-1] in ('，', ','):
                # A gapped or unsupported reading cannot carry its date to the next metric.
                linked_days = {link['date'] for link in linked_clauses.get(index-2, ())
                               if link.get('kind') == 'market_level' and link['code'] in implicit_market_codes}
                if len(linked_days) == 1:
                    prior_period, _ = _market_day_header(next(iter(linked_days)))
            numbers = list(qualified_numbers(clause, market_preface=preface is not None, market_day=prior_period))
            if check_causality:
                cause = (_EXPLICIT_CAUSAL_ATTRIBUTION if extended_causality else _CAUSAL_ASSERTION).search(clause)
                causal_preface = _ATTRIBUTION_PREFACE.fullmatch(clause)
                if cause or causal_preface:
                    prefix = clause[:cause.start()] if cause else clause
                    delimiter = clauses[index + 1] if index + 1 < len(clauses) else ''
                    following = clauses[index + 2] if delimiter in ('，', ',') else ''
                    context = ' '.join((previous, clause, following))
                    macro = economic_scope.claim(context, title, use_heading=True) is not None
                    event = task.get('entry') == '事件' and _MARKET_OUTCOME.search(context + ' ' + str(title or ''))
                    financial_outcome = extended_causality and _RESEARCH_OUTCOME.search(context + ' ' + str(title or ''))
                    position = clause_start + (cause or causal_preface).start()
                    quoted = any(start <= position < end for start, end in verified_quotes)
                    qualifier = prefix + (cause.group() if extended_causality and cause else '')
                    conditional = (_CAUSAL_QUALIFIER.search(prefix)
                                   or _ASSUMPTION.match(text[statement_start:offset])
                                   or extended_causality and (_CONDITIONAL_LEAD.search(qualifier)
                                       or _CAUSAL_QUALIFIER.search(qualifier) or _CAUSAL_NEGATION.search(prefix))
                                   or '？' in delimiter or '?' in delimiter)
                    # Explicit contribution figures keep their stricter
                    # metric and quotation checks below.
                    measured = any(_attributed_quantity(number, clause[begin:number['start']],
                                   clause[number['end']:], previous) for number, _, begin in numbers)
                    if (macro or event or financial_outcome) and not (quoted or conditional or measured and not causal_only):
                        replacement = '' if path == 'title' else _PATH_CAUSAL_GAP if causal_only else _CAUSAL_GAP
                        clauses[index] = replacement
                        removed_attribution.add(index)
                        if evidence_scope is not None:
                            evidence_scope['restricted'] = True
                        edits.append(dict(path=path, original=clause, replacement=replacement, data_refs=[],
                            calculation_refs=[], reason='draft_causal_attribution_unsubstantiated',
                            financial_approval=False))
                        continue
            clause_links = [dict(item, start=item['start']-clause_start, end=item['end']-clause_start)
                            for item in quoted_links if clause_start <= item['start'] < item['end'] <= clause_start+len(clause)]
            if causal_only or not numbers:
                if clause_links:
                    linked_clauses[index] = clause_links
                continue
            assumption = _ASSUMPTION.match(clause)
            consequence = _CONSEQUENCE.search(clause, assumption.end()) if assumption else None
            if assumption and consequence is None:
                continue
            unsupported = []
            dated_edits = {}
            extreme_edits = {}
            links = clause_links
            pair = reading_pair(clause, numbers, previous) if all('interval' not in number for number, _, _ in numbers) else None
            for number_index, (number, basis, phrase_start) in enumerate(numbers):
                interval = 'interval' in number
                suffix = clause[number['end']:]
                if (consequence and number['start'] < consequence.start()) or _CONDITION_END.match(suffix):
                    continue
                scope = draft_quantity_scope(text, clause_start + number['start'])
                prefix = scope.list_context or clause[:number['start']]
                if scope.list_context:
                    basis = _comparison_basis(prefix)
                family = number.get('valuation')
                name, field = measure(prefix, number, suffix, title)
                local = scope.list_context or clause[phrase_start:number['start']]
                valuation_kind = None
                if (history_review and not interval and number['unit'] == '%' and _VALUATION_PREFIX.search(prefix + ' ' + title)
                        and (_VALUATION_PERCENTILE.search(local) or re.match(r'^\s*分位', suffix))):
                    valuation_kind = VALUATION_POSITION
                    horizon = _VALUATION_HISTORY_LENGTH.findall(prefix)
                    endpoint = _period_day(scope.period)
                    matching = [calc for calc in calcs if calc.get('comparison_kind') == VALUATION_POSITION
                                and (not scope.code or calc['code'] == scope.code)
                                and (not endpoint or endpoint.isoformat() == calc['period'])
                                and all((amount, unit) in {('12','月'), ('十二','月'), ('1','年'), ('一','年')}
                                        for amount, unit in horizon)]
                    expected_unit = '%'
                elif (attribution_review and not interval and number['unit'] == '百分点'
                      and (component := _VALUATION_CONTRIBUTION.search(local))):
                    valuation_kind = VALUATION_ATTRIBUTION
                    label = '估值倍数' if component['multiple'] else '隐含滚动盈利'
                    date_ranges = draft_day_intervals(text[statement_start:clause_start+number['start']])
                    endpoint = _period_day(scope.period)
                    matching = [calc for calc in calcs if calc.get('comparison_kind') == VALUATION_ATTRIBUTION
                                and calc.get('component') == label and not basis
                                and (not scope.code or calc['code'] == scope.code)
                                and (not endpoint or endpoint.isoformat() == calc['window_end'])
                                and (not date_ranges or
                                     (date_ranges[-1][2].isoformat(), date_ranges[-1][3].isoformat()) ==
                                     (calc['window_start'], calc['window_end']))]
                    expected_unit = '个百分点'
                if valuation_kind:
                    expected = matching[0]['exact'] if len(matching) == 1 else None
                    if expected is not None and valuation_kind == VALUATION_ATTRIBUTION and component['action'] == '拖累':
                        expected = -Decimal(expected) if Decimal(expected) < 0 else None
                    if (expected is None or not _measure_matches(expected, expected_unit, number, prefix)
                            or _projected(text[statement_start:clause_start+number['start']])):
                        omitted_valuation_claims.add(index)
                        continue
                    calc = matching[0]
                    if evidence_scope is not None:
                        evidence_scope['refs'].update(calc['data_refs'])
                    if bound_calculations is not None:
                        bound_calculations.append(calc['id'])
                    if quantity_links is not None:
                        links.append(dict(start=number['start'], end=number['end'], original=number['text'],
                            kind=valuation_kind, data_refs=calc['data_refs'], calculation_refs=[calc['id']],
                            calculation_hash=fingerprint(calc)))
                    continue
                attributed = _attributed_quantity(number, pair.prefix if pair else local, suffix, previous, title)
                carry = economic_scope.continuation(local, previous) if not attributed and pair is None else ''
                economic = None if attributed else economic_scope.claim(pair.prefix if pair else local, carry or title,
                    use_heading=not scope.code and field in (None, 'value') and generic_reading(local), previous=previous)
                if (attributed or economic) and evidence_scope is not None:
                    evidence_scope['restricted'] = True
                qualifier = None
                if has_financial_scope(field) and not economic:
                    qualifier = financial_scope.read(text, clause_start+number['start'], clause_start+number['end'], scope,
                                                     phrase_start=clause_start+phrase_start, heading=heading)
                    scope = scope._replace(code=qualifier.code)
                if basis in daily_rows and not (attributed or economic or interval):
                    daily_scope, as_of, local_as_of = _market_as_of(text, clause_start, prefix, len(prefix), scope)
                    daily_scope = _market_subject(daily_scope, prefix, rows.values())
                    typed = field in (None, 'close') if basis == 'session' else field in (None, 'close', 'pct_chg')
                    if not typed or family:
                        unsupported.append((number, '所述日涨跌幅'))
                        continue
                    selected = {item['id'] for item in (calcs if basis == 'session' else chosen)}
                    if basis == 'session':
                        selected.update(row['id'] for row in chosen if _market_field(row) == 'close')
                    marker = _DAILY_STAMP.search(prefix)
                    inherited = preface if marker is None and daily_scope.period is None else None
                    stamp = marker['stamp'] if marker else inherited[1] if inherited else None
                    day, eligible = _daily_record(daily_rows[basis], selected, daily_scope, stamp, cutoff_day,
                        as_of=as_of, closing_points=closing_points if basis == 'session' else {})
                    matching = [row for row in eligible if _measure_matches(
                        row.get('exact', row.get('raw_value', row['value'])), row['unit'], number, prefix)]
                    if matching:
                        item = matching[0]
                        if item.get('session_basis', {}).get('historical_availability_approved') is False:
                            retrospective_calendar_used = True
                        refs = item.get('data_refs', [item['id']])
                        if evidence_scope is not None:
                            evidence_scope['refs'].update(refs)
                        calc_refs = [item['id']] if basis == 'session' else []
                        if quantity_links is not None:
                            links.append(dict(start=number['start'], end=number['end'], original=number['text'],
                                code=item['code'], date=day.isoformat(), data_refs=refs, calculation_refs=calc_refs))
                        if bound_calculations is not None:
                            bound_calculations.extend(calc_refs)
                        explicit = _period_day(daily_scope.period) == day
                        if inherited:
                            clauses[inherited[0]] = clauses[inherited[0] + 1] = ''
                        if marker or not explicit:
                            edit_span = marker.span() if marker and not scope.list_context else (0, 0)
                            replacement = '' if explicit else day.isoformat()
                            if local_as_of and not marker:
                                end = _AS_OF_CLAUSE.search(prefix).end()
                                edit_span, replacement = (end, end), '，' + replacement
                            elif marker and local_as_of and not explicit and not scope.list_context:
                                replacement = '，' + replacement
                            dated_edits[edit_span] = (replacement, refs, day.isoformat(), calc_refs)
                    else:
                        unsupported.append((number, '所述日涨跌幅'))
                    continue
                candidates = [row for row in chosen
                    if (_VALUATION_FIELDS.get(analysis_field(row)) == family if family
                        else not field or analysis_field(row) == field
                        or field in NAV_FIELDS and analysis_field(row) in NAV_FIELDS)
                    and row.get('field') != 'pct_chg'
                    and (not basis or _comparison_matches(basis, row_bases[row['id']]))
                ]
                if attributed:
                    contribution_window = observation_window(local, previous, scope, change=False)
                    candidates = [row for row in chosen
                                  if row['id'] in attribution_rows and _attribution_series_matches(row, local)
                                  and (row['id'] not in economic_scope.rows
                                       or economic_scope.reading_period(row, contribution_window))]
                elif number['unit'] in _ATTRIBUTION_UNITS:
                    candidates = [row for row in candidates if row['id'] not in attribution_rows]
                span, market = None, None
                if not (scope.list_context or attributed or economic or interval) and (family or field in _MARKET_LEVEL_FIELDS
                                              and number['unit'] not in ('%', '百分点', '基点', '%/年')):
                    span = _market_measure_span(prefix, name, family)
                    market = _market_marker(prefix, span)
                point_reading = (span and name != '股价' and not (basis or scope.base)
                                 and (market or _LEVEL_LEAD.fullmatch(prefix[span[1]:]))
                                 and not _projected(text[statement_start:clause_start+number['start']]))
                inherited_day = point_reading and scope.period is None and prior_period is not None
                if inherited_day:
                    scope = scope._replace(period=prior_period)
                inherited_stamp = (preface and span and scope.period is None and _LEVEL_LEAD.fullmatch(prefix[span[1]:]))
                lead = _MARKET_STAMP_BEFORE.sub('', prefix[:span[0]]) if span else ''
                basis_label = _VALUATION_BASIS.search(prefix[span[1]:]) if family and span else None
                incompatible_basis = (family and (lead.rstrip().endswith(('静态', '动态', '远期'))
                    or basis_label and basis_label[0].upper() != ('TTM' if family == 'pe' else 'MRQ')))
                other_market_level = _OTHER_MARKET_LEVEL.search(lead)
                level_code = (_point_subject(prefix, suffix, rows.values(), implicit_market_codes)
                    if (market_level_links and link_observations and point_reading
                    and (scope.period is None or _market_point_day(scope) is not None)
                    and not (incompatible_basis or other_market_level or _quantity_kinds(number))) else None)
                mrq = family == 'pb' and basis_label and basis_label[0].upper() == 'MRQ'
                if market or inherited_stamp or inherited_day:
                    if market:
                        mark, trailing = market
                        market_scope, as_of, local_as_of = _market_as_of(text, clause_start, prefix, mark.start(), scope)
                        as_of = as_of or inherited_day and prior_as_of
                        stamp, date_span = mark['stamp'], mark.span()
                    elif inherited_stamp:
                        market_scope, as_of, local_as_of, trailing = scope, False, False, False
                        stamp, date_span = preface[1], (span[0], span[0])
                    else:
                        market_scope, as_of, local_as_of, trailing = scope, False, False, False
                        stamp, date_span = '当日', None
                    if preface:
                        clauses[preface[0]] = clauses[preface[0] + 1] = ''
                    market_scope = _market_subject(market_scope, prefix, candidates)
                    if inherited_day and level_code:
                        market_scope = market_scope._replace(code=level_code)
                    if mrq:
                        candidates = [row for row in candidates if analysis_field(row) == 'ths_pb_mrq_stock']
                    if inherited_day and (_market_point_day(market_scope) is None or incompatible_basis or other_market_level):
                        unsupported.append((number, '所述时点的' + name))
                        continue
                    day, eligible = _market_date_match(market_rows, candidates, market_scope, stamp, cutoff_day, as_of=as_of)
                    matched = [row for row in eligible if _measure_matches(
                        row.get('raw_value', row['value']), observation_unit(row), number, prefix)]
                    if matched:
                        if evidence_scope is not None:
                            evidence_scope['refs'].update(row['id'] for row in matched)
                        if level_code and len(eligible) == len(matched) == 1:
                            _link_market_level(links, number, matched[0], day, level_code, market_rows, evidence_scope)
                        if _period_day(market_scope.period) == day:
                            replacement = ''
                        elif trailing:
                            replacement = f'（{day.isoformat()}）'
                        else:
                            replacement = ('，' if local_as_of else '') + day.isoformat()
                        if date_span is not None:
                            dated_edits[date_span] = (replacement, [row['id'] for row in matched], day.isoformat(), [])
                    else:
                        unsupported.append((number, '所述时点的' + name))
                    continue
                candidates = [] if interval else [row for row in candidates
                              if attributed and row['id'] in economic_scope.rows
                              or ((not is_filing_amount(row) or (field == analysis_field(row)
                                   and (not source_ids or row['artifact_id'] in source_ids)))
                                  and draft_quantity_source_matches(row, scope, financial=has_financial_scope(analysis_field(row))))]
                eligible = [] if family or attributed or interval else [
                    calc for calc in calcs
                    if (not field or any(analysis_field(row) == field for row in calculation_inputs[calc['id']]))
                    and (number['unit'] not in _ATTRIBUTION_UNITS or attribution_rows.isdisjoint(calc['data_refs']))
                    and calc.get('comparison_kind') != 'previous_session'
                    and (not basis or _comparison_matches(basis, calculation_bases[calc['id']]))
                    and draft_quantity_source_matches(calc, scope, financial=calc['id'] in financial_calculations,
                                                      inputs=calculation_inputs[calc['id']])
                ]
                clear_scope = not attributed or len(candidates) <= 1
                if economic and not interval:
                    change = reading_change(local, number['unit'])
                    window = observation_window(local, previous, scope, change=change)
                    if carry:
                        window = continuation_window(window, carry)
                    if change:
                        window = preceding_comparison_window(window, local, text[statement_start:clause_start])
                    if pair:
                        candidates = economic_scope.paired_rows(chosen, economic, pair)[number_index]
                    else:
                        candidates = [] if change else [row for row in chosen
                            if economic_scope.reading(row['id'], economic, window)]
                    eligible = [] if not change else [calc for calc in calcs
                        if attribution_rows.isdisjoint(calc['data_refs'])
                        and economic_scope.change(calc, calculation_inputs[calc['id']], economic, window)]
                    explicitly_cited = ([calc for calc in eligible if calc['id'] in calculation_ids]
                                        or [calc for calc in eligible if set(calc['data_refs']) <= row_ids])
                    if explicitly_cited or len(row_ids & economic_scope.rows.keys()) >= 2:
                        eligible = explicitly_cited
                    # Resolve the period and series before comparing the number.
                    distinct = {(row['code'], row['period'], row['unit'], str(row.get('raw_value', row['value'])))
                                for row in candidates}
                    clear_scope = len(distinct) <= 1 and len(eligible) <= 1
                elif not attributed:
                    candidates = [row for row in candidates if row['id'] not in economic_scope.rows]
                    eligible = [calc for calc in eligible if economic_scope.rows.keys().isdisjoint(calc['data_refs'])]
                if qualifier is not None:
                    candidates = [row for row in candidates if financial_scope.accepts(qualifier, row)]
                    eligible = [calc for calc in eligible if all(financial_scope.accepts(qualifier, row)
                                for row in calculation_inputs[calc['id']] if analysis_field(row) in FINANCIAL)]
                    finance_inputs = candidates + [row for calc in eligible for row in calculation_inputs[calc['id']]
                                                   if analysis_field(row) in FINANCIAL]
                    clear_scope = clear_scope and not qualifier.ambiguous and financial_scope.unambiguous(finance_inputs)
                if (_quantity_kinds(number) & _QUANTITY_COMPARISONS.keys()
                        and field in (None, 'value') and not (attributed or economic)):
                    # A bound has a wider numerical match than equality.
                    # Require the stated measure before using structured values.
                    candidates, eligible = [], []
                matching_rows = [row for row in candidates if _measure_matches(row.get('raw_value', row['value']),
                    observation_unit(row), number, prefix)] if clear_scope else []
                supported = bool(matching_rows)
                if evidence_scope is not None:
                    evidence_scope['refs'].update(row['id'] for row in matching_rows)
                if (economic and not interval and len(matching_rows) == 1 and not change and not pair
                        and not _projected(text[statement_start:clause_start + number['start']])):
                    row = matching_rows[0]
                    window_context = text[:clause_start + number['start']]
                    extreme = economic_scope.contradicted_extreme(row, suffix, window_context, title or '',
                                                                  task.get('question', ''))
                    if extreme:
                        length, contradicted_by = extreme
                        extreme_edits[number['end'], number['end'] + length] = [row['id'], contradicted_by]
                if level_code and _market_point_day(scope) is not None:
                    market_scope = scope._replace(code=level_code)
                    point_candidates = ([row for row in candidates if analysis_field(row) == 'ths_pb_mrq_stock']
                                        if mrq else candidates)
                    day, point_rows = _market_date_match(market_rows, point_candidates, market_scope, '当日', cutoff_day)
                    if len(point_rows) == 1 and point_rows[0] in matching_rows:
                        _link_market_level(links, number, point_rows[0], day, level_code, market_rows, evidence_scope)
                if (len(matching_rows) == 1 and is_filing_amount(matching_rows[0])
                        and not _projected(text[statement_start:clause_start+number['start']])):
                    row = matching_rows[0]
                    if quantity_links is not None:
                        links.append(dict(start=number['start'], end=number['end'], original=number['text'],
                            kind='filing_amount', data_refs=[row['id']], calculation_refs=[], row_hash=fingerprint(row)))
                    if evidence_scope is not None:
                        evidence_scope['observation_refs'].append(row['id'])
                elif (link_observations and len(candidates) == len(matching_rows) == 1
                        and not attributed and not _quantity_kinds(number)
                        and number['unit'] and not _projected(text[statement_start:clause_start+number['start']])):
                    row = matching_rows[0]
                    # A single numeric match cannot choose its own period or reporting basis.
                    financial_level = (not basis and qualifier is not None and field in FINANCIAL and scope.period is not None
                                       and bool(scope.period.year) and scope.base is None)
                    economic_level = economic and (
                        pair.window.base and pair.window.current and (pair.window.base.year or pair.window.current.year)
                        if pair else not change and window.current and window.current.year)
                    if financial_level or economic_level:
                        links.append(dict(start=number['start'], end=number['end'], original=number['text'],
                            kind='financial_level' if financial_level else 'economic_level',
                            data_refs=[row['id']], calculation_refs=[], row_hash=fingerprint(row)))
                        if evidence_scope is not None:
                            evidence_scope['observation_refs'].append(row['id'])
                if not supported and clear_scope and eligible:
                    matching_calculations = [calc for calc in eligible if any(
                        _measure_matches(calc[key], calc['unit'], number, prefix)
                        for key in ('exact', 'display', 'minimum', 'maximum') if key in calc)]
                    supported = bool(matching_calculations)
                    if evidence_scope is not None:
                        evidence_scope['refs'].update(ref for calc in matching_calculations for ref in calc['data_refs'])
                    # Resolve the reporting scope before looking at the
                    # displayed rate. A number cannot choose its own period.
                    if (quantity_links is not None and supported and len(eligible) == 1 and basis == 'year'
                            and field in FINANCIAL and number['unit'] == '%'
                            and not _projected(text[statement_start:clause_start+number['start']])):
                        calc = eligible[0]
                        if calc['method'] == 'raw_change_v1' and calc['unit'] == '%':
                            links.append(dict(start=number['start'], end=number['end'], original=number['text'],
                                kind='financial_yoy', data_refs=calc['data_refs'], calculation_refs=[calc['id']],
                                calculation_hash=fingerprint(calc)))
                            if bound_calculations is not None:
                                bound_calculations.append(calc['id'])
                    if economic and supported:
                        calc = matching_calculations[0]
                        if bound_calculations is not None:
                            bound_calculations.append(calc['id'])
                        if quantity_links is not None and not _projected(text[statement_start:clause_start+number['start']]):
                            links.append(dict(start=number['start'], end=number['end'], original=number['text'],
                                kind='economic_points', data_refs=calc['data_refs'], calculation_refs=[calc['id']],
                                calculation_hash=fingerprint(calc)))
                if not supported and (clear_scope or attributed or economic) and originals:
                    literal, prose = _quantity_literal(number), _quantity_prose(clause, number)
                    quantity_kinds = _quantity_kinds(number)
                    projected = _projected(text[statement_start:clause_start+number['start']])
                    negative = _negative_reading(number, prefix)
                    statement = (_statement_excerpt(text, statement_start, clause_start+number['end'])[2]
                                 if attributed or economic else None)
                    locations = {}
                    for ref, source_name, part_index, part, (start, end, original_start, tokens) in originals:
                        for (token, source_field, source_qualifier, source_scope, source_basis,
                             source_negative, source_projected, source_attribution, source_kinds, original) in tokens:
                            if (literal != token or source_projected and not projected
                                    or not _document_quantity_matches(source_kinds, quantity_kinds)):
                                continue
                            if qualifier is not None and (source_qualifier is None
                                    or not financial_document_matches(qualifier, source_qualifier)):
                                continue
                            if attributed or source_attribution:
                                if not (attributed and source_attribution and statement == source_attribution[2]):
                                    continue
                                excerpt_start, excerpt_end, _ = source_attribution
                            elif economic:
                                excerpt_start, excerpt_end, original_statement = _statement_excerpt(part['text'], original_start, end)
                                if statement != original_statement:
                                    continue
                                name_scope = economic_scope.claim(original_statement, source_name, use_heading=True)
                                if not name_scope or any(wanted and wanted != actual for wanted, actual in zip(economic, name_scope)):
                                    continue
                            else:
                                if prose != original and not ((qualifier is None or not qualifier.aggregate)
                                        and field is not None and field != 'value' and field == source_field
                                        and _comparison_matches(basis, source_basis) and negative == source_negative
                                        and draft_quantity_scopes_match(scope, source_scope, financial=has_financial_scope(field))):
                                    continue
                                excerpt_start, excerpt_end = start, end
                            key = (ref, part_index, excerpt_start, excerpt_end)
                            locations[key] = (source_name, part)
                    supported = bool(locations)
                    if (quantity_links is not None and len(locations) == 1 and not projected
                            and (scope.code or len(task.get('objects', ())) <= 1)):
                        (ref, part_index, start, end), (source_name, part) = next(iter(locations.items()))
                        document = excerpt_location(ref, part_index, part, start, end)
                        link_start, link_end = _qualified_span(number)
                        links.append(dict(start=link_start, end=link_end, original=clause[link_start:link_end],
                            kind='document_excerpt', data_refs=[], calculation_refs=[], document=document))
                if not supported:
                    if attributed:
                        unsupported.append((number, '分项贡献'))
                        continue
                    if economic:
                        unsupported.append((number, '该口径的宏观读数'))
                        continue
                    comparison = '同比' if basis == 'year' else '环比' if basis else ''
                    if qualifier is not None and qualifier.aggregate:
                        name = ('样本汇总' if qualifier.aggregate == 'sample' else '行业汇总') + name
                    unsupported.append((number, name + comparison if comparison and not name.endswith(comparison) else name))
            if index in omitted_valuation_claims:
                clauses[index] = ''
                removed_attribution.add(index)
                edits.append(dict(path=path, original=clause, replacement='', data_refs=[], calculation_refs=[],
                    reason='draft_valuation_quantity_unsubstantiated', financial_approval=False))
                continue
            if not (unsupported or dated_edits or extreme_edits):
                if links:
                    linked_clauses[index] = links
                continue
            if len(unsupported) == len(numbers):
                name = unsupported[0][1]
                revised = (_ECONOMIC_GAP if name == '该口径的宏观读数' else
                           name + '缺少可核对的读数' if name else '该项数据缺少可核对的依据')
            else:
                revised = clause
                replacements = {span: (replacement, False) for span, (replacement, _, _, _) in dated_edits.items()}
                replacements.update({span: ('', False) for span in extreme_edits})
                for number, name in unsupported:
                    replacements[_qualified_span(number)] = ('读数待补' if name else '数值待核对', True)
                for (start, end), (replacement, strip_lead) in sorted(replacements.items(), reverse=True):
                    left = _AMOUNT_WORDS.sub('', revised[:start]) if strip_lead else revised[:start]
                    links = _shift_quantity_links(links, len(left), end, replacement)
                    revised = left + replacement + revised[end:]
                if unsupported and revised.startswith('在') and revised.endswith('下'):
                    links = _shift_quantity_links(links, len(revised) - 1, len(revised), '')
                    links = _shift_quantity_links(links, 0, 1, '')
                    revised = revised[1:-1]
            clauses[index] = revised
            if links:
                linked_clauses[index] = links
            if unsupported:
                edits.append(dict(path=path, original=clause, replacement=revised, data_refs=[], calculation_refs=[],
                    reason='draft_quantity_unsubstantiated', financial_approval=False))
            if dated_edits:
                edits.append(dict(path=path, original=clause, replacement=revised,
                    calculation_refs=list(dict.fromkeys(ref for _, _, _, refs in dated_edits.values() for ref in refs)),
                    data_refs=list(dict.fromkeys(ref for _, refs, _, _ in dated_edits.values() for ref in refs)),
                    observation_dates=sorted({day for _, _, day, _ in dated_edits.values()}),
                    reason='market_observation_date_bound', financial_approval=False))
            if extreme_edits:
                edits.append(dict(path=path, original=clause, replacement=revised, calculation_refs=[],
                    data_refs=list(dict.fromkeys(ref for refs in extreme_edits.values() for ref in refs)),
                    reason='draft_economic_extreme_contradicted', financial_approval=False))
        for index in sorted(omitted_valuation_claims):
            if index and clauses[index-1] in ('，', ',', '、'):
                clauses[index-1] = ''
            elif index + 1 < len(clauses):
                clauses[index+1] = ''
        result = ''.join(clauses)
        if linked_clauses:
            links = []
            offset = 0
            trim = len(result) - len(result.lstrip())
            for index, clause in enumerate(clauses):
                for item in linked_clauses.get(index, []):
                    if clause[item['start']:item['end']] == item['original']:
                        links.append(dict(item, start=offset+item['start']-trim, end=offset+item['end']-trim))
                offset += len(clause)
            if links:
                quantity_links[path] = dict(text=result.strip(), links=links)
        return result

    def revise_fields(item, prefix, fields, context):
        notes_context = dict(context, evidence_scope=None)
        for field in fields:
            if field in item:
                item[field] = revise(item[field], prefix + field,
                                     **(notes_context if field in ('risk', 'condition') else context))
        for field in ('risks', 'reader_notes', 'workpaper_notes'):
            if isinstance(item.get(field), list):
                item[field] = [revise(text, f'{prefix}{field}.{index}', **notes_context)
                               for index, text in enumerate(item[field])]

    revise_fields(proposal, '', ('title', 'conclusion'), dict(heading=financial_scope.heading(proposal.get('title')), title=proposal.get('title')))
    for index, logic in logics:
        prefix = f'logics.{index}.'
        first_edit = len(edits)
        title = logic.get('title')
        evidence_scope = dict(restricted=False, refs=set(), observation_refs=[])
        context = dict(row_ids=set(draft_logic_data_refs(logic)), calculation_ids=_refs(logic.get('calculation_refs')),
                       source_ids=_refs(logic.get('source_refs')), bound_calculations=[],
                       heading=financial_scope.heading(title), title=title, evidence_scope=evidence_scope)
        revise_fields(logic, prefix, ('title', 'analysis', 'logic', 'implication', 'risk', 'detail_analysis'), context)
        if isinstance(logic.get('argument'), dict):
            revise_fields(logic['argument'], prefix + 'argument.', ('claim', 'evidence', 'reasoning', 'condition'), context)
        mechanism = logic.get('mechanism')
        if isinstance(mechanism, dict) and isinstance(mechanism.get('steps'), list):
            for offset, step in enumerate(mechanism['steps']):
                if isinstance(step, dict) and (step.get('kind') == 'observation' or extended_causality):
                    label = step.get('label')
                    if extended_causality and step.get('kind') != 'observation':
                        # Assumptions may contain scenario values. Review only
                        # an actual causal assertion, not the hypothetical data.
                        revised = revise(label, f'{prefix}mechanism.steps.{offset}.label', source_ids=set(),
                                         heading=context['heading'], title=title, causal_only=True)
                        if revised != label and _PATH_CAUSAL_GAP in revised:
                            step.update(label=revised.rstrip('。'), kind='condition', data_refs=[])
                        continue
                    revised = revise(label, f'{prefix}mechanism.steps.{offset}.label', row_ids=_refs(step.get('data_refs')),
                                     calculation_ids=set(), source_ids=set(), heading=context['heading'], title=title,
                                     evidence_scope=evidence_scope)
                    if revised != label:
                        step.update(label=revised, kind='condition')
                        if extended_causality and _CAUSAL_GAP in revised:
                            step['data_refs'] = []
        if logic_exclusions is not None and evidence_scope['restricted']:
            def cohort(row):
                return tuple(row.get(key) for key in ('code', 'field', 'label', 'unit', 'source', 'series_basis'))

            admitted = {cohort(rows[ref]) for ref in evidence_scope['refs'] if ref in rows}
            excluded = [row['id'] for row in facts if cohort(row) not in admitted]
            if excluded:
                logic_exclusions[index] = excluded
        reasons = {edit['reason'] for edit in edits[first_edit:]}
        if reasons.intersection(('draft_quantity_unsubstantiated', 'draft_causal_attribution_unsubstantiated',
                                 'draft_disclosure_issuer_unsubstantiated')):
            notes = logic.get('workpaper_notes')
            notes = list(notes) if isinstance(notes, list) else []
            if 'draft_quantity_unsubstantiated' in reasons:
                notes.append('部分数值缺少可核对的依据，原稿已留存。')
            if 'draft_causal_attribution_unsubstantiated' in reasons:
                notes.append('确定性归因缺少可核对的依据，原稿已留存。')
            if 'draft_disclosure_issuer_unsubstantiated' in reasons:
                notes.append('引文未匹配对应发行人的已留存原件，原稿已留存。')
            logic['workpaper_notes'] = notes
        if context['bound_calculations']:
            existing = logic.get('calculation_refs')
            existing = existing if isinstance(existing, list) else []
            logic['calculation_refs'] = list(dict.fromkeys(
                [ref for ref in existing if isinstance(ref, str)] + context['bound_calculations']))
            calendar_refs = [ref for calc, _ in daily_rows['session'] if calc['id'] in context['bound_calculations']
                             for ref in calc['session_basis']['source_refs']]
            if calendar_refs:
                existing_sources = logic.get('source_refs')
                existing_sources = existing_sources if isinstance(existing_sources, list) else []
                logic['source_refs'] = list(dict.fromkeys(
                    [ref for ref in existing_sources if isinstance(ref, str)] + calendar_refs))
        if evidence_scope['observation_refs']:
            existing = logic.get('data_refs')
            existing = existing if isinstance(existing, list) else []
            logic['data_refs'] = list(dict.fromkeys(
                [ref for ref in existing if isinstance(ref, str)] + evidence_scope['observation_refs']))
    if retrospective_calendar_used:
        notes = proposal.get('reader_notes')
        notes = notes if isinstance(notes, list) else []
        note = '跨休市涨跌幅以留存收盘价和本次取得的交易日历回算，属于历史复盘。'
        if note not in notes:
            proposal['reader_notes'] = notes + [note]
    return edits
