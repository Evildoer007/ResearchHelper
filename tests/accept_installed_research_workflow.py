"""Installed-App workflow acceptance, with a separate zero-call preflight.

Task creation and submission use native Accessibility, never HTTP or injected
JavaScript. Read-only SQLite supplies independent task, budget and report proof.
Live submission requires explicit single-case authorization; it never retries.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import plistlib
import re
import sqlite3
import subprocess
import tempfile
import time

from tests.accept_native_app_report_flow import ActualNativeApp, ROOT, PROBE
from tests.accept_desk_evidence_package import recorded_manifest, verify_bundle, signature
from researchhelper.archive_codec import decode_body
from researchhelper.model import ModelSettings
from researchhelper.settings import DataSettings

def candidate_from_environment(environment):
    """Use the caller's selected release; an old dated path is not a default."""
    value = (environment.get('RESEARCHHELPER_ACCEPT_CANDIDATE')
             or environment.get('RESEARCHHELPER_CURRENT_RECEIPT'))
    return Path(value) if value else None


def required_candidate(selected):
    if selected is None:
        raise RuntimeError('验收未选择明确候选，未启动App或研究')
    return Path(selected)


CANDIDATE = candidate_from_environment(os.environ)
STORE = Path.home() / 'Library/Application Support/ResearchHelper/research-v1'
BUNDLE = Path('/Applications/ResearchHelper.app')
# Keep the already-used one-case journal so a new build cannot reset its paid-submission guard.
OUTPUT = ROOT / 'result/acceptance/installed-research-workflow-20261031-003912-691918'
QUESTION = (
    '研究贵州茅台600519.SH，比较2024年和2025年全年营业收入、归母净利润及经营活动现金流，'
    '结合最近交易日收盘价与PE(TTM)，形成3条有数据支撑的策略逻辑及风险，生成带图表的对客标准报告。'
    '优先iFinD结构化接口并复用适用字段，不下载PDF、不扩展标的、不查询长周期行情，'
    '不将合并现金流变化直接归因于主营回款。'
)


class NativeDesktopBusy(RuntimeError):
    """The quiet window ended before launch; no App failure is implied."""


def write_record(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def desktop_state(registry, user_id):
    """An unlocked session may omit the lock property; identify its owner first."""
    sessions = []
    def scan(value):
        if isinstance(value, dict):
            if 'kCGSSessionUserIDKey' in value:
                sessions.append(value)
            for item in value.values(): scan(item)
        elif isinstance(value, list):
            for item in value: scan(item)
    scan(registry)
    current = [item for item in sessions if type(item.get('kCGSSessionUserIDKey')) is int
        and item['kCGSSessionUserIDKey'] == user_id and item.get('kCGSSessionOnConsoleKey') is True
        and item.get('kCGSessionLoginDoneKey') is True]
    if len(current) != 1:
        return dict(known=False, locked=None, interactive=False)
    locked = current[0].get('CGSSessionScreenIsLocked', False)
    if type(locked) is not bool:
        return dict(known=False, locked=None, interactive=False)
    return dict(known=True, locked=locked, interactive=not locked)


def desktop_session():
    """Read this user's active console session without unlocking or activating it."""
    data = plistlib.loads(subprocess.check_output(['/usr/sbin/ioreg','-n','Root','-d','1','-a']))
    return desktop_state(data, os.getuid())


def stored_tasks():
    """Decode the real archive without writing or constructing research results."""
    with sqlite3.connect((STORE / 'researchhelper.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        return [decode_body(db, row[0]) for row in db.execute('SELECT body FROM tasks')]


def preflight():
    candidate = required_candidate(CANDIDATE)
    OUTPUT.mkdir(exist_ok=True)
    manifest = recorded_manifest(candidate)
    files = verify_bundle(BUNDLE, manifest)
    signature(BUNDLE)
    model = ModelSettings(STORE).read()  # Public readiness flags, never a key.
    sources = DataSettings(STORE).read()
    running = sum(t['status'] == '运行中' for t in stored_tasks())
    record = dict(status='preflight_passed', candidate=str(candidate), installed_files=len(files),
        signature_verified=True, model=model.get('model'), model_configured=bool(model.get('has_key')),
        ifind_configured=any(sources['ifind'].values()), existing_running_tasks=running,
        question=QUESTION, limits=dict(model_calls=2, data_requests=100000, seconds=240),
        live_submission_authorized=False, real_task_started=False, model_calls=0, data_requests=0,
        native_window_opened=False, credentials_read=False, production_store_written=False)
    if (model.get('model') != 'deepseek-flash' or not record['model_configured']
            or not record['ifind_configured'] or running):
        record['status'] = 'preflight_not_ready'
    write_record(OUTPUT / 'preflight.json', record)
    return record


class LaunchedAppProcess:
    """Own the LaunchServices App, rather than the short-lived open command."""
    def __init__(self,pid,launcher,executable):
        self.pid,self.launcher,self.executable=pid,launcher,str(executable)
        self.identity=self._identity()
    def _identity(self):
        result=subprocess.run(['/bin/ps','-ww','-p',str(self.pid),'-o','lstart=,comm='],capture_output=True,text=True)
        line=result.stdout.strip()
        return line if line.endswith(self.executable) else None
    def poll(self):return None if self.identity is not None and self._identity()==self.identity else 0
    def terminate(self):
        if self.poll() is None:os.kill(self.pid,15)
    def kill(self):
        if self.poll() is None:os.kill(self.pid,9)
    def wait(self,timeout=None):
        deadline=time.monotonic()+(timeout or 30)
        while self.poll() is None and time.monotonic()<deadline:time.sleep(.1)
        if self.poll() is None:raise subprocess.TimeoutExpired(self.executable,timeout)
        self.launcher.wait(timeout=5);return 0


class InstalledApp(ActualNativeApp):
    """Reuse the established native controller; operate the installed bundle."""
    def prepare(self):
        if not desktop_session()['interactive']:raise RuntimeError('macOS锁屏或桌面不可交互，未启动前台验收')
        self.bundle = BUNDLE
        self.binary = self.output / 'native-accessibility'
        spec = importlib.util.spec_from_file_location('rh_flow_compiler', ROOT / 'packaging/app/build_app.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        # WebKit needs its AX reply flushed before the controller exits.
        source = PROBE.read_text().replace('print(String(data: value, encoding: .utf8)!)',
            'print(String(data: value, encoding: .utf8)!)\n    RunLoop.main.run(until: Date().addingTimeInterval(0.35))')
        probe = self.output / 'native-accessibility.swift'
        reusable = self.binary.is_file() and probe.is_file() and probe.read_text() == source
        probe.write_text(source)
        with tempfile.TemporaryDirectory(prefix='rh-workflow-compiler-') as scratch:
            if reusable:
                self.previous_pid = self.control('frontmost')['pid']
                if not self.control('trusted')['trusted']:
                    raise RuntimeError('Accessibility unavailable')
                return
            builder.OUTPUT = Path(scratch)
            result = subprocess.run(['xcrun', 'swiftc', *builder.swift_options(), str(probe),
                '-framework', 'AppKit', '-framework', 'ApplicationServices', '-o', str(self.binary)],
                capture_output=True, text=True, timeout=120)
            (self.output / 'compiler.log').write_text(result.stdout + result.stderr)
            if result.returncode:
                raise RuntimeError('Native controller compilation failed')
        if not self.control('trusted')['trusted']:
            raise RuntimeError('Accessibility unavailable; no global-input fallback')
        self.previous_pid = self.control('frontmost')['pid']

    def launch(self):
        self.log = (self.output / 'native.log').open('a')
        executable=BUNDLE/'Contents/MacOS/ResearchHelper'
        def app_pids():
            output=subprocess.check_output(['/bin/ps','-ww','-axo','pid=,comm='],text=True)
            return {int(line.split(None,1)[0]) for line in output.splitlines() if line.split(None,1)[-1]==str(executable)}
        if app_pids():raise RuntimeError('An existing App is running; do not reuse the user window')
        environment=dict(os.environ)
        for name in ('RESEARCHHELPER_STORE','PYTHONPATH','PYTHONHOME'):environment.pop(name,None)
        launcher=subprocess.Popen(['/usr/bin/open','-n','-W',str(BUNDLE)],cwd=self.output,env=environment,stdout=self.log,stderr=self.log)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            pids=app_pids()
            if len(pids)==1:
                self.process=LaunchedAppProcess(pids.pop(),launcher,executable);break
            if launcher.poll() is not None:raise RuntimeError('LaunchServices did not open the installed App')
            time.sleep(.1)
        else:
            launcher.terminate();launcher.wait(timeout=5);raise RuntimeError('Installed App launch timed out')
        self.record.update(native_pid=self.process.pid,app_launch_mode='launchservices')
        self.backend_url()
        self.control('activate')

    def login(self):
        # Startup may restore a session after the first AX snapshot. Wait for
        # either real destination instead of requiring a login that never appears.
        deadline = time.monotonic() + 30
        attempted = False
        while time.monotonic() < deadline:
            items = self.control('snapshot')['items']
            buttons = {n.get(key) for n in items if 'AXPress' in n.get('actions', []) for key in ('title', 'description')}
            if {'ResChat', 'ResDesk'}.issubset(buttons):
                return
            if not attempted and any('登录' in (n['title'], n['description']) for n in items):
                self.control('set', '账号', 'admin')
                self.control('set', '密码', '66666666')
                self.control('press', '登录')
                attempted = True
            time.sleep(.25)
        self.observe('startup-not-ready');self.capture('startup-not-ready')
        raise RuntimeError('Native startup did not reach the login or research surface')

    def text(self):
        return '\n'.join(str(n.get(k, '')) for n in self.control('snapshot')['items']
                         for k in ('title', 'description', 'value'))


def run_live(*, confirmed_single_case=False):
    """Call only after the pending one-case authorization is explicitly answered."""
    if confirmed_single_case is not True:
        raise PermissionError('Single-case live authorization is still required')
    ready = preflight()
    if ready['status'] != 'preflight_passed':
        raise RuntimeError('Preflight is not ready; no research sent')
    receipt = OUTPUT / 'session.json'
    if receipt.exists() and json.loads(receipt.read_text()).get('paid_submission_started'):
        raise RuntimeError('This case was already sent once; inspect its task instead of sending again')
    prior = {t['id'] for t in stored_tasks()}
    record = dict(status='preparing', candidate=ready['candidate'], question=QUESTION, checks=[], actions=[],
        paid_submission_started=False, ui_only_submission=True, http_used=False,
        javascript_injected=False, answers_injected=False, automatic_paid_retry=False,
        limits=ready['limits'], financial_quality_accepted=False, native_export_accepted=False)
    app = InstalledApp(OUTPUT, record, None)
    def save():
        write_record(receipt, record)
    def check(label, condition):
        if not condition:
            raise AssertionError(label)
        record['checks'].append(label)
        save()
    def press(label):
        app.press(label)
        record['actions'].append(dict(action='press', target=label))
        save()
        time.sleep(.25)
    def set_value(label, value):
        app.control('set', label, value)
        record['actions'].append(dict(action='set', target=label, value=value))
        save()
    save()
    try:
        app.prepare()
        app.launch()
        app.login()
        press('新建任务')
        press('新任务范围与预算')
        # The question must identify the stock; do not prefill the answer.
        set_value('研究对象', '')
        try:
            press('运行预算')
        except RuntimeError:
            app.control('focus', '运行预算')
            app.control('key', 'space')
        app.wait_control('模型调用上限')
        for label, value in [('模型调用上限', '2'), ('数据请求上限', '100000'), ('执行秒数上限', '240')]:
            set_value(label, value)
        app.observe('01-budget')
        press('保存设置')
        set_value('任务要求', QUESTION)
        app.observe('02-question')
        app.capture('02-question')
        record['paid_submission_started'] = True
        save()
        press('发送研究问题')
        app.capture('03-running')
        app.control('hide')
        if app.previous_pid:
            subprocess.run([str(app.binary), 'activate', str(app.previous_pid)], capture_output=True, timeout=8)
        deadline = time.monotonic() + 265
        task = None
        last = None
        while time.monotonic() < deadline:
            found = [t for t in stored_tasks() if t['id'] not in prior and t.get('question') == QUESTION]
            if found:
                if len(found) != 1:
                    raise AssertionError('One UI send must create exactly one task')
                task = found[0]
                record.update(task_id=task['id'], research_status=task['status'], ledger=task['ledger'])
                used = task['ledger']['used']
                if used['model_calls'] > 2 or used['data_requests'] > ready['limits']['data_requests']:
                    raise AssertionError('Finite research budget exceeded')
                summary = (task['status'], used['model_calls'], used['data_requests'])
                if summary != last:
                    print(json.dumps(dict(status=summary[0], model_calls=summary[1], data_requests=summary[2]), ensure_ascii=False), flush=True)
                    last = summary
                    save()
                if task['status'] in ('已完成', '部分完成', '失败', '已取消'):
                    break
            time.sleep(2)
        check('new task reaches terminal state', task is not None and task['status'] in ('已完成', '部分完成', '失败', '已取消'))
        write_record(OUTPUT / 'task.json', task)
        check('report durably saved', bool(task.get('delivery')))
        draft = task.get('draft_report') or {}
        facts, logics = draft.get('facts', []), draft.get('logics', [])
        check('requested subject resolved without prefilled scope', task['question'] == QUESTION and task['original_input']['objects'] == [] and {f.get('code') for f in facts} == {'600519.SH'})
        check('all five fields acquired', {'ths_revenue_stock', 'ths_np_atoopc_stock', 'ths_ncf_from_oa_stock', 'close', 'ths_pe_ttm_stock'} <= {f.get('field') for f in facts})
        check('three data-based logics generated', len(logics) == 3)
        check('iFinD API originals retained', bool(task['source_records']) and all(s.get('source') == 'ifind' and s.get('kind') == 'api' for s in task['source_records']))
        check('no PDF retrieved', not task.get('materials'))
        record.update(facts=len(facts), logics=len(logics), sources=len(task['source_records']), **task['ledger']['used'])
        app.control('activate')
        press('ResDesk')
        views = [('研究概览', ['已取数对象', '600519.SH']), ('数据来源', ['数据来源']),
                 ('研究底稿', ['逻辑1', '分析依据']), ('研究报告', ['标准报告', '完整研究报告']),
                 ('跟踪复核', ['跟踪复核'])]
        for index, (label, expected) in enumerate(views):
            press(label)
            time.sleep(.7)
            text = app.text()
            app.observe('desk-' + str(index))
            app.capture('desk-' + str(index))
            check(label + ' actual details visible', all(value in text for value in expected))
        press('数据来源')
        names = [value for item in app.control('snapshot')['items'] if item['role'] == 'AXButton'
                 for value in (item['title'], item['description'])
                 if value.startswith('查看600519.SH，') and value.endswith('的依据')]
        check('observation links to its original', bool(names))
        press(names[0])
        app.observe('source-original')
        app.capture('source-original')
        check('source reader contains retained original', '来源' in app.text() or '原始' in app.text())
        press('关闭')
        press('研究报告')
        app.wait_control('完整研究报告')
        app.capture('report-final')
        with sqlite3.connect((STORE / 'researchhelper.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
            row = db.execute('SELECT html FROM deliveries WHERE task_id=? AND version=?',
                (task['id'], task['delivery']['version'])).fetchone()
        html = row[0]
        match = re.search(r'<script id="rh-publication-document" type="application/json">(.*?)</script>', html, re.S)
        document = json.loads(match.group(1)) if match else {}
        charts = sum(b.get('kind') == 'chart' for b in document.get('blocks', []))
        check('client report contains subject, logics and actual charts', '600519.SH' in html and '策略逻辑' in html and charts > 0)
        destination = ROOT / 'result/reports/maotai-20261031'
        destination.mkdir(exist_ok=True)
        target = destination / '贵州茅台研究报告.html'
        target.write_text(html)
        record.update(status='workflow_passed', report_path=str(target), charts=charts,
            report_sha256=hashlib.sha256(html.encode()).hexdigest(),
            report_copy_method='read_only_frozen_delivery', all_prd_accepted=False)
        save()
    except BaseException as error:
        record.update(status='failed', error=str(error))
        save()
        try:
            app.observe('failure')
            app.capture('failure')
        except Exception:
            pass
        raise
    finally:
        try:
            app.close()
        finally:
            save()
            print(str(receipt), flush=True)
    return record


def review_saved():
    """Continue UI/result inspection of the one sent case. Never start research."""
    receipt = OUTPUT / 'session.json'
    record = json.loads(receipt.read_text())
    ident = record.get('task_id')
    if not ident or not record.get('paid_submission_started'):
        raise RuntimeError('No submitted task to inspect')
    task = next(t for t in stored_tasks() if t['id'] == ident)
    before_usage = dict(task['ledger']['used'])
    record.pop('error', None)
    record.update(status='reviewing_saved_case', review_started_new_research=False)
    app = InstalledApp(OUTPUT, record, None)
    def save():
        write_record(receipt, record)
    def check(label, condition):
        if not condition:
            raise AssertionError(label)
        if label not in record['checks']:
            record['checks'].append(label)
        save()
    def press(label):
        app.press(label)
        record['actions'].append(dict(action='press', target=label))
        save()
        time.sleep(.3)
    try:
        draft = task.get('draft_report') or {}
        facts, logics = draft.get('facts', []), draft.get('logics', [])
        check('same UI-created task keeps exact question', task['question'] == QUESTION)
        check('subject inferred without prefilled scope', task['original_input']['objects'] == [] and {f.get('code') for f in facts} == {'600519.SH'})
        check('all requested fields acquired', {'ths_revenue_stock', 'ths_np_atoopc_stock', 'ths_ncf_from_oa_stock', 'close', 'ths_pe_ttm_stock'} <= {f.get('field') for f in facts})
        check('three real data-backed strategy logics', len(logics) == 3 and all(item.get('data_refs') and item.get('analysis') and item.get('risk') for item in logics))
        check('iFinD API originals retained', bool(task['source_records']) and all(s.get('source') == 'ifind' and s.get('kind') == 'api' for s in task['source_records']))
        check('no PDF retrieved', not task.get('materials'))
        check('same research stayed within model budget', before_usage['model_calls'] == 2)
        record.update(facts=len(facts), logics=len(logics), sources=len(task['source_records']), **before_usage)
        app.prepare()
        app.launch()
        app.login()
        items = app.control('snapshot')['items']
        names = [n['title'] for n in items if n['role']=='AXButton' and
            any(value and value in n['title'] for value in (task.get('title'), draft.get('title'), QUESTION))]
        check('new task can be reopened from App history', len(set(names)) == 1)
        press(names[0])
        press('ResDesk')
        views = [('研究概览', ['已取数对象', '600519.SH']), ('数据来源', ['数据来源', '营业收入']),
                 ('研究底稿', ['逻辑1', '分析依据']), ('研究报告', ['标准报告', '完整研究报告']),
                 ('跟踪复核', ['暂无跟踪条件'])]
        for index, (label, expected) in enumerate(views):
            press(label)
            time.sleep(.8)
            text = app.text()
            app.observe('desk-' + str(index))
            app.capture('desk-' + str(index))
            check(label + ' actual details visible', all(value in text for value in expected))
        press('研究底稿')
        text = app.text()
        check('all three generated logics visible in workingpaper', all(logic['title'] in text for logic in logics))
        check('original theory data and risk have clear sections', '分析依据' in text and '风险与限制' in text and '数据对照' in text)
        press('数据来源')
        buttons = [value for n in app.control('snapshot')['items'] if n['role']=='AXButton'
                   for value in (n['title'], n['description']) if value.startswith('查看600519.SH，') and value.endswith('的依据')]
        check('observations expose original-source actions', bool(buttons))
        press(buttons[0])
        app.observe('source-original')
        app.capture('source-original')
        check('actual original reader opens', '来源' in app.text() or '原始' in app.text())
        press('关闭')
        press('研究报告')
        app.wait_control('完整研究报告')
        time.sleep(1)
        app.observe('report-final')
        app.capture('report-final')
        check('frozen report body actually loaded', draft['title'] in app.text() and '策略逻辑一' in app.text())
        with sqlite3.connect((STORE/'researchhelper.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
            html = db.execute('SELECT html FROM deliveries WHERE task_id=? AND version=?',
                (ident, task['delivery']['version'])).fetchone()[0]
        match = re.search(r'<script id="rh-publication-document" type="application/json">(.*?)</script>', html, re.S)
        document = json.loads(match.group(1)) if match else {}
        charts = sum(b.get('kind')=='chart' for b in document.get('blocks', []))
        headings = [b.get('text','') for b in document.get('blocks', []) if b.get('kind')=='heading']
        check('client report has all three strategy sections and charts', sum(h.startswith('策略逻辑') for h in headings)==3 and charts>=3)
        check('client report is offline', 'echarts' in html.lower() and re.search(r'<script\b[^>]*\bsrc=[\"\']https?://', html) is None)
        destination = ROOT/'result/reports/maotai-20261031'
        destination.mkdir(exist_ok=True)
        target = destination/'贵州茅台研究报告.html'
        target.write_text(html)
        after = next(t for t in stored_tasks() if t['id']==ident)
        check('inspection consumed no further calls', after['ledger']['used']==before_usage)
        record.update(status='workflow_passed', report_path=str(target), charts=charts,
            report_sha256=hashlib.sha256(html.encode()).hexdigest(),
            report_copy_method='read_only_frozen_delivery', all_prd_accepted=False)
        save()
        print(json.dumps({k:record[k] for k in ('status','task_id','facts','logics','charts','model_calls','data_requests','report_path')},ensure_ascii=False),flush=True)
    except BaseException as error:
        record.update(status='review_failed', error=str(error))
        save()
        try:
            app.observe('review-failure')
            app.capture('review-failure')
        except Exception:
            pass
        raise
    finally:
        try:
            app.close()
        finally:
            save()
    return record


if __name__ == '__main__':
    print(json.dumps(preflight(), ensure_ascii=False))
