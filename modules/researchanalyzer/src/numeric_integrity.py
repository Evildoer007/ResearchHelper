"""Bind financial quantities to cited originals or registered calculation outputs.

This is a numeric and explicit comparison contract, not a natural-language
entailment checker. Dates, identifiers and qualitative claims remain governed
by their own evidence contracts. No model or provider is invoked here.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import NamedTuple

from .contracts import (ContractError, DOCUMENT_CONTENT_INTENT, DOCUMENT_CONTENT_SCOPE, FINANCIAL_AMOUNT_UNITS,
                        METHOD_REGISTRY, calculation_dependency_hash, calculation_quantity_paths, document_content_answer,
                        method_reference_quantities, analysis_quantity_paths,
                        fingerprint, validate_evidence)
from .data_requirements import SCALED_COUNT_UNITS
from .evidence import evidence_binding


_SIGN = r"[+\-−＋－]?"
_ARABIC_MAGNITUDE = r"(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)(?:[eE][+\-]?\d+)?"
_CHINESE_MAGNITUDE = r"[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二两三四五六七八九]+)?"
_NUMBER = _SIGN + '(?:' + _ARABIC_MAGNITUDE + '|' + _CHINESE_MAGNITUDE + ')'
_UNITS = {
    '个百分点/年', '个百分点', '百分点/年', '基点/年', '%/年', '％/年', '元/股', '美元/吨', '元/吨',
    *FINANCIAL_AMOUNT_UNITS, '万亿元', '千亿元', '百亿元', '十亿元',
    '亿美元', '万美元', '美元', '港元', '欧元', '人民币元',
    '百万股', '万股', '亿股', '股', '万吨', '亿吨', '吨', '百分点', '基点',
    '%', '％', '倍', '万份', '亿份', '份', '点',
}


def _unit_alternatives(units):
    ordered = sorted(units, key=lambda unit: (-len(unit), unit))
    return '|'.join(map(re.escape, ordered))


def _quantity_expression(units):
    return re.compile(r'(?<![\d.,])(?P<number>' + _NUMBER + r')\s*(?P<unit>' + _unit_alternatives(units) + r')')


_QUANTITY = _quantity_expression(_UNITS)
_DRAFT_UNITS = SCALED_COUNT_UNITS.union(_UNITS)
_DRAFT_QUANTITY = _quantity_expression(_DRAFT_UNITS)
_PERCENT_WORDS = re.compile(r'百分之(?P<number>' + _NUMBER + r')')
_DRAFT_DENOMINATOR = re.compile(r'[/／]\s*(?:交易日|工作日|季度|[年月周日天股份手户人吨台件米]|[A-Za-z]+)')
DRAFT_CLAUSE_BOUNDARY = re.compile(r'([，；。！？;!?\n]+|(?<!\d),|,(?!\d))')
ORIGINAL_QUANTITY_QUALIFIER_POLICY = 'original_quantity_qualifier_version'
ORIGINAL_QUANTITY_QUALIFIER_VERSION = 2
_RANGE_NUMBER = _SIGN + _ARABIC_MAGNITUDE
_RANGE_UNITS = _unit_alternatives(_DRAFT_UNITS)
_HORIZONTAL = r'[ \u00a0\u202f\u3000]*'
_RANGE_JOIN_SPACE = _HORIZONTAL + r'(?:\r?\n' + _HORIZONTAL + r')?'
_QUANTITY_RANGE = re.compile(
    rf'(?<![\d.,A-Za-z_])(?P<first>{_RANGE_NUMBER})(?:{_HORIZONTAL}(?P<first_unit>{_RANGE_UNITS}))?'
    rf'(?P<before>{_RANGE_JOIN_SPACE})(?P<join>至|到|[~～—–－-])(?P<after>{_RANGE_JOIN_SPACE})'
    rf'(?P<last>{_RANGE_NUMBER}){_HORIZONTAL}(?P<last_unit>{_RANGE_UNITS})'
)
_RANGE_CONTEXT = re.compile(r'(?:介于|区间|范围|分布|波动)[^。；，\n]{0,12}$')
_CHANGE_CONTEXT = re.compile(r'(?:由|从)\s*$')
_CLOCK_CONTEXT = re.compile(r'(?:上午|下午|凌晨|中午|晚上|早上)\s*$')
_QUANTITY_PREFIX = re.compile(
    r'(?:(?P<maximum>(?:不|未|没有)(?:超过|高于|大于|多于)|至多|不超|<=|≤|＜＝)'
    r'|(?P<minimum>(?:不|未|没有)(?:低于|小于|少于)|至少|>=|≥|＞＝)'
    r'|(?P<greater>超过|高于|大于|多于|超|>|＞)'
    r'|(?P<less>低于|小于|少于|不足|不到|<|＜)'
    r'|(?P<approximate>大约|约为|约有|约达|约合|约占|将近|接近|'
    r'(?<![签预邀解违履续契公合条要立缔爽订改赴失承])约|(?<![最附临逼靠贴邻远亲年])近))'
    r'\s*(?:人民币|美元|港币|欧元)?\s*$'
)
_QUANTITY_SUFFIX = re.compile(
    r'\s*(?:(?P<minimum>及?以上)|(?P<maximum>及?以下|以内)'
    r'|(?P<approximate>左右|上下(?!游)|附近))'
)
_DRAFT_STATEMENT_BOUNDARY = re.compile(r'[；。！？;!?\n]+')
_DRAFT_CODE = re.compile(r'(?<!\d)(?:\d{5,6}\.(?:SH|SZ|BJ|HK|OF)|\d{4}\.HK)(?![A-Z])', re.I)
_DRAFT_PERIOD = re.compile(
    r'(?<![\d.])(?:19|20)\d{2}(?:-\d{2}-\d{2}|/\d{2}/\d{2}|-\d{2}(?![\d-])|\d{4}(?!\d)'
    r'|年(?:\d{1,2}月(?:\d{1,2}日)?)?|(?=[至—–~-](?:19|20)\d{2}))')
_DRAFT_REPORTING_SPAN = re.compile(
    r'(?:(?<![A-Za-z\d.])(?P<year>(?:19|20)\d{2})年?\s*|(?<![A-Za-z\d.上下前后每任个第近去过共续]))'
    r'(?:(?P<first>1[0-2]|0?[1-9])月?\s*[至到—–~～-]\s*(?P<last>1[0-2]|0?[1-9])月'
    r'|前(?P<cumulative>[两二三四234])个?季(?:度)?'
    r'|(?P<filing>[一三13])季报'
    r'|(?:第?(?P<quarter>[一二三四1234])季(?:度)?|[Qq](?P<quarter_number>[1-4])(?!\d))'
    r'|(?P<half>上半年|半年度|半年报|中报|下半年|[Hh][12](?!\d))'
    r'|全年|年度|年报)(?P<as_of>期末|末)?'
)
_DRAFT_CHINESE_DAY = r'(?:19|20)\d{2}年\d{1,2}月\d{1,2}日'
_DRAFT_FULL_DAY = r'(?:(?:19|20)\d{2}(?:-\d{2}-\d{2}|/\d{2}/\d{2})|' + _DRAFT_CHINESE_DAY + ')'
_DRAFT_DAY_SUFFIX = r'(?![\d.Tt]|\s+\d{1,2}:)'
_DRAFT_DAY_POINT = re.compile(r'(?<![\d.])' + _DRAFT_FULL_DAY + _DRAFT_DAY_SUFFIX)
_DRAFT_DAY_POINT_WITH_CODE = re.compile(
    r'(?<![\d.])(?:' + _DRAFT_FULL_DAY + _DRAFT_DAY_SUFFIX
    + '|' + _DRAFT_CHINESE_DAY + r'(?=' + _DRAFT_CODE.pattern + '))', re.I)
_DRAFT_DAY_SPAN = re.compile(r'(?<![\d.])(?P<first>' + _DRAFT_FULL_DAY
                             + r')\s*[至到—–~～-]\s*(?P<last>' + _DRAFT_FULL_DAY + ')')
_DRAFT_SHORT_CHINESE_DAY_SPAN = re.compile(
    r'(?<![\d.])(?P<year>(?:19|20)\d{2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})[日号]?'
    r'\s*[至到—–~～-]\s*(?:(?P<end_month>\d{1,2})月)?(?P<end_day>\d{1,2})[日号]')
_DRAFT_SHORT_NUMERIC_DAY_SPAN = re.compile(
    r'(?<![\d.])(?P<year>(?:19|20)\d{2})(?P<separator>[-/])(?P<month>\d{2})(?P=separator)(?P<day>\d{2})'
    r'\s*[至到—–~～]\s*(?P<end_month>\d{2})(?P=separator)(?P<end_day>\d{2})'
    r'(?![\d./%％-]|倍|元|个|[A-Za-z])')
_BASE_PERIOD_CONNECTOR = re.compile(r'\s*(?:较|比|相比于?|相对|与|对比)\s*')
_DRAFT_LIST_CONNECTOR = re.compile(r'\s*(?:、|，|,|和|与|及|以及)\s*')
_FEE_METHODS = {'fee_difference_v1', 'fee_difference_v2', 'management_custody_fee_comparison_v1', 'management_custody_fee_summary_v1'}
_DIGITS = dict(zip('零〇一二两三四五六七八九', [0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9]))
ANSWER_NUMERIC_FIELDS = ('answer',)
THESIS_NUMERIC_FIELDS = ('title', 'investment_meaning', 'limitation')


def verbatim_evidence_enabled(task):
    from .argument_support import enabled
    return task.get('verbatim_evidence_segments_version') == 1 and enabled(task)


def verbatim_evidence_eligible(task, item):
    return (verbatim_evidence_enabled(task) and item.get('task_id') == task['id']
        and item.get('research_version') == task['version']
        and item.get('source_check') == item.get('applicability_check') == '通过'
        and item.get('usage_scope') != DOCUMENT_CONTENT_SCOPE
        and item.get('artifact', {}).get('kind') in ('material', 'filing')
        and item.get('unit') == '不适用' and isinstance(item.get('text'), str) and bool(item['text'].strip()))


def verbatim_evidence_contract(task, by_question):
    eligible = {item['id'] for item in task.get('evidence', []) if verbatim_evidence_eligible(task, item)}
    return dict(version=1, enabled=verbatim_evidence_enabled(task), segment_keys=['quote_ref'],
        by_question={ident: [ref for ref in value['evidence_ids'] if ref in eligible]
                     for ident, value in by_question.items()},
        calculation_input_approval=False,
        rule='只显式选择同任务同版已核验文字摘录的quote_ref，内核逐字生成带引号的原文及来源绑定。'
             '需要保留原文金融数字但无独立数值字段时使用此片段，不把数字藏入text，不自行换算分数、单位或百分数。'
             '原文直引不产生可计算数值、不替代必要计算；解释和中文概括另写text并保留实际引用，仍须论证复核。')


def numeric_submission_contract(fields, *, allow_quotes=False):
    """Describe the validator's exact owner-specific shape, not example facts."""
    result = {'version': 1, 'type': 'object', 'allowed_fields': list(fields),
        'additional_fields': False, 'min_fields': 1,
        'field_values': {'type': 'array', 'min_items': 1,
            'text': {'required_fields': ['text'], 'text_type': 'string', 'additional_fields': False},
            'quantity': {'required_fields': ['source', 'ref', 'path', 'format', 'digits'],
                'source_values': ['evidence', 'calculation'], 'ref_type': 'string',
                'path_by_source': {'evidence': ['value', 'raw_value'],
                                   'calculation': analysis_quantity_paths()},
                'format_values': ['signed', 'comparison'],
                'digits': {'type': 'integer', 'minimum': 0, 'maximum': 8},
                'additional_fields': False, 'display_includes_unit': True}},
        'no_empty_field_placeholders': True, 'financial_approval': False}
    if allow_quotes:
        result['field_values']['verbatim_evidence'] = dict(required_fields=['quote_ref'],
            ref_type='string', additional_fields=False, value_conversion_allowed=False)
    return result


def numeric_segment_shape_diagnostics(segments, fields):
    """Retain only registered paths/types/counts, never arbitrary keys or values."""
    if not isinstance(segments, dict):
        return {'object_received': False}
    invalid_fields = set(segments) - set(fields)
    return {'object_received': True, 'unexpected_field_count': len(invalid_fields),
        'field_containers': [{
            'field': field, 'is_array': isinstance(segments[field], list),
            'empty_array': isinstance(segments[field], list) and not segments[field]}
            for field in fields if field in segments]}

# Explicit labels only. This does not infer causality or validate all prose.
# A price-change calculation cannot acquire another measure's meaning merely
# because the printed number and unit happen to match.
_PRICE_CHANGE_METHODS = {'observed_price_return_v1', 'observed_index_change_v1'}
_PRICE_LABEL = r'(?:未复权)?(?:价格(?:变化|变动|涨跌幅|涨跌|收益率)|指数(?:点位)?(?:区间)?(?:变化|变动|收益率)|(?:全收益|净全收益|价格)指数(?:点位)?(?:区间)?(?:变化|变动|收益率))'
_RETURN_MODIFIER = r'(?:持有(?:人|期)?|投资|实际|年化|超额|累计|绝对|含分红(?:再投资)?|分红再投资|费后|复权)'
_RETURN_LABEL = (rf'(?:(?:{_RETURN_MODIFIER}总?|总)(?:回报|收益)率?|(?:基金|单位)?净值收益率?'
                 r'|(?:累计|复权)净值(?:涨跌幅|涨幅|跌幅|变化率?))')
_OTHER_RETURN_LABEL = r'(?:' + _RETURN_LABEL + r'|(?:上涨|下跌|收益|盈利)概率|事件(?:因果|贡献)|因果贡献)'
_QUANTITY_LABEL = re.compile(_PRICE_LABEL + '|' + _OTHER_RETURN_LABEL)
_UNSUPPORTED_PRICE_SUFFIX = re.compile(r'^\s*(?:的|[（(]\s*)?' + _OTHER_RETURN_LABEL)


def measure_scope_contract():
    return {'version': 1, 'restricted_methods': sorted(_PRICE_CHANGE_METHODS),
            'rule': '价格或同版指数变化数量不能以相同数值和单位借用为净值收益、持有人总回报、超额收益、年化收益、概率或因果贡献；旧式正文和结构片段均核查显式口径标签。',
            'limitation': '只核查登记方法与显式数量标签的冲突，不是任意金融语义、因果或投资质量验证；其他解释仍需证据和金融阅读判断。'}


def _price_quantity_scope(text, start, end, kind, item):
    if kind != 'calculation' or item.get('method') not in _PRICE_CHANGE_METHODS:
        return True
    prefix = re.split(r'[。；;，,\n]', text[:start])[-1]
    labels = list(_QUANTITY_LABEL.finditer(prefix))
    if labels and re.fullmatch(_OTHER_RETURN_LABEL, labels[-1].group()):
        return False
    suffix = re.split(r'[。；;，,\n]', text[end:])[0]
    return _UNSUPPORTED_PRICE_SUFFIX.match(suffix) is None


NUMERIC_REJECTION_CODES = {
    '普通文字片段不能藏入金融数量': 'unbound_numeric_prose',
    '原文引用未选择当前问题的合格文字依据': 'ineligible_quote_reference',
    '数字片段未引用当前陈述来源': 'quantity_reference_outside_statement',
    '原文与数字片段生成文本不一致': 'generated_display_mismatch',
    '数字片段字段不属于当前陈述': 'invalid_numeric_field',
    '比较片段必须引用登记计算主结果': 'unregistered_comparison',
}


def _fail(message):
    error = ContractError('数字主张无法核验：' + message)
    if message in NUMERIC_REJECTION_CODES:
        error.numeric_failure_code = NUMERIC_REJECTION_CODES[message]
    raise error


def _decimal(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        _fail('来源数值必须为有限数字')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        _fail('来源数值无效')
    if not result.is_finite():
        _fail('来源数值不是有限值')
    return result


def _number(token):
    token = token.replace(',', '').replace('−', '-').replace('－', '-').replace('＋', '+')
    try:
        return Decimal(token)
    except InvalidOperation:
        pass
    sign = -1 if token.startswith('-') else 1
    token = token.lstrip('+-')
    integer, _, fraction = token.partition('点')
    if not integer or any(c not in _DIGITS and c not in '十百千万亿' for c in integer):
        _fail('数量写法未登记')
    total = section = digit = 0
    # Digit-by-digit notation has no place-value markers (e.g. 二〇二六).
    # Keep every digit instead of overwriting all but the last one.
    characters = integer
    if all(char in _DIGITS for char in integer):
        total = int(''.join(str(_DIGITS[char]) for char in integer))
        characters = ''
    for char in characters:
        if char in _DIGITS:
            digit = _DIGITS[char]
        elif char in '十百千':
            section += (digit or 1) * {'十': 10, '百': 100, '千': 1000}[char]
            digit = 0
        else:
            section += digit
            if char == '亿':
                # 亿 scales the complete preceding group, including 万.
                # 一万亿 is 10^12, not 10^4 + 10^8.
                total = (total + section or 1) * 100000000
            else:
                total += (section or 1) * 10000
            section = digit = 0
    result = Decimal(total + section + digit)
    if fraction:
        result += Decimal('0.' + ''.join(str(_DIGITS[c]) for c in fraction))
    return result * sign


_DOCUMENT_COUNT_SUFFIX = re.compile(
    r'\s*(?:[A-Za-z][A-Za-z0-9_.-]{0,63}\s+){0,3}'
    r'(?:接口响应|响应快照|原始快照|数据快照|查询记录|原件|材料|文件|公告|报告|响应|快照)')
_DOCUMENT_MODIFIER = (r'(?:最近(?:可得|取得|披露)|最新(?:可得|披露)?|本次(?:下载|取得|读取)?|'
    r'实际(?:下载|取得|读取|披露)?|已(?:经)?(?:披露|下载|取得|核验|读取)|公开(?:披露)?|'
    r'正式|完整|定期|年度|中期|半年度|季度|原始|同版|相关|上述|所用|可得|\d{4}年)(?:的)?')
_QUALIFIED_DOCUMENT_COUNT_SUFFIX = re.compile(
    r'\s*(?:[A-Za-z][A-Za-z0-9_.-]{0,63}\s+){0,3}(?:'+_DOCUMENT_MODIFIER+r'){0,6}'
    r'(?:接口响应|响应快照|原始快照|数据快照|查询记录|招募说明书|招股说明书|说明书|'
    r'原件|材料|文件|公告|报告|响应|快照|半年报|年报|中报|季报)')
_CITATION_COUNT_SUFFIX = re.compile(
    r'\s*(?:[A-Za-z][A-Za-z0-9_.-]{0,63}\s+){0,3}(?:'+_DOCUMENT_MODIFIER+r'){0,6}'
    r'(?:原文片段|引用片段|阅读片段|摘录|引文)')


def _is_document_count(suffix, *, expanded=False, citations=False):
    expression = _QUALIFIED_DOCUMENT_COUNT_SUFFIX if expanded else _DOCUMENT_COUNT_SUFFIX
    match = expression.match(suffix) or (_CITATION_COUNT_SUFFIX.match(suffix) if citations else None)
    tail=suffix[match.end():] if match else ''
    if citations:tail=tail.lstrip()
    return bool(match and not tail.startswith(('基金', '份额', '股', '证券')))


def quantity_qualifiers_enabled(task):
    version = task.get(ORIGINAL_QUANTITY_QUALIFIER_POLICY)
    if version is None:
        return False
    if type(version) is not int or version not in (1, ORIGINAL_QUANTITY_QUALIFIER_VERSION):
        _fail('原文数量限定规则版本未登记')
    return True


def quantity_ranges_enabled(task):
    return quantity_qualifiers_enabled(task) and task[ORIGINAL_QUANTITY_QUALIFIER_POLICY] == ORIGINAL_QUANTITY_QUALIFIER_VERSION


class QuantityQualifier(NamedTuple):
    kind: str
    start: int
    end: int


def literal_quantity_qualifiers(text, start, end):
    """Read explicit qualifiers attached to one literal number and its unit."""
    prefixes = []
    left = max(0, start - 40)
    while prefix := _QUANTITY_PREFIX.search(text, left, start):
        prefixes.append(QuantityQualifier(prefix.lastgroup, *prefix.span(prefix.lastgroup)))
        start = prefix.start()
    qualifiers = list(reversed(prefixes))
    suffix = _QUANTITY_SUFFIX.match(text, end)
    if suffix:
        qualifiers.append(QuantityQualifier(suffix.lastgroup, *suffix.span(suffix.lastgroup)))
    return tuple(qualifiers)


class QuantityRange(NamedTuple):
    start: int
    end: int
    first: Decimal
    last: Decimal
    first_unit: str
    last_unit: str


def _quantity_unit(unit):
    return unit.replace('％', '%').replace('个百分点', '百分点')


def literal_quantity_ranges(text):
    """Read two written endpoints and their units, without choosing a point."""
    for match in _QUANTITY_RANGE.finditer(text):
        prefix = text[max(0, match.start()-28):match.start()]
        end = match.end()
        suffix = text[end:end+12].lstrip()
        explicit_range = bool(_RANGE_CONTEXT.search(prefix) or suffix.startswith(('之间', '区间内', '范围内')))
        join, before, after = match['join'], match['before'], match['after']
        if not explicit_range and _CHANGE_CONTEXT.search(prefix):
            continue
        if join not in ('至', '到') and '\n' in before + after:
            continue
        if not explicit_range and join in ('-', '－') and before and not after:
            # A separated signed value may be the next PDF table cell.
            continue
        if (not explicit_range and join in ('-', '－') and not match['first_unit']
                and (before or after)):
            continue
        last_unit = _quantity_unit(match['last_unit'])
        if last_unit == '份' and _is_document_count(text[end:], expanded=True, citations=True):
            continue
        if last_unit == '点' and _CLOCK_CONTEXT.search(prefix):
            continue
        if text[end:end+1] in ('/', '／'):
            denominator = _DRAFT_DENOMINATOR.match(text, end)
            if denominator:
                end = denominator.end()
                last_unit += '/' + denominator.group()[1:].strip()
            else:
                last_unit += '/'
                end += 1
        first_unit = _quantity_unit(match['first_unit']) if match['first_unit'] else last_unit
        try:
            first, last = _number(match['first']), _number(match['last'])
        except ContractError:
            continue
        yield QuantityRange(match.start(), end, first, last, first_unit, last_unit)


def split_draft_clauses(text, *, protect_ranges=False):
    """Keep an original range together when a PDF wraps after its join word."""
    if not protect_ranges or '\n' not in text:
        return DRAFT_CLAUSE_BOUNDARY.split(text)
    wrapped = [span for span in literal_quantity_ranges(text) if '\n' in text[span.start:span.end]]
    parts, start = [], 0
    for boundary in DRAFT_CLAUSE_BOUNDARY.finditer(text):
        if any(span.start <= boundary.start() and boundary.end() <= span.end for span in wrapped):
            continue
        parts.extend((text[start:boundary.start()], boundary.group()))
        start = boundary.end()
    parts.append(text[start:])
    return parts


def _quantities(text, *, document_count_grammar=0, strict_units=True, scaled_counts=False):
    result = []
    quantity = _DRAFT_QUANTITY if scaled_counts else _QUANTITY
    for expression in (quantity, _PERCENT_WORDS):
        for match in expression.finditer(text):
            if (expression is quantity and match.group('unit') == '份' and
                    _is_document_count(text[match.end():], expanded=document_count_grammar in (1,2),
                                       citations=document_count_grammar==2)):
                # Counted originals and response files are not fund shares.
                # Only an explicit document noun qualifies; bare shares,
                # per-share NAV, percentages and money remain source-bound.
                continue
            if expression is quantity and match.group('unit') == '点':
                prefix = text[:match.start()]
                # Ordinal/list prose and explicitly qualified clock times are
                # not index quantities. Bare financial point claims still need
                # a same-unit source; do not exempt them by keyword guessing.
                clock = re.search(r'(?:上午|下午|凌晨|中午|晚上|早上)\s*$', prefix) and 0 <= _number(match.group('number')) < 24
                prose = prefix.endswith('第') or match.group('number') == '一' and prefix.endswith(('这', '那'))
                if prose or clock:
                    continue
            end = match.end()
            unit = '%' if expression is _PERCENT_WORDS else _quantity_unit(match.group('unit'))
            remainder = text[end:]
            if remainder.startswith(('/', '／')):
                if strict_units:
                    _fail('数量单位包含未登记的分母')
                denominator = _DRAFT_DENOMINATOR.match(remainder)
                if denominator:
                    end += denominator.end()
                    unit += '/' + denominator.group()[1:].strip()
                elif not re.match(r'[/／]\s*' + _NUMBER, remainder):
                    # An unfamiliar denominator is unbound, not a plain amount.
                    end += 1
                    unit += '/'
            token = match.group('number')
            result.append({'start': match.start(), 'end': end, 'text': text[match.start():end],
                           'value': _number(token), 'unit': unit,
                           'digits': len(token.partition('.')[2]) if '.' in token and 'e' not in token.lower() else None})
    return sorted(result, key=lambda item: item['start'])


def draft_quantities(text, *, scaled_counts=False):
    """Find recognizable draft quantities without turning syntax into a delivery gate."""
    return _quantities(text, document_count_grammar=2, strict_units=False, scaled_counts=scaled_counts)


class DraftPeriod(NamedTuple):
    year: str | None
    end: str
    start: str = ''
    statistic: str = ''


class DraftQuantityScope(NamedTuple):
    code: str | None
    period: DraftPeriod | None
    base: DraftPeriod | None = None
    list_context: str = ''


def _date_key(text):
    return ''.join(part.zfill(2) for part in re.findall(r'\d+', text))


def _month_span(year, first, last, *, as_of=False):
    # Reporting periods describe calendar months. A yearless February stays
    # month-precise because its final day cannot be known.
    ending = f'{last:02d}'
    if year or last != 2:
        ending += f'{monthrange(int(year or 2000), last)[1]:02d}'
    return DraftPeriod(year, ending, '' if as_of else f'{year or ""}{first:02d}01')


def draft_day_intervals(text):
    """Return the source spans and dates of full or abbreviated day ranges."""
    matches = [(match, _date_key(match['first']), _date_key(match['last']))
               for match in _DRAFT_DAY_SPAN.finditer(text)]
    for pattern in (_DRAFT_SHORT_CHINESE_DAY_SPAN, _DRAFT_SHORT_NUMERIC_DAY_SPAN):
        for match in pattern.finditer(text):
            year, month = match['year'], match['month']
            first = year + month.zfill(2) + match['day'].zfill(2)
            last = year + (match['end_month'] or month).zfill(2) + match['end_day'].zfill(2)
            matches.append((match, first, last))
    intervals = []
    for match, first, last in matches:
        try:
            start = date(int(first[:4]), int(first[4:6]), int(first[6:]))
            end = date(int(last[:4]), int(last[4:6]), int(last[6:]))
        except ValueError:
            continue
        if start <= end:
            intervals.append((*match.span(), start, end))
    return sorted(intervals)


def draft_day_points(text, intervals, *, adjacent_security_code=False):
    """Read complete literal dates outside the ranges already recognized."""
    pattern = _DRAFT_DAY_POINT_WITH_CODE if adjacent_security_code else _DRAFT_DAY_POINT
    for match in pattern.finditer(text):
        start, end = match.span()
        if any(left < end and start < right for left, right, _, _ in intervals):
            continue
        key = _date_key(match.group())
        try:
            day = date(int(key[:4]), int(key[4:6]), int(key[6:]))
        except ValueError:
            continue
        yield start, end, day


def _draft_periods(text):
    spans = [(begin, end, DraftPeriod(f'{last.year:04d}', f'{last.month:02d}{last.day:02d}',
                                    first.isoformat().replace('-', '')))
             for begin, end, first, last in draft_day_intervals(text)]
    for match in _DRAFT_REPORTING_SPAN.finditer(text):
        if any(start <= match.start() < end for start, end, _ in spans):
            continue
        as_of = bool(match['as_of'])
        statistic = ''
        if match['first']:
            first, last = int(match['first']), int(match['last'])
            if first > last:
                continue
        elif match['cumulative']:
            first, last = 1, 3 * _number(match['cumulative'])
            statistic = 'YTD'
        elif quarter := match['quarter'] or match['quarter_number']:
            last = 3 * _number(quarter)
            first = last - 2
            statistic = 'Q'
        elif match['filing']:
            first, last = 1, 3 * _number(match['filing'])
            # The third-quarter report can include both its single-quarter
            # and year-to-date columns. A caption only identifies the end.
            as_of = last == 9
        elif match['half']:
            first, last = (7, 12) if match['half'] in ('下半年', 'H2', 'h2') else (1, 6)
            statistic = 'H'
        else:
            first, last = 1, 12
            statistic = 'Y'
        if as_of and text[match.end():].lstrip().startswith('累计'):
            first, as_of = 1, False
            statistic = 'YTD'
        period = _month_span(match['year'], int(first), int(last), as_of=as_of)._replace(
            statistic='end' if as_of else statistic)
        spans.append((match.start(), match.end(), period))
    for match in _DRAFT_PERIOD.finditer(text):
        if not any(start < match.end() and match.start() < end for start, end, _ in spans):
            key = _date_key(match.group())
            statistic = {4: 'Y', 6: 'M'}.get(len(key), '')
            spans.append((match.start(), match.end(), DraftPeriod(key[:4], key[4:], statistic=statistic)))
    return sorted(spans, key=lambda item: item[0])


def _respective_quantity_scope(text, start):
    if text.rfind('分别', 0, start) < 0:
        return None
    prefix = _DRAFT_STATEMENT_BOUNDARY.split(text[:start])[-1]
    marker = prefix.rfind('分别')
    if marker < 0:
        return None
    periods = []
    for begin, end, period in reversed(_draft_periods(prefix[:marker])):
        if periods and not _DRAFT_LIST_CONNECTOR.fullmatch(prefix[end:periods[-1][0]]):
            break
        periods.append((begin, period))
    if len(periods) < 2:
        return None
    after_marker = prefix[marker + len('分别'):] + _DRAFT_STATEMENT_BOUNDARY.split(text[start:])[0]
    numbers = []
    for number in draft_quantities(after_marker):
        if numbers and not _DRAFT_LIST_CONNECTOR.fullmatch(after_marker[numbers[-1]['end']:number['start']]):
            break
        numbers.append(number)
    if len(numbers) != len(periods):
        return None
    offset = len(prefix) - marker - len('分别')
    context = prefix[:marker] + after_marker[:numbers[0]['start']]
    codes = {code.upper() for code in _DRAFT_CODE.findall(context)}
    if len(codes) > 1:
        return None
    for number, (_, period) in zip(numbers, reversed(periods)):
        if number['start'] == offset:
            return DraftQuantityScope(next(iter(codes), None), period, list_context=context)
    return None


def draft_quantity_scope(text, start):
    """Resolve only explicit code and period wording beside the quantity."""
    paired = _respective_quantity_scope(text, start)
    if paired is not None:
        return paired
    prefix = DRAFT_CLAUSE_BOUNDARY.split(text[:start])[-1]
    suffix = DRAFT_CLAUSE_BOUNDARY.split(text[start:])[0]
    codes = _DRAFT_CODE.findall(prefix) or _DRAFT_CODE.findall(suffix)
    code = codes[-1].upper() if codes else None
    periods = _draft_periods(prefix)
    if not periods:
        return DraftQuantityScope(code, None)
    last_start, last_end, period = periods[-1]
    if len(periods) > 1:
        _, prior_end, prior = periods[-2]
        if _BASE_PERIOD_CONNECTOR.fullmatch(prefix[prior_end:last_start]):
            if (not period.end and period.year and prior.end
                    and prefix[last_end:].lstrip().startswith(('同期', '同季度', '同月'))):
                start = period.year + prior.start[-4:] if prior.start else ''
                period = DraftPeriod(period.year, prior.end, start)
            return DraftQuantityScope(code, prior, period)
        # A parenthetical date may give an endpoint for the preceding duration.
        if prior.start and not period.start and prior.year == period.year and prior.end == period.end:
            period = prior
    return DraftQuantityScope(code, period)


def _financial_claim_period(period):
    if period is None or period.start:
        return period
    if not period.end:
        return _month_span(period.year, 1, 12)
    if len(period.end) == 2:
        month = int(period.end)
        if 1 <= month <= 12:
            return _month_span(period.year, month, month)
    return period


def _period_matches(wanted, actual, *, require_duration):
    if wanted is None:
        return True
    if (wanted.year and wanted.year != actual.year) or not actual.end.startswith(wanted.end):
        return False
    return not require_duration or not wanted.start or actual.start.endswith(wanted.start)


def _row_periods(row):
    value = str(row.get('period') or '')
    periods = [period for _, _, period in _draft_periods(value)]
    if not periods and re.fullmatch(r'\d{4}', value):
        periods = [DraftPeriod(value, '')]
    if row.get('period_kind') == 'cumulative':
        periods = [period._replace(start=period.year + '0101')
                   if not period.start and period.year and len(period.end) == 4 else period for period in periods]
    return periods


def draft_quantity_source_matches(row, scope, *, financial=False, inputs=()):
    if scope.code and str(row.get('code', '')).upper() != scope.code:
        return False
    wanted = _financial_claim_period(scope.period) if financial else scope.period
    if scope.base is not None:
        if row.get('method') not in ('raw_change_v1', 'raw_difference_v1') or len(inputs) != 2:
            return False
        base = _financial_claim_period(scope.base) if financial else scope.base
        if not _row_matches_period(inputs[0], base, financial):
            return False
    if wanted is None:
        return True
    if not financial and wanted.start and row.get('method') == 'raw_change_v1' and len(inputs) == 2:
        from .draft_sources import _sequence_day
        start, end = (_sequence_day(item.get('period')) for item in inputs)
        if start is None or end is None:
            return False
        actual = DraftPeriod(str(end.year), end.strftime('%m%d'), start.strftime('%Y%m%d'))
        return _period_matches(wanted, actual, require_duration=True)
    if financial and inputs:
        current = [item for item in inputs if item['period'] == row.get('period')]
        return bool(current) and all(_row_matches_period(item, wanted, True) for item in current)
    return _row_matches_period(row, wanted, financial)


def _row_matches_period(row, wanted, financial):
    require_duration = financial and row.get('period_kind') != 'point_in_time'
    return any(_period_matches(wanted, period, require_duration=require_duration) for period in _row_periods(row))


def draft_quantity_scopes_match(claim, source, *, financial=False):
    """A document paraphrase must preserve the literal comparison periods."""
    if claim.code and source.code != claim.code:
        return False
    for wanted, actual in ((claim.period, source.period), (claim.base, source.base)):
        if wanted is None:
            continue
        if actual is None:
            return False
        if financial:
            wanted, actual = _financial_claim_period(wanted), _financial_claim_period(actual)
        if not _period_matches(wanted, actual, require_duration=True):
            return False
    return True


def _statement_quantities(text, task):
    version = (task['nonfinancial_count_grammar_version'] if isinstance(task,dict)
               and type(task.get('nonfinancial_count_grammar_version')) is int
               and task['nonfinancial_count_grammar_version'] in (1,2) else 0)
    return _quantities(text, document_count_grammar=version)


def _same_unit(source, display):
    # The text scanner already canonicalizes 个百分点 to 百分点. Apply the
    # identical spelling rule to sources; never convert %/bp/index points.
    source = str(source).replace('％', '%').replace('个百分点', '百分点')
    return display == source or source.endswith('/年') and display == source[:-2]


def _matches(source, quantity):
    if source != 0 and quantity['value'] == 0:
        return False
    if source == quantity['value']:
        return True
    digits = quantity['digits']
    if digits is None or digits > 8:
        return False
    # Rounding is a display choice, never a scale conversion.
    try:
        return source.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP) == quantity['value']
    except InvalidOperation:
        return False


def _sources(task, proposal, refs, calculation_refs):
    if any(not isinstance(items, list) or any(not isinstance(ref, str) for ref in items)
           for items in (refs, calculation_refs)):
        _fail('引用必须为标识列表')
    questions = proposal.get('question_ids')
    if questions is None and proposal.get('id') in {q['id'] for q in task.get('questions', [])}:
        questions = [proposal['id']]
    if not isinstance(questions, list) or not questions or any(q not in {x['id'] for x in task.get('questions', [])} for q in questions):
        _fail('缺少已登记的问题绑定')
    sources = {}
    for kind, items, selected in (('evidence', task.get('evidence', []), refs), ('calculation', task.get('calculations', []), calculation_refs)):
        index = {item['id']: item for item in items}
        for ref in selected:
            item = index.get(ref)
            if item is None or item.get('task_id') != task['id'] or item.get('research_version') != task['version']:
                _fail('引用来源不属于当前任务版本')
            if kind == 'evidence' and item.get('usage_scope') == DOCUMENT_CONTENT_SCOPE and task.get('intent') != DOCUMENT_CONTENT_INTENT:
                _fail('原件内容核对来源不能用作普通研究')
            scope = item.get('question_ids', [])
            if 'question_ids' not in item:
                # Older built-in cases predate per-question source scopes.
                # Only originals passing their existing synthetic provenance
                # contract may retain that compatibility; never real sources.
                originals = [item] if kind == 'evidence' else [
                    e for e in task.get('evidence', []) if e['id'] in item.get('input_refs', [])]
                if originals and all(e.get('source') == '合成验收原件' and str(e.get('nature', '')).startswith('合成') for e in originals):
                    for original in originals:
                        validate_evidence(original, task)
                    scope = [q['id'] for q in task['questions']]
            from .contracts import evidence_applies_to_answer, calculation_applies_to_answer
            indirectly_scoped = any(
                evidence_applies_to_answer(task,item,ident,calculation_refs) if kind == 'evidence'
                else calculation_applies_to_answer(task,item,ident) for ident in questions)
            if not set(questions).intersection(scope) and not indirectly_scoped:
                _fail('引用来源不属于当前问题')
            if kind == 'calculation' and not set(item.get('input_refs', [])).issubset(refs):
                _fail('计算输入未完整引用')
            sources[kind, ref] = item
    return sources, questions


def _project(kind, item, path):
    if kind == 'evidence':
        if path not in ('value', 'raw_value') or path not in item:
            _fail('证据数值字段不存在')
        unit = item.get('raw_unit' if path == 'raw_value' else 'unit')
        value = _decimal(item[path])
        projection = 'identity'
    else:
        method = item.get('method')
        if method not in METHOD_REGISTRY:
            _fail('计算方法未登记')
        reference = method_reference_quantities(method).get(path)
        if reference is not None:
            return _decimal(reference['value']), reference['unit'], 'registered_method_reference'
        if path not in calculation_quantity_paths(method):
            _fail('计算输出字段未登记')
        output = item.get('output', {})
        if path not in output:
            _fail('计算输出不存在')
        value = _decimal(output[path])
        secondary = METHOD_REGISTRY[method].get('additional_numeric_outputs', {}).get(path)
        if secondary:
            unit = output.get(secondary['unit_field'])
            if unit != secondary['unit']:
                _fail('计算次级输出单位与登记方法不一致')
        else:
            unit = output.get('unit') if path == 'value' else '%' if path == 'premium' else '元/股'
        projection = 'ratio_to_percent' if path == 'premium' else 'identity'
        if projection == 'ratio_to_percent':
            value *= 100
        if path == 'value' and unit != METHOD_REGISTRY[method]['output_unit']:
            _fail('计算输出单位与登记方法不一致')
    if not isinstance(unit, str) or not unit or unit == '不适用':
        _fail('来源单位不能用于数字陈述')
    return value, unit, projection


def _objects(item, sources):
    method = item.get('method')
    if method not in _FEE_METHODS:
        _fail('比较表达未登记，须使用明确数值片段')
    bindings = item.get('input_bindings', {})
    keys = ('fee_a', 'fee_b') if method in ('fee_difference_v1', 'fee_difference_v2') else ('management_a', 'management_b')
    objects = []
    for key in keys:
        original = sources.get(('evidence', bindings.get(key)), {})
        name = original.get('object')
        if not isinstance(name, str) or not name:
            _fail('比较对象缺少原值绑定')
        objects.append(name)
    if objects[0] == objects[1]:
        _fail('比较对象无法区分')
    return objects


def _aliases(name):
    result = {name}
    match = re.search(r'ETF[-]?([AB甲乙])$', name, re.I)
    if match:
        result.update((match.group(), match.group(1)))
    return sorted(result, key=len, reverse=True)


def _position(text, name):
    positions = []
    for alias in _aliases(name):
        pattern = re.escape(alias)
        if alias.isascii():
            pattern = r'(?<![A-Za-z0-9])' + pattern + r'(?![A-Za-z0-9])'
        positions.extend(match.start() for match in re.finditer(pattern, text))
    return min(positions) if positions else None


def _direction(text, quantity, item, sources):
    prefix = re.split(r'[。；;，,\n]', text[:quantity['start']])[-1]
    direction = re.search(r'(高|低)(?:出|于)?\s*$', prefix)
    if direction is None:
        return None
    a, b = _objects(item, sources)
    a_pos, b_pos = _position(prefix, a), _position(prefix, b)
    if a_pos is None and b_pos is None:
        _fail('比较方向缺少可确认对象，须使用numeric_segments')
    if '比' in prefix or '较' in prefix:
        if a_pos is None or b_pos is None or a_pos == b_pos:
            _fail('比较双方无法确认，须使用numeric_segments')
        orientation = 1 if a_pos < b_pos else -1
    else:
        if a_pos is not None and b_pos is not None:
            _fail('比较对象朝向不明确，须使用numeric_segments')
        orientation = 1 if a_pos is not None else -1
    value, _, _ = _project('calculation', item, 'value')
    expected = value * orientation
    if quantity['value'] < 0 or expected == 0 or (direction.group(1) == '高') != (expected > 0):
        _fail('比较方向与计算结果不一致')
    return abs(expected)


def _source_hash(kind, item):
    """Bind facts and calculation dependencies, not renewed verification wrappers."""
    identity = {'task_id': item.get('task_id'), 'research_version': item.get('research_version')}
    if kind == 'evidence':
        return fingerprint(dict(identity, content_version=item.get('content_version'), binding=evidence_binding(item)))
    if kind == 'calculation':
        return fingerprint(dict(identity, id=item.get('id'), question_ids=item.get('question_ids'),
                                dependency_hash=calculation_dependency_hash(item), output=item.get('output')))
    return fingerprint(item)


def _binding(kind, ref, item, path, value, unit, projection, display, start, end):
    result = {'source_kind': kind, 'ref': ref, 'path': path, 'source_hash': _source_hash(kind, item),
            'projection': projection, 'canonical_value': str(value), 'unit': unit,
            'display': display, 'span': [start, end]}
    if projection == 'registered_method_reference':
        definition = method_reference_quantities(item['method'])[path]
        result['registered_reference'] = dict(method=item['method'], path=path, definition=definition,
            definition_hash=fingerprint(definition))
    return result


def _legacy_bindings(text, sources, task=None, *, reference_bindings=()):
    bindings = []
    for quantity in _statement_quantities(text, task):
        # Only a reference already rendered by _segments can license this
        # exact span. Ordinary prose still cannot borrow a method constant.
        reference = next((binding for binding in reference_bindings
            if binding.get('projection')=='registered_method_reference'
            and binding['span']==[quantity['start'],quantity['end']]), None)
        if reference is not None:
            key = (reference['source_kind'], reference['ref'])
            if key in sources and key[0]=='calculation':
                value, unit, projection = _project(key[0], sources[key], reference['path'])
                if (projection=='registered_method_reference' and _same_unit(unit, quantity['unit'])
                        and _matches(value, quantity)):
                    bindings.append(reference)
                    continue
        found = None
        prefix = re.split(r'[。；;，,\n]', text[:quantity['start']])[-1]
        directional = re.search(r'(高|低)(?:出|于)?\s*$', prefix) is not None
        if (isinstance(task,dict) and type(task.get('numeric_lookup_isolation_version')) is int
                and task['numeric_lookup_isolation_version'] == 1 and not directional):
            explicit = next((binding for binding in reference_bindings
                if binding.get('projection') in ('identity','ratio_to_percent')
                and binding.get('span') == [quantity['start'],quantity['end']]), None)
            if explicit is not None:
                key = (explicit['source_kind'],explicit['ref'])
                if key not in sources or explicit.get('source_hash') != _source_hash(key[0],sources[key]):
                    _fail('数字片段来源已变化，不能借用另一同值来源')
                value,unit,projection = _project(key[0],sources[key],explicit['path'])
                if (not _same_unit(unit,quantity['unit']) or not _matches(value,quantity)
                        or not _price_quantity_scope(text,quantity['start'],quantity['end'],key[0],sources[key])):
                    _fail('数字片段与自己的来源或口径不符')
                bindings.append(explicit)
                continue
        for (kind, ref), item in sources.items():
            paths = ('value', 'raw_value') if kind == 'evidence' else calculation_quantity_paths(item.get('method'))
            for path in paths:
                if kind == 'evidence' and (item.get(path) is None or item.get('raw_unit' if path == 'raw_value' else 'unit') in (None, '', '不适用')):
                    continue
                value, unit, projection = _project(kind, item, path)
                if not _same_unit(unit, quantity['unit']):
                    continue
                candidate = value
                if directional:
                    if kind != 'calculation' or path != 'value' or item.get('method') not in _FEE_METHODS:
                        continue
                    try:
                        candidate = _direction(text, quantity, item, sources)
                    except ContractError:
                        # Another explicitly cited calculation may have the
                        # correct objects and direction. Never coerce this one.
                        continue
                    projection = 'comparison_absolute'
                if _matches(candidate, quantity) and _price_quantity_scope(
                        text, quantity['start'], quantity['end'], kind, item):
                    found = _binding(kind, ref, item, path, value, unit, projection, quantity['text'], quantity['start'], quantity['end'])
                    break
            if found:
                break
            if kind == 'evidence' and not directional:
                original = next((q for q in _quantities(item.get('text', '')) if q['text'] == quantity['text']), None)
                if original:
                    found = _binding(kind, ref, item, 'text', original['value'], original['unit'], 'verbatim_quantity', quantity['text'], quantity['start'], quantity['end'])
                    break
        if found is None and task is not None and re.search(r'条件|阈值|假设|要求', text):
            # A quoted user threshold is not a calculated result. Bind only a
            # verbatim quantity inside a visibly conditional clause.
            before, after = text[:quantity['start']], text[quantity['end']:]
            condition_label = (re.search(r'(?:条件|阈值|假设|要求)[^，,。；;\n\d]{0,12}$', before)
                               or re.match(r'[^，,。；;\n\d]{0,12}(?:条件|阈值|假设|要求)', after))
            original = next((q for q in _quantities(task.get('question', '')) if q['text'] == quantity['text']), None)
            if original and condition_label:
                source = {'task_id': task['id'], 'research_version': task['version'], 'question': task['question']}
                found = _binding('user_condition', task['id'], source, 'question', original['value'], original['unit'],
                                 'verbatim_condition', quantity['text'], quantity['start'], quantity['end'])
        if found is None:
            _fail('正文数量没有同单位、同数值的引用依据：' + quantity['text'])
        bindings.append(found)
    return bindings


def numeric_segment_shape_errors(segments, fields, *, allow_quotes=False):
    """Fixed diagnostic paths only; shape acceptance is not numeric approval."""
    if not isinstance(segments, dict) or not segments:
        return ['numeric_segments']
    errors = set()
    if set(segments) - set(fields):
        errors.add('numeric_segments.*')
    for parts in segments.values():
        if not isinstance(parts, list) or not parts:
            errors.add('numeric_segments.*')
            continue
        for part in parts:
            if not isinstance(part, dict):
                errors.add('numeric_segments.*.*')
                continue
            if allow_quotes and set(part) == {'quote_ref'}:
                if not isinstance(part['quote_ref'], str) or not part['quote_ref']:
                    errors.add('numeric_segments.*.*.quote_ref')
                continue
            if set(part) == {'text'}:
                if not isinstance(part['text'], str):
                    errors.add('numeric_segments.*.*.text')
                continue
            if set(part) != {'source', 'ref', 'path', 'format', 'digits'}:
                errors.add('numeric_segments.*.*')
                continue
            for key in ('source', 'ref', 'path', 'format'):
                if not isinstance(part[key], str) or not part[key]:
                    errors.add('numeric_segments.*.*.' + key)
            if part['source'] not in ('evidence', 'calculation'):
                errors.add('numeric_segments.*.*.source')
            paths = ('value', 'raw_value') if part['source'] == 'evidence' else analysis_quantity_paths()
            if part['path'] not in paths:
                errors.add('numeric_segments.*.*.path')
            if part['format'] not in ('signed', 'comparison'):
                errors.add('numeric_segments.*.*.format')
            if type(part['digits']) is not int or not 0 <= part['digits'] <= 8:
                errors.add('numeric_segments.*.*.digits')
    return sorted(errors)


def _segments(parts, sources, task, *, deduplicate_units=True):
    if numeric_segment_shape_errors({'value': parts}, ['value'], allow_quotes=verbatim_evidence_enabled(task)):
        _fail('数字片段结构无效')
    if not isinstance(parts, list) or not parts:
        _fail('数字片段必须为非空列表')
    # Adjacent prose is one lexical unit: an arbitrary fragment boundary must
    # not turn a word such as 元旦 into a duplicate currency suffix.
    merged=[]
    for part in parts:
        if set(part)=={'text'} and merged and set(merged[-1])=={'text'}:
            merged[-1]={'text':merged[-1]['text']+part['text']}
        else:merged.append(part)
    text, bindings, previous_unit = '', [], ''
    for part in merged:
        if not isinstance(part, dict):
            _fail('数字片段格式无效')
        if set(part) == {'quote_ref'}:
            previous_unit = ''
            ref = part['quote_ref']
            item = sources.get(('evidence', ref))
            if item is None or not verbatim_evidence_eligible(task, item):
                _fail('原文引用未选择当前问题的合格文字依据')
            validate_evidence(item, task)
            display = '“' + item['text'] + '”'
            start = len(text)
            text += display
            bindings.append(dict(source_kind='evidence', ref=ref, path='text',
                source_hash=_source_hash('evidence', item), projection='verbatim_evidence',
                display=display, span=[start, len(text)]))
            continue
        if set(part) == {'text'}:
            if not isinstance(part['text'], str) or _statement_quantities(part['text'], task):
                _fail('普通文字片段不能藏入金融数量')
            prose=part['text']
            if deduplicate_units and previous_unit:
                repeated=re.match(r'^\s*('+re.escape(previous_unit)+r')(?=$|[\s，。；：！？、,.!?:;…（）()\[\]【】]|左右|以上|以下|以内|以外|上下|附近|之间|区间)',prose)
                if repeated:prose=prose[:repeated.start(1)]+prose[repeated.end(1):]
            text += prose
            previous_unit = ''
            continue
        if set(part) != {'source', 'ref', 'path', 'format', 'digits'}:
            _fail('数字片段含未登记字段')
        kind, ref, path = part['source'], part['ref'], part['path']
        if not all(isinstance(value, str) for value in (kind, ref, path)) or (kind, ref) not in sources:
            _fail('数字片段未引用当前陈述来源')
        if type(part['digits']) is not int or not 0 <= part['digits'] <= 8 or part['format'] not in ('signed', 'comparison'):
            _fail('数字格式或精度未登记')
        item = sources[kind, ref]
        value, unit, projection = _project(kind, item, path)
        shown = value
        lead = ''
        if part['format'] == 'comparison':
            if kind != 'calculation' or path != 'value':
                _fail('比较片段必须引用登记计算主结果')
            a, b = _objects(item, sources)
            lead = a + '比' + b + ('高' if value > 0 else '低' if value < 0 else '相同')
            shown = abs(value)
            projection = 'comparison_absolute'
        try:
            rounded = shown.quantize(Decimal(1).scaleb(-part['digits']), rounding=ROUND_HALF_UP)
            if shown != 0 and rounded == 0:
                _fail('展示精度不能将非零值写成零')
            display_unit = unit.replace('百分点', '个百分点') if part['format'] == 'comparison' else unit
            display = format(rounded, f'.{part["digits"]}f') + display_unit
        except InvalidOperation:
            _fail('数值超出展示精度范围')
        if part['format'] == 'comparison' and value == 0:
            display = lead + '，差值为' + display
        else:
            display = lead + display
        start = len(text)
        text += display
        previous_unit = display_unit
        bindings.append(_binding(kind, ref, item, path, value, unit, projection, display, start, len(text)))
    if not text.strip():
        _fail('陈述不能为空')
    # A financial quantity may not be assembled from ordinary text fragments;
    # every resulting quantity needs an explicit source-bound numeric segment.
    # A literal quote remains source-bound text, not a newly approved scalar.
    # Mask only kernel-generated quoted spans, preserving all other offsets.
    # Arbitrary prose, split numeric fragments and calculation labels still
    # pass the same quantity checks; no model-provided quote text is accepted.
    numeric_text = text
    for binding in bindings:
        if binding['projection'] == 'verbatim_evidence':
            start, end = binding['span']
            numeric_text = numeric_text[:start] + ' ' * (end - start) + numeric_text[end:]
    for quantity in _statement_quantities(numeric_text, task):
        if not any(binding['span'][0] <= quantity['start'] and quantity['end'] <= binding['span'][1]
                   for binding in bindings):
            _fail('拼接后的金融数量缺少显式数字片段绑定')
    # Check surrounding prose cannot reverse a signed or comparison fragment.
    _legacy_bindings(numeric_text, sources, task, reference_bindings=bindings)
    # The explicitly selected segment source must support its own label. A
    # different cited source with an equal number cannot license this segment.
    for binding in bindings:
        source = sources[binding['source_kind'], binding['ref']]
        if not _price_quantity_scope(text, *binding['span'], binding['source_kind'], source):
            _fail('价格或指数变化不能标作净值收益、持有人回报、超额收益、概率或因果贡献')
    return text, bindings


def bind_numeric_statement(task, proposal, fields, refs, calculation_refs, *, unit_join_version=1):
    """Validate supplied prose; return verified fields and a reproducible receipt."""
    if type(unit_join_version) is not int or unit_join_version not in (0,1):_fail('数字展示版本未登记')
    sources, questions = _sources(task, proposal, refs, calculation_refs)
    if task.get('intent') == DOCUMENT_CONTENT_INTENT:
        if fields != ['answer'] or calculation_refs or 'numeric_segments' in proposal or not refs:
            _fail('原件内容核对只能逐字引用原文，不能生成金融数字片段')
        originals = [sources['evidence', ref] for ref in refs]
        for original in originals:
            validate_evidence(original, task)
        text = document_content_answer(originals)
        if proposal.get('answer') != text:
            _fail('原件内容核对正文与已核验摘录不一致')
        bindings, cursor = [], len('原件载明：\n')
        for original in originals:
            quote = original['text']
            bindings.append({'source_kind': 'evidence', 'ref': original['id'], 'path': 'text',
                'source_hash': _source_hash('evidence', original), 'projection': 'verbatim_original',
                'display': quote, 'span': [cursor, cursor + len(quote)]})
            cursor += len(quote) + 2
        record = {'version': 1, 'mode': DOCUMENT_CONTENT_SCOPE, 'task_id': task['id'],
            'research_version': task['version'], 'question_ids': sorted(set(questions)),
            'refs': sorted(set(refs)), 'calculation_refs': [],
            'fields': {'answer': {'text_hash': fingerprint(text), 'bindings': bindings}}}
        return {'fields': {'answer': text}, 'record': record}
    segments = proposal.get('numeric_segments', {})
    if not isinstance(segments, dict) or set(segments) - set(fields):
        _fail('数字片段字段不属于当前陈述')
    texts, receipts = {}, {}
    for field in fields:
        if field in segments:
            text, bindings = _segments(segments[field], sources, task, deduplicate_units=unit_join_version==1)
            if field in proposal and proposal[field] != text:
                # Accept only the identical source-derived legacy spelling
                # with duplicated unit suffixes. Values, signs, references
                # and all other prose must still match exactly.
                legacy,_ = _segments(segments[field], sources, task, deduplicate_units=False)
                if proposal[field] != legacy:_fail('原文与数字片段生成文本不一致')
        else:
            text = proposal.get(field)
            if not isinstance(text, str) or not text.strip():
                _fail('陈述字段必须为非空文字')
            bindings = _legacy_bindings(text, sources, task)
        texts[field] = text
        receipts[field] = {'text_hash': fingerprint(text), 'bindings': bindings}
    record = {'version': 1, 'task_id': task['id'], 'research_version': task['version'],
              'question_ids': sorted(set(questions)), 'refs': sorted(set(refs)),
              'calculation_refs': sorted(set(calculation_refs)), 'fields': receipts}
    if unit_join_version:record['unit_join_version']=unit_join_version
    return {'fields': texts, 'record': record}


def validate_numeric_statement(task, proposal, fields, refs, calculation_refs):
    """Require the saved receipt to match prose and source fingerprints exactly."""
    receipt=proposal.get('numeric_integrity')
    version=receipt.get('unit_join_version',0) if isinstance(receipt,dict) else 1
    result = bind_numeric_statement(task, proposal, fields, refs, calculation_refs,unit_join_version=version)
    if (receipt != result['record'] or any(field in proposal and proposal[field]!=result['fields'][field] for field in fields)):
        _fail('数字绑定回执缺失或正文与来源已改写')
    return result
