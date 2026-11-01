"""One release batch: offline gates, install, one bounded native study, sync.

Uses existing release/install verifiers without weakening them. A submitted
native study is never automatically repeated. Cleanup touches only generated
payloads with receipts; source, production research and current demos remain.
"""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
PYTHON='/opt/anaconda3/envs/MachineLearning/bin/python'
NODE='/usr/local/bin/node'
PREVIOUS=ROOT/'result/build-candidates/20261029-015746-256723/candidate.json'
HELPERS=ROOT/'result/acceptance/native-notebook-20261029-015746-256723'
QUESTION='请研究贵州茅台600519.SH：比较2024年和2025年的营业收入、归母净利润、经营现金流，查看最近交易日收盘价和PE(TTM)，给出3条有数据依据的投资逻辑并生成标准报告。优先iFinD，不下载PDF；保留数据期间和单位，区分事实与判断，不把合并现金流下降直接等同于主营回款恶化。'
STORE=Path.home()/'Library/Application Support/ResearchHelper/research-v1'


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def run():
    output=ROOT/'result/acceptance'/('resdesk-closeout-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    output.mkdir(parents=True)
    record=dict(status='running',previous_candidate=str(PREVIOUS),model_call_limit=2,automatic_paid_retry=False,
        original_tests_modified=False,oh_modified=False,checks={},cleanup=[],pid=os.getpid())
    receipt=output/'receipt.json'
    def save():receipt.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    def phase(name):record['phase']=name;save();print(name,flush=True)
    def remove(path,category):
        assert path.exists() and not path.is_symlink()
        amount=path.stat().st_size if path.is_file() else sum(p.stat().st_size for p in path.rglob('*') if p.is_file() and not p.is_symlink())
        shutil.rmtree(path) if path.is_dir() else path.unlink()
        record['cleanup'].append(dict(path=str(path),bytes=amount,category=category));save()
    def extras():
        known=set()
        for file in (ROOT/'result/build-candidates').glob('*/candidate.json'):
            try:
                data=json.loads(file.read_text())
                known.update(v['sha256'] for v in data.get('artifacts',{}).values() if isinstance(v,dict) and 'sha256' in v)
            except (ValueError,OSError):pass
        for name in ['ResearchHelper-0.10.0-arm64 2.dmg','ResearchHelper-Skill-0.10.0 2.zip']:
            path=ROOT/'dist'/name
            if path.exists():
                assert (ROOT/'dist'/name.replace(' 2.','.')).is_file()
                assert digest(path) in known,'Unknown extra copy is preserved'
                remove(path,'known_extra_build_copy')
    def command(args,label,env=None,timeout=300):
        p=subprocess.run(args,cwd=ROOT,env=env,capture_output=True,text=True,timeout=timeout)
        (output/(label+'.log')).write_text(p.stdout+p.stderr)
        if p.returncode:raise RuntimeError(label+' failed; see '+str(output/(label+'.log')))
        return p.stdout
    def module(name,env,label):
        text=command([PYTHON,'-B','-m',name],label,env)
        for line in reversed(text.splitlines()):
            try:
                value=json.loads(line)
                if isinstance(value,dict) and value.get('receipt'):return Path(value['receipt'])
            except ValueError:pass
        raise RuntimeError('No receipt from '+name)
    references={str(path):digest(path) for path in [Path('/Users/haoranxu/Desktop/OptionHelper/products/app/frontend/shared')/name for name in ['styles.css','refinement.css','theme-overrides.css','choice-controls.css']]}
    record['oh_reference_hashes']=references;save()
    try:
        phase('清理本轮临时产物')
        for name in ['native-notebook-20261029-004705','native-notebook-20261029-015746-256723']:
            path=ROOT/'result/acceptance'/name/'compile'
            if path.exists():remove(path,'temporary_compiler_cache')
        extras()
        phase('构建一次最终候选')
        selected=os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
        if selected:candidate=Path(selected).resolve()
        else:
            text=command([PYTHON,'-B','-I','packaging/build_current.py'],'build',timeout=360)
            match=re.search(r'^回执：(.*candidate\.json)$',text,re.M)
            if not match:raise RuntimeError('Build did not return an exact candidate')
            candidate=Path(match.group(1))
        record['candidate']=str(candidate);save()
        env=dict(os.environ,RESEARCHHELPER_CURRENT_RECEIPT=str(candidate))
        phase('核验实际DMG和独立Skill')
        extras();package=module('tests.accept_oh_current_package',env,'package')
        record['checks']['package']=str(package);save()
        phase('核验五视图、PE溯源及Desk追问关联')
        extras();browser=module('tests.accept_resdesk_completion',env,'browser')
        record['checks']['browser']=str(browser);save()
        phase('安装已核验候选')
        with sqlite3.connect((STORE/'researchhelper.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
            assert not any(json.loads(row[0]).get('status')=='运行中' for row in db.execute('SELECT body FROM tasks'))
        subprocess.run(['/usr/bin/osascript','-e','tell application "ResearchHelper" to quit'],check=True,capture_output=True)
        sys.path.insert(0,str(ROOT/'packaging/app/macos'))
        from install_app import install,require_closed,recorded_manifest,verify_bundle
        for attempt in range(25):
            try:require_closed();break
            except RuntimeError:
                if attempt==24:raise
                time.sleep(.2)
        extras();installed=install(candidate,previous_candidate=PREVIOUS)
        record['checks']['installation']=installed['receipt'];save()
        subprocess.run(['/usr/bin/open','-g','/Applications/ResearchHelper.app'],check=True)
        time.sleep(3)
        phase('原生App一次实测，最多2次模型调用')
        native=ROOT/'result/acceptance'/('native-notebook-'+candidate.parent.name)
        native.mkdir(exist_ok=True)
        copied=[]
        for name in ['native-accessibility-stable','native-accessibility-stable.swift','native-pointer','native-pointer.swift']:
            source=HELPERS/name;target=native/name
            if not target.exists():shutil.copy2(source,target)
            assert digest(source)==digest(target)
            copied.append(dict(path=str(target),source=str(source),sha256=digest(target)))
        (native/'helper-provenance.json').write_text(json.dumps(copied,ensure_ascii=False,indent=2))
        session=native/'session.json'
        if session.exists() and json.loads(session.read_text()).get('paid_submission_started'):
            assert json.loads(session.read_text()).get('status')=='scoped_passed','Existing paid run is inspected, never replayed'
        else:command([PYTHON,'-B','-m','tests.accept_native_notebook'],'native',dict(env,RESEARCHHELPER_NATIVE_QUESTION=QUESTION),timeout=260)
        result=json.loads(session.read_text());assert result['status']=='scoped_passed'
        assert 'all explicitly requested financial, price and PE fields acquired' in result['checks']
        record['checks']['native']=str(session);record['actual_task_id']=result['task_id'];save()
        phase('回读App冻结报告并独立核对计算')
        from core.bootstrap import configure_imports
        configure_imports()
        from researchhelper.archive_codec import decode_body
        from decimal import Decimal
        with sqlite3.connect((STORE/'researchhelper.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
            task=decode_body(db,db.execute("SELECT body FROM tasks WHERE json_extract(body,'$.id')=?",(result['task_id'],)).fetchone()[0])
            html=db.execute('SELECT html FROM deliveries WHERE task_id=? AND version=?',(task['id'],task['delivery']['version'])).fetchone()[0]
        assert hashlib.sha256(html.encode()).hexdigest()==task['delivery']['html_sha256']
        facts={fact['id']:fact for fact in task['draft_report']['facts']};checked=[]
        for calc in task['draft_report']['calculations']:
            if calc['method']=='raw_change_v1' and len(calc['data_refs'])==2:
                rows=sorted([facts[i] for i in calc['data_refs']],key=lambda r:str(r['period']))
                start,end=(Decimal(str(row['value'])) for row in rows)
                assert abs((end-start)/start*100-Decimal(str(calc['value'])))<Decimal('.000001')
                checked.append(calc['id'])
        assert len(checked)>=3
        folder=ROOT/'result/reports/gui-maotai-20261029';folder.mkdir(exist_ok=True)
        report=folder/'贵州茅台研究报告.html';report.write_text(html)
        frozen=dict(task_id=task['id'],candidate=str(candidate),version=task['delivery']['version'],html_sha256=task['delivery']['html_sha256'],
            native_session=str(session),facts=len(facts),logics=len(task['draft_report']['logics']),registered_arithmetic_checked=checked,
            model_calls=task['ledger']['used']['model_calls'],data_requests=task['ledger']['used']['data_requests'],status=task['status'],
            known_limitations=[n['message'] for n in task.get('draft_notices',[])],financial_quality_accepted=False)
        (folder/'receipt.json').write_text(json.dumps(frozen,ensure_ascii=False,indent=2))
        record['checks']['report']=str(report);record['case_summary']=frozen;save()
        phase('同步App同版Skill与两份demo')
        extras();sys.path.insert(0,str(ROOT/'packaging/demo'))
        from sync_demo import sync_current
        synced=sync_current(candidate,package,browser);record['checks']['demo_sync']=synced['receipt'];save()
        text=command([NODE,'tests/accept_refined_demos.mjs'],'demos',timeout=240)
        record['checks']['offline_demos']=json.loads(text.splitlines()[-1])['receipt'];save()
        phase('永久清理本轮编译与被替代的构建副本')
        for child in ['compile']:
            path=native/child
            if path.exists():remove(path,'temporary_native_compiler_cache')
        current=json.loads(candidate.read_text());retirement=current.get('release_retirement',{}).get('receipt')
        if retirement:
            retired=json.loads(Path(retirement).read_text());strings=[]
            def walk(value):
                if isinstance(value,dict):
                    for item in value.values():walk(item)
                elif isinstance(value,list):
                    for item in value:walk(item)
                elif isinstance(value,str) and value.startswith(str(Path.home()/'.Trash')+'/ResearchHelper-releases-'):strings.append(Path(value))
            walk(retired)
            if strings:
                base=min(strings,key=lambda p:len(p.parts))
                for item in retired['targets']:
                    path=base/Path(item['path']).relative_to(ROOT)
                    if not path.exists():continue
                    if item.get('directory'):
                        expected=item['directory']['files'];actual={str(p.relative_to(path)) for p in path.rglob('*') if p.is_file()}
                        assert actual==set(expected) and all(digest(path/name)==sha for name,sha in expected.items())
                    else:assert digest(path)==item['sha256']
                    remove(path,'superseded_verified_build_payload')
        recovery=Path(installed['recovery'])
        if recovery.exists():
            assert recovery.name=='ResearchHelper.app' and recovery.parent.name.startswith('ResearchHelper-installed-app-')
            verify_bundle(recovery,recorded_manifest(PREVIOUS));remove(recovery,'superseded_verified_app_bundle')
        extras()
        for path,expected in references.items():assert digest(Path(path))==expected
        # A source-era legacy assertion expects RULES_VERSION=24. The previous
        # delivered Skill and current source both use186; it is unchanged here.
        record['preexisting_legacy_check']=dict(test='core.tests.test_followup_context_offline.FollowUpContextTests.test_conversation_protocol_version_is_separate_and_recorded',expected=24,previous_delivered=186,current=186)
        record.update(status='scoped_passed',all_prd_accepted=False,financial_quality_accepted=False)
        phase('当前版本验收完成')
    except Exception as error:
        record.update(status='failed',error=str(error));save();raise
    finally:
        save();print(json.dumps(dict(status=record['status'],phase=record.get('phase'),candidate=record.get('candidate'),receipt=str(receipt),error=record.get('error')),ensure_ascii=False),flush=True)


if __name__=='__main__':run()
