"""Render the actual progress component with explicitly synthetic states."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from tests.accept_native_workbench_render import RenderRuntime
from tests.accept_default_app_need_driven_filings import protected_configuration
from tests.accept_default_app_online_research import archive_snapshot

ROOT = Path(__file__).resolve().parents[1]
NODE = Path('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')
OH_REFERENCE = Path('/Users/haoranxu/Desktop/OptionHelper/products/app/frontend/shared/app.js')


def run():
    output = Path(tempfile.mkdtemp(prefix='model-progress-view-', dir=ROOT / 'result/acceptance'))
    before = protected_configuration(), archive_snapshot(), hashlib.sha256(OH_REFERENCE.read_bytes()).hexdigest()
    record = dict(status='failed', scope='synthetic_states_in_actual_progress_component',
                  model_calls=0, data_requests=0, production_store_touched=False,
                  native_window_opened=False, research_accepted=False)
    runtime = RenderRuntime(output, record)
    try:
        url = runtime.start()
        environment = dict(os.environ, RESEARCHHELPER_PACKAGE_URL=url,
                           RESEARCHHELPER_ACCEPTANCE_DIR=str(output))
        completed = subprocess.run([str(NODE), str(ROOT / 'tests/accept_model_progress_view.mjs')],
                                  cwd=ROOT, env=environment, capture_output=True, text=True, timeout=90)
        (output / 'browser.log').write_text(completed.stdout + completed.stderr)
        record['browser'] = json.loads((output / 'browser-receipt.json').read_text())
        if completed.returncode == 0 and record['browser']['status'] == 'passed':
            record['status'] = 'scoped_passed'
    except Exception as error:
        record['error'] = str(error)
    finally:
        runtime.stop()
        after = protected_configuration(), archive_snapshot(), hashlib.sha256(OH_REFERENCE.read_bytes()).hexdigest()
        record.update(configuration_unchanged=before[0] == after[0],
                      production_archive_unchanged=before[1] == after[1], oh_reference_unchanged=before[2] == after[2])
        if before != after:
            record['status'] = 'failed'
        receipt = output / 'receipt.json'
        receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(dict(status=record['status'], receipt=str(receipt),
                          checks=len(record.get('browser', {}).get('checks', [])), error=record.get('error')),
                     ensure_ascii=False))
    if record['status'] != 'scoped_passed':
        raise SystemExit(1)
    return record


if __name__ == '__main__':
    run()
