"""Ship reviewed execution binding through existing, unchanged delivery gates.

This late helper reuses the completed source inventory by content hash. It does
not repeat model calls, reset study budgets or weaken package/UI assertions.
"""

from pathlib import Path
import json
import shutil
import subprocess
import tempfile
from unittest.mock import patch
from zipfile import ZipFile

from tests import release_api_value_reference_delivery as previous
from tests.packaged_runtime_tests import run_packaged_tests

ROOT, RELEASE = previous.ROOT, previous.RELEASE
BASE = ROOT / 'result/acceptance/analysis-execution-release'
OUT = BASE / 'final'
PRIOR = previous.OUT / 'api-value-reference-delivery.json'
SOURCE = ROOT / 'result/acceptance/analysis-execution-binding'
read, digest, save = previous.read, previous.digest, previous.save
production_files = previous.production_files


def source_gate():
    gate = read(SOURCE / 'source-vi20o77z/receipt.json')
    assert gate['status'] == 'passed_with_retained_assertion_failures'
    candidates = [(p, read(p)) for p in SOURCE.glob('regression-*/runtime.json')]
    candidates = [(p, r) for p, r in candidates if r['tests_run'] == 1518
                  and all(digest(ROOT / name) == value for name, value in r['source_hashes'].items())]
    assert candidates, 'Reviewed source no longer matches the completed inventory'
    path, runtime = max(candidates, key=lambda item: item[0].stat().st_mtime)
    assert len(runtime['unique_test_ids']) == 1518
    assert {f['id'] for f in runtime['failures']} == {f['id'] for f in read(PRIOR)['legacy_assertion_failures']}
    assert not any(runtime[k] for k in ('errors', 'skipped', 'external_attempts'))
    assert runtime['sources_unchanged_during_run'] and runtime['frozen_live_runs_unchanged']
    assert all(digest(p) == value for p, value in gate['protected_live_files'].items())
    host = read(ROOT / 'result/acceptance/host-boundary-release/final/host-delivery.json')
    return runtime | {'receipt': str(path), 'obsolete_assertion': read(host['runtime_receipt'])['obsolete_assertion']}


def namespace():
    value = previous.retained.driver()
    value.update(BASE=BASE, OUT=OUT, PUBLISHED=BASE / 'published', PRIOR=PRIOR, regression=source_gate)
    return value


def build():
    source_gate()
    assert not (OUT / 'build.json').exists(), 'Keep completed build evidence'
    save(OUT / 'production-before.json', production_files())
    return namespace()['build']()


def package_checks():
    source_gate()
    original_driver = previous.package_reader.driver

    def reader_four():
        value = original_driver()
        value['VERSION_REPLACEMENTS'][previous.retained.review.REVISION_TEST] = (
            'tests.test_pdf_reader_revision_4_offline.PDFReaderRevision4Tests.'
            'test_reader_revision_changes_no_frozen_content_template')
        return value

    with patch.object(previous.package_reader, 'driver', reader_four):
        baseline = namespace()['package_checks']()
    assert baseline['app_test_executions'] == baseline['skill_test_executions'] == 459
    modules = ['tests.test_api_value_reference_offline', 'tests.test_analysis_execution_binding_offline']
    with tempfile.TemporaryDirectory(prefix='execution-package-', dir=BASE) as folder:
        with ZipFile(RELEASE / 'ResearchHelper-Skill-0.10.0.zip') as archive:
            assert all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in archive.namelist())
            archive.extractall(folder)
        backend = RELEASE / 'ResearchHelper.app/Contents/Resources/backend/researchhelper-server/researchhelper-server'
        contexts = {kind: run_packaged_tests(kind, location, ROOT, BASE, modules, 33)
                    for kind, location in (('skill', Path(folder) / 'researchhelper'), ('app', backend))}
    result = read(BASE / 'published/document-delivery-package.json') | {
        'batch': 'analysis-execution-release', 'new_execution_contexts': contexts,
        'retained_executions_per_kind': 459, 'app_test_executions': 492, 'skill_test_executions': 492,
        'late_delivery_helper_sha256': digest(__file__), 'late_helper_counted_in_source_inventory': False}
    save(BASE / 'published/analysis-execution-package.json', result)
    return {'status': 'passed', 'app_test_executions': 492, 'skill_test_executions': 492}


def resume_package_checks():
    """Retain the interrupted protection gate, then run with no parallel writers.

    The legacy gate protects all acceptance JSON outside its output. Concurrent
    browser receipt creation legitimately invalidates that gate; do not exclude
    those receipts or weaken the assertion. Archive this attempt and rerun alone.
    """
    source_gate()
    assert not (BASE / 'published/analysis-execution-package.json').exists()
    failed = Path(tempfile.mkdtemp(prefix='package-protection-failed-', dir=BASE))
    for name in ('published', 'package-reader4-execution.log'):
        path = BASE / name
        if path.exists():
            shutil.move(str(path), str(failed / name))
    save(failed / 'receipt.json', {
        'status': 'failed', 'reason': 'Concurrent browser receipts changed the retained all-JSON protection set',
        'assertion_unchanged': True, 'production_unchanged': read(OUT / 'production-before.json') == production_files(),
        'retry_scope': 'Existing built artifacts, sequential package verification; no rebuild or external calls'})
    return package_checks()


def publish():
    source_gate()
    value = namespace()['namespace']()
    value.update(BASE=BASE, OUT=OUT, PUBLISHED=BASE / 'published', PRIOR=PRIOR,
                 COUNT=1518, ADDED=274, regression=source_gate)
    package = read(BASE / 'published/analysis-execution-package.json')
    assert package['status'] == 'passed' and package['app_test_executions'] == package['skill_test_executions'] == 492
    value['publish']()
    result = read(OUT / 'document-delivery-delivery.json') | {
        'batch': 'analysis-execution-release', 'packaged_checks': package,
        'late_delivery_helper_sha256': digest(__file__), 'late_helper_counted_in_source_inventory': False}
    before = read(OUT / 'production-before.json')
    assert before == production_files(), 'Production store changed during delivery'
    result.update(production_files_unchanged=True, production_file_count=len(before))
    save(OUT / 'analysis-execution-delivery.json', result)
    return {'status': result['status'], 'source_tests': result['offline_tests_run'],
            'installed': result['installed'], 'dmg_sha256': result['dmg_sha256'], 'skill_sha256': result['skill_sha256']}


def installed_documents():
    """Check retained public spans using the actual read-only installed Skill.

    Raw material extraction stays in local evidence files, never in terminal
    output. The inherited OS sandbox denies external network and production,
    OptionHelper and DataFetcher access. This is not autonomous research.
    """
    from tests.rebuild_sdk_wire_acceptance import LOCAL_NETWORK_SANDBOX
    assert (OUT / 'analysis-execution-delivery.json').is_file()
    base = BASE / 'installed-six-public-documents'
    base.mkdir(parents=True, exist_ok=True)
    assert not (base / 'completed.json').exists()
    program = '''
import importlib.util,json
from pathlib import Path
path=Path.cwd()/'core/tests/review_six_public_documents.py'
spec=importlib.util.spec_from_file_location('installed_document_review',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.BASE=Path.cwd()/'result/acceptance/analysis-execution-release/installed-six-public-documents'
print(json.dumps(module.run(),ensure_ascii=False))
'''
    profile = LOCAL_NETWORK_SANDBOX
    for path in (ROOT.parent / 'OptionHelper', ROOT.parent / 'DataFetcher',
                 Path('/Users/haoranxu/Library/Application Support/ResearchHelper')):
        profile += '(deny file-read* (subpath ' + json.dumps(str(path)) + '))'
        profile += '(deny file-write* (subpath ' + json.dumps(str(path)) + '))'
    profile += '(deny file-write* (subpath ' + json.dumps(str(ROOT / '.skills/researchhelper')) + '))'
    with (base / 'execution.log').open('x') as log:
        completed = subprocess.run(['/usr/bin/sandbox-exec', '-p', profile,
            '/opt/anaconda3/envs/MachineLearning/bin/python', '-I', '-B', '-c', program],
            cwd=ROOT, stdout=log, stderr=log, timeout=180)
    assert completed.returncode == 0, 'Document gate failed; inspect local log without dumping original contents'
    pointer = read(base / 'completed.json')
    receipt = read(pointer['receipt'])
    assert receipt['status'] == 'passed' and receipt['distribution_kind'] == 'installed'
    return {'status': 'passed', 'cases': len(receipt['cases']),
            'public_excerpts': sum(item['public_excerpts'] for item in receipt['cases']),
            'receipt': pointer['receipt'], 'real_external_calls': 0}


def readback():
    """Verify delivered bytes and current receipts, keeping acceptance layers separate."""
    from tests.review_api_value_reference_readback import verify_files, latest
    current_path = OUT / 'analysis-execution-delivery.json'
    current = read(current_path)
    runtime = source_gate()
    manifest = read(RELEASE / 'release-manifest.json')
    assert current['runtime_receipt_sha256'] == digest(current['runtime_receipt'])
    assert current['source_hashes'] == manifest['source_hashes']
    counts = {
        'source_gate_input_files': verify_files(ROOT, runtime['source_hashes']),
        'shipping_source_files': verify_files(ROOT, manifest['source_hashes']),
        'installed_app_files': verify_files('/Applications/ResearchHelper.app', read(RELEASE / 'app-manifest.json')['files']),
        'installed_skill_files': verify_files(ROOT / '.skills/researchhelper', read(RELEASE / 'researchhelper/manifest.json')['files']),
        'desktop_demo_files': verify_files('/Users/haoranxu/Desktop/demo/ResearchHelper demo', read(RELEASE / 'demo/manifest.json')['files']),
    }
    for artifact in manifest['artifacts'].values():
        assert digest(artifact['path']) == artifact['sha256']
    for kind, key in (('dmg', 'dmg_sha256'), ('skill', 'skill_sha256')):
        assert current[key] == manifest['artifacts'][kind]['sha256']
    for name in ('engine.py', 'reasoning.py', 'evidence.py'):
        assert digest(ROOT / 'core/src/researchhelper' / name) == digest(ROOT / '.skills/researchhelper/researchhelper' / name)
    package = read(BASE / 'published/analysis-execution-package.json')
    assert package['status'] == 'passed' and package['app_test_executions'] == package['skill_test_executions'] == 492
    assert all(context['tests_run'] == 33 for context in package['new_execution_contexts'].values())
    assert current['browser_rerun_checks'] == 152
    gap_path, gap = latest(SOURCE, 'browser-*/receipt.json')
    assert len(gap['checks']) == 8 and gap['backend_stopped'] and gap['browser_stopped']
    assert not any(gap[k] for k in ('errors', 'external_attempts', 'writes'))
    pointer = read(BASE / 'installed-six-public-documents/completed.json')
    corpus = read(pointer['receipt'])
    assert corpus['status'] == 'passed' and corpus['distribution_kind'] == 'installed'
    assert len(corpus['cases']) == 6 and sum(c['public_excerpts'] for c in corpus['cases']) == 32
    assert corpus['installation_unchanged'] and corpus['originals_unchanged']
    for case in corpus['cases']:
        assert case['restart_export_identical'] and case['restart_ledger_identical']
        assert digest(case['report']) == case['report_sha256']
        assert digest(Path(case['report']).with_suffix('.json')) == case['content_sha256']
    report_path, report = latest(Path(pointer['directory']), 'browser-*/receipt.json')
    demo_path, demo = latest(BASE, 'offline-demo-*/receipt.json')
    assert len(report['checks']) == 24 and len(demo['checks']) == 2
    for browser in (report, demo):
        assert not browser['errors'] and not browser['external_attempts'] and browser['browser_stopped']
    frozen_path, frozen = latest(BASE / 'frozen-worker-before-install', 'review-*/isolated-transport-package.json')
    assert len(frozen['checks']) == 5 and frozen['dmg_sha256'] == current['dmg_sha256']
    assert frozen['current_release_receipt_sha256'] == digest(OUT / 'candidate-disk.json')
    assert all(frozen[k] for k in ('backend_stopped', 'fixture_server_stopped', 'mount_detached', 'temporary_stage_removed'))
    assert not frozen['real_model_called'] and not frozen['real_provider_called']
    production = read(OUT / 'production-before.json')
    assert production == production_files()
    retained = read(ROOT / 'result/acceptance/api-value-reference/retained-h6fa36pc/receipt.json')
    assert all(digest(p) == value for p, value in retained['protected_files'].items())
    assert read(BASE / 'ui-detector.json') == []
    result = {'status': 'passed', 'counts': counts, 'source_tests': 1518, 'source_passed': 1509,
        'retained_assertion_failures': 9, 'app_package_executions': 492, 'skill_package_executions': 492,
        'browser_checks': 152, 'additional_gap_navigation_checks': 8,
        'gap_receipt': str(gap_path), 'corpus_receipt': pointer['receipt'], 'corpus_sha256': digest(pointer['receipt']),
        'corpus_browser_receipt': str(report_path), 'desktop_demo_receipt': str(demo_path),
        'preinstall_frozen_worker_receipt': str(frozen_path), 'production_unchanged': True,
        'production_file_count': len(production), 'frozen_research_unchanged': True,
        'dmg_sha256': current['dmg_sha256'], 'skill_sha256': current['skill_sha256'],
        'delivery_receipt_sha256': digest(current_path), 'late_delivery_helper_sha256': digest(__file__),
        'late_helpers_counted_in_source_inventory': False, 'real_model_calls': 0, 'real_supplier_calls': 0,
        'foreground_app_launched': False, 'native_ui_verified': False, 'system_print_verified': False,
        'autonomous_research_verified': False, 'independent_financial_quality_verified': False,
        'full_harness_complete': False}
    save(OUT / 'completion-readback.json', result)
    return result
