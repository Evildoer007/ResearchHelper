"""R186 source checks with retained data, scope, arithmetic and UI assertions."""
import hashlib,io,json,sys,tempfile,unittest
from pathlib import Path
from tests import ROOT
from tests.accept_draft_source_types import MODULES
from researchhelper.reasoning import RULES_VERSION


def run():
    assert RULES_VERSION==186
    sys.path.insert(0,str(ROOT/'packaging'))
    from source_snapshot import source_files
    modules=[name for name in MODULES if name!='tests.test_draft_first_workflow']+['tests.test_draft_writing_context','tests.test_draft_arithmetic',
        'tests.test_draft_visual_comparisons',
        'tests.test_working_capital_catalog','tests.test_working_capital_values','tests.test_demo_finder_metadata','tests.test_macro_draft_analysis','tests.test_macro_draft_layout','tests.test_visual_library','tests.test_draft_recovery_workflow','tests.test_fixed_financial_catalog_delivery.DeliveredCalibratedCatalogueTests']
    files=source_files(ROOT)+[Path(__file__),ROOT/'tests/test_working_capital_catalog.py',ROOT/'tests/test_working_capital_values.py',ROOT/'tests/test_demo_finder_metadata.py',ROOT/'tests/test_macro_draft_analysis.py',ROOT/'tests/test_draft_recovery_workflow.py',ROOT/'tests/test_draft_first_workflow.py',ROOT/'tests/test_draft_observation_order.py',ROOT/'tests/test_macro_draft_layout.py',ROOT/'tests/test_visual_library.py']
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    hashes={str(p.relative_to(ROOT)):digest(p) for p in files}
    suite=unittest.defaultTestLoader.loadTestsFromNames(modules);expected=suite.countTestCases();stream=io.StringIO()
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    unchanged=all(digest(ROOT/name)==value for name,value in hashes.items())
    passed=result.wasSuccessful() and result.testsRun==expected and unchanged and not result.skipped
    out=Path(tempfile.mkdtemp(prefix='visual-library-source-',dir=ROOT/'result/acceptance'))
    (out/'tests.log').write_text(stream.getvalue())
    receipt=out/'receipt.json';receipt.write_text(json.dumps(dict(status='scoped_passed' if passed else 'failed',
        rules_version=186,tests_run=result.testsRun,modules=modules,source_hashes=hashes,source_unchanged=unchanged,
        real_model_calls=0,real_data_requests=0,all_prd_accepted=False),ensure_ascii=False,indent=2))
    print(json.dumps(dict(status='scoped_passed' if passed else 'failed',checks=result.testsRun,receipt=str(receipt))))
    if not passed:raise AssertionError(stream.getvalue()[-8000:])
    return receipt


if __name__=='__main__':run()
