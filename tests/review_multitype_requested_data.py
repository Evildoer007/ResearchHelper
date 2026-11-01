"""Read-only review of a completed matrix, including source-literal precision.

This never changes the first driver's receipt, asks a model, resumes a task,
or treats rounded disclosure figures as exact accounting-table amounts.
"""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re

from tests import ROOT
from tests.accept_multitype_requested_data import field_review
from tests.test_asset_data_acceptance_review import verify_source
from researchhelper.contracts import fingerprint, validate_evidence
from researchhelper.data_requirements import pending


def load_task(folder):
    path = folder / 'task.json.gz'
    if path.exists():
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            return json.load(stream)
    return json.loads((folder / 'task.json').read_text())


def broker_literal_review(task):
    fields = field_review('equity', task)
    sources = {s['id']: s for s in task['source_records']}
    summary = dict(brokerage=('经纪业务', '131.42'),
                   asset_management=('资产管理业务', '80.03'),
                   securities_investment=('证券投资业务', '198.54'),
                   underwriting=('证券承销业务', '29.14'), other_business=('其他业务', '57.79'))
    precision = {}
    for key, (label, value) in summary.items():
        if fields[key]:
            precision[key] = 'exact_yuan_table'
            continue
        matches = []
        for item in task['evidence']:
            if (item.get('object') != '600030.SH' or item.get('unit') != '亿元'
                or str(item.get('value')) != value or item.get('period') != '2026-01-01/2026-06-30'):
                continue
            source = sources[item['artifact_id']]
            locator = item.get('record_locator', {}).get('part')
            part = next((p for p in source.get('snapshot', {}).get('material', {}).get('parts', [])
                         if p['locator'] == locator), None)
            if not part:
                continue
            text = re.sub(r'\s+', '', part['text'])
            pattern = re.escape(label) + r'实现收入人民币' + re.escape(value) + '亿元'
            if '2026年上半年' in text and re.search(pattern, text):
                matches.append(item['id'])
        fields[key] = matches
        precision[key] = 'literal_rounded_yi_yuan_summary' if matches else 'missing'
    return fields, precision


def verify_retained_source(source, task):
    if source['status'] == '成功待核验':
        verify_source(source, task)
        return
    # Parsing-limited originals remain a real failure. They do not carry a
    # successful acquisition approval, but their raw bytes must still match.
    assert source['task_id'] == task['id'] and source['research_version'] == task['version']
    assert fingerprint(source['snapshot']) == source.get('snapshot_hash', source['hash'])
    assert source['request_hash'] == fingerprint(dict(source=source['source'],
        operation=source['operation'], parameters=source['parameters']))
    assert any(d.get('fingerprint') == source['request_hash'] for d in task['ledger']['dispatches'])
    material = source.get('snapshot', {}).get('material', {})
    if material.get('original_base64'):
        raw = base64.b64decode(material['original_base64'], validate=True)
        assert hashlib.sha256(raw).hexdigest() == material['hash']


def review(matrix_path, *, recovered_receipts=None):
    matrix_path = Path(matrix_path)
    raw = matrix_path.read_bytes()
    matrix = json.loads(raw)
    if matrix['status'] != 'closed_with_per_scope_results':
        raise ValueError('只复核已经结束的独立测试')
    result = dict(scope='independent_fresh_sample_review', matrix=str(matrix_path),
                  matrix_sha256=hashlib.sha256(raw).hexdigest(), network_calls=0, model_calls=0,
                  original_receipts_changed=False, report_accepted=False, installed_app_accepted=False,
                  all_asset_types_accepted=False, cases=[])
    for row in matrix['cases']:
        child = dict(name=row['name'], driver_review_status=row['status'])
        result['cases'].append(child)
        receipt_name = row.get('receipt') or (recovered_receipts or {}).get(row['name'])
        if not receipt_name:
            child.update(status='acceptance_failed', error_type=row.get('error_type'))
            continue
        receipt_path = Path(receipt_name)
        receipt = json.loads(receipt_path.read_text())
        assert receipt['question'] == row['question']
        if not row.get('receipt'):
            child['collector_review_error'] = row.get('error_type')
            child['receipt_recovered_read_only'] = True
        task = load_task(receipt_path.parent)
        assert all(receipt[k] for k in ('production_unchanged', 'source_unchanged', 'owned_runtime_closed'))
        for source in task['source_records']:
            verify_retained_source(source, task)
        for item in task['evidence']:
            validate_evidence(item, task)
        fields = field_review(row['name'], task)
        if row['name'] == 'equity':
            fields, child['disclosed_precision'] = broker_literal_review(task)
            child['precision_note'] = '亿元摘要保留原值，不当成元表精确值，不据此作精确加总。'
        unmet = pending(task)
        complete = bool(fields) and all(fields.values()) and not unmet and not task['gaps'] and not task.get('rejected_evidence')
        child.update(status='requested_sample_complete' if complete else 'requested_sample_incomplete',
                     receipt=str(receipt_path), fields=fields, gaps=task['gaps'], pending_requirements=unmet,
                     used=task['ledger']['used'], evidence_count=len(task['evidence']),
                     production_unchanged=True, source_unchanged=True, owned_runtime_closed=True)
        if row['name'] in ('industry', 'index'):
            child['unverified_scope'] = ('完整分类样本及逐公司财务' if row['name'] == 'industry'
                                         else '完整同日权重的金融证据采用；下载单独记录')
        child['source_operations'] = [dict(source=s['source'], operation=s['operation'], status=s['status'])
                                      for s in task['source_records']]
    target = matrix_path.parent / 'independent-review.json'
    if target.exists():
        raise ValueError('不覆盖既有复核')
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    assert matrix_path.read_bytes() == raw
    return target


def run():
    files = list((ROOT / 'result/acceptance').glob('multitype-requested-data-*/receipt.json'))
    matrix = max(files, key=lambda p: p.stat().st_mtime)
    recovered = ({'industry': ROOT / 'result/acceptance/autonomous-data-phase-ycntrorz/receipt.json'}
                 if matrix.parent.name == 'multitype-requested-data-flzmh5zu' else {})
    target = review(matrix, recovered_receipts=recovered)
    result = json.loads(target.read_text())
    print(json.dumps(dict(receipt=str(target), cases=[dict(name=c['name'], status=c['status'])
                                                    for c in result['cases']]), ensure_ascii=False))


if __name__ == '__main__':
    run()
