"""Independent Skill install: saved-HTML conversion and App-source parity."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,tempfile,zipfile
from tests import ROOT
from researchhelper.report_designer import render_html

HOST=r'''
import hashlib,json,socket,sys
from pathlib import Path
install,project=map(Path,sys.argv[1:3]);sys.path.insert(0,str(install))
from core.session import Session
from researchhelper.report_designer import render_html
from researchhelper.artifact_exporter import export_report

def denied(*args,**kwargs):raise AssertionError('No network in publication conversion')
socket.socket.connect=denied
content=json.loads((project/'cached-research.json').read_text());formats=[]
for profile in ('standard','detailed'):
    assert render_html(content,install,profile=profile)==(project/(profile+'.html')).read_text()
    for format in ('html','pdf','docx','xlsx','csv'):
        r=export_report(content,install,project/'result',profile=profile,format=format)
        assert Path(r['source_html']).is_file() and r['converted_from_saved_html']
        assert hashlib.sha256(Path(r['source_html']).read_bytes()).hexdigest()==r['source_html_sha256']
        assert hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['sha256']
        formats.append(profile+':'+format)
s=Session(project/'session');task=s.agent.create_case('etf');task=s.agent.start(task['id'])
assert task['status']=='已完成'
for format in ('html','pdf','docx','xlsx','csv'):
    assert s.export(task['id'],profile='detailed',format=format).is_file()
assert task['ledger']['used']['model_calls']==0 and task['ledger']['used']['data_requests']==0
print(json.dumps(dict(status='passed',formats=formats,installed_session_formats=5,source_html_parity=True,model_calls=0,data_requests=0)))
'''


def run():
    sys.path.insert(0,str(ROOT/'packaging'))
    from build_current import verify_current_build
    selected=Path(os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve();candidate=verify_current_build(selected,ROOT)
    out=Path(tempfile.mkdtemp(prefix='publication-skill-',dir=ROOT/'result/acceptance'))
    with tempfile.TemporaryDirectory(prefix='independent-',dir=out) as host:
        host=Path(host);install=host/'researchhelper';project=host/'project';project.mkdir()
        with zipfile.ZipFile(candidate['artifacts']['skill']['path']) as package:
            assert all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in package.namelist())
            package.extractall(host)
        manifest=json.loads((install/'manifest.json').read_text())
        for name,digest in manifest['files'].items():assert hashlib.sha256((install/name).read_bytes()).hexdigest()==digest
        content=json.loads((ROOT/'result/acceptance/one-sentence-research-vgjkyxq9/report.json').read_text())
        (project/'cached-research.json').write_text(json.dumps(content,ensure_ascii=False))
        for profile in ('standard','detailed'):(project/(profile+'.html')).write_text(render_html(content,ROOT,profile=profile))
        worker=host/'host.py';worker.write_text(HOST)
        policy='(version 1)(allow default)(deny network*)(deny file-write* (subpath '+json.dumps(str(install))+'))'
        for path in [ROOT/'core',ROOT/'modules',ROOT/'products',ROOT/'.skills',Path('/Users/haoranxu/Desktop/OptionHelper'),Path('/Users/haoranxu/Desktop/DataFetcher'),Path.home()/'Library/Application Support/ResearchHelper']:
            policy+='(deny file-read* (subpath '+json.dumps(str(path))+'))'
        result=subprocess.run(['/usr/bin/sandbox-exec','-p',policy,sys.executable,'-I','-B',str(worker),str(install),str(project)],cwd=project,capture_output=True,text=True,timeout=60)
        (out/'worker.log').write_text(result.stdout+result.stderr)
        if result.returncode:raise AssertionError(result.stderr[-1800:])
        record=json.loads(result.stdout)
        for name,digest in manifest['files'].items():assert hashlib.sha256((install/name).read_bytes()).hexdigest()==digest
    record.update(candidate=str(selected),temporary_project_removed=True,production_store_touched=False,installation_unchanged=True)
    receipt=out/'receipt.json';receipt.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print(json.dumps(dict(status=record['status'],receipt=str(receipt))))
    return receipt


if __name__=='__main__':run()
