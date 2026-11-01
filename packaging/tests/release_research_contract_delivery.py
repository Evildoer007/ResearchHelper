"""Batch the current shared contracts through the retained delivery gates."""

import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from zipfile import ZipFile

from tests import release_analysis_execution_delivery as previous
from tests.packaged_runtime_tests import run_packaged_tests

ROOT, RELEASE = previous.ROOT, previous.RELEASE
BASE = ROOT / 'result/acceptance/research-contract-release'
OUT = BASE / 'final'
PRIOR = previous.OUT / 'analysis-execution-delivery.json'
read, digest, save = previous.read, previous.digest, previous.save
production_files = previous.production_files
ADDITIONS = ['tests.' + name for name in ('test_general_definition_answers_offline',
    'test_calculation_input_submission_offline', 'test_numeric_composition_cache_offline',
    'test_document_count_numeric_scope_offline', 'test_query_provenance_offline',
    'test_analysis_partial_delivery_offline', 'test_calculation_parameter_submission_offline',
    'test_etf_definition_metadata_offline')]


def source_gate():
    candidates = [(path, read(path)) for path in BASE.glob('regression-*/runtime.json')]
    candidates = [(path, record) for path, record in candidates
        if record['tests_run'] == len(record['unique_test_ids']) == 1589
        and all(digest(ROOT / name) == value for name, value in record['source_hashes'].items())]
    assert candidates, 'Current source has no complete, matching retained inventory'
    path, record = max(candidates, key=lambda item: item[0].stat().st_mtime)
    assert {item['id'] for item in record['failures']} == {item['id'] for item in read(PRIOR)['legacy_assertion_failures']}
    assert not any(record[name] for name in ('errors', 'skipped', 'external_attempts'))
    assert record['sources_unchanged_during_run'] and record['frozen_live_runs_unchanged']
    host = read(ROOT / 'result/acceptance/host-boundary-release/final/host-delivery.json')
    return record | {'receipt': str(path), 'obsolete_assertion': read(host['runtime_receipt'])['obsolete_assertion']}


def namespace():
    value = previous.namespace()
    value.update(BASE=BASE, OUT=OUT, PUBLISHED=BASE / 'published', PRIOR=PRIOR, regression=source_gate)
    return value


def build():
    source_gate()
    assert not (OUT / 'build.json').exists(), 'Do not replace an existing build receipt'
    save(OUT / 'production-before.json', production_files())
    return namespace()['build']()


def package_checks():
    source_gate()
    original_driver = previous.previous.package_reader.driver

    def reader_four():
        value = original_driver()
        value['VERSION_REPLACEMENTS'][previous.previous.retained.review.REVISION_TEST] = (
            'tests.test_pdf_reader_revision_4_offline.PDFReaderRevision4Tests.'
            'test_reader_revision_changes_no_frozen_content_template')
        return value

    with patch.object(previous.previous.package_reader, 'driver', reader_four):
        baseline = namespace()['package_checks']()
    assert baseline['app_test_executions'] == baseline['skill_test_executions'] == 459
    modules = ['tests.test_api_value_reference_offline', 'tests.test_analysis_execution_binding_offline', *ADDITIONS]
    with tempfile.TemporaryDirectory(prefix='contracts-package-', dir=BASE) as folder:
        with ZipFile(RELEASE / 'ResearchHelper-Skill-0.10.0.zip') as archive:
            assert all(not Path(name).is_absolute() and '..' not in Path(name).parts for name in archive.namelist())
            archive.extractall(folder)
        backend = RELEASE / 'ResearchHelper.app/Contents/Resources/backend/researchhelper-server/researchhelper-server'
        contexts = {kind: run_packaged_tests(kind, location, ROOT, BASE, modules, 95)
                    for kind, location in (('skill', Path(folder) / 'researchhelper'), ('app', backend))}
    result = read(BASE / 'published/document-delivery-package.json') | {
        'batch': 'research-contract-release', 'current_contract_contexts': contexts,
        'retained_executions_per_kind': 459, 'app_test_executions': 554, 'skill_test_executions': 554,
        'release_helper_sha256': digest(__file__), 'helper_counted_in_source_inventory': True}
    save(BASE / 'published/research-contract-package.json', result)
    return {'status': 'passed', 'app_test_executions': 554, 'skill_test_executions': 554}


def publish():
    source_gate()
    value = namespace()['namespace']()
    value.update(BASE=BASE, OUT=OUT, PUBLISHED=BASE / 'published', PRIOR=PRIOR,
                 COUNT=1589, ADDED=345, regression=source_gate)
    package = read(BASE / 'published/research-contract-package.json')
    assert package['status'] == 'passed' and package['app_test_executions'] == package['skill_test_executions'] == 554
    value['publish']()
    result = read(OUT / 'document-delivery-delivery.json') | {
        'batch': 'research-contract-release', 'packaged_checks': package,
        'release_helper_sha256': digest(__file__), 'helper_counted_in_source_inventory': True}
    assert read(OUT / 'production-before.json') == production_files(), 'Production store changed'
    result.update(production_files_unchanged=True, production_file_count=len(production_files()))
    save(OUT / 'research-contract-delivery.json', result)
    return {'status': result['status'], 'source_tests': result['offline_tests_run'],
        'installed': result['installed'], 'dmg_sha256': result['dmg_sha256'], 'skill_sha256': result['skill_sha256']}
