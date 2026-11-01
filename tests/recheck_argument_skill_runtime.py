"""Recheck the actual Skill ZIP, retaining complete earlier source evidence.

Only shard boundaries change. No assertion, module or per-shard deadline is
removed; earlier failures remain immutable and are linked from this receipt.
"""
import contextlib
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from tests.accept_argument_support_runtime import MODULES,TEST_COUNT,ISOLATED_MODULES
from tests.accept_initial_acquisition_runtime import sharded_runner
from tests.accept_delivered_ifind_no_data import run_packaged_tests

ROOT=Path(__file__).resolve().parents[1]


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_source_cases(shards, modules):
    """Module and individual-case shards must cover the same ordered cases."""
    def identifiers(node):
        if isinstance(node, unittest.TestSuite):
            return [name for child in node for name in identifiers(child)]
        return [node.id()]
    expected = identifiers(unittest.defaultTestLoader.loadTestsFromNames(['tests.'+name for name in modules]))
    actual = identifiers(unittest.defaultTestLoader.loadTestsFromNames(
        [name for shard in shards for name in shard['modules']]))
    assert expected and len(expected)==len(set(expected))
    assert actual==expected and len(actual)==len(set(actual))
    return len(actual)


def run(candidate,source_record):
    candidate,source_record=Path(candidate).resolve(),Path(source_record).resolve()
    sys.path.insert(0,str(ROOT/'packaging'))
    from build_current import verify_current_build
    value=verify_current_build(candidate,ROOT)
    assert source_record.is_relative_to(ROOT/'result/acceptance')
    source=json.loads(source_record.read_text())
    shards=source['source_shards']
    assert sum(item['tests_run'] for item in shards)==TEST_COUNT
    assert all(item['returncode']==0 and item['count_matches'] for item in shards)
    assert validate_source_cases(shards, MODULES)==TEST_COUNT
    output=Path(tempfile.mkdtemp(prefix='argument-skill-recheck-',dir=ROOT/'result/acceptance'))
    record=dict(status='running',candidate=str(candidate),source_receipt=str(source_record),
        source_receipt_sha256=digest(source_record),source_tests=TEST_COUNT,
        source_was_rerun=False,real_model_calls=0,real_supplier_calls=0,
        full_repository_tests_passed=False,financial_quality_accepted=False,app_installed=False)
    try:
        artifact=value['artifacts']['skill']
        assert digest(artifact['path'])==artifact['sha256']
        with (output/'checks.log').open('w') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
            with tempfile.TemporaryDirectory(prefix='installation-',dir=output) as temporary:
                with zipfile.ZipFile(artifact['path']) as archive:
                    for item in archive.infolist():
                        path=Path(item.filename)
                        assert not path.is_absolute() and '..' not in path.parts and path.parts[0]=='researchhelper'
                        assert (item.external_attr>>16)&0o170000!=0o120000
                    archive.extractall(temporary)
                installation=Path(temporary)/'researchhelper'
                runner=sharded_runner(run_packaged_tests,isolated_modules=ISOLATED_MODULES,
                    case_shard_modules=ISOLATED_MODULES)
                record['skill']=runner('skill',installation,ROOT,output,MODULES,TEST_COUNT)
            record['temporary_installation_removed']=not any(output.glob('installation-*'))
            assert record['temporary_installation_removed']
            assert record['skill']['tests_run']==TEST_COUNT and record['skill']['status']=='passed'
            assert digest(source_record)==record['source_receipt_sha256']
            verify_current_build(candidate,ROOT)
            record['status']='scoped_passed'
    except BaseException as error:
        record.update(status='failed',error_type=type(error).__name__)
        with (output/'failure.txt').open('w') as failure:failure.write(str(error))
    finally:
        target=output/'receipt.json'
        target.write_text(json.dumps(record,ensure_ascii=False,indent=2))
        print(json.dumps(dict(status=record['status'],receipt=str(target),
            source_tests=record['source_tests'],skill_tests=record.get('skill',{}).get('tests_run'),
            error_type=record.get('error_type')),ensure_ascii=False),flush=True)
    return record
