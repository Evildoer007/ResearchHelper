"""Retain every prior App/Skill code check and add independent reader-five checks.

This is packaged-module regression, not native UI or live research acceptance.
It never installs a candidate, mutates a production archive, or starts a model.
"""
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from tests.build_report_reader_candidate import current_gate
from tests.packaged_runtime_tests import run_packaged_tests
from tests.review_report_reader_release import BASE, ROOT, PRIOR, digest, read, production_files

REPLACEMENTS = {
    'tests.test_document_report_index_offline.DocumentReportIndexTests.test_new_presentation_keeps_content_template_hash_and_saved_export':
        'tests.test_reader_five_units_offline.ReaderFiveFreezeTests.test_document_reader_five_keeps_content_hash_template_and_saved_export',
    'tests.test_pdf_reader_revision_4_offline.PDFReaderRevision4Tests.test_reader_revision_changes_no_frozen_content_template':
        'tests.test_reader_five_units_offline.ReaderFiveFreezeTests.test_pdf_reader_five_retains_exact_quote_and_original_export',
}
SUPERSEDED_GRAPH = 'tests.test_strategy_report_figures_offline.StrategyReportFigureTests.test_graph_is_adjacent_to_its_answer_and_only_rendered_once'


def test_ids(names):
    loader = unittest.TestLoader()
    def flatten(suite):
        for case in suite:
            if isinstance(case, unittest.TestSuite):
                yield from flatten(case)
            else:
                yield case.id()
    ids = list(flatten(loader.loadTestsFromNames(names)))
    assert not loader.errors
    return ids


def prior_groups():
    groups = []
    def visit(value):
        if isinstance(value, dict):
            if value.get('kind') == 'app' and 'test_modules' in value and 'tests_run' in value:
                ids = test_ids(value['test_modules'])
                assert len(ids) == value['tests_run']
                groups.append([REPLACEMENTS.get(identifier, identifier) for identifier in ids])
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    # Earlier summaries retained counts only. Resolve the actual per-test
    # package receipts rather than guessing their 184 test identifiers.
    receipts = ('host-package.json', 'checkpoint-package.json', 'analysis-package.json',
                'binding-package.json', 'process-package.json', 'reread-package.json',
                'preflight-package.json', 'history-package.json', 'economic-package.json',
                'dispatch-package.json')
    published = PRIOR.parent.parent/'published'
    for name in receipts:
        visit(read(published/name))
    assert sum(map(len, groups)) == 184
    visit(read(PRIOR))
    assert sum(map(len, groups)) == 574, 'Retain the complete previous packaged scope'
    return groups


def run():
    gate_path, gate = current_gate()
    builds = [(path, read(path)) for path in BASE.glob('build-*/receipt.json')]
    builds = [(path, value) for path, value in builds if value['status'] == 'built_not_accepted'
              and value['source_hashes'] == gate['source_hashes']]
    assert builds, 'Build a matching staged candidate first'
    build_path, build = max(builds, key=lambda item: item[0].stat().st_mtime_ns)
    stage = Path(build['stage'])
    groups = prior_groups()
    existing = {identifier for group in groups for identifier in group}
    extra = test_ids(['tests.test_report_visual_selection_offline',
                      'tests.test_strategy_report_figures_offline', 'tests.test_reader_five_units_offline',
                      'tests.test_fee_source_units_offline.FeeSourceUnitsTests.test_percent_originals_deliver_without_rewriting_or_annualizing',
                      'tests.test_fee_source_units_offline.FeeSourceUnitsTests.test_mixed_registered_representations_keep_each_original_unit'])
    extra = [identifier for identifier in extra if identifier not in existing and identifier != SUPERSEDED_GRAPH]
    assert len(extra) == len(set(extra))
    groups.append(extra)
    out = Path(tempfile.mkdtemp(prefix='package-', dir=BASE))
    production = production_files()
    before = {str(path): digest(path) for path in stage.rglob('*') if path.is_file()}
    receipt = {'status': 'failed', 'stage': str(stage), 'build_receipt': str(build_path),
               'build_receipt_sha256': digest(build_path), 'source_gate': str(gate_path),
               'source_hashes': gate['source_hashes'], 'version_replacements': REPLACEMENTS,
               'retained_executions_per_kind': 574, 'new_test_ids': extra,
               'contexts': [], 'real_model_calls': 0, 'real_supplier_calls': 0,
               'installed_app_updated': False, 'full_harness_complete': False,
               'scope': 'Packaged Python module regression only; native worker and UI not proved'}
    try:
        with tempfile.TemporaryDirectory(prefix='independent-skill-', dir=out) as folder:
            with ZipFile(stage/'ResearchHelper-Skill-0.10.0.zip') as archive:
                assert all(not Path(name).is_absolute() and '..' not in Path(name).parts for name in archive.namelist())
                archive.extractall(folder)
            backend = stage/'ResearchHelper.app/Contents/Resources/backend/researchhelper-server/researchhelper-server'
            for kind, location in [('skill', Path(folder)/'researchhelper'), ('app', backend)]:
                for index, group in enumerate(groups):
                    result = run_packaged_tests(kind, location, ROOT, out, group, len(group))
                    receipt['contexts'].append(result)
                    print(json.dumps({'kind': kind, 'group': index + 1, 'tests': len(group), 'status': 'passed'}), flush=True)
        assert production == production_files()
        assert all(digest(path) == value for path, value in before.items())
        assert current_gate()[1]['source_hashes'] == gate['source_hashes']
        receipt.update(status='passed', production_unchanged=True, candidate_unchanged=True,
                       executions_per_kind=574 + len(extra))
    except Exception as error:
        receipt['error'] = str(error)[:2000]
        raise
    finally:
        (out/'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'status': receipt['status'], 'receipt': str(out/'receipt.json')}), flush=True)
    return receipt


if __name__ == '__main__':
    run()
