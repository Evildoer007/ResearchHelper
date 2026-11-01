"""Publish UI action binding with unchanged prior release gates and backups.

Receipts use a new namespace. Full regression, browser evidence and original
package validators are retained; no real calls or foreground launch are used.
"""

from contextlib import contextmanager, ExitStack
from pathlib import Path
import shutil
from unittest.mock import patch

from tests import release_analysis_answer as retained
from tests import review_analysis_answer_release as review

ROOT, RELEASE = retained.ROOT, retained.RELEASE
BASE = ROOT / 'result/acceptance/task-action-binding-release'
OUT, PUBLISHED = BASE / 'final', BASE / 'published'
PRIOR = ROOT / 'result/acceptance/analysis-answer-release/final/answer-delivery.json'


@contextmanager
def gates():
    with ExitStack() as stack:
        for name, value in (('BASE', BASE), ('OUT', OUT), ('PUBLISHED', PUBLISHED), ('PRIOR', PRIOR)):
            stack.enter_context(patch.object(retained, name, value))
        yield


def regression():
    with patch.object(review, 'BASE', BASE), patch.object(review, 'PRIOR', PRIOR):
        return review.main()


def build():
    with gates():
        retained.regression()
    previous = retained.read(PRIOR)
    artifacts = {'ResearchHelper-0.10.0-arm64.dmg': previous['dmg_sha256'],
                 'ResearchHelper-Skill-0.10.0.zip': previous['skill_sha256']}
    assert all(retained.digest(RELEASE / name) == value for name, value in artifacts.items())
    backup = ROOT / 'result/release-backups/20261007-task-action-binding'
    assert not backup.exists(), 'Keep the existing recovery copy'
    backup.mkdir(parents=True)
    for name in (*artifacts, 'release-manifest.json', 'app-manifest.json'):
        shutil.copyfile(RELEASE / name, backup / name)
        assert retained.digest(RELEASE / name) == retained.digest(backup / name)
    shutil.copytree(ROOT / '.skills/researchhelper', backup / 'installed-project-skill',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    record = {'status': 'building', 'backup': str(backup), 'app_installed': False, 'demo_synced': False}
    retained.save(OUT / 'build.json', record)
    try:
        record.update(status='built_candidate_not_installed', artifacts=retained.dispatch.build(),
                      project_skill_generated=True,
                      source_hashes=retained.read(RELEASE / 'release-manifest.json')['source_hashes'])
    except BaseException:
        record.update(status='build_failed', project_skill_may_have_been_generated=True)
        raise
    finally:
        retained.save(OUT / 'build.json', record)
    return record


def package_checks():
    with gates():
        return retained.package_checks()


def publish():
    receipts = sorted((ROOT / 'result/acceptance/task-action-binding').glob('browser-*/receipt.json'),
                      key=lambda path: path.stat().st_mtime)
    source_hash = retained.digest(ROOT / 'products/app/frontend/workspace.js')
    matching = [(path, retained.read(path)) for path in receipts]
    matching = [(path, value) for path, value in matching if value['status'] == 'passed'
                and len(value['checks']) == 14 and value['source_hashes']['app/web/workspace.js'] == source_hash]
    assert matching, 'Current action binding browser acceptance is missing'
    path, browser = matching[-1]
    assert not browser['external'] and not browser['errors']
    assert browser['browser_closed'] and browser['backend_stopped']
    before = {str(p): retained.digest(p) for p in (ROOT / 'result/acceptance/current-authorized-parallel-lpr').rglob('*') if p.is_file()}
    with gates():
        record = retained.publish()
    assert all(retained.digest(path) == value for path, value in before.items())
    record.update(batch='task-action-binding', action_binding_checks=14,
                  action_binding_receipt=str(path), action_binding_receipt_sha256=retained.digest(path),
                  original_failed_real_study_unchanged=True, helper_sha256=retained.digest(__file__))
    return retained.save(OUT / 'binding-delivery.json', record)
