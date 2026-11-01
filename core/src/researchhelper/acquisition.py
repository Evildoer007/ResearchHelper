"""Bounded initial source navigation, separate from financial supplementation.

Names and filing directories unlock requests; neither is financial evidence.
Every navigation decision shares the task ledger and is checkpointed before I/O.
"""
import copy
import re

from .contracts import (ContractError, DOCUMENT_CONTENT_INTENT, FINANCIAL_PERIOD_METHODS,
                        FINANCIAL_INPUT_METRICS, METHOD_REGISTRY, fingerprint)
from .evidence import data_request_contract, identity_resolutions, filing_candidate_choices
from .planning import question_specs
from .reasoning import (extractable_source_views, original_sources, model_object,
                        supplement_request_blocked, validate_plan)
from .source_reader import context_sources

MAX_DECISIONS = 3
ORIGINAL_DISCOVERY = frozenset({('ifind', 'report_query'), ('ifind', 'filing_original'),
    ('official', 'release_catalog'), ('official', 'release_original')})


def _structured_financial_question(task, question):
    """Typed amount needs only; prose source suggestions are not obligations."""
    if (type(task.get('fixed_financial_context_version')) is not int
            or task['fixed_financial_context_version'] != 1
            or task.get('research_mode') != 'current' or task.get('intent') == DOCUMENT_CONTENT_INTENT):
        return False
    methods = question.get('method_needs', [])
    needs = question.get('evidence_needs', [])
    constraints = question.get('evidence_need_constraints', {})
    # Explicit document requests are vetoes, never a positive inference of
    # numeric suitability. Need prose such as "来源：半年度报告" is not read.
    request_text = (task.get('question', '') + '\n' + question.get('question', '')).casefold()
    if any(token in request_text for token in ('原文', '原件', 'pdf', '公告内容', '原因', '解释', '母公司报表')):
        return False
    return bool(methods and needs and all(method in FINANCIAL_PERIOD_METHODS for method in methods)
        and all(constraints.get(need, {}).get('value_kind') == 'number'
                and constraints[need].get('unit_dimension') == 'amount'
                and constraints[need].get('period_kind') == 'interval' for need in needs))


def _financial_question_target(question):
    """Conservative literal date decoding; ambiguous/multiple periods abstain."""
    codes = sorted(set(re.findall(r'(?<!\d)\d{6}\.(?:SH|SZ|BJ)', question['question'])))
    text = question['question']
    labels = re.findall(r'(\d{4})年(上半年|半年度|年度|1-6月|1至6月|1-12月|1至12月)', text)
    if len(codes) != 1 or len(set(labels)) != 1:
        return None
    year, label = labels[0]
    month = '06' if label in ('上半年', '半年度', '1-6月', '1至6月') else '12'
    return codes[0], year + month + ('30' if month == '06' else '31'), f'{year}年1-{int(month)}月'


def _financial_api_first_questions(task):
    """Offer one bounded structured attempt only where controlled definitions fit."""
    from .evidence import _controlled_financial_definitions
    if not any(_structured_financial_question(task, question) for question in task['questions']):
        return set()
    definitions = _controlled_financial_definitions()
    result = set()
    for question in task['questions']:
        if not _structured_financial_question(task, question):
            continue
        target = _financial_question_target(question)
        if target is None:
            continue
        code, report_end, _ = target
        needed = {role['tag_value'] for method in question['method_needs'] for role in _method_source_roles(method)}
        available = {definition['claims']['metric'] for definition in definitions.values()
            if code in definition.get('claims', {}).get('applicable_codes', [])
            and report_end in definition['claims'].get('applicable_periods', [])
            and all(definition['claims'].get(key) for key in ('unit', 'currency', 'accounting_standard', 'statement_scope'))}
        attempted = any(record.get('operation') == 'basic_data_service'
            and question['id'] in record.get('question_ids', []) for record in task.get('source_records', []))
        if needed and len(question['evidence_needs']) <= len(needed) and needed <= available and not attempted:
            result.add(question['id'])
    return result


def _financial_api_covered_questions(task, views):
    """Navigation coverage, never source/applicability or calculation approval."""
    from .evidence import api_cell_choices
    from .contracts import validate_calculation_context
    available = {view['id'] for view in views if view.get('kind') == 'api'}
    result = set()
    for question in task['questions']:
        if not _structured_financial_question(task, question):
            continue
        target = _financial_question_target(question)
        if target is None:
            continue
        code, _, period = target
        cells = [cell for record in task.get('source_records', [])
            if record.get('id') in available and record.get('task_id') == task['id']
            and record.get('research_version') == task['version']
            and record.get('operation') == 'basic_data_service' and question['id'] in record.get('question_ids', [])
            for cell in api_cell_choices(task, record) if cell['object'] == code and cell['period'] == period]
        by_metric = {}
        for cell in cells:
            by_metric.setdefault(cell['calculation_context']['metric'], []).append(cell)
        needed = {role['tag_value'] for method in question['method_needs'] for role in _method_source_roles(method)}
        if (not needed or len(question['evidence_needs']) > len(needed)
                or any(len(by_metric.get(metric, [])) != 1 for metric in needed)):
            continue
        try:
            for method in question['method_needs']:
                bindings = {name: by_metric[role['tag_value']][0] for role in _method_source_roles(method) for name in role['inputs']}
                first = next(iter(bindings.values()))
                context = first['calculation_context']
                validate_calculation_context(method, bindings, dict(amount_unit=first['unit'],
                    **{key: context[key] for key in ('currency', 'period', 'accounting_scope', 'accounting_standard')}))
        except ContractError:
            continue
        result.add(question['id'])
    return result


def _structured_navigation_tools(task, tools):
    required = {q['id'] for q in task['questions'] if q.get('required', True)}
    first = _financial_api_first_questions(task)
    if required and required <= first:
        return [tool for tool in tools if tool.get('operation') not in ('filing_original', 'release_original')]
    return tools


def original_navigation_tools(task, tools):
    """Keep structured financial discovery available without approving values.

    Earlier navigation narrowed every missing financial input to a file.
    A new task may instead resolve its field, period and scope through the
    implemented API contracts. This does not make a raw-only cell calculable.
    """
    from .financial_sources import structured_source_preference, IFIND_METADATA_OPERATIONS
    allowed = ORIGINAL_DISCOVERY | {('ifind', 'get_thscode')}
    if structured_source_preference(task) is not None:
        allowed |= {('ifind', operation) for operation in (
            *IFIND_METADATA_OPERATIONS, 'basic_data_service', 'smart_stock_picking')}
    if type(task.get('economic_navigation_version')) is int and task['economic_navigation_version'] == 1:
        allowed |= {('ifind', 'edb_service')}  # Filter only; never creates a tool or grants permission.
    return [tool for tool in tools if (tool.get('source'), tool.get('operation')) in allowed]


def acquisition_navigation_budget(task):
    """Finite navigation from the existing ledger, never a second allowance."""
    version=task.get('acquisition_budget_navigation_version')
    if version is None:
        return None
    if type(version) is not int or version!=1:
        raise ContractError('初次导航预算策略版本未登记')
    ledger=task['ledger']
    # Initial extraction and analysis need ordinary calls; the existing
    # delivery reserve remains available for the final independent review.
    protected=max(1,ledger['reserve']['model_calls'])+2
    downstream=task.get('acquisition_downstream_budget_version')
    if downstream is not None and (type(downstream) is not int or downstream!=1):
        raise ContractError('取数后续阶段预算策略版本未登记')
    binding_needed=False
    if downstream==1:
        # Match deferred-binding eligibility without choosing evidence or
        # approving financial applicability before the inputs are available.
        binding_needed=any(
            method in METHOD_REGISTRY and not METHOD_REGISTRY[method].get('assumption_units')
            and not any(spec.get('method')==method and question['id'] in spec.get('question_ids',[])
                for spec in task.get('calculation_specs',[]))
            for question in task.get('questions',[])
            if question.get('required',True) and question.get('status')!='已回答'
            for method in question.get('method_needs',[]))
        protected+=int(binding_needed)
    result=dict(version=1,additional_decisions=max(0,
        ledger['limits']['model_calls']-ledger['used']['model_calls']-protected),
        protected_model_calls=protected,budget_reset=False,
        rule='依据原账本剩余额度及实际新增进展继续，不固定导航次数；保留提取、分析复核及原交付预留。')
    if downstream==1:
        result['downstream_protection']=dict(version=1,extraction=1,
            calculation_binding=int(binding_needed),analysis=1,review=max(1,ledger['reserve']['model_calls']))
        if binding_needed:
            result['rule']='依据原账本剩余额度及实际新增进展继续；保留提取、计算绑定、分析、独立复核及原交付预留。'
    return result


def navigation_question_projection(task, source_contract, coverage):
    """Offer the same unresolved source scopes that the navigation guard uses.

    The original research questions, source permissions and financial evidence
    requirements do not change. No request question IDs are filled or repaired.
    """
    contract = copy.deepcopy(source_contract)
    questions = question_specs(task)
    if type(task.get('navigation_question_scope_version')) is not int or task['navigation_question_scope_version'] != 1:
        return contract, questions
    pending = set(coverage['pending_original_question_ids'] + coverage.get('pending_method_question_ids', [])
                  + coverage.get('pending_discovery_question_ids', [])
                  + coverage.get('pending_economic_question_ids', [])
                  + coverage.get('pending_financial_batch_question_ids', []))
    eligible = {q['id'] for q in task['questions'] if q.get('required', True)
                and q.get('status') != '已回答' and q.get('answer_scope') != 'general_definition'}
    allowed = [q['id'] for q in questions if q['id'] in eligible and (not pending or q['id'] in pending)]
    contract['question_ids'] = allowed
    contract['navigation_question_scope'] = dict(version=1, question_ids=allowed,
        financial_approval=False, automatic_assignment=False,
        rule='仅为本次待补来源问题选择question_ids；原件与独立行情分支可分别推进。不要复制旧请求或其他已就绪分支的编号，不代表资料已含所需事实。')
    for tool in contract.get('tools', []):
        extra = tool.get('examples', [])
        examples = [tool.get('example')] + (extra if isinstance(extra, list) else [])
        for example in examples:
            if not isinstance(example, dict) or 'question_ids' not in example:
                continue
            refs = example['question_ids']
            if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in allowed for ref in refs):
                example.pop('question_ids')
    return contract, [q for q in questions if q['id'] in allowed]


def validate_navigation_question_scope(task, needs, coverage, scope):
    if type(task.get('navigation_question_scope_version')) is int and task['navigation_question_scope_version'] == 1:
        for need in needs:
            refs = need.get('question_ids')
            if (not isinstance(refs, list) or not refs
                    or any(not isinstance(ref, str) or ref not in scope for ref in refs)):
                raise ContractError('初次导航须选择本轮尚缺来源的问题编号，不关联已就绪或未知分支')
    elif coverage['pending_original_question_ids'] and any(
            not set(need.get('question_ids', [])).intersection(coverage['pending_original_question_ids']) for need in needs):
        raise ContractError('原件依赖导航须关联尚未完成的必需问题，不重复已就绪分支')


def _method_source_roles(method):
    """Source-planning metadata, not a change to versioned method execution."""
    definition = METHOD_REGISTRY[method]
    if not definition.get('binding_context'):
        return []  # Do not demand market originals for user-supplied scenarios.
    roles = {}
    for name, unit in definition['input_units'].items():
        if method in FINANCIAL_INPUT_METRICS:
            field, value = 'metric', FINANCIAL_INPUT_METRICS[method][name]
        elif name.startswith('management_'):
            field, value = 'metric', 'management_fee'
        elif name.startswith('custody_'):
            field, value = 'metric', 'custody_fee'
        elif method == 'fee_difference_v2' and name in ('fee_a', 'fee_b'):
            field, value = 'rate_period', 'annual'
        elif name in ('price', 'start_price', 'end_price'):
            field, value = 'price_kind', 'close'
        elif name in ('start_level', 'end_level'):
            field, value = 'level_kind', 'close'
        elif name == 'nav':
            field, value = 'nav_kind', 'official'
        else:
            continue
        role = {'price_kind': 'close_price', 'level_kind': 'close_index_level', 'nav_kind': 'official_nav'}.get(field, value)
        key = field, value
        item = roles.setdefault(key, dict(role=role, tag_field=field, tag_value=value, inputs=[], units=[]))
        item['inputs'].append(name)
        item['units'] = sorted(set(item['units']).union(definition.get('input_unit_variants', {}).get(name, [unit])))
    return list(roles.values())


def _numeric_api_source_paths(record):
    """Registered fields in actual hash-bound responses; never value approval."""
    from .evidence import api_calculation_context_contract, _registered_api_fields
    contract = api_calculation_context_contract(record)
    if contract['status'] != 'registered_constraints':
        return []
    raw = record['snapshot']
    tables = ([(str(i), table.get('thscode')) for i, table in enumerate(raw.get('tables', [])) if isinstance(table, dict)]
              if record['source'] == 'ifind' else [('row', row.get('ts_code'))
                  for row in raw.get('data', []) if isinstance(row, dict)])
    paths, seen = [], set()
    for index, code in tables:
        metadata = contract.get('table_contexts', {}).get(index, contract)
        fields = _registered_api_fields(record, code)
        for tag, choices in metadata.get('column_fields', {}).items():
            for column, value in choices.items():
                if column not in fields or column not in metadata.get('registered_numeric_fields', contract['registered_numeric_fields']):
                    continue
                key = code, tag, value, fields[column]['unit']
                if key in seen:
                    continue
                seen.add(key)
                available = set(metadata.get('fixed_fields', {})) | set(metadata.get('row_fields', []))
                available.update(field for field, mapping in metadata.get('column_fields', {}).items() if column in mapping)
                if contract.get('unverified_context', {}).get('corporate_action_status', {}).get('value') == 'unknown':
                    available.add('corporate_action_status')
                paths.append(dict(artifact_id=record['id'], object=code, tag_field=tag, tag_value=value,
                    unit=fields[column]['unit'], context_fields=sorted(available)))
    return paths


def _selectable_api_source_paths(task, record, available_ids):
    """Potential source paths from registered cells and explicit context links.

    The native fields and selectable supplementary fields stay separate. This
    never chooses a link, changes an evidence item, or approves a calculation.
    """
    from .evidence import api_cell_choices
    from .etf_sources import share_context_choices
    paths=_numeric_api_source_paths(record)
    for cell in api_cell_choices(task,record):
        context=cell['calculation_context']
        additions=(share_context_choices(task,record,cell['record_locator'],cell['object'],allowed_ids=available_ids)
            if 'share_class' not in context else [])
        for tag in ('price_kind','nav_kind','level_kind', *(['metric'] if type(task.get('fixed_financial_context_version')) is int and task['fixed_financial_context_version']==1 else [])):
            if tag not in context:
                continue
            paths.append(dict(artifact_id=record['id'],object=cell['object'],tag_field=tag,
                tag_value=context[tag],unit=cell['unit'],context_fields=sorted(context),
                selectable_context_fields=sorted({a['field'] for a in additions}),
                context_reference_candidates=[dict(context_ref=a['context_ref'],field=a['field'],
                    source_artifact_id=a['basis']['artifact_id']) for a in additions]))
    return paths


def _with_method_source_coverage(task, views, result):
    """Cover each required input path before spending on interpretation.

    A readable original is only a potential source. Its data, unit, period,
    identity and suitability still go through the ordinary independent gates.
    """
    if type(task.get('method_source_coverage_version')) is not int or task['method_source_coverage_version'] != 1:
        return result
    policy=task.get('registered_api_source_paths_version')
    if policy is not None and (type(policy) is not int or policy!=1):
        raise ContractError('登记API来源路径策略版本未登记')
    selectable=(policy==1 and task.get('research_mode')=='current' and task.get('intent')!=DOCUMENT_CONTENT_INTENT)
    questions = {q['id']: q for q in task['questions']}
    if not any(question.get('required', True) and question.get('answer_scope') != 'general_definition'
               and any(method in METHOD_REGISTRY and _method_source_roles(method)
                       for method in question.get('method_needs', [])) for question in questions.values()):
        return dict(result, method_source_coverage_version=1, method_inputs=[],
                    pending_method_inputs=[], pending_method_question_ids=[])
    from .evidence import declared_security_codes
    from .market_series import series_family
    from .reasoning import filing_object_binding
    records = {record['id']: record for record in task.get('source_records', []) + task.get('materials', [])
               if record.get('task_id') == task['id'] and record.get('research_version') == task['version']}
    available_ids={view['id'] for view in views}
    api_paths = [dict(path, question_ids=records[view['id']].get('question_ids', []))
        for view in views if view.get('kind') == 'api' and view['id'] in records
        for path in (_selectable_api_source_paths(task,records[view['id']],available_ids)
            if selectable else _numeric_api_source_paths(records[view['id']]))]
    originals = [view for view in views if view.get('kind') in ('material', 'filing') and view['id'] in records]
    # Availability needs only retained issuer bindings, not extraction selectors
    # for every page. Financial interpretation still builds the full contract.
    original_descriptions = [dict(artifact_id=view['id'], object_binding=dict(
        value=filing_object_binding(task, records[view['id']]))) for view in originals]
    from .object_discovery import research_sample_codes
    items = []
    for question in questions.values():
        if not question.get('required', True) or question.get('answer_scope') == 'general_definition':
            continue
        scope, queue = set(), [question['id']]
        while queue:
            ident = queue.pop()
            if ident in scope or ident not in questions:
                continue
            scope.add(ident); queue.extend(questions[ident].get('dependencies', []))
        explicit = declared_security_codes(dict(question=question['question'], objects=[]))
        codes = explicit or research_sample_codes(task, scope)
        for method in question.get('method_needs', []):
            if method not in METHOD_REGISTRY:
                continue
            definition = METHOD_REGISTRY[method]
            roles = _method_source_roles(method)
            if not roles:
                # Legacy/scenario methods have no registered market-source
                # roles. Do not invent a context or crash before downloading
                # originals already required by the research question.
                continue
            targets = codes
            if 'fund_id' in definition['binding_context']:
                targets = [code for code in codes if series_family(code) in ('ETF', None)]
            elif 'index_id' in definition['binding_context']:
                targets = [code for code in codes if series_family(code) in ('指数', None)]
            elif 'entity_id' in definition['binding_context']:
                targets = [code for code in codes if series_family(code) in ('个股', None)]
            for code in targets or [None]:
                original_ids = []
                for source in original_descriptions:
                    record = records[source['artifact_id']]
                    binding = source.get('object_binding') or {}
                    if not scope.intersection(record.get('question_ids', [])):
                        continue
                    if code is not None and binding.get('value') not in (None, code):
                        continue
                    if code is not None and record.get('kind') != 'material' and binding.get('value') != code:
                        continue
                    original_ids.append(record['id'])
                for role in roles:
                    from .contracts import calculation_context_contract
                    role_contract = calculation_context_contract(task, method)
                    required = definition['binding_context']
                    if role_contract:
                        required = sorted({field for name in role['inputs'] for field in role_contract['input_fields'][name]})
                    groups = [binding.split('或') for binding in required]
                    groups = [[role['tag_field']] if role['tag_field'] in group else group for group in groups]
                    matching = [path for path in api_paths if (code is None or path['object'] == code)
                        and scope.intersection(path['question_ids']) and path['tag_field'] == role['tag_field']
                        and path['tag_value'] == role['tag_value'] and path['unit'] in role['units']]
                    missing = min(([ '/'.join(group) for group in groups if not set(group).intersection(path['context_fields'])]
                                   for path in matching), key=len, default=['/'.join(group) for group in groups])
                    path_missing=(min((['/'.join(group) for group in groups if not set(group).intersection(
                        set(path['context_fields'])|set(path.get('selectable_context_fields',[])))]
                        for path in matching),key=len,default=['/'.join(group) for group in groups]) if selectable else missing)
                    ready = bool(original_ids) or bool(matching) and not path_missing
                    items.append(dict(question_id=question['id'], method=method, object=code,
                        role=role['role'], input_names=role['inputs'], source_path_ready=ready,
                        api_source_ids=sorted({path['artifact_id'] for path in matching}),
                        original_source_ids=sorted(set(original_ids)), missing_api_context_fields=missing,
                        requires_original_path=not ready and (bool(matching) or role['tag_field'] not in ('price_kind', 'level_kind')),
                        financial_approval=False, numeric_values_approved=False))
                    if selectable:
                        refs=[]
                        for path in matching:
                            for ref in path.get('context_reference_candidates',[]):
                                if ref not in refs:refs.append(copy.deepcopy(ref))
                        items[-1].update(missing_source_path_fields=path_missing,
                            selectable_context_references=refs,automatic_context_selection=False)
    pending = [item for item in items if not item['source_path_ready']]
    return dict(result, ready=result['ready'] and not pending, method_source_coverage_version=1,
        method_inputs=items, pending_method_inputs=pending,
        pending_method_question_ids=list(dict.fromkeys(item['question_id'] for item in pending)))


def initial_acquisition_coverage(task):
    """Source availability only; one ready API does not discharge other originals.

    Only declared discovery requests and their task-bound directory results
    establish original-source obligations. This does not require PDFs for a
    price-only question, nor approve any file's contents or financial use.
    """
    views = extractable_source_views(task)
    result = dict(version=1, ready=bool(views), financial_approval=False,
        required_original_question_ids=[], pending_original_question_ids=[],
        available_original_ids=[])
    if task.get('initial_acquisition_scope_version') != 1:
        return _with_method_source_coverage(task, views, result)
    questions = {}
    for question in task['questions']:
        if not question.get('required', True) or question.get('answer_scope') == 'general_definition':
            continue
        needs, scopes = question.get('evidence_needs', []), question.get('evidence_need_scopes', {})
        if needs and not question.get('method_needs') and all(scopes.get(need) == 'query_provenance' for need in needs):
            continue
        questions[question['id']] = question
    records = [record for record in task.get('source_records', []) + task.get('materials', [])
        if record.get('task_id') == task['id'] and record.get('research_version') == task['version']]
    by_id = {record['id']: record for record in records}

    def scope(need):
        source, operation = need.get('source', 'ifind'), need.get('operation')
        parameters = need.get('parameters', {})
        if source == 'official' and operation == 'release_original' and 'selection_ref' in parameters:
            from .official_sources import canonical_request_parameters
            try:
                parameters = canonical_request_parameters(task, operation, parameters)
            except ContractError:
                # An invalid explicit selection is not a wildcard fulfilled
                # by some other readable original for the same question.
                return source, 'original', 'invalid_reference_' + fingerprint(parameters)
        if operation in ('report_query', 'release_catalog'):
            value = parameters.get('codes' if operation == 'report_query' else 'channel')
            return source, 'catalog', value if isinstance(value, str) and value else '*'
        value = parameters.get('source_id' if operation == 'filing_original' else 'release_id')
        return source, 'original', value if isinstance(value, str) and value else '*'

    def scope_pairs(need):
        from .source_requirements import catalogue_scopes
        alternatives=catalogue_scopes(task,need)
        return alternatives if alternatives is not None else [(qid,scope(need)) for qid in need.get('question_ids',[])]

    targeted = set()
    api_covered = _financial_api_covered_questions(task, views)
    for need in task.get('data_need_specs', []) + records:
        if (need.get('source', 'ifind'), need.get('operation')) in ORIGINAL_DISCOVERY:
            targeted.update((ident, source_scope) for ident,source_scope in scope_pairs(need)
                if ident in questions and ident not in api_covered)
    eligible = {view['id'] for view in views if view.get('kind') in ('material', 'filing')}
    covered, available = set(), []
    for record in records:
        if record.get('id') in eligible and record.get('kind') in ('material', 'filing'):
            supported = {(record.get('source'), 'catalog', '*'), (record.get('source'), 'original', '*')}
            if (record.get('source'), record.get('operation')) in ORIGINAL_DISCOVERY:
                supported.add(scope(record))
                parent = by_id.get(record.get('parameters', {}).get('catalog_id'))
                if parent and (parent.get('source'), parent.get('operation')) in ORIGINAL_DISCOVERY:
                    supported.update(source_scope for ident,source_scope in scope_pairs(parent)
                        if ident in record.get('question_ids',[]))
            related = {(ident, source_scope) for ident, source_scope in targeted
                       if ident in record.get('question_ids', []) and source_scope in supported}
            if related:
                covered.update(related)
                available.append(record['id'])
    required = [ident for ident in questions if any(qid == ident for qid, _ in targeted)]
    missing = targeted - covered
    pending = [ident for ident in required if any(qid == ident for qid, _ in missing)]
    result = dict(result, ready=bool(views) and not pending,
        required_original_question_ids=required, pending_original_question_ids=pending,
        pending_original_scopes=[dict(question_id=ident, source=source, kind=kind, target=value)
                                 for ident, (source, kind, value) in sorted(missing)],
        available_original_ids=list(dict.fromkeys(available)))
    from .object_discovery import with_discovery_coverage
    from .financial_sources import with_financial_batch_coverage
    from .period_targets import with_coverage as with_period_coverage
    from .economic_sources import with_economic_navigation_coverage
    return with_economic_navigation_coverage(task,
        with_period_coverage(task, views, with_financial_batch_coverage(task, views,
            with_discovery_coverage(task, views, _with_method_source_coverage(task, views, result)))))


def allocate_request_ids(task, decisions, needs):
    """Compile response-local request IDs; never rewrite source/catalog IDs."""
    used = {need['id'] for need in task.get('data_need_specs', [])}
    used.update(need['id'] for entry in decisions[:-1] for need in entry.get('data_requests', []))
    allocated, mapping = [], []
    for index, need in enumerate(needs, start=1):
        stem = 'navigation_' + str(len(decisions)) + '_' + need['operation']
        serial = index
        identity = stem + '_' + str(serial)
        while identity in used:
            serial += 1
            identity = stem + '_' + str(serial)
        used.add(identity)
        allocated.append(dict(copy.deepcopy(need), id=identity))
        mapping.append({'proposal_id':need['id'],'request_id':identity})
    return allocated, mapping


def negative_catalogue_searches(task):
    """A classified empty search is bounded query coverage, never evidence.

    Only an actually dispatched, retained iFinD -4001 catalog request counts.
    Authentication, transport, unknown responses and repeated hashes do not.
    """
    if type(task.get('catalogue_search_navigation_version')) is not int or task['catalogue_search_navigation_version'] != 1:
        return []
    needs = list(task.get('data_need_specs', []))
    for decision in task.get('reasoning_state', {}).get('acquisition', {}).get('decisions', []):
        needs.extend(decision.get('data_requests', []))
    requests = {fingerprint(dict(source='ifind', operation='report_query', parameters=need['parameters'])): need
        for need in needs if need.get('source', 'ifind') == 'ifind' and need.get('operation') == 'report_query'
        and isinstance(need.get('parameters'), dict)}
    seen, result = set(), []
    for event in task.get('ledger', {}).get('dispatches', []):
        key = event.get('fingerprint')
        if (event.get('kind') != 'data_requests' or event.get('source') != 'ifind'
                or event.get('operation') != 'report_query' or event.get('status') != '未返回数据'
                or event.get('business_code') not in (-4001, '-4001')
                or event.get('supplier_rejection_category') != 'no_data' or key not in requests or key in seen):
            continue
        seen.add(key)
        result.append(dict(request_hash=key, parameters=copy.deepcopy(requests[key]['parameters']),
            question_ids=copy.deepcopy(requests[key].get('question_ids', [])),
            result='本请求范围未返回数据', financial_approval=False, absence_of_event_proven=False))
    return result


def navigation_progress(task):
    """Verified navigation inputs, not source or financial approval."""
    mappings = [item for item in identity_resolutions(task) if item.get('status') == '代码映射已核验']
    from .official_sources import release_choices
    progress = (set(fingerprint(item) for item in mappings), set(
        fingerprint(item) for item in filing_candidate_choices(task)['selections']+release_choices(task)), set(
        fingerprint(item) for item in extractable_source_views(task)))
    if task.get('catalogue_search_navigation_version') == 1:
        progress += ({item['request_hash'] for item in negative_catalogue_searches(task)},)
    version = task.get('metadata_navigation_progress_version')
    if version is not None:
        if type(version) is not int or version != 1:
            raise ContractError('元数据导航进展版本未登记')
        from .financial_sources import IFIND_METADATA_OPERATIONS, _metadata_records
        # Policy and dictionaries are necessary predecessors, not financial
        # evidence. A verified, newly usable response is actual progress even
        # before it can yield a price or amount. Equivalent repeated bytes do
        # not count twice; imports, errors and tampered responses never count.
        metadata = set()
        for operation in IFIND_METADATA_OPERATIONS:
            for item in _metadata_records(task, operation):
                if item['parsed']:
                    record = item['record']
                    metadata.add(fingerprint(dict(operation=operation, parameters=record['parameters'],
                        snapshot_hash=record.get('snapshot_hash',record['hash']))))
        progress += (metadata,)
    response_version = task.get('financial_response_navigation_version')
    if response_version is not None:
        if type(response_version) is not int or response_version != 1:
            raise ContractError('财务响应导航版本未登记')
        from .financial_sources import _metadata_records
        # A transport-bound, schema-valid business response is new retrieval
        # progress even when financial units still require separate evidence.
        # Count each response once; this neither approves numbers nor declares
        # the requested financial analysis complete.
        received = set()
        for item in _metadata_records(task, 'basic_data_service'):
            record = item['record']
            received.add(fingerprint(dict(operation=record['operation'], parameters=record['parameters'],
                snapshot_hash=record.get('snapshot_hash', record['hash']))))
        progress += (received,)
    return progress


def complete_initial_acquisition(engine, task):
    if (task.get('run_mode') != 'model' or task.get('intent') == DOCUMENT_CONTENT_INTENT
            or engine.offline or engine._active_supplement(task)):
        return
    if not any(q.get('evidence_needs') or q.get('method_needs')
               for q in task['questions'] if q.get('answer_scope') != 'general_definition'):
        return
    budget_policy=acquisition_navigation_budget(task)
    coverage = initial_acquisition_coverage(task)
    if coverage['ready']:
        return
    binding = {key: task[key] for key in ('id', 'version', 'cutoff', 'input_hash')}
    if task.get('initial_acquisition_scope_version') == 1:
        binding['initial_acquisition_scope_version'] = 1
    if type(task.get('method_source_coverage_version')) is int and task['method_source_coverage_version'] == 1:
        binding['method_source_coverage_version'] = 1
    if type(task.get('official_selection_reference_version')) is int and task['official_selection_reference_version'] == 1:
        binding['official_selection_reference_version'] = 1
    if budget_policy is not None:
        binding['acquisition_budget_navigation_version']=1
    if budget_policy is not None and task.get('acquisition_downstream_budget_version')==1:
        binding['acquisition_downstream_budget_version']=1
    if task.get('registered_api_source_paths_version')==1:
        binding['registered_api_source_paths_version']=1
    from .financial_sources import source_request_fields_enabled
    if source_request_fields_enabled(task):
        binding['source_request_fields_version']=1
    state = task.setdefault('reasoning_state', {}).setdefault('acquisition', {
        'version': 1, 'binding': binding, 'decisions': [], 'status': 'ready'})

    def stop(reason):
        state.update(status='stopped', stop_reason=reason)
        remaining = initial_acquisition_coverage(task)
        pending = list(dict.fromkeys(remaining['pending_original_question_ids'] + remaining.get('pending_method_question_ids', [])
                                    + remaining.get('pending_discovery_question_ids', [])
                                    + remaining.get('pending_economic_question_ids', [])
                                    + remaining.get('pending_financial_batch_question_ids', [])))
        engine._gap(task, '初次取证停止：' + reason, pending or [q['id'] for q in task['questions'] if q['required']])
        engine._save_model_checkpoint(task)

    if state.get('binding') != binding:
        stop('导航检查点与本次研究输入不一致，不复用旧决策或重置额度')
        return
    if state.get('status') in ('stopped', 'complete'):
        return
    if any(item.get('status') in ('dispatched', 'acquiring') for item in state['decisions']):
        stop('上次导航已派发但未完成检查点，不重复不确定请求')
        return
    if state['decisions']:
        retained_hash = state['decisions'][-1].get('result_source_hash')
        if retained_hash and retained_hash != fingerprint(original_sources(task)):
            stop('导航留存来源已变化，不复用旧来源检查点')
            return
    while budget_policy is not None or len(state['decisions']) < MAX_DECISIONS:
        coverage = initial_acquisition_coverage(task)
        if coverage['ready']:
            state['status'] = 'complete'
            engine._save_model_checkpoint(task)
            return
        from .financial_sources import progressive_financial_source_ready
        if progressive_financial_source_ready(task, coverage):
            state['progressive_processing'] = dict(version=1,
                readable_question_ids=list(coverage['readable_financial_batch_question_ids']),
                source_coverage_complete=False, financial_approval=False, budget_reset=False)
            stop('已取得部分可读财务原件，先处理本批资料；其他公司、口径及行业完整性仍按实际缺口保留')
            return
        budget_policy=acquisition_navigation_budget(task)
        if budget_policy is not None and not budget_policy['additional_decisions']:
            stages=('提取、计算绑定、分析复核及交付' if budget_policy.get('downstream_protection',{}).get('calculation_binding')
                else '提取、分析复核及交付')
            stop('剩余模型额度需用于'+stages+'，保留已取得资料与实际缺口')
            return
        questions = [q['id'] for q in task['questions'] if q['required']]
        if supplement_request_blocked(task, 'ifind', questions):
            stop('来源已遇到授权权限失败，不继续派发或换源')
            return
        ledger = task['ledger']
        if (not engine._reasoning_available(task)
                or ledger['used']['data_requests'] >= ledger['limits']['data_requests']):
            stop('全局研究预算不足，保留交付预留')
            return
        has_ifind_credentials = any(engine.source_credentials.get('ifind', {}).get(key)
                                    for key in ('access_token', 'refresh_token'))
        public_navigation = task.get('official_channels_version') == 1
        if not has_ifind_credentials and not public_navigation:
            stop('未配置本任务可用的iFinD来源')
            return
        before = navigation_progress(task)
        source_hash = fingerprint(original_sources(task))
        source_contract = data_request_contract(task)
        source_contract = copy.deepcopy(source_contract)
        # Dispatch is authorized by the registered source/operation pair, not
        # an unqualified operation name. This does not enable vendor fallback.
        permitted_sources = {'ifind'}
        if public_navigation:
            permitted_sources.add('official')
            if not has_ifind_credentials:
                permitted_sources.discard('ifind')
        source_contract['tools'] = [tool for tool in source_contract.get('tools', [])
                                   if tool.get('source') in permitted_sources]
        financial_methods = any(set(FINANCIAL_PERIOD_METHODS).intersection(question.get('method_needs', []))
                                for question in task['questions'] if question['required'])
        explicit_query_scope = any(question.get('evidence_need_scopes') for question in task['questions'])
        method_policy = type(task.get('method_source_coverage_version')) is int and task['method_source_coverage_version'] == 1
        pending_methods = coverage.get('pending_method_inputs', []) if method_policy else []
        if (financial_methods and not explicit_query_scope and not method_policy
                and task.get('structured_source_priority_version') != 1):
            # Registered finance endpoints are raw-only: they cannot supply
            # the audited unit/scope required by these methods. Fetch the
            # original directly instead of spending on unusable extraction.
            source_contract = copy.deepcopy(source_contract)
            source_contract['tools'] = [tool for tool in source_contract['tools']
                if tool.get('source') == 'ifind' and tool.get('operation') in
                    ('get_thscode', 'report_query', 'filing_original')]
            source_contract['acquisition_priority'] = {'version': 1,
                'reason': '必要财务方法缺少已核验原值和口径；现有结构化财务接口仅留存原始列，不能提供所需计算输入，直接定位披露原件。',
                'financial_approval': False}
        pending_originals = coverage['pending_original_question_ids']
        if pending_originals:
            if not any(not item['requires_original_path'] for item in pending_methods):
                source_contract['tools'] = original_navigation_tools(task, source_contract['tools'])
            source_contract['acquisition_priority'] = dict(version=2,
                required_original_question_ids=pending_originals, financial_approval=False,
                reason='目录关联的必需问题尚无可读取原件；已有价格接口不能代替原件需求。仅完成实际缺少的来源，不重复已就绪行情。')
        if pending_methods:
            if all(item['requires_original_path'] for item in pending_methods):
                source_contract['tools'] = original_navigation_tools(task, source_contract['tools'])
            source_contract['acquisition_priority'] = dict(version=3, pending_method_inputs=copy.deepcopy(pending_methods),
                financial_approval=False, reason='必要方法仍缺少可用的输入或口径来源。先完成对应来源路径，不能以已有价格代替官方净值或财务口径；原始日期序列不等于可计算输入，不重复已经就绪的行情。')
        source_contract['tools'] = _structured_navigation_tools(task, source_contract['tools'])
        if coverage.get('pending_economic_question_ids'):
            source_contract['acquisition_priority'] = dict(version=4,
                pending_economic_question_ids=coverage['pending_economic_question_ids'],
                financial_approval=False,
                reason='本任务已搜索到EDB目录，但尚未取得对应业务序列。先核对实际候选并取所需窗口；已有其他原件不能代替这一步，不重复目录搜索或新增无关原件。')
        available_requests = {(tool['source'], tool['operation'])
                              for tool in source_contract.get('tools', [])}
        available_operations = {operation for _, operation in available_requests}
        source_contract, navigation_questions = navigation_question_projection(task, source_contract, coverage)
        payload = {'question': task['question'], 'questions': navigation_questions,
            'objects': task['objects'], 'cutoff': task['cutoff'],
            'data_request_contract': source_contract,
            'source_catalog': context_sources(task)['catalog'], 'budget': copy.deepcopy(ledger),
            'navigation': {'decision': len(state['decisions']) + 1,
                'maximum':len(state['decisions'])+budget_policy['additional_decisions'] if budget_policy is not None else MAX_DECISIONS,
                'allowed_operations': sorted(available_operations),
                'previous_requests': [copy.deepcopy(task.get('data_need_specs', []))] +
                    [copy.deepcopy(item.get('data_requests', [])) for item in state['decisions']],
                'rule': '只推进初次来源依赖。名称先精确转换，再按问题选择已登记业务接口；需要原文才查目录和下载原件。不可猜测代码、目录标识或字段。'}}
        if budget_policy is not None:
            payload['navigation']['budget_policy']=copy.deepcopy(budget_policy)
        if type(task.get('method_source_coverage_version')) is int and task['method_source_coverage_version'] == 1:
            payload['initial_source_readiness'] = copy.deepcopy(coverage)
        if type(task.get('financial_batch_followup_version')) is int and task['financial_batch_followup_version'] == 1:
            from .financial_sources import financial_batch_acquisition_state
            payload['received_financial_queries'] = financial_batch_acquisition_state(task)
        if public_navigation:
            payload['navigation']['allowed_requests'] = [dict(source=source, operation=operation)
                for source, operation in sorted(available_requests)]
        if task.get('catalogue_search_navigation_version') == 1:
            payload['negative_catalogue_searches'] = negative_catalogue_searches(task)
            payload['navigation']['empty_search_rule'] = (
                '明确未返回数据仅代表对应请求窗口，不是没有公告或事件。可在原对象、资料限制、截止时点与预算内显式选择不同披露窗口，'
                '不能重复原参数、猜目录标识、换源规避权限或将负面查询当金融证据；'
                +('导航次数受原总预算、实际进展和后续交付所需额度约束。' if budget_policy is not None else '仍最多三次导航决策。'))
            if task.get('research_mode') == 'current':
                payload['navigation']['current_disclosure_window'] = (
                    '未知原件公布日时，用户资料范围及单次查询上限允许的情况下，检索可覆盖所需观测期后至本次研究截止，'
                    '再按实际目录标题与正文所属期筛选；不要把检索机械截为观测日前后或固定十五日。日期仍由本轮提案明确选择。')
        if task.get('initial_acquisition_scope_version') == 1:
            from .reasoning import calculation_input_requirements
            payload['source_readiness'] = coverage
            payload['registered_calculation_inputs'] = calculation_input_requirements(task)
            payload['navigation']['date_rule'] = ('公告目录日期用于定位披露，不等于净值或财报所属日。'
                '按研究模式和截止时点选择实际目录范围，不猜首发时间，不把以后披露的当前版本当作历史当时可得。')
            payload['navigation']['input_priority'] = ('优先定位直接包含实际缺失原值及必要口径的原件。'
                '相关方法说明、估值调整或服务通知不等于已经取得登记计算输入；目录缺少适用原件时可明确调整披露检索窗口，'
                '仍受原研究截止、对象、权限及预算限制，不为下载任意一份文件而停止。')
            if source_contract.get('source_preference'):
                payload['navigation']['input_priority'] = source_contract['source_preference']['rule']
        entry = {'status': 'dispatched', 'batch': task['batch'], 'binding': copy.deepcopy(binding),
            'source_hash': source_hash, 'input_digest': fingerprint(payload)}
        state['decisions'].append(entry)
        engine._save_model_checkpoint(task)
        try:
            proposed, receipt = model_object(engine.model, task, 'acquire', payload, {'data_requests', 'stop_reason'})
            if (not isinstance(proposed, dict) or set(proposed) != {'data_requests', 'stop_reason'}
                    or not isinstance(proposed['stop_reason'], str) or not isinstance(proposed['data_requests'], list)):
                raise ContractError('初次导航输出字段或类型无效')
            if bool(proposed['data_requests']) == bool(proposed['stop_reason'].strip()):
                raise ContractError('导航须选择实际请求或明确停止原因，不能同时提交或均为空')
            entry.update(receipt=receipt, output_hash=fingerprint(proposed))
            if proposed['stop_reason']:
                entry['status'] = 'stopped'
                stop(proposed['stop_reason'])
                return
            needs = proposed['data_requests']
            if any(not isinstance(need, dict) or not isinstance(need.get('source', 'ifind'), str)
                   or not isinstance(need.get('operation'), str) or
                   (need.get('source', 'ifind'), need.get('operation')) not in available_requests for need in needs):
                raise ContractError('初次导航只允许已登记且前置条件满足的来源与操作组合')
            validate_navigation_question_scope(task, needs, coverage, source_contract.get('question_ids', []))
            validate_plan(task, {'questions': question_specs(task), 'data_requests': needs, 'calculations': []})
            if receipt.get('acquisition_rules_version', 1) >= 2:
                needs, mapping = allocate_request_ids(task, state['decisions'], needs)
                entry['request_id_allocation'] = mapping
            else:
                prior_needs = task.get('data_need_specs', []) + [need for decision in state['decisions'][:-1]
                    for need in decision.get('data_requests', [])]
                for need in needs:
                    if any(need.get('id') == prior.get('id') and fingerprint(need) != fingerprint(prior)
                           for prior in prior_needs):
                        raise ContractError('导航请求标识已绑定其他请求，不能覆盖原取证关联')
            engine._validated_data_needs(task, needs)
            if len(needs) > ledger['limits']['data_requests'] - ledger['used']['data_requests']:
                raise ContractError('导航请求超出剩余取数预算')
            entry.update(status='acquiring', data_requests=copy.deepcopy(needs))
            if task.get('acquisition_reading_intent_version')==1:
                entry['reading_intent_hash']=fingerprint(dict(binding=entry['binding'],data_requests=entry['data_requests']))
            engine._save_model_checkpoint(task)
            if type(task.get('progressive_financial_acquisition_version')) is int and task['progressive_financial_acquisition_version'] == 1:
                batch_result = engine._acquire_sources(task, needs, yield_on_readable_financial=True)
                if batch_result and batch_result.get('deferred_request_ids'):
                    entry.update(deferred_request_ids=batch_result['deferred_request_ids'],
                        source_coverage_complete=False)
            else:
                engine._acquire_sources(task, needs)
            engine._active(task['id'], task['batch'])
            after = navigation_progress(task)
            entry.update(status='completed', result_source_hash=fingerprint(original_sources(task)),
                progress=[len(new - old) for old, new in zip(before, after)])
            engine._save_model_checkpoint(task)
            if not any(entry['progress']):
                reason = ('未增加已核验映射、目录候选、可提取原件或明确新检索覆盖，不重复导航'
                    if task.get('catalogue_search_navigation_version') == 1 else
                    '未增加已核验映射、可下载目录候选或可提取原件，不重复导航')
                if task.get('metadata_navigation_progress_version') == 1:
                    reason = '未增加已核验映射、目录候选、元数据、可提取原件或明确新检索覆盖，不重复导航'
                if task.get('financial_response_navigation_version') == 1:
                    reason = '未新增有效业务响应、映射、目录、元数据或可读原件，不重复相同取数'
                stop(reason)
                return
        except ContractError as error:
            engine._active(task['id'], task['batch'])  # Cancellation never becomes a resumable success.
            entry.update(status='rejected', reason=str(error))
            stop('导航未完成：' + str(error))
            return
    if initial_acquisition_coverage(task)['ready']:
        state['status'] = 'complete'
        engine._save_model_checkpoint(task)
    else:
        stop('已达到初次导航三次决策上限，保留实际缺口')
