"""Run the real App backend with an isolated Store, source or explicit DMG."""
import json,os,subprocess,tempfile
from pathlib import Path
from tests import ROOT
from tests.accept_native_workbench_render import RenderRuntime


def run():
    out=Path(tempfile.mkdtemp(prefix='publication-workflow-',dir=ROOT/'result/acceptance'))
    record=dict(status='failed',native_window_opened=False,production_store_touched=False,system_dialog_presented=False,model_calls=0,real_data_requests=0)
    runtime=RenderRuntime(out,record)
    try:
        base=runtime.start();env=dict(os.environ,RESEARCHHELPER_PACKAGE_URL=base,RESEARCHHELPER_ACCEPTANCE_DIR=str(out))
        result=subprocess.run(['node',str(ROOT/'tests/accept_publication_workflow.mjs')],cwd=ROOT,env=env,capture_output=True,text=True,timeout=120)
        (out/'browser.log').write_text(result.stdout+result.stderr);record['browser']=json.loads((out/'publication-browser.json').read_text())
        assert result.returncode==0,record['browser'].get('error');record['status']='scoped_passed'
    finally:
        runtime.stop();(out/'receipt.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
        print(json.dumps(dict(status=record['status'],receipt=str(out/'receipt.json'))))
    return out


if __name__=='__main__':run()
