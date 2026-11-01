"""Read-only data-first review of actual retained autonomous studies.

Do not resume studies, fill missing links, call suppliers/models or execute
analysis. Registered coverage is not semantic sufficiency. A bound independent
review can reveal data missing inside an apparently covered compound need.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import socket
import tempfile
from unittest.mock import patch

from tests import ROOT
from tests.accept_one_sentence_research import production_digest
from tests.audit_data_acquisition_layers import inspect_source
from tests.audit_required_data_readiness import requirement_inventory
from researchhelper.argument_support import find_record
from researchhelper.contracts import ContractError
from researchhelper.evidence import validate_provenance


# Labels describe these bounded questions, not inferred task entry types.
SAMPLES = (
    ('个股财务', 'autonomous-data-phase-6p255og9'),
    ('行业单公司样本', 'autonomous-data-phase-ma2846eh'),
    ('ETF产品规则', 'autonomous-data-phase-6hjk3w0z'),
    ('指数名单及个股行情', 'autonomous-data-phase-zdtl2f5m'),
    ('FOMC声明', 'one-sentence-research-11r37p0e'),
    ('制造业PMI', 'one-sentence-research-c9m88gfl'),
    ('ETF报告可得性复核', 'one-sentence-research-elj_tzd_'),
)


def inspect_task(task):
    """Recompute source contracts; never trust success flags or evidence counts."""
    sources = [inspect_source(source) for source in task.get('source_records', [])]
    sources_valid = bool(sources) and all(row['snapshot_hash_valid']
        and row.get('original_bytes_hash_valid', True) for row in sources)
    checked, failures = [], []
    for item in task.get('evidence', []):
        try:
            validate_provenance(item, task)
            checked.append(item['id'])
        except ContractError:
            failures.append(item['id'])
    inventory = requirement_inventory(task)
    required = [row for row in inventory['questions'] if row['required']]
    linked = bool(required) and inventory['required_data_obligations_settled']

    # find_record reconstructs and validates the exact current proposal and
    # input hash. An older review must not clear or introduce current gaps.
    review_status, assessments = 'not_available_for_current_proposal', []
    proposal = task.get('host_result')
    if proposal and (proposal.get('answers') or proposal.get('theses')):
        try:
            record = find_record(task, proposal)
            if record is not None:
                review_status = 'bound_review_revalidated'
                assessments = record['result']['question_assessments']
        except ContractError:
            review_status = 'invalid_or_stale_review'
    required_ids = {row['question_id'] for row in required}
    gaps = [dict(question_id=row['question_id'], missing_evidence=row.get('missing_evidence', []),
                 reason=row.get('reason')) for row in assessments
            if row['question_id'] in required_ids and row['status'] == 'requires_evidence']
    input_checks = sources_valid and not failures and linked
    status = ('data_gaps_remain' if not input_checks or gaps
              else 'registered_inputs_revalidated_semantics_not_accepted')
    return dict(status=status, task_id=task['id'], research_version=task['version'],
        historical_task_status=task['status'], sources=sources,
        source_integrity_passed=sources_valid, provenance_revalidated_ids=checked,
        provenance_failed_ids=failures, registered_input_checks_passed=input_checks,
        requirement_inventory=inventory, independent_review_status=review_status,
        independently_identified_data_gaps=gaps,
        all_research_data_sufficient_accepted=False, strategy_accepted=False,
        report_accepted=False, installed_app_accepted=False)


def run():
    folder = Path(tempfile.mkdtemp(prefix='data-first-readiness-', dir=ROOT/'result/acceptance'))
    folder.chmod(0o700)
    protected, studies = {}, []
    production_before = production_digest()
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Data-first audit is offline')):
        for scope, name in SAMPLES:
            path = ROOT/'result/acceptance'/name/'task.json'
            # A missing fixture is an explicit gap, never a silent skip.
            if not path.is_file():
                studies.append(dict(scope=scope, study=name, status='retained_study_missing'))
                continue
            protected[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            task = json.loads(path.read_text())
            row = dict(scope=scope, study=name, **inspect_task(task))
            studies.append(row)
            progress = folder/'progress.json'
            progress.write_text(json.dumps(studies, ensure_ascii=False, indent=2))
            print(json.dumps(dict(scope=scope, study=name, status=row['status'],
                adopted=len(task['evidence']), revalidated=len(row['provenance_revalidated_ids']),
                independently_identified_data_gaps=len(row['independently_identified_data_gaps'])),
                ensure_ascii=False), flush=True)
            del task
    unchanged = all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected
                    for path, expected in protected.items())
    production_unchanged = production_before == production_digest()
    assert unchanged and production_unchanged, 'Read-only review observed changed protected data'
    result = dict(status='review_complete_with_gaps', studies=studies,
        uncovered_scope=['产业链经营数据全链路', '跨类型混合问题完整取数'],
        all_eight_types_accepted=False, provider_calls=0, model_calls=0,
        new_data_downloads=0, retained_studies_resumed=False,
        calculations_executed=False, analysis_started=False,
        strategy_accepted=False, report_accepted=False,
        installed_app_updated=False, skill_updated=False, demo_synced=False,
        retained_files_unchanged=unchanged, production_unchanged=production_unchanged,
        protected_sha256=protected)
    target = folder/'receipt.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(dict(receipt=str(target), status=result['status'],
        samples=len(studies), provenance_revalidated=sum(len(row.get('provenance_revalidated_ids', []))
        for row in studies)), ensure_ascii=False), flush=True)
    return target
