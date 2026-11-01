"""Recheck existing real data without repeating downloads or model calls.

Historical data proofs never become new research, current provider permissions,
complete industry coverage or an investment conclusion by aggregation.
"""
import base64
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import socket
import tempfile
from unittest.mock import patch

from tests import ROOT
from tests.test_asset_data_acceptance_review import verify_source
from researchhelper.contracts import validate_evidence
from researchhelper.financial_sources import financial_batch_response
from researchhelper.index_sources import parse_csi_workbook

BASE = ROOT/'result/acceptance'
CASES = {
    '个股财报': 'one-sentence-research-pvgbk3m0',
    '港股行情': 'autonomous-data-phase-9f4ynnmz',
    'ETF费率': 'one-sentence-research-9eq092nf',
    '指数权重': 'single-index-original-9onkknj6',
    '行业批量财务': 'one-sentence-research-tm5y1fad',
    '产业链单公司经营数据': 'one-sentence-research-g994a8ie',
    '宏观CPI': 'autonomous-data-phase-z0qgbocs',
    '事件FOMC': 'autonomous-data-phase-bbjdyut3',
}


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require_local(path):
    path = Path(path).resolve(strict=True)
    if not path.is_relative_to(BASE) or not path.is_file(): raise ValueError('Only retained local evidence is allowed')
    return path


def task_file(folder):
    for name in ('task.json', 'task.json.gz'):
        path = folder/name
        if path.is_file(): return require_local(path)
    raise FileNotFoundError('No retained task payload')


def coverage_status(*, adopted_facts, raw_rows=0, metadata_verified=False, universe_verified=False):
    if adopted_facts: return '仅所列样本字段曾通过核验'
    if raw_rows and not metadata_verified: return '已有原始行，金额口径未核实'
    if raw_rows and not universe_verified: return '已有原始行，完整范围未独立核实'
    return '不能据此认定所需字段已齐备'


def audit_task(name, folder):
    source = task_file(folder)
    receipt = require_local(folder/'receipt.json')
    before = {str(path): digest(path) for path in (source, receipt)}
    if source.suffix == '.gz':
        with gzip.open(source, 'rt', encoding='utf-8') as stream: task = json.load(stream)
    else: task = json.loads(source.read_text())
    original = json.loads(receipt.read_text())
    for item in task['source_records']: verify_source(item, task)
    for item in task['evidence']: validate_evidence(item, task)
    row = dict(name=name, source_files=before, task_id=task['id'],
        original_task_status=task['status'], original_receipt_status=original['status'],
        source_count=len(task['source_records']), adopted_facts=len(task['evidence']),
        source_operations=list(dict.fromkeys(item['operation'] for item in task['source_records'])),
        historical_snapshot_integrity_verified=True, task_reopened=False,
        fresh_provider_permission_verified=False, current_app_case_reexecuted=False,
        full_domain_research_accepted=False,
        status=coverage_status(adopted_facts=len(task['evidence'])))
    if name == '行业批量财务':
        batches = [financial_batch_response(task, item['parameters'], item['snapshot'])
            for item in task['source_records'] if item['operation'] == 'smart_stock_picking'
            and '，' in item['parameters'].get('searchstring', '')]
        assert batches and all(value['raw_only'] and not value['numeric_evidence_registered'] for value in batches)
        row.update(raw_rows=sum(value['row_count'] for value in batches),
            units_verified=all(value['unit_verified'] for value in batches),
            currency_verified=all(value['currency_verified'] for value in batches),
            accounting_scope_verified=all(value['accounting_scope_verified'] for value in batches),
            classification_time_verified=all(value['classification_time_verified'] for value in batches),
            universe_verified=all(value['universe_completeness_verified'] for value in batches),
            duplicate_codes=sorted({code for value in batches for code in value['duplicate_codes']}))
        row['status'] = coverage_status(adopted_facts=0, raw_rows=row['raw_rows'])
        row['remaining'] = '需要官方字段参数、金额单位、币种、报表口径及带时点的分类和完整样本；不能用单家公司原件替全列定口径。'
    elif name == '指数权重':
        originals = [item for item in task['source_records'] if item['operation'] == 'index_file']
        assert len(originals) == 1
        snapshot = originals[0]['snapshot']
        raw = base64.b64decode(snapshot['original_base64'], validate=True)
        assert hashlib.sha256(raw).hexdigest() == snapshot['original_sha256']
        parsed = parse_csi_workbook(raw, snapshot['index_code'], snapshot['role'], version=2)
        row.update(raw_rows=len(parsed['members']), current_parser_rechecked=True,
            observation_date=parsed['observation_date'], weight_sum=parsed.get('weight_sum'),
            original_weights_renormalized=False, independent_membership_verified=False)
        assert Decimal(str(parsed['weight_sum'])) == Decimal(str(original['weight_sum']))
        row['status'] = '权重原件已取得并可由当前解析器读取，不代表研究证据已采用'
        row['remaining'] = '保留原始日期和权重精度，不能把下载成功自动升级为所有指数或完整研究通过。'
    elif name == '产业链单公司经营数据':
        row['remaining'] = '仅单公司产能、产量和利用率样本；不等于完整产业链、环节映射、库存或供需覆盖。'
    else:
        row['remaining'] = '仅该问题、日期与所列字段样本；不自动扩展至任意标的、连续序列或全部研究类型。'
    assert all(digest(path) == value for path, value in before.items())
    row['original_files_unchanged'] = True
    return row


def run():
    output = Path(tempfile.mkdtemp(prefix='retained-data-coverage-', dir=BASE))
    record = dict(status='running', model_calls=0, provider_calls=0, study_reopened=False,
        current_permissions_accepted=False, all_research_types_accepted=False, full_prd_accepted=False,
        source='retained_real_samples_not_new_downloads', cases=[], errors=[])
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('This audit cannot call a provider')):
        for name, folder in CASES.items():
            try: record['cases'].append(audit_task(name, BASE/folder))
            except Exception as error:
                record['errors'].append(dict(name=name, type=type(error).__name__, message=str(error)[:300]))
    record['mixed_research_status'] = '尚无跨类型完整研究的独立真实验收，不以多个旧样本拼成通过。'
    record['status'] = 'reviewed_with_open_scope_gaps' if not record['errors'] else 'audit_incomplete'
    target = output/'receipt.json'
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(dict(status=record['status'], receipt=str(target), errors=record['errors'],
        cases=[dict(name=row['name'], status=row['status']) for row in record['cases']]), ensure_ascii=False))
    return target


if __name__ == '__main__': run()
