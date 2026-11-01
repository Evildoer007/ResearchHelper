"""Release the reviewed delivery changes through retained offline gates.

Only route batch outputs, counts and additional test modules. Original package,
installation, privacy and browser validators retain their assertions.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'result/acceptance/delivery-integrity-release'


def _driver():
    original = ROOT / 'packaging/tests/release_source_readiness.py'
    source = original.read_text()
    replacements = {
        "'result/acceptance/source-readiness-release'": "'result/acceptance/delivery-integrity-release'",
        "'source-readiness-release'": "'delivery-integrity-release'",
        "'source-readiness-delivery.json'": "'delivery-integrity-delivery.json'",
        "'result/acceptance/task-context-release/final/context-delivery.json'":
            "'result/acceptance/source-readiness-release/final/source-readiness-delivery.json'",
        "'result/release-backups/20261007-source-readiness'":
            "'result/release-backups/20261007-delivery-integrity'",
        "COUNT, ADDED = 1265, 21": "COUNT, ADDED = 1293, 49",
        "MODULES = [*retained.MODULES, 'tests.test_authorized_source_permissions_offline']":
            "MODULES = [*retained.MODULES, 'tests.test_authorized_source_permissions_offline', "
            "'tests.test_delivery_integrity_offline', 'tests.test_delivery_transaction_offline', "
            "'tests.test_authorized_tinyshare_sample_offline']",
        "== 248": "== 276",
        "for path in (ROOT / 'result/acceptance/current-authorized-source-permissions').rglob('*') if path.is_file()":
            "for folder in ('current-authorized-source-permissions', 'current-authorized-tinyshare-sample') "
            "for path in (ROOT / 'result/acceptance' / folder).rglob('*') if path.is_file()",
    }
    for before, after in replacements.items():
        assert before in source, 'Retained release driver changed: ' + before
        source = source.replace(before, after)
    namespace = {'__file__': __file__, '__name__': 'tests.delivery_integrity_release_in_memory'}
    exec(compile(source, str(original), 'exec'), namespace)
    assert namespace['BASE'] == BASE
    assert namespace['OUT'] == BASE / 'final' and namespace['PUBLISHED'] == BASE / 'published'
    return namespace


def review():
    return _driver()['review']()


def build():
    return _driver()['build']()


def package_checks():
    return _driver()['package_checks']()


def publish():
    return _driver()['publish']()


def production_boundary(*, finalize=False):
    return _driver()['production_boundary'](finalize=finalize)
