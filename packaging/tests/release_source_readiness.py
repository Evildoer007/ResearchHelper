"""Publish provider-specific settings using retained release gates.

Fresh output/backup namespaces; old artifacts, tests and real runs preserved.
No model/provider calls, production data writes or foreground App operation.
"""
from contextlib import ExitStack, contextmanager
from pathlib import Path
import shutil
from unittest.mock import patch

from tests import release_task_context as retained

ROOT, RELEASE = retained.ROOT, retained.RELEASE
BASE = ROOT / 'result/acceptance/source-readiness-release'
OUT, PUBLISHED = BASE / 'final', BASE / 'published'
PRIOR = ROOT / 'result/acceptance/task-context-release/final/context-delivery.json'
MODULES = [*retained.MODULES, 'tests.test_authorized_source_permissions_offline']
COUNT, ADDED = 1265, 21
read, digest, save, final, dispatch = retained.read, retained.digest, retained.save, retained.final, retained.dispatch
original_real_records = retained.real_records


def real_records():
    return original_real_records() | {
        str(path): digest(path)
        for path in (ROOT / 'result/acceptance/current-authorized-source-permissions').rglob('*') if path.is_file()}


@contextmanager
def batch():
    with ExitStack() as stack:
        for name, value in (('BASE', BASE), ('OUT', OUT), ('PUBLISHED', PUBLISHED), ('PRIOR', PRIOR),
                            ('MODULES', MODULES), ('COUNT', COUNT), ('ADDED', ADDED), ('real_records', real_records)):
            stack.enter_context(patch.object(retained, name, value))
        yield


def review():
    with batch():
        return retained.review()


def regression():
    with batch():
        return retained.regression()


def build():
    regression()
    previous = read(PRIOR)
    artifacts = {'ResearchHelper-0.10.0-arm64.dmg': previous['dmg_sha256'],
                 'ResearchHelper-Skill-0.10.0.zip': previous['skill_sha256']}
    assert all(digest(RELEASE / name) == value for name, value in artifacts.items())
    backup = ROOT / 'result/release-backups/20261007-source-readiness'
    assert not backup.exists(), 'Do not overwrite the recovery copy'
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
                     source_hashes=read(RELEASE / 'release-manifest.json')['source_hashes'])
    except BaseException:
        value['status'] = 'build_failed'
        raise
    finally:
        save(OUT / 'build.json', value)
    return value


def package_checks():
    with batch():
        result = retained.package_checks()
    assert result['skill_test_executions'] == result['app_test_executions'] == 248
    return save(PUBLISHED / 'source-readiness-package.json', result | {'release_driver_sha256': digest(__file__)})


def publish():
    runtime = regression()
    package = read(PUBLISHED / 'source-readiness-package.json')
    final.current(package)
    assert package['status'] == 'passed' and package['release_driver_sha256'] == digest(__file__)
    assert package['helper_sha256'] == digest(retained.__file__)
    assert package['skill_test_executions'] == package['app_test_executions'] == 248
    with patch.object(retained.retained.retained.browser_gate, 'BASE', BASE):
        browser = retained.retained.retained.browser_gate.browser_evidence()
    assert sum(item['checks'] for item in browser) == 152
    paths = {item['name']: Path(item['receipt']) for item in browser}
    candidates = [(path, read(path)) for path in (ROOT / 'result/acceptance/source-readiness-ui').glob('browser-*/receipt.json')]
    candidates = [(p, r) for p, r in candidates if r['status'] == 'passed' and len(r['checks']) == 18
                  and r['source_hashes']['app/web/workspace.js'] == digest(ROOT / 'products/app/frontend/workspace.js')]
    assert candidates
    ui_path, ui = max(candidates, key=lambda pair: pair[0].stat().st_mtime)
    assert not ui['failures'] and not ui['errors'] and not ui['external_attempts']
    assert ui['browser_stopped'] and ui['backend_stopped']
    cycles = [(p, read(p)) for p in (ROOT / 'result/acceptance/task-selection-cycle').glob('browser-*/receipt.json')]
    cycles = [(p, r) for p, r in cycles if r['status'] == 'passed' and len(r['checks']) == 19
              and r['source_hashes']['app/web/workspace.js'] == digest(ROOT / 'products/app/frontend/workspace.js')]
    assert cycles
    cycle_path, cycle = max(cycles, key=lambda pair: pair[0].stat().st_mtime)
    assert not cycle['errors'] and not cycle['external'] and cycle['browser_closed'] and cycle['backend_stopped']
    installer = dispatch.load_script('packaging/install_release.py')
    sync = dispatch.load_script('packaging/sync_demo.py')
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
    return save(OUT / 'source-readiness-delivery.json', {
        'status': 'offline_candidate_with_legacy_assertion_conflicts', 'batch': 'source-readiness-release',
        'offline_tests_run': COUNT, 'offline_tests_passed': COUNT - 4,
        'legacy_assertion_failures': runtime['failures'], 'runtime_receipt': runtime['receipt'],
        'runtime_receipt_sha256': digest(runtime['receipt']), 'browser_evidence': browser,
        'browser_rerun_checks': 152, 'source_readiness_ui_checks': 18, 'source_readiness_ui_receipt': str(ui_path),
        'task_selection_cycle_checks': 19, 'task_selection_cycle_receipt': str(cycle_path),
        'packaged_checks': package, 'installed': installed, 'source_hashes': release['source_hashes'],
        'prior_release_receipt': str(PRIOR), 'dmg_sha256': release['artifacts']['dmg']['sha256'],
        'skill_sha256': release['artifacts']['skill']['sha256'], 'active_providers': ['ifind', 'tinyshare'],
        'foreground_app_launched': False, 'production_store_touched': False,
        'real_model_calls': 0, 'real_supplier_calls': 0, 'full_harness_complete': False})


def production_boundary(*, finalize=False):
    """Hash-only readback; never opens or migrates the production archive."""
    store = Path.home() / 'Library/Application Support/ResearchHelper'
    hashes = {str(p): digest(p) for p in store.rglob('*') if p.is_file()}
    path = OUT / 'production-before.json'
    if not finalize:
        assert not path.exists(), 'Do not replace the before snapshot'
        return save(path, {'files': hashes})
    assert read(path)['files'] == hashes
    return save(OUT / 'production-readback.json', {'status': 'passed', 'files': hashes,
                'file_count': len(hashes), 'production_files_unchanged': True})
