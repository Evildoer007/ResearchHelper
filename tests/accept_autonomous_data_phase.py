"""One question-to-data source-kernel check, intentionally before analysis.

No supplied code, plan, material, extraction or answer. The same engine/model
adapter as App runs through the evidence node. This test subclass cancels at
the following node using the normal cancellation API; it never supplies a
success answer, completes a report, or represents installed-App acceptance.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from itertools import combinations

from tests import ROOT
from tests.accept_one_sentence_research import PRODUCTION, production_digest
from tests.accept_source_question_bound_pmi_chat import source_inventory
from tests.probe_data_acquisition_current import material_inventory
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import (ContractError, METHOD_REGISTRY, FINANCIAL_INPUT_METRICS,
    calculation_input_applies, calculation_input_units_available, validate_calculation_context)
from researchhelper.engine import ResearchAgent
from researchhelper.model import IsolatedModelService, ModelSettings
from researchhelper.settings import DataSettings

CASE = 'autonomous-stock-data-phase-20261017-v1'
QUESTION = '研究贵州茅台2026年半年报的盈利质量，核对营业收入、归母净利润和经营现金流。'


def method_input_inventory(task):
    """Negative readiness check only, never a calculation/source approval."""
    result = []
    for question in task.get('questions', []):
        for method in question.get('method_needs', []):
            if method not in METHOD_REGISTRY:
                continue
            candidates = [item for item in task.get('evidence', [])
                if type(item.get('value')) in (int, float)
                and item.get('source_check') == '通过' and item.get('applicability_check') == '通过'
                and calculation_input_applies(task, item, question['id'], method)]
            required = FINANCIAL_INPUT_METRICS.get(method)
            compatible_roles = None
            if required:
                fields = [key for key in METHOD_REGISTRY[method]['binding_context'] if key != 'metric']
                groups = {}
                for item in candidates:
                    context = item.get('calculation_context', {})
                    if any(not context.get(key) for key in fields):
                        continue
                    key = tuple(context[field] for field in fields) + (item['unit'],)
                    groups.setdefault(key,set()).add(context.get('metric'))
                compatible_roles = any(set(required.values()).issubset(metrics) for metrics in groups.values())
            elif method == 'fee_difference_v2':
                # Only inspect source-checked candidates, never write a
                # binding, infer annualization or execute a calculation.
                compatible_roles = False
                for first, second in combinations(candidates, 2):
                    context = first.get('calculation_context') or {}
                    parameters = dict(unit='%/年', period=context.get('period'), fee_kind=context.get('metric'))
                    try:
                        validate_calculation_context(method,dict(fee_a=first,fee_b=second),parameters)
                    except ContractError:
                        continue
                    compatible_roles = True
                    break
            result.append(dict(question_id=question['id'], method=method,
                numeric_candidates=len(candidates),
                units_available=calculation_input_units_available(method, [item['unit'] for item in candidates]),
                compatible_financial_roles_available=compatible_roles,
                financial_calculation_approved=False))
    return result


class DataPhaseAgent(ResearchAgent):
    def _step(self, task, node, host_result):
        if node not in ('identity', 'evidence'):
            self.cancel(task['id'])
            raise ContractError('独立取数验收按约定在数据阶段后结束，未进入计算、分析或报告')
        return super()._step(task, node, host_result)


def run():
    descriptor = os.open(ROOT / 'result/acceptance' / (CASE + '.guard'),
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    output = Path(tempfile.mkdtemp(prefix='autonomous-data-phase-', dir=ROOT / 'result/acceptance'))
    output.chmod(0o700)
    target = output / 'receipt.json'
    before, sources = production_digest(), source_inventory()
    record = dict(status='running', question=QUESTION, scope='autonomous_source_kernel_data_phase',
        supplied_codes=False, supplied_plan=False, supplied_evidence=False, supplied_answers=False,
        browser_used=False, installed_app_accepted=False, strategy_accepted=False,
        report_accepted=False, native_window_opened=False, settings_overridden=False)
    agent, task, credentials = None, None, {}

    def save():
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2))

    save()
    try:
        settings = DataSettings(PRODUCTION)
        saved = settings._configuration()
        reference = saved['ifind']['refresh_token']
        refresh = (settings.secrets.resolve(reference, 'ifind-refresh_token')
                   if 'schema' in saved and reference else settings._credential(reference or ''))
        if not refresh:
            raise ContractError('ResearchHelper未配置独立iFinD凭据')
        credentials = {'ifind': {'refresh_token': refresh}}
        refresh = ''
        model = IsolatedModelService(ModelSettings(PRODUCTION), offline=False)
        agent = DataPhaseAgent(ResearchArchive(output / 'store'), ROOT, offline=False,
            model=model, source_credentials=credentials, isolate_sources=True,
            isolate_materials=True, require_argument_support=True)
        model.journal, model.active = agent._journal, agent._check_run_control
        task = agent.create(QUESTION, research_mode='current')
        record['task_id'] = task['id']
        save()
        task = agent.start(task['id'])
        evidence_node = next(row for row in task['nodes'] if row['id'] == 'evidence')
        phases = [row.get('research_phase') for row in task['ledger']['dispatches']
                  if row['kind'] == 'model_calls']
        record.update(task_status=task['status'], evidence_node_status=evidence_node['status'],
            adopted_evidence=len(task['evidence']), rejected_evidence=len(task.get('rejected_evidence', [])),
            model_phases=phases, gaps=task['gaps'], error=task.get('error'),
            planned_data_requests=task.get('data_need_specs', []),
            sources=[dict(operation=row['operation'], status=row['status'], artifact_id=row['id'],
                          material=material_inventory(row) if row['kind'] in ('material', 'filing') else None)
                     for row in task['source_records']],
            checkpoint_stop_reached=task['status'] == '已取消' and evidence_node['status'] in ('成功', '部分成功'),
            evidence_fields=[dict(field=row.get('field'), object=row.get('object'),
                                  question_ids=row.get('question_ids')) for row in task['evidence']])
        record['method_input_inventory'] = method_input_inventory(task)
        record['pending_method_inputs'] = [item for item in record['method_input_inventory']
            if not item['units_available'] or item['compatible_financial_roles_available'] is False]
        if record['checkpoint_stop_reached'] and task['evidence'] and 'analyze' not in phases:
            record['status'] = ('data_phase_partial' if record['pending_method_inputs'] or task['gaps']
                                or task.get('rejected_evidence') else 'data_phase_sample_passed')
        else:
            record['status'] = 'data_phase_incomplete'
    finally:
        if agent is not None:
            for worker in agent.close_runtime():
                worker.join(timeout=5)
                assert not worker.is_alive()
            if record.get('task_id'):
                task = agent.archive.get(record['task_id'])
                record['ledger'] = task['ledger']
                (output / 'task.json').write_text(json.dumps(task, ensure_ascii=False, indent=2))
            agent.collector._ifind_access_cache = None
            agent.collector._ifind_original_cache.clear()
        for values in credentials.values():
            values.clear()
        credentials.clear()
        record.update(production_unchanged=before == production_digest(),
                      source_unchanged=sources == source_inventory(), owned_runtime_closed=True)
        save()
        print(json.dumps(dict(status=record['status'], receipt=str(target),
                              evidence=record.get('adopted_evidence')), ensure_ascii=False), flush=True)
    assert record['source_unchanged'] and record['production_unchanged']
    return target
