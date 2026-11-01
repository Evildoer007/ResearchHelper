"""One extraction call on copied retained inputs, never a resumed study.

No acquisition, plan, answer or review is injected. Original accounting is
retained in the copied ledger, and a durable guard prevents repeated probes.
"""
import copy
import hashlib
import json
from pathlib import Path
import re
import tempfile

from tests import ROOT
from tests.accept_one_sentence_research import PRODUCTION, production_digest
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.evidence import candidate_evidence
from researchhelper.financial_sources import expand_financial_value_references
from researchhelper.model import ModelService, ModelSettings
from researchhelper.reasoning import evidence_contract, model_object, question_specs, validate_model_specs
from researchhelper.source_reader import validate_read_specs

CASE = 'financial-shared-selector-component-20261016-v1'
RETAINED = ROOT/'result/acceptance/one-sentence-research-2bsp956w/task.json'


def run(*, case=CASE, retained=RETAINED, task_updates=None, output_prefix='financial-shared-selectors-live-'):
    retained = Path(retained).resolve()
    updates = dict(task_updates or {})
    if (not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,160}', case)
            or not re.fullmatch(r'[a-z][a-z0-9-]{0,60}-', output_prefix)
            or not retained.is_relative_to(ROOT/'result/acceptance')
            or retained.name != 'task.json'
            or set(updates)-{'financial_text_period_version'}
            or any(type(value) is not int or value!=2 for value in updates.values())):
        raise ValueError('Use an explicit retained task and a registered protocol-only override')
    with (ROOT/'result/acceptance'/(case+'.guard')).open('x'):
        pass
    output = Path(tempfile.mkdtemp(prefix=output_prefix, dir=ROOT/'result/acceptance'))
    original = retained.read_bytes()
    task = json.loads(original)
    task.update(updates)
    before_production = production_digest()
    original_used = copy.deepcopy(task['ledger']['used'])
    record = dict(case=case, status='running', component_probe_not_resumed_research=True,
        retained_task=str(retained), protocol_only_overrides=updates,
        source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
            'core/src/researchhelper/output_schema.py', 'modules/evidencecollector/src/evidence.py',
            'modules/thesisbuilder/src/reasoning.py')},
        source_task_sha256=hashlib.sha256(original).hexdigest(), real_supplier_calls=0,
        maximum_new_model_calls=1, original_used=original_used,
        ledger_reset=False, automatic_retry=False, injected_evidence=False,
        complete_research_accepted=False, financial_quality_accepted=False,
        production_settings_modified=False, native_window_opened=False)
    try:
        bundle = task['reasoning_state']['reading']['bundle']
        views = bundle['views']
        contract = evidence_contract(task, views)
        payload = dict(question=task['question'], intent=task['intent'], objects=task['objects'],
            cutoff=task['cutoff'], questions=question_specs(task), calculations=task['calculation_specs'],
            evidence_contract=contract, field_registry={}, untrusted_sources=views,
            source_reading={key:bundle[key] for key in ('reading_hash','coverage','limitations')},
            calculation_input_targets=copy.deepcopy(contract['calculation_input_requirements']['proposal_requirements']))
        service = ModelService(ModelSettings(PRODUCTION), offline=False)
        proposed, receipt = model_object(service, task, 'extract', payload, {'evidence_specs'})
        specs = expand_financial_value_references(task, proposed['evidence_specs'], views)
        validate_model_specs(task, specs)
        validate_read_specs(task, specs, bundle)
        (output/'extraction.json').write_text(json.dumps(dict(proposal=proposed, expanded_specs=specs,
            model_receipt=receipt), ensure_ascii=False, indent=2))
        sources = {source['id']:source for source in task['source_records']+task.get('materials', [])}
        checked = []
        for spec in specs:
            evidence = candidate_evidence(task, sources[spec['artifact_id']], spec)
            assessed = ResearchAnalyzer().assess(task, evidence, spec['review'], task['source_records'])
            checked.append(dict(id=evidence['id'], source_check=evidence['source_check'],
                applicability_check=assessed['applicability_check'], rejection=assessed.get('rejection'),
                metric=evidence.get('calculation_context', {}).get('metric')))
        metrics = {item['metric'] for item in checked if
            item['source_check']==item['applicability_check']=='通过'}
        record.update(status='extraction_component_completed', checks=checked,
            three_required_metrics_verified={'revenue','net_profit','operating_cash_flow'}.issubset(metrics),
            proposed_count=len(specs))
    except Exception as error:
        record.update(status='failed', error_type=type(error).__name__)
    finally:
        record['new_model_calls'] = task['ledger']['used']['model_calls']-original_used['model_calls']
        record['used'] = task['ledger']['used']
        record['dispatches'] = [{key:item.get(key) for key in ('research_phase', 'status', 'output_protocol',
            'failure_code', 'structured_failure_category', 'structured_output_diagnostic',
            'provider_completion_status', 'finish_reason', 'response_body_complete', 'usage')}
            for item in task['ledger']['dispatches'] if item['kind']=='model_calls'][
                original_used['model_calls']:]
        record['original_task_unchanged'] = retained.read_bytes()==original
        record['production_unchanged'] = production_digest()==before_production
        target = output/'receipt.json'
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        print(json.dumps(dict(receipt=str(target), **{key:record.get(key) for key in (
            'status', 'new_model_calls', 'proposed_count', 'three_required_metrics_verified',
            'original_task_unchanged', 'production_unchanged', 'error_type')}), ensure_ascii=False), flush=True)
    return record
