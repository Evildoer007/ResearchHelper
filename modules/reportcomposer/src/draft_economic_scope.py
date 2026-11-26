"""Match draft economic readings to one named series and observation window."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import re
from typing import NamedTuple
import unicodedata

from .draft_analysis import _day
from .draft_sources import _economic_metadata
from .economic_observations import _period as statistical_period
from .numeric_integrity import DraftPeriod, _DRAFT_DAY_SPAN, _draft_periods, _row_matches_period, draft_quantities


_INDICATOR = re.compile(r'(?<![a-z0-9])[a-z]\d{9}(?![a-z0-9])')
_CHINABOND_YIELD_LABEL = re.compile(r'中债国债(到期|即期)收益率:([1-9]\d?)年')
_METRICS = re.compile(
    r'(?P<cpi>(?<![a-z0-9])cpi(?![-a-z0-9])|居民消费价格(?:总?指数)?|消费物价指数)'
    r'|(?P<cpi_w>(?<![a-z0-9])cpi-w(?![a-z0-9]))'
    r'|(?P<ppi>(?<![a-z0-9])ppi(?![-a-z0-9])|工业生产者出厂价格(?:总?指数)?|生产者价格指数)'
    r'|(?P<pmi>(?<![a-z0-9])pmi(?![-a-z0-9])|采购经理指数)'
    r'|(?P<gdp>(?<![a-z0-9])gdp(?![-a-z0-9])|国内生产总值)'
    r'|(?P<hicp>(?<![a-z0-9])hicp(?![-a-z0-9])|调和消费价格指数)'
    r'|(?P<unemployment>失业率)|(?P<retail>社会消费品零售总额)|(?P<industry>工业增加值)'
)
_REGIONS = re.compile(r'中国(?:内地|大陆|香港|澳门|台湾)|全国|中国|美国|欧元区|欧盟|英国|日本|韩国|德国|法国|'
                      r'加拿大|澳大利亚|印度|巴西|俄罗斯|越南|新加坡|全球|香港|澳门|台湾|广东|上海|北京')
_REGION_ALIASES = {'全国': '中国', '中国内地': '中国', '中国大陆': '中国', '香港': '中国香港',
                   '澳门': '中国澳门', '台湾': '中国台湾'}
_VARIANTS = re.compile(r'非食品|食品|核心|能源|服务|城市|农村|累计|未经季调|未季调|季调|非制造业|制造业|预测')
_RATE = re.compile(r'同比|环比')
_FREQUENCY = {'月度': 'M', '当月': 'M', '季度': 'Q', '当季': 'Q', '年度': 'Y', '当年': 'Y', '半年度': 'H'}
_FREQUENCY_NAME = re.compile(r'(?<![\d一二三四几每])(?:半年度|月度|当月|季度|当季|年度|当年)(?!期?末|底)')
_MONTH = re.compile(r'(?<![\d前后第个年每])(?P<month>1[0-2]|0?[1-9])月(?![\d日])')
_PERIOD_END = re.compile(r'\s*(?:期末|末|底)')
_COMPARE = re.compile(r'较|相比于?|相对|对比')
_THROUGH = re.compile(r'至|到|—|–|~|～')
_LATEST = re.compile(r'最新|当前|(?:最近|最后)(?:的)?(?:[一1]期|[一1]个?月)')
_LAST_PAIR = re.compile(r'(?:最近|最新|最后)(?:的)?(?:[两二2]期|[两二2]个?月)')
_READING = re.compile(r'读数|观测值|原值|本期值|当期值')
_CHANGE = re.compile(r'读数差|差值|变动|变化|上升|上涨|增长|扩大|走高|下降|下跌|收窄|回落|升|降')
_SAME_WINDOW = re.compile(r'期间|区间|期内|这段时间')
_RELATIVE = re.compile(r'(?:较|比|相对|相比于?)(?:(?P<month>上(?:一|个)?月)|(?P<quarter>上(?:一|个)?季(?:度)?)'
                       r'|(?P<year>去年同期|上年同期)|(?P<period>上(?:一)?期))')
_SHORT_READING = re.compile(r'\s*(?:本月|当月|本期|当期)?(?:同比|环比)?(?:读数|观测值|原值)?'
                            r'(?:为|约为|约|报|录得|是|[:：=])?\s*')
_LEVEL_FROM = re.compile(r'(?:由|从)\s*$')
_LEVEL_TO = re.compile(r'\s*(?:(?P<rise>上升|回升|上涨|升|增|涨)|(?P<fall>下降|回落|下跌|降|跌|减))?'
                        r'(?:至|到|变为|变成)\s*')
_PERCENTAGE_POINTS = 'raw_percentage_points_v1'
_LABEL_COMPONENT = re.compile(r'[\u3400-\u9fff]{2,24}\Z')
_WINDOW_EXTREME = re.compile(r'\s*(?:为|是)(?:该|所述)?(?:区间|样本|观察期)(?:内)?(?:的)?'
                             r'(?:(?P<low>低点|最低(?:值|点)?)|高点|最高(?:值|点)?)')


class SeriesName(NamedTuple):
    codes: frozenset = frozenset()
    metrics: frozenset = frozenset()
    regions: frozenset = frozenset()
    rates: frozenset = frozenset()
    variants: frozenset = frozenset()
    frequencies: frozenset = frozenset()


class Window(NamedTuple):
    current: DraftPeriod | None = None
    base: DraftPeriod | None = None
    latest: bool = False
    latest_pair: bool = False
    relative: str = ''
    ambiguous: bool = False


class ReadingPair(NamedTuple):
    prefix: str
    window: Window
    direction_valid: bool


def _compact(text):
    return ''.join(unicodedata.normalize('NFKC', text).casefold().split())


def _series_name(text, labels=(), qualifiers=None, *, context=()):
    text = _compact(text)
    aliases = frozenset(code for label, code in labels if re.search(r'(?<![0-9])' + re.escape(label), text))
    metrics = frozenset(match.lastgroup for match in _METRICS.finditer(text))
    variants = set(_VARIANTS.findall(text))
    for metric in metrics or context:
        variants.update(part for part in (qualifiers or {}).get(metric, ()) if part in text)
    return SeriesName(frozenset(_INDICATOR.findall(text)) | aliases, metrics,
                      frozenset(_REGION_ALIASES.get(region, region) for region in _REGIONS.findall(text)),
                      frozenset(_RATE.findall(text)), frozenset(variants),
                      frozenset(_FREQUENCY[item] for item in _FREQUENCY_NAME.findall(text)))


def _provider_qualifiers(labels):
    """Preserve named categories in the source label alongside the headline series."""
    categories = defaultdict(set)
    for label in set(labels):
        parts = _compact(label).split(':')
        if len(parts) < 2:
            continue
        metrics = {match.lastgroup for match in _METRICS.finditer(label.casefold())}
        if len(metrics) != 1:
            continue
        metric, = metrics
        categories[metric].update(part for part in parts
            if _LABEL_COMPONENT.fullmatch(part)
            and not (_METRICS.search(part) or _REGIONS.fullmatch(part)
                     or _RATE.search(part) or _FREQUENCY_NAME.search(part) or _VARIANTS.fullmatch(part)))
    return dict(categories)


def _period_spans(text):
    spans = _draft_periods(text)
    spans.extend((match.start(), match.end(), DraftPeriod(None, match['month'].zfill(2), statistic='M'))
                 for match in _MONTH.finditer(text) if not any(start <= match.start() < end for start, end, _ in spans))
    resolved = []
    for start, end, period in sorted(spans):
        if period.statistic in ('M', 'Y') and (suffix := _PERIOD_END.match(text, end)):
            period = period._replace(end=period.end or '12', statistic='end')
            end = suffix.end()
        resolved.append((start, end, period))
    return resolved


def observation_window(prefix, previous, scope=None, *, change):
    spans = _period_spans(prefix)
    # A comma may separate an explicit base-period phrase from its metric.
    if not spans and previous:
        prior = _period_spans(previous)
        if prior and not previous[prior[-1][1]:].strip(' \t，,：:'):
            prefix, spans = previous+'，'+prefix, prior
    if scope and scope.list_context and not change:
        return Window(current=scope.period)
    latest = bool(_LATEST.search(prefix))
    latest_pair = bool(_LAST_PAIR.search(prefix))
    relation = _RELATIVE.search(prefix)
    relative = relation.lastgroup if relation else ''
    if not spans:
        return Window(latest=latest, latest_pair=latest_pair, relative=relative)
    if not change:
        return Window(current=spans[-1][2], latest=latest, latest_pair=latest_pair,
                      relative=relative, ambiguous=len(spans) > 1 or bool(spans[0][2].start and _THROUGH.search(prefix)))
    if len(spans) == 1:
        start, end, period = spans[0]
        if period.start and _THROUGH.search(prefix[start:end]):
            day_precision = _DRAFT_DAY_SPAN.fullmatch(prefix[start:end]) is not None
            base = DraftPeriod(period.start[:-4] or period.year, period.start[-4:] if day_precision else period.start[-4:-2],
                               statistic='' if day_precision else 'M')
            current = period if day_precision else period._replace(end=period.end[:2], statistic='M')
            return Window(current=current, base=base, latest=latest, latest_pair=latest_pair)
        return Window(current=period, latest=latest, latest_pair=latest_pair, relative=relative)
    if len(spans) == 2:
        left, right = spans
        between = prefix[left[1]:right[0]]
        first, last = left[2], right[2]
        if first.year and not last.year:
            last = last._replace(year=first.year)
        if _COMPARE.search(between):
            return Window(current=first, base=last, latest=latest, latest_pair=latest_pair)
        if _THROUGH.search(between) or between.strip() == '-':
            return Window(current=last, base=first, latest=latest, latest_pair=latest_pair)
    return Window(ambiguous=True)


def reading_pair(clause, numbers, previous):
    if len(numbers) != 2 or any(number['unit'] != '%' for number, _, _ in numbers):
        return None
    first, last = (item[0] for item in numbers)
    prefix = clause[:first['start']]
    transition = _LEVEL_TO.fullmatch(clause[first['end']:last['start']])
    if not transition or not _LEVEL_FROM.search(prefix):
        return None
    difference = last['value'] - first['value']
    direction_valid = (not transition['rise'] or difference > 0) and (not transition['fall'] or difference < 0)
    return ReadingPair(prefix, observation_window(prefix, previous, change=True), direction_valid)


def continuation_window(window, previous):
    prior = _period_spans(previous)
    if len(prior) != 1 or window.ambiguous or window.latest or window.latest_pair or window.relative:
        return window
    last = prior[0][2]
    if not window.current:
        return window._replace(current=last)
    if (not window.current.year and last.year and window.current.end[:2] >= last.end[:2]):
        return window._replace(current=window.current._replace(year=last.year))
    return window


def preceding_comparison_window(window, prefix, context):
    if window != Window() or not _SAME_WINDOW.search(prefix):
        return window
    windows = {context[start:end] for start, end, period in _period_spans(context) if period.start}
    if len(windows) == 1:
        return observation_window(next(iter(windows)), '', change=True)
    return window


class DraftEconomicScope:
    def __init__(self, task, rows):
        owned = {record['id'] for record in task.get('source_records', [])
                 if record.get('task_id') == task.get('id') and record.get('research_version') == task.get('version')
                 and (record.get('source'), record.get('operation')) == ('ifind', 'edb_service')}
        self.rows = {row['id']: row for row in rows if row.get('artifact_id') in owned and row.get('field') == 'value'}
        self.names, self.days, self.labels, self.frequencies = {}, defaultdict(set), set(), {}
        self.qualifiers = _provider_qualifiers(row['label'] for row in self.rows.values())
        if not self.rows:
            return
        metadata = _economic_metadata(task)
        for ident, row in self.rows.items():
            code, label = row['code'], row['label']
            name = _series_name(label, qualifiers=self.qualifiers)
            info = metadata.get(code, {})
            frequency = info.get('freq', '')
            if info.get('freq_conflict') or frequency and name.frequencies and frequency not in name.frequencies:
                continue
            if not name.regions:
                name = name._replace(regions=_series_name(info.get('country', '')).regions)
            if frequency and not name.frequencies:
                name = name._replace(frequencies=frozenset((frequency,)))
            self.frequencies[code] = next(iter(name.frequencies)) if len(name.frequencies) == 1 else ''
            self.names[ident] = name._replace(codes=frozenset((code.casefold(),)))
            if not name.metrics and label != code and len(label) >= 3:
                literal = _compact(label)
                self.labels.add((literal, code.casefold()))
                if (frequency == 'D' and info.get('dataSource') == '中国债券信息网'
                        and (yield_label := _CHINABOND_YIELD_LABEL.fullmatch(literal))):
                    price_basis, years = yield_label.groups()
                    self.labels.update((alias, code.casefold()) for alias in (
                        f'{years}年期中债国债{price_basis}收益率',
                        f'中债{years}年期国债{price_basis}收益率',
                        f'{years}年期国债{price_basis}收益率'))
            if day := _day(row['period']):
                self.days[code, row.get('series_basis', '')].add(day)

    def claim(self, prefix, title, *, use_heading, previous=''):
        heading = _series_name(title, self.labels, self.qualifiers) if isinstance(title, str) else SeriesName()
        local = _series_name(prefix, self.labels, self.qualifiers, context=heading.metrics)
        if not (use_heading or local.codes or local.metrics or local.variants):
            heading = SeriesName()
        if not (local.codes or local.metrics or heading.codes or heading.metrics):
            return None
        region = _compact(previous).removesuffix('方面').removesuffix('口径')
        if not local.regions and _REGIONS.fullmatch(region):
            local = local._replace(regions=_series_name(region).regions)
        if local.metrics and heading.metrics and local.metrics != heading.metrics:
            return local
        if local.codes and heading.codes and local.codes != heading.codes:
            return local
        # An explicit local region disambiguates a heading spanning several regions.
        combined = SeriesName(*(value or inherited for value, inherited in zip(local, heading)))
        return combined._replace(variants=local.variants) if local.metrics or local.codes else combined

    def matches(self, ident, claim):
        name = self.names.get(ident)
        return (name is not None and all(not wanted or wanted == actual for wanted, actual in zip(claim[:4], name[:4]))
                and (not claim.frequencies or claim.frequencies == name.frequencies)
                and (claim.variants == name.variants or bool(claim.codes and not claim.variants)))

    def _dates(self, row):
        return sorted(self.days[row['code'], row.get('series_basis', '')])

    def period_matches(self, row, wanted):
        if row['id'] not in self.names:
            return False
        if wanted is None:
            return True
        kind = wanted.statistic
        if not kind:
            return _row_matches_period(row, wanted, False)
        day = _day(row.get('period'))
        if day is None or wanted.year and day.year != int(wanted.year):
            return False
        if kind in ('end', 'YTD'):
            return (kind == 'end' or '累计' in self.names[row['id']].variants) and day.strftime('%m') == wanted.end[:2]
        if kind not in self.names[row['id']].frequencies:
            return False
        if kind == 'H':
            return (day.month - 1) // 6 == (int(wanted.end[:2]) - 1) // 6
        month = wanted.end[:2] or '12'
        return statistical_period(day.isoformat(), kind) == statistical_period(f'{day.year}-{month}-01', kind)

    def _relative_matches(self, start, end, relation, code):
        if not relation:
            return True
        months = (end.year - start.year) * 12 + end.month - start.month
        expected = {'month': 1, 'quarter': 3, 'year': 12,
                    'period': {'M': 1, 'Q': 3, 'Y': 12}.get(self.frequencies[code])}
        return months == expected[relation]

    def continuation(self, prefix, previous):
        if not previous or not _SHORT_READING.fullmatch(_without_periods(prefix)):
            return ''
        amounts = draft_quantities(previous)
        if len(amounts) != 1 or previous[amounts[0]['end']:].strip():
            return ''
        prior = previous[:amounts[0]['start']]
        return prior if self.claim(prior, '', use_heading=False) else ''

    def reading(self, ident, claim, window):
        return self.matches(ident, claim) and self.reading_period(self.rows[ident], window)

    def reading_period(self, row, window):
        if window.ambiguous or window.base or window.latest_pair or window.relative:
            return False
        dates = self._dates(row)
        return self.period_matches(row, window.current) and (
            not window.latest or bool(dates and _day(row['period']) == dates[-1]))

    def contradicted_extreme(self, row, suffix, *context):
        """If a scoped superlative is false, retain the actual dated reading."""
        mark = _WINDOW_EXTREME.match(suffix)
        if not mark:
            return None
        for text in context:
            bounds = {(period.start, period.year + period.end) for _, _, period in _period_spans(text)
                      if period.start and period.year and len(period.start) == 8 and len(period.end) == 4}
            if bounds:
                break
        else:
            return None
        if len(bounds) != 1:
            return None
        first, last = next(iter(bounds))
        if not first <= row['period'].replace('-', '') <= last:
            return None
        comparison = min if mark['low'] else max
        peers = []
        for candidate in self.rows.values():
            if (candidate['code'] != row['code'] or candidate.get('series_basis') != row.get('series_basis')
                    or candidate['unit'] != row['unit'] or not first <= candidate['period'].replace('-', '') <= last):
                continue
            try:
                value = Decimal(str(candidate.get('raw_value', candidate['value'])))
            except InvalidOperation:
                continue
            if value.is_finite():
                peers.append((value, candidate['id']))
        extreme = comparison(peers, default=None)
        if extreme and extreme[0] != Decimal(str(row.get('raw_value', row['value']))):
            return mark.end(), extreme[1]
        return None

    def paired_rows(self, chosen, claim, pair):
        if not pair.direction_valid or pair.window.ambiguous:
            return [], []
        series = defaultdict(lambda: defaultdict(list))
        for row in chosen:
            if self.matches(row['id'], claim) and (day := _day(row['period'])):
                series[row['code'], row.get('series_basis', '')][day].append(row)
        pairs = []
        for dated in series.values():
            days = sorted(dated)
            if pair.window.latest_pair:
                days = self._dates(dated[days[-1]][0])[-2:]
                if not set(days) <= set(dated):
                    continue
            if pair.window.base or pair.window.current:
                first = [day for day in days if self.period_matches(dated[day][0], pair.window.base)]
                last = [day for day in days if self.period_matches(dated[day][0], pair.window.current)]
                if not pair.window.base and len(days) == 2:
                    first = first[:1]
                if len(first) != 1 or len(last) != 1:
                    continue
                days = first + last
            if len(days) == 2 and days[0] < days[1]:
                left, right = (dated[day] for day in days)
                if pair.window.latest and days[-1] != self._dates(right[0])[-1]:
                    continue
                if self._relative_matches(*days, pair.window.relative, right[0]['code']):
                    pairs.append((left, right))
        return pairs[0] if len(pairs) == 1 else ([], [])

    def change(self, calc, inputs, claim, window):
        if (window.ambiguous or calc['method'] != _PERCENTAGE_POINTS or len(inputs) != 2
                or not all(self.matches(row['id'], claim) for row in inputs)):
            return False
        first, last = inputs
        start, end = _day(first['period']), _day(last['period'])
        if not start or not end or start >= end:
            return False
        if not self.period_matches(first, window.base) or not self.period_matches(last, window.current):
            return False
        if not self._relative_matches(start, end, window.relative, last['code']):
            return False
        if window.latest_pair:
            return [start, end] == self._dates(last)[-2:]
        if window.latest:
            return bool(self._dates(last) and _day(last['period']) == self._dates(last)[-1])
        return True


def generic_reading(prefix):
    """Allow an explicitly named reading in a section heading to supply its measure."""
    return _READING.search(prefix) is not None or _SHORT_READING.fullmatch(_without_periods(prefix)) is not None


def _without_periods(text):
    for start, end, _ in reversed(_period_spans(text)):
        text = text[:start] + text[end:]
    return text


def reading_change(prefix, unit):
    return unit == '百分点' and _CHANGE.search(prefix) is not None
