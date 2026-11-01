"""Verify explicit filing selection and identity guards in source and Skill."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from tests.accept_delivered_ifind_no_data import run_packaged_tests

MODULES = ['test_ifind_filing_filter_selection', 'test_ifind_retained_catalog',
           'test_identity_period_provenance']
TEST_COUNT = 38


def run():
    output = Path(tempfile.mkdtemp(prefix='filing-filter-runtime-', dir=ROOT / 'result/acceptance'))
    record = dict(status='failed', model_calls=0, provider_calls=0, native_window_opened=False,
                  production_store_touched=False, original_tests_modified=False, installed=False,
                  source_and_skill_only=True, app_accepted=False, financial_quality_accepted=False)
    try:
        result = subprocess.run([sys.executable, '-B', '-m', 'unittest',
            *['tests.' + name for name in MODULES]], cwd=ROOT,
            capture_output=True, text=True, timeout=45)
        (output / 'source-tests.log').write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stderr
        record['source_tests'] = TEST_COUNT
        with tempfile.TemporaryDirectory(prefix='payload-', dir=output) as directory:
            spec = importlib.util.spec_from_file_location('filing_skill_builder', ROOT / 'packaging/skill/build_skill.py')
            builder = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(builder)
            builder.skill(output=Path(directory), install=False)
            record['skill'] = run_packaged_tests('skill', Path(directory) / 'researchhelper',
                ROOT, output, MODULES, TEST_COUNT)
        record['temporary_payload_removed'] = not any(output.glob('payload-*'))
        record['status'] = 'scoped_passed'
    except Exception as error:
        record['error'] = str(error)
    target = output / 'receipt.json'
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(dict(status=record['status'], receipt=str(target), error=record.get('error')), ensure_ascii=False))
    if record['status'] != 'scoped_passed':
        raise SystemExit(1)


if __name__ == '__main__':
    run()
