"""Lossless model-wire projection for already registered API cell choices.

The model still selects cells, ownership and extra context explicitly. Only
the fixed version3 container and its single-item evidence array are compiled.
Original-source items and canonical Skill submissions retain their contracts.
"""
import copy

from .contracts import ContractError

POLICY = 'flat_api_selection_version'
WIRE_VERSION = 15


def enabled(task):
    version = task.get(POLICY)
    if version is None:
        return False
    if type(version) is not int or version != 1:
        raise ContractError('API单元平铺传输版本未登记')
    return task.get('research_mode') == 'current' and task.get('intent') != '原件内容核对'


def _cell_branches(branch):
    fields = branch.get('properties', {})
    selection = fields.get('selection', {})
    selected = selection.get('properties', {})
    if (branch.get('type') != 'object' or branch.get('additionalProperties') is not False
            or set(fields) not in ({'source_ref', 'kind', 'selection'},
                                   {'source_ref', 'kind', 'selection', 'additional_contexts'})
            or set(branch.get('required', [])) != set(fields)
            or fields['kind'].get('enum') != ['api']
            or not fields['source_ref'].get('enum')
            or selection.get('additionalProperties') is not False
            or set(selected) != {'version', 'evidence_specs'}
            or set(selection.get('required', [])) != set(selected)
            or selected['version'].get('enum') != [3]):
        return []
    choices = selected['evidence_specs'].get('items', {}).get('anyOf', [])
    if not choices or any(choice.get('type') != 'object'
            or choice.get('additionalProperties') is not False
            or set(choice.get('properties', {})) != {'api_cell_selections', 'review'}
            or set(choice.get('required', [])) != {'api_cell_selections', 'review'}
            for choice in choices):
        return []
    return choices


def _flatten(branch, cells):
    fields = {key: copy.deepcopy(value) for key, value in branch['properties'].items()
              if key != 'selection'}
    fields.update(copy.deepcopy(cells['properties']))
    result = copy.deepcopy(branch)
    result.update(properties=fields, required=list(fields))
    return result


def _base(task, schema):
    from .original_scope_wire import project as scope
    from .api_context_wire import project as contexts
    return contexts(task, scope(task, schema))


def project(task, canonical_schema):
    """Generate the private transport schema; never remove a real choice."""
    schema = _base(task, canonical_schema)
    if not enabled(task) or schema.get('properties', {}).get('version', {}).get('enum') not in ([12], [14]):
        return schema
    changed = False

    def visit(node):
        nonlocal changed
        if not isinstance(node, dict):
            return copy.deepcopy(node)
        result = copy.deepcopy(node)
        for key, value in node.items():
            if key in ('properties', '$defs'):
                result[key] = {name: visit(child) for name, child in value.items()}
            elif key == 'items':
                result[key] = visit(value)
            elif key == 'anyOf':
                alternatives = []
                for branch in value:
                    choices = _cell_branches(branch)
                    if choices:
                        changed = True
                        alternatives.extend(_flatten(branch, cells) for cells in choices)
                    else:
                        alternatives.append(visit(branch))
                result[key] = alternatives
        return result

    result = visit(schema)
    if changed:
        result['properties']['version']['enum'] = [WIRE_VERSION]
    return result


def restore(task, canonical_schema, wire):
    """Compile only an exact advertised flat item, then use canonical decoding.

    Do not delete extra fields, repair values or coalesce incompatible cells.
    Non-API wrappers pass untouched to existing per-requirement validation.
    """
    from .output_schema import StructuredOutputError, _matches, decode_provider_containers, schema_diagnostic
    transport = project(task, canonical_schema)
    if transport.get('properties', {}).get('version', {}).get('enum') != [WIRE_VERSION]:
        return copy.deepcopy(wire)
    if isinstance(wire, dict) and type(wire.get('version')) is int and wire['version'] == 12:
        return copy.deepcopy(wire)  # Canonical host/Skill submissions stay unchanged.
    wire = decode_provider_containers(wire, transport)
    fields = transport['properties']
    if (not isinstance(wire, dict) or set(wire) != set(fields)
            or not _matches(wire.get('version'), fields['version'])
            or not _matches(wire.get('binding_ref'), fields['binding_ref'])):
        raise StructuredOutputError('invalid_flat_api_selection',
            'API选择须使用本次版本15及绑定，不补写或修复额外字段', schema_diagnostic(wire, transport))
    base = _base(task, canonical_schema)

    def visit(value, shape):
        if 'anyOf' in shape:
            options = [branch for branch in shape['anyOf'] if _cell_branches(branch)]
            if isinstance(value, dict) and value.get('kind') == 'api' and options:
                candidates = []
                for branch in options:
                    for cells in _cell_branches(branch):
                        flat = _flatten(branch, cells)
                        if not _matches(value, flat):
                            continue
                        result = {key: copy.deepcopy(value[key]) for key in branch['properties'] if key != 'selection'}
                        result['selection'] = dict(version=3, evidence_specs=[{
                            key: copy.deepcopy(value[key]) for key in cells['properties']}])
                        if result not in candidates:
                            candidates.append(result)
                if len(candidates) != 1:
                    if any(not _cell_branches(branch) and _matches(value, branch)
                           for branch in shape['anyOf']):
                        return copy.deepcopy(value)  # An unprojected API retains its own grammar.
                    raise StructuredOutputError('invalid_flat_api_selection',
                        'API单元选择与本来源字段、需求或口径不一致，未修复或采用', schema_diagnostic(value, {'anyOf':[
                            _flatten(branch, cells) for branch in options for cells in _cell_branches(branch)]}))
                return candidates[0]
            # Preserve original-item isolation. Do not validate or repair it here.
            for branch in shape['anyOf']:
                if isinstance(value, dict) and value.get('status') in branch.get('properties', {}).get('status', {}).get('enum', []):
                    return visit(value, branch)
            return copy.deepcopy(value)
        if shape.get('type') == 'object' and isinstance(value, dict):
            return {key: visit(item, shape['properties'][key]) if key in shape.get('properties', {})
                    else copy.deepcopy(item) for key, item in value.items()}
        if shape.get('type') == 'array' and isinstance(value, list):
            return [visit(item, shape['items']) for item in value]
        return copy.deepcopy(value)

    result = visit(wire, base)
    result['version'] = base['properties']['version']['enum'][0]
    return result
