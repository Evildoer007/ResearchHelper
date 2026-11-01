"""Real settings forms and API, isolated Store, no real model execution."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from tests import ROOT
from tests.accept_native_workbench_render import RenderRuntime
from tests.accept_default_app_need_driven_filings import protected_configuration
from tests.accept_default_app_online_research import archive_snapshot

NODE=Path('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')
OH=Path('/Users/haoranxu/Desktop/OptionHelper/products/app/frontend')


def run():
    output=Path(tempfile.mkdtemp(prefix='planning-effort-view-',dir=ROOT/'result/acceptance'))
    before=protected_configuration(),archive_snapshot()
    references={str(OH/name):hashlib.sha256((OH/name).read_bytes()).hexdigest()
                for name in ('shared/choice-controls.css','settings/general-settings.css')}
    record=dict(status='failed',native_window_opened=False,model_calls=0,provider_calls=0,
                real_configuration_changed=False,research_accepted=False,oh_references=references)
    runtime=RenderRuntime(output,record)
    try:
        url=runtime.start()
        environment=dict(os.environ,RESEARCHHELPER_PACKAGE_URL=url,RESEARCHHELPER_ACCEPTANCE_DIR=str(output))
        completed=subprocess.run([str(NODE),str(ROOT/'tests/accept_planning_effort_view.mjs')],
            cwd=ROOT,env=environment,capture_output=True,text=True,timeout=120)
        (output/'browser.log').write_text(completed.stdout+completed.stderr)
        record['browser']=json.loads((output/'browser-receipt.json').read_text())
        if completed.returncode==0 and record['browser']['status']=='passed':record['status']='scoped_passed'
    except Exception as error:
        record['error']=str(error)
    finally:
        runtime.stop()
        record.update(production_unchanged=before==(protected_configuration(),archive_snapshot()),
            oh_unchanged=all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==value for path,value in references.items()))
        if not record['production_unchanged'] or not record['oh_unchanged']:record['status']='failed'
        target=output/'receipt.json';target.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print(json.dumps(dict(status=record['status'],receipt=str(target),
        checks=len(record.get('browser',{}).get('checks',[])),error=record.get('error')),ensure_ascii=False))
    if record['status']!='scoped_passed':raise SystemExit(1)
    return record


if __name__=='__main__':
    run()
