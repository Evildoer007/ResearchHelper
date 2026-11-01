"""Extend the full source, Skill and frozen-App cohort with source navigation."""
from pathlib import Path
import sys
import unittest
import shutil
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from tests import accept_report_version_paper_runtime as previous

MODULES = ['test_identity_period_navigation_guidance' if name == 'test_identity_period_current_guidance' else name
           for name in previous.MODULES] + ['test_initial_acquisition_navigation', 'test_acquisition_phase_contract',
                                           'test_observation_only_report']
TEST_COUNT = previous.TEST_COUNT + 29


def case_shard_groups(groups, case_shard_modules):
    """Expand actual test identities, shared by source and package runners."""
    expanded=[]
    for group in groups:
        if len(group)==1 and group[0] in case_shard_modules:
            suite=unittest.defaultTestLoader.loadTestsFromName('tests.'+group[0])
            def test_ids(node):
                if isinstance(node,unittest.TestSuite):
                    return [ident for child in node for ident in test_ids(child)]
                ident=node.id()
                assert ident.startswith('tests.'),ident
                return [ident.removeprefix('tests.')]
            names=test_ids(suite)
            assert names and len(names)==suite.countTestCases() and len(set(names))==len(names)
            expanded.extend(names[start:start+4] for start in range(0,len(names),4))
        else:expanded.append(group)
    return expanded


def sharded_runner(original, *, isolated_modules=(), case_shard_modules=()):
    """Keep every isolated assertion; bound each serial shard below60seconds."""
    def run_shards(kind, location, root, output, modules, count):
        results = []
        groups, group = [], []
        for name in modules:
            if name in isolated_modules:
                if group: groups.append(group); group = []
                groups.append([name])
            else:
                group.append(name)
                if len(group)==8: groups.append(group); group = []
        if group: groups.append(group)
        groups=case_shard_groups(groups,case_shard_modules)
        for index, group in enumerate(groups, 1):
            expected = unittest.defaultTestLoader.loadTestsFromNames(['tests.' + name for name in group]).countTestCases()
            folder = output / ('shard-' + str(index))
            folder.mkdir(exist_ok=False)
            results.append(original(kind, location, root, folder, group, expected))
            preview = folder / 'financial-chart-preview.html'
            if preview.is_file():
                target = output / preview.name
                if target.exists():
                    assert target.read_bytes() == preview.read_bytes()
                else:
                    shutil.copyfile(preview, target)
        total = sum(item['tests_run'] for item in results)
        assert total == count
        merged = dict(results[0], tests_run=total, test_modules=modules, shards=results)
        for field in ('module_paths', 'module_code_origins'):
            merged[field] = {}
            for result in results:
                for name, value in result[field].items():
                    assert name not in merged[field] or merged[field][name] == value
                    merged[field][name] = value
        return merged
    return run_shards


def run(*, frozen=False):
    original = previous.MODULES, previous.TEST_COUNT
    try:
        previous.MODULES, previous.TEST_COUNT = MODULES, TEST_COUNT
        if frozen:
            from tests import accept_packaged_need_driven_materials as packaged
            from tests import accept_planning_effort_view as settings
            from tests.accept_phase_settings_navigation import run as settings_navigation
            factory = packaged.frozen_material_runner
            with patch.object(packaged, 'frozen_material_runner', lambda: sharded_runner(factory())), \
                    patch.object(settings, 'run', settings_navigation):
                return previous.run_frozen()
        from tests import accept_structured_financial_runtime as source
        with patch.object(source, 'run_packaged_tests', sharded_runner(source.run_packaged_tests)):
            return previous.run()
    finally:
        previous.MODULES, previous.TEST_COUNT = original


def run_frozen():
    return run(frozen=True)


if __name__ == '__main__':
    run()
