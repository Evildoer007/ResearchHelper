"""Source gate for R178 raw-source coverage and report presentation."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from tests import ROOT
from researchhelper.reasoning import RULES_VERSION

MODULES = ['tests.test_draft_source_types', 'tests.test_draft_figures',
    'tests.test_draft_identity_scope', 'tests.test_multimarket_identity_candidates',
    'tests.test_draft_first_workflow', 'tests.test_revision_readiness.RevisionReadinessProtocolTests',
    'tests.test_supported_answer_with_pending_needs']


def run():
    assert RULES_VERSION == 178
    sys.path.insert(0, str(ROOT/'packaging'))
    from source_snapshot import source_files
    files = source_files(ROOT) + [Path(__file__), ROOT/'tests/test_draft_source_types.py',
        ROOT/'tests/test_draft_figures.py', ROOT/'tests/test_draft_first_workflow.py', ROOT/'tests/test_draft_identity_scope.py']
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    hashes = {str(path.relative_to(ROOT)): digest(path) for path in files}
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    expected = suite.countTestCases(); stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    same = all(digest(ROOT/name) == value for name, value in hashes.items())
    passed = result.wasSuccessful() and result.testsRun == expected and same and not result.skipped
    output = Path(tempfile.mkdtemp(prefix='draft-source-types-', dir=ROOT/'result/acceptance'))
    (output/'tests.log').write_text(stream.getvalue()[-32000:])
    receipt = output/'receipt.json'
    receipt.write_text(json.dumps(dict(status='scoped_passed' if passed else 'failed', rules_version=178,
        tests_run=result.testsRun, modules=MODULES, source_hashes=hashes, source_unchanged=same,
        real_model_calls=0, real_data_requests=0, original_research_resumed=False,
        scope='retained_source_shape_replay_and_first_report_not_current_permissions',
        all_prd_accepted=False), ensure_ascii=False, indent=2))
    print(json.dumps(dict(status='scoped_passed' if passed else 'failed', checks=result.testsRun, receipt=str(receipt))))
    if not passed:
        raise AssertionError(stream.getvalue()[-8000:])
    return receipt


if __name__ == '__main__':
    run()
