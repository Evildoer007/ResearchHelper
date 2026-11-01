"""Batch release task-context protection and authenticated spawn acceptance.

Preserve old artifacts and receipts. Reuse original package/install validators,
with exact output routing only; no real calls, foreground or production data.
"""
from contextlib import ExitStack
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch
from zipfile import ZipFile

from tests import release_analysis_answer as retained
from tests import review_workflow_release as workflow
from tests.packaged_runtime_tests import run_packaged_tests

ROOT, RELEASE = retained.ROOT, retained.RELEASE
BASE = ROOT / 'result/acceptance/task-context-release'
OUT, PUBLISHED = BASE / 'final', BASE / 'published'
PRIOR = ROOT / 'result/acceptance/task-action-binding-release/final/binding-delivery.json'
MODULES = ['tests.test_task_action_context_offline', 'tests.test_authorized_lpr_resume_limits_offline',
           'tests.test_authenticated_spawn_workflow_offline']
COUNT, ADDED = 1257, 13
read, digest, save, final, dispatch = retained.read, retained.digest, retained.save, retained.final, retained.dispatch


def real_records():
    return {str(path): digest(path) for folder in (
        ROOT / 'result/acceptance/current-authorized-parallel-lpr',
        ROOT / 'result/acceptance/current-authorized-lpr-resume')
        for path in folder.rglob('*') if path.is_file()}


def review():
    before = real_records()
    additions = (*workflow.ADDITIONS, 'tests.test_declared_method_knowledge_offline',
                 'tests.test_analysis_answer_types_offline', *MODULES)
    with patch.object(workflow, 'BASE', BASE), patch.object(workflow, 'ADDITIONS', additions):
        result = workflow.main()
    assert result['tests_run'] == len(result['unique_test_ids']) == COUNT
    assert {item['id'] for item in result['failures']} == {item['id'] for item in read(PRIOR)['legacy_assertion_failures']}
    assert all(any(text in item['traceback'] for text in ('18 != 15', '7 != 4', "'answers': []")) for item in result['failures'])
    assert not any(result[key] for key in ('errors', 'skipped', 'external_attempts'))
    assert result['sources_unchanged_during_run'] and result['frozen_live_runs_unchanged']
    assert real_records() == before
    receipt = max(BASE.glob('regression-*/runtime.json'), key=lambda path: path.stat().st_mtime)
    return save(BASE / 'review.json', {'runtime_receipt': str(receipt), 'runtime_sha256': digest(receipt),
        'protected_real_runs': before, 'tests_run': COUNT, 'tests_passed': COUNT - 4, 'new_real_calls': 0})


def regression():
    reviewed = read(BASE / 'review.json')
    path = Path(reviewed['runtime_receipt'])
    assert digest(path) == reviewed['runtime_sha256']
    value = read(path)
    final.current(value)
    assert value['tests_run'] == len(value['unique_test_ids']) == COUNT
    assert {item['id'] for item in value['failures']} == {item['id'] for item in read(PRIOR)['legacy_assertion_failures']}
    assert not any(value[key] for key in ('errors', 'skipped', 'external_attempts'))
    assert real_records() == reviewed['protected_real_runs']
    older = read(read(ROOT / 'result/acceptance/host-boundary-release/final/host-delivery.json')['runtime_receipt'])
    return value | {'receipt': str(path), 'obsolete_assertion': older['obsolete_assertion']}


def build():
    regression()
    previous = read(PRIOR)
    artifacts = {'ResearchHelper-0.10.0-arm64.dmg': previous['dmg_sha256'],
                 'ResearchHelper-Skill-0.10.0.zip': previous['skill_sha256']}
    assert all(digest(RELEASE / name) == value for name, value in artifacts.items())
    backup = ROOT / 'result/release-backups/20261007-task-context'
    assert not backup.exists(), 'Do not overwrite the recovery copy'
    backup.mkdir(parents=True)
    for name in (*artifacts, 'release-manifest.json', 'app-manifest.json'):
        shutil.copyfile(RELEASE / name, backup / name)
        assert digest(RELEASE / name) == digest(backup / name)
    shutil.copytree(ROOT / '.skills/researchhelper', backup / 'installed-project-skill',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    record = {'status': 'building', 'backup': str(backup), 'app_installed': False, 'demo_synced': False}
    save(OUT / 'build.json', record)
    try:
        record.update(status='built_candidate_not_installed', artifacts=dispatch.build(),
                      project_skill_generated=True, source_hashes=read(RELEASE / 'release-manifest.json')['source_hashes'])
    except BaseException:
        record.update(status='build_failed', project_skill_may_have_been_generated=True)
        raise
    finally:
        save(OUT / 'build.json', record)
    return record


def package_checks():
    regression()
    OUT.mkdir(parents=True, exist_ok=True)
    PUBLISHED.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        for name, value in (('BASE', BASE), ('OUT', OUT), ('PUBLISHED', PUBLISHED), ('regression', regression)):
            stack.enter_context(patch.object(retained, name, value))
        previous = retained.package_checks()
    assert previous['skill_test_executions'] == previous['app_test_executions'] == 227
    with tempfile.TemporaryDirectory(prefix='context-skill-', dir=BASE) as folder:
        installation = Path(folder) / 'installation'
        with ZipFile(RELEASE / 'ResearchHelper-Skill-0.10.0.zip') as archive:
            assert all(not Path(name).is_absolute() and '..' not in Path(name).parts for name in archive.namelist())
            archive.extractall(installation)
        backend = RELEASE / 'ResearchHelper.app/Contents/Resources/backend/researchhelper-server/researchhelper-server'
        contexts = {kind: run_packaged_tests(kind, location, ROOT, BASE, MODULES, ADDED)
                    for kind, location in (('skill', installation / 'researchhelper'), ('app', backend))}
    return save(PUBLISHED / 'context-package.json', {'status': 'passed', 'retained': previous,
        'new_contexts': contexts, 'skill_test_executions': 227 + ADDED, 'app_test_executions': 227 + ADDED,
        'source_hashes': read(RELEASE / 'release-manifest.json')['source_hashes'],
        'helper_sha256': digest(__file__), 'full_harness_complete': False,
        'spawn_fixture_worker_imports': 'child workers import the matching source modules; parent import provenance verified'})


def publish():
    runtime = regression()
    package = read(PUBLISHED / 'context-package.json')
    final.current(package)
    assert package['status'] == 'passed' and package['helper_sha256'] == digest(__file__)
    assert package['skill_test_executions'] == package['app_test_executions'] == 240
    with patch.object(retained.retained.browser_gate, 'BASE', BASE):
        browser = retained.retained.browser_gate.browser_evidence()
    assert sum(item['checks'] for item in browser) == 152
    routes = {item['name']: Path(item['receipt']) for item in browser}
    actions = [(path, read(path)) for path in (ROOT / 'result/acceptance/task-action-context').glob('browser-*/receipt.json')]
    actions = [(path, value) for path, value in actions if value['status'] == 'passed'
               and len(value['checks']) == 15 and value['source_hashes']['app/web/workspace.js'] == digest(ROOT / 'products/app/frontend/workspace.js')]
    assert actions
    action_path, action = max(actions, key=lambda pair: pair[0].stat().st_mtime)
    assert not action['external'] and not action['errors'] and action['browser_closed'] and action['backend_stopped']
    installer, sync = dispatch.load_script('packaging/install_release.py'), dispatch.load_script('packaging/sync_demo.py')
    with patch.object(final, 'OUT', OUT), patch.object(final, 'PUBLISHED', PUBLISHED):
        with final.exact_receipts(writes={final.ACCEPTANCE / 'installation.json': OUT / 'installation.json'}):
            installer.install()
        with final.exact_receipts(reads={
            final.ACCEPTANCE / 'package-background.json': PUBLISHED / 'package-background.json',
            final.ACCEPTANCE / 'integrity.json': OUT / 'integrity.json',
            final.ACCEPTANCE / 'autonomous-composer/full-chat/acceptance.json': routes['chat'],
            final.ACCEPTANCE / 'roles-workspace/acceptance.json': routes['roles']},
            writes={final.ACCEPTANCE / 'demo-sync-background.json': OUT / 'demo-sync.json'}):
            sync.sync(background_candidate=True)
        installed = final.installed_readback(OUT / 'installation.json', OUT / 'demo-sync.json')
    release = read(RELEASE / 'release-manifest.json')
    return save(OUT / 'context-delivery.json', {
        'status': 'offline_candidate_with_legacy_assertion_conflicts', 'batch': 'task-context-release',
        'offline_tests_run': COUNT, 'offline_tests_passed': COUNT - 4, 'legacy_assertion_failures': runtime['failures'],
        'runtime_receipt': runtime['receipt'], 'runtime_receipt_sha256': digest(runtime['receipt']),
        'browser_evidence': browser, 'browser_rerun_checks': 152,
        'action_context_checks': 15, 'action_context_receipt': str(action_path),
        'packaged_checks': package, 'installed': installed, 'source_hashes': release['source_hashes'],
        'prior_release_receipt': str(PRIOR), 'dmg_sha256': release['artifacts']['dmg']['sha256'],
        'skill_sha256': release['artifacts']['skill']['sha256'], 'active_providers': ['ifind', 'tinyshare'],
        'foreground_app_launched': False, 'production_store_touched': False,
        'real_model_calls': 0, 'real_supplier_calls': 0, 'native_ui_verified': False, 'full_harness_complete': False})
