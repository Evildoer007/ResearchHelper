"""Publish the reviewed extraction diagnostic with the retained release gates.

Fresh receipts and recovery copies; no real calls or foreground interaction.
The prior test inventory and all its failures remain visible.
"""
import json
from pathlib import Path

from tests import release_calculation_contract as previous

ROOT = previous.ROOT
BASE = ROOT / 'result/acceptance/extraction-diagnostic-release'
PRIOR = ROOT / 'result/acceptance/calculation-contract-release/final/calculation-contract-delivery.json'
ADDITIONS = ('tests.test_extraction_exhaustion_diagnostic_offline',
             'tests.test_ifind_etf_calculation_revalidation_offline')


def driver():
    original = ROOT / 'packaging/tests/release_source_readiness.py'
    source = original.read_text()
    modules = previous.driver()['MODULES'] + list(ADDITIONS)
    replacements = {
        "'result/acceptance/source-readiness-release'": "'result/acceptance/extraction-diagnostic-release'",
        "'source-readiness-release'": "'extraction-diagnostic-release'",
        "'source-readiness-delivery.json'": "'extraction-diagnostic-delivery.json'",
        "'source-readiness-package.json'": "'extraction-diagnostic-package.json'",
        "'result/acceptance/task-context-release/final/context-delivery.json'":
            "'result/acceptance/calculation-contract-release/final/calculation-contract-delivery.json'",
        "'result/release-backups/20261007-source-readiness'":
            "'result/release-backups/20261007-extraction-diagnostic'",
        'COUNT, ADDED = 1265, 21': 'COUNT, ADDED = 1317, 73',
        "MODULES = [*retained.MODULES, 'tests.test_authorized_source_permissions_offline']":
            'MODULES = ' + repr(modules),
        '== 248': '== 300',
        "for path in (ROOT / 'result/acceptance/current-authorized-source-permissions').rglob('*') if path.is_file()":
            "for folder in ('current-authorized-source-permissions', 'current-authorized-tinyshare-sample', "
            "'current-authorized-ifind-etf', 'current-token-verification', "
            "'ifind-etf-calculation-revalidation', 'current-token-research-assessment') "
            "for path in (ROOT / 'result/acceptance' / folder).rglob('*') if path.is_file()",
    }
    for before, after in replacements.items():
        assert before in source, 'Retained release routing changed'
        source = source.replace(before, after)
    namespace = {'__file__': __file__, '__name__': 'tests.extraction_diagnostic_release_in_memory'}
    exec(compile(source, str(original), 'exec'), namespace)
    assert namespace['BASE'] == BASE and namespace['PRIOR'] == PRIOR
    assert namespace['OUT'] == BASE / 'final' and namespace['PUBLISHED'] == BASE / 'published'
    return namespace


def review():
    routed = driver()
    protected = routed['real_records']()
    original = ROOT / 'packaging/tests/review_workflow_release.py'
    source = original.read_text()
    before = "assert len(prior['unique_test_ids']) == 1201"
    assert source.count(before) == 1
    namespace = {'__file__': str(original), '__name__': 'tests.extraction_diagnostic_review_in_memory'}
    exec(compile(source.replace(before, "assert len(prior['unique_test_ids']) == 1308"),
                 str(original), 'exec'), namespace)
    namespace.update(BASE=BASE, PRIOR=PRIOR, ADDITIONS=ADDITIONS)
    result = namespace['main']()
    prior = json.loads(PRIOR.read_text())
    assert result['tests_run'] == len(result['unique_test_ids']) == 1317
    assert {item['id'] for item in result['failures']} == {item['id'] for item in prior['legacy_assertion_failures']}
    assert not any(result[key] for key in ('errors', 'skipped', 'external_attempts'))
    assert result['sources_unchanged_during_run'] and result['frozen_live_runs_unchanged']
    assert routed['real_records']() == protected
    path = max(BASE.glob('regression-*/runtime.json'), key=lambda item: item.stat().st_mtime)
    return routed['save'](BASE / 'review.json', {
        'runtime_receipt': str(path), 'runtime_sha256': routed['digest'](path),
        'protected_real_runs': protected, 'tests_run': 1317, 'tests_passed': 1313, 'new_real_calls': 0})


def build():
    return driver()['build']()


def package_checks():
    return driver()['package_checks']()


def publish():
    return driver()['publish']()


def production_boundary(*, finalize=False):
    return driver()['production_boundary'](finalize=finalize)
