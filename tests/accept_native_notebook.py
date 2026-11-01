"""Installed App acceptance through macOS Accessibility only.

One explicit natural question, two model calls maximum. No HTTP, JavaScript,
evidence injection, credential copying, or automatic paid retry. SQLite is
opened read-only solely to inspect the result created through the actual UI.
"""
from pathlib import Path
from datetime import datetime
import json,sqlite3,subprocess,time,importlib.util,os

ROOT=Path(__file__).resolve().parents[1]
OPERATOR=ROOT/'result/acceptance/native-gui-current/operator'
DB=Path.home()/'Library/Application Support/ResearchHelper/research-v1/researchhelper.sqlite3'
CANDIDATE=Path(os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT',ROOT/'result/build-candidates/20261029-004705-926851/candidate.json')).resolve()
OUTPUT=ROOT/'result/acceptance'/('native-notebook-'+CANDIDATE.parent.name)
QUESTION=os.environ.get('RESEARCHHELPER_NATIVE_QUESTION','研究贵州茅台600519.SH，比较2024年和2025年营业收入、归母净利润及经营现金流，列出最近交易日收盘价和PE(TTM)，形成3条有数据支撑的投资逻辑并生成标准报告。优先复用适用的iFinD缓存，不下载PDF、不扩展到其他标的、不查询长周期行情。保留单位和期间，区分事实与判断，不将合并现金流变化直接归因于主营回款。')


def tasks():
    with sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True) as db:
        return [json.loads(row[0]) for row in db.execute('SELECT body FROM tasks')]


def run():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    guard=OUTPUT/'session.json'
    if guard.exists() and json.loads(guard.read_text()).get('paid_submission_started'):
        raise RuntimeError('This native session already submitted research; no automatic paid retry')
    table=subprocess.check_output(['/bin/ps','-axo','pid=,comm='],text=True)
    pids=[int(line.split()[0]) for line in table.splitlines() if line.strip().endswith('/Applications/ResearchHelper.app/Contents/MacOS/ResearchHelper')]
    assert len(pids)==1
    pid=pids[0]
    setter=OUTPUT/'native-accessibility-stable'
    if not setter.exists():
        spec=importlib.util.spec_from_file_location('rh_native_build',ROOT/'packaging/app/build_app.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        builder.OUTPUT=OUTPUT/'compile';builder.OUTPUT.mkdir(exist_ok=True)
        probe=OUTPUT/'native-accessibility-stable.swift'
        source=(ROOT/'packaging/tests/native_app_accessibility.swift').read_text()
        source=source.replace('print(String(data: value, encoding: .utf8)!)','print(String(data: value, encoding: .utf8)!)\n    RunLoop.main.run(until: Date().addingTimeInterval(0.35))')
        probe.write_text(source)
        result=subprocess.run(['/usr/bin/xcrun','swiftc',*builder.swift_options(),str(probe),'-framework','AppKit','-framework','ApplicationServices','-o',str(setter)],capture_output=True,text=True,timeout=90)
        assert result.returncode==0,result.stderr[:500]
    clicker=OUTPUT/'native-pointer'
    if not clicker.exists():
        spec=importlib.util.spec_from_file_location('rh_pointer_build',ROOT/'packaging/app/build_app.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        builder.OUTPUT=OUTPUT/'compile';builder.OUTPUT.mkdir(exist_ok=True)
        source=(ROOT/'tests/native_ui_operator.swift').read_text()
        before='for kind in [CGEventType.leftMouseDown,CGEventType.leftMouseUp] {'
        after='CGWarpMouseCursorPosition(point); CGEvent(mouseEventSource:nil,mouseType:.mouseMoved,mouseCursorPosition:point,mouseButton:.left)?.post(tap:.cghidEventTap); Thread.sleep(forTimeInterval:0.15)\n    '+before
        assert before in source
        source=source.replace(before,after).replace('output(["action":"mouse_click"','Thread.sleep(forTimeInterval:0.3); output(["action":"mouse_click"')
        probe=OUTPUT/'native-pointer.swift';probe.write_text(source)
        result=subprocess.run(['/usr/bin/xcrun','swiftc',*builder.swift_options(),str(probe),'-framework','AppKit','-framework','ApplicationServices','-o',str(clicker)],capture_output=True,text=True,timeout=90)
        assert result.returncode==0,result.stderr[:500]
    before=tasks();assert not any(t.get('status')=='运行中' for t in before)
    prior_ids={t['id'] for t in before}
    record=dict(status='running',candidate=str(CANDIDATE),installed_app=True,pid=pid,
        ui_only_submission=True,http_used=False,javascript_used=False,prepopulated_results=False,
        question=QUESTION,model_call_limit=2,data_request_limit=12,checks=[],actions=[],screens=[],paid_submission_started=False)
    def save():guard.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    save()
    def control(action,*args):
        labels={'objects':'研究对象','models':'模型调用上限','requests':'数据请求上限','seconds':'执行秒数上限','chat-input':'任务要求'}
        if action=='type':
            values=[str(setter),'set',str(pid),labels.get(args[0],args[0]),args[1]]
        elif action=='press':
            target=args[0]
            if target.startswith('view-'):target=['研究概览','数据来源','研究底稿','研究报告','跟踪复核'][int(target[-1])]
            values=[str(setter),'press',str(pid),target]
        elif action=='click':values=[str(clicker),action,str(pid),*args]
        elif action in ('focus','local-key'):values=[str(setter),'key' if action=='local-key' else 'focus',str(pid),*args]
        else:values=[str(OPERATOR),action,str(pid),*args]
        result=subprocess.run(values,capture_output=True,text=True,timeout=20)
        data=json.loads(result.stdout)
        if result.returncode:raise RuntimeError('Native action failed: '+str(data))
        if action not in ('list','windows'):record['actions'].append(dict(action=action,target=args[0] if args else ''))
        return data
    def snapshot():return control('list')['controls']
    def press(label):control('press',label);time.sleep(.2)
    def available(label):
        names={'objects':'研究对象','models':'模型调用上限','requests':'数据请求上限','seconds':'执行秒数上限','chat-input':'任务要求'}
        if label in names:
            value=subprocess.run([str(setter),'snapshot',str(pid)],capture_output=True,text=True,timeout=20)
            return any(names[label] in (n.get('title'),n.get('description')) and n.get('role') in ('AXTextField','AXTextArea') for n in json.loads(value.stdout)['items'])
        return any(label in (n.get('title'),n.get('description'),n.get('dom')) for n in snapshot())
    def wait(label,seconds=8):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if available(label):return
            time.sleep(.25)
        raise AssertionError('Native control unavailable: '+label)
    def capture(name):
        windows=control('windows');assert len(windows)==1
        filename=name+'.png'
        subprocess.run(['/usr/sbin/screencapture','-x','-l',str(windows[0]['id']),str(OUTPUT/filename)],check=True,capture_output=True)
        record['screens'].append(filename)
    def check(label,value):
        assert value,label
        record['checks'].append(label);save()
    try:
        check('native accessibility available',bool(snapshot()))
        
        if not available('objects'):
            if available('关闭'):press('关闭')
            press('新建任务');wait('新任务范围与预算');press('新任务范围与预算')
        wait('objects');control('type','objects','600519.SH')
        for action in ['click']:
            if available('models'):break
            control(action,'运行预算');time.sleep(.4)
        wait('models')
        control('type','models','2');control('type','requests','12');control('type','seconds','180')
        check('native model budget is exactly two',control('value','models').get('value')=='2')
        press('保存设置');wait('chat-input')
        control('type','chat-input',QUESTION)
        record['paid_submission_started']=True;save()
        press('发送研究问题')
        deadline=time.monotonic()+200;task=None
        while time.monotonic()<deadline:
            found=[t for t in tasks() if t['id'] not in prior_ids and t.get('question')==QUESTION]
            if found:
                assert len(found)==1;task=found[0]
                record.update(task_id=task['id'],research_status=task['status'],ledger=task['ledger'],delivery=task.get('delivery'));save()
                assert task['ledger']['used']['model_calls']<=2
                assert task['ledger']['used']['data_requests']<=12
                if task['status'] in ('已完成','部分完成','失败','已取消'):break
            time.sleep(2)
        check('one natural question created an actual research task',bool(task))
        check('task retained exact finite budget',task['ledger']['limits']['model_calls']==2 and task['ledger']['limits']['data_requests']==12)
        check('actual report durably generated',bool(task.get('delivery')))
        paper=task.get('draft_report') or {}
        check('actual data and investment logics exist',bool(paper.get('facts')) and bool(paper.get('logics')))
        fields={fact.get('field') for fact in paper.get('facts',[]) if fact.get('value') is not None}
        check('all explicitly requested financial, price and PE fields acquired',{'ths_revenue_stock','ths_np_atoopc_stock','ths_ncf_from_oa_stock','close','ths_pe_ttm_stock'}<=fields)
        check('three requested investment logics are actually generated',len(paper.get('logics',[]))==3)
        check('actual planner uses registered schema transport',any(event.get('research_phase')=='acquire' and event.get('submission_transport')=='responses_json_schema_v1' and event.get('status')=='成功' for event in task['ledger']['dispatches']))
        check('only iFinD API records used, no PDF retrieval',all(record.get('source')=='ifind' and record.get('kind')=='api' for record in task['source_records']) and not task.get('materials'))
        record.update(fact_count=len(paper.get('facts',[])),logic_count=len(paper.get('logics',[])),model_calls=task['ledger']['used']['model_calls'],data_requests=task['ledger']['used']['data_requests'])
        wait('ResDesk');press('ResDesk');wait('view-0')
        for stage,name in [(0,'overview'),(1,'sources'),(2,'workingpaper'),(3,'report'),(4,'tracking')]:
            press('view-'+str(stage));time.sleep(.6)
            text='\n'.join(n.get('title','') for n in snapshot())
            check(name+' native view has content',bool(text.strip()))
            if stage in (1,2,3):capture('native-'+name)
        press('view-2')
        controls=snapshot();source=next((n for n in controls if n['role']=='AXButton' and n.get('title','').startswith('查看600519.SH，') and n.get('title','').endswith('的依据')),None)
        check('native notebook exposes exact observation actions',bool(source))
        press(source['title']);time.sleep(.4);capture('native-original')
        check('native source reader opens retained data',any('来源' in n.get('title','') or '原始' in n.get('title','') or '原件' in n.get('title','') for n in snapshot()))
        press('关闭');press('ResChat')
        check('same-task Chat remains accessible',available('chat-input'))
        press('ResDesk');press('view-3');time.sleep(.7);capture('native-report-final')
        record.update(status='scoped_passed',financial_quality_accepted=False,all_prd_accepted=False)
    except Exception as error:
        record.update(status='failed',error=str(error))
        try:capture('failure')
        except Exception:pass
        raise
    finally:
        save();print(json.dumps({key:record.get(key) for key in ['status','task_id','model_calls','data_requests','fact_count','logic_count','error']},ensure_ascii=False),flush=True)
        print(str(guard),flush=True)


if __name__=='__main__':run()
