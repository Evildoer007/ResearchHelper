"""Inspect ordinary ResChat submissions without executing a research task.

Use the real authenticated workspace, task API and material archive in a fresh
offline Store. The browser stops the start request at the dispatch boundary.
This proves input fidelity, not live model quality or report completion.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from tests.accept_native_workbench_render import RenderRuntime
from tests.accept_default_app_need_driven_filings import protected_configuration
from tests.accept_default_app_online_research import archive_snapshot
from tests.accept_default_app_table_financial_research import QUESTION


ROOT = Path(__file__).resolve().parents[1]
NODE = Path('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')


def run():
    output = Path(tempfile.mkdtemp(prefix='reschat-submission-', dir=ROOT / 'result/acceptance'))
    before_config, before_archive = protected_configuration(), archive_snapshot()
    script = ROOT / 'tests/accept_reschat_submission.mjs'
    before_script = hashlib.sha256(script.read_bytes()).hexdigest()
    record = dict(status='failed', scope='ordinary_reschat_input_and_dispatch_boundary',
                  model_calls=0, data_requests=0, native_window_opened=False,
                  production_store_touched=False, research_accepted=False,
                  bootstrap_mocked=False, facts_or_plan_injected=False)
    runtime = RenderRuntime(output, record)
    try:
        base = runtime.start()
        environment = dict(os.environ, RESEARCHHELPER_PACKAGE_URL=base,
                           RESEARCHHELPER_ACCEPTANCE_DIR=str(output),
                           RESEARCHHELPER_SUBMISSION_QUESTION=QUESTION)
        completed = subprocess.run([str(NODE), str(script)], cwd=ROOT,
                                   env=environment, capture_output=True, text=True, timeout=90)
        (output / 'browser.log').write_text(completed.stdout + completed.stderr)
        record['browser'] = json.loads((output / 'browser-receipt.json').read_text())
        if completed.returncode == 0 and record['browser']['status'] == 'passed':
            record['status'] = 'scoped_passed'
    except Exception as error:
        record['error'] = str(error)
    finally:
        runtime.stop()
        record.update(configuration_unchanged=before_config == protected_configuration(),
                      production_archive_unchanged=before_archive == archive_snapshot(),
                      test_script_unchanged=before_script == hashlib.sha256(script.read_bytes()).hexdigest())
        if not all(record[key] for key in ('configuration_unchanged',
                                          'production_archive_unchanged', 'test_script_unchanged')):
            record['status'] = 'failed'
        receipt = output / 'receipt.json'
        receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(dict(status=record['status'], receipt=str(receipt),
                          checks=len(record.get('browser', {}).get('checks', [])),
                          error=record.get('error')), ensure_ascii=False))
    if record['status'] != 'scoped_passed':
        raise SystemExit(1)
    return record


if __name__ == '__main__':
    run()
