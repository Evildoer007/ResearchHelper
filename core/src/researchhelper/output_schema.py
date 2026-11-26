"""Versioned research submission formats, not financial approval or inference.

Only an explicitly configured official strict-output endpoint uses this format.
The adapter changes containers, never repairs prose, numbers or references.
"""
import copy
import json
import math
import re

from .contracts import (ContractError, DuplicateJSONField, METHOD_REGISTRY, calculation_quantity_paths, fingerprint,
                        method_reference_quantities, analysis_quantity_paths,
                        tracking_contract, unique_json_object, validate_tracking)

VERSION = 1
FUNCTION_NAME = 'rh_submit_analysis_v1'
ANALYSIS_CITATION_PROTOCOL = 'strict_analysis_citations_v2'
ANALYSIS_CITATION_FUNCTION = 'rh_submit_analysis_v2'
SUPPLEMENT_FUNCTION_NAME = 'rh_submit_material_supplement_v1'
SUPPLEMENT_PROTOCOL = 'strict_material_supplement_v1'
PLAN_FUNCTION_NAME = 'rh_submit_plan_v1'
PLAN_PROTOCOL = 'strict_plan_v1'
PLAN_REQUEST_FUNCTION_NAME = 'rh_submit_plan_v2'
PLAN_REQUEST_PROTOCOL = 'strict_plan_v2'
ACQUISITION_PROTOCOL = 'strict_acquisition_v1'
ACQUISITION_FUNCTION_NAME = 'rh_submit_acquisition_v1'
DRAFT_REPORT_SCHEMA_NAME = 'rh_research_draft_v1'
MATERIAL_EXTRACTION_FUNCTION_NAME = 'rh_submit_material_evidence_v1'
MATERIAL_EXTRACTION_PROTOCOL = 'strict_material_extraction_v1'
FINANCIAL_EXTRACTION_FUNCTION_NAME = 'rh_submit_financial_original_v7'
FINANCIAL_EXTRACTION_PROTOCOL = 'strict_financial_original_v7'
FINANCIAL_REFERENCE_PROTOCOL = 'strict_financial_reference_v1'
FINANCIAL_REFERENCE_FUNCTION_NAME = 'rh_select_financial_references_v1'
ORIGINAL_BATCH_PROTOCOL = 'strict_original_batch_v1'
ORIGINAL_BATCH_FUNCTION_NAME = 'rh_select_original_batch_v1'
ORIGINAL_SPAN_PROTOCOL = 'strict_original_spans_v1'
ORIGINAL_SPAN_FUNCTION_NAME = 'rh_select_original_spans_v1'
FEE_ORIGINAL_PROTOCOL = 'strict_fee_original_v1'
FEE_ORIGINAL_FUNCTION_NAME = 'rh_select_fee_original_v1'
API_EXTRACTION_FUNCTION_NAME = 'rh_submit_api_evidence_v1'
API_EXTRACTION_PROTOCOL = 'strict_api_extraction_v1'
CALCULATION_BINDING_FUNCTION_NAME = 'rh_bind_calculations_v1'
CALCULATION_BINDING_PROTOCOL = 'strict_calculation_binding_v1'
READING_PROTOCOL = 'strict_reading_selection_v1'
READING_FUNCTION_NAME = 'rh_submit_reading_selection_v1'
READING_REFERENCE_PROTOCOL = 'strict_reading_reference_v1'
READING_REFERENCE_FUNCTION_NAME = 'rh_select_reading_references_v1'
MAX_ARGUMENT_CHARACTERS = 256_000
LOCAL_ARRAY_POLICY = '_researchhelper_collection_policy'
SERIES_PARAMETER_BINDING = '_researchhelper_series_parameters'
EMPTY_OBJECT_WIRE = 'rh_empty_object_v1'


def provider_output_schema(schema):
    """Encode only empty containers rejected by the official strict service.

    The canonical research schema stays unchanged. This marker is a container
    representation, not a missing value, default assumption or financial fact.
    """
    result = copy.deepcopy(schema)
    result.pop(LOCAL_ARRAY_POLICY, None)
    result.pop(SERIES_PARAMETER_BINDING, None)
    if result.get('type') == 'object':
        if result.get('properties') == {}:
            return {'type':'string','enum':[EMPTY_OBJECT_WIRE]}
        result['properties'] = {key:provider_output_schema(value)
                                for key,value in result['properties'].items()}
    if isinstance(result.get('items'),dict):
        result['items'] = provider_output_schema(result['items'])
    if isinstance(result.get('anyOf'),list):
        result['anyOf'] = [provider_output_schema(value) for value in result['anyOf']]
    return result


def provider_request_schema(task, schema, protocol):
    """Share exact schema fragments; local validation stays fully expanded.

    Versioned original-source or analysis requests opt in. Generated definitions form
    an internal acyclic graph; no remote references, omitted constraints,
    source trimming or financial approvals are introduced by this encoding.
    """
    if protocol == ORIGINAL_SPAN_PROTOCOL:
        from .original_scope_wire import project
        schema = project(task, schema)
        from .api_context_wire import project as api_context_schema
        schema = api_context_schema(task, schema)
        from .flat_api_selection import project as flat_api_schema
        schema = flat_api_schema(task, schema)
    result = provider_output_schema(schema)
    original_policy = task.get('structured_schema_sharing_version')
    # Named quantity selections carry exact source-local enumerations. Some
    # saved tasks predate the sharing flag, so reuse identical definitions on
    # their wire too. The task and local decoder keep the full constraints.
    named_quantity_wire = ('structured_schema_sharing_version' not in task
                           and type(task.get('literal_quantity_reference_version')) is int
                           and task['literal_quantity_reference_version'] == 5
                           and schema.get('properties', {}).get('version', {}).get('enum') in ([10], [11], [12]))
    original = (protocol == ORIGINAL_SPAN_PROTOCOL and (
        type(original_policy) is int and original_policy == 1 or named_quantity_wire))
    analysis = (protocol == ANALYSIS_CITATION_PROTOCOL
                and type(task.get('analysis_schema_sharing_version')) is int
                and task['analysis_schema_sharing_version'] == 1)
    # New tasks share exact repeated constraints in the other registered
    # request formats too. The canonical schema and decoder are unchanged;
    # an old task does not silently acquire a new transport policy.
    request = (type(task.get('request_schema_sharing_version')) is int
               and task['request_schema_sharing_version'] == 1
               and protocol in {PLAN_PROTOCOL, PLAN_REQUEST_PROTOCOL, ACQUISITION_PROTOCOL,
                   MATERIAL_EXTRACTION_PROTOCOL, FINANCIAL_EXTRACTION_PROTOCOL,
                   FINANCIAL_REFERENCE_PROTOCOL, ORIGINAL_BATCH_PROTOCOL,
                   FEE_ORIGINAL_PROTOCOL, API_EXTRACTION_PROTOCOL,
                   CALCULATION_BINDING_PROTOCOL, READING_PROTOCOL, READING_REFERENCE_PROTOCOL,
                   SUPPLEMENT_PROTOCOL, 'strict_argument_support_v2', 'strict_argument_reference_v1'})
    if not original and not analysis and not request:
        return result
    from collections import Counter
    from .contracts import canonical
    counts = Counter()

    def count(node):
        if isinstance(node,dict):
            if isinstance(node.get('type'),str) or isinstance(node.get('anyOf'),list):
                key = canonical(node)
                if len(key) >= 64:counts[key] += 1
            for value in node.values():count(value)
        elif isinstance(node,list):
            for value in node:count(value)

    count(result)
    repeated = {key for key,n in counts.items() if n > 1 and (n-1)*len(key) > n*36+20}
    names,definitions = {},{}

    def share(node, *, defining=False, union_branch=False):
        if isinstance(node,dict):
            key = canonical(node)
            if not defining and not union_branch and key in repeated:
                if key not in names:
                    name = 's'+str(len(names)+1)
                    names[key] = name
                    definitions[name] = share(node,defining=True)
                return {'$ref':'#/$defs/'+names[key]}
            # The service parses each anyOf branch before resolving refs.
            # Keep those branches inline, including their complete object
            # properties. Refs remain in properties/items, with no redundant
            # type siblings that the service mistakes for empty objects.
            return {key:([share(branch,union_branch=True) for branch in value]
                         if key=='anyOf' and isinstance(value,list) else share(value))
                    for key,value in node.items()}
        if isinstance(node,list):return [share(value) for value in node]
        return node

    compact = share(result)
    if definitions:compact['$defs']=definitions
    return compact if len(canonical(compact)) < len(canonical(result)) else result


def decode_provider_containers(wire, schema):
    """Restore a marker only where the original schema requires exactly{}.

    Legacy canonical submissions still validate. Wrong or ambiguous shapes
    fail unchanged; no keys are deleted and no numeric/text fact is repaired.
    """
    if _matches(wire,schema):
        return copy.deepcopy(wire)
    if schema.get('type') == 'object' and schema.get('properties') == {}:
        return {} if wire == EMPTY_OBJECT_WIRE else copy.deepcopy(wire)
    if 'anyOf' in schema:
        candidates=[item for item in schema['anyOf'] if _matches(wire,provider_output_schema(item))]
        if len(candidates) == 1:
            return decode_provider_containers(wire,candidates[0])
        return copy.deepcopy(wire)
    if schema.get('type') == 'object' and isinstance(wire,dict):
        return {key:decode_provider_containers(value,schema['properties'][key])
                if key in schema['properties'] else copy.deepcopy(value) for key,value in wire.items()}
    if schema.get('type') == 'array' and isinstance(wire,list):
        return [decode_provider_containers(value,schema['items']) for value in wire]
    return copy.deepcopy(wire)


class StructuredOutputError(ContractError):
    """Only fixed diagnostic codes escape the private completion boundary."""
    def __init__(self, category, message, diagnostic=None):
        self.category = category
        self.diagnostic = diagnostic or {}
        super().__init__(message)


def object_schema(properties):
    return {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}


def array_schema(items):
    # DeepSeek strict mode does not support minItems/maxItems. Local validation
    # still enforces resource bounds, in addition to the existing kernel gates.
    return {'type': 'array', 'items': items}


def draft_report_output_schema(task):
    """Describe the report container, leaving judgment and evidence review to the kernel."""
    from .planning import draft_research_scope

    text = {'type': 'string'}
    texts = array_schema(text)
    def nullable(schema):
        return {'anyOf': [schema, {'type': 'null'}]}

    scope = draft_research_scope(task)
    scopes = scope['allowed_report_scopes'] if scope else ['fact', 'comparison', 'mechanism', 'research']
    step = object_schema(dict(label=text, kind={'type': 'string', 'enum': ['observation', 'hypothesis', 'condition']},
                              data_refs=texts))
    tracking = dict(indicator=text, source=text, window=text, condition=text)
    logic = object_schema(dict(title=text, analysis=text,
        argument=nullable(object_schema(dict(claim=text, evidence=text, reasoning=text, condition=text))),
        implication=text, risk=text, data_refs=texts, source_refs=texts, calculation_refs=texts,
        knowledge_refs=array_schema(object_schema(dict(id=text, version={'type': 'integer'}))),
        mechanism=nullable(object_schema(dict(steps=array_schema(step)))),
        tracking={'anyOf': [object_schema(tracking), object_schema(dict(tracking, benchmark=text)), {'type': 'null'}]},
        workpaper_notes=texts))
    return object_schema(dict(title=text, conclusion=text, report_scope=nullable({'type': 'string', 'enum': scopes}),
                              logics=array_schema(logic), risks=texts, reader_notes=texts, workpaper_notes=texts))


def _literal_schema(value):
    """Describe registered constants without filling them into a proposal."""
    if isinstance(value, str):
        return {'type': 'string', 'enum': [value]}
    if type(value) is bool:
        return {'type': 'boolean', 'enum': [value]}
    if type(value) is int:
        return {'type': 'integer', 'enum': [value]}
    if isinstance(value, dict) and value:
        return object_schema({key: _literal_schema(item) for key, item in value.items()})
    raise ContractError('登记常量没有严格输出格式，未派发请求')


def _request_option_schema(options, title_filter=None):
    variants = [object_schema({}) if option == {} else _literal_schema(option) for option in options]
    if title_filter is not None:
        for option in options:
            properties = {name: _literal_schema(value) for name, value in option.items()}
            properties[title_filter['parameter']] = dict(type='string',
                minLength=title_filter['minLength'], maxLength=title_filter['maxLength'])
            variants.append(object_schema(properties))
    return {'anyOf': variants}


def _semantic_request_shapes(tool):
    from .financial_sources import FINANCIAL_QUERY_FIELDS, FINANCIAL_QUERY_PERIODS
    topic = {'type': 'string', 'pattern': r'^[A-Za-z0-9\u4e00-\u9fff]{1,24}$'}
    common = dict(topic=topic, discovery_kind={'type': 'string', 'enum': ['行业', '产业链']})
    periods = dict(year={'type': 'integer', 'minimum': 1000, 'maximum': 9999},
        report_period={'type': 'string', 'enum': list(FINANCIAL_QUERY_PERIODS)},
        fields=dict(array_schema({'type': 'string', 'enum': list(FINANCIAL_QUERY_FIELDS)}),
                    minItems=1, maxItems=len(FINANCIAL_QUERY_FIELDS), uniqueItems=True))
    result = []
    if tool.get('object_discovery'):
        result.append(object_schema(dict(query_kind=_literal_schema('object_discovery'), **common)))
    if tool.get('financial_batch'):
        result.append(object_schema(dict(query_kind=_literal_schema('financial_batch'), **common, **periods)))
    if tool.get('available_codes'):
        result.append(object_schema(dict(query_kind=_literal_schema('financial_single'),
            code={'type': 'string', 'enum': tool['available_codes']}, **periods)))
    return result


def _decimal_page_pattern(maximum):
    """Exact canonical decimal range without enumerating a large directory."""
    if type(maximum) is not int or maximum<0:raise ContractError('目录页码范围无效')
    upper=str(maximum);choices=['0']
    if len(upper)>1:choices.append('[1-9][0-9]{0,'+str(len(upper)-2)+'}')
    prefix=''
    for index,digit in enumerate(upper):
        low=1 if index==0 else 0;high=int(digit)-1;remaining=len(upper)-index-1
        if high>=low:
            part=str(low) if low==high else '['+str(low)+'-'+str(high)+']'
            choices.append(prefix+part+('[0-9]{'+str(remaining)+'}' if remaining else ''))
        prefix+=digit
    if maximum:choices.append(upper)
    return '^(?:'+'|'.join(choices)+')$'


def _planned_request_schemas(task, selected_contract=None):
    from .evidence import data_request_contract
    text = {'type': 'string'}
    choices = []
    contract = selected_contract if selected_contract is not None else data_request_contract(task)
    for tool in contract['tools']:
        if 'metadata_parameter_schema' in tool:
            parameter_shapes = [copy.deepcopy(tool['metadata_parameter_schema'])]
        elif 'page_number_profiles' in tool:
            parameter_shapes=[object_schema(dict(channel=_literal_schema(profile['channel']),
                page=dict(anyOf=[dict(type='integer',minimum=profile['minimum'],maximum=profile['maximum']),
                    dict(type='string',pattern=_decimal_page_pattern(profile['maximum']))])))
                for profile in tool['page_number_profiles']]
        elif 'filing_filter_profiles' in tool:
            parameter_shapes = []
            for profile in tool['filing_filter_profiles']:
                parameters = {key: text for key in tool['parameter_keys']}
                parameters['codes'] = dict(type='string', enum=profile['available_codes'])
                parameters['functionpara'] = _request_option_schema(profile['functionpara_options'],
                    tool.get('optional_title_filter'))
                for key, value in tool.get('fixed_parameters', {}).items():
                    parameters[key] = _literal_schema(value)
                for key in ('beginrDate', 'endrDate'):
                    parameters[key] = dict(type='string', pattern=r'^\d{4}-\d{2}-\d{2}$')
                parameter_shapes.append(object_schema(parameters))
        elif 'index_profiles' in tool:
            parameter_shapes = [object_schema({
                **{key:_literal_schema(value) for key,value in tool['fixed_parameters'].items()},
                'functionpara':object_schema({'date':dict(type='string',pattern=r'^\d{8}$'),
                    'blockname':_literal_schema(profile['blockname']),
                    'iv_type':_literal_schema('allcontract')})}) for profile in tool['index_profiles']]
        elif 'official_index_file_choices' in tool:
            parameter_shapes=[object_schema(dict(catalog_id=_literal_schema(choice['catalog_id']),
                entry_id=_literal_schema(choice['entry_id']),as_of={'anyOf':[
                    {'type':'null'},{'type':'string','pattern':r'^\d{4}-\d{2}-\d{2}$'}]}))
                for choice in tool['official_index_file_choices']]
        elif 'available_parameter_sets' in tool:
            parameter_shapes = [object_schema({key: _literal_schema(value) for key, value in values.items()})
                                for values in tool['available_parameter_sets']]
        elif 'family_shapes' in tool:
            parameter_shapes = []
            for profile in tool['family_shapes']:
                parameters = {key: text for key in tool['parameter_keys']}
                codes = profile['available_codes']
                parameters['codes'] = ({'type':'string','enum':codes} if codes else
                                       {'type':'string','pattern':profile['code_pattern']})
                if codes and 'batch_limits' in profile:
                    maximum = profile['batch_limits']['max_codes']
                    options = '|'.join(map(re.escape, codes))
                    parameters['codes'] = dict(type='string',
                        pattern='^(?:'+options+')(?:,(?:'+options+')){0,'+str(maximum-1)+'}$')
                for key,value in profile['fixed_parameters'].items():
                    parameters[key] = _literal_schema(value)
                parameters['indipara'] = array_schema({'anyOf':[object_schema({
                    'indicator':{'type':'string','enum':[name]}, 'indiparams':array_schema(text)})
                    for name in profile['field_parameters']]})
                parameter_shapes.append(object_schema(parameters))
        elif 'query_modes' in tool:
            if task.get('planning_request_encoding') == 2:
                from .evidence import IDENTITY_EXACT_PROFILE
                query_value = ({'type':'string','enum':tool['query_value_options']}
                    if tool.get('query_value_options') else text)
                parameter_shapes = [object_schema({
                    'query_mode': {'type':'string','enum':tool['query_modes']},
                    'query_value': query_value,
                    'profile_id': {'type':'string','enum':[IDENTITY_EXACT_PROFILE]}})]
                if 'query_value_options' in tool and not tool['query_value_options']:
                    parameter_shapes = []
                if 'candidate_profile' in tool:
                    profile=tool['candidate_profile']
                    parameter_shapes.append(object_schema({
                        'query_mode':{'type':'string','enum':profile['query_modes']},
                        'query_value':text,'profile_id':{'type':'string','enum':[profile['profile_id']]}}))
            else:
                parameter_shapes = [object_schema({mode: text, 'functionpara': _literal_schema(
                    tool['fixed_parameters_by_mode'][mode])}) for mode in tool['query_modes']]
        else:
            parameters = {key: text for key in tool['parameter_keys']}
            for key, options in tool.get('parameter_options', {}).items():
                parameters[key] = _request_option_schema(options,
                    tool.get('optional_title_filter') if key == 'functionpara' else None)
            for key, value in tool.get('fixed_parameters', {}).items():
                parameters[key] = _literal_schema(value)
            if selected_contract is not None:
                for key in ('startdate', 'enddate', 'beginrDate', 'endrDate'):
                    if key in parameters:
                        parameters[key] = {'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'}
                if 'codes' in parameters:
                    codes = contract.get('callable_codes', [])
                    if not codes:
                        continue
                    parameters['codes'] = ({'type': 'string', 'pattern': '^(?:' + '|'.join(map(re.escape, codes))
                        + ')(?:,(?:' + '|'.join(map(re.escape, codes)) + '))*$'}
                        if tool['operation'] == 'cmd_history_quotation' else {'type': 'string', 'enum': codes})
            if ((selected_contract is not None or task.get('financial_batch_query_version')==1) and tool['source'] == 'ifind'
                    and tool['operation'] == 'smart_stock_picking'):
                from .financial_sources import FINANCIAL_QUERY_FIELDS, FINANCIAL_QUERY_PERIODS
                patterns = []
                codes = tool.get('available_codes', [])
                if codes:
                    patterns.append({'type': 'string', 'pattern': '^(?:' + '|'.join(map(re.escape, codes))
                        + r')\d{4}年(?:' + '|'.join(map(re.escape, FINANCIAL_QUERY_PERIODS)) + ')(?:'
                        + '|'.join(map(re.escape, FINANCIAL_QUERY_FIELDS)) + ')(?:、(?:'
                        + '|'.join(map(re.escape, FINANCIAL_QUERY_FIELDS)) + ')){0,4}$'})
                if tool.get('object_discovery'):
                    patterns.append({'type': 'string', 'pattern': r'^[A-Za-z0-9\u4e00-\u9fff]{1,24}(?:行业|产业链)股票$'})
                if tool.get('financial_batch'):
                    patterns.append({'type':'string','pattern':tool['financial_batch']['searchstring_pattern']})
                if not patterns:
                    continue
                parameters['searchstring'] = {'anyOf': patterns}
            if 'indipara' in parameters:
                parameters['indipara'] = array_schema({'anyOf': [object_schema({
                    'indicator': {'type': 'string', 'enum': [name]},
                    'indiparams': array_schema(text)}) for name in tool['field_parameters']]})
            parameter_shapes = [object_schema(parameters)]
        if tool['source'] == 'ifind' and tool['operation'] == 'smart_stock_picking':
            from .financial_sources import semantic_query_enabled
            if semantic_query_enabled(task):
                parameter_shapes = _semantic_request_shapes(tool)
        for parameters in parameter_shapes:
            policy = task.get('series_parameter_selection_version')
            if policy is not None and (type(policy) is not int or policy != 1):
                raise ContractError('日期序列固定参数选择版本未登记')
            if policy == 1 and tool['source'] == 'ifind' and tool['operation'] == 'date_sequence':
                from .market_series import SERIES_ROUTING_VERSION
                binding = task.get('series_parameter_registry', {})
                if (not isinstance(binding, dict) or set(binding) != {'version', 'fields'}
                        or type(binding['version']) is not int or binding['version'] != SERIES_ROUTING_VERSION
                        or not isinstance(binding['fields'], dict)):
                    raise ContractError('日期序列参数目录未锁定或版本已变化，未派发请求')
                registered = binding['fields']
                selection = parameters['properties']['indipara']
                fields = {}
                for branch in selection['items']['anyOf']:
                    name = branch['properties']['indicator']['enum'][0]
                    if name not in registered or not isinstance(registered[name], list) or not all(isinstance(value, str) for value in registered[name]):
                        raise ContractError('日期序列字段不在本任务锁定目录，不猜参数')
                    fields[name] = copy.deepcopy(registered[name])
                    branch['properties'].pop('indiparams')
                    branch['required'].remove('indiparams')
                selection.update(minItems=1, maxItems=len(fields), uniqueItems=True)
                selection[SERIES_PARAMETER_BINDING] = dict(version=1, fields=fields)
            references = array_schema(_identifier_schema())
            if selected_contract is not None:
                ids = contract.get('question_ids', [])
                references = (dict(array_schema({'type': 'string', 'enum': ids}), minItems=1)
                              if ids else dict(array_schema(_identifier_schema()), maxItems=0))
            fields={'id': _identifier_schema(),
                'source': {'type': 'string', 'enum': [tool['source']]},
                'operation': {'type': 'string', 'enum': [tool['operation']]},
                'parameters': parameters, 'question_ids': references,
                'selection_reason': text}
            if (task.get('catalogue_alternative_version')==1
                    and (tool['source'],tool['operation'])==('ifind','report_query')):
                from .financial_sources import source_request_fields_enabled
                if not source_request_fields_enabled(task) or contract.get('catalogue_alternatives'):
                    fields['catalogue_alternative_ref']={'anyOf':[{'type':'null'},
                        {'type':'string','enum':[choice['reference'] for choice in contract.get('catalogue_alternatives',[])]}]
                        } if contract.get('catalogue_alternatives') else {'type':'null'}
            if (type(task.get('acquisition_reading_intent_version')) is int
                    and task['acquisition_reading_intent_version']==1
                    and (tool['source'],tool['operation']) in (('ifind','filing_original'),('official','release_original'))):
                fields['reading_queries']=dict(array_schema(dict(type='string',minLength=1,maxLength=80)),
                    maxItems=8,uniqueItems=True)
            choice = object_schema(fields)
            if tool.get('request_description'):
                choice['description'] = tool['request_description']
            observation = tool.get('observation_selection')
            if observation is not None:
                # Date ranges keep their original wire form. A latest query
                # selects a task-bound window instead of inventing two dates.
                latest = copy.deepcopy(choice)
                fields = latest['properties']['parameters']['properties']
                required = latest['properties']['parameters']['required']
                for name in ('startdate','enddate'):
                    fields.pop(name);required.remove(name)
                fields['observation_window'] = object_schema(dict(
                    mode=dict(type='string',enum=['latest_available']),
                    as_of=dict(type='string',enum=[observation['as_of']]),
                    lookback_calendar_days=dict(type='integer',enum=observation['lookback_calendar_days'])))
                required.append('observation_window')
                latest['description'] = observation['rule']
                choices.append(latest)
            choices.append(choice)
    return choices


def acquisition_output_schema(task, description):
    """Only the current filtered tools and question scope, never the full registry."""
    contract = description['data_request_contract']
    choices = _planned_request_schemas(task, contract)
    requests = array_schema({'anyOf': choices}) if choices else dict(array_schema(object_schema({})), maxItems=0)
    binding = fingerprint(dict(task_id=task['id'], version=task['version'], batch=task['batch'],
        input_hash=task['input_hash'], contract=contract))[:16]
    result = object_schema(dict(version={'type': 'integer', 'enum': [1]},
        binding_ref={'type': 'string', 'enum': [binding]}, data_requests=requests,
        stop_reason={'type': 'string'}))
    result['description'] = _identity_profile_binding(task.get('identity_candidate_lookup_version') in (1,2))
    return result


def _decode_identity_requests(requests, schema):
    from .evidence import identity_query_parameters, IDENTITY_EXACT_PROFILE, IDENTITY_CANDIDATE_PROFILE
    candidate_profile = schema.get('description') == _identity_profile_binding(True)
    if not candidate_profile and schema.get('description') != _identity_profile_binding():
        raise StructuredOutputError('stale_identity_profile', '身份查询参数登记版本已变化，未采用旧规划')
    for request in requests:
        if request['source'] == 'ifind' and request['operation'] == 'get_thscode':
            parameters = request['parameters']
            allowed = (IDENTITY_EXACT_PROFILE, IDENTITY_CANDIDATE_PROFILE) if candidate_profile else (IDENTITY_EXACT_PROFILE,)
            if parameters['profile_id'] not in allowed:
                raise StructuredOutputError('unknown_identity_profile', '身份查询未选择已登记精确匹配配置')
            request['parameters'] = identity_query_parameters(parameters['query_mode'], parameters['query_value'],parameters['profile_id'])


def _decode_semantic_requests(requests):
    from .financial_sources import semantic_query_parameters
    for request in requests:
        if (request['source'] == 'ifind' and request['operation'] == 'smart_stock_picking'
                and 'query_kind' in request['parameters']):
            request['parameters'] = semantic_query_parameters(request['parameters'])
        if (request['source']=='official' and request['operation']=='release_catalog'
                and type(request['parameters'].get('page')) is int):
            request['parameters']=dict(request['parameters'],page=str(request['parameters']['page']))


def _decode_series_parameter_selections(requests, schema):
    """Compile omitted constants from the exact local schema, never repair output."""
    branches = schema['properties']['data_requests']['items'].get('anyOf', [])
    for request in requests:
        if request.get('source') != 'ifind' or request.get('operation') != 'date_sequence': continue
        matches = []
        for branch in branches:
            binding = branch.get('properties', {}).get('parameters', {}).get('properties', {}).get('indipara', {}).get(SERIES_PARAMETER_BINDING)
            if binding is not None and _matches(request, branch): matches.append(binding)
        if not matches: continue  # Old tasks keep their explicit parameter contract.
        if len(matches) != 1 or matches[0].get('version') != 1:
            raise StructuredOutputError('invalid_series_parameter_binding', '日期序列固定参数绑定不唯一，未执行')
        values = matches[0]['fields']
        for item in request['parameters']['indipara']:
            if set(item) != {'indicator'} or item['indicator'] not in values:
                raise StructuredOutputError('invalid_series_parameter_binding', '日期序列只选择本次登记字段，不修复额外参数')
            item['indiparams'] = copy.deepcopy(values[item['indicator']])


def decode_acquisition_output(wire, schema, description):
    if not _matches(wire, schema):
        raise StructuredOutputError('schema_mismatch', '导航请求不符合本轮工具和问题范围，未采用提案',
                                    schema_diagnostic(wire, schema))
    result = {name: copy.deepcopy(wire[name]) for name in ('data_requests', 'stop_reason')}
    if bool(result['data_requests']) == bool(result['stop_reason'].strip()) or len(result['data_requests']) > 50:
        raise StructuredOutputError('invalid_acquisition_decision', '导航须明确请求或停止，不执行矛盾提案')
    _decode_series_parameter_selections(result['data_requests'], schema)
    _decode_identity_requests(result['data_requests'], schema)
    _decode_semantic_requests(result['data_requests'])
    from .market_series import compile_observation_window
    for index,(original,request) in enumerate(zip(wire['data_requests'],result['data_requests'])):
        if 'observation_window' not in original.get('parameters',{}):continue
        branches=schema['properties']['data_requests']['items']['anyOf']
        matches=[branch for branch in branches if _matches(original,branch)]
        if len(matches)!=1:raise StructuredOutputError('ambiguous_observation_window','最近观测窗口未唯一绑定')
        window=matches[0]['properties']['parameters']['properties']['observation_window']['properties']
        selection=dict(version=1,as_of=window['as_of']['enum'][0],lookback_calendar_days=window['lookback_calendar_days']['enum'])
        result['data_requests'][index]=compile_observation_window(request,selection)
    return result


def _identifier_schema():
    return {'type': 'string', 'pattern': '^[A-Za-z][A-Za-z0-9_]{0,63}$'}


def material_extraction_output_schema(task, views):
    """Bind containers to actual readings, not facts or timestamps.

    API, required calculation and document-only work keeps its full protocol;
    no obligation is removed to fit a narrower submission container.
    """
    if (task.get('calculation_specs') or any(q.get('method_needs') for q in task.get('questions', []))):
        return None
    return _original_extraction_schema(task, views)


def _fee_original_choices(task, views):
    """Describe read originals and mandatory fee context, never fee approval."""
    from .contracts import DOCUMENT_CONTENT_INTENT
    from .reasoning import evidence_contract
    from .financial_sources import original_line_index, original_date_anchors
    methods={m for question in task.get('questions',[]) for m in question.get('method_needs',[])}
    methods.update(item.get('method') for item in task.get('calculation_specs',[]))
    if (type(task.get('fee_original_selection_version')) is not int
            or task['fee_original_selection_version'] != 1
            or task.get('intent') == DOCUMENT_CONTENT_INTENT
            or not task.get('argument_support_contract') or not methods
            or not methods.issubset({'management_custody_fee_comparison_v1','management_custody_fee_summary_v1'})
            or not isinstance(views,list) or not views or len(views)>12
            or any(not isinstance(view,dict) or view.get('kind') not in ('material','filing') for view in views)):
        return None
    contract=evidence_contract(task,views)
    if contract['source_bindings_omitted'] or not contract['question_ids']:
        return None
    choices=[]
    for view in views:
        source=next((item for item in contract['sources'] if item['artifact_id']==view['id']),None)
        lines,dates=original_line_index(view),original_date_anchors(view)
        if not source or not lines or not dates or not source.get('current_availability_reference'):
            return None
        choices.append(dict(source_ref='source_'+str(len(choices)+1),source=source,
            view=view,lines=lines,dates=dates))
    return choices


def _fee_original_v1_schema(task, views):
    """Explicit same-source ranges plus all six registered fee-context fields."""
    from .contracts import context, fingerprint
    choices=_fee_original_choices(task,views)
    if choices is None:return None
    from .reasoning import evidence_contract
    contract=evidence_contract(task,views)
    text={'type':'string'}
    contexts={field:text for field in METHOD_REGISTRY['management_custody_fee_summary_v1']['binding_context']}
    contexts.update(fee_basis={'type':'string','enum':['net_assets']},
        rate_period={'type':'string','enum':['annual']},
        metric={'type':'string','enum':['management_fee','custody_fee']})
    question={'type':'string','enum':contract['question_ids']}
    needs=[object_schema(dict(question_id={'type':'string','enum':[ident]},
        need_ids=array_schema({'type':'string','enum':[item['id'] for item in items]})))
        for ident,items in contract['question_requirements'].items() if items]
    if not needs:return None
    sources=[]
    for choice in choices:
        index={'type':'integer','minimum':0,'maximum':len(choice['lines'])-1}
        span=object_schema(dict(start=index,end=index))
        bound=choice['source'].get('object_binding')
        selected_object={'type':'string','enum':[bound['value']]} if bound else text
        rate=object_schema(dict(id=_identifier_schema(),object=selected_object,
            raw_token={'type':'string','pattern':r'^\d+(?:\.\d+)?$'},
            raw_unit={'type':'string','enum':['%','%/年']},span=span,
            observation_anchor={'type':'integer','enum':list(range(len(choice['dates'])))},
            question_ids=array_schema(question),need_links=array_schema({'anyOf':needs}),
            calculation_context=object_schema(contexts),
            context_spans=object_schema({field:span for field in contexts})))
        sources.append(object_schema(dict(source_ref={'type':'string','enum':[choice['source_ref']]},
            rates=array_schema(rate))))
    binding=fingerprint(dict(task=context(task),methods=sorted({m for q in task.get('questions',[])
        for m in q.get('method_needs',[])}),requirements=contract['question_requirements'],
        sources=[dict(source=item['source'],view=item['view']) for item in choices]))[:24]
    return object_schema(dict(version={'type':'integer','enum':[1]},
        binding_ref={'type':'string','enum':[binding]},sources=array_schema({'anyOf':sources})))


def _fee_candidate_choices(task, views):
    """Recognized literal addresses, never default facts or evidence approval."""
    if type(task.get('fee_candidate_encoding')) is not int or task['fee_candidate_encoding']!=1:
        return None
    choices=_fee_original_choices(task,views)
    if choices is None:return None
    from .fee_sources import original_fee_choices
    policy=task.get('original_duration_anchor_version')
    literal_duration=type(policy) is int and policy==1
    result=[]
    for choice in choices:
        bound=choice['source'].get('object_binding')
        if (not isinstance(bound,dict) or not isinstance(bound.get('value'),str)
                or not re.fullmatch(r'\d{6}\.(?:SH|SZ)',bound['value'])):
            return None
        candidates=original_fee_choices(choice['view'],object_code=bound['value'].split('.')[0],
            literal_duration=literal_duration)
        if not candidates['eligible']:return None
        result.append(dict(**choice,candidates=candidates))
    return result


def fee_original_output_schema(task, views):
    """New tasks select literal refs; unrecognized/old forms keep v1 obligations."""
    choices=_fee_candidate_choices(task,views)
    if choices is None:return _fee_original_v1_schema(task,views)
    from .contracts import context,fingerprint
    from .reasoning import evidence_contract
    contract=evidence_contract(task,views)
    needs=[object_schema(dict(question_id={'type':'string','enum':[ident]},
        need_ids=array_schema({'type':'string','enum':[item['id'] for item in items]})))
        for ident,items in contract['question_requirements'].items() if items]
    if not needs:return None
    sources=[]
    for choice in choices:
        candidates=choice['candidates']
        refs={field:{'type':'string','enum':[item['reference'] for item in candidates[kind]]}
            for field,kind in (('rate_reference','rates'),('fund_reference','funds'),
                              ('share_reference','shares'),('period_reference','periods'))}
        rate=object_schema(dict(id=_identifier_schema(),
            object={'type':'string','enum':[choice['source']['object_binding']['value']]},
            **refs,question_ids=array_schema({'type':'string','enum':contract['question_ids']}),
            need_links=array_schema({'anyOf':needs})))
        sources.append(object_schema(dict(source_ref={'type':'string','enum':[choice['source_ref']]},
            rates=array_schema(rate))))
    binding=fingerprint(dict(version=2,policy=task['fee_candidate_encoding'],task=context(task),
        requirements=contract['question_requirements'],sources=[dict(source=item['source'],
            view=item['view'],candidates=item['candidates']) for item in choices]))[:24]
    return object_schema(dict(version={'type':'integer','enum':[2]},
        binding_ref={'type':'string','enum':[binding]},sources=array_schema({'anyOf':sources})))


def fee_original_prompt(payload, task):
    """Show source characters once with finite line and date addresses."""
    from .reasoning import evidence_contract
    choices=_fee_original_choices(task,payload.get('untrusted_sources'))
    schema=fee_original_output_schema(task,payload.get('untrusted_sources'))
    if choices is None or schema is None:
        raise ContractError('本次费用原件不适用完整口径选择，不删除方法义务')
    result=copy.deepcopy(payload)
    result['untrusted_sources']=[]
    result['evidence_contract']=evidence_contract(task,payload['untrusted_sources'])
    result['evidence_contract']['sources']=[]
    result['fee_binding_ref']=schema['properties']['binding_ref']['enum'][0]
    result['source_choices']=[]
    for choice in choices:
        result['source_choices'].append(dict(source_ref=choice['source_ref'],
            object_binding=choice['source'].get('object_binding'),
            question_requirements=result['evidence_contract']['question_requirements'],
            observation_anchors=[dict(index=i,**date) for i,date in enumerate(choice['dates'])]))
        fragments=[]
        for index,part in enumerate(choice['view']['parts']):
            fragments.append(dict(locator=part['locator'],indexed_lines=[
                [line['index'],line['text']] for line in choice['lines'] if line['fragment_index']==index]))
        result['untrusted_sources'].append(dict(source_ref=choice['source_ref'],parts=fragments))
    if schema['properties']['version']['enum']==[2]:
        for shown,choice in zip(result['source_choices'],_fee_candidate_choices(task,payload['untrusted_sources'])):
            shown['fee_candidates']=copy.deepcopy(choice['candidates'])
        result['fee_candidate_policy']=dict(version=1,wire_version=2,
            literal_refs_are_approval=False,missing_choices_are_proof_of_absence=False,
            listed_share_rule='交易代码仅按明确标签识别；基金简称候选仅表示原件基金整体范围，不证明A/C等份额类别。',
            period_rule='所选明确报告期间的结束日是观测日，不使用公告日或下载日。')
    return result


def _encode_fee_duration(task, spec):
    """Encode an explicitly selected literal duration, without changing facts.

    The fee protocol historically used the printed period label. New tasks also
    require a typed interval. Bind that representation to the same selected
    characters and endpoint; never infer a half-year from a title or a date.
    Invalid/incomplete selections remain unchanged for ordinary validation.
    """
    if task.get('original_duration_anchor_version') != 1:
        return
    from .financial_sources import original_interval_spans
    basis = spec.get('reporting_period_basis', {})
    locator = spec.get('record_locator', {})
    period = spec.get('period')
    if (not isinstance(period, str) or basis.get('part') != locator.get('period_part')
            or spec.get('calculation_context', {}).get('period') != period
            or spec.get('calculation_context_basis', {}).get('period') != basis):
        return
    selected = re.sub(r'\s+', '', period)
    matches = [span for span in original_interval_spans(basis.get('quote', ''))
        if re.sub(r'\s+', '', span['quote']) == selected
        and span['period'].split('/')[-1] == spec.get('observation_date')
        and isinstance(locator.get('period_quote'), str)
        and locator['period_quote'] in span['quote']]
    if len(matches) != 1:
        return
    span = matches[0]
    encoded_basis = dict(basis, version=3, quote=span['quote'])
    spec['period'] = span['period']
    spec['calculation_context']['period'] = span['period']
    spec['reporting_period_basis'] = encoded_basis
    spec['calculation_context_basis']['period'] = copy.deepcopy(encoded_basis)


def _decode_fee_original_v1(wire, schema, context):
    """Restore explicitly selected characters; all ordinary gates still run."""
    from decimal import Decimal, InvalidOperation
    from .financial_sources import selected_original_span
    if not isinstance(context,tuple) or len(context)!=2:
        raise StructuredOutputError('missing_fee_context','费用选择缺少实际任务与读取原件')
    task,views=context
    expected=_fee_original_v1_schema(task,views)
    if expected is None or schema!=expected or not _matches(wire,schema):
        raise StructuredOutputError('schema_mismatch','费用选择不符合本次原件、对象或完整口径，不修复提案')
    from .reasoning import evidence_contract
    contract=evidence_contract(task,views)
    choices={item['source_ref']:item for item in _fee_original_choices(task,views)}
    if len(wire['sources'])>len(choices):
        raise StructuredOutputError('duplicate_fee_source','费用来源重复或超过本次读取范围')
    seen_sources,seen_ids,specs=set(),set(),[]
    for selected in wire['sources']:
        ref=selected['source_ref']
        if ref in seen_sources:
            raise StructuredOutputError('duplicate_fee_source','同一费用原件不能重复提交')
        seen_sources.add(ref);choice=choices[ref];source=choice['source']
        if len(selected['rates'])>64 or len(specs)+len(selected['rates'])>128:
            raise StructuredOutputError('fee_selection_limit','费用原值提案超出本轮上限')
        for rate in selected['rates']:
            if rate['id'] in seen_ids:
                raise StructuredOutputError('duplicate_fee_id','费用证据标识须全局唯一')
            seen_ids.add(rate['id'])
            if not rate['question_ids'] or len(set(rate['question_ids']))!=len(rate['question_ids']):
                raise StructuredOutputError('invalid_fee_question','费用证据须显式关联不重复问题')
            mapped={}
            for pair in rate['need_links']:
                ident,needs=pair['question_id'],pair['need_ids']
                if (ident not in rate['question_ids'] or ident in mapped or not needs
                        or len(set(needs))!=len(needs)):
                    raise StructuredOutputError('invalid_need_mapping','费用需求关联为空、重复或不属于本证据问题')
                lookup={item['id']:item['text'] for item in contract['question_requirements'][ident]}
                mapped[ident]=[lookup[need] for need in needs]
            if set(mapped)!=set(rate['question_ids']):
                raise StructuredOutputError('invalid_need_mapping','费用证据每个问题须有明确需求关联')
            quote=selected_original_span(choice['lines'],rate['span'])
            bases={field:dict(kind='material',artifact_id=source['artifact_id'],
                snapshot_hash=source['snapshot_hash'],**selected_original_span(choice['lines'],span))
                for field,span in rate['context_spans'].items()}
            date=choice['dates'][rate['observation_anchor']]
            try:number=float(Decimal(rate['raw_token']))
            except (ValueError,InvalidOperation,OverflowError):
                raise StructuredOutputError('invalid_fee_number','费用原文数字无效，不采用提案') from None
            if not math.isfinite(number):
                raise StructuredOutputError('invalid_fee_number','费用原文数字不是有限数值')
            specs.append(dict(id=rate['id'],artifact_id=source['artifact_id'],object=rate['object'],
                nature='合同政策条款',text=quote['quote'],record_locator=dict(part=quote['part'],
                    period_part=date['part'],period_quote=date['quote']),
                observation_date=date['date'],period=rate['calculation_context']['period'],
                availability=copy.deepcopy(source['current_availability_reference']),published_at=None,
                publication=dict(at=None,basis_ref=None,precision='未知',timezone='未知',certainty='未知',
                    basis='首次公开时间未知，当前可得性另由实际取数账本核验'),
                raw_token=rate['raw_token'],raw_value=number,value=number,
                raw_unit=rate['raw_unit'],unit=rate['raw_unit'],conversion_factor=1,
                calculation_context=copy.deepcopy(rate['calculation_context']),calculation_context_basis=bases,
                reporting_period_basis=copy.deepcopy(bases['period']),
                question_ids=copy.deepcopy(rate['question_ids']),review=dict(
                    question_ids=copy.deepcopy(rate['question_ids']),allowed_natures=['合同政策条款'],
                    conditions=['仅比较实际原件披露的对应份额、适用期间及两项名义年费率，不代表全部成本或收益。',
                        '原文、原值、日期、逐字段依据及金融适用性仍须核验，不证明历史可得或事件因果。'],
                    reason='显式选择实际读取的费率原文与完整计费口径，提交核验而非批准。',
                    temporal_use='current_version',evidence_need_texts=mapped)))
            _encode_fee_duration(task, specs[-1])
    return dict(evidence_specs=specs)


def decode_fee_original_output(wire, schema, context):
    """Expand selected local refs, then retain every incumbent source gate."""
    if not isinstance(context,tuple) or len(context)!=2:
        raise StructuredOutputError('missing_fee_context','费用选择缺少实际任务与读取原件')
    task,views=context
    expected=fee_original_output_schema(task,views)
    if expected is None or schema!=expected or not _matches(wire,schema):
        raise StructuredOutputError('schema_mismatch','费用选择不属于本次原件、版本或登记候选')
    if schema['properties']['version']['enum']==[1]:
        return _decode_fee_original_v1(wire,schema,context)
    choices={item['source_ref']:item for item in _fee_candidate_choices(task,views)}
    legacy=_fee_original_v1_schema(task,views)
    expanded=dict(version=1,binding_ref=legacy['properties']['binding_ref']['enum'][0],sources=[])
    for source in wire['sources']:
        choice=choices[source['source_ref']];candidates=choice['candidates'];rates=[]
        for item in source['rates']:
            selected={kind:next(value for value in candidates[kind] if value['reference']==item[field])
                for field,kind in (('rate_reference','rates'),('fund_reference','funds'),
                                  ('share_reference','shares'),('period_reference','periods'))}
            rate,fund,share,period=(selected[kind] for kind in ('rates','funds','shares','periods'))
            contexts=dict(fund_id=fund['value'],share_class=share['value'],period=period['value'],
                fee_basis='net_assets',rate_period='annual',metric=rate['metric'])
            spans=dict(fund_id=fund['span'],share_class=share['span'],period=period['span'],
                fee_basis=rate['span'],rate_period=rate['span'],metric=rate['span'])
            rates.append(dict(id=item['id'],object=item['object'],raw_token=rate['raw_token'],
                raw_unit=rate['raw_unit'],span=rate['span'],observation_anchor=period['observation_anchor'],
                question_ids=copy.deepcopy(item['question_ids']),need_links=copy.deepcopy(item['need_links']),
                calculation_context=contexts,context_spans=copy.deepcopy(spans)))
        expanded['sources'].append(dict(source_ref=source['source_ref'],rates=rates))
    decoded=_decode_fee_original_v1(expanded,legacy,context)
    index=0
    for source in wire['sources']:
        for item in source['rates']:
            share=next(value for value in choices[source['source_ref']]['candidates']['shares']
                if value['reference']==item['share_reference'])
            if share['role']=='reported_fund_scope_not_share_class':
                decoded['evidence_specs'][index]['review']['conditions'].append(share['limitation'])
            index+=1
    return decoded


def financial_extraction_v2_schema(task, views):
    """Explicit financial cell choices, with all original context obligations."""
    from .contracts import FINANCIAL_PERIOD_METHODS
    methods = {m for q in task.get('questions', []) for m in q.get('method_needs', [])}
    methods.update(c.get('method') for c in task.get('calculation_specs', []))
    if not methods or not methods.issubset(FINANCIAL_PERIOD_METHODS):
        return None
    original = _original_extraction_schema(task, views, financial=True)
    if original is None or len(views) != 1:
        return None
    # Use two fixed record types instead of overlapping anyOf alternatives.
    # Other publication/source combinations retain the full incumbent route.
    choices = original['properties']['evidence_specs']['items']['anyOf']
    financial = [item for item in choices if 'financial_value_reference' in item['properties']
                 and 'availability' in item['properties']]
    quotes = [item for item in choices if 'text' in item['properties']
              and 'availability' in item['properties'] and 'observation_date' in item['properties']]
    if not financial or not quotes:
        return None
    numeric = copy.deepcopy(financial[0])
    quote = copy.deepcopy(quotes[0])
    locators = quote['properties']['record_locator']['anyOf']
    quote['properties']['record_locator'] = copy.deepcopy(locators[0])
    from .financial_sources import original_date_anchors
    anchors=original_date_anchors(views[0])
    if not anchors:
        return None
    date={'type':'string','enum':sorted({anchor['date'] for anchor in anchors})}
    period_part={'type':'string','enum':list(dict.fromkeys(anchor['part'] for anchor in anchors))}
    period_quote={'type':'string','enum':list(dict.fromkeys(anchor['quote'] for anchor in anchors))}
    for item in (numeric,quote):
        item['properties']['observation_date']=copy.deepcopy(date)
        locator=item['properties']['record_locator']['properties']
        locator.update(period_part=copy.deepcopy(period_part),period_quote=copy.deepcopy(period_quote))
    return object_schema({'version':{'type':'integer','enum':[2]},
        'financial_cells':array_schema(numeric),'text_quotes':array_schema(quote)})


def _financial_extraction_contract(task, views):
    from .contracts import FINANCIAL_PERIOD_METHODS, DOCUMENT_CONTENT_INTENT
    from .reasoning import evidence_contract
    methods={m for q in task.get('questions',[]) for m in q.get('method_needs',[])}
    methods.update(c.get('method') for c in task.get('calculation_specs',[]))
    if (not methods or not methods.issubset(FINANCIAL_PERIOD_METHODS)
            or task.get('intent')==DOCUMENT_CONTENT_INTENT or not isinstance(views,list)
            or len(views)!=1 or views[0].get('kind') not in ('material','filing')):
        return None
    return evidence_contract(task, views)


def financial_extraction_v5_schema(task, views):
    """A fixed source-selector protocol: no retyped quotations or amounts."""
    return _financial_extraction_v5_schema(task, views, _financial_extraction_contract(task, views))


def _financial_extraction_v5_schema(task, views, contract):
    from .financial_sources import original_line_index, original_date_anchors
    if (contract is None or contract['source_bindings_omitted']
            or len(contract['sources']) != 1 or not contract['question_ids']):
        return None
    source=contract['sources'][0]
    availability=source.get('current_availability_reference')
    cells=source.get('financial_table_cells',{}).get('cells',[])
    dates=original_date_anchors(views[0]);lines=original_line_index(views[0])
    if not cells or not dates or not lines or not availability or availability.get('kind')!='current_original':
        return None
    text={'type':'string'}
    question={'type':'string','enum':contract['question_ids']}
    nature={'type':'string','enum':contract['natures']}
    needs=[object_schema({'question_id':{'type':'string','enum':[ident]},
        'need_ids':array_schema({'type':'string','enum':[need['id'] for need in items]})})
        for ident,items in contract['question_requirements'].items() if items]
    coverage=array_schema({'anyOf':needs}) if needs else array_schema(object_schema({
        'question_id':question,'need_ids':array_schema(text)}))
    review=object_schema({'scope':{'type':'string','enum':['current_original_descriptive_only']},
        'need_links':coverage})
    index={'type':'integer','minimum':0,'maximum':len(lines)-1}
    refs=object_schema({'start':index,'end':index})
    base={'id':_identifier_schema(),'object':{'type':'string','enum':task['objects']} if task['objects'] else text,
        'nature':nature,'question_ids':array_schema(question),'review':review,
        'observation_anchor':{'type':'integer','enum':list(range(len(dates)))}}
    if source.get('object_binding'):
        base['object']={'type':'string','enum':[source['object_binding']['value']]}
    keys=METHOD_REGISTRY['net_profit_margin_v1']['binding_context']
    context={key:text for key in keys}
    context['metric']={'type':'string','enum':sorted({cell['metric'] for cell in cells})}
    context['period']={'type':'string','enum':sorted({cell['period'] for cell in cells})}
    numeric=object_schema(dict(base,financial_value_reference={'type':'string','enum':[cell['candidate_id'] for cell in cells]},
        calculation_context=object_schema(context),context_spans=object_schema({key:refs for key in keys})))
    quote=object_schema(dict(base,span=refs,period=text))
    return object_schema({'version':{'type':'integer','enum':[5]},
        'source_reference':_literal_schema({'artifact_id':source['artifact_id'],'snapshot_hash':source['snapshot_hash']}),
        'current_version_only':{'type':'boolean','enum':[True]},
        'financial_cells':array_schema(numeric),'text_quotes':array_schema(quote)})


def financial_extraction_output_schema(task, views):
    """Select bound original positions rather than regenerate known fields."""
    return _financial_extraction_output_schema(task, views, _financial_extraction_contract(task, views))


def _financial_extraction_output_schema(task, views, contract):
    schema = _financial_extraction_v5_schema(task, views, contract)
    if schema is None:
        return None
    source = contract['sources'][0]
    cells=source.get('financial_context_cells',[])
    shared=source.get('financial_shared_context_choices',{})
    if not cells or any(not shared.get(field) for field in ('entity_id','accounting_standard')):
        return schema
    schema=copy.deepcopy(schema)
    from .contracts import fingerprint
    # Local addresses are not global identities. Bind their complete meanings,
    # literal positions and task/view fingerprints into the schema as well as
    # the formal short source binding derived from it. Renumbering or changing
    # a basis must not reuse an old schema merely because its enum size agrees.
    schema['description']='财务主体与准则选择绑定：'+fingerprint(dict(version=2,choices=shared))
    schema['properties']['version']['enum']=[6]
    fields=schema['properties']['financial_cells']['items']['properties']
    for field in ('financial_value_reference','calculation_context','context_spans'):fields.pop(field)
    fields['financial_context_reference']={'type':'string','enum':[cell['candidate_id'] for cell in cells]}
    fields['shared_context_references']=object_schema({field:{'type':'string',
        'enum':[item['candidate_id'] for item in shared[field]]} for field in ('entity_id','accounting_standard')})
    schema['properties']['financial_cells']['items']['required']=list(fields)
    quotes=source.get('financial_quote_choices',[])
    if quotes and len(quotes)<=512:
        schema['properties']['version']['enum']=[7]
        fields=schema['properties']['text_quotes']['items']['properties']
        fields.pop('span')
        fields['quote_reference']={'type':'string','enum':[quote['candidate_id'] for quote in quotes]}
        schema['properties']['text_quotes']['items']['required']=list(fields)
    return schema


def financial_selector_prompt(payload,task=None):
    """Present all source characters once, alongside their local line index."""
    from .financial_sources import original_line_index,original_date_anchors,financial_quote_choices
    from .reasoning import material_prompt_projection
    projected,_=material_prompt_projection(payload)
    result=copy.deepcopy(projected)
    navigation = None
    if task is not None:
        from .knowledge import literal_navigation_rule
        candidate_rule = literal_navigation_rule(task)
        if candidate_rule is not None and candidate_rule.get('version') == 2:
            navigation = candidate_rule
            result['knowledge_navigation'] = navigation
    # Full bindings remain in the canonical evidence contract and source/schema
    # validation. The model selects only the finite local address; showing the
    # hash again invites it to submit that hash instead of the address.
    for source in result.get('evidence_contract',{}).get('sources',[]):
        for choices in source.get('financial_shared_context_choices',{}).values():
            for choice in choices:
                choice.pop('candidate_binding',None)
    for view in result['untrusted_sources']:
        original=next(v for v in payload['untrusted_sources'] if v['id']==view['id'])
        choices=financial_quote_choices(task,original) if task is not None else []
        if choices and len(choices)<=512:
            if navigation:
                hits = []
                for index, term in enumerate(navigation['terms']):
                    pattern = re.compile(r'\s*'.join(re.escape(char) for char in term))
                    matches = [item['candidate_id'] for item in choices if pattern.search(item['quote'])]
                    # Bounded first/last alternatives are literal block ids,
                    # not rankings, completeness or factual approval.
                    for reference in dict.fromkeys(matches[:2]+matches[-1:]):
                        hits.append([index,reference])
                if hits:view['knowledge_quote_hits'] = hits
            for fragment_index,part in enumerate(view['parts']):
                part['quote_choices']=[[item['candidate_id'],item['quote']] for item in choices if item['fragment_index']==fragment_index]
                del part['text']
            view['observation_anchors']=[dict(index=index,**anchor)
                for index,anchor in enumerate(original_date_anchors(original))]
            for source in result.get('evidence_contract',{}).get('sources',[]):
                if source.get('artifact_id')==view['id']:
                    # Every block and its address are already above; the raw
                    # cell locations are also bound by context-cell choices.
                    # Remove duplicate indices, never source characters or
                    # question/method/publication obligations.
                    source.pop('financial_quote_choices',None)
                    if source.get('financial_context_cells'):source.pop('financial_table_cells',None)
            continue
        indexed=original_line_index(view)
        groups={index:[] for index,_ in enumerate(view['parts'])}
        for line in indexed:
            groups[line['fragment_index']].append([line['index'],line['text']])
        for fragment_index,part in enumerate(view['parts']):
            part['indexed_lines']=groups[fragment_index]
            del part['text']
        # Enumerated addresses are not a claim of date relevance or approval.
        original=next(v for v in payload['untrusted_sources'] if v['id']==view['id'])
        view['observation_anchors']=[dict(index=index,**anchor)
            for index,anchor in enumerate(original_date_anchors(original))]
    return result


def _financial_reference_binding(task, views, contract):
    """Bind a selector to the contract computed from this operation's read view."""
    from .contracts import context, fingerprint
    legacy = _financial_extraction_output_schema(task, views, contract)
    if legacy is None or legacy['properties']['version']['enum'] != [7]:
        return None
    choices = legacy['properties']['text_quotes']['items']['properties']['quote_reference']['enum']
    refs = {'quote_' + str(index + 1): value for index, value in enumerate(choices)}
    binding = dict(source_ref='source_1', binding_ref=fingerprint(dict(task=context(task), schema=legacy))[:16])
    return dict(legacy=legacy, refs=refs, binding=binding, contract=contract)


def financial_reporting_period_choices(task, views):
    """Exact period labels and anchors from actual read financial headers."""
    from .reasoning import evidence_contract
    return _financial_reporting_period_choices(evidence_contract(task, views)['sources'][0])


def _financial_reporting_period_choices(source):
    from .contracts import fingerprint
    choices, seen = [], set()
    for cell in source.get('financial_context_cells', []):
        period = cell['context']['period']
        basis = dict(kind='material', artifact_id=source['artifact_id'],
            snapshot_hash=source['snapshot_hash'], **cell['bases']['period'])
        key = fingerprint(dict(period=period, basis=basis))
        if key in seen:
            continue
        seen.add(key)
        choices.append(dict(reference='period_'+str(len(choices)+1), period=period, basis=basis,
            candidate_is_approval=False))
    return choices


def _financial_period_binding(binding, choices):
    from .contracts import fingerprint
    return dict(binding, binding_ref=fingerprint(dict(
        base=binding, text_period_version=2, choices=choices))[:16])


def _financial_need_map_enabled(task):
    return (type(task.get('financial_need_map_version')) is int
            and task['financial_need_map_version'] == 1)


def _financial_question_owner_enabled(task):
    return (_financial_need_map_enabled(task)
            and type(task.get('financial_question_owner_version')) is int
            and task['financial_question_owner_version'] == 1)


def _financial_observation_reference_enabled(task):
    return (type(task.get('financial_observation_reference_version')) is int
            and task['financial_observation_reference_version'] == 1)


def original_observation_choices(choice):
    """Source-bound names for actual eligible dates, never table row offsets."""
    from .contracts import fingerprint
    dates=choice['description']['dates']
    source=choice['description']['source']
    indices=choice.get('observation_indices',list(range(len(dates))))
    return [dict(reference='observation_'+fingerprint(dict(
        artifact_id=source['artifact_id'],snapshot_hash=source['snapshot_hash'],
        index=index,anchor=dates[index]))[:20],index=index,
        period=(dates[index]['observation_period']['value']
                if 'observation_period' in dates[index] else dates[index]['date']))
        for index in indices if not dates[index].get('date_role')]


def document_date_choices(choice):
    """Separate reference namespace; these are not observation anchors."""
    from .document_dates import ROLE
    source = choice['description']['source']
    return [dict(reference='document_date_' + fingerprint(dict(artifact_id=source['artifact_id'],
        snapshot_hash=source['snapshot_hash'], index=index, anchor=anchor))[:20],
        index=index, period=anchor['date'], part=anchor['part'])
        for index, anchor in enumerate(choice['description']['dates']) if anchor.get('date_role') == ROLE]


def source_label_choices(task, choice):
    if choice.get('kind') != 'original': return []
    from .source_labels import candidates, enabled
    if not enabled(task): return []
    from .document_dates import ROLE
    view = choice['view']
    if not view.get('snapshot_hash'): return []
    records = [record for record in task.get('source_records', [])
               if record.get('source') == 'official' and record.get('operation') == 'release_original'
               and record.get('id') == view['id'] and record.get('snapshot_hash') == view['snapshot_hash']]
    if len(records) != 1: return []
    result = []
    for candidate in candidates(task, records[0], view.get('parts', [])):
        declared = candidate['label']['declared_date']
        indices = [index for index, anchor in enumerate(choice['description']['dates'])
                   if anchor.get('date_role') == ROLE and anchor['date'] == declared['value']
                   and anchor['part'] == declared['part'] and anchor['quote'] == declared['quote']]
        if len(indices) == 1: result.append(dict(candidate, index=indices[0], period=declared['value']))
    return result


def financial_observation_choices(views):
    from .financial_sources import original_date_anchors
    return [dict(reference='observation_'+str(index+1), **anchor)
            for index, anchor in enumerate(original_date_anchors(views[0]))]


def _financial_observation_binding(binding, choices):
    from .contracts import fingerprint
    return dict(binding, binding_ref=fingerprint(dict(
        base=binding, observation_reference_version=1, choices=choices))[:16])


def _financial_need_map_schema(task, contract):
    """Unique question keys, explicit empty selection, no inferred coverage."""
    requirements = contract['question_requirements']
    mapping = {qid: dict(array_schema({'type':'string', 'enum':[
        need['id'] for need in needs]}), uniqueItems=True, maxItems=len(needs))
        for qid, needs in requirements.items() if needs}
    if _financial_question_owner_enabled(task):
        # null explicitly withholds an owner; [] selects the owner without
        # claiming a registered need. One map expresses both, never two lists.
        return object_schema({qid: {'anyOf':[mapping.get(qid, dict(
            array_schema({'type':'string'}), maxItems=0)), {'type':'null'}]}
            for qid in contract['question_ids']})
    return object_schema(mapping)


def _financial_text_period_need_schema(task, views, quote, *, contract=None):
    """Offer only compatible literal period/need pairs, never approve a fact.

    Group equivalent choices to avoid a branch per date or original header.
    Empty links remain available for legitimate background quotations.
    """
    version = task.get('financial_period_need_compatibility_version')
    if version is None:
        return quote
    if type(version) is not int or version != 1:
        raise ContractError('财务摘录期间与需求兼容版本未登记')
    if task.get('financial_text_period_version') != 2 or not _financial_need_map_enabled(task):
        raise ContractError('财务摘录期间兼容须使用显式原文期间和问题需求映射')
    from .data_requirements import normalized, period_matches
    if contract is None:
        from .reasoning import evidence_contract
        contract = evidence_contract(task, views)
    requirements = contract['question_requirements']
    constraints = {question['id']: normalized(question) for question in task['questions']}
    periods = {choice['reference']: choice['period']
               for choice in _financial_reporting_period_choices(contract['sources'][0])}
    # observation_date decodes the original anchor's ISO date, not a duration.
    from .financial_sources import original_date_anchors
    observations = original_date_anchors(views[0])
    if any(not period_matches(anchor['date'], 'point') for anchor in observations):
        raise ContractError('财务观测日期形状未登记，不能假定为期间')
    groups = {}
    for reference in quote['properties']['period_reference']['enum']:
        period = observations[0]['date'] if reference == 'observation_date' and observations else periods.get(reference)
        eligible = {}
        for qid, needs in requirements.items():
            typed = constraints.get(qid)
            eligible[qid] = [need['id'] for need in needs if typed is None or (
                typed[need['text']]['value_kind'] == 'text'
                and period_matches(period, typed[need['text']]['period_kind']))]
        group = groups.setdefault(fingerprint(eligible), dict(eligible=eligible, references=[]))
        group['references'].append(reference)
    branches = []
    for group in groups.values():
        branch = copy.deepcopy(quote)
        branch['properties']['period_reference']['enum'] = group['references']
        mapping = branch['properties']['review']['properties']['need_links']['properties']
        for qid, field in mapping.items():
            array = field['anyOf'][0] if 'anyOf' in field else field
            allowed = group['eligible'].get(qid, [])
            array['items'] = dict(type='string', **({'enum': allowed} if allowed else {}))
            array['maxItems'] = len(allowed)
        branches.append(branch)
    return branches[0] if len(branches) == 1 else {'anyOf': branches}


def _financial_amount_catalogue(task, contract):
    from .financial_sources import financial_amount_selection_enabled
    if not financial_amount_selection_enabled(task):
        return None
    sources = contract['sources']
    if len(sources) != 1 or 'financial_amount_tables' not in sources[0]:
        raise ContractError('财务明细选择缺少同原件读取目录')
    return sources[0]['financial_amount_tables']


def _financial_amount_reference_enabled(task):
    version = task.get('financial_amount_reference_version')
    if version is None:
        return False
    if type(version) is not int or version != 1:
        raise ContractError('财务金额单元选择版本未登记')
    from .financial_data import product_selection_enabled
    return product_selection_enabled(task)


def _financial_amount_choices(task, catalogue):
    """One exact, task-bound address per already-read original amount cell."""
    from .contracts import context
    base = dict(task=context(task), catalogue_hash=fingerprint(catalogue))
    choices = {}
    for table in catalogue['tables']:
        for column in table['columns']:
            for row in column['row_references']:
                address = dict(table_reference=table['reference'],
                    column_reference=column['reference'], row_reference=row)
                reference = 'amount_' + fingerprint(dict(base, address=address))[:16]
                if reference in choices:
                    raise ContractError('财务金额单元引用重复')
                choices[reference] = dict(table=table, column=column, row=row, address=address)
    return choices


def financial_amount_submission_rule(task):
    """One instruction source for the active amount wire, including final prompts."""
    address = ('每条用amount_reference选择本来源financial_amount_choices.reference，'
               '一个字符串对应一个已读金额单元，多项金额分别提交多条记录。'
               if _financial_amount_reference_enabled(task) else
               '选择本来源financial_amount_tables的table_reference、column_reference和row_reference，'
               '逐项复制原目录字符串，row_reference保留line_前缀。')
    return ('仅本来源Schema列出amount_cells时才填写该字段。'+address+
        '观测日期选择同来源observation_reference；不抄金额，不将空白当零，不补单位或计算口径。'
        '只关联完整原行实际支持且期间相容的问题和需求，所选记录仍须来源和金融适用性核验。'
        'text_quotes不能代替数值需求；缺项、歧义列及未读原文保留实际限制。')


def _financial_amount_choice_prompt(task, catalogue):
    return [dict(reference=reference, period=choice['column']['period'],
                 unit=choice['table']['unit'], part=choice['table']['part'],
                 text=choice['table']['rows'][choice['row']]['text'])
            for reference, choice in _financial_amount_choices(task, catalogue).items()]


def _financial_amount_binding(binding, catalogue, task=None):
    description = dict(base=binding, financial_amount_selection_version=1, catalogue=catalogue)
    if task is not None and _financial_amount_reference_enabled(task):
        description['financial_amount_reference_version'] = 1
    return dict(binding, binding_ref=fingerprint(description)[:16])


def _financial_amount_need_compatible(spec, period):
    """A numeric row retains text as well as its exact monetary value.

    Match the ordinary adoption gate: literal row text may support a text
    need, while numeric needs still require amount dimension and period.
    Eligibility does not establish the row's meaning or approve coverage.
    """
    from .data_requirements import period_matches
    return (period_matches(period, spec['period_kind'])
            and (spec['value_kind'] == 'text'
                 or spec['value_kind'] == 'number' and spec['unit_dimension'] == 'amount'))


def _financial_amount_wire_schema(task, views, catalogue, contract):
    from .data_requirements import normalized, period_matches
    source = contract['sources'][0]
    if not source.get('object_binding') or not source.get('current_availability_reference'):
        return dict(array_schema(object_schema({})), maxItems=0)
    observations = financial_observation_choices(views)
    base_map = _financial_need_map_schema(task, contract)
    constraints = {question['id']: normalized(question) for question in task['questions']}
    choices = _financial_amount_choices(task, catalogue) if _financial_amount_reference_enabled(task) else None
    variants = []
    for table in catalogue['tables']:
        for column in table['columns']:
            mapping = copy.deepcopy(base_map)
            for qid, shape in mapping['properties'].items():
                eligible = [need['id'] for need in contract['question_requirements'].get(qid, [])
                    if constraints.get(qid) is None or _financial_amount_need_compatible(
                        constraints[qid][need['text']], column['period'])]
                array = shape['anyOf'][0] if 'anyOf' in shape else shape
                array['items'] = dict(type='string', **({'enum': eligible} if eligible else {}))
                array['maxItems'] = len(eligible)
            dates = [choice['reference'] for choice in observations
                     if not period_matches(column['period'], 'point') or choice['date'] == column['period']]
            if not dates:
                continue
            address = (dict(amount_reference={'type':'string','enum':[
                reference for reference, choice in choices.items()
                if choice['table']['reference'] == table['reference']
                and choice['column']['reference'] == column['reference']]}) if choices is not None else dict(
                table_reference={'type':'string','enum':[table['reference']]},
                column_reference={'type':'string','enum':[column['reference']]},
                row_reference={'type':'string','enum':column['row_references']}))
            variants.append(object_schema(dict(id=_identifier_schema(),
                object={'type':'string','enum':[source['object_binding']['value']]},
                nature={'type':'string','enum':contract['natures']},
                **address,
                observation_reference={'type':'string','enum':dates},
                review=object_schema(dict(scope={'type':'string','enum':['current_original_descriptive_only']},
                                          need_links=mapping)))))
    return (array_schema({'anyOf': variants}) if variants else dict(array_schema(object_schema({})), maxItems=0))


def _decode_financial_amounts(task, views, selections, catalogue, contract):
    """Restore selected original cells, never an assumed metric or calculation."""
    from .financial_sources import _financial_amount_value
    source = contract['sources'][0]
    tables = {table['reference']: table for table in catalogue['tables']}
    observations = {choice['reference']: choice for choice in financial_observation_choices(views)}
    requirements = {qid: {need['id']: need['text'] for need in needs}
                    for qid, needs in contract['question_requirements'].items()}
    specs = []
    choices = _financial_amount_choices(task, catalogue) if _financial_amount_reference_enabled(task) else None
    for item in selections:
        if choices is not None:
            selected = choices.get(item.get('amount_reference'))
            if selected is None:
                raise StructuredOutputError('invalid_financial_amount', '财务金额单元不属于本任务已读目录')
            item = dict(item, **selected['address'])
        table = tables[item['table_reference']]
        column = next(value for value in table['columns'] if value['reference'] == item['column_reference'])
        row = table['rows'][item['row_reference']]['text']
        if table.get('version') == 2:
            from .financial_data import original_amount_value
            cell = table['rows'][item['row_reference']]['cells'][column['reference']]
            values = original_amount_value(cell)
        else:
            values = _financial_amount_value(row.split('\t')[column['column_index']], table['unit'])
        if values is None:
            raise StructuredOutputError('invalid_financial_amount', '财务明细单元格不是可无损读取的原值')
        mapping = item['review']['need_links']
        owners = [qid for qid, needs in mapping.items() if needs is not None]
        if not owners:
            raise StructuredOutputError('invalid_need_mapping', '财务明细没有显式选择适用问题')
        mapped = {qid: [requirements[qid][ident] for ident in needs] for qid, needs in mapping.items() if needs}
        observation = observations[item['observation_reference']]
        column_locator = dict(version=1, header_part=table['header_part'], header_quote=table['header_quote'],
            row_quote=row, column_index=column['column_index'], period_mode=column['mode'],
            report_period_part=column['report_part'], report_period_quote=column['report_quote'],
            unit_quote=table['unit_quote'], period_basis_part=column['period_basis']['part'],
            period_basis_quote=column['period_basis']['quote'])
        if table.get('version') == 2:
            column_locator = dict(version=2, cell_key=cell['key'], cell_binding=fingerprint(cell),
                row_quote=cell['citation']['quote'], header_part=cell['period_basis']['part'],
                header_quote=cell['period_basis']['quote'], unit_quote=cell['unit_basis']['quote'],
                period_basis_part=cell['period_basis']['part'], period_basis_quote=cell['period_basis']['quote'])
        review = dict(question_ids=owners, allowed_natures=[item['nature']], conditions=[
            '原列、原单位和明确报告期间须独立核验，不以本目录批准事实、合并范围、币种或现金转化判断。',
            '仅当前取得版本，首次公开未知仍未知；本明细不授予登记方法的计算口径。'],
            reason='显式选择同原件表格行列、原值与需求，继续来源和适用性核验。', temporal_use='current_version')
        if mapped:
            review['evidence_need_texts'] = mapped
        specs.append(dict(id=item['id'], artifact_id=source['artifact_id'], object=item['object'],
            nature=item['nature'], question_ids=owners, text=row, period=column['period'],
            **values, availability=copy.deepcopy(source['current_availability_reference']), published_at=None,
            publication=dict(at=None, basis_ref=None, precision='未知', timezone='未知', certainty='未知',
                             basis='首次公开时间未知，当前可得性另由实际取数账本核验'),
            observation_date=observation['date'],
            record_locator=dict(part=table['part'], unit_part=table['unit_part'],
                period_part=observation['part'], period_quote=observation['quote'],
                financial_amount_column=column_locator),
            reporting_period_basis=dict(kind='material', artifact_id=source['artifact_id'],
                snapshot_hash=source['snapshot_hash'], **copy.deepcopy(column['period_basis'])), review=review))
        if table.get('version')==2 and re.fullmatch(r'\d{4}-\d{2}-\d{2}',column['period']):
            from .financial_data import statement_details_enabled
            if not statement_details_enabled(task):
                raise StructuredOutputError('invalid_financial_amount','本任务未登记余额日期单元')
            specs[-1]['reporting_period_basis']['version']=4
    return specs


def financial_reference_output_schema(task, views):
    """Short quote addresses, still bound to the exact source and read view."""
    selector = _financial_reference_binding(task, views, _financial_extraction_contract(task, views))
    return _financial_reference_schema(task, views, selector) if selector is not None else None


def _financial_reference_schema(task, views, selector):
    contract = selector['contract']
    binding = selector['binding']
    schema = copy.deepcopy(selector['legacy'])
    schema['properties']['version']['enum'] = [8]
    schema['properties']['source_reference'] = _literal_schema(binding)
    field = schema['properties']['text_quotes']['items']['properties']['quote_reference']
    field['enum'] = list(selector['refs'])
    if task.get('financial_text_period_version') == 2:
        choices = _financial_reporting_period_choices(contract['sources'][0])
        schema['properties']['version']['enum'] = [9]
        schema['properties']['source_reference'] = _literal_schema(_financial_period_binding(binding, choices))
        quote = schema['properties']['text_quotes']['items']
        quote['properties'].pop('period')
        quote['properties']['period_reference'] = {'type':'string', 'enum':[
            'observation_date', *[choice['reference'] for choice in choices]]}
        quote['required'] = list(quote['properties'])
    if _financial_observation_reference_enabled(task):
        choices = financial_observation_choices(views)
        current_binding = {key:value['enum'][0] for key,value in
                           schema['properties']['source_reference']['properties'].items()}
        schema['properties']['source_reference'] = _literal_schema(_financial_observation_binding(current_binding,choices))
        for name in ('financial_cells','text_quotes'):
            item = schema['properties'][name]['items']
            item['properties'].pop('observation_anchor')
            item['properties']['observation_reference'] = {'type':'string','enum':[row['reference'] for row in choices]}
            item['required'] = list(item['properties'])
    if _financial_need_map_enabled(task):
        mapping = _financial_need_map_schema(task, contract)
        for name in ('financial_cells', 'text_quotes'):
            item = schema['properties'][name]['items']
            fields = item['properties']
            fields['review']['properties']['need_links'] = copy.deepcopy(mapping)
            if _financial_question_owner_enabled(task):
                fields.pop('question_ids')
                item['required'] = list(fields)
            else:
                fields['question_ids']['uniqueItems'] = True
    schema['properties']['text_quotes']['items'] = _financial_text_period_need_schema(
        task, views, schema['properties']['text_quotes']['items'], contract=contract)
    catalogue = _financial_amount_catalogue(task, contract)
    if catalogue is not None:
        if not _financial_question_owner_enabled(task) or not _financial_observation_reference_enabled(task):
            raise ContractError('财务明细选择须使用显式问题归属和观测日期引用')
        current = {key: value['enum'][0] for key, value in schema['properties']['source_reference']['properties'].items()}
        schema['properties']['source_reference'] = _literal_schema(_financial_amount_binding(current, catalogue, task))
        shape = _financial_amount_wire_schema(task, views, catalogue, contract)
        from .financial_data import product_selection_enabled
        if not product_selection_enabled(task) or shape.get('maxItems') != 0:
            schema['properties']['amount_cells'] = shape
        schema['required'] = list(schema['properties'])
    return schema


def _financial_selector_projection(payload, task):
    """Project wire instructions only; retain originals and all local gates.

    The complete evidence contract describes post-decoding canonical records.
    Its API/legacy submission examples are not additional selector formats.
    """
    if (type(task.get('financial_selector_projection_version')) is not int
            or task['financial_selector_projection_version'] != 1):
        return payload
    fields = ('version','knowledge_guidance','binding','research_scope','statistical_period_contract',
        'natures','identifier_contract','object_binding_contract','question_ids','question_requirements',
        'question_coverage_contract','document_coverage_contract','calculation_input_requirements',
        'calculation_context_shape','material_text_contract','calculation_unit_rule',
        'current_availability_rule','current_original_rule','sources','source_bindings_omitted',
        'unusable_rule','template_is_approval','external_publication_relationships_supported')
    contract = payload['evidence_contract']
    payload['evidence_contract'] = {name:copy.deepcopy(contract[name]) for name in fields if name in contract}
    payload['evidence_contract']['selector_submission_contract'] = dict(version=1, automatic_approval=False,
        rule='本契约提供金融适用条件，不是另一套返回格式。只按本次Schema与source_choices的选择格式提交。',
        decoding='共享内核恢复完整证据记录后，继续核验来源、原值、期间、主体、单位、适用性及需求覆盖。')
    return payload


def financial_reference_prompt(payload, task):
    views = payload.get('untrusted_sources')
    selector = _financial_reference_binding(task, views, _financial_extraction_contract(task, views))
    if selector is None:
        raise ContractError('本次原件不适用财务短引用，不改变旧提取协议')
    return _financial_reference_prompt(payload, task, selector)


def _financial_reference_prompt(payload, task, selector):
    contract = selector['contract']
    binding = selector['binding']
    aliases = {value: key for key, value in selector['refs'].items()}
    result = financial_selector_prompt(payload, task)
    for view in result['untrusted_sources']:
        for part in view['parts']:
            part['quote_choices'] = [[aliases[ref], quote] for ref, quote in part['quote_choices']]
        if 'knowledge_quote_hits' in view:
            view['knowledge_quote_hits'] = [[index,aliases[ref]] for index,ref in view['knowledge_quote_hits']]
    result['financial_reference_binding'] = binding
    if _financial_question_owner_enabled(task):
        result['financial_need_link_rule'] = (
            'review.need_links是本摘录适用问题及需求的唯一固定对象，不另填question_ids。'
            '每个问题键：null表示不关联该题；[]表示关联该题但不覆盖任何登记需求；'
            '非空列表只选本摘录实际支持的该题need_id，不重复、不跨题。至少显式关联一题；'
            '没有适用问题则省略整项，不提交所有问题均为null的条目，不为保留条目而随意关联问题。'
            '内核仅无损恢复这些显式选择，不自动选需求、改值或批准事实，仍须来源及金融适用性核验。')
    elif _financial_need_map_enabled(task):
        result['financial_need_link_rule'] = (
            'review.need_links是Schema所列问题id的固定对象，不是question_id/need_ids列表。'
            '每个键填写本摘录实际支持的该题need_id列表，不支持则填[]；编号不重复、不跨题。'
            '非空关联只能属于本条question_ids，空列表不计覆盖。关联仍须来源及金融适用性核验。')
    if task.get('financial_text_period_version') == 2:
        choices = _financial_reporting_period_choices(contract['sources'][0])
        result['reporting_period_choices'] = choices
        result['financial_reference_binding'] = _financial_period_binding(binding, choices)
        result['reporting_period_rule'] = ('text_quotes.period_reference显式选择原文期间，或observation_date只保留观测日。'
            '期间、观测日和首次公开时间不互相替代；这些候选不是事实或适用性批准。')
        if task.get('financial_period_need_compatibility_version') == 1:
            result['reporting_period_need_rule'] = (
                'text_quotes按Schema选择兼容的period_reference与review.need_links。'
                '区间文字需求显式选择原文期间，不能以期末观测日代替；文字摘录不覆盖数值需求。'
                '无兼容原文期间则保留该需求缺口，不能猜日期或更改已登记需求。'
                '空需求列表可保留背景摘录，不计覆盖；兼容形状不证明期间、内容或金融适用性。')
    if _financial_observation_reference_enabled(task):
        choices = financial_observation_choices(payload['untrusted_sources'])
        result['untrusted_sources'][0].pop('observation_anchors')
        result['untrusted_sources'][0]['observation_choices'] = choices
        result['financial_reference_binding'] = _financial_observation_binding(result['financial_reference_binding'], choices)
        result['financial_observation_rule'] = (
            'financial来源的financial_cells和text_quotes均显式选择observation_reference字符串，'
            '仅取本来源observation_choices.reference，不填数字下标、原始日期、null或observation_anchor。'
            '每个编号只定位所列date、part和逐字quote，不证明首次公开时间或自动批准观测日期的金融用途。')
    catalogue = _financial_amount_catalogue(task, contract)
    if catalogue is not None:
        result['financial_reference_binding'] = _financial_amount_binding(result['financial_reference_binding'], catalogue, task)
        result['financial_amount_tables'] = dict(version=1, scan_complete=catalogue['scan_complete'],
            unoffered_columns=catalogue['unoffered_columns'], limitation=catalogue['limitation'],
            tables=[dict(reference=table['reference'], part=table['part'], header_quote=table['header_quote'],
                         unit=table['unit'], columns=table['columns'],
                         rows=[[ref, row['text']] for ref, row in table['rows'].items()]) for table in catalogue['tables']])
        for source in result['evidence_contract'].get('sources', []):
            source.pop('financial_amount_tables', None)
        result['financial_amount_rule'] = (
            '未列入登记计算指标的财务明细金额用amount_cells。按financial_amount_tables显式选择同表的table_reference、'
            'column_reference、row_reference和本来源observation_reference，不抄金额、单位或补计算口径。'
            '这些reference全部是目录中的字符串；row_reference保留line_前缀，不能填数字行号、null、列表或对象。'
            '找不到适用行列时不提交该项，不能用空值占位。'
            'review.need_links只关联本次列出的、由所选完整原行实际支持的需求。金额记录保留原行文字，'
            '可关联期间相容的文字说明需求，不丢弃数值或改成纯文字；不能把text_quotes用于充抵数值需求。'
            '某问题的允许列表为空时填null或空列表。原值、负号、单位与期间由原件恢复，'
            '仍须独立核验；已有专用引用的登记方法输入继续用financial_cells及完整口径，不能用无口径明细代替。'
            '未提供的相对上期不能映射为本期，不把空白当零。')
        from .financial_data import product_selection_enabled
        if _financial_amount_reference_enabled(task):
            result.pop('financial_amount_tables')
            # The active core selector uses financial_context_cells. Keep the
            # old raw-cell catalogue in the canonical decoder, rather than
            # showing two different address namespaces to the model.
            for source in result['evidence_contract'].get('sources', []):
                source.pop('financial_table_cells', None)
                for cell in source.get('financial_context_cells', []):
                    cell.pop('financial_value_reference', None)
            result['financial_amount_choices'] = _financial_amount_choice_prompt(task, catalogue)
            result['financial_amount_rule'] = (
                'amount_cells只选择本来源financial_amount_choices.reference填写amount_reference，'
                '每条记录只填一个字符串引用；选择多项金额时分别提交多条记录，不把引用合并为列表或对象。'
                '不填写表、行、列或金额，不提交空值占位。每个引用只对应所列原文、期间与单位，'
                '程序恢复原始单元格并独立核验。review.need_links仅关联原文实际支持且期间相容的需求；'
                '登记方法输入继续使用financial_cells及完整口径，明细不自动取得计算权限。')
        if product_selection_enabled(task):
            result['unread_supplement_cells'] = catalogue.get('unread_supplement_cells', [])
            if not catalogue['tables']:
                result['financial_amount_rule'] = '本次没有可选择的财务明细单元，不填写amount_cells。未读片段可继续按需读取，不把目录为空解释为原件没有数据。'
    return _financial_selector_projection(result, task)


def decode_financial_references(wire, schema, context):
    if not isinstance(context, tuple) or len(context) != 2:
        raise StructuredOutputError('missing_financial_reference_context', '财务短引用缺少实际任务和原件')
    task, views = context
    selector = _financial_reference_binding(task, views, _financial_extraction_contract(task, views))
    if selector is None or _financial_reference_schema(task, views, selector) != schema:
        raise StructuredOutputError('source_binding_changed', '财务短引用绑定已变化，未采用提案')
    return _decode_financial_reference_selection(wire, schema, task, views, selector)


def _decode_financial_reference_selection(wire, schema, task, views, selector):
    if not _matches(wire, schema):
        raise StructuredOutputError('invalid_financial_reference', '财务短引用不在本次候选范围，未采用提案',
                                    schema_diagnostic(wire, schema))
    if wire['version'] == 9:
        identifiers = [item['id'] for key in ('financial_cells', 'text_quotes', 'amount_cells') for item in wire.get(key, [])]
        if len(identifiers) != len(set(identifiers)):
            raise StructuredOutputError('duplicate_evidence_id', '原件选择的证据标识重复，未覆盖期间依据')
    contract = selector['contract']
    legacy, refs = selector['legacy'], selector['refs']
    expanded = copy.deepcopy(wire)
    amount_selections = expanded.pop('amount_cells', [])
    expanded['version'] = 7
    expanded['source_reference'] = {key: value['enum'][0] for key, value in
        legacy['properties']['source_reference']['properties'].items()}
    if _financial_observation_reference_enabled(task):
        observations = {row['reference']:index for index,row in enumerate(financial_observation_choices(views))}
        for name in ('financial_cells','text_quotes'):
            for item in expanded[name]:
                item['observation_anchor'] = observations[item.pop('observation_reference')]
    periods = {}
    choices = ({choice['reference']:choice
                for choice in _financial_reporting_period_choices(contract['sources'][0])}
               if wire['version'] == 9 else {})
    if wire['version'] == 9:
        from .financial_sources import original_date_anchors
        dates = original_date_anchors(views[0])
    for item in expanded['text_quotes']:
        item['quote_reference'] = refs[item['quote_reference']]
        if wire['version'] == 9:
            selected = item.pop('period_reference')
            if selected == 'observation_date':
                item['period'] = dates[item['observation_anchor']]['date']
            else:
                item['period'] = choices[selected]['period']
                periods[item['id']] = choices[selected]['basis']
    if _financial_need_map_enabled(task):
        for name in ('financial_cells', 'text_quotes'):
            for item in expanded[name]:
                mapping = item['review']['need_links']
                if _financial_question_owner_enabled(task):
                    item['question_ids'] = [qid for qid, needs in mapping.items() if needs is not None]
                    if not item['question_ids']:
                        raise StructuredOutputError('invalid_need_mapping',
                            '财务摘录没有显式选择适用问题，未采用提案')
                elif any(needs and qid not in item['question_ids'] for qid, needs in mapping.items()):
                    raise StructuredOutputError('invalid_need_mapping',
                        '财务需求关联超出本摘录适用问题，未采用提案')
                # Decode only explicit selections. Empty arrays mean no link;
                # never fill a need, merge duplicate pairs or approve evidence.
                item['review']['need_links'] = [dict(question_id=qid, need_ids=needs)
                    for qid, needs in mapping.items() if needs]
    # Ordinary extraction, exact original text and all later source/financial
    # gates remain authoritative. The selector never approves a fact.
    result = _decode_financial_extraction_output(expanded, legacy, (task, views), contract=contract)
    for item in result['evidence_specs']:
        if item['id'] in periods:
            item['reporting_period_basis'] = copy.deepcopy(periods[item['id']])
    if amount_selections:
        result['evidence_specs'].extend(_decode_financial_amounts(
            task, views, amount_selections, _financial_amount_catalogue(task, contract), contract))
    return result


def _general_original_selection(task):
    from .contracts import DOCUMENT_CONTENT_INTENT
    methods = {method for question in task.get('questions', []) for method in question.get('method_needs', [])}
    methods.update(item.get('method') for item in task.get('calculation_specs', []))
    return (type(task.get('general_original_selection_version')) is int
            and task['general_original_selection_version'] in (1, 2) and not methods
            and bool(task.get('argument_support_contract')) and task.get('research_mode') == 'current'
            and task.get('intent') != DOCUMENT_CONTENT_INTENT)


def _span_need_references(task, contract):
    """Question-local aliases; canonical task needs remain in the archive."""
    compact = (_general_original_selection(task) and task['general_original_selection_version'] == 2
               or _method_span_projection(task))
    return {qid: {(f'n{index}' if compact else need['id']): need
                  for index, need in enumerate(needs, 1)}
            for qid, needs in contract['question_requirements'].items()}


def _original_quote_selector(task, view, *, _document_dates=False, contract=None):
    """A nonnumeric original can explain a financial question, not supply cash."""
    from .contracts import context, fingerprint
    from .financial_sources import original_date_anchors, financial_quote_choices
    if contract is None:
        from .reasoning import evidence_contract
        contract = evidence_contract(task, [view])
    if contract['source_bindings_omitted'] or len(contract['sources']) != 1:
        return None
    source = contract['sources'][0]
    dates = original_date_anchors(view)
    if task.get('original_duration_anchor_version')==1:
        from .financial_sources import interval_date_anchors
        dates=interval_date_anchors(view,dates)
    general = _general_original_selection(task)
    if general:
        from .official_sources import statistical_period_anchors
        records = [record for record in task.get('source_records', []) if record.get('id') == view.get('id')
                   and record.get('snapshot_hash', record.get('hash')) == view.get('snapshot_hash')]
        monthly = statistical_period_anchors(task, records[0], view.get('parts', [])) if len(records) == 1 else []
        if monthly:
            dates = monthly
        temporal = source.get('publication_mode_contract', {})
        if not monthly and temporal.get('version') == 1:
            # The official adapter already verifies literal FOMC date headers
            # against the same retained page and URL. Reuse those addresses in
            # the compact selector; do not infer first publication/effect time
            # or fall back to a much larger generic grammar for English dates.
            for candidate in temporal.get('date_candidates', []):
                if candidate not in dates:
                    dates.append(copy.deepcopy(candidate))
    if _document_dates:
        from .document_dates import anchors
        records = [record for record in task.get('source_records', []) if record.get('id') == view.get('id')]
        if len(records) == 1:
            dates += anchors(task, records[0], view.get('parts', []))
    quotes = financial_quote_choices(task, view)
    availability = source.get('current_availability_reference')
    if (not availability or availability.get('kind') != 'current_original'
            or not dates or not quotes or len(quotes) > 512):
        return None
    questions = {'type':'string', 'enum':contract['question_ids']}
    needs = [object_schema({'question_id':{'type':'string','enum':[ident]},
        'need_ids':array_schema({'type':'string','enum':list(items)})})
        for ident, items in _span_need_references(task, contract).items() if items]
    if not needs:
        return None
    objects = ({'type':'string','enum':[source['object_binding']['value']]}
        if source.get('object_binding') else {'type':'string','enum':task['objects']})
    if not objects.get('enum'):
        if not general:
            return None
        objects = {'type': 'string', 'minLength': 1}
    refs = {'quote_' + str(index+1):quote for index, quote in enumerate(quotes)}
    binding = fingerprint(dict(task=context(task), view=view))[:16]
    schema = object_schema({'binding_ref':{'type':'string','enum':[binding]},
        'text_quotes':array_schema(object_schema({'id':_identifier_schema(), 'object':objects,
            'nature':{'type':'string','enum':contract['natures']}, 'question_ids':array_schema(questions),
            'review':object_schema({'scope':{'type':'string','enum':['current_original_descriptive_only']},
                'need_links':array_schema({'anyOf':needs})}),
            'observation_anchor':{'type':'integer','enum':list(range(len(dates)))},
            'quote_reference':{'type':'string','enum':list(refs)}}))})
    periods = []
    from .financial_data import product_selection_enabled
    if product_selection_enabled(task):
        catalogue = source.get('financial_amount_tables', {})
        seen_periods = set()
        for table in catalogue.get('tables', []):
            for column in table['columns']:
                signature = fingerprint(dict(period=column['period'], basis=column['period_basis']))
                if signature in seen_periods:
                    continue
                seen_periods.add(signature)
                periods.append(dict(reference='period_'+str(len(periods)+1), period=column['period'],
                    basis=dict(kind='material', artifact_id=source['artifact_id'],
                        snapshot_hash=source['snapshot_hash'], **column['period_basis'])))
        if periods:
            quote = schema['properties']['text_quotes']['items']
            quote['properties']['period_reference'] = {'type':'string', 'enum':['observation_date',
                *[period['reference'] for period in periods]]}
            quote['required'] = list(quote['properties'])
        if catalogue.get('tables'):
            shape = _financial_amount_wire_schema(task, [view], catalogue, contract)
            if shape.get('maxItems') != 0:
                schema['properties']['amount_cells'] = shape
                schema['required'] = list(schema['properties'])
    return dict(schema=schema, binding_ref=binding, refs=refs, dates=dates, source=source,
                contract=contract, reporting_period_choices=periods)


def _original_batch_choices(task, views):
    """Do not discard a source, method or API to obtain a simpler container."""
    from .contracts import FINANCIAL_PERIOD_METHODS
    methods = {method for q in task.get('questions', []) for method in q.get('method_needs', [])}
    methods.update(spec.get('method') for spec in task.get('calculation_specs', []))
    quote_fallback = (type(task.get('financial_quote_fallback_version')) is int
                      and task['financial_quote_fallback_version'] == 1)
    if (not methods or not methods.issubset(FINANCIAL_PERIOD_METHODS)
            or not isinstance(views, list) or not (1 if quote_fallback else 2) <= len(views) <= 12
            or any(view.get('kind') not in ('filing','material') for view in views)
            or len({view.get('id') for view in views}) != len(views)):
        return None
    choices = []
    for index, view in enumerate(views):
        source_views = [view]
        contract = _financial_extraction_contract(task, source_views)
        selector = _financial_reference_binding(task, source_views, contract)
        if selector is not None:
            schema = _financial_reference_schema(task, source_views, selector)
            kind = 'financial'
        else:
            selector = _original_quote_selector(task, view, contract=contract)
            if selector is None:
                return None
            schema, kind = selector['schema'], 'qualitative'
        choices.append(dict(source_ref='source_'+str(index+1), kind=kind, view=view,
                            schema=schema, selector=selector))
    # A borderless financial report remains readable prose. Reuse the literal
    # quote protocol without manufacturing table cells, numeric inputs or a
    # successful method. Existing typed sources always retain their own route.
    return choices if quote_fallback or any(item['kind']=='financial' for item in choices) else None


def original_batch_output_schema(task, views):
    choices = _original_batch_choices(task, views)
    return _original_batch_schema(task, choices) if choices is not None else None


def _original_batch_schema(task, choices):
    from .contracts import context, fingerprint
    binding = fingerprint(dict(task=context(task), choices=[dict(source_ref=item['source_ref'],
        schema=item['schema']) for item in choices]))[:16]
    return object_schema({'version':{'type':'integer','enum':[1]},
        'binding_ref':{'type':'string','enum':[binding]},
        'sources':array_schema({'anyOf':[object_schema({
            'source_ref':{'type':'string','enum':[item['source_ref']]}, 'selection':item['schema']})
            for item in choices]})})


def original_batch_prompt(payload, task):
    choices = _original_batch_choices(task, payload.get('untrusted_sources'))
    if choices is None:
        raise ContractError('本次多原件不适用登记选择格式，不删材料或义务')
    result = copy.deepcopy(payload)
    result['untrusted_sources'] = []
    result['evidence_contract']['sources'] = []
    result['source_choices'] = []
    from .knowledge import literal_navigation_rule
    navigation = literal_navigation_rule(task)
    if navigation is not None and navigation.get('version') == 2:
        result['knowledge_navigation'] = navigation
    result['batch_binding_ref'] = _original_batch_schema(task, choices)[
        'properties']['binding_ref']['enum'][0]
    for item in choices:
        view = item['view']
        period_metadata = {}
        if item['kind']=='financial':
            selector = item['selector']
            projected = _financial_reference_prompt(dict(untrusted_sources=[view],
                evidence_contract=selector['contract']), task, selector)
            result['untrusted_sources'].extend(projected['untrusted_sources'])
            result['evidence_contract']['sources'].extend(projected['evidence_contract']['sources'])
            binding = projected['financial_reference_binding']
            if 'financial_need_link_rule' in projected:
                period_metadata['financial_need_link_rule'] = projected['financial_need_link_rule']
            if 'financial_observation_rule' in projected:
                period_metadata['financial_observation_rule'] = projected['financial_observation_rule']
            if task.get('financial_text_period_version') == 2:
                period_metadata['reporting_period_choices'] = projected['reporting_period_choices']
            if 'reporting_period_need_rule' in projected:
                period_metadata['reporting_period_need_rule'] = projected['reporting_period_need_rule']
            if 'financial_amount_tables' in projected:
                period_metadata['financial_amount_tables'] = projected['financial_amount_tables']
                period_metadata['financial_amount_rule'] = projected['financial_amount_rule']
            if 'financial_amount_choices' in projected:
                period_metadata['financial_amount_choices'] = projected['financial_amount_choices']
                period_metadata['financial_amount_rule'] = projected['financial_amount_rule']
            if 'unread_supplement_cells' in projected:
                period_metadata['unread_supplement_cells'] = projected['unread_supplement_cells']
        else:
            quote = item['selector']
            projected_view = copy.deepcopy(view)
            for index, part in enumerate(projected_view['parts']):
                part['quote_choices'] = [[ref, value['quote']] for ref, value in quote['refs'].items()
                    if value['fragment_index']==index]
                del part['text']
            projected_view['observation_anchors'] = [dict(index=index, **date)
                for index, date in enumerate(quote['dates'])]
            result['untrusted_sources'].append(projected_view)
            result['evidence_contract']['sources'].append(copy.deepcopy(quote['source']))
            binding = dict(binding_ref=quote['binding_ref'])
            if quote['reporting_period_choices']:
                period_metadata['reporting_period_choices'] = quote['reporting_period_choices']
                period_metadata['reporting_period_rule'] = 'text_quotes按Schema显式选择period_reference；原文期间和观测日不互相替代。'
            from .financial_data import product_selection_enabled
            if product_selection_enabled(task):
                catalogue = quote['source'].get('financial_amount_tables', {})
                period_metadata['unread_supplement_cells'] = catalogue.get('unread_supplement_cells', [])
                if 'amount_cells' in quote['schema']['properties']:
                    period_metadata['financial_amount_tables'] = dict(tables=[dict(
                        reference=table['reference'], part=table['part'], unit=table['unit'],
                        columns=table['columns'], rows=[[ref,row['text']] for ref,row in table['rows'].items()])
                        for table in catalogue['tables']])
                    period_metadata['observation_choices'] = financial_observation_choices([view])
                    period_metadata['financial_amount_rule'] = '仅amount_cells可选本来源已读明细行列及observation_choices中的字符串；不补金额，不授予登记方法口径。'
                    if _financial_amount_reference_enabled(task):
                        period_metadata.pop('financial_amount_tables')
                        result['evidence_contract']['sources'][-1].pop('financial_amount_tables', None)
                        period_metadata['financial_amount_choices'] = _financial_amount_choice_prompt(task, catalogue)
                        period_metadata['financial_amount_rule'] = (
                            'amount_cells只按本来源financial_amount_choices.reference填写amount_reference，'
                            '每条记录只填一个字符串引用；多项金额分别提交多条记录，不填列表或对象。'
                            '显式选择observation_choices中的观测日期；不填行号、表列或金额，'
                            '不补原件未披露值，不授予登记方法计算口径，仍逐项核验。')
            # The quote selector and decoder use question-local aliases when
            # the span projection is enabled. The batch's general contract
            # still has canonical hashes for financial selectors; expose this
            # source's exact alias catalogue rather than mixing both grammars.
            references = _span_need_references(task,quote['contract'])
            period_metadata['quote_need_choices'] = {qid:[dict(id=ref,text=need['text'])
                for ref,need in needs.items()] for qid,needs in references.items()}
            period_metadata['quote_need_link_rule'] = (
                '本来源selection.text_quotes的review.need_links只选择quote_need_choices中对应问题的id。'
                '不复制其他来源或通用契约的编号，不计算哈希；内核恢复完整需求，仍逐项核验。')
        result['source_choices'].append(dict(source_ref=item['source_ref'], selection_kind=item['kind'],
            artifact_id=view['id'], binding=binding, **period_metadata))
    return _financial_selector_projection(result, task)


def _decode_original_quote_selection(task, view, wire, description):
    if not _matches(wire, description['schema']):
        raise StructuredOutputError('invalid_original_quote_selection', '原件摘录不属于当前来源选择范围')
    source = description['source']
    requirements = {qid:{reference:need['text'] for reference,need in needs.items()}
        for qid,needs in _span_need_references(task,description['contract']).items()}
    specs, period_bases = [], {}
    periods = {period['reference']:period for period in description['reporting_period_choices']}
    for item in wire['text_quotes']:
        quote = description['refs'][item['quote_reference']]
        date = description['dates'][item['observation_anchor']]
        review = dict(question_ids=copy.deepcopy(item['question_ids']), allowed_natures=[item['nature']],
            conditions=['仅本任务当前原件的字面陈述与所选日期；不确认政策实际影响、首次公开、历史信息集或事件因果。'],
            reason='按实际读取原文及日期位置提交核验，不批准材料或投资判断。', temporal_use='current_version',
            evidence_need_texts=[dict(question_id=link['question_id'],
                texts=[requirements[link['question_id']][ident] for ident in link['need_ids']])
                for link in item['review']['need_links']])
        period = date['date']
        if periods and item['period_reference'] != 'observation_date':
            selected = periods[item['period_reference']]
            period = selected['period']; period_bases[item['id']] = selected['basis']
        specs.append(dict(id=item['id'], artifact_id=view['id'], object=item['object'],
            nature=item['nature'], question_ids=copy.deepcopy(item['question_ids']), period=period,
            text=quote['quote'], unit='不适用', availability=copy.deepcopy(source['current_availability_reference']),
            publication_mode='unknown_current_original', observation_date=date['date'],
            record_locator=dict(part=quote['part'], period_part=date['part'], period_quote=date['quote']), review=review))
    contract = description['contract']
    result = decode_material_extraction_output(dict(version=1, evidence_specs=specs),
        _original_extraction_schema(task, [view], contract=contract))['evidence_specs']
    for item in result:
        if item['id'] in period_bases:
            item['reporting_period_basis'] = copy.deepcopy(period_bases[item['id']])
    if wire.get('amount_cells'):
        result.extend(_decode_financial_amounts(task, [view], wire['amount_cells'],
                      description['source']['financial_amount_tables'], contract))
    return result


def independent_financial_selections(task):
    version = task.get('independent_financial_selections_version')
    if version is None:
        return False
    if type(version) is not int or version != 1:
        raise ContractError('财务原件逐项选择版本未登记')
    return True


def _financial_selector_frame_schema(shape, question_ids, *, isolate_address_scalars=False):
    """Keep authority/shape strict while testing bounded local assignments.

    This is only a rejection classifier, never a decoding or approval schema.
    An item admitted here but rejected by its real schema is discarded whole.
    Unlike a truncated diagnostic list, a full shape check cannot hide a later
    authority, type or unknown-field error behind many bad local references.
    """
    references = {'$.financial_context_reference', '$.shared_context_references.entity_id',
                  '$.shared_context_references.accounting_standard', '$.quote_reference',
                  '$.period_reference', '$.observation_reference', '$.observation_anchor',
                  '$.table_reference', '$.row_reference', '$.column_reference', '$.amount_reference'}
    needs = {'$.review.need_links.'+qid for qid in question_ids}
    need_addresses = {path+'[*]' for path in needs}
    if isolate_address_scalars:
        # Qualitative originals use links with an explicit question and local
        # need IDs. Only the IDs are selectors; the question is still authority.
        need_addresses.add('$.review.need_links[*].need_ids[*]')

    def visit(node, path='$'):
        result = copy.deepcopy(node)
        if path in references or path in need_addresses:
            result.pop('enum', None)
        if isolate_address_scalars and path in references and node.get('type') == 'string':
            # Rejection classification only: malformed scalar addresses are
            # discarded whole, never coerced to strings or used for lookup.
            # Authority fields, containers and need-link types stay strict.
            return {'anyOf': [{'type': kind} for kind in ('string', 'number', 'boolean', 'null')]}
        if path in needs and result.get('type') == 'array':
            result.update(minItems=0, maxItems=200)
            result.pop('uniqueItems', None)
        if 'anyOf' in result:
            result['anyOf'] = [visit(branch, path) for branch in result['anyOf']]
        if result.get('type') == 'object':
            result['properties'] = {key: visit(child, path+'.'+key)
                                    for key, child in result['properties'].items()}
        if result.get('type') == 'array':
            result['items'] = visit(result['items'], path+'[*]')
        return result

    return visit(shape)


def _decode_independent_financial_batch(wire, schema, task, choices):
    """Quarantine bad local selectors, not changed authority or source frames.

    The emptied arrays are used only to check the envelope. No submitted item
    is repaired: each accepted item must match its full original schema and
    still pass ordinary decoding, source and financial applicability checks.
    """
    address_policy = task.get('financial_address_isolation_version')
    if address_policy is not None and (type(address_policy) is not int or address_policy != 1):
        raise StructuredOutputError('invalid_original_batch', '财务引用隔离版本未登记')
    if not isinstance(wire, dict) or not isinstance(wire.get('sources'), list):
        raise StructuredOutputError('invalid_original_batch', '原件批次容器格式无效')
    frame = copy.deepcopy(wire)
    entries, identifiers, seen, submitted = [], [], set(), 0
    for index, entry in enumerate(wire['sources']):
        if (not isinstance(entry, dict) or entry.get('source_ref') not in choices
                or entry['source_ref'] in seen or not isinstance(entry.get('selection'), dict)):
            raise StructuredOutputError('invalid_original_batch', '原件批次来源或绑定无效、重复')
        seen.add(entry['source_ref'])
        choice = choices[entry['source_ref']]
        fields = tuple(field for field in ('financial_cells', 'text_quotes', 'amount_cells')
                       if field in choice['schema']['properties'])
        for field in fields:
            values = entry['selection'].get(field)
            shape = choice['schema']['properties'][field]
            if (not isinstance(values, list) or not shape.get('minItems', 0) <= len(values)
                    <= min(200, shape.get('maxItems', 200))):
                diagnostic = schema_diagnostic(entry['selection'], choice['schema'])
                diagnostic['selection_location'] = f'$.sources[{index}].selection.{field}'
                raise StructuredOutputError('invalid_original_batch', '原件选择列表格式或数量不符', diagnostic)
            submitted += len(values)
            identifiers.extend(item.get('id') for item in values if isinstance(item, dict))
            if address_policy == 1 or choice['kind'] == 'financial' or field == 'amount_cells':
                frame['sources'][index]['selection'][field] = []
                entries.extend((index, field, item, shape['items'], position)
                               for position, item in enumerate(values))
    if (submitted > 1000 or len(identifiers) != submitted
            or any(not isinstance(identifier, str) for identifier in identifiers)
            or len(set(identifiers)) != submitted
            or not _matches(frame, schema)):
        raise StructuredOutputError('invalid_original_batch', '原件批次权限、绑定、标识或容器无效',
                                    schema_diagnostic(wire, schema))
    filtered = copy.deepcopy(frame)
    rejected = []
    question_ids = [question['id'] for question in task['questions']]
    frame_schemas = {}
    for index, field, item, shape, position in entries:
        if _matches(item, shape):
            # All-null ownership is a local semantic rejection, not an
            # instruction to infer a question or erase other valid records.
            # Check only after the entire item shape and authority are valid.
            need_links = item['review']['need_links']
            if (_financial_question_owner_enabled(task) and isinstance(need_links, dict)
                    and not any(value is not None for value in need_links.values())):
                rejected.append(dict(location=f'$.sources[{index}].selection.{field}[{position}]',
                                     category='selection_decode_rejected'))
                continue
            filtered['sources'][index]['selection'][field].append(copy.deepcopy(item))
            continue
        shape_key = id(shape)
        if shape_key not in frame_schemas:
            frame_schemas[shape_key] = _financial_selector_frame_schema(shape, question_ids,
                isolate_address_scalars=address_policy == 1)
        classification = item
        if (_financial_amount_reference_enabled(task) and field == 'amount_cells'
                and isinstance(item, dict) and isinstance(item.get('amount_reference'), (list, dict))):
            # This copy only classifies a rejected local address. The original
            # item is discarded whole and never reaches decoding or adoption.
            # Its source, ownership, authority and every other field remain
            # subject to the complete rejection-frame check.
            classification = dict(item, amount_reference='invalid_local_amount_address')
        if not _matches(classification, frame_schemas[shape_key]):
            raise StructuredOutputError('invalid_original_batch', '财务选择含未登记字段、权限或结构，未采用',
                                        schema_diagnostic(wire, schema))
        rejected_item = dict(location=f'$.sources[{index}].selection.{field}[{position}]',
                             category='selector_schema_mismatch')
        if address_policy == 1:
            rejected_item['diagnostic'] = validated_selector_schema_diagnostic(
                schema_diagnostic(item, shape), [shape])
        rejected.append(rejected_item)
    specs = []
    for entry in filtered['sources']:
        choice = choices[entry['source_ref']]
        specs.extend(_decode_batch_selection(task, choice, entry['selection']))
    if len(specs) > 1000 or len({item['id'] for item in specs}) != len(specs):
        raise StructuredOutputError('invalid_original_batch_size', '展开后的原件标识重复或数量超限')
    result = dict(evidence_specs=specs)
    if rejected:
        diagnostic = dict(version=DETAILED_SELECTOR_DIAGNOSTIC_VERSION if address_policy == 1 else 1,
            schema_hash=fingerprint(schema),
            submitted_items=submitted, accepted_items=submitted-len(rejected), rejected_items=rejected,
            automatic_repair=False, evidence_approved=False, coverage_approved=False)
        # A decoder must emit the same contract its caller consumes. Validate
        # the detailed sidecar here as well as at the model gateway.
        result[ORIGINAL_SELECTION_REJECTIONS] = (validated_original_selection_rejections(diagnostic, schema)
            if address_policy == 1 else diagnostic)
    return result


def decode_original_batch(wire, schema, context):
    if not isinstance(context, tuple) or len(context)!=2:
        raise StructuredOutputError('missing_original_batch_context', '多原件选择缺少实际任务和读取绑定')
    task, views = context
    batch = _original_batch_choices(task, views)
    if batch is None or _original_batch_schema(task, batch) != schema:
        raise StructuredOutputError('source_binding_changed', '多原件选择绑定已变化')
    choices = {item['source_ref']: item for item in batch}
    if independent_financial_selections(task):
        return _decode_independent_financial_batch(wire, schema, task, choices)
    if not _matches(wire, schema):
        raise StructuredOutputError('invalid_original_batch', '多原件选择不在当前候选范围', schema_diagnostic(wire, schema))
    seen, specs = set(), []
    for entry in wire['sources']:
        ref = entry['source_ref']
        if ref in seen:
            raise StructuredOutputError('duplicate_original_source', '同一原件重复提交，未合并提案')
        seen.add(ref)
        specs.extend(_decode_batch_selection(task, choices[ref], entry['selection']))
    if len(specs)>1000 or len({spec['id'] for spec in specs})!=len(specs):
        raise StructuredOutputError('invalid_original_batch_size', '多原件提案数量超限或标识重复')
    return dict(evidence_specs=specs)


def _decode_batch_selection(task, choice, wire):
    if choice['kind'] == 'financial':
        return _decode_financial_reference_selection(wire, choice['schema'], task,
            [choice['view']], choice['selector'])['evidence_specs']
    return _decode_original_quote_selection(task, choice['view'], wire, choice['selector'])


def _original_number_tokens(lines, source_index, *, compact=False):
    """Literal addresses only. Date and footnote numbers remain distinguishable."""
    from .financial_sources import _AMOUNT_TOKEN
    pattern = r'(?<![0-9A-Za-z_.,])' + _AMOUNT_TOKEN + r'(?![0-9A-Za-z_.,])'
    tokens = [dict(ref=f'num{source_index}_{line["index"]}_{ordinal}', line=line['index'], token=match.group())
              for line in lines for ordinal, match in enumerate(re.finditer(pattern, line['text']))]
    if compact:
        for index, token in enumerate(tokens, 1):
            token['ref'] = f'n{source_index}_{index}'
    return tokens


def _adjacent_quantity_units(choice):
    """Literal suffixes only, no unit guessing, conversion or source approval."""
    from .financial_sources import _AMOUNT_TOKEN
    pattern = r'(?<![0-9A-Za-z_.,])' + _AMOUNT_TOKEN + r'(?![0-9A-Za-z_.,])'
    by_line = {}
    for token in choice['number_tokens']:
        by_line.setdefault(token['line'], []).append(token)
    result = {}
    for line in choice['lines']:
        matches = list(re.finditer(pattern, line['text']))
        tokens = by_line.get(line['index'], [])
        if len(matches) != len(tokens):
            raise ContractError('数字单位定位与原文地址不一致，不推断单位')
        for match, token in zip(matches, tokens):
            if match.group() != token['token']:
                raise ContractError('数字单位定位不是相同逐字原值')
            suffix = re.match(r'[ \t\u00a0\u3000]*(个百分点|百分点|%)(?![ \t\u00a0\u3000]*[/／])', line['text'][match.end():])
            if suffix:
                result[token['ref']] = dict(unit=suffix.group(1), line=token['line'])
    return result


def _literal_quantity_selection(task, choices):
    return (type(task.get('literal_quantity_reference_version')) is int
            and task['literal_quantity_reference_version'] in (1, 2, 3, 4, 5) and _general_original_selection(task)
            and task['general_original_selection_version'] == 2
            and task.get('original_number_reference_version') == 1
            and all(choice['kind'] == 'original' for choice in choices))


def _quantity_index(choice):
    return {index: dict(value, number_ref=ref)
            for index, (ref, value) in enumerate(_adjacent_quantity_units(choice).items(), 1)}


def _named_quantity_index(choice):
    """Opaque source-local names, distinct from values, line numbers and tokens."""
    return {'v' + str(index): value for index, value in _quantity_index(choice).items()}


def _explicit_original_unit_references(lines):
    """Literal unit addresses, never automatic number-to-header assignment.

    A model selects both the numeric address and the unit address. Scope,
    financial columns, currency and suitability still require their checks.
    Longest literal units are matched first, so 万元 is never shortened to 元.
    The incumbent free-unit branch remains available for other literal units.
    """
    from .financial_sources import _AMOUNT_UNITS
    units = sorted((*_AMOUNT_UNITS, 'GWh', 'MWh', 'kWh', '个百分点', '百分点', '%'), key=len, reverse=True)
    pattern = re.compile(r'(?<![A-Za-z百千万亿])(?:'+
                         '|'.join(map(re.escape, units))+r')(?![A-Za-z]|[ \t\u00a0\u3000]*[/／])')
    result = []
    for line in lines:
        for match in pattern.finditer(line['text']):
            unit, prefix = match.group(), line['text'][:match.start()]
            if re.search(r'[/／][ \t\u00a0\u3000]*$', prefix):
                continue
            # Do not strip 元 from an unregistered currency name. RMB is an
            # explicit literal prefix, not a currency inferred for the value.
            if unit in _AMOUNT_UNITS and re.search(r'[\u4e00-\u9fff]$', prefix) and not prefix.endswith('人民币'):
                continue
            result.append(dict(ref='u'+str(len(result)+1),line=line['index'],unit=unit))
    return result


def _span_context_value(field, source):
    from .evidence import MATERIAL_CONTEXT_TOKENS
    values = list(MATERIAL_CONTEXT_TOKENS.get(field, {}))
    binding = source.get('object_binding')
    code = binding.get('value') if isinstance(binding, dict) else None
    if field in ('fund_id', 'security_id') and isinstance(code, str) and re.fullmatch(r'\d{6}\.(?:SH|SZ)', code):
        values = [code.split('.')[0]]
    if field == 'share_class' and source.get('listed_etf_unit_candidates'):
        values = list(dict.fromkeys(item['value'] for item in source['listed_etf_unit_candidates']))
    return {'type': 'string', 'enum': values} if values else {'type': 'string', 'minLength': 1}


def _original_span_choices(task, views):
    """Finite read positions for methods without an existing typed selector."""
    from .contracts import DOCUMENT_CONTENT_INTENT, FINANCIAL_PERIOD_METHODS
    from .financial_sources import original_line_index
    methods = {method for question in task.get('questions', []) for method in question.get('method_needs', [])}
    methods.update(item.get('method') for item in task.get('calculation_specs', []))
    methodless = (_general_original_selection(task) and isinstance(views, list)
                  and all(isinstance(view, dict) and view.get('kind') in ('material', 'filing') for view in views))
    if (type(task.get('original_span_selection_version')) is not int
            or task['original_span_selection_version'] != 1 or not task.get('argument_support_contract')
            or task.get('research_mode') != 'current' or task.get('intent') == DOCUMENT_CONTENT_INTENT
            or not methods and not methodless or not methods.issubset(METHOD_REGISTRY)
            or methods.intersection(FINANCIAL_PERIOD_METHODS)
            or any(method.startswith('management_custody_fee_') for method in methods)
            or not isinstance(views, list) or not 1 <= len(views) <= 12
            or any(view.get('kind') not in ('api', 'filing', 'material') for view in views)
            or len({view.get('id') for view in views}) != len(views)):
        return None
    mixed = any(view.get('kind') == 'api' for view in views)
    from .etf_sources import has_api_share_context
    api_share_context = ('etf_daily_nav_premium_v1' in methods and has_api_share_context(task, views))
    if mixed and (task.get('mixed_original_selection_version') != 1
                  or not any(view.get('kind') in ('filing', 'material') for view in views) and not api_share_context):
        return None
    choices = []
    for index, view in enumerate(views, 1):
        if view.get('kind') == 'api':
            from .reasoning import evidence_contract
            api_schema = api_extraction_output_schema(task, [view])
            contract = evidence_contract(task, [view])
            if api_schema is None or len(contract['sources']) != 1:
                return None
            choices.append(dict(source_ref='source_' + str(index), view=view, kind='api',
                api_schema=api_schema, source=contract['sources'][0]))
            continue
        description = _original_quote_selector(task, view, _document_dates=True)
        lines = original_line_index(view)
        if description is None or not lines:
            return None
        choice = dict(source_ref='source_' + str(index), view=view, kind='original',
                      lines=lines, description=description)
        if type(task.get('original_period_selection_version')) is int and task['original_period_selection_version'] == 1:
            from .financial_sources import original_period_anchors
            interval_context = (type(task.get('original_interval_context_version')) is int
                                and task['original_interval_context_version'] == 1)
            choice['period_anchors'] = original_period_anchors(view, description['dates'],
                representation_version=2 if interval_context else 1)
            if task.get('original_duration_anchor_version')==1:
                from .financial_sources import original_reporting_intervals
                choice['period_anchors']=original_reporting_intervals(view,description['dates'])
        if (type(task.get('original_observation_selection_version')) is int
                and task['original_observation_selection_version'] == 1):
            from .evidence import original_date_is_nonobservational, _original_download_profile
            records = [record for record in task.get('source_records', []) if record.get('id') == view.get('id')
                       and record.get('snapshot_hash', record.get('hash')) == view.get('snapshot_hash')]
            if len(records) != 1:
                return None
            profile = _original_download_profile(records[0])
            if profile is None:
                return None
            parts = {part['locator']: part['text'] for part in profile['material']['parts']}
            choice['observation_indices'] = [index for index, date in enumerate(description['dates'])
                if not date.get('date_role') and ('observation_period' in date or not original_date_is_nonobservational(
                    parts.get(date['part'], ''), date['quote'], date['date']))]
            if 'period_anchors' in choice:
                choice['period_anchors'] = [period for period in choice['period_anchors']
                    if period['end_anchor'] in choice['observation_indices']]
            if task.get('original_duration_anchor_version')==1:
                from .financial_sources import original_date_is_interval_boundary
                choice['observation_indices']=[i for i in choice['observation_indices']
                    if 'observation_period' in description['dates'][i] or
                    not description['dates'][i].get('interval_only') and not original_date_is_interval_boundary(
                        parts.get(description['dates'][i]['part'],''),description['dates'][i]['quote'],description['dates'][i]['date'])]
        if type(task.get('original_number_reference_version')) is int and task['original_number_reference_version'] == 1:
            choice['number_tokens'] = _original_number_tokens(lines, index, compact=(
                _general_original_selection(task) and task['general_original_selection_version'] == 2
                or _method_span_projection(task)))
            capacity_policy = task.get('original_number_catalogue_capacity_version')
            if capacity_policy is not None and (type(capacity_policy) is not int or capacity_policy != 1):
                raise ContractError('原文数字目录容量版本未登记')
            maximum = 8192 if capacity_policy == 1 else 2000
            if len(choice['number_tokens']) > maximum:
                if capacity_policy == 1:
                    raise ContractError('实际读取的数字条目超过8192项，请选择更明确的读取范围；不截断数字或回退旧格式')
                return None  # Preserve legacy behavior, never truncate a token catalogue.
            unit_policy = task.get('original_explicit_unit_reference_version')
            if unit_policy is not None and (type(unit_policy) is not int or unit_policy != 1):
                raise ContractError('原文字面单位引用版本未登记')
            if unit_policy == 1:
                choice['unit_references'] = _explicit_original_unit_references(lines)
        if any(date.get('date_role') for date in description['dates']):
            choice['observation_indices'] = [index for index in choice.get('observation_indices', range(len(description['dates'])))
                                             if not description['dates'][index].get('date_role')]
        choices.append(choice)
    return choices


def _local_span_selection(task):
    return (type(task.get('original_local_span_version')) is int
            and task['original_local_span_version']==1 and _method_span_projection(task))


def _whole_part_selection(task):
    return (_local_span_selection(task) and type(task.get('original_whole_part_selection_version')) is int
            and task['original_whole_part_selection_version']==1)


def _span_fragments(choice):
    fragments={}
    for line in choice['lines']:
        fragments.setdefault(line['fragment_index'],[]).append(line)
    return fragments


def _original_span_schema(task,choice):
    if not _local_span_selection(task):
        index=dict(type='integer',minimum=0,maximum=len(choice['lines'])-1)
        return object_schema(dict(start=index,end=copy.deepcopy(index)))
    fragments=_span_fragments(choice)
    index=dict(type='integer',minimum=0,maximum=max(map(len,fragments.values()))-1)
    precise=object_schema(dict(part=dict(type='integer',enum=list(fragments)),
                               start=index,end=copy.deepcopy(index)))
    if _whole_part_selection(task):
        complete=[part for part,lines in fragments.items() if len(lines)<=200]
        if complete:
            return {'anyOf':[object_schema(dict(part_ref=dict(type='integer',enum=complete))),precise]}
    return precise


def _expand_local_span(choice,span):
    """A page namespace cannot address lines in its following page."""
    if isinstance(span,dict) and set(span)=={'part_ref'}:
        if type(span['part_ref']) is not int:
            raise ContractError('页段引用须选择实际整数编号')
        lines=_span_fragments(choice).get(span['part_ref'],[])
        if not lines or len(lines)>200:
            raise ContractError('整段引用不存在或超过原200行上限，须显式选择局部范围')
        return dict(start=lines[0]['index'],end=lines[-1]['index'])
    if (not isinstance(span,dict) or set(span)!={'part','start','end'}
            or any(type(span[key]) is not int for key in span)):
        raise ContractError('原件范围须显式选择页段序号及局部起止行')
    lines=_span_fragments(choice).get(span['part'],[])
    if not 0<=span['start']<=span['end']<len(lines) or span['end']-span['start']>=200:
        raise ContractError('局部原文范围超出所选页段，不跨页、不裁剪或补齐')
    return dict(start=lines[span['start']]['index'],end=lines[span['end']]['index'])


def _local_span_address(choice,span):
    from .financial_sources import selected_original_span
    selected_original_span(choice['lines'],span)
    first=choice['lines'][span['start']]
    rows=_span_fragments(choice)[first['fragment_index']]
    return dict(part=first['fragment_index'],start=span['start']-rows[0]['index'],
                end=span['end']-rows[0]['index'])


def _missing_api_context_variants(choice, choices, fields, number_references, task):
    """Group real selectable cells by object and existing keys, not API hints.

    The API catalogue contains the exact context restored by the decoder.
    Its public row-field hints are broader and cannot decide what is missing.
    An addition applies to every selected cell, so incompatible groups must
    remain separate submissions. No candidate is dropped or auto-selected.
    """
    from .etf_sources import share_context_choices
    records = {record['id']: record for record in task.get('source_records', [])}
    allowed_ids = {item['view']['id'] for item in choices if 'view' in item}
    grouped, shared = {}, {}
    for cell in choice['source']['cell_choices']:
        additions = (share_context_choices(task, records[choice['view']['id']],
            cell['record_locator'], cell['object'], allowed_ids=allowed_ids)
            if 'share_class' in fields and 'share_class' not in cell['calculation_context']
            and choice.get('view', {}).get('id') in records else [])
        refs = tuple(addition['context_ref'] for addition in additions)
        shared.update((addition['context_ref'], addition) for addition in additions)
        key = (cell['object'], tuple(sorted(cell['calculation_context'])), refs)
        grouped.setdefault(key, []).append(cell['candidate_id'])
    result = []
    for (object_code, existing_fields, shared_refs), identifiers in grouped.items():
        selection = copy.deepcopy(choice['api_schema'])
        inner = selection['properties']['evidence_specs']['items']
        options = []
        for option in inner['anyOf']:
            enum = option['properties']['api_cell_selections']['items']['enum']
            enum[:] = [ident for ident in enum if ident in identifiers]
            if enum:
                options.append(option)
        if not options:
            raise ContractError('接口单元分组与实际可选目录不一致，不省略来源')
        inner['anyOf'] = options
        missing = [field for field in fields if field not in existing_fields
                   and field not in ('nav_kind', 'price_kind', 'level_kind', 'metric')]
        contexts = []
        for ref in shared_refs:
            option = object_schema({'context_ref': _literal_schema(ref)})
            option['description'] = ('显式采用同代码、同日上交所清单的share_class=' + shared[ref]['value']
                + '；只补上市份额，不改变原值、币种、日期或其他价格口径。')
            contexts.append(option)
        for original in choices:
            if original['kind'] != 'original':
                continue
            source = original['description']['source']
            binding = source.get('object_binding')
            if not binding or binding['value'] != object_code:
                continue
            span = _original_span_schema(task,original)
            for field in missing:
                contexts.append(object_schema({'source_ref': {'type': 'string', 'enum': [original['source_ref']]},
                    'field': {'type': 'string', 'enum': [field]},
                    'value': _span_context_value(field, source) if number_references else {'type': 'string', 'minLength': 1},
                    'span': span}))
        additions = array_schema({'anyOf': contexts}) if contexts else dict(array_schema(object_schema({})), maxItems=0)
        result.append(object_schema({'source_ref': {'type': 'string', 'enum': [choice['source_ref']]},
            'kind': {'type': 'string', 'enum': ['api']}, 'selection': selection, 'additional_contexts': additions}))
    return result


def _api_context_selection_guide(task, choices, schema):
    """Expose exact existing context choices beside their eligible API cells.

    This is a prompt index, not a new grammar or an adopted relationship. It
    cannot offer a reference absent from this source's actual Schema branch.
    """
    if not isinstance(schema,dict) or not choices:
        return []
    from .etf_sources import share_context_choices
    records={record['id']:record for record in task.get('source_records',[])}
    allowed_ids={choice['view']['id'] for choice in choices if 'view' in choice}
    sources={choice['source_ref']:choice for choice in choices if choice['kind']=='api'}
    source_refs={choice['view']['id']:choice['source_ref'] for choice in choices if 'view' in choice}
    variants=schema.get('properties',{}).get('selections',{}).get('items',{}).get('anyOf',[])
    rows=[]
    for variant in variants:
        fields=variant.get('properties',{})
        if fields.get('kind',{}).get('enum')!=['api']:
            continue
        options=fields.get('additional_contexts',{}).get('items',{}).get('anyOf',[])
        refs={ref for option in options for ref in option.get('properties',{}).get('context_ref',{}).get('enum',[])}
        if not refs:
            continue
        spec_options=fields['selection']['properties']['evidence_specs']['items'].get('anyOf',[])
        ids={ident for option in spec_options for ident in
            option.get('properties',{}).get('api_cell_selections',{}).get('items',{}).get('enum',[])}
        for source_ref in fields['source_ref']['enum']:
            choice=sources[source_ref];record=records[choice['view']['id']]
            for cell in choice['source'].get('cell_choices',[]):
                if cell['candidate_id'] not in ids:
                    continue
                available=share_context_choices(task,record,cell['record_locator'],cell['object'],allowed_ids=allowed_ids)
                for context in available:
                    if context['context_ref'] not in refs:
                        continue
                    row=dict(source_ref=source_ref,api_cell_selections=[cell['candidate_id']],
                        additional_contexts=[dict(context_ref=context['context_ref'])],
                        supplied_field=context['field'],supplied_value=context['value'],
                        basis_source_ref=source_refs[context['basis']['artifact_id']],period=cell['period'])
                    if row not in rows:
                        rows.append(row)
    return rows


def fee_context_mapping_enabled(task):
    """An explicit complete fee selection, not inferred financial context."""
    policy = task.get('original_fee_context_mapping_version')
    if policy is None:
        return False
    if type(policy) is not int or policy != 1:
        raise ContractError('原文费率口径映射版本未登记')
    methods = {method for question in task.get('questions', []) for method in question.get('method_needs', [])}
    methods.update(item['method'] for item in task.get('calculation_specs', []))
    return (methods == {'fee_difference_v2'} and task.get('intent') != '原件内容核对'
            and type(task.get('original_interval_context_version')) is int
            and task['original_interval_context_version'] == 1)


def _fee_context_mapping_schema(variants):
    """Name every required field once; keep its exact source selections."""
    required = set(METHOD_REGISTRY['fee_difference_v2']['binding_context'])
    for variant in variants:
        properties = variant['properties']
        if properties['kind']['enum'] != ['number']:
            continue
        shape = properties.get('contexts', {})
        grouped = {}
        for entry in shape.get('items', {}).get('anyOf', []):
            fields = entry.get('properties', {}).get('field', {}).get('enum', [])
            if len(fields) != 1:
                raise ContractError('费率口径须分别登记实际字段，不接受未定字段集合')
            selected = {key:copy.deepcopy(value) for key,value in entry['properties'].items() if key != 'field'}
            grouped.setdefault(fields[0], []).append(object_schema(selected))
        if set(grouped) != required:
            raise ContractError('费率口径选择缺少登记字段，不补造基金身份或其他条件')
        properties['contexts'] = object_schema({field: entries[0] if len(entries)==1 else {'anyOf':entries}
            for field,entries in grouped.items()})


def fee_input_completion_binding(task, views):
    """Bind a numeric-only initial pass to the exact prior textual proposal."""
    state = task.get('reasoning_state', {})
    required=(type(task.get('required_numeric_completion_version')) is int
        and task['required_numeric_completion_version']==1
        and state.get('required_numeric_completion',{}).get('status')=='已派发')
    policy = task.get('required_numeric_completion_version') if required else task.get('initial_fee_input_completion_version')
    if policy is None:
        return None
    if type(policy) is not int or policy != 1:
        raise ContractError('初始费率数值输入提取版本未登记')
    completion = state.get('required_numeric_completion' if required else 'fee_input_completion', {})
    if completion.get('status') != '已派发':
        return None
    from .contracts import context
    first_hash = completion.get('first_specs_hash')
    extraction = state.get('extraction', {})
    if (not (required or fee_context_mapping_enabled(task)) or type(completion.get('version')) is not int
            or completion['version'] != 1 or completion.get('binding') != context(task)
            or completion.get('views_hash') != fingerprint(views)
            or not isinstance(first_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', first_hash)
            or extraction.get('evidence_specs_hash') != first_hash
            or first_hash != fingerprint(task.get('host_result', {}).get('evidence_specs'))
            or completion.get('input_digest') != extraction.get('input_digest')
            or completion.get('id_prefix') != 'input_' + first_hash[:10] + '_'):
        raise ContractError('数值输入提取与初始提案、原件或任务绑定不一致')
    if required:
        from .extraction_feedback import completion_scope
        scope = completion_scope(task, task['host_result']['evidence_specs'], views)
        if (completion.get('scope')!='required_numeric' or completion.get('missing_numeric_needs')!=
                scope['eligible']):
            raise ContractError('必要数字补提的缺口与初次已提交提案不一致，未扩大选择范围')
        if scope['feedback_hash'] is not None and any(completion.get(key) != scope[field] for key, field in (
                ('extraction_feedback_hash', 'feedback_hash'), ('all_missing_numeric_needs', 'all_missing'),
                ('deferred_numeric_needs', 'deferred'))):
            raise ContractError('数值补提的反馈、原需求或暂缓范围已变化')
    return {key: copy.deepcopy(completion[key]) for key in
            ('version', 'input_digest', 'views_hash', 'first_specs_hash', 'id_prefix',
             *(('scope','missing_numeric_needs') if required else ()),
             *(('extraction_feedback_hash','all_missing_numeric_needs','deferred_numeric_needs')
               if required and scope['feedback_hash'] is not None else ()))}


def original_span_output_schema(task, views, *, _period_selection=True):
    if type(task.get('nav_original_choice_version')) is int and task['nav_original_choice_version'] == 1:
        from .original_choices import output_schema
        selected = output_schema(task, views)
        if selected is not None:
            return selected
    from .contracts import context
    choices = _original_span_choices(task, views)
    if choices is None:
        return None
    number_references = (type(task.get('original_number_reference_version')) is int
                         and task['original_number_reference_version'] == 1)
    methods = {method for question in task['questions'] for method in question.get('method_needs', [])}
    methods.update(item['method'] for item in task.get('calculation_specs', []))
    fields = sorted({field for method in methods for combined in METHOD_REGISTRY[method].get('binding_context', [])
                     for field in combined.split('或')})
    from .contracts import calculation_context_contract
    for method in methods:
        role_contract = calculation_context_contract(task, method)
        if role_contract:
            fields = sorted(set(fields).union(field for required in role_contract['input_fields'].values() for field in required))
    variants = []
    for choice in choices:
        if choice['kind'] == 'api':
            if (type(task.get('missing_api_context_selection_version')) is int
                    and task['missing_api_context_selection_version'] == 1
                    and task.get('original_context_selection_version') == 1
                    and choice['api_schema']['properties']['version']['enum'] in ([2],[3])):
                variants.extend(_missing_api_context_variants(choice, choices, fields, number_references,task))
                continue
            api = {'source_ref': {'type': 'string', 'enum': [choice['source_ref']]},
                   'kind': {'type': 'string', 'enum': ['api']}, 'selection': choice['api_schema']}
            if task.get('original_context_selection_version') == 1:
                contexts = []
                if choice['api_schema']['properties']['version']['enum'] in ([2],[3]):
                    for original in choices:
                        if original['kind'] != 'original' or not original['description']['source'].get('object_binding'):
                            continue
                        span = _original_span_schema(task,original)
                        additional_fields = [field for field in fields
                                             if field not in ('nav_kind', 'price_kind', 'level_kind', 'metric')]
                        if not additional_fields:
                            continue
                        for selected_fields in ([[field] for field in additional_fields] if number_references else [additional_fields]):
                            contexts.append(object_schema({'source_ref': {'type': 'string', 'enum': [original['source_ref']]},
                                'field': {'type': 'string', 'enum': selected_fields},
                                'value': (_span_context_value(selected_fields[0], original['description']['source'])
                                          if number_references else {'type': 'string', 'minLength': 1}), 'span': span}))
                api['additional_contexts'] = (array_schema({'anyOf': contexts}) if contexts
                                              else dict(array_schema(object_schema({})), maxItems=0))
            variants.append(object_schema(api))
            continue
        properties = choice['description']['schema']['properties']['text_quotes']['items']['properties']
        span = _original_span_schema(task,choice)
        base = {key: copy.deepcopy(properties[key]) for key in ('id', 'object', 'nature', 'question_ids', 'review', 'observation_anchor')}
        base.update(source_ref={'type': 'string', 'enum': [choice['source_ref']]}, text_span=span,
                    period={'type': 'string', 'minLength': 1})
        if all('observation_period' in anchor for anchor in choice['description']['dates']):
            base['period'] = {'type': 'string', 'enum': sorted({
                anchor['observation_period']['value'] for anchor in choice['description']['dates']})}
        text_choice = dict(base, kind={'type': 'string', 'enum': ['text']})
        if _literal_quantity_selection(task, choices) and task['literal_quantity_reference_version'] >= 2:
            text_choice.pop('text_span')
            point = object_schema({'line': {'type': 'integer', 'minimum': 0, 'maximum': len(choice['lines']) - 1}})
            text_choice['text_reference'] = {'anyOf': [
                object_schema({'part': {'type': 'string', 'minLength': 1, 'maxLength': 200}}),
                point if task['literal_quantity_reference_version'] >= 3 else object_schema({'span': span})]}
        variants.append(object_schema(text_choice))
        if number_references and not choice['number_tokens']:
            continue
        numeric = dict(base, kind={'type': 'string', 'enum': ['number']},
            number_occurrence={'type': 'integer', 'minimum': 0, 'maximum': 1999},
            unit={'type': 'string', 'minLength': 1}, unit_span=span,
            contexts=(array_schema(object_schema({'field': {'type': 'string', 'enum': fields},
                'value': {'type': 'string', 'minLength': 1}, 'span': span})) if fields
                      else dict(array_schema(object_schema({})), maxItems=0)))
        if number_references:
            numeric.pop('number_occurrence')
            numeric['number_ref'] = {'type': 'string', 'enum': [token['ref'] for token in choice['number_tokens']]}
            if (_general_original_selection(task) and task['general_original_selection_version'] == 2
                    or _method_span_projection(task)):
                # The prompt retains every finite address. The schema encodes
                # only its source namespace; the decoder still requires an
                # actual token inside the selected source and original span.
                prefix = choice['number_tokens'][0]['ref'].split('_')[0]
                numeric['number_ref'] = {'type': 'string', 'pattern': re.escape(prefix) + r'_[1-9][0-9]{0,3}'}
            if fields:
                numeric['contexts'] = array_schema({'anyOf': [object_schema({
                    'field': {'type': 'string', 'enum': [field]},
                    'value': _span_context_value(field, choice['description']['source']), 'span': span}) for field in fields]})
                unit_refs = [row['ref'] for row in choice['description']['source'].get('listed_etf_unit_candidates',[])
                             if 'ref' in row]
                if (unit_refs and type(task.get('listed_report_unit_context_version')) is int
                        and task['listed_report_unit_context_version'] == 1):
                    for index,entry in enumerate(numeric['contexts']['items']['anyOf']):
                        if entry['properties']['field']['enum']==['share_class']:
                            numeric['contexts']['items']['anyOf'][index] = object_schema({
                                'field':{'type':'string','enum':['share_class']},
                                'unit_ref':{'type':'string','enum':unit_refs}})
        if _literal_quantity_selection(task, choices):
            quantities = _adjacent_quantity_units(choice)
            selection = {key: numeric.pop(key) for key in ('number_ref', 'unit', 'unit_span', 'contexts')}
            if task['literal_quantity_reference_version'] == 2:
                selection['text_span'] = numeric.pop('text_span')
            elif task['literal_quantity_reference_version'] >= 3:
                numeric.pop('text_span')
                selection.pop('unit_span')
                selection.pop('contexts')
                selection['unit_reference'] = copy.deepcopy(text_choice['text_reference'])
            alternatives = [object_schema(selection)]
            if quantities:
                # The full finite catalogue is in the prompt and checked again
                # by the decoder. Repeat only the source namespace here, as
                # for ordinary compact number references, not every address.
                if task['literal_quantity_reference_version'] == 5:
                    reference = {'type': 'string', 'enum': list(_named_quantity_index(choice))}
                elif task['literal_quantity_reference_version'] == 4:
                    reference = {'type': 'integer', 'minimum': 1, 'maximum': len(quantities)}
                else:
                    prefix = next(iter(quantities)).split('_')[0]
                    reference = {'type': 'string', 'pattern': re.escape(prefix) + r'_[1-9][0-9]{0,3}'}
                alternatives.insert(0, object_schema({'quantity_ref': reference}))
            if choice.get('unit_references') and task['literal_quantity_reference_version'] >= 3:
                alternatives.insert(0, object_schema({
                    'number_ref': copy.deepcopy(selection['number_ref']),
                    'unit_ref': {'type':'string','enum':[unit['ref'] for unit in choice['unit_references']]}}))
            numeric['number_reference'] = {'anyOf': alternatives} if len(alternatives) > 1 else alternatives[0]
        separate_contexts = (type(task.get('original_interval_context_version')) is int
                             and task['original_interval_context_version'] == 1)
        if (number_references and (not fields or separate_contexts) and _method_span_projection(task)
                and type(task.get('original_period_selection_version')) is int
                and task['original_period_selection_version'] == 1
                and type(task.get('method_literal_quantity_version')) is int
                and task['method_literal_quantity_version'] == 1):
            quantities = _adjacent_quantity_units(choice)
            if quantities:
                prefix=choice['number_tokens'][0]['ref'].split('_')[0]
                explicit={key:numeric.pop(key) for key in ('number_ref','unit','unit_span','text_span')}
                if not separate_contexts:
                    explicit['contexts']=numeric.pop('contexts')
                literal = {'literal_ref':{'type':'string','pattern':re.escape(prefix)+r'_[1-9][0-9]{0,3}'}}
                if (type(task.get('method_literal_address_version')) is int
                        and task['method_literal_address_version']==1):
                    # Only actual same-source addresses with literal units
                    # belong to this shorthand. Other numbers remain
                    # available through the explicit unit/span branch.
                    literal['literal_ref']={'type':'string','enum':list(quantities)}
                if (type(task.get('method_excerpt_selection_version')) is int
                        and task['method_excerpt_selection_version']==1):
                    literal['text_span']=copy.deepcopy(span)
                numeric['number_reference']={'anyOf':[
                    object_schema(literal),
                    object_schema(explicit)]}
        variants.append(object_schema(numeric))
    completion_binding = fee_input_completion_binding(task, views)
    binding = fingerprint(dict(task=context(task), views=views,
        **({'initial_fee_input_completion':completion_binding} if completion_binding else {})))
    version = 2 if any(choice['kind'] == 'api' for choice in choices) else 1
    if version == 2 and task.get('original_context_selection_version') == 1:
        version = 3
    if number_references:
        version = 4
    if number_references and _literal_quantity_selection(task, choices):
        version = 5 + task['literal_quantity_reference_version']
    if (version == 10 and type(task.get('original_question_link_version')) is int
            and task['original_question_link_version'] == 1):
        # The model declares each question only once, together with its needs.
        # Canonical question_ids are restored from this explicit association;
        # unknown/empty links and duplicate declarations are not repaired.
        version = 11
        for variant in variants:
            properties = variant['properties']
            properties.pop('question_ids')
            variant['required'].remove('question_ids')
            links = properties['review']['properties']['need_links']
            links.update(minItems=1, uniqueItems=True)
            for need in links['items']['anyOf']:
                need['properties']['need_ids'].update(minItems=1, uniqueItems=True)
    if (_period_selection and type(task.get('original_period_selection_version')) is int
            and task['original_period_selection_version'] == 1):
        version = 12
        by_source = {choice['source_ref']: choice for choice in choices}
        metadata_variants, unavailable = [], set()
        for variant in variants:
            properties = variant['properties']
            choice = by_source[properties['source_ref']['enum'][0]]
            if choice['kind'] != 'original':
                continue
            if (type(task.get('original_context_question_link_version')) is int
                    and task['original_context_question_link_version'] == 1 and 'question_ids' in properties):
                # Context-bearing methods must not declare the same ownership
                # twice. Restore only explicit need_links; never add a need.
                properties.pop('question_ids')
                links = properties['review']['properties']['need_links']
                links.update(minItems=1, uniqueItems=True)
                for need in links['items']['anyOf']:
                    need['properties']['need_ids'].update(minItems=1, uniqueItems=True)
            if properties['kind']['enum'] == ['text'] and 'text_reference' in properties:
                from .document_dates import ROLE
                for declared in document_date_choices(choice):
                    selected = copy.deepcopy(variant)
                    fields = selected['properties']
                    fields.pop('observation_anchor', None); fields.pop('period', None)
                    fields['date_role'] = {'type': 'string', 'enum': [ROLE]}
                    fields['nature'] = {'type': 'string', 'enum': ['已实现事实']}
                    fields['temporal_reference'] = object_schema(dict(document_date_ref={
                        'type': 'string', 'enum': [declared['reference']]}))
                    fields['temporal_reference'] = {'anyOf': [fields['temporal_reference']]}
                    fields['text_reference'] = object_schema(dict(part={'type': 'string', 'enum': [declared['part']]}))
                    selected['required'] = list(fields)
                    metadata_variants.append(selected)
                labels = source_label_choices(task, choice)
                if labels:
                    fields = {key: copy.deepcopy(properties[key]) for key in
                              ('id', 'kind', 'source_ref', 'review', 'question_ids') if key in properties}
                    fields['source_label_ref'] = {'type': 'string', 'enum': [row['reference'] for row in labels]}
                    metadata_variants.append(object_schema(fields))
            observation = properties.pop('observation_anchor')
            if 'observation_indices' in choice:
                observation['enum'] = choice['observation_indices']
            temporal = [object_schema({'observation_anchor': observation})] if observation['enum'] else []
            if task.get('original_observation_reference_version')==1 and observation['enum']:
                temporal=[object_schema({'observation_ref':{'type':'string','enum':[
                    entry['reference'] for entry in original_observation_choices(choice)]}})]
            periods = choice['period_anchors']
            if periods:
                temporal.append(object_schema({'period_ref': {'type':'string', 'enum':[p['ref'] for p in periods]}}))
                if (properties['kind']['enum'] == ['number']
                        and type(task.get('original_interval_context_version')) is int
                        and task['original_interval_context_version'] == 1):
                    containers = [properties]
                    if 'number_reference' in properties:
                        containers += [branch['properties'] for branch in properties['number_reference'].get('anyOf', [])]
                    for container in containers:
                        context_shape = container.get('contexts')
                        if not context_shape or 'anyOf' not in context_shape.get('items', {}):
                            continue
                        for index, entry in enumerate(context_shape['items']['anyOf']):
                            if entry['properties']['field']['enum'] == ['period']:
                                context_shape['items']['anyOf'][index] = object_schema({
                                    'field': {'type':'string', 'enum':['period']},
                                    'period_ref': {'type':'string', 'enum':[p['ref'] for p in periods]}})
            if not temporal:
                unavailable.add(id(variant))
                continue
            properties.pop('period')
            properties['temporal_reference'] = {'anyOf': temporal}
            variant['required'] = list(properties)
        variants = [variant for variant in variants if id(variant) not in unavailable] + metadata_variants
    if fee_context_mapping_enabled(task):
        _fee_context_mapping_schema(variants)
    from .data_requirements import constrain_original_variants
    variants=constrain_original_variants(task,variants,choices)
    if not variants:return None
    if completion_binding:
        variants = [variant for variant in variants if variant['properties']['kind']['enum'] == ['number']]
        if completion_binding.get('scope')=='required_numeric':
            from .contracts import evidence_requirements
            wanted={(n['question_id'],n['text']) for n in completion_binding['missing_numeric_needs']}
            refs=_span_need_references(task,{'question_requirements':{
                q['id']:evidence_requirements(q) for q in task['questions']}})
            kept=[]
            for variant in variants:
                variant=copy.deepcopy(variant)
                links=variant['properties']['review']['properties']['need_links']
                groups=[]
                for group in links['items']['anyOf']:
                    qid=group['properties']['question_id']['enum'][0]
                    ids=[r for r in group['properties']['need_ids']['items']['enum']
                         if (qid,refs[qid][r]['text']) in wanted]
                    if ids:
                        group['properties']['need_ids']['items']['enum']=ids;groups.append(group)
                if groups:links['items']['anyOf']=groups;kept.append(variant)
            variants=constrain_original_variants(task,kept,choices)
        if not variants:
            return None
        for variant in variants:
            variant['properties']['id'] = ({'type':'string','enum':[
                completion_binding['id_prefix']+'item'+str(i) for i in range(1,257)]}
                if task.get('source_extraction_id_choices_version')==1 else {'type':'string',
                'pattern':re.escape(completion_binding['id_prefix']) + r'[A-Za-z0-9_]{1,32}'})
    batch=task.get('reasoning_state',{}).get('source_extraction_batches',{}).get('active')
    if batch and task.get('source_extraction_batches_version')==1:
        for variant in variants:
            if variant['properties']['kind']['enum'][0] in ('text','number'):
                variant['properties']['id']=({'type':'string','enum':[
                    batch['id_prefix']+'item'+str(i) for i in range(1,257)]}
                    if task.get('source_extraction_id_choices_version')==1 else
                    {'type':'string','pattern':re.escape(batch['id_prefix'])+r'[A-Za-z][A-Za-z0-9_]{0,47}'})
    result=object_schema({'version': {'type': 'integer', 'enum': [version]},
        'binding_ref': {'type': 'string', 'enum': [binding]},
        'selections': array_schema({'anyOf': variants})})
    if (version==12 and (not completion_binding or completion_binding.get('scope')=='required_numeric')
            and all(c['kind']=='original' for c in choices)
            and task.get('typed_numeric_selection_version') in (1,2,3)):
        return _typed_requirement_selection_schema(task,result,choices)
    return result


def _typed_requirement_selection_schema(task,schema,choices):
    """Each typed need gets one explicit found-or-missing answer.

    Literal selection branches and downstream gates are unchanged. Text may
    no longer silently stand in for all requested numbers in this protocol.
    """
    from .data_requirements import numeric_slots,requirement_slots,constrain_original_variants,_constrain_original_periods
    from .period_targets import resolve as resolve_period_targets
    from .evidence import declared_security_codes
    all_typed=task.get('typed_numeric_selection_version')==3
    unique_owner=task.get('typed_numeric_selection_version') in (2,3)
    declared=declared_security_codes(task) if unique_owner else []
    slots=requirement_slots(task) if all_typed else numeric_slots(task)
    if not slots:return schema
    variants=schema['properties']['selections']['items']['anyOf']
    numbers=[v for v in variants if v['properties']['kind']['enum']==['number']]
    literals=[v for v in variants if v['properties']['kind']['enum']!=['number']]
    if not literals and not unique_owner:return schema
    requirements={}
    period_targets = {(row['question_id'], row['text']): row for row in resolve_period_targets(task)}
    by_source = {choice['source_ref']: choice for choice in choices}
    for slot in slots:
        branches=[]
        target_codes={code for code in declared if re.search(
            r'(?<![A-Za-z0-9_.])'+re.escape(code)+r'(?![A-Za-z0-9_.])',slot['text'])}
        for variant in (variants if all_typed else numbers):
            selected=copy.deepcopy(variant)
            if selected['properties']['kind']['enum']!=[slot['constraints']['value_kind']]:continue
            if target_codes and not target_codes.intersection(selected['properties'].get('object',{}).get('enum',[])):
                continue
            links=selected['properties']['review']['properties'].get('need_links')
            if not links or 'anyOf' not in links.get('items',{}):continue
            permitted=[]
            for group in links['items']['anyOf']:
                fields=group['properties']
                if (fields['question_id']['enum']==[slot['question_id']]
                        and slot['need_ref'] in fields['need_ids']['items']['enum']):
                    group=copy.deepcopy(group)
                    group['properties']['need_ids']['items']['enum']=[slot['need_ref']]
                    permitted.append(group)
            if not permitted:continue
            links['items']['anyOf']=permitted
            # Unit candidates are narrowed by this exact need, not all numeric
            # needs combined. No source is assigned by text/code matching.
            branches.append(selected)
        branches=constrain_original_variants(task,branches,choices)
        target = period_targets.get((slot['question_id'], slot['text']))
        if target is not None:
            branches = [branch for branch in branches if target['resolved_period'] is not None
                and 'temporal_reference' in branch['properties'] and _constrain_original_periods(
                    branch['properties'], by_source[branch['properties']['source_ref']['enum'][0]],
                    {slot['constraints']['period_kind']}, task=task, exact_period=target['resolved_period'])]
        if unique_owner:
            for branch in branches:
                review=branch['properties']['review']
                review['properties'].pop('need_links')
                review['required'].remove('need_links')
        options=[object_schema(dict(status={'type':'string','enum':['not_found']},reason={'type':'string'}))]
        if branches:
            options.insert(0,object_schema(dict(status={'type':'string','enum':['found']},
                items=array_schema({'anyOf':branches}))))
        requirements[slot['key']]={'anyOf':options,'description':slot['text']}
    result=copy.deepcopy(schema)
    if literals and not all_typed:result['properties']['selections']['items']['anyOf']=literals
    else:result['properties']['selections']['maxItems']=0
    field='requirements' if all_typed else 'numeric_requirements'
    result['properties'][field]=object_schema(requirements)
    result['required'].append(field)
    return result


def _compact_passage_context(view, source, *, table_overrides=False):
    passage = source.get('passage_context_index')
    if isinstance(passage, dict) and passage.get('version') == 2:
        rows = passage.get('parts', [])
        columns = view.get('part_columns', [])
        locators = [part['locator'] if isinstance(part, dict) else part[columns.index('locator')]
                    for part in view.get('parts', [])]
        if (rows and len(rows) == len(locators)
                and all(set(row) == {'part', 'physical_part', 'section_ref'} for row in rows)
                and [row['part'] for row in rows] == locators
                and (table_overrides or all(row['physical_part'] == row['part'] for row in rows))):
            overrides = {str(index):row['physical_part'] for index,row in enumerate(rows)
                         if row['physical_part'] != row['part']}
            passage['version'] = 4 if overrides else 3
            passage['part_order'] = 'untrusted_sources.parts'
            passage['physical_part_rule'] = 'same_as_locator'
            passage['section_refs'] = [row['section_ref'] for row in rows]
            passage.pop('parts')
            passage['rule'] += 'section_refs与本来源parts逐项对应，physical_part等于该项locator。'
            if overrides:
                passage['physical_part_overrides'] = overrides
                passage['rule'] += 'physical_part_overrides按parts零起始位置恢复实际物理页，覆盖默认locator。'


def _compact_general_original_metadata(prompt, task):
    """Losslessly share repeated addresses, never truncate original text."""
    compact = task['general_original_selection_version'] == 2
    marked = (compact and task.get('original_prompt_projection_version') == 2
              and task.get('literal_quantity_reference_version') == 5)
    if compact:
        requirements = prompt['evidence_contract']['question_requirements']
        references = _span_need_references(task, prompt['evidence_contract'])
        prompt['evidence_contract']['question_requirements'] = {
            qid: [dict(need, id=ref) for ref, need in references[qid].items()]
            for qid in requirements}
        prompt['need_reference_encoding'] = dict(version=2, scope='bound_task_and_question',
            rule='need_ids选择question_requirements对应问题的id短引用；内核恢复完整登记需求，不改变需求文字。')
    for view, source in zip(prompt['untrusted_sources'], prompt['evidence_contract']['sources']):
        _compact_passage_context(view, source)
        anchors = view.get('observation_anchors', [])
        if anchors and all('observation_period' in anchor for anchor in anchors):
            view['observation_anchor_columns'] = ['index', 'value', 'part', 'quote']
            view['observation_frequency'] = 'month'
            view['observation_anchors'] = [[anchor['index'], anchor['observation_period']['value'],
                anchor['part'], anchor['quote']] for anchor in anchors]
        parts = view.get('parts', [])
        quantity_units = view.pop('quantity_units', None)
        adjacent_units = {ref: unit for unit, refs in (quantity_units or {}).items() for ref in refs}
        quantity_ids = view.pop('quantity_ids', None)
        if compact and parts:
            for part in parts:
                lines = part['indexed_lines']
                tokens = part['number_tokens']
                line_ids = {line[0] for line in lines}
                if any(token[1] not in line_ids for token in tokens):
                    raise ContractError('数字位置不在实际原文行中，不省略未知定位')
                part['indexed_lines'] = [[line, text,
                    [[quantity_ids.get(ref, ref) if quantity_ids is not None else ref, token]
                     + ([adjacent_units[ref]] if ref in adjacent_units else [])
                     for ref, index, token in tokens if index == line]]
                    for line, text in lines]
                if marked:
                    part['indexed_lines'] = [[line, _mark_reference_line(text,
                        [(str(token[0]), token[1]) for token in numbers])]
                        for line, text, numbers in part['indexed_lines']]
                del part['number_tokens']
            view['indexed_line_columns'] = ['line', 'text', 'number_tokens']
            view['number_token_columns'] = ['number_ref', 'raw_token']
            if quantity_units is not None:
                view['number_token_columns'].append('adjacent_unit')
                view['adjacent_unit_optional'] = True
                if quantity_ids is not None:
                    view['number_token_columns'][0] = 'reference'
                    view['reference_types'] = view.pop('quantity_reference_types',
                        {'integer': 'quantity_ref', 'string': 'number_ref'})
            if marked:
                view['indexed_line_columns'] = ['line', 'marked_text']
                view.pop('number_token_columns', None)
                view['literal_encoding'] = dict(number_markers=['⟦','⟧'], escaped_left='⟦⟦')
        if parts and all(set(part) == set(parts[0]) for part in parts):
            # Column names are shared only when every row has the same exact
            # fields. No absent key becomes null and no metadata is discarded.
            keys = set(parts[0])
            columns = [key for key in ('locator', 'indexed_lines', 'number_tokens') if key in keys]
            columns += sorted(keys - set(columns))
            view['part_columns'] = columns
            view['part_encoding_version'] = 1
            view['parts'] = [[part[key] for key in columns] for part in parts]
    if marked:
        prompt['original_prompt_encoding'] = dict(version=2, exact_text_preserved=True,
            rule='数字前标记仅是引用；去掉非转义标记后还原双左标记，即完整原文。v引用为同源字面数量，n引用为数字位置。')
    return prompt


def _method_span_projection(task):
    return (type(task.get('original_prompt_projection_version')) is int
            and task['original_prompt_projection_version'] in (1, 2) and not _general_original_selection(task))


def _mark_reference_line(text, tokens):
    """Encode each actual number once, with reversible literal escaping."""
    from .financial_sources import _AMOUNT_TOKEN
    matches = list(re.finditer(r'(?<![0-9A-Za-z_.,])'+_AMOUNT_TOKEN+r'(?![0-9A-Za-z_.,])', text))
    if len(matches) != len(tokens):
        raise ContractError('数字块与实际原文位置不一致，不裁剪后继续')
    chunks, position = [], 0
    for match, (reference, token) in zip(matches, tokens):
        if match.group() != token:
            raise ContractError('数字块必须逐字保留原值')
        chunks.append(text[position:match.start()].replace('⟦','⟦⟦'))
        chunks.append('⟦'+reference+'⟧'+token)
        position = match.end()
    chunks.append(text[position:].replace('⟦','⟦⟦'))
    return ''.join(chunks)


def _share_original_addresses(view, source, choice, *, table_overrides=False):
    if 'directory' in view:
        directory = view.pop('directory')
        view['directory_archive_ref'] = dict(artifact_id=choice['view']['id'],
            snapshot_hash=choice['view']['snapshot_hash'], field='directory', content_hash=fingerprint(directory))
    _compact_passage_context(view, source, table_overrides=table_overrides)
    units = source.get('listed_etf_unit_candidates', [])
    if isinstance(units,list) and any(item.get('basis') == 'explicit_primary_code_etf_operation_and_share_listing'
                                      and 'quote' in item for item in units):
        # Quotes are exact concatenations of the already-present source lines.
        # Keep every candidate/address and all metadata; share bytes, not facts.
        from .financial_sources import selected_original_span
        compact_units = []
        for item in units:
            if 'quote' not in item:
                compact_units.append(copy.deepcopy(item))
                continue
            if selected_original_span(choice['lines'],item['span']) != {key:item[key] for key in ('part','quote')}:
                raise ContractError('份额候选不能从实际索引逐字复原')
            encoded = {key:copy.deepcopy(value) for key,value in item.items() if key!='quote'}
            encoded['quote_from_span'] = True
            compact_units.append(encoded)
        source['listed_etf_unit_candidates'] = _columnar_records(compact_units)
    anchors = view.get('observation_anchors', [])
    if not anchors or not all(isinstance(anchor, dict) for anchor in anchors):
        return
    if source.get('original_date_anchors') == [
            {key:value for key,value in anchor.items() if key != 'index'} for anchor in anchors]:
        source.pop('original_date_anchors')
        source['original_date_anchors_ref'] = dict(source_ref=view['source_ref'], table='observation_anchors', omit='index')
    if all(set(anchor) == {'index','date','part','quote'} for anchor in anchors):
        columns = ['index','date','part','quote']
        view['observation_anchor_columns'] = columns
        view['observation_anchors'] = [[anchor[key] for key in columns] for anchor in anchors]


def _omit_other_original_formats(prompt, choices):
    """Remove submission manuals for formats this call cannot emit, not facts."""
    if not all(choice['kind'] == 'original' for choice in choices):
        return []
    names = ('cell_selection_contract','api_candidate_contract','compact_api_value','api_row_groups',
        'compact_api_context','compact_material_context','calculation_context_shape','material_text_contract',
        'material_numeric_contract','identity_fields_for_api','publication_fields','current_publication_template',
        'publication_reference_contract','publication_candidate_contract','compact_material_publication',
        'publication_types','review_fields','review_types','allowed_fields','document_publication_template','usage_scope')
    omitted = [name for name in names if name in prompt['evidence_contract']]
    for name in omitted:
        prompt['evidence_contract'].pop(name)
    return omitted


def _columnar_records(records):
    """Encode a homogeneous metadata table without merging absent keys/nulls."""
    from .contracts import canonical
    if (not isinstance(records, list) or not records
            or not all(isinstance(record, dict) and set(record) == set(records[0]) for record in records)):
        return records
    columns = list(records[0])
    table = dict(columns=columns, rows=[[record[key] for key in columns] for record in records])
    return table if len(canonical(table)) < len(canonical(records)) else records


def _compact_original_part_records(prompt):
    """Share heterogeneous part keys without conflating absent fields and null.

    Each layout is the exact ordered key list for a part. A row begins with
    its layout index and then contains all original values in that order.
    Source lines, geometry, addresses and selection schemas are not changed.
    """
    from .contracts import canonical
    changed=False
    for source in prompt.get('untrusted_sources',[]):
        parts=source.get('parts')
        if not isinstance(parts,list) or not parts or not all(isinstance(part,dict) for part in parts):
            continue
        layouts=[];indices={};rows=[]
        for part in parts:
            keys=tuple(part)
            if keys not in indices:
                indices[keys]=len(layouts)
                layouts.append(list(keys))
            rows.append([indices[keys],*[part[key] for key in keys]])
        encoded=dict(layouts=layouts,rows=rows)
        if len(canonical(encoded))+64<len(canonical(parts)):
            source['parts']=encoded
            changed=True
    if changed:
        prompt.setdefault('original_prompt_encoding',{})['parts']=(
            'parts含layouts及rows。每条row首项选择layouts的索引，其余值按该layout字段顺序逐列对应。'
            '逐行还原为原页段；字段未出现与null不同。布局索引不是页码、原文行号或证据编号；'
            'indexed_lines、表格位置、读取范围、全部文字与数字保持原样。')


def _share_literal_date_values(view, source):
    """ISO dates are recoverable only from the same exact complete date quote."""
    from .evidence import PUBLICATION_DATE_PATTERN, _publication_date_value
    columns = view.get('observation_anchor_columns')
    rows = view.get('observation_anchors', [])
    if columns != ['index','date','part','quote'] or not rows:
        return
    if not all(re.fullmatch(PUBLICATION_DATE_PATTERN, row[3])
               and _publication_date_value(row[3]) == row[1] for row in rows):
        return
    view['observation_anchor_columns'] = ['index','part','quote']
    view['observation_anchors'] = [[row[0],row[2],row[3]] for row in rows]
    view['observation_date_encoding'] = 'iso_date_from_same_literal_full_date_quote'
    reference = source.get('original_date_anchors_ref')
    if reference:
        reference['derived_date'] = 'iso_date_from_same_literal_full_date_quote'


def _share_original_date_tables(view,source,choice):
    """One lossless catalogue for heterogeneous date metadata, not inference.

    Duration anchors carry extra keys, which prevented the older homogeneous
    sharing from applying. Preserve absent keys by grouping exact key shapes;
    keep every original index, quote, value and observation eligibility.
    """
    from .contracts import canonical
    dates=choice['description']['dates']
    if not dates:return
    original=choice['description']['source'].get('original_date_anchors',[])
    labels=list(dict.fromkeys(date['part'] for date in dates))
    tables=[];shapes={}
    for index,date in enumerate(dates):
        row={'index':index,**{('part_ref' if key=='part' else key):
            labels.index(value) if key=='part' else copy.deepcopy(value) for key,value in date.items()}}
        shape=tuple(row)
        if shape not in shapes:
            shapes[shape]=len(tables);tables.append(dict(columns=list(shape),rows=[]))
        tables[shapes[shape]]['rows'].append([row[key] for key in shape])
    indices=[]
    for date in original:
        matches=[i for i,value in enumerate(dates) if value==date]
        if len(matches)!=1:return  # No guessed identity or dropped source row.
        indices.append(matches[0])
    def anchors(field,columns_field):
        rows=view.get(field,[]);columns=view.get(columns_field)
        if not isinstance(rows,list):raise ContractError('日期共享前须保留实际索引表')
        return [row['index'] if isinstance(row,dict) else row[columns.index('index')] for row in rows]
    updated=copy.deepcopy(view);updated_source=copy.deepcopy(source)
    updated['date_part_labels']=labels;updated['date_anchor_tables']=tables
    updated['observation_anchors']=dict(table='date_anchor_tables',indices=anchors('observation_anchors','observation_anchor_columns'))
    updated['non_observation_dates']=dict(table='date_anchor_tables',indices=anchors('non_observation_dates','observation_anchor_columns'))
    updated['date_anchor_encoding']='grouped_exact_columns_v1'
    updated.pop('observation_anchor_columns',None);updated.pop('observation_date_encoding',None)
    updated_source.pop('original_date_anchors',None)
    updated_source['original_date_anchors_ref']=dict(source_ref=view['source_ref'],table='date_anchor_tables',indices=indices)
    if len(canonical(updated))+len(canonical(updated_source))>=len(canonical(view))+len(canonical(source)):return
    view.clear();view.update(updated);source.clear();source.update(updated_source)


def _project_method_span_prompt(prompt, choices, task):
    """Reference-marked text reconstructs every original character.

    Escape original opening marks before inserting numeric references. Remove
    only unescaped reference marks, then unescape, to reproduce the text.
    Canonical views, numeric validation and the decoder stay unprojected.
    """
    by_source = {choice['source_ref']:choice for choice in choices}
    for view in prompt['untrusted_sources']:
        choice = by_source[view['source_ref']]
        if choice['kind'] != 'original':
            continue
        for part in view['parts']:
            tokens_by_line = {}
            for ref,line,token in part['number_tokens']:
                tokens_by_line.setdefault(line,[]).append((ref,token))
            rows = []
            for line,text in part['indexed_lines']:
                tokens = tokens_by_line.get(line,[])
                rows.append([line,_mark_reference_line(text,tokens)])
            part['indexed_lines']=rows
            part.pop('number_tokens')
        view.pop('number_token_columns',None)
        view['indexed_line_columns']=['line','marked_text']
        view['literal_encoding']=dict(number_markers=['⟦','⟧'],escaped_left='⟦⟦',
            rule='单个左标记后的n编号是数字引用；去掉非转义引用标记后，将双左标记还原为单个，即原文。')
        source=next(source for source in prompt['evidence_contract']['sources']
                    if source['artifact_id']==choice['view']['id'])
        _share_original_addresses(view, source, choice)
    prompt['original_prompt_encoding']=dict(version=1,exact_text_preserved=True,
        rule='数字引用嵌入原文行，仅改变呈现；完整来源与定位仍由原输入绑定及解码器核验。')
    requirements=prompt['evidence_contract']['question_requirements']
    references=_span_need_references(task,prompt['evidence_contract'])
    prompt['evidence_contract']['question_requirements']={qid:[dict(need,id=ref)
        for ref,need in references[qid].items()] for qid in requirements}
    prompt['need_reference_encoding']=dict(version=2,scope='bound_task_and_question',
        rule='need_ids选择本问题提供的id，不计算哈希；完整需求文字及业务ID由内核保留。')
    if all(choice['kind'] == 'original' for choice in choices):
        prompt['original_prompt_encoding']['other_submission_formats_omitted'] = _omit_other_original_formats(prompt, choices)
    return prompt


def _present_observation_roles(prompt, choices):
    """Keep every date, but never present rejected dates as selectable anchors."""
    for choice in choices:
        if 'observation_indices' not in choice:
            continue
        view = next(view for view in prompt['untrusted_sources'] if view['source_ref'] == choice['source_ref'])
        anchors = view['observation_anchors']
        columns = view.get('observation_anchor_columns')
        allowed = set(choice['observation_indices'])
        def index(row):
            return row['index'] if isinstance(row, dict) else row[columns.index('index')]
        view['observation_anchors'] = [row for row in anchors if index(row) in allowed]
        excluded = [row for row in anchors if index(row) not in allowed]
        if excluded:
            view['non_observation_dates'] = excluded
            source = next(source for source in prompt['evidence_contract']['sources']
                          if source['artifact_id'] == choice['view']['id'])
            reference = source.get('original_date_anchors_ref')
            if reference and reference.get('table') == 'observation_anchors':
                reference.pop('table')
                reference['tables'] = ['observation_anchors', 'non_observation_dates']
    return prompt


def _present_local_spans(prompt,choices,task):
    by_source={choice['source_ref']:choice for choice in choices}
    for view in prompt['untrusted_sources']:
        choice=by_source[view['source_ref']]
        if choice['kind']!='original':continue
        columns=view.get('part_columns')
        visible_refs=_whole_part_selection(task)
        if visible_refs and columns is not None:
            columns.append('part_ref')
        for index,part in enumerate(view['parts']):
            lines=part['indexed_lines'] if isinstance(part,dict) else part[columns.index('indexed_lines')]
            expected=_span_fragments(choice).get(index,[])
            if [row[0] for row in lines]!=[row['index'] for row in expected]:
                raise ContractError('页段局部地址与本次实际读取行不一致')
            for offset,row in enumerate(lines):row[0]=offset
            if visible_refs:
                if isinstance(part,dict):part['part_ref']=index
                else:part.append(index)
        view['line_addressing']=dict(version=1,part='zero_based_parts_index',lines='zero_based_within_part')
        source=next(row for row in prompt['evidence_contract']['sources']
                    if row['artifact_id']==choice['view']['id'])
        # Only these registered spans are line addresses. Publication
        # candidates also carry start/end, but those are character offsets.
        units=source.get('listed_etf_unit_candidates',[])
        if isinstance(units,list):
            for unit in units:
                if 'span' in unit:unit['span']=_local_span_address(choice,unit['span'])
        elif isinstance(units,dict) and 'span' in units.get('columns',[]):
            position=units['columns'].index('span')
            for row in units['rows']:row[position]=_local_span_address(choice,row[position])
    return prompt


def _present_read_query_navigation(prompt, task, choices):
    """Index literal hits of this reading's own queries, not inferred answers."""
    bundle=task.get('reasoning_state',{}).get('reading',{}).get('bundle',{})
    if (not _general_original_selection(task) or
            fingerprint(bundle.get('views')) != fingerprint([choice['view'] for choice in choices])):
        return prompt
    selections=bundle.get('selection',[])
    for shown,choice in zip(prompt['untrusted_sources'],choices):
        if choice['kind']!='original':continue
        selected=[row for row in selections if row.get('artifact_id')==choice['view']['id']
                  and row.get('snapshot_hash')==choice['view']['snapshot_hash']]
        if len(selected)!=1:continue
        queries=selected[0].get('queries',[])
        if not isinstance(queries,list) or any(not isinstance(query,str) or not query for query in queries):continue
        hits=[[index,line['index']] for index,query in enumerate(queries)
              for line in choice['lines'] if query.casefold() in line['text'].casefold()]
        # This is an optional index, not a truncation of source text or a
        # no-match claim. Large indices are explicitly omitted as a whole.
        shown['read_query_navigation']=dict(version=1,queries=queries,
            columns=['query_index','line'],hits=hits if len(hits)<=256 else [],
            literal_hits=len(hits),index_omitted=len(hits)>256,source_text_preserved=True,
            financial_approval=False)
    return prompt


def _present_concept_navigation(prompt,task,choices):
    from bisect import bisect_right
    from .knowledge import literal_navigation_rule
    rule=literal_navigation_rule(task)
    if rule is None:return prompt
    by_source={choice['source_ref']:choice for choice in choices}
    found=False
    for view in prompt['untrusted_sources']:
        choice=by_source[view['source_ref']]
        if choice['kind']!='original':continue
        hits=[]
        for term_index,term in enumerate(rule['terms']):
            pattern=r'\s*'.join(re.escape(char) for char in term)
            for part,lines in _span_fragments(choice).items():
                text=''.join(line['text'] for line in lines)
                match=re.search(pattern,text)
                if match is None:continue
                starts=[];offset=0
                for line in lines:starts.append(offset);offset+=len(line['text'])
                first=max(0,bisect_right(starts,match.start())-2)
                last=min(len(lines)-1,bisect_right(starts,match.end()-1))
                if last-first>=200:continue
                address=[part,first,last] if _local_span_selection(task) else [lines[first]['index'],lines[last]['index']]
                hits.append([term_index,*address])
                break
        if hits:view['concept_hits']=hits;found=True
    if found:prompt['concept_navigation']=rule
    return prompt


def selection_field_guide(branch):
    """Describe one wire branch's field locations, without selecting values.

    Source and kind discriminate different formats in mixed PDF/API batches.
    Nested alternatives stay separate: a whole-part reference is not a range.
    This is a schema projection, never a repair or a second output contract.
    """
    properties = branch['properties']
    guide = dict(kind=properties['kind']['enum'][0], fields=list(properties))
    if 'source_ref' in properties:
        guide['source_refs'] = list(properties['source_ref'].get('enum', []))
    nested = {}

    def visit(shape, path):
        if 'anyOf' in shape:
            options = []
            for option in shape['anyOf']:
                fields = list(option.get('properties', {}))
                if fields and fields not in options:
                    options.append(fields)
            if options:
                nested[path] = options
            # Do not merge distinct anyOf formats into one invented object.
            return
        fields = shape.get('properties')
        if fields is not None:
            nested[path] = [list(fields)]
            for name, child in fields.items():
                visit(child, path+'.'+name)
        if isinstance(shape.get('items'), dict):
            visit(shape['items'], path+'[]')

    for name, shape in properties.items():
        visit(shape, name)
    if nested:
        guide['nested_field_alternatives'] = nested
    return guide


def original_span_prompt(payload, task):
    selected = original_span_output_schema(task, payload.get('untrusted_sources'))
    if selected is not None and selected['properties']['version']['enum'] == [5]:
        from .original_choices import prompt
        return prompt(payload, task)
    choices = _original_span_choices(task, payload.get('untrusted_sources'))
    schema = original_span_output_schema(task, payload.get('untrusted_sources'))
    if choices is None or schema is None:
        raise ContractError('当前原件不适用行范围选择，不删来源或计算义务')
    result = copy.deepcopy(payload)
    feature_version = schema['properties']['version']['enum'][0]
    if feature_version == 12:
        feature_version = original_span_output_schema(task, payload.get('untrusted_sources'),
            _period_selection=False)['properties']['version']['enum'][0]
    if (type(task.get('missing_api_context_selection_version')) is int
            and task['missing_api_context_selection_version'] == 1):
        result['additional_api_context_policy'] = dict(version=1,
            rule='仅从同对象原件选择当前单元分组缺少且格式允许的字段；已有API口径不重复提交。不同分组分开选择，原值与金融核验不变。',
            source_or_financial_approval=False)
    result['span_binding_ref'] = schema['properties']['binding_ref']['enum'][0]
    context_guide=_api_context_selection_guide(task,choices,schema)
    if context_guide:
        result['api_supplementary_contexts']=dict(version=1,candidates=context_guide,
            candidate_is_approval=False,automatic_selection=False,
            rule='每条仅用于所列source_ref及api_cell_selections。计算需要该口径且原件支持时，'
                 '须在该来源选择项的additional_contexts显式提交所列引用；不能与同来源其他日期的单元混选。'
                 '未选仍可说明原值，但不能完成缺少该口径的计算；不补数字、币种、日期、批准或历史权限。')
    result['untrusted_sources'] = []
    result['evidence_contract']['sources'] = []
    for choice in choices:
        view = copy.deepcopy(choice['view'])
        if choice['kind'] == 'api':
            view.update(source_ref=choice['source_ref'], selection_kind='api')
            result['untrusted_sources'].append(view)
            result['evidence_contract']['sources'].append(copy.deepcopy(choice['source']))
            continue
        for index, part in enumerate(view['parts']):
            part['indexed_lines'] = [[line['index'], line['text']] for line in choice['lines']
                                     if line['fragment_index'] == index]
            del part['text']
            if feature_version in (4, 6, 7, 8, 9, 10, 11):
                indices = {line[0] for line in part['indexed_lines']}
                part['number_tokens'] = [[token['ref'], token['line'], token['token']]
                    for token in choice['number_tokens'] if token['line'] in indices]
        if feature_version in (4, 6, 7, 8, 9, 10, 11):
            view['number_token_columns'] = ['number_ref', 'line', 'raw_token']
        if feature_version in (6, 7, 8, 9, 10, 11):
            quantities = _adjacent_quantity_units(choice)
            view['quantity_units'] = {unit: [ref for ref, entry in quantities.items() if entry['unit'] == unit]
                                      for unit in dict.fromkeys(entry['unit'] for entry in quantities.values())}
            if task['literal_quantity_reference_version'] in (4, 5):
                catalogue = (_named_quantity_index(choice) if task['literal_quantity_reference_version'] == 5
                             else _quantity_index(choice))
                view['quantity_ids'] = {value['number_ref']: index for index, value in catalogue.items()}
                if task['literal_quantity_reference_version'] == 5:
                    view['quantity_reference_types'] = {'v_prefix': 'quantity_ref', 'n_prefix': 'number_ref'}
        view.update(source_ref=choice['source_ref'],
            observation_anchors=[dict(index=index, **date)
                                 for index, date in enumerate(choice['description']['dates'])])
        if choice.get('unit_references'):
            view['unit_reference_columns'] = ['unit_ref','line','literal_unit']
            view['unit_references'] = [[unit['ref'],unit['line'],unit['unit']] for unit in choice['unit_references']]
        if schema['properties']['version']['enum'] == [12] and choice['period_anchors']:
            view['reporting_period_columns'] = ['period_ref', 'part', 'quote']
            view['reporting_period_choices'] = [[p['ref'], p['part'], p['quote']] for p in choice['period_anchors']]
            if choice['period_anchors'][0].get('representation_version') in (2,3):
                view['reporting_period_encoding'] = 'iso_interval_from_same_literal_complete_dates_v2'
                if choice['period_anchors'][0]['representation_version']==3:
                    view['reporting_period_encoding']='iso_interval_from_explicit_or_shared_literal_year_v3'
        result['untrusted_sources'].append(view)
        result['evidence_contract']['sources'].append(copy.deepcopy(choice['description']['source']))
    if _general_original_selection(task):
        result = _compact_general_original_metadata(result, task)
    elif _method_span_projection(task):
        result = _project_method_span_prompt(result,choices,task)
    if task.get('original_prompt_projection_version') == 2:
        for view, choice in zip(result['untrusted_sources'], choices):
            if choice['kind'] != 'original':
                continue
            source = next(source for source in result['evidence_contract']['sources']
                          if source['artifact_id'] == choice['view']['id'])
            _share_original_addresses(view, source, choice, table_overrides=True)
            if 'publication_candidates' in source:
                source['publication_candidates'] = _columnar_records(source['publication_candidates'])
            _share_literal_date_values(view, source)
        omitted = _omit_other_original_formats(result, choices)
        if omitted and 'original_prompt_encoding' in result:
            result['original_prompt_encoding']['other_submission_formats_omitted'] = omitted
        reading = result.get('source_reading') or {}
        coverage = reading.get('coverage')
        if isinstance(coverage, list):
            for record in coverage:
                if isinstance(record, dict) and 'query_coverage' in record:
                    record['query_coverage'] = _columnar_records(record['query_coverage'])
            reading['coverage'] = _columnar_records(coverage)
    if (type(task.get('original_interval_context_version')) is int
            and task['original_interval_context_version'] == 1
            and all(choice['kind']=='original' for choice in choices)):
        # These are submission manuals, not source/need content: this wire
        # cannot submit API identity or document-only answers. Its single
        # need_links owner also cannot submit the old duplicated ownership.
        unused = ['identity_period_provenance','document_coverage_contract','identifier_contract']
        if not any('observation_period' in date for choice in choices for date in choice['description']['dates']):
            unused.append('statistical_period_contract')
        if (type(task.get('original_context_question_link_version')) is int
                and task['original_context_question_link_version'] == 1):
            unused.append('question_binding_contract')
        omitted = [key for key in unused if key in result['evidence_contract']]
        for key in omitted:result['evidence_contract'].pop(key)
        # Publication is restored by the readonly selector. These old free-
        # form submission templates cannot be emitted by this source wire;
        # retain actual publication candidates, limitations and availability.
        for key in ('publication_required_fields','publication_fixed_fields','publication_reading_fields'):
            if any(key in source for source in result['evidence_contract']['sources']):
                for source in result['evidence_contract']['sources']:source.pop(key,None)
                omitted.append('sources.'+key)
        result.setdefault('original_prompt_encoding',{}).setdefault('other_submission_formats_omitted',[]).extend(omitted)
    result=_present_observation_roles(result,choices)
    if _local_span_selection(task):result=_present_local_spans(result,choices,task)
    result=_present_read_query_navigation(result,task,choices)
    result=_present_concept_navigation(result,task,choices)
    if task.get('shared_original_dates_version')==1:
        for choice in choices:
            if choice['kind']!='original':continue
            view=next(v for v in result['untrusted_sources'] if v['source_ref']==choice['source_ref'])
            source=next(s for s in result['evidence_contract']['sources'] if s['artifact_id']==view['id'])
            _share_original_date_tables(view,source,choice)
    if task.get('original_observation_reference_version')==1:
        for choice in choices:
            if choice['kind']!='original':continue
            view=next(v for v in result['untrusted_sources'] if v['source_ref']==choice['source_ref'])
            view['observation_choices']=original_observation_choices(choice)
            declared = document_date_choices(choice)
            if declared:
                view['document_date_choices'] = declared
            labels = source_label_choices(task, choice)
            if labels:
                view['source_label_choices'] = [dict(reference=row['reference'], title=row['label']['title'],
                    period=row['period'], site_label=row['label']['site_label'],
                    limitation=row['limitation'], source_or_financial_approval=False) for row in labels]
    if task.get('typed_numeric_selection_version') in (1,2):
        from .data_requirements import numeric_slots
        result['numeric_requirement_slots']=[{k:v for k,v in slot.items() if k!='need_ref'}
                                           for slot in numeric_slots(task)]
    if task.get('typed_numeric_selection_version')==3:
        from .data_requirements import requirement_slots
        result['requirement_slots']=[{k:v for k,v in slot.items() if k!='need_ref'} for slot in requirement_slots(task)]
    if task.get('original_prompt_projection_version')==2:
        _compact_original_part_records(result)
    return result


ORIGINAL_SELECTION_REJECTIONS = '_original_selection_rejections'
DETAILED_SELECTOR_DIAGNOSTIC_VERSION = 3
SELECTOR_SCHEMA_ISSUES = frozenset(('shape_limit', 'no_branch', 'expected_object', 'unknown_fields',
    'missing_field', 'expected_array', 'array_limit', 'too_few_items', 'too_many_items',
    'duplicate_items', 'type_mismatch', 'too_short', 'too_long', 'enum_mismatch',
    'pattern_mismatch', 'bound_mismatch'))


def validated_selector_schema_diagnostic(value, item_schemas):
    """Only registered paths/categories; never values, arbitrary keys or approval."""
    fields = {'version', 'schema_issues', 'submitted_values_retained', 'automatic_repair'}
    if (not isinstance(value, dict) or set(value) != fields
            or type(value['version']) is not int or value['version'] != 1
            or value['submitted_values_retained'] is not False or value['automatic_repair'] is not False
            or not isinstance(value['schema_issues'], list) or not 1 <= len(value['schema_issues']) <= 8):
        raise ContractError('选择字段诊断格式无效或含未登记内容')
    paths = set()
    def visit(shape, path='$', depth=0):
        paths.add(path)
        if depth > 16 or not isinstance(shape, dict):
            return
        for branch in shape.get('anyOf', []):
            visit(branch, path, depth+1)
        for name, child in shape.get('properties', {}).items():
            visit(child, path+'.'+name, depth+1)
        if isinstance(shape.get('items'), dict):
            visit(shape['items'], path+'[*]', depth+1)
    for shape in item_schemas:
        visit(shape)
    for issue in value['schema_issues']:
        if (not isinstance(issue, dict) or set(issue) != {'path', 'code'}
                or not isinstance(issue['path'], str) or issue['path'] not in paths
                or not isinstance(issue['code'], str) or issue['code'] not in SELECTOR_SCHEMA_ISSUES):
            raise ContractError('选择字段诊断位置或类别未登记')
    return copy.deepcopy(value)


def independent_original_selections(task):
    version=task.get('independent_original_selections_version')
    if version is None:return False
    if type(version) is not int or version!=1:
        raise ContractError('独立原文选择版本未登记')
    return True


def original_requirement_isolation(task):
    version = task.get('original_requirement_isolation_version')
    if version is None:
        return False
    if type(version) is not int or version != 1:
        raise ContractError('独立原文需求容器版本未登记')
    return True


def validated_original_selection_rejections(value,schema):
    """Accept only fixed decoder diagnostics, never model-provided fact fields."""
    fields={'version','schema_hash','submitted_items','accepted_items','rejected_items',
            'automatic_repair','evidence_approved','coverage_approved'}
    version = value.get('version') if isinstance(value, dict) else None
    requirements = []
    if type(version) is int and version == 2:
        fields |= {'expected_requirements', 'rejected_requirements'}
        requirements = value.get('rejected_requirements')
        field = next((key for key in ('requirements', 'numeric_requirements') if key in schema.get('properties', {})), None)
        known = schema['properties'][field].get('properties', {}) if field else {}
        if (not known or type(value.get('expected_requirements')) is not int
                or value['expected_requirements'] != len(known)
                or not isinstance(requirements, list) or not requirements or len(requirements) > len(known)):
            raise ContractError('原文需求诊断未绑定本次登记需求')
        locations = set()
        for item in requirements:
            if (not isinstance(item, dict) or set(item) != {'location', 'category'}
                    or item['category'] not in ('requirement_schema_mismatch', 'requirement_not_submitted')
                    or not isinstance(item['location'], str)
                    or item['location'] not in {'$.' + field + '.' + key for key in known}
                    or item['location'] in locations):
                raise ContractError('原文需求诊断位置无效或重复')
            locations.add(item['location'])
    if (not isinstance(value,dict) or set(value)!=fields
            or type(version) is not int or version not in (1, 2, DETAILED_SELECTOR_DIAGNOSTIC_VERSION)
            or version == DETAILED_SELECTOR_DIAGNOSTIC_VERSION and 'sources' not in schema.get('properties', {})
            or value['schema_hash']!=fingerprint(schema)
            or any(value[key] is not False for key in ('automatic_repair','evidence_approved','coverage_approved'))
            or any(type(value[key]) is not int or not 0<=value[key]<=1000 for key in ('submitted_items','accepted_items'))
            or not isinstance(value['rejected_items'],list) or not (value['rejected_items'] or requirements)
            or value['accepted_items']+len(value['rejected_items'])!=value['submitted_items']):
        raise ContractError('独立原文选择诊断未绑定本次契约或数量不符')
    paths=set()
    for item in value['rejected_items']:
        detailed = (version == DETAILED_SELECTOR_DIAGNOSTIC_VERSION and isinstance(item, dict)
                    and item.get('category') == 'selector_schema_mismatch')
        item_fields = {'location', 'category'} | ({'diagnostic'} if detailed else set())
        if (not isinstance(item,dict) or set(item)!=item_fields
                or item['category'] not in ('selector_schema_mismatch','selection_decode_rejected')
                or not isinstance(item['location'],str)):
            raise ContractError('原文选择诊断字段未登记')
        if 'sources' in schema.get('properties', {}):
            match = re.fullmatch(r'\$\.sources\[(0|[1-9][0-9]{0,2})\]\.selection\.(financial_cells|text_quotes|amount_cells)\[(0|[1-9][0-9]{0,2})\]', item['location'])
            source_item = schema['properties']['sources']['items']
            variants = source_item.get('anyOf', [source_item] if source_item.get('type') == 'object' else [])
            if (not match or item['location'] in paths or int(match[1]) >= min(200, len(variants))
                    or int(match[3]) >= 200 or not any(match[2] in branch['properties']['selection']['properties']
                                                      for branch in variants)):
                raise ContractError('财务原件选择诊断位置无效或重复')
            if detailed:
                shapes = [branch['properties']['selection']['properties'][match[2]]['items']
                    for branch in variants if match[2] in branch['properties']['selection']['properties']]
                validated_selector_schema_diagnostic(item['diagnostic'], shapes)
            paths.add(item['location'])
            continue
        match=re.fullmatch(r'\$\.(selections|(?:requirements|numeric_requirements)\.([A-Za-z0-9_]+)\.items)\[(0|[1-9][0-9]{0,2})\]',item['location'])
        if not match or item['location'] in paths:
            raise ContractError('原文选择诊断位置无效或重复')
        if match[2]:
            field=match[1].split('.')[0]
            if match[2] not in schema['properties'].get(field,{}).get('properties',{}):
                raise ContractError('原文选择诊断不属于本次需求')
            if any(item['location'].startswith(entry['location'] + '.') for entry in requirements):
                raise ContractError('同一需求不能同时记录为整个容器拒绝和已处理条目')
        paths.add(item['location'])
    return copy.deepcopy(value)


def _decode_independent_original_spans(wire,schema,task,views):
    """Separate invalid local selectors, never a changed binding or authority.

    The projection below validates only the original envelope, without claiming
    its emptied arrays were the submitted result. Every actual item is checked
    against its original requirement-specific schema before it is decoded.
    """
    frame=copy.deepcopy(wire);items=[]
    isolate_requirements = original_requirement_isolation(task)
    frame_schema = copy.deepcopy(schema) if isolate_requirements else schema
    rejected_requirements = []
    limit=MAX_ARGUMENT_CHARACTERS if schema.get(LOCAL_ARRAY_POLICY)==2 else 200

    def collect(values,shape,path,slot=None):
        if (not isinstance(values,list) or not shape.get('minItems',0)<=len(values)
                <=min(limit,shape.get('maxItems',limit))):
            raise StructuredOutputError('invalid_original_spans','原文选择数组超量或格式不符，未采用')
        items.extend((item,shape['items'],path+'['+str(index)+']',slot)
                     for index,item in enumerate(values))

    if not isinstance(frame,dict) or not isinstance(frame.get('selections'),list):
        raise StructuredOutputError('invalid_original_spans','原文选择容器格式不符，未采用')
    collect(wire['selections'],schema['properties']['selections'],'$.selections')
    frame['selections']=[]
    field=next((key for key in ('requirements','numeric_requirements') if key in schema['properties']),None)
    if field:
        from .data_requirements import requirement_slots,numeric_slots
        slots={slot['key']:slot for slot in (requirement_slots(task) if field=='requirements' else numeric_slots(task))}
        responses=frame.get(field)
        if not isinstance(responses,dict):
            raise StructuredOutputError('invalid_original_spans','原文需求容器格式不符，未采用')
        if isolate_requirements:
            count = len(wire['selections']) + sum(len(response['items']) for response in responses.values()
                if isinstance(response, dict) and isinstance(response.get('items'), list))
            if count > 1000:
                raise StructuredOutputError('invalid_original_spans', '原文选择总量超限，未采用')
        keys = list(slots) if isolate_requirements else list(responses)
        for key in keys:
            response = responses.get(key)
            if isolate_requirements:
                shape = schema['properties'][field]['properties'].get(key)
                if shape is None:
                    continue
                projected = copy.deepcopy(response)
                if isinstance(projected, dict) and projected.get('status') == 'found' and isinstance(projected.get('items'), list):
                    projected['items'] = []  # Only the wrapper is checked here; actual items are checked below.
                empty = (isinstance(response, dict) and
                    (response.get('status') == 'found' and response.get('items') == []
                     or response.get('status') == 'not_found' and isinstance(response.get('reason'), str)
                     and not response['reason'].strip()))
                if empty or not _matches(projected, shape):
                    # Remove only this known requirement from the validation
                    # projection. Never synthesize found/not_found or a reason.
                    rejected_requirements.append(dict(location='$.' + field + '.' + key,
                        category='requirement_schema_mismatch' if key in responses else 'requirement_not_submitted'))
                    responses.pop(key, None)
                    frame_schema['properties'][field]['properties'].pop(key)
                    frame_schema['properties'][field]['required'].remove(key)
                    continue
            if key not in slots or not isinstance(response,dict):continue  # Exact frame check below rejects it.
            if response.get('status')=='found' and isinstance(response.get('items'),list):
                branches=schema['properties'][field]['properties'][key]['anyOf']
                found=next((b for b in branches if b['properties']['status'].get('enum')==['found']),None)
                if found is None:continue
                if not response['items']:
                    raise StructuredOutputError('empty_numeric_selection','已找到依据须提交实际选择，不用空列表声称取得')
                collect(wire[field][key]['items'],found['properties']['items'],
                        '$.'+field+'.'+key+'.items',slots[key])
                response['items']=[]
            elif response.get('status')=='not_found' and isinstance(response.get('reason'),str) and not response['reason'].strip():
                raise StructuredOutputError('empty_numeric_missing_reason','未找到依据须明确原因')
    if len(items)>1000 or not _matches(frame,frame_schema):
        raise StructuredOutputError('invalid_original_spans','原文选择容器与本次契约不符，未采用',schema_diagnostic(wire,schema))
    ids=[item.get('id') for item,_,_,_ in items if isinstance(item,dict) and isinstance(item.get('id'),str)]
    if len(ids)!=len(set(ids)):
        raise StructuredOutputError('invalid_numeric_selection_ids','原文选择编号重复，不自动改名或选择其中一项')
    permitted_paths={
        '$.number_reference.quantity_ref','$.number_reference.literal_ref',
        '$.number_reference.number_ref','$.number_reference.unit_ref',
        '$.temporal_reference.period_ref','$.temporal_reference.observation_ref',
        '$.temporal_reference.document_date_ref',
        '$.source_label_ref',
        '$.text_reference.line','$.text_reference.part',
    }
    selected=[];rejected=[]
    for item,shape,path,slot in items:
        if not _matches(item,shape):
            diagnostic=schema_diagnostic(item,shape)
            issues=diagnostic['schema_issues']
            if not issues or any(issue['code'] not in ('enum_mismatch','pattern_mismatch')
                                 or issue['path'] not in permitted_paths for issue in issues):
                raise StructuredOutputError('invalid_original_spans','原文选择字段或权限格式不符，未采用',schema_diagnostic(wire,schema))
            rejected.append(dict(location=path,category='selector_schema_mismatch'))
            continue
        item=copy.deepcopy(item)
        if slot and task.get('typed_numeric_selection_version') in (2,3):
            item['review']['need_links']=[dict(question_id=slot['question_id'],need_ids=[slot['need_ref']])]
        selected.append((item,path))
    choices={choice['source_ref']:choice for choice in _original_span_choices(task,views)}
    period_version=(original_span_output_schema(task,views,_period_selection=False)['properties']['version']['enum'][0]
                    if wire['version']==12 else None)
    specs=[];accepted=0
    accepted_paths = []
    for item,path in selected:
        unit=dict(version=wire['version'],binding_ref=wire['binding_ref'],selections=[item])
        try:
            decoded=_decode_original_selection_set(task,views,unit,schema,choices=choices,period_version=period_version)
        except ContractError:
            rejected.append(dict(location=path,category='selection_decode_rejected'))
            continue
        specs.extend(decoded['evidence_specs']);accepted+=1
        accepted_paths.append(path)
    identifiers=[spec['id'] for spec in specs]
    if len(identifiers)>1000 or len(identifiers)!=len(set(identifiers)):
        raise StructuredOutputError('invalid_numeric_selection_ids','展开后的原文选择编号重复或超量，未覆盖或截断')
    result=dict(evidence_specs=specs)
    from .extraction_feedback import build as build_feedback, FIELD as FEEDBACK_FIELD
    feedback = build_feedback(task, views, schema, wire, specs, accepted_paths, rejected, rejected_requirements)
    if feedback is not None: result[FEEDBACK_FIELD] = feedback
    if rejected or rejected_requirements:
        result[ORIGINAL_SELECTION_REJECTIONS]=dict(version=1,schema_hash=fingerprint(schema),
            submitted_items=len(items),accepted_items=accepted,rejected_items=rejected,
            automatic_repair=False,evidence_approved=False,coverage_approved=False)
        if rejected_requirements:
            result[ORIGINAL_SELECTION_REJECTIONS].update(version=2,
                expected_requirements=len(schema['properties'][field]['properties']),
                rejected_requirements=rejected_requirements)
    return result


def decode_original_spans(wire, schema, selected_context):
    """Restore exact bytes and literal numbers, not their financial meaning."""
    if not isinstance(selected_context, tuple) or len(selected_context) != 2:
        raise StructuredOutputError('missing_original_span_context', '原文范围选择缺少当前任务和实际读取绑定')
    task, views = selected_context
    if schema.get('properties', {}).get('version', {}).get('enum') == [5]:
        from .original_choices import decode
        return decode(wire, schema, task, views)
    expected = original_span_output_schema(task, views)
    if expected is not None and schema==expected and independent_original_selections(task):
        return _decode_independent_original_spans(wire,schema,task,views)
    if expected is None or schema != expected or not _matches(wire, schema):
        if task.get('original_span_diagnostics_version') == 1:
            if expected is None or schema != expected:
                diagnostic = dict(version=1, schema_issues=[dict(path='$', code='context_schema_mismatch')],
                                  automatic_repair=False, submitted_values_retained=False)
                message = '原文选择容器与当前任务或读取契约不符，未采用'
            else:
                diagnostic = schema_diagnostic(wire, schema)
                message = '原文提取字段不符合本次选择格式，原件保留，未采用'
            raise StructuredOutputError('invalid_original_spans', message, diagnostic)
        raise StructuredOutputError('invalid_original_spans', '原文范围与当前任务或读取绑定不符，未采用')
    slot_field=next((k for k in ('requirements','numeric_requirements') if k in schema['properties']),None)
    if slot_field:
        wire=copy.deepcopy(wire)
        from .data_requirements import numeric_slots,requirement_slots
        slots={slot['key']:slot for slot in (requirement_slots(task) if slot_field=='requirements' else numeric_slots(task))}
        for key,response in wire.pop(slot_field).items():
            if response['status']=='found':
                if not response['items']:
                    raise StructuredOutputError('empty_numeric_selection','已找到依据须提交实际原文引用，不能用空列表声称取得')
                if task.get('typed_numeric_selection_version') in (2,3):
                    slot=slots[key]
                    for item in response['items']:
                        item['review']['need_links']=[dict(question_id=slot['question_id'],need_ids=[slot['need_ref']])]
                wire['selections'].extend(response['items'])
            elif not response['reason'].strip():
                raise StructuredOutputError('empty_numeric_missing_reason','未找到所需数字须明确原因，不自动判定原件未披露')
        ids=[item['id'] for item in wire['selections']]
        if len(ids)>1000 or len(ids)!=len(set(ids)):
            raise StructuredOutputError('invalid_numeric_selection_ids','原文数值及文字选择编号重复或超量，不自动改名')
    return _decode_original_selection_set(task,views,wire,schema)


def _decode_original_selection_set(task,views,wire,schema,*,choices=None,period_version=None):
    """Decode already shape-checked selectors; neither evidence gate is bypassed."""
    from decimal import Decimal,InvalidOperation
    from .financial_sources import selected_original_span,_AMOUNT_TOKEN
    if fee_context_mapping_enabled(task):
        # Validation checked the explicit six keys. Restore only their named
        # fields, never missing values, spans, source facts or approvals.
        wire = copy.deepcopy(wire)
        for item in wire['selections']:
            if item['kind'] == 'number':
                item['contexts'] = [dict(field=field, **selection)
                                    for field,selection in item['contexts'].items()]
    if choices is None:choices={choice['source_ref']:choice for choice in _original_span_choices(task,views)}
    if _local_span_selection(task):
        wire=copy.deepcopy(wire)
        def expanded(value,choice):
            if isinstance(value,dict):
                if set(value) in ({'part','start','end'},{'part_ref'}):
                    return _expand_local_span(choice,value)
                return {key:expanded(item,choice) for key,item in value.items()}
            if isinstance(value,list):return [expanded(item,choice) for item in value]
            return value
        for index,item in enumerate(wire['selections']):
            choice=choices[item['source_ref']]
            if choice['kind']=='original':
                wire['selections'][index]=expanded(item,choice)
            else:
                for addition in item.get('additional_contexts',[]):
                    if 'context_ref' in addition:
                        continue
                    addition['span']=_expand_local_span(choices[addition['source_ref']],addition['span'])
    periods, context_periods, source_labels = {}, {}, {}
    context_question_links = (wire['version'] == 12
        and type(task.get('original_context_question_link_version')) is int
        and task['original_context_question_link_version'] == 1)
    if wire['version'] == 12:
        wire = copy.deepcopy(wire)
        wire['version'] = (period_version if period_version is not None else
            original_span_output_schema(task,views,_period_selection=False)['properties']['version']['enum'][0])
        for item in wire['selections']:
            choice = choices[item['source_ref']]
            if choice['kind'] != 'original':
                continue
            if context_question_links:
                item['question_ids'] = [link['question_id'] for link in item['review']['need_links']]
            if 'source_label_ref' in item:
                from .source_labels import ROLE as SOURCE_LABEL_ROLE
                reference = item.pop('source_label_ref')
                matches = [row for row in source_label_choices(task, choice)
                           if row['reference'] == reference]
                if len(matches) != 1 or item['kind'] != 'text':
                    raise ContractError('来源标注引用不属于本次实际读取，不猜测或补写')
                label = matches[0]
                source_labels[item['id']] = label
                item.update(observation_anchor=label['index'], period=label['period'],
                    object='来源标注', nature='已实现事实', date_role=SOURCE_LABEL_ROLE,
                    text_reference={'part': label['label']['page_header']['part']})
                continue
            temporal = item.pop('temporal_reference')
            if 'document_date_ref' in temporal:
                from .document_dates import ROLE
                reference = next((entry for entry in document_date_choices(choice)
                                  if entry['reference'] == temporal['document_date_ref']), None)
                if reference is None or item.get('kind') != 'text' or item.get('date_role') != ROLE:
                    raise ContractError('原页日期引用不属于本次原件文字选择')
                item.update(observation_anchor=reference['index'], period=reference['period'])
                continue
            if 'observation_ref' in temporal:
                references={entry['reference']:entry['index'] for entry in original_observation_choices(choice)}
                if temporal['observation_ref'] not in references:
                    raise StructuredOutputError('invalid_observation_reference','日期短编号不属于当前原件，不补造观测日期')
                temporal={'observation_anchor':references[temporal['observation_ref']]}
            if 'period_ref' in temporal:
                period = next(p for p in choice['period_anchors'] if p['ref'] == temporal['period_ref'])
                item.update(period=period['period'], observation_anchor=period['end_anchor'])
                source = choice['description']['source']
                periods[item['id']] = dict(kind='material', artifact_id=source['artifact_id'],
                    snapshot_hash=source['snapshot_hash'], part=period['part'], quote=period['quote'])
                if period.get('representation_version') in (2,3):
                    periods[item['id']]['version'] = period['representation_version']
            else:
                date = choice['description']['dates'][temporal['observation_anchor']]
                item.update(observation_anchor=temporal['observation_anchor'],
                    period=date['observation_period']['value'] if 'observation_period' in date else date['date'])
            if (item['kind'] == 'number' and type(task.get('original_interval_context_version')) is int
                    and task['original_interval_context_version'] == 1):
                container = item if 'contexts' in item else item.get('number_reference',item)
                contexts = container.get('contexts', [])
                selected = [entry for entry in contexts if entry['field'] == 'period']
                if len(selected) > 1:
                    raise ContractError('同一期间口径不能重复提交')
                if selected and 'period_ref' in selected[0]:
                    if temporal.get('period_ref') != selected[0]['period_ref']:
                        raise ContractError('计算期间须显式选择本原值相同的报告区间')
                    context_periods[item['id']] = (item['period'],copy.deepcopy(periods[item['id']]))
                    container['contexts'] = [entry for entry in contexts if entry['field'] != 'period']
            if item['kind']=='number' and _method_span_projection(task) and 'number_reference' in item:
                reference=item.pop('number_reference')
                if 'literal_ref' in reference:
                    quantity = _adjacent_quantity_units(choice).get(reference['literal_ref'])
                    if quantity is None:
                        raise StructuredOutputError('invalid_literal_quantity','所选地址没有同源字面单位，不补造单位')
                    point=dict(start=quantity['line'],end=quantity['line'])
                    item.update(number_ref=reference['literal_ref'],unit=quantity['unit'],
                        text_span=copy.deepcopy(reference.get('text_span',point)),
                        unit_span=dict(point),contexts=item.get('contexts',[]))
                else:
                    item.update(reference)
    single_question_links = wire['version'] == 11
    if single_question_links:
        wire = copy.deepcopy(wire)
        wire['version'] = 10
        for item in wire['selections']:
            item['question_ids'] = [link['question_id'] for link in item['review']['need_links']]
    if wire['version'] in (6, 7, 8, 9, 10):
        original_version = wire['version']
        wire = copy.deepcopy(wire)
        wire['version'] = 4

        def point_span(choice, reference):
            if 'line' in reference:
                return dict(start=reference['line'], end=reference['line'])
            lines = [line for line in choice['lines'] if line['part'] == reference['part']]
            if not lines or len({line['fragment_index'] for line in lines}) != 1:
                raise StructuredOutputError('invalid_original_part_reference', '所选段落不在本来源实际读取片段或存在多段窗口，不猜测位置')
            return dict(start=lines[0]['index'], end=lines[-1]['index'])

        for item in wire['selections']:
            if original_version in (7, 8, 9, 10) and item['kind'] == 'text':
                reference = item.pop('text_reference')
                if original_version >= 8 or 'part' in reference:
                    item['text_span'] = point_span(choices[item['source_ref']], reference)
                else:
                    item['text_span'] = reference['span']
            if item['kind'] != 'number':
                continue
            reference = item.pop('number_reference')
            if 'unit_ref' in reference:
                unit = next((unit for unit in choices[item['source_ref']].get('unit_references',[])
                             if unit['ref'] == reference['unit_ref']), None)
                if unit is None:
                    raise StructuredOutputError('invalid_original_unit_reference', '单位引用不在本来源实际原文中，不猜测或跨源借用')
                reference = dict(number_ref=reference['number_ref'],unit=unit['unit'],
                                 unit_reference=dict(line=unit['line']))
            if 'quantity_ref' in reference:
                quantities = (_named_quantity_index(choices[item['source_ref']]) if original_version == 10
                              else _quantity_index(choices[item['source_ref']]) if original_version == 9
                              else _adjacent_quantity_units(choices[item['source_ref']]))
                quantity = quantities.get(reference['quantity_ref'])
                if quantity is None:
                    raise StructuredOutputError('invalid_literal_quantity', '数字与字面单位未在本来源登记，不猜测或补值')
                item.update(number_ref=quantity['number_ref'] if original_version >= 9 else reference['quantity_ref'], unit=quantity['unit'],
                    unit_span=dict(start=quantity['line'], end=quantity['line']), contexts=[])
                if original_version >= 7:
                    item['text_span'] = dict(start=quantity['line'], end=quantity['line'])
            else:
                if original_version >= 8:
                    choice = choices[item['source_ref']]
                    address = next((token for token in choice['number_tokens'] if token['ref'] == reference['number_ref']), None)
                    if address is None:
                        raise StructuredOutputError('number_reference_outside_span', '数字引用不属于本来源，不替换或补造')
                    reference['unit_span'] = point_span(choice, reference.pop('unit_reference'))
                    reference['text_span'] = dict(start=address['line'], end=address['line'])
                    reference['contexts'] = []
                item.update(reference)
    specs, identifiers = [], set()
    for item in wire['selections']:
        if item['kind'] == 'api':
            choice = choices[item['source_ref']]
            selected_api = decode_api_extraction_output(item['selection'], choice['api_schema'],
                context=(task,[choice['view']]))['evidence_specs']
            from .evidence import expand_api_cell_selections
            # The independent-selection compiler needs actual selected IDs
            # even when no supplementary context was chosen. These are exact
            # catalogue expansions, not missing-field repairs or approvals.
            selected_api = expand_api_cell_selections(task, selected_api)
            if item.get('additional_contexts'):
                from .evidence import expand_api_context_reference
                records = {record['id']: record for record in task.get('source_records', [])}
                for index, api in enumerate(selected_api):
                    api = expand_api_context_reference(task, records[api['artifact_id']], api)
                    for addition in item['additional_contexts']:
                        if 'context_ref' in addition:
                            from .etf_sources import share_context_choices
                            available = share_context_choices(task, records[api['artifact_id']],
                                api['record_locator'], api['object'],
                                allowed_ids={value['view']['id'] for value in choices.values()})
                            selected = [value for value in available if value['context_ref'] == addition['context_ref']]
                            if len(selected) != 1 or selected[0]['field'] in api['calculation_context']:
                                raise ContractError('跨接口口径引用已失效或覆盖已有字段，不补造或改写')
                            selected = selected[0]
                            api['calculation_context'][selected['field']] = selected['value']
                            api['calculation_context_basis'][selected['field']] = copy.deepcopy(selected['basis'])
                            continue
                        original = choices[addition['source_ref']]
                        binding = original['description']['source'].get('object_binding')
                        if not binding or binding['value'] != api['object']:
                            raise ContractError('新增计算口径原件不属于当前行情对象，不跨基金借用依据')
                        if addition['field'] in api['calculation_context']:
                            raise ContractError('新增原件口径不能重复或覆盖行情已登记的原始口径')
                        anchor = selected_original_span(original['lines'], addition['span'])
                        api['calculation_context'][addition['field']] = addition['value']
                        api['calculation_context_basis'][addition['field']] = dict(kind='material',
                            artifact_id=original['view']['id'], snapshot_hash=original['view']['snapshot_hash'], **anchor)
                    selected_api[index] = api
            specs.extend(selected_api)
            continue
        if item['id'] in identifiers:
            raise ContractError('原文范围提取标识重复，不覆盖前项')
        identifiers.add(item['id'])
        choice = choices[item['source_ref']]
        source, description = choice['description']['source'], choice['description']
        selected = selected_original_span(choice['lines'], item['text_span'])
        date = description['dates'][item['observation_anchor']]
        links = item['review']['need_links']
        if (not links or any(not link['need_ids'] for link in links)
                or len({link['question_id'] for link in links}) != len(links)
                or {link['question_id'] for link in links} != set(item['question_ids'])):
            if single_question_links or context_question_links:
                raise StructuredOutputError('invalid_original_need_links', '原件问题关联重复或缺少需求，不猜测或合并',
                    dict(version=1, automatic_repair=False, submitted_values_retained=False,
                         relation_issues=[dict(code='duplicate_question_link')]))
            raise ContractError('原文范围的问题与非空需求关联须一致，不自动补齐')
        requirements = {qid: {ref: need['text'] for ref, need in needs.items()}
                        for qid, needs in _span_need_references(task, description['contract']).items()}
        review = dict(question_ids=copy.deepcopy(item['question_ids']), allowed_natures=[item['nature']],
            conditions=['仅本次实际读取原件的明确内容和当前版本用途；位置选择不批准数值口径或投资结论。'],
            reason='按原件行范围提交既有核验，不重抄或修复原文。', temporal_use='current_version',
            evidence_need_texts={link['question_id']: [requirements[link['question_id']][ref]
                for ref in link['need_ids']] for link in links})
        if item['id'] in source_labels:
            from .source_labels import specification, LIMITATION
            label = source_labels[item['id']]
            if selected != label['label']['page_header']:
                raise ContractError('来源标注所选文字不是同一完整原页标题')
            review['conditions'] = [LIMITATION]
            record = next(record for record in task['source_records'] if record['id'] == source['artifact_id'])
            specs.append(specification(task, record, label, item['id'], item['question_ids'], review))
            continue
        spec = dict(id=item['id'], artifact_id=source['artifact_id'], object=item['object'], nature=item['nature'],
            question_ids=copy.deepcopy(item['question_ids']), period=item['period'],
            text=selected['quote'], record_locator=dict(part=selected['part'], period_part=date['part'], period_quote=date['quote']),
            published_at=None, publication=dict(at=None, basis_ref=None, precision='未知', timezone='未知',
                certainty='未知', basis='首次公开时间未知，当前可得性另由实际取数账本核验'),
            availability=copy.deepcopy(source['current_availability_reference']), unit='不适用', review=review)
        if item['id'] in periods:
            spec['reporting_period_basis'] = periods[item['id']]
        if date.get('date_role'):
            if item.get('kind') != 'text' or item.get('date_role') != date['date_role']:
                raise ContractError('原页日期不能充当数值观测或普通日期')
            spec['date_role'] = date['date_role']
        elif 'observation_period' in date:
            if item['period'] != date['observation_period']['value']:
                raise ContractError('所属月与所选原文月份不一致，不能替换为另一个月份或月末日期')
            spec['observation_period'] = copy.deepcopy(date['observation_period'])
        else:
            spec['observation_date'] = date['date']
        if item['kind'] == 'number':
            if wire['version'] == 4:
                address = next((token for token in choice['number_tokens'] if token['ref'] == item['number_ref']), None)
                if address is None or not item['text_span']['start'] <= address['line'] <= item['text_span']['end']:
                    raise StructuredOutputError('number_reference_outside_span', '数字引用不在本来源所选原文范围，不移动位置或替换原值')
                token = address['token']
                # A literal single-day label can use the bound date's standard
                # representation. Durations, clocks and different days stay unchanged.
                from .evidence import PUBLICATION_DATE_PATTERN, _publication_date_value
                if (any(c['field'] == 'valuation_date' and c['value'] == date['date'] for c in item['contexts'])
                        and re.fullmatch(PUBLICATION_DATE_PATTERN, item['period'].strip())
                        and _publication_date_value(item['period']) == date['date']):
                    spec['period'] = date['date']
            else:
                tokens = list(re.finditer(r'(?<![0-9A-Za-z_.,])' + _AMOUNT_TOKEN + r'(?![0-9A-Za-z_.,])', selected['quote']))
                if item['number_occurrence'] >= len(tokens):
                    raise ContractError('所选原文没有对应数字位置，不补值或寻找近似值')
                token = tokens[item['number_occurrence']].group()
            try:
                value = Decimal(token.replace(',', '').replace('−', '-'))
                number = int(value) if value == value.to_integral_value() else float(value)
                exact = value.is_finite() and math.isfinite(number) and Decimal(str(number)) == value
            except (InvalidOperation, OverflowError, ValueError):
                exact = False
            if not exact:
                raise ContractError('原文数字不能无损表示，不舍入后提交')
            unit = selected_original_span(choice['lines'], item['unit_span'])
            if item['unit'] not in unit['quote']:
                raise ContractError('所选单位不在对应原文范围，不补单位或借其他页段')
            spec['record_locator']['unit_part'] = unit['part']
            spec.update(raw_token=token, raw_value=number, value=number, unit=item['unit'], raw_unit=item['unit'], conversion_factor=1)
            contexts, bases = {}, {}
            for entry in item['contexts']:
                if entry['field'] in contexts:
                    raise ContractError('同一口径不能提交重复原文范围')
                if 'unit_ref' in entry:
                    selected_unit = next((row for row in source.get('listed_etf_unit_candidates',[])
                                          if row.get('ref')==entry['unit_ref']),None)
                    if entry['field']!='share_class' or selected_unit is None:
                        raise ContractError('份额引用须属于本来源实际候选，不借用其他原件或补值')
                    contexts['share_class'] = selected_unit['value']
                    bases['share_class'] = dict(kind='material',artifact_id=source['artifact_id'],
                        snapshot_hash=source['snapshot_hash'],part=selected_unit['part'],quote=selected_unit['quote'])
                    continue
                basis = selected_original_span(choice['lines'], entry['span'])
                contexts[entry['field']] = entry['value']
                bases[entry['field']] = dict(kind='material', artifact_id=source['artifact_id'],
                    snapshot_hash=source['snapshot_hash'], **basis)
            if item['id'] in context_periods:
                if 'period' in contexts:
                    raise ContractError('不能同时提交期间引用与手写期间口径')
                contexts['period'], bases['period'] = context_periods[item['id']]
            if contexts:
                spec.update(calculation_context=contexts, calculation_context_basis=bases)
        specs.append(spec)
    return {'evidence_specs': specs}


def _original_extraction_schema(task, views, *, financial=False, contract=None):
    from .contracts import DOCUMENT_CONTENT_INTENT, FINANCIAL_PERIOD_METHODS
    if (task.get('intent') == DOCUMENT_CONTENT_INTENT or not isinstance(views, list) or not views
            or any(not isinstance(v, dict) or v.get('kind') not in ('material', 'filing') for v in views)):
        return None
    if contract is None:
        from .reasoning import evidence_contract
        contract = evidence_contract(task, views)
    if contract['source_bindings_omitted']:
        return None
    text = {'type': 'string'}
    question_ids = {'type': 'string', 'enum': contract['question_ids']}
    needs = [object_schema({'question_id': {'type': 'string', 'enum': [ident]},
        'texts': array_schema({'type': 'string', 'enum': [n['text'] for n in items]})})
        for ident, items in contract['question_requirements'].items() if items]
    coverage = array_schema({'anyOf': needs}) if needs else array_schema(object_schema({
        'question_id': question_ids, 'texts': array_schema(text)}))
    nature = {'type': 'string', 'enum': contract['natures']}
    review = object_schema({'question_ids': array_schema(question_ids), 'allowed_natures': array_schema(nature),
        'conditions': array_schema(text), 'reason': text, 'evidence_need_texts': coverage})
    publication = {'at': text, 'basis': text, 'precision': {'type': 'string', 'enum': ['日', '秒']},
        'timezone': {'type': 'string', 'enum': ['Asia/Shanghai', 'UTC']},
        'certainty': {'type': 'string', 'enum': ['已确认']}}
    objects = {'type': 'string', 'enum': task['objects']} if task['objects'] else text
    choices = []
    financial_choices = 0
    for source, view in zip(contract['sources'], views):
        parts = list(dict.fromkeys(p['locator'] for p in view.get('parts', [])))
        if not parts or not source.get('publication_fixed_fields'):
            return None
        part = {'type': 'string', 'enum': parts}
        base = {'id': _identifier_schema(), 'artifact_id': {'type': 'string', 'enum': [source['artifact_id']]},
            'object': objects, 'record_locator': {'anyOf': [object_schema({'part': part}),
                object_schema({'part': part, 'unit_part': part})]},
            'text': text, 'period': text, 'nature': nature, 'question_ids': array_schema(question_ids),
            'review': review, 'published_at': text}
        fixed = source['publication_fixed_fields']
        basis = {key: _literal_schema(value) for key, value in fixed.items()}
        if fixed['kind'] == 'material':
            basis.update(part=part, quote=text)
        variants = [dict(base, publication=object_schema(dict(publication, basis_ref=object_schema(basis))))]
        short_ids = [r['candidate_id'] for r in source['material_publication_references']]
        if short_ids:
            variants.append(dict(base, publication=object_schema(publication),
                material_publication_candidate_id={'type': 'string', 'enum': short_ids}))
        temporal = source.get('publication_mode_contract')
        current_only = (isinstance(temporal, dict)
            and temporal.get('allowed_modes') == ['unknown_current_original'])
        if current_only:
            # The source adapter has not registered first-publication proof.
            # Do not offer a contradictory representation and reject it later.
            variants = []
        current = source.get('current_availability_reference')
        if isinstance(current, dict) and current.get('kind') == 'current_original':
            current_review = object_schema({'question_ids': array_schema(question_ids),
                'allowed_natures': array_schema(nature), 'conditions': array_schema(text), 'reason': text,
                'evidence_need_texts': coverage, 'temporal_use': {'type': 'string', 'enum': ['current_version']}})
            period_fields = {'part': part, 'period_part': part, 'period_quote': text}
            current_variant = dict({key: value for key, value in base.items() if key != 'published_at'},
                availability=_literal_schema(current), publication_mode={'type': 'string', 'enum': ['unknown_current_original']},
                review=current_review,
                record_locator={'anyOf': [object_schema(period_fields),
                    object_schema(dict(period_fields, unit_part=part))]})
            anchors = temporal.get('date_candidates', []) if current_only else []
            if anchors:
                for anchor in anchors:
                    locator = dict(part=part, period_part=_literal_schema(anchor['part']),
                        period_quote=_literal_schema(anchor['quote']))
                    anchored = dict(current_variant, period=_literal_schema(anchor['date']),
                        record_locator={'anyOf': [object_schema(locator), object_schema(dict(locator, unit_part=part))]})
                    variants.append(anchored)
                    variants.append(dict(anchored, observation_date=_literal_schema(anchor['date'])))
            else:
                variants.append(current_variant)
                variants.append(dict(current_variant, observation_date=text))
        for variant in variants:
            choices.append(object_schema(dict(variant, unit={'type': 'string', 'enum': ['不适用']})))
            if not financial:
                choices.append(object_schema(dict(variant, unit=text, raw_unit=text, raw_token=text,
                    raw_value={'type': 'number'}, value={'type': 'number'}, conversion_factor={'type': 'number'})))
                continue
            cells = source.get('financial_table_cells', {}).get('cells', [])
            if not cells:
                continue
            context_keys = sorted({key for method in FINANCIAL_PERIOD_METHODS
                for key in METHOD_REGISTRY[method]['binding_context']})
            context_values = {key:text for key in context_keys}
            context_values['metric'] = {'type':'string','enum':sorted({c['metric'] for c in cells})}
            context_values['period'] = {'type':'string','enum':sorted({c['period'] for c in cells})}
            basis = object_schema({'kind':{'type':'string','enum':['material']},
                'artifact_id':_literal_schema(source['artifact_id']),
                'snapshot_hash':_literal_schema(source['snapshot_hash']), 'part':part,'quote':text})
            compact = {key:value for key,value in variant.items()
                       if key not in ('text','period','record_locator')}
            compact.update(financial_value_reference={'type':'string','enum':[c['candidate_id'] for c in cells]},
                calculation_context=object_schema(context_values),
                calculation_context_basis=object_schema({key:basis for key in context_keys}))
            if 'availability' in compact:
                compact['record_locator']=object_schema({'period_part':part,'period_quote':text})
                compact['observation_date']=text
            else:
                compact['record_locator']=object_schema({})
            choices.append(object_schema(compact))
            financial_choices += 1
    if financial and not financial_choices:
        return None
    return object_schema({'version': {'type': 'integer', 'enum': [1]},
        'evidence_specs': array_schema({'anyOf': choices})})


def decode_material_extraction_output(wire, schema):
    if not _matches(wire, schema):
        raise StructuredOutputError('schema_mismatch', '结构化原件提取不符合本次实际读取格式，未采用提案',
                                    schema_diagnostic(wire, schema))
    if len(wire['evidence_specs']) > 1000:
        raise StructuredOutputError('invalid_extraction_size', '结构化原件提取超出数量上限，未采用提案')
    result = copy.deepcopy(wire['evidence_specs'])
    for spec in result:
        if spec.pop('publication_mode', None) == 'unknown_current_original':
            # The explicit wire mode selects the existing business contract;
            # it neither supplies a first-publication date nor approves use.
            spec['published_at'] = None
            spec['publication'] = {'at': None, 'basis_ref': None, 'precision': '未知', 'timezone': '未知',
                'certainty': '未知', 'basis': '首次公开时间未知，当前可得性另由实际取数账本核验'}
        pairs = spec['review'].pop('evidence_need_texts')
        mapped = {}
        for item in pairs:
            if not item['texts'] or item['question_id'] in mapped:
                raise StructuredOutputError('invalid_need_mapping', '结构化原件需求关联为空或重复，未采用提案')
            mapped[item['question_id']] = item['texts']
        if mapped:
            spec['review']['evidence_need_texts'] = mapped
    return {'evidence_specs': result}


def decode_financial_extraction_output(wire, schema, context=None):
    return _decode_financial_extraction_output(wire, schema, context)


def _decode_financial_extraction_output(wire, schema, context, *, contract=None):
    if not _matches(wire,schema):
        raise StructuredOutputError('schema_mismatch','结构化财务提取不符合本次固定提交格式，未采用提案',
                                    schema_diagnostic(wire,schema))
    if wire['version'] in (5,6,7):
        if not isinstance(context,tuple) or len(context)!=2:
            raise StructuredOutputError('missing_source_binding','财务行引用缺少本任务实际读取绑定，未采用提案')
        task,views=context
        if contract is None:
            contract = _financial_extraction_contract(task, views)
        expected = (_financial_extraction_v5_schema(task, views, contract) if wire['version'] == 5
                    else _financial_extraction_output_schema(task, views, contract))
        if expected != schema:
            raise StructuredOutputError('source_binding_changed','财务行引用的任务或来源已改变，未采用提案')
        from .financial_sources import (original_line_index, original_date_anchors,
                                        selected_original_span, financial_quote_choices)
        source=contract['sources'][0]
        quotes = ({choice['candidate_id']: choice for choice in financial_quote_choices(task, views[0])}
                  if wire['version'] == 7 and wire['text_quotes'] else {})
        requirements={qid:{need['id']:need['text'] for need in needs}
                      for qid,needs in contract['question_requirements'].items()}
        lines=original_line_index(views[0]);dates=original_date_anchors(views[0])
        specs=[]
        def span_reference(span,field,index):
            try:return selected_original_span(lines,span)
            except ContractError as error:
                raise StructuredOutputError('invalid_source_span',str(error),{'version':1,
                    'field':field,'specification_index':index,'evidence_approved':False,'automatic_repair':False}) from None
        for numeric,name in ((True,'financial_cells'),(False,'text_quotes')):
            for item_index,item in enumerate(wire[name]):
                spec=copy.deepcopy(item)
                anchor=dates[spec.pop('observation_anchor')]
                spec.update(artifact_id=wire['source_reference']['artifact_id'],
                    availability=copy.deepcopy(source['current_availability_reference']),
                    published_at=None,publication={'at':None,'basis_ref':None,'precision':'未知','timezone':'未知',
                        'certainty':'未知','basis':'首次公开时间未知，当前可得性另由实际取数账本核验'},
                    observation_date=anchor['date'],record_locator={'period_part':anchor['part'],'period_quote':anchor['quote']})
                proposed_review=spec.pop('review');mapped={}
                pairs=proposed_review['need_links']
                for pair in pairs:
                    if not pair['need_ids'] or pair['question_id'] in mapped or len(set(pair['need_ids']))!=len(pair['need_ids']):
                        raise StructuredOutputError('invalid_need_mapping','财务提取需求关联为空或重复，未采用提案')
                    mapped[pair['question_id']]=[requirements[pair['question_id']][ident] for ident in pair['need_ids']]
                spec['review']={'question_ids':copy.deepcopy(spec['question_ids']),
                    'allowed_natures':[spec['nature']],'conditions':[
                        '仅本任务当前取得版本及明确观测日期，不证明历史可得性或事件因果。',
                        '口径、数字、来源与所关联问题仍由内核核验；不直接证明盈利质量或投资收益。'],
                    'reason':'显式选择本原件位置、逐字段口径行及研究需求，提交来源与适用性核验，不自签通过。',
                    'temporal_use':'current_version'}
                if mapped:spec['review']['evidence_need_texts']=mapped
                if numeric:
                    if wire['version'] in (6,7):
                        selected=spec.pop('financial_context_reference')
                        cell=next(cell for cell in source['financial_context_cells'] if cell['candidate_id']==selected)
                        spec['financial_value_reference']=cell['financial_value_reference']
                        values=copy.deepcopy(cell['context']);bases=copy.deepcopy(cell['bases'])
                        for field,ident in spec.pop('shared_context_references').items():
                            choice=next(choice for choice in source['financial_shared_context_choices'][field]
                                if choice['candidate_id']==ident)
                            values[field]=choice['value'];bases[field]=copy.deepcopy(choice['basis'])
                        spec['calculation_context']=values
                        spec['calculation_context_basis']={field:dict(kind='material',
                            artifact_id=wire['source_reference']['artifact_id'],snapshot_hash=wire['source_reference']['snapshot_hash'],
                            **basis) for field,basis in bases.items()}
                    else:
                        spec['calculation_context_basis']={key:dict(kind='material',
                            artifact_id=wire['source_reference']['artifact_id'],snapshot_hash=wire['source_reference']['snapshot_hash'],
                            **span_reference(span,key,item_index)) for key,span in spec.pop('context_spans').items()}
                else:
                    if wire['version']==7:
                        choice = quotes[spec.pop('quote_reference')]
                        quote={'part':choice['part'],'quote':choice['quote']}
                    else:
                        quote=span_reference(spec.pop('span'),'text',item_index)
                    spec['record_locator']['part']=quote['part'];spec['text']=quote['quote'];spec['unit']='不适用'
                specs.append(spec)
        if len(specs)>1000:
            raise StructuredOutputError('invalid_extraction_size','财务提取超出数量上限，未采用提案')
        return {'evidence_specs':specs}
    specs=wire['financial_cells']+wire['text_quotes']
    canonical_schema=object_schema({'version':{'type':'integer','enum':[1]},
        'evidence_specs':array_schema({'anyOf':[schema['properties'][key]['items']
                                              for key in ('financial_cells','text_quotes')]})})
    return decode_material_extraction_output({'version':1,'evidence_specs':specs},canonical_schema)


def _index_weight_extraction_schema(task,views):
    from .index_sources import weight_table_choices
    from .data_requirements import normalized,unit_matches,period_matches
    choices=weight_table_choices(task,views)
    if not choices or len(choices)!=len(views):return None
    variants=[]
    for choice in choices:
        links=[]
        for question in task['questions']:
            constraints=normalized(question)
            if constraints is None:continue
            refs=['n'+str(index) for index,text in enumerate(question['evidence_needs'],1)
                if period_matches(choice['observation_date'],constraints[text]['period_kind'])
                and (constraints[text]['value_kind']=='text' or unit_matches('%',constraints[text]['unit_dimension']))]
            if refs:links.append(object_schema(dict(question_id=dict(type='string',enum=[question['id']]),
                need_ids=array_schema(dict(type='string',enum=refs)))))
        if not links:continue
        review=object_schema(dict(need_links=array_schema(dict(anyOf=links)),
            allowed_natures=array_schema(dict(type='string',enum=['已实现事实'])),
            conditions=array_schema(dict(type='string')),reason=dict(type='string')))
        variants.append(object_schema(dict(index_weight_table_selection=dict(type='string',enum=[choice['reference']]),review=review)))
    return (object_schema(dict(version=dict(type='integer',enum=[4]),evidence_specs=array_schema(dict(anyOf=variants))))
            if variants else None)


def api_extraction_output_schema(task, views):
    """One explicit current-snapshot container, with original financial gates.

    Only complete catalogues of actual registered cells qualify. This does not
    truncate a larger response, infer context or turn locations into approval.
    Historical, mixed-source and raw-only work retains its existing protocol.
    """
    from .contracts import DOCUMENT_CONTENT_INTENT
    from .reasoning import evidence_contract
    if (task.get('research_mode') != 'current' or task.get('intent') == DOCUMENT_CONTENT_INTENT
            or not isinstance(views, list) or not views
            or any(not isinstance(v, dict) or v.get('kind') != 'api' for v in views)):
        return None
    index_schema=_index_weight_extraction_schema(task,views)
    if index_schema is not None:return index_schema
    contract = evidence_contract(task, views)
    if contract['source_bindings_omitted'] or not contract['question_ids']:
        return None
    text = {'type': 'string'}
    question = {'type': 'string', 'enum': contract['question_ids']}
    nature = {'type': 'string', 'enum': contract['natures']}
    need_choices = [object_schema({'question_id': {'type': 'string', 'enum': [ident]},
        'texts': array_schema({'type': 'string', 'enum': [n['text'] for n in items]})})
        for ident, items in contract['question_requirements'].items() if items]
    coverage = array_schema({'anyOf': need_choices}) if need_choices else array_schema(object_schema({
        'question_id': question, 'texts': array_schema(text)}))
    review = object_schema({'question_ids': array_schema(question), 'allowed_natures': array_schema(nature),
        'conditions': array_schema(text), 'reason': text, 'evidence_need_texts': coverage})
    # New ordinary tasks may explicitly select complete, source-bound close
    # cells. Their existing decoder restores the original row/context and
    # still runs both financial gates; no values or approvals are inferred.
    # Incomplete catalogues and every other API retain the original grammar.
    selection_choices = []
    if task.get('api_cell_selection_encoding') == 1:
        for source in contract['sources']:
            catalogue = source.get('cell_choice_coverage', {})
            offered = source.get('cell_choices', [])
            if (not catalogue.get('selection_complete') or not offered
                    or catalogue.get('eligible_positions') != len(offered)
                    or catalogue.get('choices_hash') != fingerprint(offered)
                    or catalogue.get('snapshot_hash') != source['snapshot_hash']):
                selection_choices = []
                break
            from .data_requirements import api_cell_need_groups
            groups=api_cell_need_groups(task,offered)
            if groups is None:
                groups=[dict(candidate_ids=[item['candidate_id'] for item in offered],need_choices=None)]
            for group in groups:
                scoped_review=copy.deepcopy(review)
                short_needs=task.get('api_need_compatibility_version')==2
                if group['need_choices'] is not None:
                    choices=[]
                    for qid,texts in group['need_choices'].items():
                        values=texts
                        if short_needs:
                            question_record=next(q for q in task['questions'] if q['id']==qid)
                            values=['n'+str(index) for index,need in enumerate(question_record['evidence_needs'],1)
                                if need in texts]
                        choices.append(object_schema(dict(question_id=dict(type='string',enum=[qid]),
                            **{('need_ids' if short_needs else 'texts'):array_schema(dict(type='string',enum=values))})))
                    field='need_links' if short_needs else 'evidence_need_texts'
                    scoped_review['properties'][field]=(
                        array_schema(dict(anyOf=choices)) if choices
                        else dict(array_schema(object_schema({})),maxItems=0))
                fields={
                    'api_cell_selections': array_schema({'type': 'string','enum':group['candidate_ids']}),
                    'question_ids': array_schema(question), 'review': scoped_review}
                if short_needs:
                    fields.pop('question_ids')
                    for field in ('question_ids','evidence_need_texts'):
                        scoped_review['properties'].pop(field,None)
                        scoped_review['required'].remove(field)
                    scoped_review['required'].append('need_links')
                selection_choices.append(object_schema(fields))
        if selection_choices:
            return object_schema({'version': {'type': 'integer', 'enum': [3 if task.get('api_need_compatibility_version')==2 else 2]},
                'evidence_specs': array_schema({'anyOf': selection_choices})})
    objects = {'type': 'string', 'enum': task['objects']} if task['objects'] else text
    planned_methods = {method for q in task.get('questions', []) for method in q.get('method_needs', [])}
    planned_methods.update(c.get('method') for c in task.get('calculation_specs', []))
    context_variants = []
    for method, definition in METHOD_REGISTRY.items():
        if method not in planned_methods or not definition.get('binding_context'):
            continue
        variants = [[]]
        for field in definition['binding_context']:
            variants = [prefix + [option] for prefix in variants for option in field.split('或')]
        for fields in variants:
            if fields not in context_variants:
                context_variants.append(fields)
    choices = []
    for source in contract['sources']:
        cells = source.get('api_cells', {})
        if (not cells.get('scan_complete') or not cells.get('cells')
                or not source.get('value_reference') or not source.get('current_availability_reference')):
            return None
        positions = [object_schema({key: _literal_schema(value) for key, value in
            cell['proposal_fields']['record_locator'].items()}) for cell in cells['cells']]
        codes = list(dict.fromkeys(c['source_metadata']['provider_code'] for c in cells['cells']))
        base = {'id': _identifier_schema(), 'artifact_id': _literal_schema(source['artifact_id']),
            'object': objects, 'record_locator': {'anyOf': positions}, 'nature': nature,
            'question_ids': array_schema(question), 'review': review,
            'api_value_reference': object_schema({key: _literal_schema(value) for key, value in source['value_reference'].items()}),
            'availability': object_schema({key: _literal_schema(value) for key, value in source['current_availability_reference'].items()}),
            'publication_mode': {'type': 'string', 'enum': ['unknown_current_snapshot']}}
        identities = []
        if source.get('direct_code_reference'):
            identities.append({'direct_code_reference': _literal_schema(source['direct_code_reference'])})
        for reference in source.get('candidate_code_references', []):
            identities.append({'candidate_code_reference': _literal_schema(reference)})
        for basis in contract['identity_period_provenance']['admissible_bases']:
            if basis['identity']['provider_code'] in codes:
                identities.append({'identity': _literal_schema(basis['identity'])})
        if not identities:
            return None  # No usable identity source, not an invitation to invent dates.
        for identity in identities:
            variant = dict(base, **identity)
            choices.append(object_schema(variant))
            if source.get('compact_context_reference'):
                for fields in context_variants:
                    choices.append(object_schema(dict(variant,
                        calculation_context=object_schema({key: text for key in fields}),
                        api_context_reference=object_schema({key: _literal_schema(value) for key, value in
                            source['compact_context_reference'].items()}))))
    return object_schema({'version': {'type': 'integer', 'enum': [1]},
        'evidence_specs': array_schema({'anyOf': choices})})


def decode_api_extraction_output(wire, schema, context=None):
    if not _matches(wire, schema):
        raise StructuredOutputError('schema_mismatch', '结构化接口提取不符合本次实际响应格式，未采用提案',
                                    schema_diagnostic(wire, schema))
    if len(wire['evidence_specs']) > 1000:
        raise StructuredOutputError('invalid_extraction_size', '结构化接口提取超出数量上限，未采用提案')
    specs = copy.deepcopy(wire['evidence_specs'])
    short_needs=wire['version'] in (3,4)
    if short_needs:
        if not isinstance(context,tuple) or len(context)!=2:
            raise StructuredOutputError('missing_api_need_context','接口需求短引用缺少当前任务，不采用提案')
        task,views=context
        from .planning import input_fingerprint
        if (task.get('api_need_compatibility_version')!=2 or task['input_hash']!=input_fingerprint(task)):
            raise StructuredOutputError('invalid_api_need_context','接口需求短引用与当前任务不一致，不采用提案')
        need_map={q['id']:{'n'+str(i):text for i,text in enumerate(q['evidence_needs'],1)} for q in task['questions']}
    for spec in specs:
        if short_needs:
            links=spec['review'].pop('need_links')
            if not links or any(not link['need_ids'] for link in links):
                raise StructuredOutputError('invalid_need_mapping','接口观测须显式关联实际支持的问题需求')
            pairs=[]
            for link in links:
                if link['question_id'] not in need_map or any(ref not in need_map[link['question_id']] for ref in link['need_ids']):
                    raise StructuredOutputError('invalid_need_mapping','接口需求短引用不属于当前问题')
                pairs.append(dict(question_id=link['question_id'],texts=[need_map[link['question_id']][ref] for ref in link['need_ids']]))
            spec['question_ids']=[link['question_id'] for link in links]
            spec['review'].update(question_ids=copy.deepcopy(spec['question_ids']),evidence_need_texts=pairs)
        # An explicit enum selects the existing unknown-time business variant.
        # No publication time, value, unit or calculation context is invented.
        if wire['version'] == 1:
            spec.pop('publication_mode')
            spec['published_at'] = None
            spec['publication'] = {'at': None, 'basis_ref': None, 'precision': '未知', 'timezone': '未知',
                'certainty': '未知', 'basis': '首次公开时间未知，当前可得性另由实际取数账本核验'}
        elif wire['version'] in (2,3) and not 1 <= len(spec['api_cell_selections']) <= 64:
            raise StructuredOutputError('invalid_cell_selection_size', '接口单元选择须为1至64个实际候选，未采用提案')
        pairs = spec['review'].pop('evidence_need_texts')
        mapped = {}
        for item in pairs:
            if not item['texts'] or item['question_id'] in mapped:
                raise StructuredOutputError('invalid_need_mapping', '结构化接口需求关联为空或重复，未采用提案')
            mapped[item['question_id']] = item['texts']
        if mapped:
            spec['review']['evidence_need_texts'] = mapped
    return {'evidence_specs': specs}


def planning_protocol(task):
    return ((PLAN_REQUEST_PROTOCOL, PLAN_REQUEST_FUNCTION_NAME)
            if task.get('planning_request_encoding') == 2 else (PLAN_PROTOCOL, PLAN_FUNCTION_NAME))


def _identity_profile_binding(candidates=False):
    from .contracts import fingerprint
    from .evidence import IDENTITY_EXACT_PROFILE, IDENTITY_PARAMETERS_BY_MODE
    profile = dict(profile=IDENTITY_EXACT_PROFILE, parameters=IDENTITY_PARAMETERS_BY_MODE)
    if candidates:
        from .evidence import IDENTITY_CANDIDATE_PROFILE, identity_query_parameters
        profile['candidate_profile'] = dict(profile=IDENTITY_CANDIDATE_PROFILE,
            parameters=identity_query_parameters('secname','query',IDENTITY_CANDIDATE_PROFILE))
    return 'identity_request_profile:' + fingerprint(profile)


def planning_output_schema(task):
    """Typed plans cover all registered tools, not a material-only shortcut.

    IDs, periods, sources and assumptions remain explicit model proposals.
    Schemas approve no evidence and do not weaken validate_plan. Runtime
    registration is the only authority for request and method definitions.
    """
    from .planning import QUESTION_KINDS, planning_scope_policy
    from .contracts import methods_for_task
    methods = methods_for_task(task)
    text = {'type': 'string'}
    shared = {'id': _identifier_schema(), 'question': text,
              'required': {'type': 'boolean','description':
                  '直接回答主问题或核验关键判断所必需时为true；辅助指标、拓展背景独立为false子问题。'
                  '不把知识包检查清单全部设为true；核心问题不能依赖仅辅助的扩展问题。'
                  '关键反证、身份、适用口径和用户明确要求不能降级。'}, 'group': _identifier_schema()}
    ordinary = dict(shared, kind={'type': 'string', 'enum': list(QUESTION_KINDS)},
        dependencies=array_schema(_identifier_schema()), evidence_needs=array_schema(text),
        method_needs=array_schema({'type': 'string', 'enum': list(methods)}))
    scope = planning_scope_policy(task)
    if scope is not None:
        ordinary['kind']['description'] = scope['rule']
    from .data_requirements import enabled as typed_needs,DIMENSIONS,PERIODS,GUIDANCE
    if typed_needs(task):
        need=object_schema(dict(text=text,
            value_kind={'type':'string','enum':['text','number']},
            unit_dimension={'type':'string','enum':list(DIMENSIONS)},
            minimum_count={'type':'integer','minimum':1,'maximum':1000},
            period_kind={'type':'string','enum':list(PERIODS)}))
        if task.get('typed_observation_contract_version')==2:
            number=copy.deepcopy(need);literal=copy.deepcopy(need)
            number['properties']['value_kind']={'type':'string','enum':['number']}
            number['properties']['unit_dimension']={'type':'string','enum':[d for d in DIMENSIONS if d!='not_applicable']}
            literal['properties']['value_kind']={'type':'string','enum':['text']}
            literal['properties']['unit_dimension']={'type':'string','enum':['not_applicable']}
            need={'anyOf':[number,literal]}
        from .period_targets import enabled as period_targets_enabled, wire_schema as period_target_schema, GUIDANCE as PERIOD_GUIDANCE
        if period_targets_enabled(task):
            # Keep ordinary observations compact and compatible. A distinct
            # exact shape permits an explicit series goal only where needed.
            variants = need.get('anyOf', [need])
            targeted = copy.deepcopy(variants[0])
            targeted['properties']['value_kind'] = {'type': 'string', 'enum': ['number']}
            targeted['properties']['unit_dimension'] = {'type': 'string', 'enum': ['percentage']}
            targeted['properties']['period_kind'] = {'type': 'string', 'enum': ['month']}
            targeted['properties']['period_target'] = period_target_schema()
            targeted['required'].append('period_target')
            need = {'anyOf': variants + [targeted]}
        ordinary['evidence_needs']=array_schema(need)
        ordinary['evidence_needs']['description']=GUIDANCE + (PERIOD_GUIDANCE if period_targets_enabled(task) else '')
    query = dict(ordinary, kind={'type': 'string', 'enum': ['fact']},
        evidence_need_scopes=array_schema(object_schema({'need': text,
            'scope': {'type': 'string', 'enum': ['query_provenance'] +
                (['market_calendar_coverage'] if type(task.get('market_coverage_basis_version')) is int
                    and task['market_coverage_basis_version'] == 1 else [])}})))
    general = dict(shared, kind={'type': 'string', 'enum': ['fact']},
        answer_scope={'type': 'string', 'enum': ['general_definition']},
        knowledge_needs=array_schema(_identifier_schema()))
    question_shapes = [object_schema(ordinary), object_schema(query)]
    if task.get('definition_projection_version') == 1:
        from .knowledge import ResearchKnowledge
        definitions = [entry['id'] for entry in ResearchKnowledge().planning_definitions(task)['candidates']]
        if definitions:
            general['knowledge_needs'] = array_schema({'type': 'string', 'enum': definitions})
            general['knowledge_needs']['minItems'] = 1
            question_shapes.append(object_schema(general))
    else:
        question_shapes.append(object_schema(general))
    calculations = []
    for method, definition in methods.items():
        parameters = {key: ({'type': 'string', 'enum': definition['parameter_variants'][key]}
                            if key in definition.get('parameter_variants', {}) else _literal_schema(value))
                      for key, value in definition['parameters'].items()}
        parameters.update({key: text for key in definition.get('context_parameters', [])})
        fields = {'id': _identifier_schema(), 'method': {'type': 'string', 'enum': [method]},
            'inputs': object_schema({name: _identifier_schema() for name in definition['input_units']}),
            'parameters': object_schema(parameters), 'question_ids': array_schema(_identifier_schema())}
        if definition.get('assumption_units'):
            number = {'type': 'number'}
            fields['assumptions'] = object_schema({name: number for name in definition['assumption_units']})
            fields['assumption_sources'] = object_schema({name: object_schema({
                'source': {'type': 'string', 'enum': ['user_question']}, 'quote': text,
                'raw_token': text, 'raw_unit': text, 'factor': number, 'value': number})
                for name in definition['assumption_units']})
        calculations.append(object_schema(fields))
    version = 2 if task.get('planning_request_encoding') == 2 else 1
    result = object_schema({'version': {'type': 'integer', 'enum': [version]},
        'questions': array_schema({'anyOf': question_shapes}),
        'data_requests': array_schema({'anyOf': _planned_request_schemas(task)}),
        'calculations': array_schema({'anyOf': calculations})})
    if version == 2:
        result['description'] = _identity_profile_binding(task.get('identity_candidate_lookup_version') in (1,2))
    from .planning import initial_decomposition, plan_preservation_contract
    if (type(task.get('planning_main_binding_version')) is int
            and task['planning_main_binding_version'] == 1 and initial_decomposition(task)):
        main = plan_preservation_contract(task)['protected_questions'][0]
        main_shapes = copy.deepcopy(question_shapes)
        for shape in main_shapes:
            shape['properties']['id'] = _literal_schema(main['id'])
            for field, value in main['exact_fields'].items():
                shape['properties'][field] = _literal_schema(value)
        result['properties'].pop('questions')
        result['properties'].update(main_question={'anyOf': main_shapes},
            additional_questions=array_schema({'anyOf': copy.deepcopy(question_shapes)}))
        result['properties']['version'] = _literal_schema(3)
        result['required'] = list(result['properties'])
    return result


def reading_output_schema(task, description):
    """Choose real source spans; a model never needs to invent byte offsets."""
    from .source_reader import catalog, binding
    if not isinstance(description, dict) or description.get('binding') != binding(task):
        raise ContractError('读取目录与当前研究输入不一致')
    items = description.get('items')
    if not isinstance(items, list) or not items or len(items) > 24:
        raise ContractError('读取目录没有有界原件列表')
    ids = [item.get('id') for item in items if isinstance(item, dict)]
    expected = catalog(task, ids)
    if items != expected['items']:
        raise ContractError('读取目录不是本任务实际原件目录')
    questions = [question['id'] for question in task['questions']]
    choices = []
    for item in items:
        if item['kind'] not in ('material', 'filing'):
            continue
        spans = item['reading_spans']
        # No available span means an empty array; keyword search remains able
        # to locate deeper pages throughout the original, within reader bounds.
        span_schema = ({'anyOf': [_literal_schema(span) for span in spans]} if spans else
                       object_schema({'part': {'type': 'string'}, 'start': {'type': 'integer'},
                                      'end': {'type': 'integer'}}))
        choices.append(object_schema({
            **{key: _literal_schema(value) for key, value in item['request_binding'].items()},
            'queries': array_schema({'type': 'string'}), 'parts': array_schema(span_schema),
            'reason': {'type': 'string'},
            'question_ids': array_schema({'type': 'string', 'enum': questions})}))
    if not choices or not questions:
        raise ContractError('当前目录没有可读文字原件或研究问题')
    return object_schema({'version': {'type': 'integer', 'enum': [1]},
                          'requests': array_schema({'anyOf': choices})})


def decode_reading_output(wire, schema):
    if not _matches(wire, schema) or not 1 <= len(wire['requests']) <= 12:
        raise StructuredOutputError('invalid_reading_selection', '读取选择不符合本任务原件目录，未采用提案',
                                    schema_diagnostic(wire, schema))
    return {'requests': copy.deepcopy(wire['requests'])}


def reading_reference_description(task, description):
    """Short choices over the exact catalog, never invented source aliases."""
    from .contracts import fingerprint
    reading_output_schema(task, description)  # Reproduce the actual catalog.
    sources = []
    for item in description['items']:
        if item['kind'] not in ('material', 'filing'):
            continue
        overview = {key: copy.deepcopy(value) for key, value in item.items()
                    if key not in ('id','snapshot_hash','request_binding','reading_spans')}
        number = len(sources)+1
        sources.append(dict(source_ref='source_' + str(number), overview=overview,
            span_choices=[dict(span_ref='span_' + str(number) + '_' + str(index+1), **copy.deepcopy(span))
                          for index, span in enumerate(item['reading_spans'])]))
    return dict(version=1, binding_ref=fingerprint(description['binding'])[:16], sources=sources)


def reading_reference_output_schema(task, description):
    choices = reading_reference_description(task, description)
    questions = [item['id'] for item in task['questions']]
    branches = []
    for item in choices['sources']:
        spans = [span['span_ref'] for span in item['span_choices']]
        span_array = array_schema({'type':'string','enum':spans}) if spans else dict(
            type='array', items={'type':'string'}, maxItems=0)
        branches.append(object_schema(dict(source_ref=_literal_schema(item['source_ref']),
            queries=dict(array_schema({'type':'string'}), maxItems=8),
            span_refs=dict(span_array, maxItems=min(8,len(spans))), reason={'type':'string'},
            question_ids=dict(array_schema({'type':'string','enum':questions}), minItems=1))))
    return object_schema(dict(version=_literal_schema(1), binding_ref=_literal_schema(choices['binding_ref']),
        requests=dict(array_schema({'anyOf':branches}), minItems=1, maxItems=12)))


def decode_reading_references(wire, schema, task, description):
    choices = reading_reference_description(task, description)
    if schema != reading_reference_output_schema(task, description) or not _matches(wire,schema):
        raise StructuredOutputError('invalid_reading_reference', '读取短引用不属于本次真实目录，未采用选择',
                                    schema_diagnostic(wire,schema))
    if not 1 <= len(wire['requests']) <= 12:
        raise StructuredOutputError('invalid_reading_reference_count', '读取选择数量无效，不补默认原件')
    sources = {item['source_ref']:item for item in choices['sources']}
    originals = [item for item in description['items'] if item['kind'] in ('material','filing')]
    requests = []
    for item in wire['requests']:
        source = sources[item['source_ref']]
        index = choices['sources'].index(source)
        original = originals[index]
        spans = {span['span_ref']:{key:value for key,value in span.items() if key != 'span_ref'}
                 for span in source['span_choices']}
        if (len(item['queries']) > 8 or len(item['span_refs']) > 8
                or len(set(item['span_refs'])) != len(item['span_refs'])
                or any(ref not in spans for ref in item['span_refs'])
                or not (item['queries'] or item['span_refs'])):
            raise StructuredOutputError('invalid_reading_span_reference', '读取页段为空、重复或不属于所选原件')
        requests.append(dict(copy.deepcopy(original['request_binding']), queries=copy.deepcopy(item['queries']),
            parts=[copy.deepcopy(spans[ref]) for ref in item['span_refs']], reason=item['reason'],
            question_ids=copy.deepcopy(item['question_ids'])))
    return decode_reading_output(dict(version=1,requests=requests),reading_output_schema(task,description))


def decode_planning_output(wire, schema):
    if not _matches(wire, schema):
        raise StructuredOutputError('schema_mismatch', '结构化规划不符合本任务格式，未采用提案',
                                    schema_diagnostic(wire, schema))
    if wire['version'] == 3:
        wire = dict(wire, questions=[wire['main_question']] + wire['additional_questions'])
        ids = [question['id'] for question in wire['questions']]
        if len(ids) != len(set(ids)):
            raise StructuredOutputError('duplicate_plan_question', '规划问题标识重复，不覆盖用户主问题')
    if not wire['questions'] or any(len(wire[name]) > 50 for name in ('questions', 'data_requests', 'calculations')):
        raise StructuredOutputError('invalid_plan_size', '结构化规划问题或动作数量无效，未采用提案')
    result = {name: copy.deepcopy(wire[name]) for name in ('questions', 'data_requests', 'calculations')}
    _decode_series_parameter_selections(result['data_requests'], schema)
    if wire['version'] in (2, 3):
        _decode_identity_requests(result['data_requests'], schema)
    _decode_semantic_requests(result['data_requests'])
    for question in result['questions']:
        needs=question.get('evidence_needs',[])
        if needs and isinstance(needs[0],dict):
            constraints={}
            for need in needs:
                if need['text'] in constraints:
                    raise StructuredOutputError('duplicate_typed_need','数据需求不能重复覆盖')
                constraints[need['text']]={key:value for key,value in need.items() if key not in ('text', 'period_target')}
            targets = {need['text']: copy.deepcopy(need['period_target']) for need in needs if need.get('period_target') is not None}
            question['evidence_needs']=list(constraints)
            question['evidence_need_constraints']=constraints
            if targets: question['evidence_period_targets'] = targets
        if 'evidence_need_scopes' in question:
            pairs = question.pop('evidence_need_scopes')
            scopes = {}
            for item in pairs:
                if item['need'] in scopes:
                    raise StructuredOutputError('duplicate_evidence_scope', '结构化规划需求用途重复，未覆盖条目')
                scopes[item['need']] = item['scope']
            if scopes:
                question['evidence_need_scopes'] = scopes
    return result


def _analysis_output_schema_v1(task, selected=None):
    ids = [q['id'] for q in task['questions']]
    selected = ids if selected is None else selected
    if (not isinstance(selected, list) or not selected or len(selected) > 50
            or not all(isinstance(ident, str) and ident in ids for ident in selected)
            or len(set(selected)) != len(selected)):
        raise ContractError('结构化分析的问题范围无效')
    text = {'type': 'string'}
    references = array_schema(text)
    question_ids = {'type': 'string', 'enum': list(selected)}
    from .contracts import evidence_applies_to_answer, calculation_applies_to_answer
    dependency_policy = task.get('dependency_synthesis_version') is not None
    calculation_ids = ([] if task.get('analysis_question_scope_version') == 1 else
                       [item['id'] for item in task.get('calculations', [])])

    def applies(source, item):
        if not dependency_policy:
            return bool(set(selected).intersection(item.get('question_ids', [])))
        return any(evidence_applies_to_answer(task, item, ident, calculation_ids)
                   if source == 'evidence' else calculation_applies_to_answer(task, item, ident)
                   for ident in selected)
    paths = ['value', 'raw_value', 'premium', 'undiscounted']
    for calculation in task.get('calculations', []):
        if applies('calculation', calculation):
            paths.extend(analysis_quantity_paths(calculation.get('method')))
    quantity = object_schema({'source': {'type': 'string', 'enum': ['evidence', 'calculation']},
        'ref': text, 'path': {'type': 'string', 'enum': list(dict.fromkeys(paths))},
        'format': {'type': 'string', 'enum': ['signed', 'comparison']},
        'digits': {'type': 'integer', 'minimum': 0, 'maximum': 8}})
    quantity_choices = [quantity]
    if 'evidence' in task and 'calculations' in task:
        # Full engine tasks carry an actual quantity catalogue. Qualitative
        # quotes must not expose a value/raw_value branch. Sparse legacy
        # transport callers keep their old container; they still gain no
        # source approval and face the same downstream financial validators.
        quantity_choices = []
        for source, items in (('evidence', task['evidence']), ('calculation', task['calculations'])):
            for item in items:
                if not applies(source, item):
                    continue
                values = item if source == 'evidence' else item.get('output', {})
                available = ('value', 'raw_value') if source == 'evidence' else calculation_quantity_paths(item.get('method'))
                available = [path for path in available if path in values and values[path] is not None]
                if source == 'calculation':
                    available += list(method_reference_quantities(item.get('method')))
                if not available or source == 'evidence' and item.get('unit') in (None, '不适用'):
                    continue
                fields = copy.deepcopy(quantity['properties'])
                fields.update(source={'type': 'string', 'enum': [source]},
                    ref={'type': 'string', 'enum': [item['id']]}, path={'type': 'string', 'enum': available})
                quantity_choices.append(object_schema(fields))
        if (type(task.get('analysis_input_presentation_version')) is int
                and task['analysis_input_presentation_version'] in (1, 2)):
            # Same source, path set and all remaining constraints: unioning
            # only ref enums is exactly the original alternatives, not a
            # cross-product between incompatible quantities or questions.
            from .contracts import canonical
            grouped = {}
            for choice in quantity_choices:
                base = copy.deepcopy(choice)
                reference_choice = base['properties'].pop('ref')
                key = canonical(base)
                if key not in grouped:
                    grouped[key] = copy.deepcopy(choice)
                else:
                    grouped[key]['properties']['ref']['enum'].extend(reference_choice['enum'])
            quantity_choices = list(grouped.values())
    from .numeric_integrity import verbatim_evidence_eligible
    quoted = [item['id'] for item in task.get('evidence', [])
              if applies('evidence', item) and verbatim_evidence_eligible(task, item)]
    quote_choices = [object_schema({'quote_ref': {'type': 'string', 'enum': quoted}})] if quoted else []
    segment_shapes = [object_schema({'text': text}), *quantity_choices, *quote_choices]
    segments = array_schema({'anyOf': segment_shapes}) if len(segment_shapes) > 1 else array_schema(segment_shapes[0])
    knowledge = array_schema(object_schema({'id': text, 'version': {'type': 'integer', 'minimum': 1}}))
    provenance = array_schema(object_schema({'version': {'type': 'integer', 'enum': [1]},
        'artifact_id': text, 'request_hash': text, 'snapshot_hash': text, 'projection_hash': text,
        'evidence_need_ids': references}))
    tracking_fields = {name: text for name in tracking_contract()['required_fields']}
    # Avoid an empty-object branch in the provider grammar. Explicit absence
    # cannot become a made-up monitoring rule or a silently filled condition.
    tracking = object_schema(dict(tracking_fields, applicable={'type': 'boolean'}, benchmark=text))
    answer_ids = list(selected)
    if task.get('definition_projection_version') == 1:
        definition_ids = {q['id'] for q in task['questions'] if q.get('answer_scope') == 'general_definition'}
        answer_ids = [ident for ident in selected if ident not in definition_ids]
        # Model answers and theses share the writable scope. Definitions are
        # projected separately; retaining them only in the thesis grammar would
        # make schema reconstruction from answer IDs non-idempotent.
        question_ids = {'type': 'string', 'enum': answer_ids or list(selected)}
    answers = object_schema({'question_id': {'type': 'string', 'enum': answer_ids or list(selected)}, 'segments': segments,
        'refs': references, 'calculation_refs': references, 'knowledge_refs': knowledge,
        'provenance_refs': provenance})
    theses = object_schema({'title': segments, 'judgment': {'type': 'string', 'enum': ['支持', '反对', '证据不足']},
        'question_ids': array_schema(question_ids), 'support': references, 'counter': references,
        'calculation_refs': references, 'investment_meaning': segments, 'limitation': segments,
        'knowledge_refs': knowledge, 'tracking': tracking})
    properties = {'version': {'type': 'integer', 'enum': [VERSION]},
                  'answers': array_schema(answers), 'theses': array_schema(theses)}
    if task.get('definition_projection_version') == 1 and not answer_ids:
        properties['answers']['maxItems'] = 0
        properties['theses']['maxItems'] = 0
    from .judgment_review import contract as review_contract, STATES, ASSESSMENTS, EXPECTATION_KINDS
    review = review_contract(task, selected)
    if review is not None:
        dimension_schemas = {}
        for key, states in STATES.items():
            base = dict(state={'type': 'string', 'enum': list(states)}, segments=segments,
                        refs=references, calculation_refs=references)
            if key == 'expectations':
                neutral = dict(base, state={'type': 'string', 'enum': ['证据不足', '未到验证期', '不适用']})
                compared = dict(base, comparison_kind={'type': 'string', 'enum': list(EXPECTATION_KINDS)},
                                expected_refs=references, observed_refs=references)
                dimension_schemas[key] = {'anyOf': [object_schema(neutral), object_schema(compared)]}
            else:
                dimension_schemas[key] = object_schema(base)
        common = dict(assessment={'type': 'string', 'enum': list(ASSESSMENTS)},
            current_thesis_indices=array_schema({'type': 'integer', 'minimum': 0, 'maximum': 49}),
            dimensions=object_schema(dimension_schemas),
            prior_version={'type': 'integer', 'enum': [review['prior_version']]})
        shapes = [object_schema(dict(common, prior_thesis_id=_literal_schema(anchor['id']),
            prior_thesis_hash=_literal_schema(anchor['thesis_hash']), conditions_hash=_literal_schema(anchor['conditions_hash'])))
            for anchor in review['anchors']]
        # A fact node can have no eligible prior judgments. The grammar stays
        # representable; local scope validation then permits only an empty list.
        if not shapes:
            shapes = [object_schema(dict(common, prior_thesis_id=text, prior_thesis_hash=text, conditions_hash=text))]
        properties['judgment_reviews'] = array_schema({'anyOf': shapes} if len(shapes) > 1 else shapes[0])
    return research_collection_schema(object_schema(properties), task.get('analysis_input_presentation_version'))


def analysis_protocol(task):
    return ((ANALYSIS_CITATION_PROTOCOL, ANALYSIS_CITATION_FUNCTION)
            if task.get('analysis_citation_encoding') == 2 else ('strict_analysis_v1', FUNCTION_NAME))


def analysis_selected_questions(schema):
    """Recover the finite authoring scope from direct or question-bound items."""
    answers = schema['properties']['answers']
    if answers.get('type') == 'object' and schema['properties']['version'].get('enum') == [3]:
        selected = list(answers.get('properties', {}))
        if not selected or any(not isinstance(ident, str) or not ident for ident in selected):
            raise ValueError('Missing fixed question map')
        return selected
    item = schema['properties']['answers']['items']
    choices = item.get('anyOf', [item])
    selected = []
    for choice in choices:
        values = choice['properties']['question_id']['enum']
        if not isinstance(values, list) or not values or any(not isinstance(value, str) for value in values):
            raise ValueError('Invalid answer question catalogue')
        selected.extend(values)
    if not selected:
        raise ValueError('Missing answer question catalogue')
    return list(dict.fromkeys(selected))


def analysis_output_schema(task, selected=None):
    """A fixed question key owns its references; null explicitly withholds it."""
    schema = _analysis_array_schema(task, selected)
    if (type(task.get('analysis_question_map_version')) is not int or task['analysis_question_map_version'] != 1
            or task.get('analysis_citation_encoding') != 2 or task.get('analysis_question_scope_version') != 1
            or schema['properties']['answers'].get('maxItems') == 0):
        return research_collection_schema(schema, task.get('analysis_input_presentation_version'))
    questions = analysis_selected_questions(schema)
    if len(questions) < 2:
        return research_collection_schema(schema, task.get('analysis_input_presentation_version'))
    definitions = {question['id'] for question in task['questions'] if question.get('answer_scope') == 'general_definition'}
    properties = {}
    for ident in questions:
        if ident in definitions:
            properties[ident] = {'type': 'null'}
            continue
        answer = copy.deepcopy(_analysis_citation_schema(task, [ident])['properties']['answers']['items'])
        answer['properties'].pop('question_id')
        answer['required'].remove('question_id')
        properties[ident] = {'anyOf': [answer, {'type': 'null'}]}
    schema['properties']['answers'] = object_schema(properties)
    schema['properties']['version'] = _literal_schema(3)
    return research_collection_schema(schema, task.get('analysis_input_presentation_version'))


def research_collection_schema(schema, version):
    """Complete research arrays use the existing submission-size budget.

    This local policy is stripped from the supplier schema. Explicit limits,
    uniqueness, scope and value constraints remain unchanged. Older schemas
    retain their original 200-item resource policy.
    """
    if type(version) is int and version == 2:
        schema[LOCAL_ARRAY_POLICY] = 2
    return schema


def _analysis_array_schema(task, selected=None):
    """Bind each answer's quantities and prose to that answer's own question.

    The versioned producer constraint mirrors the existing consumer guard;
    it does not widen evidence eligibility or upgrade saved research.
    """
    schema = _analysis_citation_schema(task, selected)
    if task.get('analysis_citation_encoding') != 2 or task.get('analysis_question_scope_version') != 1:
        return schema
    questions = analysis_selected_questions(schema)
    if len(questions) > 1 and schema['properties']['answers'].get('maxItems') != 0:
        schema['properties']['answers']['items'] = {'anyOf': [
            _analysis_citation_schema(task, [ident])['properties']['answers']['items']
            for ident in questions]}
    return schema


def _analysis_citation_schema(task, selected=None):
    """New authors cite while writing; old canonical research is unchanged."""
    schema = _analysis_output_schema_v1(task, selected)
    if task.get('analysis_citation_encoding') != 2:
        return schema
    from .contracts import context, evidence_applies_to_answer, calculation_applies_to_answer
    selected = schema['properties']['answers']['items']['properties']['question_id']['enum']
    calculation_ids = ([] if task.get('analysis_question_scope_version') == 1 else
                       [item['id'] for item in task.get('calculations', [])])
    identifiers = [item['id'] for item in task.get('evidence', [])
        if (any(evidence_applies_to_answer(task, item, ident, calculation_ids) for ident in selected)
            if task.get('dependency_synthesis_version') is not None
            else bool(set(selected).intersection(item.get('question_ids', []))))]
    if len(set(identifiers)) != len(identifiers):
        raise ContractError('本次分析证据身份重复，未生成引用选择容器')
    reference = {'type': 'string', **({'enum': identifiers} if identifiers else {})}
    references = array_schema(reference)
    if not identifiers and task.get('analysis_question_scope_version') == 1:
        references['maxItems'] = 0
    support_references = copy.deepcopy(references)
    if task.get('analysis_prose_reference_version') == 1:
        evidence_ids = {item['id'] for item in task.get('evidence', [])}
        calculations = task.get('calculations', [])
        if evidence_ids.intersection(item['id'] for item in calculations):
            raise ContractError('证据和计算引用身份冲突，不能猜测文字引用类型')
        choices = [item['id'] for item in calculations if item.get('input_refs')
            and set(item['input_refs']).issubset(evidence_ids)
            and any(calculation_applies_to_answer(task,item,ident) for ident in selected)]
        if choices:
            support_references = array_schema({'type':'string','enum':identifiers+choices})
    prose = object_schema({'text': {'type': 'string'}, 'refs': support_references,
                           'counter_refs': copy.deepcopy(references)})

    def segments(value):
        item = value['items']
        choices = item.get('anyOf', [item])
        choices = [copy.deepcopy(prose) if 'text' in choice.get('properties', {}) else choice
                   for choice in choices]
        return array_schema({'anyOf': choices} if len(choices) > 1 else choices[0])

    def revised(value, fields, remove):
        properties = value['properties']
        for field in fields:
            properties[field] = segments(properties[field])
        for field in remove:
            properties.pop(field)
        value['required'] = list(properties)

    revised(schema['properties']['answers']['items'], ['segments'], ['refs', 'calculation_refs'])
    revised(schema['properties']['theses']['items'], ['title', 'investment_meaning', 'limitation'],
            ['support', 'counter', 'calculation_refs'])
    if 'judgment_reviews' in schema['properties']:
        item = schema['properties']['judgment_reviews']['items']
        for review in item.get('anyOf', [item]):
            for dimension in review['properties']['dimensions']['properties'].values():
                for choice in dimension.get('anyOf', [dimension]):
                    revised(choice, ['segments'], ['refs', 'calculation_refs'])
    schema['properties']['version'] = {'type': 'integer', 'enum': [2]}
    schema['properties']['binding'] = _literal_schema(context(task))
    schema['required'] = list(schema['properties'])
    return schema


def decode_analysis_citations(wire, schema, task):
    """Project only explicit citations and chosen calculations' registered inputs.

    No search, source inference, global-evidence union or semantic approval.
    The ordinary numeric, scope, independent review and save gates still run.
    """
    if not isinstance(task, dict) or task.get('analysis_citation_encoding') != 2:
        raise StructuredOutputError('missing_analysis_citation_context', '逐段引用缺少当前任务契约')
    try:
        selected = analysis_selected_questions(schema)
    except (KeyError, TypeError, ValueError):
        raise StructuredOutputError('invalid_analysis_citation_schema', '逐段引用容器无效') from None
    expected = analysis_output_schema(task, selected)
    if schema != expected or not _matches(wire, schema):
        diagnostic = {}
        if task.get('analysis_question_scope_version') == 1:
            diagnostic = (analysis_reference_diagnostic(wire, schema, task) if schema == expected else
                dict(version=1, schema_issues=[dict(path='$', code='context_schema_mismatch')],
                     automatic_repair=False, submitted_values_retained=False))
        raise StructuredOutputError('schema_mismatch', '逐段引用不属于当前任务或不符合选择格式', diagnostic)
    if schema['properties']['answers'].get('type') == 'object':
        wire = copy.deepcopy(wire)
        declared = wire['answers']
        wire['answers'] = [dict(declared[ident], question_id=ident) for ident in selected if declared[ident] is not None]
        wire['version'] = 2
    evidence = {item['id']: item for item in task.get('evidence', [])}
    calculations = {item['id']: item for item in task.get('calculations', [])}
    if len(calculations) != len(task.get('calculations', [])):
        raise StructuredOutputError('duplicate_calculation_identity', '逐段引用的计算身份重复')

    def project(groups):
        support, counter, methods = [], [], []
        def calculation_reference(ref):
            calculation = calculations.get(ref)
            inputs = calculation.get('input_refs') if calculation is not None else None
            if (not isinstance(inputs, list)
                    or not all(isinstance(value,str) and value in evidence for value in inputs)
                    or len(set(inputs)) != len(inputs)):
                raise StructuredOutputError('invalid_calculation_input_reference', '所选计算缺少完整登记输入')
            support.extend(inputs)
            methods.append(ref)
        for group in groups:
            for index, item in enumerate(group):
                if 'text' in item:
                    for field, target in (('refs', support), ('counter_refs', counter)):
                        refs = item[field]
                        if len(set(refs)) != len(refs):
                            raise StructuredOutputError('invalid_segment_reference', '文字片段引用未登记或重复')
                        for ref in refs:
                            if ref in evidence:
                                target.append(ref)
                            elif (field == 'refs' and task.get('analysis_prose_reference_version') == 1
                                    and ref in calculations):
                                calculation_reference(ref)
                            else:
                                raise StructuredOutputError('invalid_segment_reference', '文字片段引用未登记或重复')
                    group[index] = {'text': item['text']}
                elif 'quote_ref' in item:
                    if item['quote_ref'] not in evidence:
                        raise StructuredOutputError('invalid_segment_reference', '原文引用未登记')
                    support.append(item['quote_ref'])
                elif item['source'] == 'evidence':
                    if item['ref'] not in evidence:
                        raise StructuredOutputError('invalid_segment_reference', '数量原值未登记')
                    support.append(item['ref'])
                else:
                    calculation_reference(item['ref'])
        return tuple(list(dict.fromkeys(items)) for items in (support, counter, methods))

    canonical = copy.deepcopy(wire)
    canonical.pop('binding')
    canonical['version'] = VERSION
    for answer in canonical['answers']:
        support, counter, methods = project([answer['segments']])
        answer['refs'] = list(dict.fromkeys(support + counter))
        answer['calculation_refs'] = methods
    for thesis in canonical['theses']:
        thesis['support'], thesis['counter'], thesis['calculation_refs'] = project(
            [thesis[field] for field in ('title', 'investment_meaning', 'limitation')])
    for review in canonical.get('judgment_reviews', []):
        for dimension in review['dimensions'].values():
            support, counter, methods = project([dimension['segments']])
            dimension['refs'] = list(dict.fromkeys(support + counter))
            dimension['calculation_refs'] = methods
    return decode_analysis_output(canonical, _analysis_output_schema_v1(task, selected))


def analysis_reference_diagnostic(wire, schema, task):
    """Classify rejected answer refs without persisting a submitted value."""
    diagnostic = schema_diagnostic(wire, schema)
    if task.get('analysis_prose_reference_version') != 1 or not isinstance(wire,dict):
        return diagnostic
    selected = analysis_selected_questions(schema)
    answers = wire.get('answers')
    if isinstance(answers,list):
        answers = {row.get('question_id'):row for row in answers if isinstance(row,dict)
                   and isinstance(row.get('question_id'),str) and row.get('question_id') in selected}
    if not isinstance(answers,dict):
        return diagnostic
    evidence = {item['id'] for item in task.get('evidence', [])}
    calculations = {item['id'] for item in task.get('calculations', [])}
    sources = {item['id'] for item in task.get('source_records', [])}
    categories = []
    for ident in selected:
        answer = answers.get(ident)
        if not isinstance(answer,dict) or not isinstance(answer.get('segments'),list):
            continue
        current = _analysis_citation_schema(task,[ident])['properties']['answers']['items']
        prose = current['properties']['segments']['items']
        prose = next((branch for branch in prose.get('anyOf',[prose]) if 'text' in branch.get('properties',{})),None)
        if prose is None:
            continue
        counts = {}
        for segment in answer['segments'][:200]:
            if not isinstance(segment,dict) or 'text' not in segment:
                continue
            for field in ('refs','counter_refs'):
                refs = segment.get(field)
                if not isinstance(refs,list):
                    continue
                allowed = prose['properties'][field]['items'].get('enum', [])
                for ref in refs[:200]:
                    if not isinstance(ref,str) or ref in allowed:
                        continue
                    category = ('calculation_out_of_scope' if ref in calculations else
                        'evidence_out_of_scope' if ref in evidence else
                        'source_record_not_evidence' if ref in sources else 'unregistered_reference')
                    key = (field,category); counts[key] = counts.get(key,0)+1
        categories.extend(dict(question_id=ident,field=field,category=category,count=count)
                          for (field,category),count in counts.items())
    if categories:
        diagnostic['reference_categories'] = categories[:8]
    return diagnostic


def material_supplement_output_schema(task, actions):
    """Project available material actions, never choose or approve a method.

    Network/filing and assumption-bearing choices keep their existing full
    protocol. None means not applicable, not permission to remove a choice.
    All finite input references still pass the kernel's context and lineage
    checks after this container has been decoded.
    """
    if (not isinstance(actions, dict) or actions.get('sources')
            or actions.get('filing_candidates')):
        return None
    questions = [q for q in task['questions'] if q.get('required', True) and q.get('status') != '已回答']
    if not questions:
        return None
    text = {'type': 'string'}
    question_ids = {'type': 'string', 'enum': [q['id'] for q in questions]}
    choices = []
    artifacts = actions.get('artifact_ids', [])
    if artifacts:
        if not isinstance(artifacts, list) or not all(isinstance(ident, str) and ident for ident in artifacts):
            return None
        choices.append(object_schema({'kind': {'type': 'string', 'enum': ['read']},
            'artifact_id': {'type': 'string', 'enum': list(dict.fromkeys(artifacts))}}))
    for method in actions.get('methods', []):
        definition = METHOD_REGISTRY.get(method)
        if definition is None or definition.get('assumption_units'):
            return None
        selected = [q['id'] for q in questions if method in q.get('method_needs', []) and not any(
            spec.get('method') == method and q['id'] in spec.get('question_ids', [])
            for spec in task.get('calculation_specs', []))]
        if not selected:
            return None
        inputs = {}
        for field, unit in definition['input_units'].items():
            units = definition.get('input_unit_variants', {}).get(field, [unit])
            from .contracts import calculation_input_applies
            refs = [item['id'] for item in task.get('evidence', [])
                if item.get('task_id') == task['id'] and item.get('research_version') == task['version']
                and item.get('source_check') == item.get('applicability_check') == '通过'
                and item.get('usage_scope') != 'document_content' and item.get('value') is not None
                and item.get('unit') in units
                and any(calculation_input_applies(task,item,ident,method) for ident in selected)]
            if not refs:
                break
            inputs[field] = {'type': 'string', 'enum': list(dict.fromkeys(refs))}
        if set(inputs) != set(definition['input_units']):
            # Do not narrow away a method still offered by the scheduler.
            return None
        if not all(isinstance(value, str) for value in definition['parameters'].values()):
            return None
        parameters = {key: {'type': 'string', 'enum': definition.get('parameter_variants', {}).get(key, [value])}
                      for key, value in definition['parameters'].items()}
        parameters.update({key: text for key in definition.get('context_parameters', [])})
        specification = object_schema({'id': text, 'method': {'type': 'string', 'enum': [method]},
            'inputs': object_schema(inputs), 'parameters': object_schema(parameters),
            'question_ids': array_schema({'type': 'string', 'enum': selected})})
        choices.append(object_schema({'kind': {'type': 'string', 'enum': ['calculate']},
                                     'calculation': specification}))
    if not choices:
        return None
    continuation = object_schema({'mode': {'type': 'string', 'enum': ['continue']},
        'question_ids': array_schema(question_ids), 'reason': text, 'expected_change': text,
        'actions': array_schema({'anyOf': choices})})
    stop = object_schema({'mode': {'type': 'string', 'enum': ['stop']}, 'reason': text})
    return object_schema({'version': {'type': 'integer', 'enum': [1]},
                          'decision': {'anyOf': [continuation, stop]}})


def decode_material_supplement_output(wire, schema):
    """Map explicit branches; never repair conflicting actions or new numbers."""
    if not _matches(wire, schema):
        raise StructuredOutputError('schema_mismatch', '结构化补证参数不符合本任务格式，未采用提案')
    decision = wire['decision']
    if not decision['reason'].strip() or len(decision['reason']) > 4000:
        raise StructuredOutputError('invalid_decision_reason', '结构化补证缺少有限理由，未采用提案')
    result = {'question_ids': [], 'reason': '', 'expected_change': '',
              'data_requests': [], 'calculations': [], 'artifact_ids': [], 'stop_reason': ''}
    if decision['mode'] == 'stop':
        result['stop_reason'] = decision['reason']
        return result
    if (not decision['actions'] or len(decision['actions']) > 50 or not decision['question_ids']
            or len(set(decision['question_ids'])) != len(decision['question_ids'])
            or not decision['expected_change'].strip() or len(decision['expected_change']) > 4000):
        raise StructuredOutputError('invalid_continuation', '结构化补证动作或预期改变不完整，未采用提案')
    for name in ('question_ids', 'reason', 'expected_change'):
        result[name] = copy.deepcopy(decision[name])
    for action in decision['actions']:
        if action['kind'] == 'read':
            result['artifact_ids'].append(action['artifact_id'])
        else:
            result['calculations'].append(copy.deepcopy(action['calculation']))
    return result


def _calculation_role_candidate(method, contract, name, item):
    """Select actual input roles, not the old price-versus-NAV shorthand."""
    tags = {
        'etf_daily_nav_premium_v1': {'price': ('price_kind', 'close'), 'nav': ('nav_kind', 'official')},
        'observed_price_return_v1': {'start_price': ('price_kind', 'close'), 'end_price': ('price_kind', 'close')},
        'observed_index_change_v1': {'start_level': ('level_kind', 'close'), 'end_level': ('level_kind', 'close')},
    }
    tag = tags.get(method, {}).get(name)
    context = item.get('calculation_context')
    return (tag is not None and isinstance(context, dict)
        and set(contract['input_fields'][name]) <= set(context) and context.get(tag[0]) == tag[1])


def deferred_calculation_output_schema(task):
    """Offer already planned methods only after their actual inputs exist.

    This is initial plan refinement, not evidence supplementation. An enum
    identifies an available observation; context validation still approves or
    rejects its use in a specific method. Assumptions are never inferred.
    """
    if task.get('intent') == '原件内容核对':
        return None
    methods, qualified = [], {}
    for question in task.get('questions', []):
        if not question.get('required', True) or question.get('status') == '已回答':
            continue
        for method in question.get('method_needs', []):
            definition = METHOD_REGISTRY.get(method)
            if (not definition or definition.get('assumption_units') or any(
                    spec.get('method') == method and question['id'] in spec.get('question_ids', [])
                    for spec in task.get('calculation_specs', []))):
                continue
            from .contracts import calculation_input_applies, calculation_context_contract
            role_contract = calculation_context_contract(task, method)
            candidates = [item for item in task.get('evidence', [])
                if item.get('task_id') == task['id'] and item.get('research_version') == task['version']
                and item.get('source_check') == item.get('applicability_check') == '通过'
                and item.get('usage_scope') != 'document_content' and item.get('value') is not None
                and calculation_input_applies(task, item, question['id'], method)
                and (any(_calculation_role_candidate(method, role_contract, name, item)
                         for name in role_contract['input_fields']) if role_contract else
                     set(definition.get('binding_context', [])) <= set(item.get('calculation_context') or {}))]
            from .contracts import calculation_input_units_available
            role_ready = (role_contract is None or all(any(
                _calculation_role_candidate(method, role_contract, name, item) for item in candidates)
                for name in role_contract['input_fields']))
            if role_ready and calculation_input_units_available(method, [item['unit'] for item in candidates]):
                methods.append(method)
                qualified.setdefault(method, set()).update(item['id'] for item in candidates)
    if not methods:
        return None
    base = material_supplement_output_schema(task, {'methods': list(dict.fromkeys(methods)),
        'artifact_ids': [], 'sources': [], 'filing_candidates': []})
    if base is None:
        return None
    actions = base['properties']['decision']['anyOf'][0]['properties']['actions']['items']['anyOf']
    choices = []
    for action in actions:
        specification = copy.deepcopy(action['properties']['calculation'])
        method = specification['properties']['method']['enum'][0]
        role_contract = calculation_context_contract(task, method)
        evidence = {item['id']: item for item in task.get('evidence', [])}
        for name, field in specification['properties']['inputs']['properties'].items():
            field['enum'] = [ident for ident in field['enum'] if ident in qualified[method]]
            if role_contract:
                field['enum'] = [ident for ident in field['enum']
                    if _calculation_role_candidate(method, role_contract, name, evidence[ident])]
        choices.append(specification)
    return object_schema({'version': {'type': 'integer', 'enum': [1]},
        'calculations': array_schema({'anyOf': choices}), 'reason': {'type': 'string'}})


def decode_calculation_binding_output(wire, schema):
    if (not _matches(wire, schema) or len(wire['calculations']) > 50
            or not wire['reason'].strip() or len(wire['reason']) > 4000):
        raise StructuredOutputError('invalid_calculation_binding', '计算输入绑定不符合当前方法与证据契约，未采用提案')
    return {key: copy.deepcopy(wire[key]) for key in ('calculations', 'reason')}


def _schema_identity(value):
    """JSON equality: object key order is irrelevant; booleans aren't numbers."""
    if isinstance(value,bool):return ('boolean',value)
    if isinstance(value,(int,float)):return ('number',value)
    if isinstance(value,str):return ('string',value)
    if isinstance(value,list):return ('array',tuple(_schema_identity(item) for item in value))
    if isinstance(value,dict):return ('object',tuple(sorted((key,_schema_identity(item)) for key,item in value.items())))
    return ('null',None)


def _matches(value, schema, depth=0, *, array_limit=200):
    """Validate our finite schema vocabulary without a new runtime dependency."""
    if depth > 24:
        return False
    if type(schema.get(LOCAL_ARRAY_POLICY)) is int and schema[LOCAL_ARRAY_POLICY] == 2:
        array_limit = MAX_ARGUMENT_CHARACTERS
    if 'anyOf' in schema:
        return any(_matches(value, choice, depth + 1, array_limit=array_limit) for choice in schema['anyOf'])
    kind = schema.get('type')
    if kind == 'object':
        properties = schema['properties']
        return (isinstance(value, dict) and set(value) == set(properties)
                and all(_matches(value[name], spec, depth + 1, array_limit=array_limit)
                        for name, spec in properties.items()))
    if kind == 'array':
        return (isinstance(value, list) and schema.get('minItems',0) <= len(value)
                <= min(array_limit,schema.get('maxItems',array_limit))
                and all(_matches(item, schema['items'], depth + 1, array_limit=array_limit) for item in value)
                and (not schema.get('uniqueItems',False)
                     or len({_schema_identity(item) for item in value})==len(value)))
    if kind == 'string':
        valid = isinstance(value, str)
    elif kind == 'integer':
        valid = type(value) is int
    elif kind == 'number':
        try:
            valid = type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            valid = False
    elif kind == 'boolean':
        valid = type(value) is bool
    elif kind == 'null':
        valid = value is None
    else:
        return False
    return (valid and ('enum' not in schema or value in schema['enum'])
            and ('pattern' not in schema or re.fullmatch(schema['pattern'], value) is not None)
            and ('minLength' not in schema or len(value) >= schema['minLength'])
            and ('maxLength' not in schema or len(value) <= schema['maxLength'])
            and ('minimum' not in schema or value >= schema['minimum'])
            and ('maximum' not in schema or value <= schema['maximum']))


def registered_structure_issues(schema, diagnostic):
    """Return known field/type paths only, never enum, scope or value approval."""
    if (not isinstance(diagnostic, dict) or set(diagnostic) != {
            'version', 'schema_issues', 'submitted_values_retained', 'automatic_repair'}
            or type(diagnostic['version']) is not int or diagnostic['version'] != 1
            or diagnostic['submitted_values_retained'] is not False or diagnostic['automatic_repair'] is not False):
        return None
    paths = set()
    def collect(shape, path='$'):
        paths.add(path)
        for name, child in shape.get('properties', {}).items():
            collect(child, path + '.' + name)
        if 'items' in shape:
            collect(shape['items'], path + '[*]')
        for branch in shape.get('anyOf', []):
            collect(branch, path)
    collect(schema)
    issues = diagnostic['schema_issues']
    structural = {'unknown_fields', 'missing_field', 'expected_object', 'expected_array', 'type_mismatch'}
    if (not isinstance(issues, list) or not 1 <= len(issues) <= 8 or any(
            not isinstance(issue, dict) or set(issue) != {'path', 'code'}
            or issue['code'] not in structural or issue['path'] not in paths for issue in issues)):
        return None
    return copy.deepcopy(issues)


def registered_temporal_choice_issues(schema, diagnostic):
    """Recognize only an explicit observation-index choice rejection.

    This authorizes asking the model once, never accepting or repairing the
    rejected selection. Bindings, money, units and arbitrary enums are excluded.
    """
    return _registered_locator_choice_issues(schema, diagnostic,
        '$.selections[*].temporal_reference.observation_anchor', 'integer')


def registered_analysis_reference_choice_issues(schema, diagnostic):
    """Only an answer's known wrong-question evidence choice, never approval.

    Re-selecting a registered citation does not establish that it supports a
    claim. Unknown identities, other enum fields and mixed errors still stop.
    """
    required = {'version', 'schema_issues', 'submitted_values_retained', 'automatic_repair', 'reference_categories'}
    if (not isinstance(diagnostic, dict) or set(diagnostic) != required
            or type(diagnostic['version']) is not int or diagnostic['version'] != 1
            or diagnostic['submitted_values_retained'] is not False or diagnostic['automatic_repair'] is not False):
        return None
    categories = diagnostic['reference_categories']
    selected = set(analysis_selected_questions(schema))
    if (not isinstance(categories, list) or not 1 <= len(categories) <= 8 or any(
            not isinstance(item, dict) or set(item) != {'question_id', 'field', 'category', 'count'}
            or not isinstance(item['question_id'], str) or item['question_id'] not in selected
            or item['field'] not in ('refs', 'counter_refs')
            or item['category'] != 'evidence_out_of_scope' or type(item['count']) is not int
            or not 1 <= item['count'] <= 200 for item in categories)):
        return None
    issues = diagnostic['schema_issues']
    if not isinstance(issues, list) or not 1 <= len(issues) <= 8:
        return None
    base = {key: diagnostic[key] for key in required - {'reference_categories'}}
    for issue in issues:
        if not isinstance(issue, dict) or set(issue) != {'path', 'code'} or not isinstance(issue['path'], str):
            return None
        allowed_paths = {path for item in categories for path in (
            '$.answers.' + item['question_id'] + '.segments[*].' + item['field'] + '[*]',
            '$.answers[*].segments[*].' + item['field'] + '[*]')}
        if (issue['path'] not in allowed_paths or _registered_locator_choice_issues(schema,
                dict(base, schema_issues=[issue]), issue['path'], 'string') is None):
            return None
    return copy.deepcopy(issues)


def registered_literal_choice_issues(schema, diagnostic):
    """Only the current original's finite literal address, never its value."""
    return _registered_locator_choice_issues(schema, diagnostic,
        '$.selections[*].number_reference.literal_ref', 'string')


def _registered_locator_choice_issues(schema, diagnostic, path, kind):
    if (not isinstance(diagnostic, dict) or set(diagnostic) != {
            'version', 'schema_issues', 'submitted_values_retained', 'automatic_repair'}
            or type(diagnostic['version']) is not int or diagnostic['version'] != 1
            or diagnostic['submitted_values_retained'] is not False or diagnostic['automatic_repair'] is not False):
        return None
    found = False
    def collect(shape, current='$'):
        nonlocal found
        if current == path and shape.get('type') == kind:
            choices = shape.get('enum')
            found = found or (isinstance(choices, list) and bool(choices)
                             and all(type(value) is {'integer':int,'string':str}[kind] for value in choices))
        for name, child in shape.get('properties', {}).items():
            collect(child, current + '.' + name)
        if 'items' in shape:collect(shape['items'], current + '[*]')
        for branch in shape.get('anyOf', []):collect(branch, current)
    collect(schema)
    issues = diagnostic['schema_issues']
    if (not found or not isinstance(issues, list) or not 1 <= len(issues) <= 8 or any(
            not isinstance(issue, dict) or issue != dict(path=path, code='enum_mismatch') for issue in issues)):
        return None
    return copy.deepcopy(issues)


def schema_diagnostic(value, schema):
    """Bounded registered paths and categories only, never submitted values.

    Union diagnostics use their closest branch only to locate rejection, not
    to coerce or accept output. Unknown property names and array contents are
    deliberately excluded; a financial validator still owns meaning.
    """
    def compatible_type(item, shape, depth=0):
        """Pick diagnostic branches by JSON type, never by value coercion."""
        if depth > 16 or not isinstance(shape, dict):
            return False
        if 'anyOf' in shape:
            return any(compatible_type(item, branch, depth+1) for branch in shape['anyOf'])
        kind = shape.get('type')
        if kind == 'integer': return type(item) is int
        if kind == 'number': return type(item) in (int, float)
        expected = {'object':dict, 'array':list, 'string':str, 'boolean':bool, 'null':type(None)}.get(kind)
        return expected is not None and isinstance(item, expected)

    array_limit = MAX_ARGUMENT_CHARACTERS if schema.get(LOCAL_ARRAY_POLICY) == 2 else 200

    def issues(item, shape, path='$', depth=0):
        def issue(code):
            return [{'path': path, 'code': code}]
        if depth > 16 or not isinstance(shape, dict):
            return issue('shape_limit')
        if 'anyOf' in shape:
            branches = shape['anyOf']
            typed = [branch for branch in branches if compatible_type(item, branch)]
            choices = [issues(item, branch, path, depth + 1) for branch in typed or branches]
            return min(choices, key=len) if choices else issue('no_branch')
        kind = shape.get('type')
        if kind == 'object':
            if not isinstance(item, dict):
                return issue('expected_object')
            properties = shape.get('properties', {})
            result = issue('unknown_fields') if set(item) - set(properties) else []
            for field, child in properties.items():
                registered_path = path + '.' + field
                if field not in item:
                    result.append({'path': registered_path, 'code': 'missing_field'})
                else:
                    result.extend(issues(item[field], child, registered_path, depth + 1))
            return result[:8]
        if kind == 'array':
            if not isinstance(item, list):
                return issue('expected_array')
            if len(item) > array_limit:
                return issue('array_limit')
            result = []
            if len(item)<shape.get('minItems',0):result.extend(issue('too_few_items'))
            if len(item)>shape.get('maxItems',array_limit):result.extend(issue('too_many_items'))
            if shape.get('uniqueItems',False) and len({_schema_identity(child) for child in item})!=len(item):
                result.extend(issue('duplicate_items'))
            for child in item:
                result.extend(issues(child, shape['items'], path + '[*]', depth + 1))
                if len(result) >= 8:
                    break
            return result[:8]
        if not _matches(item, {key: val for key, val in shape.items()
                              if key not in ('enum', 'pattern', 'minimum', 'maximum','minLength','maxLength')},
                        array_limit=array_limit):
            return issue('type_mismatch')
        if kind=='string' and len(item)<shape.get('minLength',0):return issue('too_short')
        if kind=='string' and 'maxLength' in shape and len(item)>shape['maxLength']:return issue('too_long')
        if 'enum' in shape and item not in shape['enum']:
            return issue('enum_mismatch')
        if 'pattern' in shape and re.fullmatch(shape['pattern'], item) is None:
            return issue('pattern_mismatch')
        if not _matches(item, shape, array_limit=array_limit):
            return issue('bound_mismatch')
        return []
    return {'version': 1, 'schema_issues': issues(value, schema)[:8],
            'submitted_values_retained': False, 'automatic_repair': False}


class _NonfiniteSubmissionConstant(ValueError):
    """An unsupported numeric literal is not a usable financial value."""


def _reject_constant(value):
    raise _NonfiniteSubmissionConstant()


def decode_analysis_output(wire, schema):
    if schema.get('properties', {}).get('version', {}).get('enum') != [VERSION] or not _matches(wire, schema):
        raise StructuredOutputError('schema_mismatch', '结构化分析参数不符合本任务格式，未采用正文',
                                    schema_diagnostic(wire, schema))
    result = {'answers': {}, 'theses': []}
    for item in wire['answers']:
        ident = item['question_id']
        if ident in result['answers']:
            raise StructuredOutputError('duplicate_answers', '结构化分析重复提交同一问题，未覆盖旧条目')
        answer = {name: copy.deepcopy(item[name]) for name in ('refs', 'calculation_refs')}
        if item['segments']:
            answer['numeric_segments'] = {'answer': copy.deepcopy(item['segments'])}
        for name in ('knowledge_refs', 'provenance_refs'):
            if item[name]:
                answer[name] = copy.deepcopy(item[name])
        result['answers'][ident] = answer
    for item in wire['theses']:
        segments = {name: copy.deepcopy(item[name]) for name in ('title', 'investment_meaning', 'limitation')}
        if not all(segments.values()):
            raise StructuredOutputError('empty_thesis_segments', '结构化论点正文缺失，未采用该批正文')
        thesis = {name: copy.deepcopy(item[name]) for name in
                  ('judgment', 'question_ids', 'support', 'counter', 'calculation_refs')}
        thesis['numeric_segments'] = segments
        if item['knowledge_refs']:
            thesis['knowledge_refs'] = copy.deepcopy(item['knowledge_refs'])
        tracking = item['tracking']
        fields = tracking_contract()['required_fields']
        if tracking['applicable']:
            rule = {name: tracking[name] for name in fields}
            if tracking['benchmark']:
                rule['benchmark'] = tracking['benchmark']
            try:
                validate_tracking(rule)
            except ContractError:
                raise StructuredOutputError('invalid_tracking', '结构化追踪条件无效，未采用该批正文') from None
            thesis['tracking'] = rule
        elif any(tracking[name] for name in [*fields, 'benchmark']):
            raise StructuredOutputError('conflicting_tracking', '结构化分析声明不追踪却提交条件，未推定其用途')
        result['theses'].append(thesis)
    if 'judgment_reviews' in wire:
        from .judgment_review import validate_shape
        reviews = copy.deepcopy(wire['judgment_reviews'])
        for review in reviews:
            for dimension in review['dimensions'].values():
                segments = dimension.pop('segments')
                if not segments:
                    raise StructuredOutputError('empty_review_segments', '判断复核缺少分项依据，未采用该批复核')
                dimension['numeric_segments'] = {'answer': segments}
        try:
            validate_shape(reviews)
        except ContractError:
            raise StructuredOutputError('invalid_judgment_review', '判断复核格式不属于本次分析，未采用提案') from None
        result['judgment_reviews'] = reviews
    return result


def _submission_decoder(protocol, context):
    if protocol == 'strict_analysis_v1':
        function_name, decoder = FUNCTION_NAME, decode_analysis_output
    elif protocol == ANALYSIS_CITATION_PROTOCOL:
        function_name = ANALYSIS_CITATION_FUNCTION
        decoder = lambda wire, selected: decode_analysis_citations(wire, selected, context)
    elif protocol == SUPPLEMENT_PROTOCOL:
        function_name, decoder = SUPPLEMENT_FUNCTION_NAME, decode_material_supplement_output
    elif protocol == ACQUISITION_PROTOCOL:
        function_name = ACQUISITION_FUNCTION_NAME
        decoder = lambda wire, schema: decode_acquisition_output(wire, schema, context)
    elif protocol == PLAN_PROTOCOL:
        function_name, decoder = PLAN_FUNCTION_NAME, decode_planning_output
    elif protocol == PLAN_REQUEST_PROTOCOL:
        function_name, decoder = PLAN_REQUEST_FUNCTION_NAME, decode_planning_output
    elif protocol == MATERIAL_EXTRACTION_PROTOCOL:
        function_name, decoder = MATERIAL_EXTRACTION_FUNCTION_NAME, decode_material_extraction_output
    elif protocol == FINANCIAL_EXTRACTION_PROTOCOL:
        function_name = FINANCIAL_EXTRACTION_FUNCTION_NAME
        decoder = lambda wire,selected:decode_financial_extraction_output(wire,selected,context)
    elif protocol == FINANCIAL_REFERENCE_PROTOCOL:
        function_name = FINANCIAL_REFERENCE_FUNCTION_NAME
        decoder = lambda wire,selected:decode_financial_references(wire,selected,context)
    elif protocol == ORIGINAL_BATCH_PROTOCOL:
        function_name = ORIGINAL_BATCH_FUNCTION_NAME
        decoder = lambda wire,selected:decode_original_batch(wire,selected,context)
    elif protocol == ORIGINAL_SPAN_PROTOCOL:
        function_name = ORIGINAL_SPAN_FUNCTION_NAME
        def decoder(wire, selected):
            if isinstance(context, tuple) and len(context) == 2:
                from .flat_api_selection import restore as restore_flat_api
                wire = restore_flat_api(context[0], selected, wire)
                from .api_context_wire import restore as restore_api_contexts
                wire = restore_api_contexts(context[0], selected, wire)
                from .original_scope_wire import restore
                wire = restore(context[0], selected, wire)
            return decode_original_spans(wire, selected, context)
    elif protocol == FEE_ORIGINAL_PROTOCOL:
        function_name=FEE_ORIGINAL_FUNCTION_NAME
        decoder=lambda wire,selected:decode_fee_original_output(wire,selected,context)
    elif protocol == API_EXTRACTION_PROTOCOL:
        function_name = API_EXTRACTION_FUNCTION_NAME
        decoder=lambda wire,selected:decode_api_extraction_output(wire,selected,context)
    elif protocol == CALCULATION_BINDING_PROTOCOL:
        function_name, decoder = CALCULATION_BINDING_FUNCTION_NAME, decode_calculation_binding_output
    elif protocol == READING_PROTOCOL:
        function_name, decoder = READING_FUNCTION_NAME, decode_reading_output
    elif protocol == READING_REFERENCE_PROTOCOL:
        if not isinstance(context, tuple) or len(context) != 2:
            raise StructuredOutputError('missing_reading_reference_context', '读取短引用缺少当前原件目录')
        task, description = context
        function_name = READING_REFERENCE_FUNCTION_NAME
        decoder = lambda wire,selected:decode_reading_references(wire,selected,task,description)
    elif protocol in ('strict_argument_support_v2','strict_argument_reference_v1'):
        from .argument_support import (FUNCTION_NAME as REVIEW_FUNCTION_NAME, review_request, decode_review,
            REFERENCE_FUNCTION_NAME, decode_reference_review)
        if not isinstance(context, tuple) or len(context) != 2:
            raise StructuredOutputError('missing_argument_context', '论证复核缺少实际任务和提案')
        task, proposal = context
        request = review_request(task, proposal)
        if protocol == 'strict_argument_reference_v1':
            function_name = REFERENCE_FUNCTION_NAME
            decoder = lambda wire, selected: decode_reference_review(wire, selected, request)
        else:
            function_name = REVIEW_FUNCTION_NAME
            decoder = lambda wire, selected: decode_review(wire, selected, request)
    else:
        raise StructuredOutputError('unknown_protocol', '结构化提交协议未登记，不执行工具')
    return function_name, decoder


def structured_completion(message, finish, schema, *, protocol='strict_analysis_v1',context=None):
    """Consume one fixed tool submission; never fall back to ordinary prose."""
    function_name, decoder = _submission_decoder(protocol, context)
    calls = message.get('tool_calls')
    if (finish != 'tool_calls' or message.get('role') != 'assistant'
            or message.get('content') not in (None, '') or message.get('function_call') is not None
            or not isinstance(calls, list) or len(calls) != 1):
        raise StructuredOutputError('invalid_submission_envelope', '结构化提交没有单一明确结果，不自动改用普通正文')
    call = calls[0]
    function = call.get('function') if isinstance(call, dict) else None
    if (not isinstance(call, dict) or not {'id', 'type', 'function'} <= set(call)
            or set(call) - {'id', 'type', 'function', 'index'}
            or ('index' in call and (type(call['index']) is not int or call['index'] != 0))
            or call['type'] != 'function' or not isinstance(call['id'], str) or not call['id']
            or not isinstance(function, dict) or set(function) != {'name', 'arguments'}
            or function['name'] != function_name or not isinstance(function['arguments'], str)
            or len(function['arguments']) > MAX_ARGUMENT_CHARACTERS):
        raise StructuredOutputError('invalid_call_shape', '结构化提交名称或格式无效，不执行工具')
    return _decode_submission_arguments(function['arguments'], schema, decoder)


def structured_json_completion(text, schema, *, protocol='strict_analysis_v1', context=None):
    """Explicit Responses JSON-schema channel, not a tool-call repair/fallback.

    The caller must confirm the Responses completion envelope first. Both
    declared transports share the same canonical container and phase decoder.
    """
    _, decoder = _submission_decoder(protocol, context)
    if not isinstance(text, str) or not text or len(text) > MAX_ARGUMENT_CHARACTERS:
        raise StructuredOutputError('invalid_submission_envelope', '结构化正文长度或格式无效，未采用结果')
    return _decode_submission_arguments(text, schema, decoder)


def _decode_submission_arguments(arguments, schema, decoder):
    try:
        wire = json.loads(arguments, object_pairs_hook=unique_json_object, parse_constant=_reject_constant)
    except (DuplicateJSONField, _NonfiniteSubmissionConstant) as error:
        category = ('duplicate_key' if isinstance(error, DuplicateJSONField)
                    else 'nonfinite_constant')
        raise StructuredOutputError('invalid_argument_json',
            '结构化提交含重复键或非有限数值，未修复或采用正文',
            {'version': 1, 'json_error': category, 'submitted_values_retained': False,
             'automatic_repair': False}) from None
    except json.JSONDecodeError as error:
        categories = {'Extra data': 'extra_data', 'Unterminated string starting at': 'unterminated_string',
            'Invalid \\escape': 'invalid_escape', 'Invalid control character at': 'invalid_control_character',
            'Invalid \\uXXXX escape': 'invalid_unicode_escape',
            'Expecting property name enclosed in double quotes': 'expected_property_name',
            "Expecting ',' delimiter": 'expected_comma', "Expecting ':' delimiter": 'expected_colon',
            'Expecting value': 'expected_value'}
        raise StructuredOutputError('invalid_argument_json', '结构化提交参数不是明确JSON，未修复或采用正文',
            {'version': 1, 'json_error': categories.get(error.msg, 'syntax_error'),
             'line': error.lineno, 'column': error.colno, 'submitted_values_retained': False,
             'automatic_repair': False}) from None
    except (ValueError, TypeError):
        raise StructuredOutputError('invalid_argument_json', '结构化提交参数不是明确JSON，未修复或采用正文') from None
    return json.dumps(decoder(decode_provider_containers(wire,schema), schema), ensure_ascii=False, allow_nan=False)
