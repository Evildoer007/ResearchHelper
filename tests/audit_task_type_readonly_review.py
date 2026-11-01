"""Read-only task-type inspection with independently retained findings.

No study is resumed, no answer is supplied to a research run, and no installed
delivery is updated. Existing assertions are executed unchanged in subprocesses
so a broken fixture cannot strand a server thread in this audit process.
"""
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch

from tests import ROOT
from researchhelper import argument_support
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, METHOD_REGISTRY
from researchhelper.economic_sources import ECONOMIC_REQUEST_REGISTRY, declared_economic_indicators
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import (FIELD_REGISTRY, acquisition_channels, data_request_contract,
    validate_calculation_context_basis)
from researchhelper.market_series import SERIES_REQUEST_REGISTRY
from researchhelper.source_reader import validate_read_specs, reader_version_for
from tests.audit_task_type_semantics import SCENARIOS
from tests.audit_current_claim_support import CASES, RETAINED, probe
from tests.review_live_financial_report import run as review_financial_values

MODULES = (
    'test_object_series_routing', 'test_economic_source_offline',
    'test_index_change_workflow_offline', 'test_financial_methods_offline',
    'test_financial_amount_units_offline', 'test_management_custody_fee_offline',
    'test_fee_source_units_offline', 'test_current_original_availability',
    'test_current_original_financial_period', 'test_financial_statement_scope',
    'test_raw_query_provenance', 'test_research_coverage_offline',
    'test_thesis_question_context', 'test_argument_support_review',
    'test_argument_support_workflow', 'test_dependency_synthesis',
    'test_reading_reference_protocol', 'test_fee_original_selection',
    'test_completed_planning_format_recovery',
)
STUDIES = dict(stock='one-sentence-research-f6e_kjx1',
    etf='one-sentence-research-iohya1u7', planning_failure='one-sentence-research-vm0gp5kr')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contract_checks(output):
    program = '''
import io,json,socket,sys,unittest
from unittest.mock import patch
from tests import ROOT
original_connect=socket.socket.connect
def loopback_only(connection,address):
    if isinstance(address,tuple) and address[0] in ('127.0.0.1','localhost','::1'):
        return original_connect(connection,address)
    raise AssertionError('Read-only audit blocks external networking')
with patch.object(socket.socket,'connect',loopback_only):
    stream=io.StringIO()
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromName('tests.'+sys.argv[1]))
print(stream.getvalue())
print('RH_AUDIT_RESULT='+json.dumps(dict(run=result.testsRun,
    successful=result.wasSuccessful(), failures=[test.id() for test,_ in result.failures],
    errors=[test.id() for test,_ in result.errors],skipped=len(result.skipped))))
'''
    results = []
    for module in MODULES:
        try:
            completed = subprocess.run([sys.executable, '-B', '-c', program, module],
                cwd=ROOT, capture_output=True, text=True, timeout=60,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
            text = completed.stdout + completed.stderr
            line = next((line for line in completed.stdout.splitlines()
                if line.startswith('RH_AUDIT_RESULT=')), None)
            result = json.loads(line.split('=', 1)[1]) if line else dict(
                run=0, successful=False, failures=[], errors=['fixture_process_failed'], skipped=0)
            result['process_exit_code'] = completed.returncode
        except subprocess.TimeoutExpired as error:
            def decoded(value):
                return value.decode(errors='replace') if isinstance(value, bytes) else value or ''
            text = decoded(error.stdout) + decoded(error.stderr)
            result = dict(run=None, successful=False, failures=[],
                errors=['audit_fixture_timeout'], skipped=None, process_terminated=True)
        (output/(module+'.log')).write_text(text)
        results.append(dict(module=module, **result))
        (output/'contract-progress.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
        print(json.dumps(dict(module=module, run=result['run'],
            failures=len(result['failures']), errors=len(result['errors'])), ensure_ascii=False), flush=True)
    return dict(modules=results, run=sum(item['run'] or 0 for item in results),
        failures=sum(len(item['failures']) for item in results),
        errors=sum(len(item['errors']) for item in results),
        skipped=sum(item['skipped'] or 0 for item in results),
        all_successful=all(item['successful'] for item in results),
        unchanged_old_assertions=True, synthetic_verdicts_are_not_semantic_acceptance=True)


def entry_probes(output):
    rows = []
    with tempfile.TemporaryDirectory(prefix='isolated-', dir=output) as temporary:
        agent = ResearchAgent(ResearchArchive(Path(temporary)/'store'), ROOT,
            offline=True, require_argument_support=True)
        try:
            for entry, question in SCENARIOS.items():
                explicit = agent.create(question, entry=entry)
                chat = agent.create(question)
                contract = data_request_contract(chat)
                rows.append(dict(entry=entry, question=question,
                    explicit_review_required=argument_support.enabled(explicit),
                    default_chat_review_required=argument_support.enabled(chat),
                    declared_codes=contract['declared_codes'],
                    explicit_economic_ids=declared_economic_indicators(chat),
                    tools=[item['source']+'.'+item['operation'] for item in contract['tools']],
                    series_profiles=[dict(family=profile['family'], codes=profile['available_codes'],
                        fields=list(profile['field_parameters'])) for tool in contract['tools']
                        if tool['operation']=='date_sequence' for profile in tool['family_shapes']],
                    new_task_reader_version=reader_version_for(chat),
                    fee_candidate_encoding=chat.get('fee_candidate_encoding'),
                    real_research_executed=False))
        finally:
            for worker in agent.close_runtime():
                worker.join(timeout=5)
                if worker.is_alive():
                    raise RuntimeError('Owned audit worker did not stop')
    assert len(rows)==8 and all(item['explicit_review_required'] and
        item['default_chat_review_required'] for item in rows)
    return rows


def study_summary(folder):
    task = json.loads((folder/'task.json').read_text())
    receipt = json.loads((folder/'receipt.json').read_text())
    return dict(receipt=str(folder/'receipt.json'), candidate=receipt.get('candidate'),
        status=task['status'], used=task['ledger']['used'], evidence=len(task['evidence']),
        calculations=len(task['calculations']), report_completed=receipt.get('ordinary_chat_report_completed'),
        support_reviews=len(task.get('argument_support_reviews', [])),
        gaps=task['gaps'], study_restarted=False,
        model_phases=[dict(phase=item.get('research_phase'),status=item.get('status'),
            failure_code=item.get('failure_code')) for item in task['ledger']['dispatches']
            if item['kind']=='model_calls'])


def inspect_etf_proposals(task, output):
    """Reproduce failures on copies; source presence never becomes approval."""
    from pypdf import PdfReader
    bundle = task['reasoning_state']['reading']['bundle']
    sources = {item['id']: item for item in task['source_records']}
    originals = {}; checks = []
    for original_spec in task['host_result']['evidence_specs']:
        spec = copy.deepcopy(original_spec)
        spec.update(task_id=task['id'], research_version=task['version'])
        source = sources[spec['artifact_id']]
        if source['id'] not in originals:
            material=source['snapshot']['material']
            payload=base64.b64decode(material['original_base64'], validate=True)
            assert hashlib.sha256(payload).hexdigest()==material['hash']
            originals[source['id']]=dict(payload=payload,
                pages=[page.extract_text() for page in PdfReader(io.BytesIO(payload)).pages],
                original_hash=material['hash'])
        original=originals[source['id']]
        page_number=int(spec['record_locator']['part'].removeprefix('第').removesuffix('页'))
        text=original['pages'][page_number-1]
        gate_results={}
        for name, validator in (
                ('read_anchor',lambda:validate_read_specs(task,[spec],bundle)),
                ('calculation_context',lambda:validate_calculation_context_basis(spec,task['source_records']))):
            try:
                validator()
                gate_results[name]=dict(accepted=True)
            except ContractError as error:
                gate_results[name]=dict(accepted=False,error=str(error))
        checks.append(dict(id=spec['id'], object=spec['object'],
            original_sha256=original['original_hash'], selected_page=page_number,
            raw_token_on_selected_pdf_page=spec['raw_token'] in text,
            observation_date=spec.get('observation_date'), context=spec['calculation_context'],
            gates=gate_results, evidence_adopted=False))
    rendered=[]
    # Full relevant pages establish layout independently of model line indices.
    for source_id, original in originals.items():
        code=next(item['object'].split('.')[0] for item in task['host_result']['evidence_specs']
            if item['artifact_id']==source_id)
        selected={item['selected_page'] for item in checks if item['object'].startswith(code)}
        if code=='510500':selected.add(29)  # Retained rejected selection was page28.
        for number in sorted(selected):
            prefix=output/(code+'-page-'+str(number))
            rendered_page=subprocess.run(['/opt/homebrew/bin/pdftoppm','-f',str(number),'-l',str(number),
                '-singlefile','-scale-to','1600','-png','-',str(prefix)],
                input=original['payload'],capture_output=True,timeout=30)
            if rendered_page.returncode:
                raise RuntimeError('Retained original could not be rendered')
            rendered.append(str(prefix.with_suffix('.png')))
    assert len(checks)==4 and all(not item['gates']['calculation_context']['accepted'] for item in checks)
    return dict(proposals=checks,original_hashes_verified=True,rendered_pages=rendered,
        source_presence_is_approval=False,manual_repair_applied=False,
        finance_calculation_executed=False)


def run():
    output=Path(tempfile.mkdtemp(prefix='task-type-readonly-review-', dir=ROOT/'result/acceptance'))
    code_files=sorted({*ROOT.glob('core/src/researchhelper/*.py'),
        *ROOT.glob('modules/*/src/*.py')})
    protected=code_files+[RETAINED]
    for name in STUDIES.values():
        protected += [ROOT/'result/acceptance'/name/file for file in
            ('task.json','receipt.json','report.html','report.json')
            if (ROOT/'result/acceptance'/name/file).is_file()]
    before={str(path):digest(path) for path in protected}
    record=dict(status='running',real_model_calls=0,real_supplier_calls=0,
        business_code_modified=False,production_store_accessed=False,
        installed_app_updated=False,skill_updated=False,demo_updated=False,
        native_window_opened=False,all_task_types_accepted=False,
        current_model_semantic_quality_accepted=False,
        harness_revision=2,loopback_allowed_for_owned_test_fixtures=True,
        prior_interrupted_audit=str(ROOT/'result/acceptance/task-type-readonly-review-9naw5rph/receipt.json'),
        audit_driver_sha256=digest(Path(__file__)))
    try:
        record['regressions']=contract_checks(output)
        with patch.object(socket.socket,'connect',side_effect=AssertionError('Audit forbids networking')):
            record['entry_probes']=entry_probes(output)
            record['retained_real_studies']={key:study_summary(ROOT/'result/acceptance'/folder)
                for key,folder in STUDIES.items()}
            record['stock_original_recomputation']=review_financial_values(
                ROOT/'result/acceptance'/STUDIES['stock'],require_report=False)
            task=json.loads((ROOT/'result/acceptance'/STUDIES['etf']/'task.json').read_text())
            record['etf_original_and_gate_checks']=inspect_etf_proposals(task,output)
            original=json.loads(RETAINED.read_text())
            original['argument_support_contract']=argument_support.VERSION
            original['argument_support_reviews']=[]
            record['unreviewed_claim_controls']=[dict(case=name,
                probe_is_synthetic=True,**probe(original,title,meaning,limitation))
                for name,title,meaning,limitation,_ in CASES]
            assert all(not item['accepted'] for item in record['unreviewed_claim_controls'])
        record['acquisition_channels']=acquisition_channels()
        record['approved_numeric_fields']={key:list(value['fields']) for key,value in FIELD_REGISTRY.items()}
        record['raw_series_contract']=copy.deepcopy(SERIES_REQUEST_REGISTRY)
        record['raw_economic_contract']=copy.deepcopy(ECONOMIC_REQUEST_REGISTRY)
        record['registered_methods']={key:value['title'] for key,value in METHOD_REGISTRY.items()}
        candidate_path=ROOT/'result/build-candidates/20261016-124220-758210/candidate.json'
        candidate=json.loads(candidate_path.read_text())
        changed=[name for name,value in candidate['source_hashes'].items()
            if not (ROOT/name).is_file() or digest(ROOT/name)!=value]
        added=[str(path.relative_to(ROOT)) for path in code_files
            if str(path.relative_to(ROOT)) not in candidate['source_hashes']]
        record['source_delivery_boundary']=dict(latest_candidate=str(candidate_path),
            changed_since_candidate=changed,unpackaged_module_sources=added,
            source_matches_candidate=not changed and not added,
            installed_candidate_record=str(ROOT/'result/maintenance/fee-original-delivery-q1u2_qxz/receipt.json'),
            tested_here='development source and retained runs, not current installed App')
        assert record['stock_original_recomputation']['status']=='math_only_passed'
        record['status']='audit_complete_with_findings'
    except BaseException as error:
        record.update(status='audit_failed',error_type=type(error).__name__,error=str(error)[:600])
        raise
    finally:
        record['protected_sha256']=before
        record['protected_files_unchanged']=all(digest(Path(path))==value for path,value in before.items())
        if not record['protected_files_unchanged']:
            record.update(status='audit_failed',error='Protected files changed during read-only audit')
        target=output/'receipt.json'
        target.write_text(json.dumps(record,ensure_ascii=False,indent=2))
        print(json.dumps(dict(status=record['status'],receipt=str(target),
            protected_files_unchanged=record['protected_files_unchanged']),ensure_ascii=False),flush=True)
    return record
