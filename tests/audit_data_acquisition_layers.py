"""Read-only independent acquisition review; never resumes any study.

Separate explicit live adapter samples, earlier autonomous downloads, extraction,
and missing breadth. No model, provider request or research conclusion here.
"""
import base64
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
from unittest.mock import patch

from tests import ROOT
from tests.audit_task_type_readonly_review import entry_probes
from tests.probe_data_acquisition_current import table_inventory, material_inventory
from researchhelper.archive_codec import decode_body
from researchhelper.contracts import fingerprint

LIVE = ROOT / 'result/acceptance/data-acquisition-current-pajcq5fi'
RETAINED = dict(个股='f6e_kjx1', ETF='aq8vm90m', 事件='11r37p0e', 宏观='c9m88gfl')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_source(source):
    # Official originals bind original bytes with hash and the full parsed
    # snapshot separately with snapshot_hash. Vendor API/filing records bind
    # their snapshots with hash. Preserve each registered contract.
    expected = (source.get('snapshot_hash') if source.get('source') == 'official'
                and source['operation'] == 'release_original' else source['hash'])
    row = dict(artifact_id=source['id'], operation=source['operation'],
        snapshot_hash_valid=fingerprint(source['snapshot']) == expected)
    if source['operation'] in ('cmd_history_quotation', 'date_sequence'):
        row['tables'] = table_inventory(source)
    if source['operation'] in ('filing_original', 'release_original'):
        row['material'] = material_inventory(source)
        material = source['snapshot'].get('material', source['snapshot'])
        encoded = material.get('original_base64')
        if encoded:
            row['original_bytes_hash_valid'] = (
                hashlib.sha256(base64.b64decode(encoded, validate=True)).hexdigest()
                == material['hash'])
    return row


def run():
    folder = Path(tempfile.mkdtemp(prefix='data-acquisition-layers-', dir=ROOT / 'result/acceptance'))
    database = LIVE / 'store/researchhelper.sqlite3'
    paths = [LIVE / 'receipt.json', database]
    paths += [ROOT / 'result/acceptance' / ('one-sentence-research-' + suffix) / 'task.json'
              for suffix in RETAINED.values()]
    protected = {str(path): sha(path) for path in paths}
    receipt = json.loads((LIVE / 'receipt.json').read_text())
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Read-only acquisition audit')):
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            body = db.execute('SELECT body FROM tasks WHERE id=?', (receipt['task_id'],)).fetchone()[0]
            task = decode_body(db, body)
        current = [inspect_source(source) for source in task['source_records']]
        prior = []
        for entry, suffix in RETAINED.items():
            path = ROOT / 'result/acceptance' / ('one-sentence-research-' + suffix) / 'task.json'
            retained = json.loads(path.read_text())
            prior.append(dict(entry=entry, study=str(path), adopted_evidence=len(retained['evidence']),
                historical_task_status=retained['status'], study_resumed=False,
                sources=[inspect_source(source) for source in retained['source_records']]))
        probes = entry_probes(folder)
    assert all(row['snapshot_hash_valid'] and row.get('original_bytes_hash_valid', True)
               for row in current)
    assert all(row['snapshot_hash_valid'] and row.get('original_bytes_hash_valid', True)
               for study in prior for row in study['sources'])
    report = dict(status='review_complete_with_gaps', live_adapter_receipt=str(LIVE / 'receipt.json'),
        current_sources=current, earlier_autonomous_studies=prior, eight_entry_contracts=probes,
        live_model_calls=0, live_provider_calls=0, all_task_types_accepted=False,
        production_store_accessed=False, installed_app_updated=False,
        report_or_strategy_accepted=False, retained_files_unchanged=all(
            sha(Path(path)) == expected for path, expected in protected.items()),
        protected_sha256=protected,
        corrections=[dict(receipt=str(LIVE / 'receipt.json'),
            issue='原诊断器从snapshot根部读取PDF片段数量，实际片段位于snapshot.material.parts；原回执保留，本复核记录真实数量。')],
        gaps=[
            '当前实取为明确输入的适配器诊断，不是八类问题自主找数验收。',
            '行业缺少已登记行业分类、样本和行业指标的自动定位覆盖。',
            '产业链缺少环节、产品、样本和价格产销量指标的来源映射覆盖。',
            'EDB仅接受问题明确声明的指标ID，尚无按名称自动发现指标的已验收路径。',
            '指数仅验证行情和PE序列，成分权重、盈利及变动归因未完成取数验收。',
            'ETF当前验证净值字段和历史费率原件样本，未证明持仓、实时IOPV及单位口径全覆盖。',
            '事件官方原文仅固定统计局与FOMC声明渠道，其他事件不能视为已经接通。',
            '混合问题须逐个子问题核对来源覆盖，工具注册不代表全部数据已齐。'])
    assert report['retained_files_unchanged']
    (folder / 'receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(dict(status=report['status'], receipt=str(folder / 'receipt.json'),
        current_sources=len(current), earlier_studies=len(prior)), ensure_ascii=False), flush=True)
    return folder / 'receipt.json'
