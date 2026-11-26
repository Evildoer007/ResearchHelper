"""Versioned read-only evaluation using the engine's actual input contract.

Keep v1 and its historical receipts unchanged. Expected financial outcomes stay
outside the Agent; neither version substitutes contracts for semantic review.
"""
import copy
import hashlib

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.planning import input_fingerprint
from researchhelper.reports import compose
from tests import research_quality_evaluation as legacy

VERSION = 2
REPORT_BINDINGS = ('research_mode', 'source_records', 'data_need_specs', 'reasoning_state',
                   'optional_findings', 'tracking_reason', 'run_mode', 'plan_version',
                   'changes', 'update_from')


def input_scope(task):
    # Problem identity is not task ID, run number or derived plan. Explicit
    # current/historical scope and reference-resolution context do change it.
    scope = legacy.input_scope(task)
    for key in ('research_mode', 'conversation_context'):
        if key in task:
            scope[key] = copy.deepcopy(task[key])
    return scope


def evaluate(task, specification, *, saved_delivery=None):
    original = copy.deepcopy(specification)
    if not isinstance(original, dict) or original.get('input_digest') != fingerprint(input_scope(task)):
        raise ContractError('评估案例不对应本任务完整问题范围与对话上下文')
    converted = copy.deepcopy(original)
    converted['input_digest'] = fingerprint(legacy.input_scope(task))
    result = legacy.evaluate(task, converted, saved_delivery=saved_delivery)
    violations = set(result['critical_violations'])
    effective_input_digest = None
    try:
        effective_input_digest = input_fingerprint(task)
        if task.get('input_hash') != effective_input_digest:
            raise ContractError('登记输入哈希不一致')
    except (ContractError, KeyError, TypeError, ValueError):
        violations.add('invalid_locked_input')
    if 'research_mode' in task and task['research_mode'] not in ('current', 'historical'):
        violations.add('invalid_research_mode')
    html_integrity = 'not_delivered'
    if task.get('status') in ('已完成', '部分完成') and isinstance(saved_delivery, dict):
        stored = saved_delivery.get('content')
        try:
            current = compose(task)
            if not isinstance(stored, dict) or any(
                    fingerprint(stored.get(key)) != fingerprint(current.get(key)) for key in REPORT_BINDINGS):
                violations.add('saved_research_context_mismatch')
        except (ContractError, KeyError, TypeError, ValueError):
            violations.add('content_contract_failed')
        delivery = task.get('delivery') if isinstance(task.get('delivery'), dict) else {}
        expected = delivery.get('html_sha256')
        html = saved_delivery.get('html')
        html_integrity = 'legacy_unverified' if expected is None else 'failed'
        if expected is not None:
            if isinstance(html, str) and expected == hashlib.sha256(html.encode()).hexdigest():
                html_integrity = 'verified'
            else:
                violations.add('saved_html_integrity_failed')
    result.update(evaluation_version=VERSION, input_digest=original['input_digest'],
        case_specification_hash=fingerprint(original), critical_violations=sorted(violations),
        saved_html_integrity=html_integrity,
        effective_input_digest=effective_input_digest,
        functional_status='passed' if result['functional_status'] == 'passed' and not violations else 'failed')
    return result


def aggregate(records):
    if any(record.get('evaluation_version') != VERSION for record in records):
        raise ContractError('评估记录版本不一致，旧回执不能冒充新版评估')
    converted = [dict(record, evaluation_version=legacy.VERSION) for record in records]
    result = legacy.aggregate(converted)
    groups = {(entry, intent): [] for entry in legacy.ENTRIES for intent in legacy.INTENTS}
    for record in records:
        pair = (record['entry'], record['intent'])
        if pair not in groups:
            raise ContractError('评估记录包含未登记的研究入口或意图')
        groups[pair].append(record)
    coverage = [
        dict(entry=entry, intent=intent, recorded_runs=len(group),
             distinct_inputs=len({record['input_digest'] for record in group}),
             functional_passed=sum(record['functional_status'] == 'passed' for record in group))
        for (entry, intent), group in groups.items()
    ]
    result.update(evaluation_version=VERSION,
        cost_basis='sum_of_cumulative_run_ledgers_not_unique_network_usage',
        saved_html_verified_count=sum(record.get('saved_html_integrity') == 'verified' for record in records),
        legacy_html_unverified_count=sum(record.get('saved_html_integrity') == 'legacy_unverified' for record in records),
        entry_intent_coverage=coverage,
        uncovered_entry_intent_combinations=[dict(entry=row['entry'], intent=row['intent'])
                                              for row in coverage if row['recorded_runs'] == 0])
    return result
