"""Lossless App-model encoding of two fixed original-selector constants.

Canonical App/Skill evidence selection stays version 12. An eligible model
transport uses version 13, omitting selections=[] and the invariant current
descriptive scope. It cannot choose a permission or repair an old response.
"""
import copy

from .contracts import ContractError

POLICY = 'original_scope_wire_version'
CANONICAL_VERSION = 12
WIRE_VERSION = 13
SCOPE = 'current_original_descriptive_only'


def enabled(task):
    value = task.get(POLICY)
    if value is None: return False
    if type(value) is not int or value != 1:
        raise ContractError('原件固定用途传输版本未登记')
    return (task.get('research_mode') == 'current' and task.get('intent') != '原件内容核对'
            and task.get('typed_numeric_selection_version') == 3)


def _found_branches(schema):
    for requirement in schema['properties']['requirements']['properties'].values():
        for option in requirement['anyOf']:
            if option['properties']['status']['enum'] == ['found']:
                yield from option['properties']['items']['items']['anyOf']


def project(task, canonical_schema):
    """Omit constants only when every live branch declares that exact value."""
    schema = copy.deepcopy(canonical_schema)
    fields = schema.get('properties', {})
    if (not enabled(task) or set(fields) != {'version', 'binding_ref', 'selections', 'requirements'}
            or fields['version'].get('enum') != [CANONICAL_VERSION]
            or fields['selections'].get('type') != 'array' or fields['selections'].get('maxItems') != 0
            or set(schema.get('required', [])) != set(fields)):
        return schema
    branches = list(_found_branches(schema))
    if not branches: return schema
    for branch in branches:
        review = branch['properties'].get('review', {})
        if (review.get('type') != 'object' or set(review.get('properties', {})) != {'scope'}
                or review.get('required') != ['scope'] or review.get('additionalProperties') is not False
                or review['properties']['scope'].get('type') != 'string'
                or review['properties']['scope'].get('enum') != [SCOPE]):
            return schema  # A real choice stays explicit in the canonical wire.
    fields['version']['enum'] = [WIRE_VERSION]
    fields.pop('selections'); schema['required'].remove('selections')
    for branch in branches:
        branch['properties'].pop('review'); branch['required'].remove('review')
    return schema


def restore(task, canonical_schema, wire):
    """Restore constants after checking the fixed transport envelope.

    The canonical decoder remains authoritative for selector validation and
    per-need isolation. No key/value is deleted to make a proposal pass.
    """
    from .output_schema import StructuredOutputError, _matches, schema_diagnostic, decode_provider_containers
    transport_schema = project(task, canonical_schema)
    if transport_schema.get('properties', {}).get('version', {}).get('enum') != [WIRE_VERSION]:
        return copy.deepcopy(wire)
    wire = decode_provider_containers(wire, transport_schema)
    fields = transport_schema['properties']
    if (not isinstance(wire, dict) or set(wire) != set(fields)
            or not _matches(wire.get('version'), fields['version'])
            or not _matches(wire.get('binding_ref'), fields['binding_ref'])
            or not isinstance(wire.get('requirements'), dict)):
        raise StructuredOutputError('invalid_original_scope_wire',
            '原件传输须按版本13提交实际选择，不能附加review、权限字段或旧容器；未修复或采用',
            schema_diagnostic(wire, transport_schema))
    restored = copy.deepcopy(wire)
    restored['version'] = CANONICAL_VERSION; restored['selections'] = []
    selected_count = 0
    for key in fields['requirements']['properties']:
        requirement = restored['requirements'].get(key)
        if not isinstance(requirement, dict) or requirement.get('status') != 'found': continue
        items = requirement.get('items')
        if not isinstance(items, list): continue  # Canonical wrapper isolation.
        selected_count += len(items)
        if selected_count > 1000:
            raise StructuredOutputError('invalid_original_scope_wire_size', '原件选择数量超过上限，未采用结果')
        for item in items:
            if not isinstance(item, dict): continue  # Canonical item validation.
            if 'review' in item:
                raise StructuredOutputError('invalid_original_scope_wire',
                    '版本13不提交review，不删除、覆盖或修复额外权限字段；未采用',
                    schema_diagnostic(wire, transport_schema))
            item['review'] = dict(scope=SCOPE)
    return restored
