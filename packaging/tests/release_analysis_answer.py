"""Deliver explicit analysis text types; preserve failures and prior releases."""

from contextlib import ExitStack
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from tests import release_parallel_upgrade as retained
from tests.packaged_runtime_tests import run_packaged_tests

ROOT, RELEASE = retained.ROOT, retained.RELEASE
BASE = ROOT / 'result/acceptance/analysis-answer-release'
OUT, PUBLISHED = BASE / 'final', BASE / 'published'
PRIOR = ROOT / 'result/acceptance/parallel-upgrade-release/final/parallel-delivery.json'
MODULES = ['tests.test_analysis_answer_types_offline']
read, digest, save = retained.retained.dispatch.read, retained.retained.dispatch.digest, retained.save
final, dispatch = retained.final, retained.retained.dispatch


def regression():
    review = read(BASE / 'review.json')
    path = Path(review['runtime_receipt'])
    assert digest(path) == review['runtime_sha256']
    value = read(path)
    final.current(value)
    assert value['tests_run'] == len(value['unique_test_ids']) == 1244
    expected = {item['id'] for item in read(PRIOR)['legacy_assertion_failures']}
    assert {item['id'] for item in value['failures']} == expected
    assert all(any(fragment in item['traceback'] for fragment in ('18 != 15', '7 != 4', "'answers': []"))
               for item in value['failures'])
    assert not any(value[key] for key in ('errors', 'skipped', 'external_attempts'))
    assert value['sources_unchanged_during_run'] and value['frozen_live_runs_unchanged']
    assert all(digest(path) == expected for path, expected in review['protected_current_run'].items())
    original = read(read(retained.retained.PRIOR)['runtime_receipt'])
    return value | {'receipt': str(path), 'obsolete_assertion': original['obsolete_assertion']}


def build():
    regression()
    prior = read(PRIOR)
    artifacts = {'ResearchHelper-0.10.0-arm64.dmg': prior['dmg_sha256'],
                 'ResearchHelper-Skill-0.10.0.zip': prior['skill_sha256']}
    assert all(digest(RELEASE / key) == value for key, value in artifacts.items())
    backup = ROOT / 'result/release-backups/20261007-analysis-answer'
    assert not backup.exists(), 'Do not replace a recovery copy'
    backup.mkdir(parents=True)
    for name in (*artifacts, 'release-manifest.json', 'app-manifest.json'):
        shutil.copyfile(RELEASE / name, backup / name)
        assert digest(RELEASE / name) == digest(backup / name)
    shutil.copytree(ROOT / '.skills/researchhelper', backup / 'installed-project-skill',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    value = {'status': 'building', 'backup': str(backup), 'app_installed': False, 'demo_synced': False}
    save(OUT / 'build.json', value)
    try:
        value.update(status='built_candidate_not_installed', artifacts=dispatch.build(),
                     project_skill_generated=True, source_hashes=read(RELEASE / 'release-manifest.json')['source_hashes'])
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
    assert previous['skill_test_executions'] == previous['app_test_executions'] == 223
    with tempfile.TemporaryDirectory(prefix='answer-skill-', dir=BASE) as folder:
        installation = Path(folder) / 'installation'
        with ZipFile(RELEASE / 'ResearchHelper-Skill-0.10.0.zip') as archive:
            assert all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in archive.namelist())
            archive.extractall(installation)
        backend = RELEASE / 'ResearchHelper.app/Contents/Resources/backend/researchhelper-server/researchhelper-server'
        contexts = {kind: run_packaged_tests(kind, location, ROOT, BASE, MODULES, 4)
                    for kind, location in (('skill', installation / 'researchhelper'), ('app', backend))}
    return save(PUBLISHED / 'answer-package.json', {'status': 'passed', 'retained': previous,
        'new_contexts': contexts, 'skill_test_executions': 227, 'app_test_executions': 227,
        'source_hashes': read(RELEASE / 'release-manifest.json')['source_hashes'],
        'helper_sha256': digest(__file__), 'full_harness_complete': False})


def publish():
    runtime = regression()
    package = read(PUBLISHED / 'answer-package.json')
    final.current(package)
    assert package['status'] == 'passed' and package['helper_sha256'] == digest(__file__)
    assert package['skill_test_executions'] == package['app_test_executions'] == 227
    with patch.object(retained.browser_gate, 'BASE', BASE):
        browser = retained.browser_gate.browser_evidence()
    assert sum(item['checks'] for item in browser) == 152
    paths = {item['name']: Path(item['receipt']) for item in browser}
    installer, sync = dispatch.load_script('packaging/install_release.py'), dispatch.load_script('packaging/sync_demo.py')
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
    release = read(RELEASE / 'release-manifest.json')
    return save(OUT / 'answer-delivery.json', {
        'status': 'offline_candidate_with_legacy_assertion_conflicts', 'offline_tests_run': 1244,
        'offline_tests_passed': 1240, 'legacy_assertion_failures': runtime['failures'],
        'runtime_receipt': runtime['receipt'], 'runtime_receipt_sha256': digest(runtime['receipt']),
        'browser_evidence': browser, 'browser_rerun_checks': 152, 'packaged_checks': package, 'installed': installed,
        'prior_release_receipt': str(PRIOR), 'dmg_sha256': release['artifacts']['dmg']['sha256'],
        'skill_sha256': release['artifacts']['skill']['sha256'], 'active_providers': ['ifind', 'tinyshare'],
        'foreground_app_launched': False, 'production_store_touched': False,
        'real_model_calls': 0, 'real_supplier_calls': 0, 'native_ui_verified': False,
        'full_harness_complete': False})
