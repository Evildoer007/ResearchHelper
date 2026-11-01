"""Publish verified method-context and focus fixes without altering prior receipts."""

from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch
from zipfile import ZipFile

from tests import release_workflow_batch as retained
from tests import release_host_boundary as browser_gate
from tests import rebuild_publication_guard_final_delivery as final
from tests.packaged_runtime_tests import run_packaged_tests

ROOT, RELEASE = retained.ROOT, retained.RELEASE
BASE = ROOT / 'result/acceptance/parallel-upgrade-release'
OUT, PUBLISHED = BASE / 'final', BASE / 'published'
RUNTIME = ROOT / 'result/acceptance/parallel-upgrade/regression-oendrxzx/runtime.json'
PRIOR = ROOT / 'result/acceptance/workflow-release/final/workflow-delivery.json'
MODULES = ['tests.test_declared_method_knowledge_offline']


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    return value


def regression():
    value = retained.dispatch.read(RUNTIME)
    final.current(value)
    prior = retained.dispatch.read(PRIOR)
    assert value['tests_run'] == len(value['unique_test_ids']) == 1240
    assert value['failures'] == prior['legacy_assertion_failures']
    assert not any(value[key] for key in ('errors', 'skipped', 'external_attempts'))
    assert value['sources_unchanged_during_run'] and value['frozen_live_runs_unchanged']
    assert not value['old_test_files_modified'] and not value['all_retained_tests_passed']
    baseline = retained.dispatch.read(retained.dispatch.read(retained.PRIOR)['runtime_receipt'])
    return value | {'receipt': str(RUNTIME), 'obsolete_assertion': baseline['obsolete_assertion']}


def build():
    regression()
    prior = retained.dispatch.read(PRIOR)
    artifacts = {'ResearchHelper-0.10.0-arm64.dmg': prior['dmg_sha256'],
                 'ResearchHelper-Skill-0.10.0.zip': prior['skill_sha256']}
    assert all(retained.dispatch.digest(RELEASE / key) == value for key, value in artifacts.items())
    backup = ROOT / 'result/release-backups/20261007-parallel-upgrade'
    assert not backup.exists(), 'Keep the existing recovery copy'
    backup.mkdir(parents=True)
    for name in (*artifacts, 'release-manifest.json', 'app-manifest.json'):
        shutil.copyfile(RELEASE / name, backup / name)
        assert retained.dispatch.digest(RELEASE / name) == retained.dispatch.digest(backup / name)
    shutil.copytree(ROOT / '.skills/researchhelper', backup / 'installed-project-skill',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    value = {'status': 'building', 'backup': str(backup), 'app_installed': False, 'demo_synced': False}
    save(OUT / 'build.json', value)
    try:
        value.update(artifacts=retained.dispatch.build(), status='built_candidate_not_installed',
                     project_skill_generated=True,
                     source_hashes=retained.dispatch.read(RELEASE / 'release-manifest.json')['source_hashes'])
    except BaseException:
        value.update(status='build_failed', project_skill_may_have_been_generated=True)
        raise
    finally:
        save(OUT / 'build.json', value)
    return value


def package_checks():
    regression()
    OUT.mkdir(parents=True, exist_ok=True)
    PUBLISHED.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        for name, value in (('BASE', BASE), ('OUT', OUT), ('PUBLISHED', PUBLISHED), ('regression', regression)):
            stack.enter_context(patch.object(retained, name, value))
        previous = retained.package_checks()
    assert previous['skill_test_executions'] == previous['app_test_executions'] == 214
    with tempfile.TemporaryDirectory(prefix='parallel-skill-', dir=BASE) as folder:
        installation = Path(folder) / 'installation'
        with ZipFile(RELEASE / 'ResearchHelper-Skill-0.10.0.zip') as archive:
            assert all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in archive.namelist())
            archive.extractall(installation)
        backend = RELEASE / 'ResearchHelper.app/Contents/Resources/backend/researchhelper-server/researchhelper-server'
        contexts = {kind: run_packaged_tests(kind, location, ROOT, BASE, MODULES, 9)
                    for kind, location in (('skill', installation / 'researchhelper'), ('app', backend))}
    return save(PUBLISHED / 'parallel-package.json', {
        'status': 'passed', 'retained': previous, 'new_contexts': contexts,
        'skill_test_executions': 223, 'app_test_executions': 223,
        'source_hashes': retained.dispatch.read(RELEASE / 'release-manifest.json')['source_hashes'],
        'helper_sha256': retained.dispatch.digest(__file__), 'full_harness_complete': False})


def publish():
    runtime = regression()
    package = retained.dispatch.read(PUBLISHED / 'parallel-package.json')
    final.current(package)
    assert package['status'] == 'passed' and package['helper_sha256'] == retained.dispatch.digest(__file__)
    assert package['skill_test_executions'] == package['app_test_executions'] == 223
    with patch.object(browser_gate, 'BASE', BASE):
        browser = browser_gate.browser_evidence()
    assert sum(item['checks'] for item in browser) == 152
    focus_path = ROOT / 'result/acceptance/sidebar-dialog-focus/browser-eFtFpU/receipt.json'
    focus = retained.dispatch.read(focus_path)
    final.current(focus)
    assert focus['status'] == 'passed' and len(focus['checks']) == 28
    assert not focus['errors'] and not focus['external'] and not focus['writes']
    assert focus['browser_closed'] and focus['backend_stopped']
    paths = {item['name']: Path(item['receipt']) for item in browser}
    installer = retained.dispatch.load_script('packaging/install_release.py')
    sync = retained.dispatch.load_script('packaging/sync_demo.py')
    with patch.object(final, 'OUT', OUT), patch.object(final, 'PUBLISHED', PUBLISHED):
        with final.exact_receipts(writes={final.ACCEPTANCE / 'installation.json': OUT / 'installation.json'}):
            installer.install()
        with final.exact_receipts(reads={
            final.ACCEPTANCE / 'package-background.json': PUBLISHED / 'package-background.json',
            final.ACCEPTANCE / 'integrity.json': OUT / 'integrity.json',
            final.ACCEPTANCE / 'autonomous-composer/full-chat/acceptance.json': paths['chat'],
            final.ACCEPTANCE / 'roles-workspace/acceptance.json': paths['roles']},
            writes={final.ACCEPTANCE / 'demo-sync-background.json': OUT / 'demo-sync.json'}):
            sync.sync(background_candidate=True)
        installed = final.installed_readback(OUT / 'installation.json', OUT / 'demo-sync.json')
    release = retained.dispatch.read(RELEASE / 'release-manifest.json')
    return save(OUT / 'parallel-delivery.json', {
        'status': 'offline_candidate_with_legacy_assertion_conflicts',
        'offline_tests_run': 1240, 'offline_tests_passed': 1236, 'legacy_assertion_failures': runtime['failures'],
        'runtime_receipt': str(RUNTIME), 'runtime_receipt_sha256': retained.dispatch.digest(RUNTIME),
        'browser_evidence': browser, 'browser_rerun_checks': 152,
        'focus_checks': 28, 'focus_receipt': str(focus_path), 'packaged_checks': package, 'installed': installed,
        'prior_release_receipt': str(PRIOR), 'dmg_sha256': release['artifacts']['dmg']['sha256'],
        'skill_sha256': release['artifacts']['skill']['sha256'], 'active_providers': ['ifind', 'tinyshare'],
        'foreground_app_launched': False, 'production_store_touched': False,
        'real_model_calls': 0, 'real_supplier_calls': 0, 'native_ui_verified': False,
        'full_harness_complete': False})
