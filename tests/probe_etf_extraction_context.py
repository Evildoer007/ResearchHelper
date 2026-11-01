"""Measure the retained two-original extraction; no model or study replay."""
import hashlib
import json
from pathlib import Path
import socket
import tempfile
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import canonical
from researchhelper.evidence import FIELD_REGISTRY
from researchhelper.output_schema import original_span_output_schema, original_span_prompt
from researchhelper.planning import question_specs
from researchhelper.reasoning import evidence_contract

RETAINED=ROOT/'result/acceptance/autonomous-data-phase-cfm6_slz/task.json'


def payload_for(task):
    bundle=task['reasoning_state']['reading']['bundle'];views=bundle['views']
    acquired={str(v.get('source'))+'.'+str(v.get('operation')) for v in views if v.get('kind')=='api'}
    payload=dict(question=task['question'],intent=task['intent'],objects=task['objects'],cutoff=task['cutoff'],
        questions=question_specs(task),calculations=task['calculation_specs'],evidence_contract=evidence_contract(task,views),
        field_registry={key:FIELD_REGISTRY[key] for key in acquired if key in FIELD_REGISTRY},untrusted_sources=views,
        source_reading={key:bundle[key] for key in ('reading_hash','coverage','limitations')})
    payload['calculation_input_targets']=payload['evidence_contract']['calculation_input_requirements']['proposal_requirements']
    return payload


def run(*, projected=False):
    raw=RETAINED.read_bytes();task=json.loads(raw)
    if projected:
        from researchhelper.planning import input_fingerprint
        task['original_prompt_projection_version']=1
        task['input_hash']=input_fingerprint(task)
    with patch.object(socket.socket,'connect',side_effect=AssertionError('Read-only extraction measurement')):
        payload=payload_for(task)
        presented=original_span_prompt(payload,task)
        schema=original_span_output_schema(task,payload['untrusted_sources'])
    sections={key:len(canonical(value)) for key,value in presented.items()}
    sources=[dict(source=v['source_ref'],characters=len(canonical(v)),
        sections={key:len(canonical(value)) for key,value in v.items()}) for v in presented['untrusted_sources']]
    record=dict(status='measured',retained=str(RETAINED),provider_calls=0,model_calls=0,study_resumed=False,
        projection_on_memory_copy=projected,
        task_unchanged=RETAINED.read_bytes()==raw,retained_sha256=hashlib.sha256(raw).hexdigest(),
        payload_characters=len(canonical(presented)),schema_characters=len(canonical(schema)),
        total_characters=len(canonical(presented))+len(canonical(schema)),sections=sections,sources=sources)
    folder=Path(tempfile.mkdtemp(prefix='etf-extraction-context-',dir=ROOT/'result/acceptance'))
    target=folder/'receipt.json';target.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print(json.dumps(dict(receipt=str(target),total=record['total_characters'],schema=record['schema_characters'],
        sections=sections,sources=sources),ensure_ascii=False),flush=True)
    return target
