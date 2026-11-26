"""Task-scoped iFinD economic series retrieval, separate from numeric approval.

The official HTTP manual defines EDB requests but does not certify the unit,
meaning, original release time or revision vintage of any particular series.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
import json
import re
from unicodedata import normalize

from .contracts import ContractError, DuplicateJSONField, fingerprint, market_date, timepoint, unique_json_object
from .subject_scope import scope_question, subject_allowed

EDB_DOCUMENTATION = 'https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/manual.html'
ECONOMIC_REQUEST_REGISTRY = {'ifind.edb_service': {
    'version': 1, 'kind': '宏观与行业原始序列', 'parameters': ['indicators', 'startdate', 'enddate'],
    'raw_only': True, 'fields': {}, 'documentation': EDB_DOCUMENTATION,
    'limitations': '仅查询当前问题明确的EDB指标ID。指标含义、单位、频率、首次公开及修订版本未核验；不能直接作数字证据或计算输入。',
}}
INDICATOR = re.compile(r'(?<![A-Za-z0-9_])M[0-9]{9}(?![A-Za-z0-9_])')
CURRENT_INDICATOR = re.compile(r'(?<![A-Za-z0-9_])[MG][0-9]{9}(?![A-Za-z0-9_])')
DISPLAY_ID = re.compile(r'[A-Z][0-9]{9}')
DRAFT_PERIOD_POLICY = 'draft_economic_period_coverage_version'
DRAFT_PERIOD_VERSION = 6
DIRECT_ECONOMIC_POLICY = 'direct_economic_acquisition_version'
DIRECT_ECONOMIC_VERSION = 1
CATALOGUE_READOUT_POLICY = 'ifind_catalogue_readout_version'
_FREQUENCY_MONTHS = {'M': 1, 'Q': 3, 'Y': 12}
_EXPLICIT_CADENCE = {
    'M': re.compile(r'逐月|每月|各月'),
    'Q': re.compile(r'逐季|每季|各季'),
    'Y': re.compile(r'逐年|每年|各年'),
}
_PERIOD_CONNECTOR = re.compile(r'\s*[至到—–~～-]\s*')
_QUARTER_SHORT_RANGE = re.compile(r'((?:19|20)\d{2}年\s*第?[一二三四1-4])(?=\s*[至到—–~～-]\s*第?[一二三四1-4]季)')
_OPEN_PERIOD_PREFIX = re.compile(r'(?:截至|截止到?|自|从)\s*$')
_OPEN_PERIOD_SUFFIX = re.compile(r'^\s*份?\s*(?:以来|之?[前后]|以[前后]|起|左右|附近|至今|到现在|期末|末)')
_MONTH_LIST_ITEM = re.compile(r'\s*(?:、|以及|及|和|与)\s*(1[0-2]|0?[1-9])月份?(?!\s*\d{1,2}[日号])')
_FOLLOWUP_MONTH = re.compile(r'(?<![\d.])(?:1[0-2]|0?[1-9])月份?(?!\s*\d{1,2}[日号])')
_FOLLOWUP_READOUT = re.compile(
    r'这个指标|该指标|上述指标|同一指标|前者|后者|它|其|'
    r'读数|指标值|数值|观测值|原值|的值|是多少|多少|核对|查询|查看|改看|换看|改为|改成|换到|看看?|列出|给出|'
    r'逐月|每月|各月|逐季|每季|各季|逐年|每年|各年|'
    r'本轮|本次|请|那么|那|再|继续|只|仅|分别|把|将|在|为|的|呢|'
    r'以及|和|与|及|至|到|[\s，,、。！？?!（）()—–~～-]')
_ECONOMIC_REPORT_TAIL = re.compile(
    r'[，,；;。]\s*(?:并\s*)?(?:请\s*)?(?:生成|输出|给出?|写)(?:一[份篇])?'
    r'(?:(?:简短|完整|详细)的?)?(?:研究)?报告[。！？!?\s]*$')
_ECONOMIC_LOOKUP_HEAD = re.compile(r'^\s*(?:请\s*)?(?:查一下|查(?!询|看)|对比|比较|梳理)(?=\s*\S)')
_RESEARCH_ACTION = r'(?:分析|解释|研究|判断|评估|检验|讨论|说明)'
_RESEARCH_CLAUSE = re.compile(r'^(?:请\s*)?' + _RESEARCH_ACTION)
_RESEARCH_CLAUSE_BOUNDARY = re.compile(r'[，,]\s*(?:(?:并|再|同时)\s*)?(?=(?:请\s*)?' + _RESEARCH_ACTION + ')')
_CHINA_YIELD_TARGET = re.compile(
    r'对(?P<region>中国(?:内地|大陆)?|国内|在岸)?\s*(?:(?P<years>[1-9]\d?|十)\s*年(?:期)?)?\s*'
    r'(?:国债(?:到期)?收益率|国债利率|债券利率)(?:的?(?:判断|定价|走势))?的(?:意义|影响|传导|启示)')
_YIELD_EXCLUSION = re.compile(
    r'(?:不(?:查|看|取|用|使用|讨论|涉及|考虑)|无需|排除|不纳入)\s*(?:同窗|同期|任何)?\s*(?:国债|债券|利率|行情)')
# Preserve the provider's complete hierarchy, including qualifiers outside the
# built-in macro vocabulary. Full-width punctuation is common in Chinese input.
_CATALOGUE_TITLE = re.compile(r'(?:EDB)?指标(?:全称|名称)?\s*【(?P<title>[\w\u3400-\u9fff :：()（）%％/、.+－-]{1,160})】', re.I)
_INDUSTRY_TITLE_READOUT = re.compile(
    r'(?:请)?(?:核对|查询|查看|列出|给出)?\s*(?:的)?(?P<title>[a-zA-Z0-9\u3400-\u9fff:：]{1,70}?'
    r'(?:产量|销量|销售额|销售收入|营业收入|出口额|进口额|库存量|产能利用率|开工率|装机容量|发电量|用电量)'
    r'(?:累计同比|当月同比|累计值|当月值|同比|环比)?)'
    r'(?:的)?(?:(?P<cadence>月度|季度|年度)(?:读数|数值|值)|读数|数值|是多少)?[。？！?!]?')
_FULL_METRIC_NAME = {
    'cpi': '居民消费价格指数', 'ppi': '工业生产者出厂价格指数',
    'pmi': '采购经理指数', 'gdp': '国内生产总值',
}


def discovery_enabled(task):
    return (type(task.get('economic_discovery_version')) is int
            and task['economic_discovery_version'] == 1
            and task.get('research_mode') == 'current')


def search_response(payload):
    """Decode one supplier JSON block, never infer metadata from the question.

    The supplier returns text containing an independently described JSON list.
    Keep that complete text in the snapshot; this is only a derived directory.
    """
    if (not isinstance(payload, dict) or set(payload) != {'text'}
            or not isinstance(payload['text'], str) or not 0 < len(payload['text']) <= 200000):
        raise ContractError('EDB搜索须保留完整文本响应，不接受预先批准的指标列表')
    text = payload['text']
    if any(word in text for word in ('无权限', '调用次数上限', '调用额度已用完')):
        raise ContractError('EDB搜索权限或额度不足，不自动重试')
    blocks = re.findall(r'```json\s*\n([\s\S]*?)\n```', text)
    if len(blocks) != 1:
        if not blocks and text.strip() == '未查询到符合要求的EDB指标':
            return []
        raise ContractError('EDB搜索未返回唯一的明确JSON指标列表，保留原文不猜代码')
    try:
        rows = json.loads(blocks[0], object_pairs_hook=unique_json_object)
    except DuplicateJSONField:
        raise
    except (ValueError, TypeError):
        raise ContractError('EDB搜索指标列表不是有效JSON') from None
    fields = {'country', 'dataSource', 'displayId', 'endDate', 'freq', 'id', 'indexName', 'node', 'startDate', 'unit'}
    if (not isinstance(rows, list) or len(rows) > 1000
            or any(not isinstance(row, dict) or not fields <= set(row)
                or any(not isinstance(row[key], str) or len(row[key]) > 2000 for key in fields)
                or not DISPLAY_ID.fullmatch(row['displayId'])
                or not row['indexName'].strip() or not row['dataSource'].strip()
                or not row['freq'].strip() or not row['unit'].strip()
                or not re.fullmatch(r'\d{8}', row['startDate'])
                or not re.fullmatch(r'\d{8}', row['endDate']) for row in rows)
            or len({row['displayId'] for row in rows}) != len(rows)):
        raise ContractError('EDB搜索目录字段缺失、代码重复或标识不明确，不取数字id代替displayId')
    for row in rows:
        for key in ('startDate', 'endDate'):
            value = row[key]
            timepoint(value[:4]+'-'+value[4:6]+'-'+value[6:])
        if row['startDate'] > row['endDate']:
            raise ContractError('EDB搜索指标覆盖区间倒置')
    return [{key: row[key] for key in sorted(fields)} for row in rows]


def discovered_indicators(task):
    """Only current task's actual, unmodified search acquisitions unlock IDs."""
    if not discovery_enabled(task):
        return []
    from .financial_sources import _metadata_records
    return [dict(row, artifact_id=source['record']['id'], snapshot_hash=source['record']['hash'],
                 metadata_only=True, financial_approval=False, historical_vintage_approved=False)
            for source in _metadata_records(task, 'search_edb') for row in source['parsed']]


def available_economic_indicators(task):
    found = {row['displayId'] for row in discovered_indicators(task)}
    return sorted(code for code in found | set(declared_economic_indicators(task)) if subject_allowed(task, code))


def declared_economic_indicators(task):
    """Read explicit IDs; never turn model memory or securities into EDB IDs."""
    texts = [scope_question(task), *task.get('objects', [])]
    namespace = task.get('economic_identifier_namespace_version')
    return sorted({match.group() for text in texts for match in economic_indicator_matches(text, namespace=namespace)
                   if subject_allowed(task, match.group())})


def economic_indicator_matches(text, *, namespace=None):
    pattern = CURRENT_INDICATOR if type(namespace) is int and namespace == 2 else INDICATOR
    return pattern.finditer(text) if isinstance(text, str) else ()


def validate_economic_request(task, operation, parameters):
    definition = ECONOMIC_REQUEST_REGISTRY.get('ifind.' + str(operation))
    if definition is None:
        raise ContractError('宏观接口未登记')
    if not isinstance(parameters, dict) or set(parameters) != set(definition['parameters']):
        raise ContractError('EDB请求须明确指标ID和日期窗口，不接受额外参数')
    if not all(isinstance(value, str) and value for value in parameters.values()):
        raise ContractError('EDB指标与日期须为明确文字，不补默认值')
    identifiers = parameters['indicators'].split(',')
    if (len(identifiers) != len(set(identifiers))
            or not set(identifiers).issubset(available_economic_indicators(task))):
        raise ContractError('EDB仅查询本任务明确或实际搜索取得的displayId，不猜代码、重复指标或借用其他任务目录')
    dates = [parameters[key] for key in ('startdate', 'enddate')]
    for value in dates:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ContractError('EDB日期须为YYYY-MM-DD，不改写实际请求')
        timepoint(value)
    if not task.get('cutoff') or dates[0] > dates[1] or dates[1] > market_date(task['cutoff']):
        raise ContractError('EDB窗口倒置、未绑定截止时点或越过北京时间截止日期')
    return dates


def economic_request_shapes(task):
    identifiers = available_economic_indicators(task)
    if discovery_enabled(task) and not identifiers:
        return []  # Search first; never offer a free-form guessed EDB code.
    questions = [item['id'] for item in task['questions']]
    return [{'qualified_id_for_lookup_only': 'ifind.edb_service', 'source': 'ifind', 'operation': 'edb_service',
        'parameter_keys': ['indicators', 'startdate', 'enddate'], 'date_format': 'YYYY-MM-DD',
        'date_timezone': 'Asia/Shanghai', 'declared_indicators': identifiers, 'numeric_fields': [], 'raw_only': True,
        'prerequisite': '指标ID须由当前问题或研究对象明确声明，或来自本任务实际search_edb响应；不从模型记忆猜映射。',
        **({'search_candidates': discovered_indicators(task),
            'metadata_parameter_schema': {'type': 'object', 'additionalProperties': False,
                'required': ['indicators', 'startdate', 'enddate'], 'properties': {
                    'indicators': {'type': 'string', 'pattern': '^(?:'+'|'.join(map(re.escape, identifiers))+')(?:,(?:'+'|'.join(map(re.escape, identifiers))+'))*$'},
                    'startdate': {'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'},
                    'enddate': {'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'}}},
            'selection_rule': '逐项核对名称、地区、来源、频率、单位及覆盖期；年度、累计、当月及预测指标不能相互替代。搜索只提供候选，不批准数值或历史版本。'} if discovery_enabled(task) else {}),
        'limitations': economic_record_contract(task)['limitations'],
        'example': {'id': 'shape_example', 'source': 'ifind', 'operation': 'edb_service',
            'parameters': {'indicators': identifiers[0], 'startdate': market_date(task['cutoff']), 'enddate': market_date(task['cutoff'])},
            'question_ids': questions[:1], 'selection_reason': '仅接口形状示例，不是默认指标或交易日'} if identifiers and questions else None}]


def economic_record_contract(task=None):
    if task is not None and discovery_enabled(task):
        return {'version': 2, 'raw_only': True, 'numeric_evidence_registered': False,
            'documentation': EDB_DOCUMENTATION,
            'limitations': 'EDB代码可来自本任务实际搜索。独立核对指标名称、地区、频率和原始来源；搜索元数据不自动批准数值、连续期间覆盖或历史首次可得。原始序列和修订信息完整保留，不直接作计算输入。',
            'vintage': '留存当前取得版本，不证明历史当时可得',
            'unit': '候选目录有字面单位，数值适用性仍待联合核验', 'publication': '未核验'}
    return {'version': 1, 'raw_only': True, 'numeric_evidence_registered': False,
            'documentation': EDB_DOCUMENTATION, 'limitations': ECONOMIC_REQUEST_REGISTRY['ifind.edb_service']['limitations'],
            'vintage': '留存当前取得版本，不证明历史当时可得', 'unit': '未核验', 'publication': '未核验'}


def _period_number(day, months):
    return (day.year * 12 + day.month - 1) // months


def draft_period_enabled(task):
    version = task.get(DRAFT_PERIOD_POLICY)
    return type(version) is int and version in (1, 2, 3, 4, 5, DRAFT_PERIOD_VERSION)


def stated_period_enabled(task):
    version = task.get(DRAFT_PERIOD_POLICY)
    return type(version) is int and version in (3, 4, 5, DRAFT_PERIOD_VERSION)


def scoped_period_enabled(task):
    version = task.get(DRAFT_PERIOD_POLICY)
    return type(version) is int and version in (4, 5, DRAFT_PERIOD_VERSION)


def projects_stated_periods(task):
    version = task.get(DRAFT_PERIOD_POLICY)
    return type(version) is int and version in (5, DRAFT_PERIOD_VERSION)


def explicit_economic_cadence(text):
    cadences = {name for name, pattern in _EXPLICIT_CADENCE.items() if pattern.search(text)}
    return next(iter(cadences)) if len(cadences) == 1 else None


def _indicator_question_texts(task, questions):
    texts = {}
    reference = economic_period_reference(task)
    for row in questions:
        text = scope_question(task, row['question'])
        for match in economic_indicator_matches(text, namespace=task.get('economic_identifier_namespace_version')):
            texts.setdefault(match.group(), {})[row['id']] = text
        if reference and row['question'] == task['question']:
            texts.setdefault(reference['code'], {})[row['id']] = text
    return texts


def declared_economic_cadences(task):
    """Cadence explicitly linked to a named code, without inferring units."""
    texts = _indicator_question_texts(task, task.get('questions', []))
    default = scope_question(task)
    return {code: cadence for code in declared_economic_indicators(task)
            if (cadence := explicit_economic_cadence('\n'.join(texts.get(code, {}).values()) or default))}


def _indicator_frequencies(rows):
    result = {}
    for row in rows:
        result.setdefault(row['displayId'], set()).add(row['freq'])
    return result


def _matched_directory_indicator(task, directories, question, records):
    """Bind a named scope to a unique literal match selected for this question."""
    from .draft_economic_scope import _series_name

    candidates = {}
    for row in directories:
        code = row['displayId']
        if question['id'] in row['question_ids'] and subject_allowed(task, code):
            candidates.setdefault(code, []).append(row)
    name = _series_name(scope_question(task, question['question']))
    if name.codes or len(name.metrics) != 1:
        return None
    matched = set()
    for code, rows in candidates.items():
        for row in rows:
            actual = _series_name(row['indexName']+' '+row['country'])
            if (name.metrics == actual.metrics and name.rates == actual.rates and name.variants == actual.variants
                    and (not name.regions or name.regions == actual.regions)
                    and (not name.frequencies or name.frequencies == {row['freq']})):
                matched.add(code)
    selected = {code for code in matched if any(_economic_query_links(record, code, question['id'])
                for record in records.values())}
    resolved = selected or matched
    return (next(iter(resolved)), len(matched) == 1) if len(resolved) == 1 else None


def _calendar_bounds(period, inherited_year=None):
    year = int(period.year or inherited_year or 0)
    month = int(period.end[:2] or '12')
    try:
        end_day = int(period.end[2:]) if len(period.end) == 4 else monthrange(year, month)[1]
        end = date(year, month, end_day)
        if period.start:
            start = date(int(period.start[:-4] or year), int(period.start[-4:-2]), int(period.start[-2:]))
        elif len(period.end) == 4:
            start = end
        else:
            start = date(year, month if period.end else 1, 1)
    except ValueError:
        return None
    return (start, end) if start <= end else None


def _stated_period_windows(text, *, cadence=None):
    """Use explicit calendar intervals, never bridge separate comparison windows."""
    from .numeric_integrity import _draft_periods

    text = _QUARTER_SHORT_RANGE.sub(r'\1季度', text)
    spans = _draft_periods(text)
    if cadence is not None and any(period.statistic and period.statistic != cadence for _, _, period in spans):
        return []
    ranges, joined = set(), set()
    for index, (left, right) in enumerate(zip(spans, spans[1:])):
        if not left[2].year or not _PERIOD_CONNECTOR.fullmatch(text[left[1]:right[0]]):
            continue
        first = _calendar_bounds(left[2])
        last = _calendar_bounds(right[2], left[2].year)
        if first and last and first[0] <= last[1]:
            ranges.add((first[0], last[1]))
            joined.update((index, index+1))
    for index, (_, _, period) in enumerate(spans):
        if index not in joined and period.year and period.start and (bounds := _calendar_bounds(period)):
            ranges.add(bounds)
    return sorted(ranges)


def _stated_period_request(text, *, explicit_points=False):
    """Resolve a dated statistical request with or without a cadence word."""
    if explicit_points:
        from .numeric_integrity import DRAFT_CLAUSE_BOUNDARY, _DRAFT_CODE
        if _DRAFT_CODE.search(text):
            from .draft_economic_scope import _METRICS, _compact

            # A clause with a separate security does not set the macro window.
            clauses = DRAFT_CLAUSE_BOUNDARY.split(text)[::2]
            text = '，'.join(clause for clause in clauses if not _DRAFT_CODE.search(clause)
                            or CURRENT_INDICATOR.search(clause) or _METRICS.search(_compact(clause)))
    cadence = explicit_economic_cadence(text)
    if cadence and (windows := _stated_period_windows(text)):
        return cadence, windows
    if not explicit_points:
        return None
    from .numeric_integrity import _draft_periods

    spans = _draft_periods(text)
    if any(_PERIOD_CONNECTOR.fullmatch(text[left[1]:right[0]])
           for left, right in zip(spans, spans[1:])):
        return None

    points = []
    for start, end, period in spans:
        if not period.year or period.statistic not in _FREQUENCY_MONTHS:
            continue
        suffix = text[end:].removeprefix('份') if period.statistic == 'M' else text[end:]
        if _OPEN_PERIOD_PREFIX.search(text[:start]) or _OPEN_PERIOD_SUFFIX.match(suffix):
            return None
        if not (bounds := _calendar_bounds(period)):
            continue
        points.append((period.statistic, bounds))
        # In a list like 2026年8月和9月 only the immediately following
        # month inherits the year. A trading day or another clause does not.
        while period.statistic == 'M' and (item := _MONTH_LIST_ITEM.match(suffix)):
            year, month = int(period.year), int(item[1])
            bounds = date(year, month, 1), date(year, month, monthrange(year, month)[1])
            points.append(('M', bounds))
            suffix = suffix[item.end():]
        if _OPEN_PERIOD_SUFFIX.match(suffix):
            return None

    frequencies = {frequency for frequency, _ in points}
    if len(frequencies) != 1 or cadence and frequencies != {cadence}:
        return None
    frequency, = frequencies
    return frequency, sorted({bounds for _, bounds in points})


def economic_period_reference(task):
    if task.get(DRAFT_PERIOD_POLICY) == DRAFT_PERIOD_VERSION and discovery_enabled(task):
        return (task.get('conversation_context') or {}).get('economic_period_reference')
    return None


def economic_reference_windows(reference):
    return [(date.fromisoformat(row['startdate']), date.fromisoformat(row['enddate']))
            for row in reference['windows']]


def _mask_period_spans(text, spans):
    result = text
    for start, end in sorted(spans, reverse=True):
        end += text[end:end+1] == '份'
        result = result[:start] + ' ' * (end - start) + result[end:]
    return result


def direct_economic_readout(task):
    """Identify a single dated indicator lookup without broader obligations."""
    if task.get(DRAFT_PERIOD_POLICY) != DRAFT_PERIOD_VERSION or not discovery_enabled(task):
        return False
    required = [row for row in task.get('questions', ()) if row.get('required', True)]
    if (len(required) != 1 or required[0]['question'] != task['question']
            or required[0].get('answer_scope') == 'general_definition'
            or required[0].get('method_needs') or required[0].get('dependencies')
            or _query_provenance_only(required[0])):
        return False
    if economic_period_reference(task):
        return True

    from .draft_sources import local_scope_text

    text = local_scope_text(scope_question(task))
    return (_explicit_catalogue_scope(task, required[0]['id'], text) is not None
            or _literal_economic_scope(task, text) is not None)


def _literal_economic_scope(task, text):
    from .draft_sources import _unquoted_local_scope
    from .draft_economic_scope import (_FREQUENCY_NAME, _INDICATOR, _METRICS, _RATE, _REGIONS, _VARIANTS,
                                       _compact, _series_name)
    from .numeric_integrity import _draft_periods

    text = _unquoted_local_scope(text)
    name = _series_name(text)
    period_scope = _stated_period_request(text, explicit_points=True)
    if period_scope is None and len(name.frequencies) == 1:
        cadence, = name.frequencies
        if cadence in _FREQUENCY_MONTHS and (windows := _stated_period_windows(text, cadence=cadence)):
            period_scope = cadence, windows
    if not period_scope:
        return None
    codes = {match.group().casefold() for match in economic_indicator_matches(
        text, namespace=task.get('economic_identifier_namespace_version'))}
    if (len(name.codes) > 1 or len(name.metrics) > 1 or len(name.regions) > 1 or '预测' in name.variants
            or name.codes != codes or not (name.metrics or codes)):
        return None
    text = _QUARTER_SHORT_RANGE.sub(r'\1季度', text)
    dates = _draft_periods(text)
    rest = _compact(_mask_period_spans(text, [(first, last) for first, last, _ in dates]))
    for term in (_FREQUENCY_NAME, _INDICATOR, _METRICS, _RATE, _REGIONS, _VARIANTS):
        rest = term.sub(' ', rest)
    if _FOLLOWUP_READOUT.sub('', _FOLLOWUP_MONTH.sub(' ', rest)):
        return None
    frequency, windows = period_scope
    return name, frequency, windows


def _direct_economic_text(task, text):
    from .draft_sources import _NO_PDF, stated_source_text
    from .market_series import _IFIND_ONLY

    text = _IFIND_ONLY.sub('', _NO_PDF.sub('', stated_source_text(task, text)))
    text = _ECONOMIC_REPORT_TAIL.sub('', text.strip(' ，,。；;！!？?\n'))
    return _ECONOMIC_LOOKUP_HEAD.sub('核对', text, count=1)


def direct_economic_scope(task, *, clause=None):
    """Compile the selected indicator and statistical periods without a model."""
    from .draft_sources import local_sources_only, stated_access_restricted
    policy = task.get(DIRECT_ECONOMIC_POLICY)
    if (type(policy) is not int or policy != DIRECT_ECONOMIC_VERSION or not discovery_enabled(task)
            or task.get('research_mode') != 'current' or task.get(DRAFT_PERIOD_POLICY) != DRAFT_PERIOD_VERSION
            or task.get('intent') not in ('综合研究', '比较对象')
            or task.get('materials') or task.get('pending_materials') or task.get('calculation_specs')
            or local_sources_only(task)):
        return None
    questions = [row for row in task.get('questions', ()) if row.get('required', True)]
    if len(questions) != 1:
        return None
    question, = questions
    if (question['question'] != task['question'] or question.get('answer_scope')
            or question.get('evidence_needs') or question.get('method_needs') or question.get('dependencies')):
        return None
    original = scope_question(task) if clause is None else clause
    text = _direct_economic_text(task, original)
    if stated_access_restricted(task, text):
        return None
    return _economic_lookup_scope(task, question['id'], text)


def _explicit_catalogue_scope(task, question_id, text, *, research_clause=False):
    if task.get(CATALOGUE_READOUT_POLICY) != 1:
        return None
    from .draft_economic_scope import _compact, _series_name
    from .draft_sources import _unquoted_local_scope
    from .numeric_integrity import _draft_periods

    text = _unquoted_local_scope(text)
    matches = list(_CATALOGUE_TITLE.finditer(text))
    if matches:
        if len(matches) != 1:
            return None
        match, = matches
        title = match['title'].strip()
        outside = text[:match.start()] + ' ' + text[match.end():]
    else:
        outside = text
    spans = [(start, end) for start, end, _ in _draft_periods(outside)]
    remaining = ''.join(_mask_period_spans(outside, spans).split())
    cadence = None
    if matches:
        if _FOLLOWUP_READOUT.sub('', _FOLLOWUP_MONTH.sub(' ', _compact(remaining))):
            return None
    else:
        # A plain industry name is accepted only in a complete readout question.
        # Preserve the original case and qualifiers for the supplier search.
        natural = _INDUSTRY_TITLE_READOUT.fullmatch(remaining)
        if not natural or not (research_clause or re.match(r'^(?:请)?(?:核对|查询|查看|列出|给出)', remaining)
                               or re.search(r'是多少[。？！?!]?$', remaining)):
            return None
        title = natural['title']
        if re.search(_RESEARCH_ACTION + r'|预测|展望|影响|原因|为何|如何|不要|不用|不查|无需|排除', title):
            return None
        cadence = {'月度':'M', '季度':'Q', '年度':'Y'}.get(natural['cadence'])
    period = _stated_period_request(outside, explicit_points=True)
    if not period and cadence and (windows := _stated_period_windows(outside, cadence=cadence)):
        period = cadence, windows
    if not period:
        return None
    frequency, windows = period
    if cadence and frequency != cadence:
        return None
    if not title or not subject_allowed(task, title):
        return None
    return dict(question_id=question_id, code=None, search=title, label=title, exact_title=title,
                natural_title=not matches, name=_series_name(title), frequency=frequency, windows=windows)


def _economic_lookup_scope(task, question_id, text, *, research_clause=False):
    from .draft_economic_scope import _METRICS, _VARIANTS, _compact

    if exact := _explicit_catalogue_scope(task, question_id, text, research_clause=research_clause):
        return exact
    literal = _literal_economic_scope(task, text)
    if not literal:
        return None
    name, frequency, windows = literal
    if (not name.codes and not name.regions) or (name.frequencies and name.frequencies != {frequency}):
        return None
    code = next(iter(name.codes)).upper() if name.codes else None
    if code and code not in declared_economic_indicators(task):
        return None
    label, lookup = code, None
    if name.metrics:
        metric = _METRICS.search(_compact(text))
        region = next(iter(name.regions), '')
        rate = next(iter(name.rates), '')
        if len(name.rates) > 1:
            return None
        variants = ''.join(dict.fromkeys(_VARIANTS.findall(_compact(text))))
        label = region+variants+metric.group().upper()+rate
        lookup = region+variants+_FULL_METRIC_NAME.get(metric.lastgroup, metric.group().upper())+rate
    return dict(question_id=question_id, code=code, search=lookup, label=label,
                name=name, frequency=frequency, windows=windows)


def direct_economic_scopes(task):
    if scope := direct_economic_scope(task):
        return [scope]
    if task.get(DIRECT_ECONOMIC_POLICY) != DIRECT_ECONOMIC_VERSION:
        return []
    from .draft_sources import _unquoted_local_scope
    from .market_series import _latest_clauses

    text = _unquoted_local_scope(_direct_economic_text(task, scope_question(task)))
    scopes = []
    for clause in _latest_clauses(text, valuation=True):
        if (scope := direct_economic_scope(task, clause=clause)) is not None and scope not in scopes:
            scopes.append(scope)
    return scopes


def research_economic_scopes(task):
    """Keep explicitly specified statistical lookups inside an open investigation."""
    from .draft_sources import _unquoted_local_scope, local_sources_only, stated_access_restricted, validate_stated_source
    from .market_series import _latest_clauses

    policy = task.get(DIRECT_ECONOMIC_POLICY)
    if (type(policy) is not int or policy != DIRECT_ECONOMIC_VERSION or not discovery_enabled(task)
            or task.get(DRAFT_PERIOD_POLICY) != DRAFT_PERIOD_VERSION or task.get('intent') == '原件内容核对'
            or local_sources_only(task) or stated_access_restricted(task, _direct_economic_text(task, task['question']))):
        return []
    try:
        validate_stated_source(task, 'ifind')
    except ContractError:
        return []
    scopes = []
    for question in task.get('questions', []):
        if not question.get('required', True) or question.get('answer_scope') or _query_provenance_only(question):
            continue
        text = _direct_economic_text(task, _unquoted_local_scope(scope_question(task, question['question'])))
        if stated_access_restricted(task, text):
            continue
        clauses = [piece for part in _latest_clauses(text, valuation=True)
                   for piece in _RESEARCH_CLAUSE_BOUNDARY.split(part)]
        if not any(_RESEARCH_CLAUSE.match(part) for part in clauses):
            continue
        for part in clauses:
            text = _RESEARCH_CLAUSE.sub('', part, count=1).strip()
            scope = _economic_lookup_scope(task, question['id'], _direct_economic_text(task, text), research_clause=True)
            if scope is not None and scope not in scopes:
                scopes.append(scope)
    return scopes


def _research_yield_scopes(task, statistical_scopes):
    """Find a dated, domestic benchmark for an explicitly requested rates link."""
    from .draft_sources import _unquoted_local_scope

    original = _unquoted_local_scope(task['question'])
    if _YIELD_EXCLUSION.search(original):
        return []
    cutoff = date.fromisoformat(market_date(task['cutoff']))
    questions = {row['id']: _unquoted_local_scope(scope_question(task, row['question']))
                 for row in task['questions']}
    regions = {}
    for scope in statistical_scopes:
        regions.setdefault(scope['question_id'], set()).update(scope['name'].regions)
    result = {}
    for scope in statistical_scopes:
        if scope['name'].regions != {'中国'}:
            continue
        question_id = scope['question_id']
        targets = {10 if match['years'] in (None, '十') else int(match['years'])
                   for match in _CHINA_YIELD_TARGET.finditer(questions[question_id])
                   if regions[question_id] == {'中国'} or match['region']}
        if len(targets) != 1:
            continue
        years, = targets
        months = _FREQUENCY_MONTHS[scope['frequency']]
        periods = _closed_period_slots(scope['windows'], months, cutoff)
        windows = [window for period in periods for window in _period_query_windows(period, months, scope['windows'])]
        if windows:
            result.setdefault((question_id, years), []).extend(windows)
    return [dict(question_id=question_id, frequency='D', windows=_merged_date_windows(windows),
                 search=f'中国中债国债到期收益率{years}年', label=f'中债国债到期收益率:{years}年')
            for (question_id, years), windows in sorted(result.items())]


def research_economic_searches(task, scopes=None):
    if scopes is None:
        scopes = research_economic_scopes(task)
    return direct_economic_searches(task, [*scopes, *_research_yield_scopes(task, scopes)]) if scopes else []


def direct_economic_searches(task, scopes):
    """Plan the exact named directory searches, using a retained same-task reply once."""
    from .financial_sources import _metadata_records

    cutoff = date.fromisoformat(market_date(task['cutoff']))
    pending = {}
    for scope in scopes:
        months = _FREQUENCY_MONTHS.get(scope['frequency'])
        closed = _closed_period_slots(scope['windows'], months, cutoff) if months else scope['windows']
        if scope['search'] and closed:
            pending.setdefault(scope['search'], set()).add(scope['question_id'])
    if not pending:
        return []
    for entry in _metadata_records(task, 'search_edb'):
        record = entry['record']
        query = record['parameters']['query']
        if query in pending:
            pending[query].difference_update(record.get('question_ids', ()))
    return [dict(id='stated_economic_'+fingerprint([query, sorted(questions)])[:24],
                 source='ifind', operation='search_edb', parameters=dict(query=query),
                 question_ids=sorted(questions), selection_reason='按原问题的地区和指标口径检索iFinD目录。')
            for query, questions in pending.items() if questions]


def research_yield_requests(task):
    """Fetch actual daily observations in the closed statistical date windows."""
    from .evidence import _current_api_reference
    from .financial_sources import _metadata_records

    statistical_scopes = research_economic_scopes(task)
    scopes = _research_yield_scopes(task, statistical_scopes) if statistical_scopes else []
    if not scopes:
        return []
    directories = _metadata_records(task, 'search_edb')
    obtained = [row for row in task.get('source_records', ())
                if row.get('operation') == 'edb_service'
                and _current_api_reference(task, row, {'ifind.edb_service'}, 'current_snapshot') is not None]
    requests = []
    for scope in scopes:
        question_id = scope['question_id']
        codes = {row['displayId'] for entry in directories
                 if entry['record']['parameters']['query'] == scope['search']
                 and question_id in entry['record'].get('question_ids', ())
                 for row in entry['parsed'] if row['indexName'] == scope['label']
                 and row['country'] == '中国' and row['dataSource'] == '中国债券信息网'
                 and row['freq'] == 'D' and row['unit'].strip() == '%'
                 and subject_allowed(task, row['displayId'])}
        if len(codes) != 1:
            continue
        code, = codes
        covered = _merged_date_windows(
            (date.fromisoformat(row['parameters']['startdate']), date.fromisoformat(row['parameters']['enddate']))
            for row in obtained if question_id in row.get('question_ids', ())
            and code in row['parameters']['indicators'].split(','))
        for first, last in scope['windows']:
            for start, end in unqueried_date_windows(first, last, covered):
                params = dict(indicators=code, startdate=start.isoformat(), enddate=end.isoformat())
                requests.append(dict(id='research_yield_'+fingerprint((question_id, params))[:24],
                    source='ifind', operation='edb_service', parameters=params, question_ids=[question_id],
                    selection_reason='按同一日期范围比较国债到期收益率的实际变化，为利率传导判断补充市场证据。'))
    return requests


def _direct_economic_code(task, scope, directories):
    """Use exact metadata and coverage to distinguish equally named series."""
    if not scope['search']:
        return scope['code']
    from .draft_economic_scope import _series_name

    if title := scope.get('exact_title'):
        # An exact title can still describe multiple providers or series. Never
        # break that tie using result order or the length of reported history.
        def matches_title(row):
            actual = normalize('NFKC', row['indexName'].strip())
            expected = normalize('NFKC', title)
            if not scope['natural_title']:
                return actual == expected
            actual, expected = (''.join(word.replace(':', '').split()) for word in (actual, expected))
            # Catalogue names often append the level's own statistical period.
            # A monthly level can match 当月值. Rates and cumulative series keep their qualifiers.
            period_value = {'M': '当月值', 'Q': '当季值', 'Y': '当年值'}.get(scope['frequency'])
            labels = {actual}
            if period_value is not None:
                labels.add(actual.removesuffix(period_value))
            country = row['country'].strip()
            return expected in labels or any(expected == country + label for label in labels)

        identities = {(row['displayId'], row['dataSource'], row['country'], row['unit'])
                      for entry in directories if entry['record']['parameters']['query'] == scope['search']
                      and scope['question_id'] in entry['record'].get('question_ids', ())
                      for row in entry['parsed'] if row['freq'] == scope['frequency']
                      and matches_title(row)}
        if len(identities) != 1:
            return None
        code = next(iter(identities))[0]
        return code if subject_allowed(task, code) else None

    matches, complete = set(), set()
    name, frequency = scope['name'], scope['frequency']
    months = _FREQUENCY_MONTHS[frequency]
    cutoff = date.fromisoformat(market_date(task['cutoff']))
    requested = _closed_period_slots(scope['windows'], months, cutoff)
    for entry in directories:
        record = entry['record']
        if (record['parameters']['query'] != scope['search']
                or scope['question_id'] not in record.get('question_ids', ())):
            continue
        for row in entry['parsed']:
            code = row['displayId']
            if ((scope['code'] and scope['code'] != code) or not subject_allowed(task, code)
                    or row['freq'] != frequency):
                continue
            actual = _series_name(row['indexName']+' '+row['country'])
            if (actual.metrics != name.metrics or actual.rates != name.rates or actual.variants != name.variants
                    or name.regions and actual.regions != name.regions
                    or name.rates and row['unit'].strip() != '%'):
                continue
            matches.add(code)
            first = _period_number(date.fromisoformat(row['startDate']), months)
            last = _period_number(date.fromisoformat(row['endDate']), months)
            if requested and first <= min(requested) <= max(requested) <= last:
                complete.add(code)
    preferred = complete or matches
    return next(iter(preferred)) if len(preferred) == 1 else None


def _resolved_economic_scopes(task, scopes):
    from .financial_sources import _metadata_records

    directories = _metadata_records(task, 'search_edb') if any(scope['search'] for scope in scopes) else []
    grouped = {}
    for scope in scopes:
        key = scope['question_id'], scope['code'], scope['search'], scope['frequency'], scope.get('natural_title')
        if key in grouped:
            grouped[key]['windows'].extend(scope['windows'])
        else:
            grouped[key] = dict(scope, windows=list(scope['windows']))
    return [(scope, _direct_economic_code(task, scope, directories)) for scope in grouped.values()]


def _direct_period_requirements(task, scopes):
    by_cadence = {}
    for scope, code in _resolved_economic_scopes(task, scopes):
        if not code:
            continue
        key = code, scope['question_id']
        frequency = scope['frequency']
        periods = by_cadence.setdefault(key, {})
        periods.setdefault(frequency, []).extend(scope['windows'])
    result = {}
    for key, periods in by_cadence.items():
        if len(periods) == 1:
            (frequency, windows), = periods.items()
            result[key] = frequency, _merged_date_windows(windows), True
    return result


def _followup_calendar_request(text, periods):
    if requested := _stated_period_request(text, explicit_points=True):
        return requested
    # A closed range expressed in calendar months can supply the months even
    # if the user omitted “逐月”. Metadata still has to agree with the cadence.
    if periods and all(period.year and (period.statistic == 'M' or
                       (period.start and not period.statistic and text[end-1:end] == '月'
                        and _FOLLOWUP_MONTH.search(text, start, end)))
                       for start, end, period in periods):
        return _stated_period_request(text + '逐月', explicit_points=True)
    return None


def resolve_economic_followup_period(task, previous, objects):
    """Resolve a readout of one inherited indicator, using only user-stated dates."""
    from .draft_sources import _unquoted_local_scope, local_scope_text
    from .market_calendar import _market_date_mention
    from .numeric_integrity import _draft_periods

    if (previous is None or task.get(DRAFT_PERIOD_POLICY) != DRAFT_PERIOD_VERSION
            or not discovery_enabled(task) or len(objects) != 1):
        return None
    code, = objects
    if code not in declared_economic_indicators(previous):
        return None
    text = _QUARTER_SHORT_RANGE.sub(r'\1季度',
        _unquoted_local_scope(local_scope_text(scope_question(task))))
    date_reference = _market_date_mention(text)
    periods = _draft_periods(text)
    spans = [(start, end) for start, end, _ in periods]
    if date_reference:
        spans.append(date_reference.span())
    rest = _mask_period_spans(text, spans)
    months = list(_FOLLOWUP_MONTH.finditer(rest))
    if not (spans or months) or _FOLLOWUP_READOUT.sub('', _FOLLOWUP_MONTH.sub(' ', rest)):
        return None

    requested = _followup_calendar_request(text, periods)
    if requested is None:
        prior = None
        if (bound := economic_period_reference(previous)) is not None:
            if bound['code'] == code and bound['windows']:
                prior = bound['frequency'], economic_reference_windows(bound)
        elif declared_economic_indicators(previous) == [code]:
            prior_text = _unquoted_local_scope(local_scope_text(scope_question(previous)))
            prior = _followup_calendar_request(prior_text, _draft_periods(prior_text))
        has_date = bool(periods or months)
        if prior and date_reference and not has_date and len(prior[1]) == 1:
            requested = prior
        elif prior and has_date:
            years = {day.year for window in prior[1] for day in window}
            if len(years) == 1 and not any(period.year for _, _, period in periods):
                first = min([*(start for start, _, _ in periods), *(month.start() for month in months)])
                text = text[:first] + f'{next(iter(years))}年' + text[first:]
                requested = _followup_calendar_request(text, _draft_periods(text))
    if not requested:
        return dict(code=code, windows=[])
    frequency, windows = requested
    return dict(code=code, frequency=frequency, windows=[
        dict(startdate=first.isoformat(), enddate=last.isoformat()) for first, last in windows])


def _period_label(period, months):
    year, offset = divmod(period, 12//months)
    return f'{year}年' + ({1:f'{offset+1}月', 3:f'第{offset+1}季度', 12:''}[months])


def _merged_date_windows(windows):
    merged = []
    for first, last in sorted(windows):
        if merged and first.toordinal() <= merged[-1][1].toordinal()+1:
            merged[-1] = merged[-1][0], max(last, merged[-1][1])
        else:
            merged.append((first, last))
    return merged


def unqueried_date_windows(start, end, queried):
    """Subtract already queried days from an inclusive requested window."""
    windows = []
    cursor = start
    for first, last in queried:
        if last < cursor or first > end:
            continue
        if cursor < first:
            windows.append((cursor, date.fromordinal(first.toordinal() - 1)))
        if last >= end:
            return windows
        cursor = max(cursor, date.fromordinal(last.toordinal() + 1))
    if cursor <= end:
        windows.append((cursor, end))
    return windows


def _period_query_windows(slot, months, windows):
    year, offset = divmod(slot * months, 12)
    start = date(year, offset+1, 1)
    end_month = offset+months
    end = date(year, end_month, monthrange(year, end_month)[1])
    return [(max(start, first), min(end, last)) for first, last in windows
            if first <= end and start <= last]


def _stated_period_questions(task):
    if not stated_period_enabled(task) or not discovery_enabled(task):
        return {}
    point_periods = task.get(DRAFT_PERIOD_POLICY) == DRAFT_PERIOD_VERSION
    reference = economic_period_reference(task)
    result = {}
    for row in task.get('questions', ()):
        if (not row.get('required', True) or row.get('answer_scope') == 'general_definition'
                or _query_provenance_only(row)):
            continue
        if reference and row['question'] == task['question']:
            if reference['windows']:
                result[row['id']] = reference['frequency'], economic_reference_windows(reference)
        elif parsed := _stated_period_request(scope_question(task, row['question']), explicit_points=point_periods):
            result[row['id']] = parsed
    return result


def stated_period_requirements(task, records):
    """Bind explicit statistical windows to their indicator and question."""
    if task.get('run_mode') == 'direct' and (scopes := direct_economic_scopes(task)):
        return _direct_period_requirements(task, scopes)
    scopes = research_economic_scopes(task)
    requirements = _direct_period_requirements(task, scopes) if scopes else {}
    periods = _stated_period_questions(task)
    questions = [row for row in task.get('questions', ()) if row['id'] in periods]
    if not questions:
        return requirements
    declared = set(declared_economic_indicators(task))
    texts = _indicator_question_texts(task, questions)
    texts = {code: values for code, values in texts.items() if code in declared}
    auto_query = {(code, ident) for code, entries in texts.items() for ident in entries}
    if any(record.get('operation') == 'search_edb' for record in task.get('source_records', [])):
        from .financial_sources import _metadata_records
        directories = [dict(row, question_ids=entry['record'].get('question_ids', []))
                       for entry in _metadata_records(task, 'search_edb') for row in entry['parsed']]
        explicitly_linked = {question_id for values in texts.values() for question_id in values}
        # A missing/ambiguous exact catalogue title cannot be replaced by a
        # broader match on the generic CPI/industry words in the question.
        explicitly_linked.update(scope['question_id'] for scope in scopes if scope.get('exact_title'))
        for row in questions:
            if row['id'] not in explicitly_linked:
                if match := _matched_directory_indicator(task, directories, row, records):
                    code, unique = match
                    texts.setdefault(code, {})[row['id']] = scope_question(task, row['question'])
                    if unique:
                        auto_query.add((code, row['id']))
        frequencies = _indicator_frequencies(directories)
    else:
        frequencies = {}
    for code in sorted(texts):
        for question_id in texts[code]:
            cadence, windows = periods[question_id]
            if frequencies.get(code, {cadence}) == {cadence}:
                key = code, question_id
                requirements.setdefault(key, (cadence, windows, key in auto_query))
    return requirements


def scoped_request_periods(task, request, requirements):
    """Narrow the source only when all its linked questions state a scope."""
    if not projects_stated_periods(task):
        return {}
    parameters = request.get('parameters')
    indicators = parameters.get('indicators') if isinstance(parameters, dict) else None
    questions = set(request.get('question_ids') or ())
    if not (isinstance(indicators, str) and questions):
        return {}
    mentioned = {}
    linked = [row for row in task.get('questions', ()) if row['id'] in questions]
    for code, texts in _indicator_question_texts(task, linked).items():
        for ident in texts:
            mentioned.setdefault(ident, set()).add(code)
    scopes = {}
    for code in indicators.split(','):
        bounds = [requirements.get((code, ident)) for ident in questions
                  if ident not in mentioned or code in mentioned[ident]]
        if bounds and all(bounds):
            scopes[code] = _merged_date_windows(window for _, windows, _ in bounds for window in windows)
    return scopes


def _closed_period_slots(windows, months, cutoff):
    last_closed = _period_number(cutoff, months)-1
    return {slot for first, last in windows for slot in
            range(_period_number(first, months), min(_period_number(last, months), last_closed)+1)}


def stated_period_coverage(task, requests, facts, records, *, include_query_windows=False, requirements=None):
    """Measure the user’s closed periods separately from any narrower query."""
    if requirements is None:
        requirements = stated_period_requirements(task, records)
    if not requirements:
        return []
    project_queries = include_query_windows and scoped_period_enabled(task)
    cutoff = date.fromisoformat(market_date(task['cutoff']))
    query_windows = {}
    for request in requests:
        if (request.get('source'), request.get('operation')) != ('ifind', 'edb_service'):
            continue
        params = request['parameters']
        try:
            first, last = map(date.fromisoformat, validate_economic_request(task, 'edb_service', params))
        except ContractError:
            continue
        for code in params['indicators'].split(','):
            for question_id in request.get('question_ids') or ():
                key = code, question_id
                if key in requirements:
                    query_windows.setdefault(key, []).append((first, last))
    result = []
    for (code, question_id), (cadence, windows, auto_query) in requirements.items():
        months = _FREQUENCY_MONTHS[cadence]
        expected = _closed_period_slots(windows, months, cutoff)
        if not expected:
            continue
        values = {}
        for row in facts:
            if (row['code'] != code or row['field'] != 'value'
                    or not _economic_query_links(records[row['artifact_id']], code, question_id)):
                continue
            if row.get('value') is None or row.get('previous_close_for'):
                continue
            day = date.fromisoformat(row['period'])
            if any(first <= day <= last for first, last in windows):
                values.setdefault(_period_number(day, months), set()).add(Decimal(str(row['raw_value']).strip()))
        missing = sorted(expected - {slot for slot, numbers in values.items() if len(numbers) == 1})
        queries = _merged_date_windows(query_windows.get((code, question_id), ()))
        pending = {}
        for slot in missing:
            # A single-day query cannot discharge an entire requested month.
            needed = [piece for lo, hi in _period_query_windows(slot, months, windows)
                      for piece in unqueried_date_windows(lo, hi, queries)]
            if needed:
                pending[slot] = needed
        item = dict(code=code, question_id=question_id, frequency=cadence,
            windows=[dict(startdate=first.isoformat(), enddate=last.isoformat()) for first, last in windows],
            expected_period_count=len(expected), periods=[_period_label(slot, months) for slot in missing[:12]],
            missing_period_count=len(missing), unqueried_periods=[_period_label(slot, months) for slot in list(pending)[:12]],
            unqueried_period_count=len(pending))
        if include_query_windows:
            missing_windows = _merged_date_windows(window for pieces in pending.values() for window in pieces)
            item['unqueried_windows'] = [dict(startdate=first.isoformat(), enddate=last.isoformat())
                                         for first, last in missing_windows]
            if project_queries:
                item['auto_query'] = auto_query
        result.append(item)
    return result


def economic_period_scope(task):
    """Copy the bound comparison windows needed to recompute a frozen report."""
    if not scoped_period_enabled(task) or not discovery_enabled(task):
        return {}
    coverage = task.get('draft_acquisition_coverage') or {}
    binding = dict(task_id=task['id'], research_version=task['version'], input_hash=task.get('input_hash'))
    if not isinstance(coverage, dict) or coverage.get('binding') != binding:
        return {}
    windows = [dict(code=row['code'], question_id=row['question_id'], frequency=row['frequency'],
                    windows=[dict(window) for window in row['windows']])
               for row in coverage.get('stated_economic_periods', [])]
    return dict(binding=binding, stated_economic_periods=windows) if windows else {}


def economic_comparison_windows(task):
    """Group bound date windows with their statistical cadence by series."""
    windows = {}
    scope = economic_period_scope(task)
    if not scope:
        return windows
    cutoff = date.fromisoformat(market_date(task['cutoff']))
    for row in scope['stated_economic_periods']:
        series = windows.setdefault(row['code'], [])
        months = _FREQUENCY_MONTHS[row['frequency']]
        year, offset = divmod(_period_number(cutoff, months) * months, 12)
        last_closed = date(year, offset+1, 1) - timedelta(days=1)
        series.extend((row['question_id'], months, last_closed,
                       date.fromisoformat(window['startdate']), date.fromisoformat(window['enddate']))
                      for window in row['windows'])
    return windows


def _query_provenance_only(question):
    needs = question.get('evidence_needs', ())
    scopes = question.get('evidence_need_scopes', {})
    return bool(needs) and all(scopes.get(need) == 'query_provenance' for need in needs)


def _economic_query_links(row, code, question_id):
    indicators = row['parameters'].get('indicators') if isinstance(row.get('parameters'), dict) else None
    return (row.get('source') == 'ifind' and row.get('operation') == 'edb_service'
            and question_id in (row.get('question_ids') or ()) and isinstance(indicators, str)
            and code in indicators.split(','))


def draft_period_windows(task, request):
    """Resolve a calendar window for each indicator with an explicit cadence."""
    version = task.get(DRAFT_PERIOD_POLICY)
    if not draft_period_enabled(task) or not discovery_enabled(task):
        return {}
    params = request['parameters']
    indicators = params.get('indicators')
    if not isinstance(indicators, str) or (version == 1 and not DISPLAY_ID.fullmatch(indicators)):
        return {}
    codes = set(indicators.split(','))
    if not all(DISPLAY_ID.fullmatch(code) for code in codes):
        return {}
    refs = set(request.get('question_ids') or ())
    questions = [row for row in task['questions'] if row['id'] in refs]
    shared_cadence = explicit_economic_cadence('\n'.join(scope_question(task, row['question']) for row in questions))
    by_indicator = _indicator_question_texts(task, questions) if version >= 2 and len(refs) > 1 else {}
    declared = set(declared_economic_indicators(task))
    frequencies = _indicator_frequencies(discovered_indicators(task))
    try:
        first, last = (date.fromisoformat(params[key]) for key in ('startdate', 'enddate'))
        cutoff = date.fromisoformat(market_date(task['cutoff']))
    except (ContractError, KeyError, ValueError, TypeError):
        return {}
    windows = {}
    for code in sorted(codes):
        cadence, bound_refs = shared_cadence, refs
        if own := by_indicator.get(code):
            cadence = explicit_economic_cadence('\n'.join(own.values()))
            bound_refs = set(own)
        registered = frequencies.get(code)
        if not cadence or (registered is None and code not in declared) or (registered is not None and registered != {cadence}):
            continue
        months = _FREQUENCY_MONTHS[cadence]
        start = _period_number(first, months)
        # A still-open period need not have an observation yet.
        end = min(_period_number(last, months), _period_number(cutoff, months)-1)
        if start <= end:
            windows[code] = (months, start, end), bound_refs
    return windows


def draft_period_gaps(window, rows, *, requested_periods=()):
    months, first, last = window
    received = {_period_number(date.fromisoformat(row['period']), months) for row in rows}
    expected = set(range(first, last+1))
    if requested_periods:
        expected.intersection_update(slot for start, end in requested_periods for slot in
                                     range(_period_number(start, months), _period_number(end, months)+1))
    missing = sorted(expected - received)
    if not missing:
        return {}

    return dict(periods=[_period_label(period, months) for period in missing[:12]], missing_period_count=len(missing))


def disconnected_economic_series(task):
    """Identify deliberately separated intervals without treating them as missing data."""
    if not projects_stated_periods(task):
        return []
    disconnected = []
    for code, windows in economic_comparison_windows(task).items():
        periods = sorted((_period_number(first, months), _period_number(min(last, closed), months))
                         for _, months, closed, first, last in windows if first <= closed)
        previous_end = None
        for start, end in periods:
            if previous_end is not None and start > previous_end + 1:
                disconnected.append(code)
                break
            previous_end = end if previous_end is None else max(previous_end, end)
    return disconnected


def _unbound_period_notes(task, bound_questions):
    if task.get(DRAFT_PERIOD_POLICY) != DRAFT_PERIOD_VERSION:
        return []
    if task.get('run_mode') == 'direct' and (scopes := direct_economic_scopes(task)):
        cutoff = date.fromisoformat(market_date(task['cutoff']))
        results = _resolved_economic_scopes(task, scopes)
        cadences = {}
        for scope, code in results:
            if code:
                cadences.setdefault((code, scope['question_id']), set()).add(scope['frequency'])
        notes = []
        for scope, code in results:
            months = _FREQUENCY_MONTHS[scope['frequency']]
            slots = sorted(_closed_period_slots(scope['windows'], months, cutoff))
            label = scope['label']
            if not slots:
                notes.append(f'{label}所查统计期在研究截止日尚未结束。')
            elif not code or len(cadences[(code, scope['question_id'])]) != 1:
                periods = '、'.join(_period_label(slot, months) for slot in slots[:12])
                if len(slots) > 12:
                    periods += f'等{len(slots)}期'
                notes.append(f'{label}缺少{periods}读数，未能锁定唯一匹配的指标序列。')
        return notes
    requested = _stated_period_questions(task)
    if not requested.keys() - bound_questions:
        return []

    from .draft_economic_scope import _METRICS, _VARIANTS, _compact, _series_name

    cutoff = date.fromisoformat(market_date(task['cutoff']))
    notes = []
    for row in task['questions']:
        ident, question = row['id'], scope_question(task, row['question'])
        if ident in bound_questions or ident not in requested:
            continue
        name = _series_name(question)
        if name.codes or len(name.metrics) != 1:
            continue
        frequency, windows = requested[ident]
        months = _FREQUENCY_MONTHS[frequency]
        slots = sorted(_closed_period_slots(windows, months, cutoff))
        if not slots:
            continue
        compact = _compact(question)
        metric = _METRICS.search(compact).group().upper()
        variants = ''.join(dict.fromkeys(_VARIANTS.findall(compact)))
        region = next(iter(name.regions)) if len(name.regions) == 1 else ''
        rate = next(iter(name.rates)) if len(name.rates) == 1 else ''
        periods = '、'.join(_period_label(slot, months) for slot in slots[:12])
        if len(slots) > 12:
            periods += f'等{len(slots)}期'
        notes.append(f'{region}{variants}{metric}{rate}缺少{periods}读数，指标口径待核对。')
    return notes


def draft_period_summary(task, facts):
    """Present unresolved periods and keep their series from implying continuity."""
    if not draft_period_enabled(task):
        return [], []
    coverage = task.get('draft_acquisition_coverage', {})
    if coverage.get('binding') != dict(task_id=task['id'], research_version=task['version'], input_hash=task.get('input_hash')):
        return [], []
    notes = []
    incomplete = set()
    stated = coverage.get('stated_economic_periods', [])
    known_codes = {row['code'] for row in stated}
    gaps = [gap for request in coverage['unresolved'] if request['operation'] == 'edb_service'
            for gap in request['missing'] if gap['code'] not in known_codes]
    for gap in [*stated, *gaps]:
        if not gap.get('periods'):
            continue
        incomplete.add(gap['code'])
        labels = {row['label'] for row in facts if row['code'] == gap['code'] and row['field'] == 'value'}
        name = next(iter(labels)) if len(labels) == 1 else gap['code']
        periods = '、'.join(gap['periods'])
        if gap['missing_period_count'] > len(gap['periods']):
            periods += f'等{gap["missing_period_count"]}期'
        notes.append(f'{name}缺少{periods}读数。')
    notes.extend(_unbound_period_notes(task, {row['question_id'] for row in stated}))
    if reference := economic_period_reference(task):
        if not reference['windows']:
            notes.append('未能确定所指统计期，请明确年份或日期区间。')
        elif reference['code'] not in known_codes:
            notes.append(f'未取得{reference["code"]}在所问统计期及口径下的读数。')
    return list(dict.fromkeys(notes)), sorted(incomplete)


def draft_search_completed(task, search, complete_requests):
    """Skip the extra navigation turn only for covered statistical periods."""
    declared = set(declared_economic_indicators(task))
    questions = set(search.get('question_ids', []))
    if not declared or not questions or not discovery_enabled(task):
        return False

    from .financial_sources import _metadata_records
    from .evidence import _current_api_reference
    directories = _metadata_records(task, 'search_edb')
    matches = next((entry['parsed'] for entry in directories
                    if entry['record']['id'] == search['id']), [])
    if declared.isdisjoint(row['displayId'] for row in matches):
        return False

    series = [record for record in task.get('source_records', [])
              if not complete_requests.isdisjoint(record.get('data_need_ids', []))
              and questions.issubset(record.get('question_ids', []))
              and _current_api_reference(task, record, {'ifind.edb_service'}, 'current_snapshot') is not None]
    if not series:
        return False

    from .draft_sources import observations
    frequencies = _indicator_frequencies(row for directory in directories for row in directory['parsed'])
    series_ids = {record['id'] for record in series}
    merge = draft_period_enabled(task)
    dates = {}
    for fact in observations(task):
        if fact['artifact_id'] in series_ids and fact['code'] in declared and fact['value'] is not None:
            dates.setdefault((None if merge else fact['artifact_id'], fact['code']), set()).add(date.fromisoformat(fact['period']))

    received = set()
    for record in series:
        params = record['parameters']
        indicators = declared.intersection(params['indicators'].split(','))
        first, last = (date.fromisoformat(params[key]) for key in ('startdate', 'enddate'))
        for code in indicators:
            frequency = frequencies.get(code, ())
            months = _FREQUENCY_MONTHS.get(next(iter(frequency))) if len(frequency) == 1 else None
            if months is None:
                return False  # No reliable count for an unregistered frequency or a disputed definition.
            retained = {_period_number(day, months) for day in dates.get((None if merge else record['id'], code), ())
                        if first <= day <= last}
            if len(retained) != _period_number(last, months) - _period_number(first, months) + 1:
                return False
        received.update(indicators)
    return declared.issubset(received)


def with_economic_navigation_coverage(task, coverage):
    """A searched directory is an unfinished data route, not a numeric source.

    Readable files for the same question cannot discharge a declared EDB
    discovery route. One actual, bound business response closes navigation;
    missing observations and financial applicability are still checked later.
    No indicator is selected here and a directory-only question adds no route.
    """
    version = task.get('economic_navigation_version')
    if version is None:
        return coverage
    if type(version) is not int or version != 1:
        raise ContractError('宏观来源导航版本未登记')
    if not discovery_enabled(task) or task.get('intent') == '原件内容核对':
        return coverage
    from .financial_sources import _metadata_records
    from .evidence import _current_api_reference
    eligible = set()
    for question in task.get('questions', []):
        needs = question.get('evidence_needs', [])
        scopes = question.get('evidence_need_scopes', {})
        if (question.get('required', True) and question.get('status') != '已回答'
                and question.get('answer_scope') != 'general_definition' and needs
                and not all(scopes.get(need) == 'query_provenance' for need in needs)):
            eligible.add(question['id'])
    candidates = {}
    searches = _metadata_records(task, 'search_edb')
    for source in searches:
        identifiers = {row['displayId'] for row in source['parsed']}
        if not identifiers:
            continue  # An actual empty search is not proof that data exists.
        for question_id in eligible.intersection(source['record'].get('question_ids', [])):
            candidates.setdefault(question_id, set()).update(identifiers)
    attempted = set()
    for record in task.get('source_records', []):
        if record.get('source') != 'ifind' or record.get('operation') != 'edb_service':
            continue
        if _current_api_reference(task, record, {'ifind.edb_service'}, 'current_snapshot') is None:
            continue
        try:
            validate_economic_request(task, 'edb_service', record['parameters'])
        except (ContractError, KeyError, TypeError):
            continue
        identifiers = set(record['parameters']['indicators'].split(','))
        for question_id in set(record.get('question_ids', [])).intersection(candidates):
            earlier = {row['displayId'] for source in searches
                if question_id in source['record'].get('question_ids', [])
                and source['record']['acquisition']['dispatch_index'] < record['acquisition']['dispatch_index']
                and timepoint(source['record']['obtained_at']) <= timepoint(record['obtained_at'])
                for row in source['parsed']}
            if identifiers and identifiers <= earlier:
                attempted.add(question_id)
    pending = sorted(set(candidates) - attempted)
    return dict(coverage, ready=coverage['ready'] and not pending,
        economic_navigation_version=1, pending_economic_question_ids=pending,
        economic_data_route_attempted_question_ids=sorted(attempted),
        economic_directory_question_ids=sorted(candidates),
        economic_numeric_approval=False,
        economic_route_rule='已搜索到目录的数值需求须继续取得本任务EDB业务响应；目录、其他原件及接口尝试均不证明数值完整或适用，缺失值保留。')
