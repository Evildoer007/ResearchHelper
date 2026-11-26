"""Task-bound stock candidates from the documented iFinD semantic endpoint.

Version1 permits exact identity lookups. Version2 also permits data acquisition
for explicitly selected full codes from current bound responses, not industry
membership approval, financial evidence, complete universes or historical use.
Returned URLs and supplier prose remain untrusted material and are not fetched.
"""
import math
import re

from .contracts import ContractError
from .subject_scope import scope_question, subject_allowed

VERSION = 2
MAX_ROWS = 200
MAX_CANDIDATES = 64
KINDS = ('行业', '产业链')
GENERIC_TOPICS = frozenset(('全部', '所有', '全市场', 'A股', '沪深', '中国', '股票', '上市公司', '研究', '分析', '比较'))
LIMITATION = ('仅当前实际检索返回的候选，不证明行业分类、产品暴露、排名含义或样本完整；'
    '后续请求范围按本任务版本和callable_codes执行，仍需取得所需原件核对业务关联和财务事实。'
    '不自动下载溯源地址，不作为历史信息集、因果、数值证据或投资判断。')


def enabled(task):
    return (type(task.get('object_discovery_version')) is int
            and task['object_discovery_version'] in (1, VERSION)
            and task.get('research_mode') == 'current')


def discovery_request(task, params):
    """Return the explicitly registered discovery shape or None for finance."""
    if not enabled(task) or not isinstance(params, dict):
        return None
    query = params.get('searchstring')
    match = re.fullmatch(r'([A-Za-z0-9\u4e00-\u9fff]{1,24})(行业|产业链)股票', query or '') if isinstance(query, str) else None
    if match is None:
        return None
    if set(params) != {'searchstring', 'searchtype'} or params['searchtype'] != 'stock':
        raise ContractError('候选发现仅使用登记的stock语义查询，不附加字段或条件')
    topic, kind = match.groups()
    texts = [scope_question(task), *task.get('objects', [])]
    if task.get('discovery_planned_scope_version')==1:
        # Approved research questions may express the user's theme more
        # precisely. They widen retrieval vocabulary, never financial scope
        # or approval; supplier responses and material text are not included.
        for question in task.get('questions',[]):
            texts.append(question.get('question',''))
            texts.extend(question.get('evidence_needs',[]))
    if (topic in GENERIC_TOPICS or not subject_allowed(task, topic) or
            not any(isinstance(text, str) and topic in text for text in texts)):
        raise ContractError('候选发现主题须对应当前问题、研究对象或已登记研究计划，不扫描无关市场'
            if task.get('discovery_planned_scope_version')==1 else
            '候选发现主题须逐字来自当前用户问题或研究对象，不猜主题、不扫描全市场')
    return dict(query_kind='object_discovery', topic=topic, discovery_kind=kind)


def discovery_response(task, params, payload):
    requested = discovery_request(task, params)
    if requested is None:
        raise ContractError('本任务未登记当前候选发现请求')
    if (not isinstance(payload, dict) or type(payload.get('errorcode')) is bool
            or payload.get('errorcode') not in (0, '0')):
        raise ContractError('候选发现业务未成功，不作为空表或代码授权')
    tables = payload.get('tables')
    if not isinstance(tables, list) or len(tables) > 8:
        raise ContractError('候选发现表结构异常，不截断后冒充完整返回')
    count, names, locations = 0, {}, {}
    columns = set()
    for table_index, table in enumerate(tables):
        fields = table.get('table') if isinstance(table, dict) else None
        if (not isinstance(fields, dict) or not {'股票代码', '股票简称'} <= set(fields)
                or len(fields) > 64 or any(not isinstance(key, str) or not isinstance(values, list)
                                           for key, values in fields.items())):
            raise ContractError('候选发现缺少实际股票代码、简称或有错位字段')
        length = len(fields['股票代码'])
        count += length
        if count > MAX_ROWS or any(len(values) != length for values in fields.values()):
            raise ContractError('候选发现超过登记行数或列错位，原始响应保留但不开放候选')
        if any(isinstance(value, (dict, list, bool)) or isinstance(value, float) and not math.isfinite(value)
               for values in fields.values() for value in values):
            raise ContractError('候选发现原始字段包含异常值，不推断代码或缺失项')
        columns.update(fields)
        for index, (code, name) in enumerate(zip(fields['股票代码'], fields['股票简称'])):
            if (not isinstance(code, str) or re.fullmatch(r'\d{4,6}\.[A-Z]{2,3}', code) is None
                    or not isinstance(name, str) or not name.strip() or name != name.strip() or len(name) > 80
                    or any(ord(character) < 32 or ord(character) == 127 for character in name)):
                raise ContractError('候选发现缺少明确的原始市场代码或名称，不补交易所、不猜代码')
            if code in names and names[code] != name:
                raise ContractError('同一候选代码返回冲突名称，不选择第一行或合并身份')
            names[code] = name
            locations.setdefault(code, []).append(dict(table=table_index, row=index))
    supported = {code: name for code, name in names.items()
                 if re.fullmatch(r'(?:6\d{5}\.SH|[03]\d{5}\.SZ)', code)}
    candidates = [dict(code=code, name=name, row_locations=locations[code])
                  for code, name in supported.items()][:MAX_CANDIDATES]
    unsupported = [dict(code=code, name=name, row_locations=locations[code],
                        reason='已留存，但后续查询市场未登记，不开放精确映射或其他接口')
                   for code, name in names.items() if code not in supported][:MAX_CANDIDATES]
    return dict(version=task['object_discovery_version'], **requested, raw_only=True, numeric_evidence_registered=False,
        unit_verified=False, industry_membership_verified=False, historical_vintage_verified=False,
        row_count=count, returned_columns=sorted(columns), candidates=candidates,
        returned_unique_codes=len(names), lookup_supported_codes=len(supported),
        shown_candidates=len(candidates), unsupported_candidates=unsupported,
        candidate_list_truncated=len(supported) > MAX_CANDIDATES,
        unsupported_list_truncated=len(names) - len(supported) > MAX_CANDIDATES,
        provider_completeness='未证实',
        limitations=LIMITATION)


def retained_discovery_results(task):
    """Recompute candidates only from actual same-task metered responses."""
    if not enabled(task):
        return []
    from .evidence import current_query_reference
    result = []
    for record in task.get('source_records', []):
        if record.get('source') != 'ifind' or record.get('operation') != 'smart_stock_picking':
            continue
        try:
            if discovery_request(task, record.get('parameters')) is None or current_query_reference(task, record) is None:
                continue
            contract = discovery_response(task, record['parameters'], record['snapshot'])
        except (ContractError, TypeError, ValueError):
            continue
        result.append(dict(artifact_id=record['id'], snapshot_hash=record['hash'],
            request_hash=record['request_hash'], question_ids=list(record.get('question_ids', [])), **contract))
    return result


def identity_query_candidates(task):
    """Only values for a subsequent exact lookup, never callable stock codes."""
    return [candidate for result in retained_discovery_results(task) for candidate in result['candidates']]


def callable_discovery_codes(task):
    """Current source-bound retrieval scope, never a security/industry verdict.

    The subsequent data request explicitly chooses a returned full code. Do
    not mutate the user's objects, create a user declaration, auto-select the
    whole candidate list, or grant a direct-observation evidence reference.
    Conflicting names across retained queries cannot widen callable scope.
    """
    if type(task.get('object_discovery_version')) is not int or task['object_discovery_version'] != 2:
        return []
    names = {}
    for candidate in identity_query_candidates(task):
        names.setdefault(candidate['code'], set()).add(candidate['name'])
    return sorted(code for code, values in names.items() if len(values) == 1)


def research_sample_codes(task, question_scope=None):
    """Queryable candidates are not automatically the calculation universe.

    Preserve user-declared and uniquely mapped objects. Candidate-only codes
    enter the research sample only after an explicit, actually returned
    business request for the relevant question/dependency scope. No candidate
    is chosen here and no financial input is approved.
    """
    from .evidence import (available_security_codes, declared_security_codes,
                           identity_resolutions, current_query_reference)
    available = set(available_security_codes(task))
    if task.get('discovery_sample_scope_version') != 1 or not enabled(task):
        return sorted(available)
    selected = set(declared_security_codes(task))
    selected.update(code for row in identity_resolutions(task) if row.get('status') == '代码映射已核验'
                    for code in row['codes'])
    for record in task.get('source_records', []):
        if question_scope is not None and not set(record.get('question_ids', [])).intersection(question_scope):
            continue
        if record.get('operation') == 'get_thscode' or current_query_reference(task, record) is None:
            continue
        parameters = record.get('parameters', {})
        if record.get('operation') == 'smart_stock_picking':
            if discovery_request(task, parameters) is not None:
                continue
            from .financial_sources import validate_financial_query
            try:
                code = validate_financial_query(task, parameters).get('code')
            except ContractError:
                continue
            if code in available:
                selected.add(code)
        else:
            codes = parameters.get('codes', parameters.get('ts_code', ''))
            if isinstance(codes, str):
                selected.update(set(codes.split(',')) & available)
    return sorted(selected & available)


def request_guidance(task, *, include_results=True):
    if not enabled(task):
        return {}
    guidance = dict(version=task['object_discovery_version'], searchstring_templates=['用户原文主题+行业股票', '用户原文主题+产业链股票'],
        topic_rule='主题须为当前用户问题或objects内逐字连续的1至24字中文、字母或数字，不加入日期、交易条件或自创主题。'
                   '例如用户明确提到证券或算力时，才可分别组装证券行业股票或算力产业链股票；示例不构成默认研究范围。',
        searchtype='stock', limitations=LIMITATION,
        maximum_response_rows=MAX_ROWS, maximum_identity_candidates=MAX_CANDIDATES,
        next_step='从results明确选择所需候选，用get_thscode精确转换其实际股票简称或不含交易所的原始六位代码。'
                  '候选发现本身不开放行情、财报或公告查询权限；代码映射通过后再按问题取数。')
    if task['object_discovery_version'] == 2:
        guidance['next_step'] = ('从当前实际返回且列入callable_codes的完整市场代码中，显式选择研究所需样本继续取数；'
            '不要求把已明确的市场代码再次做名称转换。来源绑定只批准请求范围，不批准身份、行业归属、业务暴露或数值证据。'
            '不把样本写成用户明确声明的对象，不伪造direct_code_reference；相关原件与后续金融核验照常执行。')
        guidance['retrieval_scope_only'] = True
        guidance['sample_rule'] = ('可查询的候选集合不是实际研究或计算样本。按原问题显式选择需要取数的完整代码，'
            '不得默认将全部候选纳入计算或声称一家公司代表行业。')
    if task.get('discovery_planned_scope_version')==1:
        guidance['searchstring_templates']=['研究主题+行业股票','研究主题+产业链股票']
        guidance['topic_rule']=('主题可来自用户问题、objects或已登记研究子问题及数据需求，不要求重复用户原句。'
            '使用其中连续的1至24字主题；不从供应商文本或材料新增无关主题，不扫描全市场。'
            '这只允许候选检索，不证明行业分类、样本完整或实际业务暴露。')
    if include_results:
        guidance['results'] = retained_discovery_results(task)
    else:
        guidance['results_location'] = 'data_request_contract.object_discovery.results'
    return guidance


def with_discovery_coverage(task, views, coverage):
    """A candidate list or identity lookup is navigation, not business data.

    Applies only to explicitly discovery-linked research questions. It does
    not require PDFs: an actual suitable API response can finish acquisition,
    while the separate extraction/method gates still decide admissibility.
    Explicit query-provenance-only needs keep their metadata-only route.
    """
    if not enabled(task):
        return coverage
    questions = {}
    for question in task.get('questions', []):
        needs, scopes = question.get('evidence_needs', []), question.get('evidence_need_scopes', {})
        if (not question.get('required', True) or question.get('answer_scope') == 'general_definition'
                or not (needs or question.get('method_needs'))
                or needs and not question.get('method_needs')
                and all(scopes.get(need) == 'query_provenance' for need in needs)):
            continue
        questions[question['id']] = question
    targeted = {ident for result in retained_discovery_results(task)
                for ident in result['question_ids'] if ident in questions}
    readable = {view['id'] for view in views}
    covered = set()
    for record in task.get('source_records', []) + task.get('materials', []):
        if (record.get('id') not in readable or record.get('task_id') != task['id']
                or record.get('research_version') != task['version']):
            continue
        if record.get('kind') == 'api':
            if record.get('operation') in ('get_thscode', 'report_query', 'release_catalog'):
                continue
            if record.get('source') == 'ifind' and record.get('operation') == 'smart_stock_picking':
                try:
                    if discovery_request(task, record.get('parameters')) is not None:
                        continue
                except ContractError:
                    continue
        covered.update(record.get('question_ids', []))
    pending = [ident for ident in questions if ident in targeted - covered]
    return dict(coverage, ready=coverage['ready'] and not pending,
        pending_discovery_question_ids=pending,
        discovery_followup_rule='候选目录和身份映射仅完成对象发现，继续按原问题取得实际业务数据或原件。'
            '不要求所有候选都下载，不强制PDF；内容、单位、方法及样本代表性仍单独核验。')
