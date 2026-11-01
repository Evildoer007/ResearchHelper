"""Explicitly authorized formal DMG App startup and isolated restart check.

Launch the signed executable unchanged, not the test controller. Observe only
its PID/window, query its own backend, and terminate all owned processes.
"""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]
OBSERVER = ROOT / 'packaging/tests/packaged_launcher_observer.js'


def process_tree(root_pid):
    rows = subprocess.check_output(['/bin/ps', '-axo', 'pid=,ppid='], text=True)
    parents = [tuple(map(int, line.split())) for line in rows.splitlines() if line.strip()]
    owned = {root_pid}
    while True:
        expanded = owned | {pid for pid, parent in parents if parent in owned}
        if expanded == owned:
            return owned
        owned = expanded


def listener(pids):
    result = subprocess.run(['/usr/sbin/lsof', '-nP', '-a', '-p', ','.join(map(str, pids)),
                             '-iTCP', '-sTCP:LISTEN', '-Fpn'], capture_output=True, text=True, timeout=5)
    addresses = [line[1:] for line in result.stdout.splitlines() if line.startswith('n')]
    if not addresses:
        return None
    assert len(addresses) == 1 and addresses[0].startswith('127.0.0.1:'), 'Unexpected owned listener'
    return 'http://' + addresses[0]


def run():
    if os.environ.get('RESEARCHHELPER_ALLOW_VISIBLE_ACCEPTANCE') != '1':
        raise RuntimeError('须先取得当次原生启动检查授权')
    selected = os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
    if not selected:
        raise RuntimeError('须指定当次候选回执')
    sys.path.insert(0, str(ROOT / 'packaging'))
    sys.path.insert(0, str(ROOT / 'maintenance'))
    from build_current import verify_current_build
    from candidate_acceptance import verify_mounted_bundle
    candidate = verify_current_build(selected, ROOT)
    output = Path(tempfile.mkdtemp(prefix='packaged-app-launcher-', dir=ROOT / 'result/acceptance'))
    record = {'status': 'failed', 'candidate': str(Path(selected).resolve()), 'checks': [],
              'human_authorized_visible_window': True, 'test_entry_replaced_launcher': False,
              'installed': False, 'production_store_touched': False, 'global_input_posted': False,
              'real_data_requests': 0, 'model_calls': 0, 'runs': [], 'mount_detached': False}
    attached = False
    process = None
    owned = set()
    observer = OBSERVER
    previous_pid = 0

    def check(name, condition):
        assert condition, name
        record['checks'].append(name)
        print(name, file=sys.stderr, flush=True)

    def observe(*arguments, environment=None):
        result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', str(observer), *map(str, arguments)], env=environment,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def stop():
        nonlocal process
        if process is None:
            return
        owned.update(process_tree(process.pid))
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        record['runs'][-1]['launcher_exit_code'] = process.returncode
        surviving = []
        for pid in owned - {process.pid}:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            surviving.append(pid)
        record['runs'][-1]['owned_children_stopped'] = not surviving
        if surviving:
            raise RuntimeError('Owned candidate children remain: ' + str(surviving))
        process = None
        owned.clear()

    @contextmanager
    def runtime_workspace():
        nonlocal attached
        with tempfile.TemporaryDirectory(prefix='isolated-', dir=output) as folder:
            try:
                yield Path(folder)
            finally:
                # Close the real App before removing its private WebKit home.
                stop()
                if attached:
                    subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)],
                                   check=True, capture_output=True, timeout=30)
                    attached = False
                if previous_pid and observer and observer.exists():
                    record['previous_foreground_restored'] = observe('restore', previous_pid)['restored']

    try:
        with runtime_workspace() as directory:
            record['observer_sha256'] = hashlib.sha256(OBSERVER.read_bytes()).hexdigest()
            private_home = directory / 'private-home'
            private_home.mkdir()
            environment = {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8',
                           'CFFIXED_USER_HOME': str(private_home),
                           'RESEARCHHELPER_STORE': str(directory / 'research-store'),
                           'PYTHONDONTWRITEBYTECODE': '1'}
            homes = observe('home', environment=environment)
            record['home_isolation'] = homes
            check('Foundation and FileManager use the private home',
                  all(Path(value).resolve() == private_home.resolve() for value in homes.values()))
            previous_pid = observe('foreground')['pid']
            mount = directory / 'disk'
            mount.mkdir()
            subprocess.run(['/usr/bin/hdiutil', 'attach', candidate['artifacts']['dmg']['path'],
                            '-readonly', '-nobrowse', '-mountpoint', str(mount)],
                           check=True, capture_output=True, timeout=30)
            attached = True
            bundle = mount / 'ResearchHelper.app'
            verify_mounted_bundle(bundle, candidate)
            subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)],
                           check=True, capture_output=True, timeout=30)
            check('actual DMG bundle and strict signature verified', True)
            with (bundle / 'Contents/Info.plist').open('rb') as file:
                metadata = plistlib.load(file)
            executable = bundle / 'Contents/MacOS' / metadata['CFBundleExecutable']
            record['executable_sha256'] = hashlib.sha256(executable.read_bytes()).hexdigest()
            policy = '(version 1)(allow default)(deny network*)'
            policy += '(allow network* (local ip "localhost:*"))(allow network* (remote ip "localhost:*"))'
            policy += '(allow network* (local unix-socket))(allow network* (remote unix-socket))'
            for denied in [ROOT / 'core', ROOT / 'modules', ROOT / 'products', Path('/opt/anaconda3'),
                           Path('/Users/haoranxu/Desktop/OptionHelper'), Path('/Users/haoranxu/Desktop/DataFetcher'),
                           Path('/Users/haoranxu/Library/Application Support/ResearchHelper')]:
                policy += '(deny file-read* (subpath ' + json.dumps(str(denied)) + '))'
                policy += '(deny file-write* (subpath ' + json.dumps(str(denied)) + '))'
            task_id = None
            for number in (1, 2):
                run_record = {'number': number, 'window_visible': False, 'owned_children_stopped': False}
                record['runs'].append(run_record)
                with (output / f'launcher-{number}.log').open('w') as errors:
                    process = subprocess.Popen(['/usr/bin/sandbox-exec', '-p', policy, str(executable)],
                                               cwd=directory, env=environment, stdout=errors, stderr=errors)
                    owned = {process.pid}
                    began = time.monotonic()
                    base = None
                    while process.poll() is None and time.monotonic() - began < 25:
                        owned.update(process_tree(process.pid))
                        base = listener(owned)
                        if base:
                            break
                        time.sleep(.2)
                    check(f'run {number}: formal launcher starts its own loopback backend', base is not None)
                    run_record['backend_ready_seconds'] = round(time.monotonic() - began, 2)
                    status = observe('status', process.pid)
                    run_record['native'] = status
                    check(f'run {number}: native executable identity matches bundle',
                          status['bundle_id'] == metadata['CFBundleIdentifier'] and
                          Path(status['executable']).resolve() == executable.resolve())
                    windows = [window for window in status['windows'] if window['bounds'].get('Width', 0) >= 780]
                    check(f'run {number}: formal App has an onscreen main window', len(windows) == 1)
                    run_record['window_visible'] = True
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    def request(route, body=None, cookie=None, token=None):
                        headers = {'Cookie': cookie} if cookie else {}
                        if body is not None:
                            headers.update({'Content-Type': 'application/json', 'X-ResearchHelper-Token': token})
                        call = urllib.request.Request(base + route, headers=headers,
                            data=json.dumps(body).encode() if body is not None else None)
                        with opener.open(call, timeout=5) as response:
                            return response.read(), response.headers
                    auth = json.loads(request('/api/auth/status')[0])
                    check(f'run {number}: isolated preset login is configured', auth['configured'] and auth['authentication_required'])
                    _, headers = request('/api/auth/login', {'account': 'admin', 'password': '66666666', 'remember': False}, token=auth['token'])
                    cookie = headers.get('Set-Cookie').split(';')[0]
                    state = json.loads(request('/api/bootstrap', cookie=cookie)[0])
                    check(f'run {number}: actual backend is offline and uses temporary archive',
                          state['offline'] and Path(state['storage_location']).resolve() == Path(environment['RESEARCHHELPER_STORE']).resolve())
                    check(f'run {number}: six public report cases are available', len(state['history']) == 6)
                    if number == 1:
                        check('new isolated archive is empty', not state['tasks'])
                        task = json.loads(request('/api/tasks', {'question': '临时原生启动恢复验收，不执行研究'}, cookie, auth['token'])[0])
                        task_id = task['id']
                        check('temporary task created without model or data calls',
                              task['ledger']['used']['data_requests'] == task['ledger']['used']['model_calls'] == 0)
                    else:
                        saved = next((task for task in state['tasks'] if task['id'] == task_id), None)
                        check('formal App restart restores the same temporary task', saved is not None)
                        check('restart does not reset or consume task budget',
                              saved['ledger']['used']['data_requests'] == saved['ledger']['used']['model_calls'] == 0)
                    screenshot = output / f'formal-launcher-window-{number}.png'
                    # Allow the copied OH startup transition to settle. Image
                    # existence is not a login-readiness assertion: inspect the
                    # two owned-window images before claiming visible-page QA.
                    time.sleep(6)
                    captured = subprocess.run(['/usr/sbin/screencapture', '-x', '-o', '-l', str(windows[0]['id']), str(screenshot)],
                                              capture_output=True, text=True, timeout=8)
                    run_record['owned_window_capture'] = captured.returncode == 0 and screenshot.is_file()
                    run_record['image_requires_visual_review'] = True
                    check(f'run {number}: actual App window captured without desktop fallback', run_record['owned_window_capture'])
                    check(f'run {number}: formal App and own backend remain alive', process.poll() is None)
                    stop()
                    check(f'run {number}: normal SIGTERM handler closes App and backend',
                          run_record['launcher_exit_code'] == 0 and run_record['owned_children_stopped'])
            check('signed executable remains unchanged', record['executable_sha256'] == hashlib.sha256(executable.read_bytes()).hexdigest())
            subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)], check=True, capture_output=True, timeout=30)
            attached = False
            if previous_pid:
                record['previous_foreground_restored'] = observe('restore', previous_pid)['restored']
        record['temporary_runtime_removed'] = not directory.exists()
        check('temporary runtime and native preferences removed', record['temporary_runtime_removed'])
        verify_current_build(selected, ROOT)
        check('PID/window observer unchanged during check',
              record['observer_sha256'] == hashlib.sha256(OBSERVER.read_bytes()).hexdigest())
        record['status'] = 'scoped_passed'
    except Exception as error:
        record['error'] = str(error)
    finally:
        try:
            stop()
        except Exception as error:
            record['cleanup_error'] = str(error)
        if attached:
            result = subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)], capture_output=True, timeout=30)
            attached = result.returncode != 0
        record['mount_detached'] = not attached
        record['launcher_stopped'] = process is None or process.poll() is not None
        (output / 'receipt.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({'status': record['status'], 'checks': len(record['checks']),
                      'receipt': str(output / 'receipt.json'), 'error': record.get('error')}, ensure_ascii=False))
    if record['status'] != 'scoped_passed':
        raise SystemExit(1)


if __name__ == '__main__':
    run()
