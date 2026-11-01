"""Complete only missing Skill cases against the same witnessed ZIP bytes."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from tests import ROOT
from tests.accept_argument_support_runtime import MODULES, TEST_COUNT
from tests.accept_delivered_ifind_no_data import run_packaged_tests
from tests.packaged_loopback_runner import run as loopback_tests, verify_operating_system_policy, LOOPBACK_MODULES
from tests.recheck_argument_skill_runtime import validate_source_cases


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def case_ids(specifications):
    def flatten(node):
        if isinstance(node, unittest.TestSuite):
            return [ident for child in node for ident in flatten(child)]
        ident = node.id()
        if not ident.startswith('tests.'):
            raise RuntimeError('A missing or foreign test cannot count as coverage')
        return [ident.removeprefix('tests.')]
    return flatten(unittest.defaultTestLoader.loadTestsFromNames(['tests.' + name for name in specifications]))


def missing_cases(expected, passed):
    if len(expected) != len(set(expected)) or len(passed) != len(set(passed)) or not set(passed).issubset(expected):
        raise RuntimeError('Duplicate or foreign cases cannot be merged into a complete cohort')
    return [name for name in expected if name not in set(passed)]


def run(candidate_path, source_path, runtime_path, witness_path):
    sys.path.insert(0, str(ROOT / 'packaging'))
    from build_current import verify_current_build
    candidate_path, source_path, runtime_path, witness_path = map(Path,
        (candidate_path, source_path, runtime_path, witness_path))
    candidate = verify_current_build(candidate_path, ROOT)
    protected = {str(path): digest(path) for path in (source_path, runtime_path, witness_path)}
    source, runtime, witness = [json.loads(path.read_text()) for path in (source_path, runtime_path, witness_path)]
    assert source['tests_expected'] == TEST_COUNT and runtime['source_tests'] == TEST_COUNT
    assert all(item['returncode'] == 0 and item['count_matches'] for item in source['source_shards'])
    assert validate_source_cases(source['source_shards'], MODULES) == TEST_COUNT
    assert witness['candidate'] == str(candidate_path.resolve())
    artifact = Path(candidate['artifacts']['skill']['path'])
    assert digest(artifact) == witness['artifact_sha256']
    old_payload = Path(witness['payload'])
    expected, passed, retained = case_ids(MODULES), [], []
    for folder in sorted(runtime_path.parent.glob('shard-*'), key=lambda path: int(path.name.split('-')[-1])):
        log = folder / 'skill-tests.log'
        try:
            result, _ = json.JSONDecoder().raw_decode(log.read_text().lstrip())
        except (ValueError, OSError):
            continue  # Failed records are retained, never relabelled successful.
        assert result['status'] == 'passed' and result['kind'] == 'skill'
        names = case_ids(result['test_modules'])
        assert len(names) == result['tests_run']
        for path in result['module_paths'].values():
            path = Path(path)
            assert path.is_relative_to(old_payload)
            assert path.relative_to(old_payload).as_posix() in witness['files']
        assert result['module_paths']
        passed.extend(names)
        retained.append(dict(log=str(log), sha256=digest(log), tests_run=len(names)))
    remaining = missing_cases(expected, passed)
    assert remaining and {name.split('.')[0] for name in remaining} <= {
        'test_task_read_snapshot', 'test_review_presentation_boundary'}
    output = Path(tempfile.mkdtemp(prefix='skill-loopback-recovery-', dir=ROOT / 'result/acceptance'))
    record = dict(status='running', candidate=str(candidate_path.resolve()),
        original_records=protected, source_tests=TEST_COUNT, source_was_rerun=False,
        retained_passed_tests=len(passed), retained_shards=retained, missing_case_ids=remaining,
        real_model_calls=0, real_supplier_calls=0, native_window_opened=False,
        app_installed=False, financial_quality_accepted=False, full_repository_tests_passed=False)
    try:
        record['sandbox_negative_checks'] = verify_operating_system_policy()
        with tempfile.TemporaryDirectory(prefix='installation-', dir=output) as temporary:
            temporary = Path(temporary)
            with zipfile.ZipFile(artifact) as archive:
                for info in archive.infolist():
                    path = Path(info.filename)
                    assert not path.is_absolute() and '..' not in path.parts and path.parts[0] == 'researchhelper'
                    assert (info.external_attr >> 16) & 0o170000 != 0o120000
                archive.extractall(temporary)
            installation = temporary / 'researchhelper'

            def verify_files():
                actual = {path.relative_to(installation).as_posix(): digest(path)
                          for path in installation.rglob('*') if path.is_file()}
                assert actual == witness['files']
            verify_files()
            fresh, results = [], []
            for module in dict.fromkeys(name.split('.')[0] for name in remaining):
                names = [name for name in remaining if name.split('.')[0] == module]
                folder = output / module
                folder.mkdir()
                runner = loopback_tests if module in LOOPBACK_MODULES else run_packaged_tests
                result = runner('skill', installation, ROOT, folder, names, len(names))
                assert result['status'] == 'passed' and result['tests_run'] == len(names)
                for path in result['module_paths'].values():
                    assert Path(path).is_relative_to(installation)
                    assert Path(path).relative_to(installation).as_posix() in witness['files']
                assert result['module_paths']
                fresh.extend(case_ids(result['test_modules']))
                results.append(result)
            verify_files()
        assert Counter(passed + fresh) == Counter(expected) and len(passed + fresh) == TEST_COUNT
        assert all(digest(path) == value for path, value in protected.items())
        assert digest(artifact) == witness['artifact_sha256']
        record.update(status='scoped_passed', skill_tests=TEST_COUNT, newly_run_tests=len(fresh),
            newly_run=results, original_records_unchanged=True, all_case_ids=expected,
            bytes_equal_to_released_zip=True, temporary_installation_removed=not any(output.glob('installation-*')))
    except Exception as error:
        record.update(status='failed', error_type=type(error).__name__, error=str(error)[:1000])
    finally:
        target = output / 'receipt.json'
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        print(json.dumps(dict(status=record['status'], retained_tests=len(passed),
            newly_run_tests=record.get('newly_run_tests'), receipt=str(target), error=record.get('error'))), flush=True)
    return record
