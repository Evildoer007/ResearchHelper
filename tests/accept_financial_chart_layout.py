"""Re-render immutable real content; never reopen or rerun its study."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from tests import ROOT
from researchhelper.visual import render

CASE=ROOT/'result/acceptance/skill-host-financial-20261015-v2'
SOURCE=CASE/'result/researchhelper/rh_1cd38423decc4c6b9b46ea53ec82f05b/research-v1.json'
NODE=Path('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(frozen_html=None):
    output=Path(tempfile.mkdtemp(prefix='financial-chart-layout-',dir=ROOT/'result/acceptance'))
    protected={str(path):digest(path) for path in (SOURCE,SOURCE.with_suffix('.html'),CASE/'final-task.json')}
    content=json.loads(SOURCE.read_text())
    report=output/'report.html'
    if frozen_html is None:report.write_text(render(content,ROOT))
    else:shutil.copyfile(frozen_html,report)
    manifest=dict(report=str(report),content=str(SOURCE),values=[item['value'] for item in content['evidence']],
                  source_hashes=protected,render_mode='source' if frozen_html is None else 'frozen')
    (output/'input.json').write_text(json.dumps(manifest,ensure_ascii=False))
    environment=dict(os.environ,RESEARCHHELPER_CHART_REVIEW=str(output/'input.json'))
    completed=subprocess.run([str(NODE),str(ROOT/'tests/accept_financial_chart_layout.mjs')],
        cwd=ROOT,env=environment,capture_output=True,text=True,timeout=90)
    (output/'browser.log').write_text(completed.stdout+completed.stderr)
    browser=json.loads((output/'browser-receipt.json').read_text())
    unchanged=all(digest(path)==expected for path,expected in protected.items())
    record=dict(status='scoped_passed' if completed.returncode==0 and unchanged else 'failed',
        render_mode=manifest['render_mode'],browser=browser,source_hashes=protected,
        frozen_content_unchanged=unchanged,model_calls=0,data_requests=0,
        study_reopened=False,app_model_quality_accepted=False)
    target=output/'receipt.json';target.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print(json.dumps(dict(status=record['status'],receipt=str(target),failures=browser.get('failures'),
                         error=browser.get('error')),ensure_ascii=False),flush=True)
    return record


if __name__=='__main__':run()
