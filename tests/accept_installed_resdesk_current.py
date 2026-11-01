"""One installed-App study and export through application-local Accessibility.

No HTTP/JavaScript injection or prepared research results. Paid submission has
one durable guard and a two-call budget; read-only SQLite confirms the result.
The helper never sends keyboard/pointer events to another application.
"""
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import hashlib,importlib.util,json,os,sqlite3,subprocess,time
from core.bootstrap import configure_imports
configure_imports()
from researchhelper.archive_codec import decode_body

ROOT=Path(__file__).resolve().parents[1]
DB=Path.home()/'Library/Application Support/ResearchHelper/research-v1/researchhelper.sqlite3'
QUESTION='研究贵州茅台600519.SH，比较2024年和2025年营业收入、归母净利润、经营现金流，取最近交易日收盘价与PE(TTM)，形成3条有数据支持的投资逻辑并生成标准报告。优先iFinD，不下载PDF，不扩展其他标的或长周期行情；区分经营事实与条件性判断，合并现金流不能直接代表主营回款。'


def task_rows():
    with sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True) as db:
        return [decode_body(db,row[0]) for row in db.execute('SELECT body FROM tasks')]


def run():
    candidate=Path(os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve()
    output=ROOT/'result/acceptance'/('installed-resdesk-'+candidate.parent.name);output.mkdir(parents=True,exist_ok=True)
    receipt=output/'receipt.json'
    if receipt.exists() and json.loads(receipt.read_text()).get('paid_submission_started'):
        raise RuntimeError('This acceptance already submitted one study; inspect it without any paid retry')
    table=subprocess.check_output(['/bin/ps','-axo','pid=,comm='],text=True)
    pids=[int(line.split()[0]) for line in table.splitlines() if line.strip().endswith('/Applications/ResearchHelper.app/Contents/MacOS/ResearchHelper')]
    if len(pids)!=1:raise RuntimeError('Expected exactly one installed ResearchHelper process')
    pid=pids[0]
    spec=importlib.util.spec_from_file_location('rh_builder',ROOT/'packaging/app/build_app.py');builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder);builder.OUTPUT=output/'compile';builder.OUTPUT.mkdir(exist_ok=True)
    helper=output/'native-accessibility'
    source=(ROOT/'packaging/tests/native_app_accessibility.swift').read_text()
    # Scope reads to non-secret research controls and support a native path field.
    anchor='let all = nodes(application)'
    extra='''
let all = nodes(application)
if command == "value" {
    let safe = ["研究对象", "模型调用上限", "数据请求上限", "执行秒数上限", "任务要求"]
    guard arguments.count == 4, safe.contains(arguments[3]), let item = all.first(where: { matches($0, arguments[3]) }) else { emit(["error":"unsafe read selector"]); exit(1) }
    emit(["value":string(item,kAXValueAttribute)]);exit(0)
}
'''
    assert source.count(anchor)==1;source=source.replace(anchor,extra)
    source=source.replace('["AXTextField", "AXTextArea"].contains(string($0, kAXRoleAttribute))','["AXTextField", "AXTextArea", "AXComboBox"].contains(string($0, kAXRoleAttribute))')
    source=source.replace('print(String(data: value, encoding: .utf8)!)','print(String(data: value, encoding: .utf8)!)\n    RunLoop.main.run(until: Date().addingTimeInterval(0.25))')
    # Keep AX handles and command-local bindings in a lexical function scope.
    # Top-level Swift bindings in the generated executable produced recursive
    # application aliases instead of independent child handles on this toolchain.
    boundary='NSApplication.shared.setActivationPolicy(.prohibited)'
    head,body=source.split(boundary,1)
    source=head+'func runController() {\n'+boundary+body+'\n}\nrunController()\n'
    swift=output/'native-accessibility.swift';swift.write_text(source)
    compiled=subprocess.run(['xcrun','swiftc',*builder.swift_options(),str(swift),'-framework','AppKit','-framework','ApplicationServices','-o',str(helper)],capture_output=True,text=True,timeout=150)
    if compiled.returncode:raise RuntimeError(compiled.stderr[:3000])
    record=dict(status='running',candidate=str(candidate),installed_app='/Applications/ResearchHelper.app',pid=pid,checks=[],actions=[],question=QUESTION,
        paid_submission_started=False,model_call_limit=2,data_request_limit=12,automatic_paid_retry=False,http_used=False,javascript_used=False,global_events_posted=False,
        prepopulated_research=False,financial_quality_accepted=False,all_prd_accepted=False)
    def save():receipt.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    def control(action,*args):
        result=subprocess.run([str(helper),action,str(pid),*args],capture_output=True,text=True,timeout=20)
        if result.returncode:raise RuntimeError('Native '+action+' failed: '+result.stdout[:400])
        value=json.loads(result.stdout)
        if action not in ('snapshot','value'):record['actions'].append(dict(action=action,target=args[0] if args else ''));save()
        return value
    def items():return control('snapshot')['items']
    def visible(label):return any(label in (row.get('id'),row.get('title'),row.get('description')) for row in items())
    def wait(label,timeout=12):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if visible(label):return
            time.sleep(.2)
        raise RuntimeError('Native control unavailable: '+label)
    def press(label):control('press',label);time.sleep(.2)
    def expand(label):
        try:press(label)
        except RuntimeError:control('focus',label);control('key','space');time.sleep(.25)
    def check(label,value):
        if not value:raise AssertionError(label)
        record['checks'].append(label);save()
    def value(label):return control('value',label)['value']
    def open_budget():
        wait('新建任务');press('新建任务');wait('新任务范围与预算');expand('新任务范围与预算');wait('研究对象')
        if not visible('模型调用上限'):expand('运行预算')
        wait('模型调用上限')
    previous=None
    save()
    try:
        check('application-local Accessibility available',bool(items()))
        before=task_rows();check('no other research is running',not any(row['status']=='运行中' for row in before));old_ids={row['id'] for row in before}
        open_budget()
        previous={key:value(key) for key in ('研究对象','模型调用上限','数据请求上限','执行秒数上限')};record['previous_defaults']=previous;save()
        for label,text in [('研究对象','600519.SH'),('模型调用上限','2'),('数据请求上限','12'),('执行秒数上限','180')]:control('set',label,text)
        check('two model calls configured in actual App',value('模型调用上限')=='2')
        press('保存设置');wait('任务要求');control('set','任务要求',QUESTION)
        check('exact natural question entered into the App',value('任务要求')==QUESTION)
        record['paid_submission_started']=True;save();press('发送研究问题')
        until=time.monotonic()+205;task=None
        while time.monotonic()<until:
            found=[row for row in task_rows() if row['id'] not in old_ids and row.get('question')==QUESTION]
            if found:
                check_unique=len(found)==1
                if not check_unique:raise AssertionError('Duplicate native study submission')
                task=found[0];record.update(task_id=task['id'],research_status=task['status'],ledger=task['ledger'],delivery=task.get('delivery'));save()
                if task['ledger']['used']['model_calls']>2 or task['ledger']['used']['data_requests']>12:raise AssertionError('Budget exceeded')
                if task['status']!='运行中':break
            time.sleep(2)
        check('one native question created one real study',bool(task))
        check('report is durably generated',bool(task.get('delivery')))
        paper=task.get('draft_report') or {};fields={fact.get('field') for fact in paper.get('facts',[]) if fact.get('value') is not None}
        check('all five requested financial and market fields acquired',{'ths_revenue_stock','ths_np_atoopc_stock','ths_ncf_from_oa_stock','close','ths_pe_ttm_stock'}<=fields)
        check('three investment logics actually generated',len(paper.get('logics',[]))==3)
        check('iFinD API records only, no downloaded PDF',bool(task.get('source_records')) and all(row.get('source')=='ifind' and row.get('kind')=='api' for row in task['source_records']) and not task.get('materials'))
        facts={row['id']:row for row in paper['facts']};arithmetic=[]
        for calc in paper.get('calculations',[]):
            if calc.get('method')=='raw_change_v1' and len(calc.get('data_refs',[]))==2:
                rows=sorted([facts[key] for key in calc['data_refs']],key=lambda row:str(row['period']))
                first,last=(Decimal(str(row['value'])) for row in rows)
                if abs((last-first)/first*100-Decimal(str(calc['value'])))>=Decimal('.000001'):raise AssertionError('Registered change differs from retained inputs')
                arithmetic.append(calc['id'])
        check('registered changes independently recalculated',len(arithmetic)>=3)
        with sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True) as db:
            raw=db.execute('SELECT html FROM deliveries WHERE task_id=? AND version=?',(task['id'],task['delivery']['version'])).fetchone()[0]
        check('stored HTML hash matches delivery',hashlib.sha256(raw.encode()).hexdigest()==task['delivery']['html_sha256'])
        (output/'actual-frozen-report.html').write_text(raw)
        wait('ResDesk');press('ResDesk')
        for label in ['研究概览','数据来源','研究底稿','研究报告','跟踪复核']:
            wait(label);press(label);check(label+' is accessible in installed App',len(items())>10)
        press('研究报告');wait('导出')
        press('导出');wait('OKButton',timeout=10)
        control('key','folder');time.sleep(.4)
        path_field=next((row for row in items() if row.get('id')=='PathTextField'),None)
        check('native folder chooser is available',path_field is not None)
        selector='PathTextField'
        control('set',selector,str(output));control('key','return');time.sleep(.4)
        press('OKButton');time.sleep(.8)
        check('native export reports saved after disk write',any('已保存：' in (row.get('value') or '') for row in items()))
        exported=[path for path in output.glob('*.html') if path.name!='actual-frozen-report.html']
        check('exported HTML actually exists on disk',len(exported)==1 and exported[0].stat().st_size>1000)
        from lxml import html
        tree=html.document_fromstring(exported[0].read_bytes());documents=tree.xpath('//script[@id="rh-publication-document"]')
        check('export contains the frozen presentation document',len(documents)==1)
        document=json.loads(documents[0].text)
        check('each investment logic has its own visual',all(document['figure_coverage'].get('logic-'+str(i),{}).get('figures',0)>0 for i in range(1,4)))
        record.update(status='scoped_passed',fact_count=len(facts),logic_count=len(paper['logics']),registered_arithmetic_checked=arithmetic,
            model_calls=task['ledger']['used']['model_calls'],data_requests=task['ledger']['used']['data_requests'],exported_report=str(exported[0]),figure_coverage=document['figure_coverage'])
    except Exception as error:
        record.update(status='failed',error=str(error));raise
    finally:
        if previous:
            try:
                if visible('CancelButton'):press('CancelButton')
                if visible('关闭'):press('关闭')
                open_budget()
                for label,text in previous.items():control('set',label,text)
                press('保存设置');record['defaults_restored']=True
            except Exception as error:record['defaults_restore_error']=str(error)
        save();print(json.dumps({key:record.get(key) for key in ('status','task_id','fact_count','logic_count','model_calls','data_requests','exported_report','error')},ensure_ascii=False),flush=True)
        print(str(receipt),flush=True)


def resume_saved_report_export():
    """Finish native save on the already-paid task; never submits research."""
    candidate=Path(os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve()
    output=ROOT/'result/acceptance'/('installed-resdesk-'+candidate.parent.name)
    original=json.loads((output/'receipt.json').read_text());task_id=original['task_id']
    before=next(row for row in task_rows() if row['id']==task_id)
    assert before.get('delivery') and before['status']!='运行中'
    helper=output/'native-accessibility';pid=str(original['pid'])
    record=dict(status='running',task_id=task_id,candidate=str(candidate),source_session=str(output/'receipt.json'),
        checks=[],additional_model_calls=0,additional_data_requests=0,http_used=False,javascript_used=False,global_events_posted=False)
    receipt=output/'export-recheck.json'
    def call(action,*args):
        r=subprocess.run([str(helper),action,pid,*args],capture_output=True,text=True,timeout=20)
        if r.returncode:raise RuntimeError(r.stdout[:300])
        return json.loads(r.stdout)
    def items():return call('snapshot')['items']
    def press(label):call('press',label);time.sleep(.2)
    def wait(label,seconds=8):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if any(label in (row.get('id'),row.get('title'),row.get('description')) for row in items()):return
            time.sleep(.15)
        raise RuntimeError('Native control unavailable: '+label)
    def check(label,value):
        if not value:raise AssertionError(label)
        record['checks'].append(label)
    front=subprocess.check_output(['/usr/bin/osascript','-e','tell application "System Events" to get bundle identifier of first application process whose frontmost is true'],text=True).strip()
    try:
        press('~'+QUESTION);press('ResDesk');press('研究报告');wait('导出')
        press('导出');wait('OKButton')
        # AppKit's Go to Folder shortcut needs the save sheet's key window.
        # Activate only for this bounded system sheet; return foreground after save.
        call('activate');call('key','folder');wait('PathTextField',seconds=4)
        call('set','PathTextField',str(output));call('key','return');time.sleep(.4)
        press('OKButton');time.sleep(.6)
        check('actual native save completion is reported',any('已保存：' in (row.get('value') or '') for row in items()))
        files=[path for path in output.glob('*.html') if path.name!='actual-frozen-report.html']
        check('report exists on disk after NSSavePanel confirmation',len(files)==1 and files[0].stat().st_size>1000)
        from lxml import html
        tree=html.document_fromstring(files[0].read_bytes());nodes=tree.xpath('//script[@id="rh-publication-document"]')
        check('saved file contains its exact presentation document',len(nodes)==1)
        doc=json.loads(nodes[0].text)
        check('all three actual logics have visual evidence',all(doc['figure_coverage'].get('logic-'+str(index),{}).get('figures',0)>0 for index in range(1,4)))
        after=next(row for row in task_rows() if row['id']==task_id)
        check('save did not alter delivery or spend more API quota',after['delivery']==before['delivery'] and after['ledger']['used']==before['ledger']['used'])
        record.update(status='scoped_passed',exported_report=str(files[0]),sha256=hashlib.sha256(files[0].read_bytes()).hexdigest(),
            fact_count=len(after['draft_report']['facts']),logic_count=len(after['draft_report']['logics']),
            model_calls=after['ledger']['used']['model_calls'],data_requests=after['ledger']['used']['data_requests'],figure_coverage=doc['figure_coverage'])
    except Exception as error:
        record.update(status='failed',error=str(error));raise
    finally:
        if front and front!='io.researchhelper.app':subprocess.run(['/usr/bin/open','-b',front],check=True)
        record['foreground_restored']=bool(front)
        receipt.write_text(json.dumps(record,ensure_ascii=False,indent=2))
        print(json.dumps(record,ensure_ascii=False),flush=True)
    return record

if __name__=='__main__':run()
