"""Measure the actual retained operating-data extraction wire, without dispatch."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile

from tests import ROOT
from researchhelper.contracts import canonical
from researchhelper.output_schema import (original_span_prompt, original_span_output_schema,
                                         provider_request_schema, ORIGINAL_SPAN_PROTOCOL)
from researchhelper.reasoning import evidence_contract, question_specs, MAX_CONTEXT_CHARACTERS
from researchhelper.model_presentation import present, restore

SOURCE = ROOT / 'result/acceptance/autonomous-data-phase-sn86n7gl/task.json'


def payload(task):
    bundle = task['reasoning_state']['reading']['bundle']
    views = bundle['views']
    result = dict(question=task['question'], intent=task['intent'], objects=task['objects'], cutoff=task['cutoff'],
                  questions=question_specs(task), calculations=task['calculation_specs'],
                  evidence_contract=evidence_contract(task, views), field_registry={}, untrusted_sources=views,
                  source_reading={key: bundle[key] for key in ('reading_hash', 'coverage', 'limitations')})
    requirements = result['evidence_contract']['calculation_input_requirements']
    result['calculation_input_targets'] = copy.deepcopy(requirements['proposal_requirements'])
    result['unbound_calculation_input_requirements'] = copy.deepcopy(requirements['unbound_requirements'])
    return result


def run():
    original = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    task = json.loads(SOURCE.read_text())
    supplied = payload(task)
    prompt = original_span_prompt(supplied, task)
    schema = original_span_output_schema(task, supplied['untrusted_sources'])
    wire = provider_request_schema(task, schema, ORIGINAL_SPAN_PROTOCOL)
    size = lambda value: len(canonical(value))
    compact, changed = present(prompt)
    assert canonical(restore(compact)) == canonical(prompt) if changed else compact == prompt
    output = Path(tempfile.mkdtemp(prefix='operating-extraction-capacity-', dir=ROOT / 'result/acceptance'))
    record = dict(status='measured', source_sha256=original, body_characters=size(prompt),
                  schema_characters=size(wire), max_characters=MAX_CONTEXT_CHARACTERS,
                  combined_characters=size(prompt) + size(wire),
                  lossless_presentation_changed=changed, compact_body_characters=size(compact),
                  compact_combined_characters=size(compact) + size(wire),
                  body_fields={key: size(value) for key, value in prompt.items()},
                  source_fields=[{key: size(value) for key, value in source.items()}
                                 for source in prompt['untrusted_sources']],
                  source_part_encodings=['layout_rows' if isinstance(source.get('parts'),dict)
                                         else 'original_records' for source in prompt['untrusted_sources']],
                  contract_fields={key: size(value) for key, value in prompt['evidence_contract'].items()},
                  source_unchanged=hashlib.sha256(SOURCE.read_bytes()).hexdigest() == original,
                  model_calls=0, network_calls=0, old_study_resumed=False)
    assert record['source_unchanged']
    receipt = output / 'receipt.json'
    receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(dict(receipt=str(receipt), **record), ensure_ascii=False))
    return receipt


if __name__ == '__main__':
    run()
