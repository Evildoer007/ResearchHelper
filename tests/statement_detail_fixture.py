"""Local parser replay of owned original bytes, never a new acquisition."""
import base64
import copy
import json
from pathlib import Path

from researchhelper.contracts import fingerprint
from researchhelper.material_parser import MaterialParser

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'result/acceptance/one-sentence-research-8s4to77o/task.json'


def replay_statement_details():
    task=json.loads(SOURCE.read_bytes())
    requests=copy.deepcopy(task['reasoning_state']['reading']['bundle']['selection'])
    witness=[]
    for record in task['source_records']:
        if record.get('kind')!='filing':continue
        old=record['snapshot']['material'];prior=record['hash']
        raw=base64.b64decode(old['original_base64'],validate=True)
        parsed=MaterialParser().parse(old['name'],raw)
        if parsed['status']!='已解析':raise AssertionError(parsed.get('parse_runtime'))
        assert parsed['hash']==old['hash'] and parsed['extract_version']=='pdf-tables-6'
        runtime=parsed.pop('parse_runtime');parsed.pop('obtained_at',None)
        parsed['original_base64']=old['original_base64']
        record['snapshot']['material']=parsed
        record['hash']=record['snapshot_hash']=fingerprint(record['snapshot'])
        record['acquisition']['snapshot_hash']=record['hash']
        event=task['ledger']['dispatches'][record['acquisition']['dispatch_index']]
        event.update(response_snapshot_hash=record['hash'],synthetic_test_transport=True,
                     representation_replay_prior_hash=prior)
        for request in requests:
            if request['artifact_id']==record['id']:request['snapshot_hash']=record['hash']
        witness.append(dict(artifact_id=record['id'],original_sha256=old['hash'],
            prior_snapshot_sha256=prior,replayed_snapshot_sha256=record['hash'],
            parser_runtime=runtime,new_supplier_acquisition=False))
    task['financial_statement_details_version']=1
    task['table_geometry_projection_version']=1
    task['financial_amount_reference_version']=1
    task['reasoning_state']={}
    assert len(witness)==2
    return task,requests,witness
