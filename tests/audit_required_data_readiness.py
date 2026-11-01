"""Read-only audit of acquired data against every registered obligation.

File download, excerpt adoption, input availability and answer acceptance are
different states. This review never infers a semantic link, changes a study,
or promotes an independent text review into a source approval.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import socket
import tempfile
from unittest.mock import patch

from tests import ROOT
from tests.accept_autonomous_data_phase import method_input_inventory
from tests.accept_one_sentence_research import production_digest
from tests.audit_data_acquisition_layers import inspect_source
from researchhelper.contracts import ContractError, METHOD_REGISTRY, missing_evidence_requirements
from researchhelper.market_calendar import covered_need_ids as calendar_need_ids
from researchhelper.provenance import bind_query_references


STUDIES = (
    'autonomous-data-phase-ma2846eh',
    'one-sentence-research-p0x5bdar',
    'one-sentence-research-f6e_kjx1',
    'one-sentence-research-aq8vm90m',
    'one-sentence-research-11r37p0e',
    'one-sentence-research-c9m88gfl',
)


def requirement_inventory(task):
    """Conservative coverage diagnostics, assuming independently checked inputs.

    A missing link means unresolved coverage, not proof a file is absent.
    No word matching or file-receipt shortcut covers a financial requirement.
    Numeric candidates are not executed calculations or approved strategies.
    """
    eligible = []
    excluded = []
    for item in task.get('evidence', []):
        same_version = (type(item.get('research_version')) is int
                        and item['research_version'] == task['version'])
        value = item.get('value')
        valid_value = value is None or type(value) in (int, float) and math.isfinite(value)
        if (item.get('task_id') == task['id'] and same_version and valid_value
                and item.get('source_check') == '通过' and item.get('applicability_check') == '通过'):
            eligible.append(item)
        else:
            excluded.append(item.get('id'))

    questions = []
    for question in task.get('questions', []):
        ident = question['id']
        references = task.get('host_result', {}).get('answers', {}).get(ident, {}).get(
            'provenance_refs', question.get('provenance_refs', []))
        invalid_lineage = False
        try:
            lineage = bind_query_references(task, question, references)
        except ContractError:
            lineage = {'covered_need_ids': []}
            invalid_lineage = True
        try:
            calendar = calendar_need_ids(task, question)
            invalid_calendar = False
        except ContractError:
            calendar, invalid_calendar = [], True
        unresolved = missing_evidence_requirements(question, eligible,
            lineage['covered_need_ids'], market_need_ids=calendar)

        # Explicit executable requests remain obligations even when omitted
        # from method_needs. Never silently skip an unregistered method.
        methods = list(dict.fromkeys(question.get('method_needs', []) + [
            spec['method'] for spec in task.get('calculation_specs', [])
            if ident in spec.get('question_ids', [])]))
        registered = [method for method in methods if method in METHOD_REGISTRY]
        inputs = method_input_inventory(dict(task, evidence=eligible,
            questions=[dict(question, method_needs=registered)]))
        pending_inputs = [item for item in inputs if not item['units_available']
            or item['compatible_financial_roles_available'] is False]
        unknown_methods = [method for method in methods if method not in METHOD_REGISTRY]
        settled = not (unresolved or pending_inputs or unknown_methods or invalid_lineage or invalid_calendar)
        questions.append(dict(question_id=ident, required=question.get('required', True),
            unresolved_requirements=unresolved, method_inputs=inputs,
            pending_method_inputs=pending_inputs, unregistered_methods=unknown_methods,
            invalid_query_binding=invalid_lineage, invalid_calendar_binding=invalid_calendar,
            registered_data_obligations_settled=settled))
    return dict(version=1, task_id=task['id'], research_version=task['version'],
        questions=questions, excluded_evidence_ids=excluded,
        required_data_obligations_settled=bool(questions) and all(
            item['registered_data_obligations_settled'] for item in questions if item['required']),
        independent_source_validation_required=True,
        calculation_execution_accepted=False, strategy_accepted=False,
        semantic_sufficiency_accepted=False, customer_report_accepted=False,
        unresolved_link_is_proof_of_missing_download=False)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run():
    folder = Path(tempfile.mkdtemp(prefix='required-data-readiness-', dir=ROOT/'result/acceptance'))
    protected = {}
    reviewed = []
    production_before = production_digest()
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Read-only data audit')):
        for name in STUDIES:
            path = ROOT/'result/acceptance'/name/'task.json'
            protected[str(path)] = _sha(path)
            task = json.loads(path.read_text())
            sources = [inspect_source(source) for source in task['source_records']]
            assert all(item['snapshot_hash_valid'] and item.get('original_bytes_hash_valid', True)
                       for item in sources)
            inventory = requirement_inventory(task)
            reviewed.append(dict(study=name, historical_task_status=task['status'],
                sources=sources, inventory=inventory, study_resumed=False,
                evidence_links_changed=False, historical_status_changed=False))
            del task
    unchanged = all(_sha(Path(path)) == expected for path, expected in protected.items())
    assert unchanged and production_before == production_digest()
    receipt = dict(status='review_complete_with_gaps', studies=reviewed,
        provider_calls=0, model_calls=0, source_validation='retained_snapshot_and_original_bytes',
        all_task_types_accepted=False, strategy_accepted=False, app_updated=False,
        retained_files_unchanged=unchanged, production_unchanged=True,
        protected_sha256=protected)
    target = folder/'receipt.json'
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(dict(receipt=str(target), studies=[dict(study=item['study'],
        settled=item['inventory']['required_data_obligations_settled'],
        unresolved=sum(len(q['unresolved_requirements']) for q in item['inventory']['questions']))
        for item in reviewed]), ensure_ascii=False), flush=True)
    return target
