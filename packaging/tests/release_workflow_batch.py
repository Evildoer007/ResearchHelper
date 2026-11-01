"""Batch-build only after retained regression; preserve prior deliveries and receipts."""

from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch
from zipfile import ZipFile

from tests import release_host_boundary as previous
from tests import rebuild_market_dispatch_delivery as dispatch
from tests import rebuild_publication_guard_final_delivery as final
from tests.packaged_runtime_tests import run_packaged_tests

ROOT, RELEASE = dispatch.ROOT, dispatch.RELEASE
BASE = ROOT / 'result/acceptance/workflow-release'
OUT, PUBLISHED = BASE / 'final', BASE / 'published'
RUNTIME = BASE / 'regression-mzra02st/runtime.json'
PRIOR = ROOT / 'result/acceptance/host-boundary-release/final/host-delivery.json'
ADDITIONS = ['tests.test_bank_knowledge_workflow_offline', 'tests.test_index_change_workflow_offline',
             'tests.test_observed_price_nature_offline', 'tests.test_task_material_binding_offline',
             'tests.test_numeric_checkpoint_structure_offline', 'tests.test_current_catalog_boundaries_offline']


def regression():
    record = dispatch.read(RUNTIME)
    final.current(record)
    expected = {
        'tests.test_management_custody_fee_offline.FeeCatalogCompatibilityTests.test_catalog_extension_preserves_original_app_boundary_assertions': '18 != 15',
        'tests.test_management_custody_fee_offline.FeeCatalogCompatibilityTests.test_earnings_dependencies_with_fifteen_catalog_entries': '18 != 15',
        'tests.test_research_knowledge_domains_offline.ResearchKnowledgeDomainTests.test_necessary_methods_not_dropped_and_sources_are_explicit': '7 != 4',
        "tests.test_model_json_format_offline.ModelJSONFormatTests.test_every_phase_has_parseable_example_with_exact_fields (phase='analyze')": "'answers': []",
    }
    assert record['tests_run'] == len(record['unique_test_ids']) == 1231
    assert len(record['added_test_ids']) == 30 and len(record['retained_prior_test_ids']) == 1201
    assert {item['id'] for item in record['failures']} == set(expected)
    assert all(expected[item['id']] in item['traceback'] for item in record['failures'])
    assert not any(record[key] for key in ('errors', 'skipped', 'external_attempts'))
    assert record['sources_unchanged_during_run'] and record['frozen_live_runs_unchanged']
    assert not record['old_test_files_modified'] and not record['all_retained_tests_passed']
    prior = dispatch.read(dispatch.read(PRIOR)['runtime_receipt'])
    return record | {'receipt': str(RUNTIME), 'obsolete_assertion': prior['obsolete_assertion']}


def build():
    regression()
    prior = dispatch.read(PRIOR)
    expected = {'ResearchHelper-0.10.0-arm64.dmg': prior['dmg_sha256'],
                'ResearchHelper-Skill-0.10.0.zip': prior['skill_sha256']}
    assert all(dispatch.digest(RELEASE / name) == value for name, value in expected.items())
    backup = ROOT / 'result/release-backups/20261007-workflow-batch'
    assert not backup.exists(), 'Do not replace an existing recovery copy'
    backup.mkdir(parents=True)
    for name in (*expected, 'release-manifest.json', 'app-manifest.json'):
        shutil.copyfile(RELEASE / name, backup / name)
        assert dispatch.digest(backup / name) == dispatch.digest(RELEASE / name)
    shutil.copytree(ROOT / '.skills/researchhelper', backup / 'installed-project-skill',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    OUT.mkdir(parents=True, exist_ok=True)
    record = {'status': 'building', 'backup': str(backup), 'app_installed': False, 'demo_synced': False}
    path = OUT / 'build.json'
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    try:
        artifacts = dispatch.build()
        record.update(status='built_candidate_not_installed', artifacts=artifacts, project_skill_generated=True,
                      source_hashes=dispatch.read(RELEASE / 'release-manifest.json')['source_hashes'])
    except BaseException:
        record.update(status='build_failed', project_skill_may_have_been_generated=True)
        raise
    finally:
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return record


def package_checks():
    regression()
    PUBLISHED.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        for name, value in (('BASE', BASE), ('OUT', OUT), ('PUBLISHED', PUBLISHED), ('regression', regression)):
            stack.enter_context(patch.object(previous, name, value))
        retained = previous.package_checks()
    assert retained['skill_test_executions'] == retained['app_test_executions'] == 184
    with tempfile.TemporaryDirectory(prefix='batch-skill-', dir=BASE) as project:
        installation = Path(project) / 'installation'
        with ZipFile(RELEASE / 'ResearchHelper-Skill-0.10.0.zip') as archive:
            assert all(not Path(name).is_absolute() and '..' not in Path(name).parts for name in archive.namelist())
            archive.extractall(installation)
        backend = RELEASE / 'ResearchHelper.app/Contents/Resources/backend/researchhelper-server/researchhelper-server'
        contexts = {kind: run_packaged_tests(kind, location, ROOT, BASE, ADDITIONS, 30)
            for kind, location in (('skill', installation / 'researchhelper'), ('app', backend))}
    record = {'status': 'passed', 'retained': retained, 'new_contexts': contexts,
              'skill_test_executions': 214, 'app_test_executions': 214,
              'source_hashes': dispatch.read(RELEASE / 'release-manifest.json')['source_hashes'],
              'helper_sha256': dispatch.digest(__file__), 'full_harness_complete': False}
    (PUBLISHED / 'batch-package.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return record
