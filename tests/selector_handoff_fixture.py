"""Explicit valid/invalid selections from an owned, closed R176 acquisition.

This does not resume its research. The model HTTP response is synthetic and
all real external I/O is denied by the calling tests.
"""
import copy
import json
from pathlib import Path

from researchhelper.reasoning import evidence_contract
from researchhelper.output_schema import original_batch_output_schema, original_batch_prompt

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'result/acceptance/one-sentence-research-s4h1y39l/task.json'


def wire_for(task, views):
    payload = dict(question=task['question'], intent=task['intent'], objects=task['objects'],
        cutoff=task['cutoff'], questions=task['questions'], calculations=[],
        untrusted_sources=views, evidence_contract=evidence_contract(task, views), field_registry={})
    schema = original_batch_output_schema(task, views)
    prompt = original_batch_prompt(payload, task)
    index = next(index for index, source in enumerate(payload['evidence_contract']['sources'])
        if any(cell['context']['metric'] == 'revenue' and '2026' in cell['context']['period']
               for cell in source.get('financial_context_cells', [])))
    source = payload['evidence_contract']['sources'][index]
    choice = prompt['source_choices'][index]
    cell = next(cell for cell in source['financial_context_cells']
        if cell['context']['metric'] == 'revenue' and '2026' in cell['context']['period'])
    owner = next(question for question in task['questions'] if question['id'] == 'q2')
    need = next(row['id'] for row in payload['evidence_contract']['question_requirements'][owner['id']]
        if '2026' in row['text'] and '营业收入' in row['text'])
    links = {question['id']: None for question in task['questions']}
    links[owner['id']] = [need]
    observation = next(row['reference'] for row in prompt['untrusted_sources'][index]['observation_choices']
        if row['date'] == '2026-06-30')
    valid = dict(id='handoff_revenue_2026', object=source['object_binding']['value'], nature='已实现事实',
        financial_context_reference=cell['candidate_id'],
        shared_context_references={field: source['financial_shared_context_choices'][field][0]['candidate_id']
                                   for field in ('entity_id', 'accounting_standard')},
        observation_reference=observation,
        review=dict(scope='current_original_descriptive_only', need_links=links))
    bad_links = {question['id']: None for question in task['questions']}
    bad_links[owner['id']] = []
    invalid = dict(id='handoff_rejected_optional', object=valid['object'], nature=valid['nature'],
        amount_reference='private_optional_address_marker', observation_reference=observation,
        review=dict(scope='current_original_descriptive_only', need_links=bad_links))
    selection = dict(version=9, source_reference=choice['binding'], current_version_only=True,
        financial_cells=[valid], text_quotes=[], amount_cells=[invalid])
    wire = dict(version=1, binding_ref=prompt['batch_binding_ref'],
        sources=[dict(source_ref=choice['source_ref'], selection=selection)])
    return payload, schema, wire, prompt


def retained_case():
    task = json.loads(SOURCE.read_bytes())
    assert task['status'] == '部分完成' and not task['evidence'] and not task['calculations']
    views = copy.deepcopy(task['reasoning_state']['reading']['bundle']['views'])
    assert task['financial_address_isolation_version'] == 1
    return task, views, wire_for(task, views)
