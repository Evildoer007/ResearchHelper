"""Visible build stages with complete logs and bounded process-tree cleanup."""
from contextlib import contextmanager
import os
import queue
import re
import signal
import subprocess
import threading
import time


def progress(label, message):
    print('[' + label + '] ' + message, flush=True)


@contextmanager
def stage(label, message):
    started = time.monotonic()
    progress(label, message)
    try:
        yield
    except BaseException:
        progress(label, '未完成，已用时' + str(round(time.monotonic() - started, 1)) + '秒')
        raise
    else:
        progress(label, '完成，用时' + str(round(time.monotonic() - started, 1)) + '秒')


def stop_process_tree(process):
    if os.name == 'nt':
        if process.poll() is None:
            try:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               capture_output=True, timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
            if process.poll() is None:
                process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == 'nt':
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=5)
    finally:
        # The leader may exit before a TERM-ignoring descendant. This process
        # group was created solely for our build; close remaining children too.
        if os.name != 'nt':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def run_logged(command, *, cwd, stdout, stderr=None, check=True, on_progress=None):
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding='utf-8', errors='replace', **options)
    pending = queue.Queue()
    def read():
        try:
            for line in process.stdout:
                pending.put(line)
        finally:
            pending.put(None)
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    activity = time.monotonic()
    started = activity
    try:
        while True:
            try:
                line = pending.get(timeout=1)
            except queue.Empty:
                if time.monotonic() - activity >= 30:
                    message = '构建仍在运行，已用时' + str(round(time.monotonic() - started)) + '秒；完整输出持续写入日志。'
                    progress('构建', message)
                    stdout.write(message + '\n'); stdout.flush()
                    activity = time.monotonic()
                continue
            if line is None:
                break
            stdout.write(line); stdout.flush()
            if re.match(r'^\[(?:\d+/7|App \d+/5|Windows \d+/5)\]', line):
                print(line.rstrip(), flush=True)
                activity = time.monotonic()
                if on_progress:
                    on_progress(line.rstrip())
        code = process.wait()
        if check and code:
            raise subprocess.CalledProcessError(code, command)
        return subprocess.CompletedProcess(command, code)
    except BaseException:
        try:
            stop_process_tree(process)
        except Exception as cleanup_error:
            stdout.write('构建进程回收未确认：' + type(cleanup_error).__name__ + '\n'); stdout.flush()
        raise
    finally:
        reader.join(timeout=3)
        if not reader.is_alive():
            process.stdout.close()
