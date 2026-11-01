"""Complete prior cohort plus argument adoption, with bounded source shards."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests import accept_delivery_ready_financial_runtime as previous

ROOT = Path(__file__).resolve().parents[1]
MODULES = previous.MODULES + ['test_argument_support_workflow','test_reading_reference_protocol',
                            'test_registered_financial_bindings','test_reviewed_analysis_recovery',
                            'test_financial_reference_protocol','test_original_batch_protocol',
                            'test_reasoned_delivery_capacity','test_argument_reference_protocol',
                            'test_passage_scope_context','test_passage_context_capacity',
                            'test_review_submission_dispatch','test_review_remaining_deadline',
                            'test_completed_analysis_format_recovery','test_review_target_order',
                            'test_shared_financial_selectors','test_registered_ratio_reference',
                            'test_target_review_basis_schema','test_schema_cardinality',
                            'test_review_basis_aliases','test_table_heading_scope_context',
                            'test_thesis_question_context','test_planning_identity_profile',
                            'test_analysis_interpretation_scope','test_financial_text_period_reference',
                            'test_analysis_segment_citations','test_task_read_snapshot',
                            'test_review_presentation_boundary', 'test_workspace_task_view',
                            'test_completed_extraction_format_recovery', 'test_dependency_synthesis']
TEST_COUNT = previous.TEST_COUNT + 340
ISOLATED_MODULES=tuple(MODULES[len(previous.MODULES):])


def completed_frozen_receipt(before, candidate):
    """Older drivers return None after recording and checking all stages."""
    created = set(ROOT.glob('result/acceptance/packaged-filing-filters-*/receipt.json')) - before
    if len(created) != 1:
        raise RuntimeError('Frozen driver did not create one authoritative receipt')
    receipt = created.pop()
    result = json.loads(receipt.read_text())
    app = result.get('app', {})
    if (result.get('status') != 'scoped_passed' or result.get('candidate') != str(Path(candidate).resolve())
            or app.get('status') != 'passed' or app.get('tests_run') != TEST_COUNT
            or result.get('service_stopped') is not True or result.get('mount_detached') is not True):
        raise RuntimeError('Frozen receipt does not prove the selected candidate and complete cohort')
    return result, receipt


def run(*, frozen=False):
    prior = previous.MODULES, previous.TEST_COUNT
    previous.MODULES, previous.TEST_COUNT = MODULES, TEST_COUNT
    output = Path(tempfile.mkdtemp(prefix='argument-support-runtime-', dir=ROOT/'result/acceptance'))
    record = dict(status='running', tests_expected=TEST_COUNT, frozen=frozen,
                  skipped=0, source_shards=[], app_installed=False)
    original_run = subprocess.run

    def source_shards(command, *args, **kwargs):
        if (not isinstance(command, list) or command[1:4] != ['-B','-m','unittest']
                or command[4:] != ['tests.' + name for name in MODULES]):
            return original_run(command, *args, **kwargs)
        stdout, stderr, total, returncode = [], [], 0, 0
        groups,group=[],[]
        for name in MODULES:
            if name in ISOLATED_MODULES:
                if group:groups.append(group);group=[]
                groups.append([name])
            else:
                group.append(name)
                if len(group)==4:groups.append(group);group=[]
        if group:groups.append(group)
        from tests.accept_initial_acquisition_runtime import case_shard_groups
        groups=case_shard_groups(groups,ISOLATED_MODULES)
        for group in groups:
            names = ['tests.' + name for name in group]
            expected = unittest.defaultTestLoader.loadTestsFromNames(names).countTestCases()
            settings = dict(kwargs, timeout=60)
            result = original_run(command[:4] + names, *args, **settings)
            index = len(record['source_shards']) + 1
            (output/f'source-{index}.log').write_text(result.stdout + result.stderr)
            counts = re.findall(r'^Ran (\d+) tests? in ', result.stderr, re.MULTILINE)
            actual = int(counts[-1]) if counts else 0
            record['source_shards'].append(dict(index=index, modules=names, expected=expected,
                tests_run=actual, returncode=result.returncode, count_matches=actual == expected))
            total += actual
            returncode = returncode or result.returncode or (actual != expected)
            stdout.append(result.stdout); stderr.append(result.stderr)
            if result.returncode or actual != expected:
                # Preserve the failure and incomplete count; never spend the
                # remaining source/Skill cohort on a known failing candidate.
                break
        returncode = returncode or (total != TEST_COUNT)
        stderr.append(f'\nAggregated source shards: Ran {total} tests across {len(record["source_shards"])} shards.\n')
        return subprocess.CompletedProcess(command, int(bool(returncode)), ''.join(stdout), ''.join(stderr))

    try:
        runner = previous.previous
        factory = runner.sharded_runner
        from tests.packaged_loopback_runner import run as loopback_tests, LOOPBACK_MODULES
        def with_http_profile(original):
            def scoped(kind, location, root, folder, modules, count):
                if modules and all(name.split('.')[0] in LOOPBACK_MODULES for name in modules):
                    return loopback_tests(kind, location, root, folder, modules, count, runner=original)
                return original(kind, location, root, folder, modules, count)
            return factory(scoped, isolated_modules=ISOLATED_MODULES,case_shard_modules=ISOLATED_MODULES)
        with patch.object(runner, 'sharded_runner', with_http_profile):
            if frozen:
                before = set(ROOT.glob('result/acceptance/packaged-filing-filters-*/receipt.json'))
                result = previous.run_frozen()
                if result is None:
                    result, receipt = completed_frozen_receipt(before, os.environ['RESEARCHHELPER_CURRENT_RECEIPT'])
                    record['authoritative_frozen_receipt'] = str(receipt)
            else:
                with patch.object(subprocess, 'run', source_shards):
                    result = previous.run()
        record['status'] = result['status']
    except BaseException as error:
        record.update(status='failed', error_type=type(error).__name__)
        raise
    finally:
        previous.MODULES, previous.TEST_COUNT = prior
        receipt = output/'receipt.json'
        receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        print(json.dumps(dict(receipt=str(receipt), status=record['status'],
                             source_shards=len(record['source_shards']))), flush=True)
    return record


def run_frozen():
    return run(frozen=True)


if __name__ == '__main__':
    run()
