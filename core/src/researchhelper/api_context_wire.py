"""Model-only omission of an unselected API context array, not missing facts.

Canonical Skill/App selections remain version12. Version14 explicitly permits
omitting additional_contexts only where the canonical contract accepts[].
No identity, number, unit, date, permission or chosen context is supplied here.
"""
import copy

from .contracts import ContractError

POLICY = 'api_context_wire_version'
WIRE_VERSION = 14
CANONICAL_VERSION = 12


def enabled(task):
    policy = task.get(POLICY)
    if policy is None:
        return False
    if type(policy) is not int or policy != 1:
        raise ContractError('API附加口径传输版本未登记')
    return task.get('research_mode') == 'current' and task.get('intent') != '原件内容核对'


def _eligible(schema):
    from .output_schema import _matches
    properties = schema.get('properties', {})
    return (schema.get('type') == 'object' and schema.get('additionalProperties') is False
        and properties.get('kind', {}).get('enum') == ['api']
        and bool(properties.get('source_ref', {}).get('enum')) and 'selection' in properties
        and 'additional_contexts' in schema.get('required', [])
        and properties.get('additional_contexts', {}).get('type') == 'array'
        and _matches([], properties['additional_contexts']))


def _api_branches(schema):
    if not isinstance(schema, dict):
        return
    if _eligible(schema):
        yield schema
    for key, value in schema.items():
        if key in ('properties', '$defs') and isinstance(value, dict):
            for child in value.values():
                yield from _api_branches(child)
        elif key == 'items' and isinstance(value, dict):
            yield from _api_branches(value)
        elif key == 'anyOf' and isinstance(value, list):
            for child in value:
                yield from _api_branches(child)


def project(task, schema):
    """Offer two exact object shapes; every property within a shape is required."""
    result = copy.deepcopy(schema)
    fields = result.get('properties', {})
    if (not enabled(task) or not {'version', 'binding_ref'} <= set(fields)
            or set(result.get('required', [])) != set(fields)
            or fields.get('version', {}).get('enum') != [CANONICAL_VERSION]
            or not list(_api_branches(result))):
        return result

    def transform(node):
        if not isinstance(node, dict):
            return copy.deepcopy(node)
        output = {}
        for key, value in node.items():
            if key in ('properties', '$defs') and isinstance(value, dict):
                output[key] = {name:transform(child) for name,child in value.items()}
            elif key == 'items' and isinstance(value, dict):
                output[key] = transform(value)
            elif key == 'anyOf' and isinstance(value, list):
                choices = []
                for branch in value:
                    full = transform(branch)
                    if _eligible(branch):
                        compact = copy.deepcopy(full)
                        compact['properties'].pop('additional_contexts')
                        compact['required'].remove('additional_contexts')
                        compact['description'] = '未选择额外口径；省略additional_contexts等价于[]，不补任何金融字段。'
                        choices.append(compact)
                    choices.append(full)
                output[key] = choices
            else:
                output[key] = copy.deepcopy(value)
        return output

    result = transform(result)
    result['properties']['version']['enum'] = [WIRE_VERSION]
    return result


def restore(task, schema, wire):
    """Compile a declared omission, never delete unknown fields or fix JSON."""
    from .output_schema import StructuredOutputError, _matches, schema_diagnostic, decode_provider_containers
    projected = project(task, schema)
    if projected.get('properties', {}).get('version', {}).get('enum') != [WIRE_VERSION]:
        return copy.deepcopy(wire)
    # Existing full canonical submissions remain valid and get no defaults.
    if isinstance(wire, dict) and type(wire.get('version')) is int and wire['version'] == CANONICAL_VERSION:
        return copy.deepcopy(wire)
    value = decode_provider_containers(wire, projected)
    properties = projected['properties']
    if (not isinstance(value, dict) or set(value) != set(properties)
            or not _matches(value.get('version'), properties['version'])
            or not _matches(value.get('binding_ref'), properties['binding_ref'])):
        raise StructuredOutputError('invalid_api_context_wire', 'API提取传输版本、任务绑定或容器不符，未采用',
            schema_diagnostic(value, projected))
    branches = list(_api_branches(schema))

    def compile_value(item):
        if isinstance(item, list):
            return [compile_value(child) for child in item]
        if not isinstance(item, dict):
            return copy.deepcopy(item)
        result = {key:compile_value(child) for key,child in item.items()}
        if result.get('kind') == 'api' and 'additional_contexts' not in result:
            for branch in branches:
                allowed = set(branch['properties']) - {'additional_contexts'}
                if (set(result) == allowed
                        and _matches(result.get('source_ref'), branch['properties']['source_ref'])):
                    result['additional_contexts'] = []
                    break
        return result

    result = compile_value(value)
    result['version'] = CANONICAL_VERSION
    return result
