"""Read-scoped extraction feedback, never evidence or source-absence proof."""
import copy
import re

from .contracts import ContractError, canonical, context, fingerprint

POLICY = 'original_requirement_feedback_version'
FIELD = '_original_requirement_feedback'
STATUSES = {'selected', 'partially_selected', 'not_found', 'not_submitted', 'format_rejected'}


def enabled(task):
    version = task.get(POLICY)
    if version is None: return False
    if type(version) is not int or version != 1: raise ContractError('提取反馈版本未登记')
    return True


def _slots(task, field):
    from .data_requirements import requirement_slots, numeric_slots
    if field not in ('requirements', 'numeric_requirements'): raise ContractError('提取反馈需求容器未登记')
    # The first feedback still addresses the complete registered plan while a
    # later numeric-only call has a narrower active presentation scope.
    state = task.get('reasoning_state', {})
    if 'required_numeric_completion' in state:
        task = dict(task, reasoning_state={key: value for key, value in state.items()
                                         if key != 'required_numeric_completion'})
    return {slot['key']: slot for slot in (requirement_slots(task) if field == 'requirements' else numeric_slots(task))}


def build(task, views, schema, wire, specs, accepted_paths, rejected_items, rejected_requirements):
    if not enabled(task): return None
    field = next((key for key in ('requirements', 'numeric_requirements') if key in schema['properties']), None)
    if field is None: return None
    slots = _slots(task, field)
    wrappers = {item['location']: item['category'] for item in rejected_requirements}
    records = []
    for key in schema['properties'][field]['properties']:
        slot, path = slots[key], '$.' + field + '.' + key
        response = wire[field].get(key)
        selected = sum(location.startswith(path + '.items[') for location in accepted_paths)
        rejected = sum(item['location'].startswith(path + '.items[') for item in rejected_items)
        reason = None
        if wrappers.get(path) == 'requirement_not_submitted': status = 'not_submitted'
        elif path in wrappers:
            status, rejected = 'format_rejected', rejected + 1
        elif response['status'] == 'not_found':
            status, reason = 'not_found', response['reason']
        elif selected:
            status = 'partially_selected' if rejected else 'selected'
        else: status = 'format_rejected'
        records.append(dict(key=key, question_id=slot['question_id'], need_ref=slot['need_ref'],
            status=status, selected_items=selected, rejected_items=rejected, reason=reason))
    value = dict(version=1, context=context(task), schema_hash=fingerprint(schema), views_hash=fingerprint(views),
        evidence_specs_hash=fingerprint(specs), field=field, requirements=records,
        evidence_approved=False, coverage_approved=False, source_absence_proved=False)
    return validate(value, task, views, specs, schema_hash=fingerprint(schema), schema=schema)


def validate(value, task, views, specs, *, schema_hash, schema=None):
    fields = {'version', 'context', 'schema_hash', 'views_hash', 'evidence_specs_hash', 'field',
              'requirements', 'evidence_approved', 'coverage_approved', 'source_absence_proved'}
    if (not enabled(task) or not isinstance(value, dict) or set(value) != fields
            or type(value.get('version')) is not int or value['version'] != 1
            or not isinstance(value['context'], dict) or canonical(value['context']) != canonical(context(task))
            or value['schema_hash'] != schema_hash or not re.fullmatch(r'[0-9a-f]{64}', str(schema_hash))
            or value['views_hash'] != fingerprint(views) or value['evidence_specs_hash'] != fingerprint(specs)
            or any(value[key] is not False for key in ('evidence_approved', 'coverage_approved', 'source_absence_proved'))):
        raise ContractError('提取反馈未绑定本任务、同次读取和提案，不能据此省略或追加调用')
    slots = _slots(task, value['field'])
    records = value['requirements']
    expected = set(schema['properties'][value['field']]['properties']) if schema is not None else None
    if not isinstance(records, list) or not records or len(records) > len(slots):
        raise ContractError('提取反馈需求数量无效')
    seen = set()
    for record in records:
        if (not isinstance(record, dict) or set(record) != {'key', 'question_id', 'need_ref', 'status',
                'selected_items', 'rejected_items', 'reason'} or not isinstance(record['key'], str)
                or record['key'] not in slots or record['key'] in seen
                or any(record[key] != slots[record['key']][key] for key in ('question_id', 'need_ref'))
                or not isinstance(record['status'], str) or record['status'] not in STATUSES
                or any(type(record[key]) is not int or not 0 <= record[key] <= 1000
                       for key in ('selected_items', 'rejected_items'))):
            raise ContractError('提取反馈存在未知、重复或跨问题需求')
        seen.add(record['key'])
        status, selected, rejected, reason = (record[key] for key in
            ('status', 'selected_items', 'rejected_items', 'reason'))
        if (status == 'not_found' and (selected or rejected or not isinstance(reason, str) or not reason.strip())
                or status != 'not_found' and reason is not None
                or status == 'not_submitted' and (selected or rejected)
                or status == 'selected' and (not selected or rejected)
                or status == 'partially_selected' and (not selected or not rejected)
                or status == 'format_rejected' and (selected or not rejected)):
            raise ContractError('提取反馈状态与实际选择或拒绝数量不一致')
    if expected is not None and seen != expected: raise ContractError('提取反馈未逐项保留本次需求')
    return copy.deepcopy(value)


def completion_scope(task, specs, views):
    """Scope a numeric-only omission pass; keep every original obligation.

    Explicit negative/rejected selections take their own evidence/recovery
    path, not an immediate numeric-omission retry on identical read views.
    A changed read scope does not inherit those negative declarations.
    """
    from .data_requirements import unproposed_numeric
    missing = unproposed_numeric(task, specs)
    result = dict(all_missing=copy.deepcopy(missing), eligible=copy.deepcopy(missing), deferred=[], feedback_hash=None)
    if not enabled(task): return result
    state = task.get('reasoning_state', {}).get('extraction', {})
    receipt = state.get('receipt', {})
    value = receipt.get('original_requirement_feedback')
    if not isinstance(value, dict) or value.get('views_hash') != fingerprint(views): return result
    if (receipt.get('phase') != 'extract' or receipt.get('output_protocol') != 'strict_original_spans_v1'
            or any(type(receipt.get(key)) is not type(task[field]) or receipt.get(key) != task[field]
                   for key, field in (('task_id', 'id'), ('research_version', 'version'),
                                      ('batch', 'batch'), ('input_hash', 'input_hash')))):
        return result
    validate(value, task, views, specs, schema_hash=receipt.get('output_schema_hash'))
    slots = _slots(task, value['field'])
    blocked = {(record['question_id'], slots[record['key']]['text']): record['status'] for record in value['requirements']
               if record['status'] in ('not_found', 'format_rejected')}
    result['eligible'] = []
    for need in missing:
        status = blocked.get((need['question_id'], need['text']))
        if status:
            result['deferred'].append(dict(question_id=need['question_id'], text=need['text'], feedback_status=status))
        else: result['eligible'].append(copy.deepcopy(need))
    result['feedback_hash'] = fingerprint(value)
    return result
