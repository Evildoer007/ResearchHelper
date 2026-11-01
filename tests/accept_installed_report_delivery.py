"""Inspect/export the one saved native-App research. Never starts research."""
from pathlib import Path
import hashlib,json,os,sqlite3,subprocess,time
from tests.accept_installed_research_workflow import (
    InstalledApp,ROOT,STORE,CANDIDATE,stored_tasks,write_record,desktop_session,
    candidate_from_environment,required_candidate,NativeDesktopBusy,
)
from tests.accept_desk_evidence_package import recorded_manifest,verify_bundle,signature

CANDIDATE=candidate_from_environment(os.environ) or CANDIDATE
TASK_ID='rh_567c8c8495b94ff2bf59097371b42fcf'
OUTPUT=Path(os.environ.get('RESEARCHHELPER_DELIVERY_QA_OUTPUT',str(ROOT/'result/acceptance/installed-report-delivery-20261031')))
OPERATOR=ROOT/'result/acceptance/native-gui-current/operator'


def task():
    return next(value for value in stored_tasks() if value['id']==TASK_ID)


def enter_report(app,current):
    app.prepare();app.launch();app.login()
    title=current.get('title') or current['draft_report']['title']
    names={next(text for text in (item['title'], item['description']) if title in text)
           for item in app.control('snapshot')['items'] if item['role']=='AXButton'
           and any(title in text for text in (item['title'], item['description']))}
    if len(names)!=1:raise RuntimeError('Saved task history is not unique')
    app.press(names.pop());app.press('ResDesk');app.press('研究报告')
    app.wait_control('完整研究报告');time.sleep(.7)
    app.press('标准报告');app.press('导出格式');app.press('HTML')


def probe_save_panel():
    candidate=required_candidate(CANDIDATE)
    OUTPUT.mkdir(parents=True,exist_ok=True)
    current=task();before=dict(current['ledger']['used'])
    verify_bundle(Path('/Applications/ResearchHelper.app'),recorded_manifest(candidate))
    signature(Path('/Applications/ResearchHelper.app'))
    record=dict(status='probing_save_panel',candidate=str(candidate),task_id=TASK_ID,
                model_calls=0,data_requests=0,scope='same_saved_report_only',native_panel_input='foreground_guarded_keyboard',system_clipboard_touched=False)
    app=InstalledApp(OUTPUT,record,None)
    try:
        enter_report(app,current);app.press('导出');time.sleep(1)
        # Observe the real window without traversing a remote file-panel tree.
        windows=json.loads(subprocess.check_output([str(OPERATOR),'windows',str(app.process.pid)],text=True))
        window=max(windows,key=lambda item:item['bounds']['Width'])
        subprocess.run(['/usr/sbin/screencapture','-x','-l',str(window['id']),str(OUTPUT/'save-panel.png')],check=True,capture_output=True)
        print(json.dumps({'native_pid':app.process.pid,'windows':windows,'capture':str(OUTPUT/'save-panel.png')},ensure_ascii=False),flush=True)
        subprocess.run([str(OPERATOR),'key',str(app.process.pid),'escape'],check=True,capture_output=True)
        record['status']='panel_captured_for_review'
    except BaseException as error:
        record.update(status='failed',error=str(error))
        raise
    finally:
        app.close();record['usage_unchanged']=task()['ledger']['used']==before
        write_record(OUTPUT/'probe.json',record)
    return record


REPORTS=Path(os.environ.get('RESEARCHHELPER_DELIVERY_REPORTS',str(ROOT/'result/reports/maotai-20261031')))


def run_exports(*, pdf_operation_registered=False, app_factory=InstalledApp):
    candidate=required_candidate(CANDIDATE)
    verify_bundle(Path('/Applications/ResearchHelper.app'),recorded_manifest(candidate))
    signature(Path('/Applications/ResearchHelper.app'))
    OUTPUT.mkdir(parents=True,exist_ok=True);REPORTS.mkdir(parents=True,exist_ok=True)
    current=task();before=dict(current['ledger']['used'])
    record=dict(status='running',candidate=str(candidate),task_id=TASK_ID,checks=[],exports=[],
                model_calls=0,data_requests=0,scope='same_saved_report_only',native_panel_input='foreground_guarded_keyboard',system_clipboard_touched=False)
    session=desktop_session()
    if not session['interactive']:
        record.update(status='pending_desktop_unlock',desktop_session=session,native_window_opened=False)
        write_record(OUTPUT/'receipt.json',record)
        return record
    app=app_factory(OUTPUT,record,None)
    def save():write_record(OUTPUT/'receipt.json',record)
    def check(label,condition):
        if not condition:raise AssertionError(label)
        record['checks'].append(label);save()
    def native_save(name):
        target=REPORTS/name
        if target.exists():raise RuntimeError('Do not overwrite a prior native export: '+name)
        record['stage']='saving '+name;save()
        app.press('导出');time.sleep(.7)
        # The native panel initially focuses its selected filename. Send
        # The XPC save panel needs HID routing; every event checks App focus.
        app.control('save-key','select-all');app.control('save-text',Path(name).stem);time.sleep(.4)
        app.control('save-key','folder');time.sleep(.7)
        app.control('save-key','select-all');app.control('save-text',str(REPORTS));time.sleep(.7)
        app.control('save-key','return');time.sleep(1)
        app.control('save-key','return')
        deadline=time.monotonic()+12
        while not target.exists() and time.monotonic()<deadline:time.sleep(.15)
        if not target.exists():
            windows=json.loads(subprocess.check_output([str(OPERATOR),'windows',str(app.process.pid)],text=True))
            window=max(windows,key=lambda item:item['bounds']['Width'])
            subprocess.run(['/usr/sbin/screencapture','-x','-l',str(window['id']),str(OUTPUT/'save-failure.png')],capture_output=True)
            raise RuntimeError('Native save did not produce '+name)
        raw=target.read_bytes();record['exports'].append(dict(path=str(target),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),native_save=True));save()
        check('native save feedback names '+name,'已保存：'+name in app.text())
        print(json.dumps({'stage':'native_saved','name':name,'bytes':len(raw)},ensure_ascii=False),flush=True)
        time.sleep(.3)
        return raw
    save()
    try:
        enter_report(app,current)
        standard=native_save('600519-report-standard.html')
        app.press('详细报告');time.sleep(.7)
        detailed=native_save('600519-report-detailed.html')
        def document(raw):
            import re
            match=re.search(r'<script id="rh-publication-document" type="application/json">(.*?)</script>',raw.decode(),re.S)
            if not match:raise AssertionError('Saved HTML has no publication document')
            return json.loads(match.group(1))
        standard_doc,detailed_doc=document(standard),document(detailed)
        check('standard template exported through native save',standard_doc['template']['id']=='report-standard')
        check('detailed template exported through native save',detailed_doc['template']['id']=='report-detailed')
        check('two profiles retain the same research dataset',standard_doc['dataset']==detailed_doc['dataset'])
        check('detailed profile expands the same logic',len(detailed_doc['blocks'])>len(standard_doc['blocks']))
        check('all forty observations exported',len(standard_doc['dataset']['rows'])==40)
        check('compact prose and figures retain their HTML layout',len({b['reading_group'] for b in standard_doc['blocks'] if b.get('reading_group')})==2)
        app.press('标准报告');app.press('导出格式');app.press('PDF')
        marker=Path('/Users/haoranxu/.codex/plugins/cache/openai-primary-runtime/pdf/26.1017.22136/skills/pdf/container_tools/mark_artifact_operation_started.mjs')
        if not pdf_operation_registered:
            subprocess.run(['/usr/local/bin/node',str(marker),'--operation-kind','create','--expected-output-count','1','--output-format','pdf'],check=True,capture_output=True)
        pdf=native_save('600519-report-standard.pdf')
        check('native PDF is a real document',pdf.startswith(b'%PDF-'))
        from io import BytesIO
        from pypdf import PdfReader
        pages=PdfReader(BytesIO(pdf)).pages
        check('saved standard PDF is one complete A4 page',len(pages)==1 and abs(float(pages[0].mediabox.width)-595.276)<1)
        # Normalize only this visual-QA comparison; the saved bytes and source quotes stay untouched.
        import unicodedata
        visible_text=unicodedata.normalize('NFKC',pages[0].extract_text()).replace('⻛','风')
        check('PDF retains all three logics and risk text',all(text in visible_text for text in ['策略逻辑一','策略逻辑二','策略逻辑三','风险提示']))
        app.observe('exported-report');app.capture('exported-report')
        check('task ledger unchanged after exports',task()['ledger']['used']==before)
        record['status']='native_exports_passed';save()
    except NativeDesktopBusy as error:
        if app.process:
            record.update(status='failed',error='Busy marker received after App launch');save();raise
        record.update(status='deferred_while_desktop_in_use',error=str(error),foreground_opened=False,native_window_opened=False);save()
        return record
    except BaseException as error:
        record.update(status='failed',error=str(error));save()
        if app.process and app.process.poll() is None:
            try:
                windows=json.loads(subprocess.check_output([str(OPERATOR),'windows',str(app.process.pid)],text=True,timeout=5))
                if windows:
                    window=max(windows,key=lambda item:item['bounds']['Width'])
                    subprocess.run(['/usr/sbin/screencapture','-x','-l',str(window['id']),str(OUTPUT/'native-failure.png')],capture_output=True,timeout=5)
            except (subprocess.SubprocessError,ValueError,KeyError):pass
        raise
    finally:
        app.close();record['usage_unchanged']=task()['ledger']['used']==before;save()
    return record


if __name__=='__main__':run_exports()
