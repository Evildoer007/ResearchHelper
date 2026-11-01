"""Actual comparison component, isolated source or frozen runtime; no study."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from tests.accept_native_workbench_render import RenderRuntime
from tests.accept_default_app_need_driven_filings import protected_configuration
from tests.accept_default_app_online_research import archive_snapshot

ROOT=Path(__file__).resolve().parents[1]
NODE=Path('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')
OH=Path('/Users/haoranxu/Desktop/OptionHelper/products/app/frontend/shared/app.js')


def run():
    output=Path(tempfile.mkdtemp(prefix='version-calculation-view-',dir=ROOT/'result/acceptance'))
    before=protected_configuration(),archive_snapshot(),hashlib.sha256(OH.read_bytes()).hexdigest()
    record=dict(status='failed',model_calls=0,provider_calls=0,native_window_opened=False,
        real_configuration_changed=False,research_accepted=False,scope='explicit_synthetic_component')
    runtime=RenderRuntime(output,record)
    try:
        url=runtime.start()
        environment=dict(os.environ,RESEARCHHELPER_PACKAGE_URL=url,RESEARCHHELPER_ACCEPTANCE_DIR=str(output))
        completed=subprocess.run([str(NODE),str(ROOT/'tests/accept_version_calculation_view.mjs')],
            cwd=ROOT,env=environment,capture_output=True,text=True,timeout=90)
        (output/'browser.log').write_text(completed.stdout+completed.stderr)
        record['browser']=json.loads((output/'browser-receipt.json').read_text())
        if completed.returncode==0 and record['browser']['status']=='passed':record['status']='scoped_passed'
    except Exception as error:
        record['error']=str(error)
    finally:
        runtime.stop()
        record['protected_unchanged']=before==(
            protected_configuration(),archive_snapshot(),hashlib.sha256(OH.read_bytes()).hexdigest())
        if not record['protected_unchanged']:record['status']='failed'
        target=output/'receipt.json';target.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print(json.dumps(dict(status=record['status'],checks=len(record.get('browser',{}).get('checks',[])),
        receipt=str(target),error=record.get('error')),ensure_ascii=False))
    if record['status']!='scoped_passed':raise SystemExit(1)
    return record


if __name__=='__main__':run()
