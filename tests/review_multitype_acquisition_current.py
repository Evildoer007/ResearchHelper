"""Read-only review of retained real responses; no replay or provider calls."""
import base64
import gzip
import hashlib
import json
from pathlib import Path

from tests import ROOT
from tests.probe_multitype_acquisition_current import inspect_series
from researchhelper.contracts import fingerprint


def verify_source_snapshot(source):
    snapshot=source['snapshot']
    if source['kind']=='material':
        # Official material keeps binary-original and parsed-snapshot hashes
        # separately. They certify different bytes and cannot be interchanged.
        assert fingerprint(snapshot)==source['snapshot_hash']
        payload=base64.b64decode(snapshot['original_base64'],validate=True)
        assert hashlib.sha256(payload).hexdigest()==snapshot['hash']==source['hash']
    else:
        assert fingerprint(snapshot)==source['hash']


def run():
    base=ROOT/'result/acceptance'
    folders=[base/'data-acquisition-matrix-current',base/'registered-discovery-current']
    output=base/'data-acquisition-review-current.json'
    if output.exists():raise ValueError('不覆盖已有复核')
    protected={path:hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in folders for path in folder.iterdir() if path.is_file()}
    record=dict(status='reviewed_with_gaps',scope='explicit_request_acquisition_not_autonomous_chat',
        provider_calls=0,model_calls=0,source_archives=[],problems=[],physical_pdf_reading_approved=False,
        task_types_fully_accepted=False,financial_approval=False)
    calls=0
    for folder in folders:
        receipt=json.loads((folder/'receipt.json').read_text())
        assert receipt['production_unchanged'] and receipt['private_store_removed']
        assert receipt['ledger']['used']['model_calls']==0
        calls+=receipt['ledger']['used']['data_requests']
        sources=json.load(gzip.open(folder/'sources.json.gz','rt',encoding='utf-8'))
        if receipt.get('sources_sha256'):
            assert hashlib.sha256((folder/'sources.json.gz').read_bytes()).hexdigest()==receipt['sources_sha256']
        by_id={source['id']:source for source in sources}
        assert len(by_id)==len(sources)
        original_count=0
        for source in sources:
            verify_source_snapshot(source)
            assert source['task_id']==receipt['task_id'] and source['research_version']==1
            assert fingerprint(dict(source=source['source'],operation=source['operation'],
                parameters=source['parameters']))==source['request_hash']
            assert any(event.get('fingerprint')==source['request_hash'] and event['status']=='成功'
                for event in receipt['ledger']['dispatches'])
            if source['operation'] in ('cmd_history_quotation','date_sequence'):
                assert inspect_series(source,source['parameters'])['usable']
            if source['operation']=='filing_original':
                material=source['snapshot']['material']
                payload=base64.b64decode(material['original_base64'],validate=True)
                assert hashlib.sha256(payload).hexdigest()==material['hash']
                assert source['parameters']['catalog_id'] in by_id
                catalog=by_id[source['parameters']['catalog_id']]
                assert any(str(row['source_id'])==str(source['parameters']['source_id']) for row in catalog['catalog_entries'])
                original_count+=1
        for row in receipt['results']:
            harness_issue=(folder==folders[0] and row['label'] in ('证券行业候选','算力产业链候选','上证50成分'))
            if harness_issue:continue
            if row['status'] in ('failed','returned_but_incomplete'):
                request_hash=fingerprint(dict(source=row.get('source','ifind'),operation=row['operation'],parameters=row['parameters']))
                events=[event for event in receipt['ledger']['dispatches'] if event.get('fingerprint')==request_hash]
                record['problems'].append(dict(label=row['label'],status=row['status'],
                    provider_business_codes=[event['business_code'] for event in events if 'business_code' in event],
                    unmatched_fields=row.get('inspection',{}).get('contract',{}).get('unmatched_fields',[])))
        record['source_archives'].append(dict(path=str(folder/'sources.json.gz'),
            sha256=hashlib.sha256((folder/'sources.json.gz').read_bytes()).hexdigest(),
            records=len(sources),originals_hash_checked=original_count,
            receipt_sha256=hashlib.sha256((folder/'receipt.json').read_bytes()).hexdigest()))
    record.update(real_requests=calls,raw_response_integrity_checked=True,
        first_matrix_harness_issue='3项本地拒绝来自旧策略任务，已用当前App策略独立实取，不计为供应商失败。',
        no_study_reopened=True,inputs_unchanged=all(hashlib.sha256(path.read_bytes()).hexdigest()==value
            for path,value in protected.items()))
    assert record['inputs_unchanged']
    output.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print(json.dumps(dict(status=record['status'],real_requests=calls,problems=record['problems'],receipt=str(output)),
        ensure_ascii=False),flush=True)
    return record
