"""Batch release using unchanged native, package, browser and privacy gates."""

from pathlib import Path
from tests import release_research_contract_delivery as previous

ROOT = previous.ROOT
BASE = ROOT / 'result/acceptance/requirement-contract-release'
OUT = BASE / 'final'
PRIOR = previous.OUT / 'research-contract-delivery.json'


def source_gate():
    candidates = [(path, previous.read(path)) for path in BASE.glob('regression-*/runtime.json')]
    candidates = [(path, record) for path, record in candidates
        if record['tests_run'] == len(record['unique_test_ids']) == 1613
        and all(previous.digest(ROOT / name) == value for name, value in record['source_hashes'].items())]
    assert candidates, 'No matching full retained inventory for current source'
    path, record = max(candidates, key=lambda item: item[0].stat().st_mtime_ns)
    assert {item['id'] for item in record['failures']} == {item['id'] for item in previous.read(PRIOR)['legacy_assertion_failures']}
    assert not any(record[name] for name in ('errors', 'skipped', 'external_attempts'))
    assert record['sources_unchanged_during_run'] and record['frozen_live_runs_unchanged']
    reviews = [previous.read(p) for p in BASE.glob('source-*/receipt.json')]
    reviews = [r for r in reviews if r['source_hashes'] == record['source_hashes']]
    assert reviews and reviews[-1]['old_live_files_unchanged']
    assert all(previous.digest(p) == value for p, value in reviews[-1]['protected_files'].items())
    host = previous.read(ROOT / 'result/acceptance/host-boundary-release/final/host-delivery.json')
    return record | {'receipt': str(path), 'obsolete_assertion': previous.read(host['runtime_receipt'])['obsolete_assertion']}


def driver():
    original = Path(previous.__file__).read_text()
    source = original.replace('research-contract-release', 'requirement-contract-release')
    source = source.replace('research-contract-package.json', 'requirement-contract-package.json')
    source = source.replace('research-contract-delivery.json', 'requirement-contract-delivery.json')
    source = source.replace('1589', '1613').replace('ADDED=345', 'ADDED=369')
    source = source.replace('modules, 95', 'modules, 115').replace('554', '574')
    namespace = {'__file__': __file__, '__name__': 'tests.requirement_delivery_driver'}
    exec(compile(source, previous.__file__, 'exec'), namespace)
    namespace.update(BASE=BASE, OUT=OUT, PRIOR=PRIOR, source_gate=source_gate)
    namespace['ADDITIONS'] += ['tests.test_requirement_text_submission_offline',
                              'tests.test_numeric_submission_shape_offline']
    return namespace


def build():
    return driver()['build']()


def package_checks():
    return driver()['package_checks']()


def publish():
    return driver()['publish']()
