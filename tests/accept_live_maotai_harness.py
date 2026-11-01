"""One authorized native-App research run, with a persistent budget and no retry.

Only RH-owned settings are copied into this study's private store. The public
receipt never contains credentials; copied secrets are cleared after the run.
"""
from pathlib import Path
import hashlib,json,os,re,signal,subprocess,sys,time
from tests import ROOT
from researchhelper.model import ModelSettings
from researchhelper.settings import DataSettings
from researchhelper.runtime import ExecutionPolicy
from tests.accept_current_live_document_reading import PrivateAppClient,task_context

OUT=ROOT/'result/acceptance/live-maotai-harness-current'
QUESTION=('请研究贵州茅台600519.SH，对比2024年和2025年两个完整会计年度的营业收入、归母净利润、经营活动现金流，'
          '结合最新股价和市盈率分析基本面与估值是否匹配。请自动优先使用iFinD结构化接口取数，不默认下载PDF，'
          '形成2至3条有数据支撑的投资逻辑、反证条件和风险，生成带图表的标准报告。')
LIMITS={'data_requests':24,'model_calls':6,'seconds':480}


def native_backend_url(process):
    until=time.monotonic()+35
    children=[]
    while time.monotonic()<until:
        if process.poll() is not None:raise RuntimeError('RH原生测试实例提前退出')
        found=subprocess.run(['/usr/bin/pgrep','-P',str(process.pid)],capture_output=True,text=True)
        children=[int(s) for s in found.stdout.split() if s.isdecimal()]
        if children:
            ports=subprocess.run(['/usr/sbin/lsof','-nP','-a','-p',','.join(map(str,children)),'-iTCP','-sTCP:LISTEN','-Fn'],capture_output=True,text=True)
            match=re.search(r'^n127\.0\.0\.1:(\d+)$',ports.stdout,re.M)
            if match:return 'http://127.0.0.1:'+match.group(1)+'/',children
        time.sleep(.2)
    raise RuntimeError('RH原生实例未在35秒内提供回环服务')


def run():
    OUT.mkdir(parents=True,exist_ok=True);receipt=OUT/'receipt.json'
    if receipt.exists() and json.loads(receipt.read_text()).get('task_id'):
        raise RuntimeError('本研究已启动，须读取既有任务与预算，不重复创建或重试')
    production=Path.home()/'Library/Application Support/ResearchHelper/research-v1';store=OUT/'store'
    model=ModelSettings(production).read(private=True);sources=DataSettings(production).read(private=True)
    if model.get('model')!='deepseek-flash' or not model.get('api_key') or not sources['ifind'].get('access_token'):
        raise RuntimeError('当前RH独立Flash或iFinD配置不完整')
    ModelSettings(store).save({key:model[key] for key in ('endpoint','model','api_key','extraction_mode','reasoning_effort','planning_effort') if key in model})
    DataSettings(store).save({'ifind':sources['ifind']});ExecutionPolicy(store,configurable=True).save({'enabled':True,'confirm':True})
    secrets=[model['api_key']]+[v for v in sources['ifind'].values() if v]
    record=dict(status='starting',question=QUESTION,limits=LIMITS,model=model['model'],model_endpoint=model['endpoint'],
                native_app='/Applications/ResearchHelper.app',native_window_opened=True,production_store_touched=False,automatic_retry=False,paid_case_count=1)
    def save():
        text=json.dumps(record,ensure_ascii=False,indent=2)
        if any(secret in text for secret in secrets):raise RuntimeError('拒绝写入含凭据的验收记录')
        receipt.write_text(text)
    save();process=None;children=[];client=None;log=(OUT/'native.log').open('w')
    try:
        env=dict(os.environ,RESEARCHHELPER_STORE=str(store),PYTHONDONTWRITEBYTECODE='1')
        env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
        process=subprocess.Popen(['/Applications/ResearchHelper.app/Contents/MacOS/ResearchHelper'],cwd=OUT,env=env,stdout=log,stderr=log,start_new_session=True)
        base,children=native_backend_url(process);record['base_url']=base;record['native_pid']=process.pid;save()
        print(json.dumps(dict(stage='native_opened',base_url=base,limits=LIMITS,model=model['model']),ensure_ascii=False),flush=True)
        client=PrivateAppClient(base);boot=client.request('api/bootstrap')
        assert boot['execution']['mode']=='live'
        task=client.request('api/tasks',{'question':QUESTION,'research_mode':'current','limits':LIMITS})
        record.update(task_id=task['id'],status='running');save()
        task=client.request('api/tasks/'+task['id']+'/start',{'expected_task':task_context(task),'expected_execution':{k:boot['execution'][k] for k in ('mode','policy_version')}})
        deadline=time.monotonic()+490;last=''
        while task['status']=='运行中' and time.monotonic()<deadline:
            summary=json.dumps(dict(status=task['status'],stage=task.get('current_node',task.get('phase','')),used=task['ledger']['used'],evidence=len(task.get('evidence',[])),sources=len(task.get('source_records',[]))),ensure_ascii=False,sort_keys=True)
            if summary!=last:print(summary,flush=True);last=summary
            time.sleep(2);task=client.request('api/tasks/'+task['id'])
        if task['status']=='运行中':client.request('api/tasks/'+task['id']+'/cancel',{'expected_task':task_context(task)});task=client.request('api/tasks/'+task['id'])
        serialized=json.dumps(task,ensure_ascii=False,indent=2)
        if any(secret in serialized for secret in secrets):raise RuntimeError('任务响应意外包含凭据，未落盘')
        (OUT/'task.json').write_text(serialized)
        record.update(status=task['status'],ledger=task['ledger'],source_count=len(task.get('source_records',[])),evidence_count=len(task.get('evidence',[])),draft=bool(task.get('draft')),delivery=task.get('delivery'),gaps=task.get('gaps',[]))
        if task.get('delivery'):
            report=client.request('api/tasks/'+task['id']+'/report?profile=standard',html=True)
            destination=ROOT/'result/reports/maotai-20261028';destination.mkdir(parents=True,exist_ok=True);target=destination/'贵州茅台研究报告.html';target.write_text(report)
            detailed=client.request('api/tasks/'+task['id']+'/report?profile=detailed',html=True);(destination/'贵州茅台详细报告.html').write_text(detailed)
            record['report']=str(target);record['report_sha256']=hashlib.sha256(report.encode()).hexdigest()
        save();print(json.dumps(dict(status=record['status'],task_id=record['task_id'],sources=record['source_count'],evidence=record['evidence_count'],used=record['ledger']['used'],report=record.get('report'),receipt=str(receipt)),ensure_ascii=False),flush=True)
    except BaseException as error:
        record.update(status='test_failed',error=type(error).__name__+': '+str(error));save();raise
    finally:
        if client:
            try:client.request('api/settings/execution',{'enabled':False,'confirm':True})
            except Exception:pass
        if process and process.poll() is None:
            for child in children:
                try:os.kill(child,signal.SIGTERM)
                except ProcessLookupError:pass
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        log.close()
        settings=ModelSettings(store).read()
        try:ModelSettings(store).save({'endpoint':settings['endpoint'],'model':settings['model'],'clear_key':True})
        finally:DataSettings(store).save({'ifind':{'clear_access_token':True,'clear_refresh_token':True}})
        record.update(copied_credentials_cleared=True,native_test_closed=process is None or process.poll() is not None);save()
    return record

if __name__=='__main__':run()
