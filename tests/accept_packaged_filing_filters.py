"""Run the same filing and identity assertions against actual frozen App code."""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'packaging')]
from build_current import verify_current_build
from tests.accept_native_workbench_render import RenderRuntime
from tests.accept_delivered_ifind_no_data import run_packaged_tests
from tests.accept_filing_filter_runtime import MODULES, TEST_COUNT


def run():
    selected = Path(os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve()
    candidate = verify_current_build(selected, ROOT)
    output = Path(tempfile.mkdtemp(prefix='packaged-filing-filters-', dir=ROOT / 'result/acceptance'))
    record = dict(status='failed', candidate=str(selected), model_calls=0, provider_calls=0,
        native_window_opened=False, production_store_touched=False, installed=False,
        financial_quality_accepted=False)
    runtime = RenderRuntime(output, record)
    try:
        runtime.start()
        executable = runtime.mount / 'ResearchHelper.app/Contents/Resources/backend/researchhelper-server/researchhelper-server'
        record['app'] = run_packaged_tests('app', executable, ROOT, output, MODULES, TEST_COUNT)
        assert verify_current_build(selected, ROOT) == candidate
        record['status'] = 'scoped_passed'
    except Exception as error:
        record['error'] = str(error)
    finally:
        runtime.stop()
        target = output / 'receipt.json'
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(dict(status=record['status'], receipt=str(target), error=record.get('error')), ensure_ascii=False))
    if record['status'] != 'scoped_passed':
        raise SystemExit(1)


if __name__ == '__main__':
    run()
