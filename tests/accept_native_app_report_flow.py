"""Launch the unchanged DMG App and operate its actual UI through accessibility.

Independent tests only: fresh Store, private preferences, owned-process controls,
no production installation, no shared SDK, no JavaScript or response injection.
The first diagnostic pass opens/login/observes and closes without research.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import re

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / 'packaging/tests/native_app_accessibility.swift'


class ActualNativeApp:
    def __init__(self, output, record, candidate):
        self.output, self.record, self.candidate = output, record, candidate
        self.process = self.log = self.mount = self.binary = None
        self.attached = False
        self.bundle = None
        self.previous_pid = 0
        self.owned_backend_pids = set()

    def control(self, command, *arguments, check=True):
        values = [str(self.binary), command]
        if command not in ('trusted', 'frontmost'):
            values.append(str(self.process.pid))
        checked = subprocess.run(values + list(arguments), capture_output=True, text=True, timeout=15)
        result = json.loads(checked.stdout)
        if check and checked.returncode:
            raise RuntimeError('native control: ' + result.get('error', command) + ': ' + result.get('selector', ''))
        return result

    def prepare(self):
        spec = importlib.util.spec_from_file_location('rh_ax_build_options', ROOT / 'packaging/app/build_app.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        builder.OUTPUT = self.output / 'compile'
        builder.OUTPUT.mkdir()
        self.binary = builder.OUTPUT / 'native-app-accessibility'
        built = subprocess.run(['xcrun', 'swiftc', *builder.swift_options(), str(PROBE),
                                '-framework', 'AppKit', '-framework', 'ApplicationServices',
                                '-o', str(self.binary)], capture_output=True, text=True, timeout=120)
        (self.output / 'compiler.log').write_text(built.stdout + built.stderr)
        if built.returncode:
            raise RuntimeError('Accessibility helper compilation failed; see compiler.log')
        if self.control('trusted')['trusted'] is not True:
            raise RuntimeError('Accessibility unavailable; no permission popup or global-input fallback')
        self.previous_pid = self.control('frontmost')['pid']
        self.mount = self.output / 'mount'
        self.mount.mkdir()
        subprocess.run(['/usr/bin/hdiutil', 'attach', self.candidate['artifacts']['dmg']['path'],
                        '-readonly', '-nobrowse', '-mountpoint', str(self.mount)],
                       check=True, capture_output=True, timeout=30)
        self.attached = True
        bundle = self.mount / 'ResearchHelper.app'
        sys.path.insert(0, str(ROOT / 'maintenance'))
        from candidate_acceptance import verify_mounted_bundle
        verify_mounted_bundle(bundle, self.candidate)
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)],
                       check=True, capture_output=True, timeout=30)
        self.bundle = bundle

    def launch(self):
        # Network denial is appropriate for the backend-only test harness, not
        # its AppKit/WebKit parent and OS services. Native snapshots and file
        # panels must run under the installed App's GUI policy. Financial
        # dispatch remains locked by the private execution policy until the
        # explicitly chosen live case; no supplier credentials are installed.
        policy = '(version 1)(allow default)'
        self.record['normal_native_gui_services_allowed'] = True
        for path in (ROOT / 'core', ROOT / 'modules', ROOT / 'products', Path('/opt/anaconda3'),
                     Path('/Users/haoranxu/Desktop/OptionHelper'), Path('/Users/haoranxu/Desktop/DataFetcher'),
                     Path('/Applications/ResearchHelper.app'),
                     Path('/Users/haoranxu/Library/Application Support/ResearchHelper')):
            policy += '(deny file-read* file-write* (subpath ' + json.dumps(str(path)) + '))'
        preferences = self.output / 'private-preferences'
        preferences.mkdir(exist_ok=True)
        self.log = (self.output / 'native-launch.log').open('a')
        environment = {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8',
            'RESEARCHHELPER_STORE': str(self.output / 'store'), 'CFFIXED_USER_HOME': str(preferences),
            'PYTHONDONTWRITEBYTECODE': '1'}
        executable = self.bundle / 'Contents/MacOS/ResearchHelper'
        if not executable.is_file():
            raise RuntimeError('Expected actual native App launcher absent')
        self.process = subprocess.Popen(['/usr/bin/sandbox-exec', '-p', policy, str(executable)],
            cwd=self.output, env=environment, stdout=self.log, stderr=self.log)
        self.record.update(actual_packaged_launcher=True, recompiled_shell=False,
                           production_paths_denied=True, script_injected=False, global_input_posted=False)

    def backend_url(self):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            table = subprocess.check_output(['/bin/ps', '-axo', 'pid=,ppid='], text=True)
            pairs = [tuple(map(int, line.split())) for line in table.splitlines()]
            owned = {self.process.pid}
            while True:
                expanded = owned | {pid for pid, parent in pairs if parent in owned}
                if expanded == owned:
                    break
                owned = expanded
            checked = subprocess.run(['/usr/sbin/lsof', '-n', '-P', '-a', '-p', ','.join(map(str, owned)),
                                      '-iTCP', '-sTCP:LISTEN', '-Fn'], capture_output=True, text=True, timeout=8)
            ports = re.findall(r'^n127\.0\.0\.1:(\d+)$', checked.stdout, flags=re.M)
            if len(set(ports)) == 1:
                self.owned_backend_pids |= owned - {self.process.pid}
                return 'http://127.0.0.1:' + ports[0] + '/'
            time.sleep(.3)
        raise RuntimeError('Could not locate the owned packaged backend loopback listener')

    def observe(self, label):
        data = self.control('snapshot')
        (self.output / (label + '.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2))
        return data

    def wait_control(self, selector, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self.control('snapshot')
            if any((any(selector[1:] in item[key] for key in ('id', 'title', 'description')) if selector.startswith('~')
                    else selector in (item['id'], item['title'], item['description'])) and item['enabled']
                   for item in data['items']):
                return data
            if self.process.poll() is not None:
                raise RuntimeError('Actual native App exited before ' + selector)
            time.sleep(.5)
        self.observe('failed-' + ('login' if selector == '登录' else 'workspace'))
        raise RuntimeError('Actual App control unavailable: ' + selector)

    def login(self):
        state = self.control('snapshot')
        if any(item['title'] == '任务要求' for item in state['items']):
            return
        self.wait_control('登录')
        self.control('set', '账号', 'admin')
        self.control('set', '密码', '66666666')
        self.control('press', '登录')
        self.wait_control('任务要求')
        self.wait_control('收起任务栏')
        self.control('activate')
        time.sleep(1.5)
        self.wait_control('收起任务栏')

    def press(self, label):
        self.wait_control(label)
        self.control('press', label)

    def capture(self, label):
        import ctypes
        library = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        library.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        if not library.CGPreflightScreenCaptureAccess():
            self.record['window_capture_unavailable'] = True
            return
        windows = [item for item in self.control('snapshot')['windows'] if item['layer'] == 0]
        if not windows:
            return
        window_id = max(windows, key=lambda item: item['bounds'].get('Width', 0))['id']
        destination = self.output / (label + '.png')
        checked = subprocess.run(['/usr/sbin/screencapture', '-x', '-l', str(window_id), str(destination)],
                                 capture_output=True, timeout=10)
        if checked.returncode == 0 and destination.exists():
            self.record.setdefault('screens', []).append(str(destination))

    def open_report(self, prefix, client, task):
        self.press('~研究报告')
        self.wait_control('保存HTML')
        self.wait_control('完整研究报告')
        time.sleep(1)
        state = self.observe(prefix + '-report')
        report = client.request('api/tasks/' + task['id'] + '/report', html=True)
        (self.output / (prefix + '-report.html')).write_text(report)
        self.record[prefix + '_report_sha256'] = hashlib.sha256(report.encode()).hexdigest()
        self.record[prefix + '_report_frozen'] = bool(task['delivery'])
        self.capture(prefix + '-report')
        if not any(item['role'] == 'AXWebArea' and '完整研究报告' in (item['title'], item['description'])
                   for item in state['items']):
            raise RuntimeError('Saved report did not open in actual App WebKit')
        return report

    def export_report(self, prefix, report):
        self.press('保存HTML')
        self.wait_control('存储')
        self.control('key', 'folder')
        time.sleep(.4)
        self.observe(prefix + '-save-panel')
        self.control('set', '@focused', str(self.output))
        self.control('key', 'return')
        time.sleep(.4)
        self.observe(prefix + '-save-location')
        self.control('set', '@focused', prefix + '-export.html')
        self.press('存储')
        target = self.output / (prefix + '-export.html')
        deadline = time.monotonic() + 8
        while not target.exists() and time.monotonic() < deadline:
            time.sleep(.2)
        if not target.exists() or target.read_text() != report:
            raise RuntimeError('Native save panel did not export the exact frozen report to the private folder')
        self.record[prefix + '_native_export_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()

    def close(self):
        restore_previous=False
        if self.process and self.binary and self.binary.exists():
            try:restore_previous=self.control('frontmost')['pid']==self.process.pid
            except (subprocess.SubprocessError,ValueError,KeyError):pass
        if self.process:
            table = subprocess.check_output(['/bin/ps', '-axo', 'pid=,ppid='], text=True)
            pairs = [tuple(map(int, line.split())) for line in table.splitlines()]
            owned = {self.process.pid}
            while True:
                expanded = owned | {pid for pid, parent in pairs if parent in owned}
                if expanded == owned:
                    break
                owned = expanded
            self.owned_backend_pids |= owned - {self.process.pid}
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        executable = self.bundle / 'Contents/Resources/backend/researchhelper-server/researchhelper-server' if self.bundle else None
        for pid in self.owned_backend_pids:
            # PyInstaller has a bootloader and a child. Preserve ownership
            # captured before quitting; after a forced UI exit the child may
            # be reparented to launchd. Validate its exact mounted binary again.
            checked = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'comm='],
                                     capture_output=True, text=True, timeout=5)
            if checked.stdout.strip() == str(executable):
                os.kill(pid, 15)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            living = []
            for pid in self.owned_backend_pids:
                checked = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'comm='],
                                         capture_output=True, text=True, timeout=5)
                if checked.stdout.strip() == str(executable):
                    living.append(pid)
            if not living:
                break
            time.sleep(.2)
        if living:
            raise RuntimeError('Owned bundled backend has not stopped; no unrelated process is terminated')
        if self.log:
            self.log.close()
            self.log = None
        if restore_previous and self.previous_pid and self.binary and self.binary.exists():
            subprocess.run([str(self.binary), 'activate', str(self.previous_pid)],
                           capture_output=True, timeout=10)
        self.record['owned_app_stopped'] = not self.process or self.process.poll() is not None
        self.record['owned_backends_stopped'] = True

    def stop(self):
        self.close()
        if self.attached:
            subprocess.run(['/usr/bin/hdiutil', 'detach', str(self.mount)],
                           check=True, capture_output=True, timeout=30)
            self.attached = False
        self.record['mount_detached'] = not self.attached


def run():
    if os.environ.get('RESEARCHHELPER_ALLOW_VISIBLE_ACCEPTANCE') != '1':
        raise RuntimeError('This independent actual-App test requires current visible-window authorization')
    selected = Path(os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve()
    sys.path.insert(0, str(ROOT / 'packaging'))
    from build_current import verify_current_build
    candidate = verify_current_build(selected, ROOT)
    output = Path(tempfile.mkdtemp(prefix='actual-native-report-flow-', dir=ROOT / 'result/acceptance'))
    record = {'status': 'executing', 'candidate': str(selected), 'checks': [],
              'installed': False, 'production_store_touched': False, 'model_calls': 0,
              'real_data_requests': 0, 'full_real_research_verified': False,
              'probe_sha256': hashlib.sha256(PROBE.read_bytes()).hexdigest()}
    app = ActualNativeApp(output, record, candidate)
    client = configuration = settings = None
    protected = {}
    task = None
    try:
        app.prepare()
        app.launch()
        login = app.wait_control('登录')
        record['native_window_opened'] = bool(login['windows'])
        app.observe('login')
        app.login()
        app.observe('workspace')
        record['checks'].append('actual DMG App launched and logged in through accessibility')
        record['status'] = 'diagnostic_passed'
        if os.environ.get('RESEARCHHELPER_NATIVE_FLOW') in ('offline_report', 'live_report'):
            from tests.accept_current_live_document_reading import PrivateAppClient
            client = PrivateAppClient(app.backend_url())
            boot = client.request('api/bootstrap')
            assert not boot['tasks'] and boot['execution']['mode'] == 'offline'
            app.press('账户与设置，admin')
            app.press('设置中心')
            app.press('存储与导出')
            app.press('合成案例')
            example = next(item for item in boot['cases'] if item['id'] == 'etf')
            app.press('~' + example['title'])
            app.press('开始研究')
            deadline = time.monotonic() + 25
            task = None
            while time.monotonic() < deadline:
                tasks = client.request('api/bootstrap')['tasks']
                if tasks and tasks[0]['status'] in ('已完成', '部分完成', '失败', '已取消'):
                    task = tasks[0]
                    break
                time.sleep(.4)
            assert task and task['synthetic'] and task['fixture'] == 'etf'
            record['offline_research_status'] = task['status']
            record['offline_error'] = task.get('error')
            record['offline_gaps'] = task.get('gaps')
            assert task['status'] == '已完成' and task['delivery'], task.get('error') or task.get('gaps')
            record['offline_task_id'] = task['id']
            record['offline_ledger'] = task['ledger']
            record['checks'].append('actual UI selected and ran the labelled offline ETF case')
            report = app.open_report('offline', client, task)
            app.export_report('offline', report)
            record['checks'].append('actual native report opened and exact frozen HTML exported')
            app.close()
            client = None
            app.launch()
            app.wait_control('登录')
            app.login()
            client = PrivateAppClient(app.backend_url())
            reopened = client.request('api/tasks/' + task['id'])
            assert reopened['ledger'] == task['ledger'] and reopened['delivery'] == task['delivery']
            assert client.request('api/tasks/' + task['id'] + '/report', html=True) == report
            app.press('~' + example['title'])
            app.open_report('restart', client, reopened)
            record['checks'].append('actual App restart preserved the frozen report and budget ledger')
            record['status'] = 'offline_report_passed'
        if os.environ.get('RESEARCHHELPER_NATIVE_FLOW') == 'live_report':
            from tests.accept_current_live_document_reading import PRODUCTION, eligible_configuration, file_identity
            sys.path.insert(0, str(ROOT / 'core'))
            from bootstrap import configure_imports
            configure_imports()
            from researchhelper.model import ModelSettings
            protected = {name: file_identity(PRODUCTION / name) for name in
                         ('model-settings.json', 'source-settings.json', 'execution-policy.json')}
            configuration = eligible_configuration(ModelSettings(PRODUCTION).read(private=True))
            settings = ModelSettings(output / 'store')
            client.request('api/settings/model', configuration)
            client.request('api/settings/execution', {'enabled': True, 'confirm': True})
            app.close()
            client = None
            app.launch()
            app.wait_control('登录')
            app.login()
            client = PrivateAppClient(app.backend_url())
            app.press('新建任务')
            app.press('新任务范围与预算')
            app.press('运行预算')
            app.control('set', '数据请求上限', '1')
            app.control('set', '模型调用上限', '5')
            app.control('set', '执行秒数上限', '240')
            app.press('保存设置')
            app.press('关闭')
            question = ('仅解释ETF市场成交价格、基金单位净值和总回报这三个通用概念的区别与用途边界。'
                        '不研究具体基金，不获取行情，不作历史判断，不计算收益，不给买卖建议。')
            app.control('set', '任务要求', question)
            app.press('发送研究问题')
            record['actual_ui_live_question'] = question
            record['model_endpoint_changed'] = False
            record['independent_financial_quality'] = False
            record['max_real_model_calls'] = 4
            deadline = time.monotonic() + 255
            task = None
            while time.monotonic() < deadline:
                current = next((item for item in client.request('api/bootstrap')['tasks']
                                if item['question'] == question and not item['synthetic']), None)
                if current:
                    task = current
                    if current['status'] in ('已完成', '部分完成', '失败', '已取消'):
                        break
                time.sleep(.7)
            assert task and task['question'] == question
            record.update(live_task_id=task['id'], live_research_status=task['status'],
                          live_ledger=task['ledger'], model_calls=task['ledger']['used']['model_calls'],
                          real_data_requests=task['ledger']['used']['data_requests'],
                          live_question_statuses=[{'question': item['question'], 'status': item['status']}
                                                 for item in task['questions']])
            assert 0 < record['model_calls'] <= 4 and record['real_data_requests'] == 0
            app.observe('live-result')
            app.capture('live-result')
            record['live_gaps'] = task.get('gaps')
            record['live_error'] = task.get('error')
            assert task['status'] == '已完成' and task['delivery'], 'Actual UI model research did not fully complete'
            assert all(item['status'] == '已回答' for item in task['questions'] if item['required'])
            report = app.open_report('live', client, task)
            app.export_report('live', report)
            record['checks'].append('ordinary question submitted through actual App used real configured model and delivered')
            record['status'] = 'scoped_passed'
        verify_current_build(selected, ROOT)
    except Exception as error:
        record.update(status='failed', error=str(error))
    finally:
        if settings:
            try:
                if client:
                    client.request('api/settings/execution', {'enabled': False, 'confirm': True})
                    client.request('api/settings/model', dict(configuration, api_key='', clear_key=True))
                settings.save(dict(configuration, api_key='', clear_key=True))
                record['probe_credentials_removed'] = not list((output / 'store/credentials').glob('*.secret'))
                from tests.accept_current_live_document_reading import PRODUCTION, file_identity
                record['production_configuration_unchanged'] = all(file_identity(PRODUCTION / name) == value
                                                                    for name, value in protected.items())
            except Exception:
                record.update(status='failed', credential_cleanup_failed=True)
        try:
            app.stop()
        except Exception as error:
            record.update(status='failed', cleanup_error=str(error))
        (output / 'receipt.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({'status': record['status'], 'receipt': str(output / 'receipt.json'),
                      'error': record.get('error')}, ensure_ascii=False))
    return record


if __name__ == '__main__':
    raise SystemExit(0 if run()['status'] in ('diagnostic_passed', 'offline_report_passed', 'scoped_passed') else 1)
