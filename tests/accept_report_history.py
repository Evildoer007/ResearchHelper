"""Source or exact-DMG history UI acceptance, with a disposable offline Store."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,tempfile,threading,time,zipfile
from tests import ROOT
sys.path.insert(0,str(ROOT/'packaging'))
from accept_current import AppClient
from tests.accept_native_workbench_render import RenderRuntime
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import context,fingerprint
from researchhelper.service import make_server

REPORT=dict(title='原稿保留案例',conclusion='原值仅支持观察，解释须保留适用条件。',
    logics=[dict(title='核对原始依据',analysis='本次只检查历史记录，不产生市场判断。')])


SKILL_WORKER=r'''
from pathlib import Path
import json,sys
installation,project,input_path,result=map(Path,sys.argv[1:]);sys.path.insert(0,str(installation))
from core.session import Session
session=Session(project)
try:
    wire=json.loads(input_path.read_text());task=session.create('独立Skill历史更正验收')
    old=session.draft(task['id'],report=wire['report']);original=session.archive.report(task['id'],1)
    for reason in wire['notes']:session.correct_report(task['id'],reason,version=1,expected_digest=original['digest'])
    session.update(task['id']);assert [row['version'] for row in session.report_history(task['id'])]==[1]
    current=session.draft(task['id'],report={**wire['report'],'title':'修订后的案例'})
    history=session.report_history(task['id'])
    assert [row['version'] for row in history]==[2,1]
    assert not history[0]['corrections']
    assert [note['reason'] for note in history[1]['corrections']]==wire['notes']
    assert all(row['task_id']==task['id'] for row in history)
    assert all(row['digest']==session.archive.report(task['id'],row['version'])['digest'] for row in history)
    assert session.archive.report(task['id'],1)['html']==original['html']
    assert current['ledger']['used']['model_calls']==current['ledger']['used']['data_requests']==0
    assert session.tools(task['id'])['report_corrections']['history']=='report_history(task_id)'
    result.write_text(json.dumps(dict(status='passed',versions=[2,1],original_notes=wire['notes'],
        current_notes=[],old_report_unchanged=True,model_calls=0,data_requests=0),ensure_ascii=False))
finally:session.agent.close_runtime()
'''


def accept_skill_history(candidate,base,output,notes):
    from build_current import verify_current_build
    selected=verify_current_build(candidate,ROOT);artifact=selected['artifacts']['skill']
    archive=Path(artifact['path']);assert hashlib.sha256(archive.read_bytes()).hexdigest()==artifact['sha256']
    installation=base/'skill-installation'
    with zipfile.ZipFile(archive) as source:source.extractall(installation)
    def inventory():return {str(path.relative_to(installation)):hashlib.sha256(path.read_bytes()).hexdigest() for path in installation.rglob('*') if path.is_file()}
    before=inventory();worker=base/'skill-history.py';worker.write_text(SKILL_WORKER)
    wire=base/'skill-input.json';wire.write_text(json.dumps(dict(report=REPORT,notes=notes),ensure_ascii=False));target=base/'skill-result.json'
    policy='(version 1)(allow default)(deny network*)(deny file-write* (subpath '+json.dumps(str(installation))+'))'
    for denied in (ROOT/'core',ROOT/'modules',ROOT/'products',ROOT/'.skills',Path.home()/'Library/Application Support/ResearchHelper',Path('/Users/haoranxu/Desktop/OptionHelper'),Path('/Users/haoranxu/Desktop/DataFetcher')):
        policy+='(deny file-read* file-write* (subpath '+json.dumps(str(denied))+'))'
    done=subprocess.run(['/usr/bin/sandbox-exec','-p',policy,sys.executable,'-I','-B',str(worker),str(installation/'researchhelper'),
        str(base/'skill-project'),str(wire),str(target)],cwd=base,capture_output=True,text=True,timeout=60)
    (output/'skill.log').write_text(done.stdout+done.stderr)
    assert done.returncode==0,'Independent Skill history acceptance failed; inspect skill.log'
    result=json.loads(target.read_text());assert result['status']=='passed' and inventory()==before
    return dict(status='passed',versions=result['versions'],original_notes=len(result['original_notes']),current_notes=0,
        old_report_unchanged=True,network_denied=True,source_imports_denied=True,installation_readonly=True)


def run():
    candidate=os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
    default=ROOT/'result/acceptance/report-history-20261101'/('package' if candidate else 'source')
    output=Path(os.environ.get('RESEARCHHELPER_HISTORY_QA_OUTPUT',str(default))).resolve()
    if not output.is_relative_to(ROOT/'result/acceptance'):raise ValueError('History QA output must stay in this project acceptance directory')
    output.mkdir(parents=True,exist_ok=True)
    record=dict(status='running',model_calls=0,data_requests=0,production_store_touched=False,
        native_window_opened=False,synthetic_fixture=True)
    runtime=server=thread=None
    try:
        with tempfile.TemporaryDirectory(dir=output) as temporary:
            base=Path(temporary)
            try:
                if candidate:
                    runtime=RenderRuntime(base,record,candidate_path=candidate,discard_temporary_store=True);url=runtime.start().rstrip('/')
                else:
                    server=make_server(base/'store',ROOT,offline=True,authentication=True,preset_accounts=True,runtime_configurable=True)
                    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                    url='http://127.0.0.1:'+str(server.server_port);record['backend_mode']='development_source'
                client=AppClient(url);archive=ResearchArchive(base/'store')
                execution={key:client.bootstrap['execution'][key] for key in ('mode','policy_version')}
                tasks=[]
                def wait(ident,version):
                    deadline=time.monotonic()+25
                    while time.monotonic()<deadline:
                        value=client.call('/api/tasks/'+ident)
                        if value.get('delivery') and value['version']==version:return value
                        if value['status'] not in ('待开始','运行中'):raise RuntimeError(value.get('error') or value['status'])
                        time.sleep(.05)
                    raise RuntimeError('Offline fixture did not finish')
                for question in ('报告历史关联验收','独立历史对照任务'):
                    created=client.call('/api/tasks',dict(question=question));seeded=archive.get(created['id'])
                    seeded['synthetic']=True;seeded['messages'][0]['synthetic']=True;archive.save(seeded)
                    client.call('/api/tasks/'+created['id']+'/start',dict(expected_task=context(seeded),expected_execution=execution,
                        host_result=dict(data_requests=[],draft_report=REPORT)))
                    tasks.append(wait(created['id'],1))
                first=archive.report(tasks[0]['id'],1)
                notes=['现金流与归母净利的范围不同，原稿应保留适用限制。<svg onload="window.historyExecuted=true">',
                       '年度利润不能代替对应窗口的TTM分母。字段定位：'+('原始字段_'*28)]
                for reason in notes:
                    client.call('/api/tasks/'+tasks[0]['id']+'/report-correction',dict(version=1,reason=reason,expected_digest=first['digest']))
                client.call('/api/tasks/'+tasks[0]['id']+'/update',dict(expected_task=context(tasks[0]),expected_execution=execution,
                    host_result=dict(data_requests=[],draft_report={**REPORT,'title':'修订后的案例'})))
                current=wait(tasks[0]['id'],2)
                before={task['id']:fingerprint(archive.get(task['id'])) for task in tasks}
                fixture=output/'fixture.json';fixture.write_text(json.dumps(dict(ids=[t['id'] for t in tasks],notes=notes),ensure_ascii=False))
                env=dict(os.environ,RESEARCHHELPER_UI_BASE=url,RESEARCHHELPER_UI_OUTPUT=str(output),RESEARCHHELPER_UI_FIXTURE=str(fixture))
                with (output/'browser.log').open('w') as stream:
                    done=subprocess.run(['/usr/local/bin/node',str(ROOT/'tests/accept_report_history.mjs')],cwd=ROOT,env=env,
                        stdout=stream,stderr=subprocess.STDOUT,timeout=150)
                assert done.returncode==0,'History browser acceptance failed; inspect browser.log'
                browser=json.loads((output/'browser.json').read_text());assert browser['status']=='passed'
                assert before=={task['id']:fingerprint(archive.get(task['id'])) for task in tasks}
                assert archive.report(tasks[0]['id'],1)['html']==first['html']
                assert len(archive.correction_records(tasks[0]['id'],1))==2
                assert not archive.correction_records(tasks[0]['id'],2)
                if candidate:record['independent_skill']=accept_skill_history(candidate,base,output,notes)
                record.update(status='scoped_passed',checks=len(browser['checks']),frozen_original_unchanged=True,
                    tasks_and_ledgers_unchanged=True,notes_bound_to_original_version=True)
            finally:
                if runtime:runtime.stop()
                if server:
                    server.shutdown();server.server_close();thread.join(timeout=10);record['service_stopped']=not thread.is_alive()
        record['temporary_store_removed']=not Path(temporary).exists()
    except BaseException as error:record.update(status='failed',error_type=type(error).__name__,error=str(error));raise
    finally:
        receipt=output/'receipt.json';receipt.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(dict(status=record['status'],receipt=str(receipt))),flush=True)


if __name__=='__main__':run()
