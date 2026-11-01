"""Authorized, single-launch startup frames from the unchanged signed DMG App.

This is separate from restart acceptance. It captures only the owned window,
does not authenticate or run research, and stops before the six-second visible
deadline. Screenshots require a separate visual review, not an automatic pass.
"""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import threading
import time
import shutil

from tests.accept_packaged_app_launcher import OBSERVER, ROOT, process_tree


def run():
    if os.environ.get('RESEARCHHELPER_ALLOW_VISIBLE_ACCEPTANCE') != '1':
        raise RuntimeError('须取得当次短暂显示窗口的明确授权；默认不启动App')
    selected = os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
    if not selected:
        raise RuntimeError('须明确指定候选回执')
    sys.path.insert(0, str(ROOT / 'packaging'))
    sys.path.insert(0, str(ROOT / 'maintenance'))
    from build_current import verify_current_build
    from candidate_acceptance import verify_mounted_bundle
    candidate = verify_current_build(selected, ROOT)
    output = Path(tempfile.mkdtemp(prefix='packaged-startup-frames-', dir=ROOT / 'result/acceptance'))
    record = dict(status='failed', candidate=str(Path(selected).resolve()), frames=[],
                  human_authorized_visible_window=True, formal_launcher_unchanged=True,
                  installed=False, global_input_posted=False, production_store_touched=False,
                  model_calls=0, real_data_requests=0, visible_deadline_seconds=6,
                  native_animation_visually_verified=False)
    process = timer = None
    owned = set()
    attached = False
    previous_pid = 0
    first_visible = None
    directory = None
    mount = None

    def observe(*arguments, environment=None):
        result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', str(OBSERVER),
                                 *map(str, arguments)], env=environment,
                                capture_output=True, text=True, timeout=3)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return json.loads(result.stdout)

    def terminate_at_deadline():
        if process is not None and process.poll() is None:
            process.terminate()

    def stop():
        nonlocal process
        if process is None:
            return
        owned.update(process_tree(process.pid))
        terminate_at_deadline()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        survivors = []
        for pid in owned - {process.pid}:
            try:
                os.kill(pid, 0)
                survivors.append(pid)
            except ProcessLookupError:
                pass
        record['launcher_exit_code'] = process.returncode
        record['owned_children_stopped'] = not survivors
        if survivors:
            raise RuntimeError('测试所属子进程未结束：' + str(survivors))
        process = None
        owned.clear()

    try:
        temporary = tempfile.mkdtemp(prefix='isolated-', dir=output)
        # No automatic recursive cleanup while an owned DMG could still be mounted.
        directory = Path(temporary)
        private_home = directory / 'private-home'
        private_home.mkdir()
        environment = dict(PATH='/usr/bin:/bin', LANG='en_US.UTF-8',
                           CFFIXED_USER_HOME=str(private_home),
                           RESEARCHHELPER_STORE=str(directory / 'research-store'),
                           PYTHONDONTWRITEBYTECODE='1')
        homes = observe('home', environment=environment)
        assert all(Path(value).resolve() == private_home.resolve() for value in homes.values())
        record['home_isolation'] = homes
        previous_pid = observe('foreground')['pid']
        mount = directory / 'disk'
        mount.mkdir()
        try:
            subprocess.run(['/usr/bin/hdiutil', 'attach', candidate['artifacts']['dmg']['path'],
                            '-readonly', '-nobrowse', '-mountpoint', str(mount)],
                           check=True, capture_output=True, timeout=30)
            attached = True
            bundle = mount / 'ResearchHelper.app'
            verify_mounted_bundle(bundle, candidate)
            subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)],
                           check=True, capture_output=True, timeout=30)
            metadata = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
            executable = bundle / 'Contents/MacOS' / metadata['CFBundleExecutable']
            original_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
            policy = '(version 1)(allow default)(deny network*)'
            policy += '(allow network* (local ip "localhost:*"))(allow network* (remote ip "localhost:*"))'
            policy += '(allow network* (local unix-socket))(allow network* (remote unix-socket))'
            for denied in (ROOT / 'core', ROOT / 'modules', ROOT / 'products',
                           Path('/opt/anaconda3'), Path('/Users/haoranxu/Desktop/OptionHelper'),
                           Path('/Users/haoranxu/Desktop/DataFetcher'),
                           Path('/Users/haoranxu/Library/Application Support/ResearchHelper')):
                for operation in ('file-read*', 'file-write*'):
                    policy += '(deny ' + operation + ' (subpath ' + json.dumps(str(denied)) + '))'
            with (output / 'launcher.log').open('w') as log:
                process = subprocess.Popen(['/usr/bin/sandbox-exec', '-p', policy, str(executable)],
                                           cwd=directory, env=environment, stdout=log, stderr=log)
                began = time.monotonic()
                window = None
                while process.poll() is None and time.monotonic() - began < 25:
                    try:
                        state = observe('status', process.pid)
                    except RuntimeError as error:
                        # LaunchServices may not yet expose the new PID. Keep
                        # the exact observation error rather than treating it
                        # as a successful native state or restarting the App.
                        record.setdefault('prewindow_observer_errors', []).append(str(error))
                        time.sleep(.05)
                        continue
                    windows = [item for item in state['windows'] if item['onscreen'] and
                               item['bounds'].get('Width', 0) >= 780]
                    if windows:
                        assert len(windows) == 1
                        first_visible = time.monotonic()
                        timer = threading.Timer(6, terminate_at_deadline)
                        timer.start()
                        assert state['bundle_id'] == metadata['CFBundleIdentifier']
                        assert Path(state['executable']).resolve() == executable.resolve()
                        window = windows[0]
                        break
                    time.sleep(.05)
                assert window is not None, '正式App未在限时内显示自己的主窗口'
                record['window_id'] = window['id']
                for index, target in enumerate((.15, .7, 1.4, 2.5, 4.2)):
                    delay = first_visible + target - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    assert process.poll() is None, '正式App提前退出'
                    frame = output / f'frame-{index}.png'
                    captured_at = time.monotonic() - first_visible
                    subprocess.run(['/usr/sbin/screencapture', '-x', '-o', '-l', str(window['id']), str(frame)],
                                   check=True, capture_output=True, timeout=1)
                    assert frame.is_file() and frame.stat().st_size > 0
                    record['frames'].append(dict(path=str(frame), elapsed_seconds=round(captured_at, 3),
                                                 sha256=hashlib.sha256(frame.read_bytes()).hexdigest()))
                assert len({frame['sha256'] for frame in record['frames']}) > 1, '未观察到不同画面，不能确认动态变化'
                stop()
                record['observed_visible_seconds'] = round(time.monotonic() - first_visible, 3)
                assert record['observed_visible_seconds'] <= 6, '窗口时长超出授权边界'
                assert hashlib.sha256(executable.read_bytes()).hexdigest() == original_hash
                record['executable_sha256'] = original_hash
                record['status'] = 'requires_visual_review'
        finally:
            if timer is not None:
                timer.cancel()
                timer.join(timeout=1)
            # The private home is removed only after the App and mount close.
            cleanup_errors = []
            try:
                stop()
            except Exception as error:
                cleanup_errors.append(str(error))
            if attached and process is None:
                try:
                    subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)],
                                   check=True, capture_output=True, timeout=30)
                    attached = False
                except Exception as error:
                    cleanup_errors.append(str(error))
            if previous_pid:
                try:
                    record['previous_foreground_restored'] = observe('restore', previous_pid)['restored']
                except Exception as error:
                    cleanup_errors.append(str(error))
            if cleanup_errors:
                record['cleanup_errors'] = cleanup_errors
                raise RuntimeError('测试回收未完成，保留临时目录供核对')
        record['mount_detached'] = not attached
        shutil.rmtree(directory)
        record['temporary_runtime_removed'] = not directory.exists()
        verify_current_build(selected, ROOT)
    except Exception as error:
        record['status'] = 'failed'
        record['error'] = str(error)
    finally:
        record['mount_detached'] = not attached
        record['launcher_stopped'] = process is None
        if directory is not None and directory.exists():
            record['preserved_runtime'] = str(directory)
        (output / 'receipt.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(dict(status=record['status'], receipt=str(output / 'receipt.json'))))
    if record['status'] == 'failed':
        raise SystemExit(1)


if __name__ == '__main__':
    run()
