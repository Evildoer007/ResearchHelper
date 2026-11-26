"""Exercise the packaged backend with a disposable ResearchHelper Store."""
import json
from pathlib import Path
import os
import queue
import subprocess
from tempfile import TemporaryDirectory
import threading
from urllib.parse import urlsplit


def _ready_url(lines):
    try:
        ready = json.loads(lines.get(timeout=30))
        url = urlsplit(ready['url'])
        if (ready.get('ready') is not True or url.scheme != 'http' or url.hostname != '127.0.0.1'
                or not url.port or url.username is not None or url.password is not None
                or url.path not in ('', '/') or url.query or url.fragment):
            raise ValueError
    except (queue.Empty, KeyError, TypeError, ValueError) as error:
        raise RuntimeError('包内服务未返回有效回环就绪状态') from error
    return ready['url'].rstrip('/')


def _stop_backend(process):
    """Use the desktop pipe first, then reap an unresponsive verification process."""
    try:
        process.stdin.close()
    except OSError:
        pass
    try:
        return process.wait(timeout=8) == 0
    except subprocess.TimeoutExpired:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           capture_output=True, check=False, timeout=20)
        else:
            process.kill()
        process.wait(timeout=10)
        return False


def verify_backend(executable, resources, workspace, *, reports=False):
    from accept_current import AppClient, accept_app_reports

    workspace = Path(workspace)
    if not Path(executable).is_file() or not Path(resources).is_dir():
        raise RuntimeError('缺少包内服务或报告资源目录')
    workspace.mkdir(parents=True, exist_ok=False)
    log = workspace / 'backend-stderr.txt'
    environment = {key: value for key,value in os.environ.items()
                   if not key.upper().startswith('RESEARCHHELPER_') and key.upper() not in ('PYTHONPATH', 'PYTHONHOME')}
    environment.update(RESEARCHHELPER_DESKTOP_LIFETIME='stdin',
                       PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    outcome = {'history_cases': 6, 'offline': True, 'production_store_touched': False,
               'native_window_opened': False, 'model_calls': 0, 'real_data_requests': 0}
    with TemporaryDirectory(prefix='store-', dir=workspace) as store, log.open('w', encoding='utf-8') as errors:
        environment['RESEARCHHELPER_STORE'] = str(Path(store) / 'research')
        process = subprocess.Popen([str(executable)], cwd=workspace, env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True, encoding='utf-8',
            errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        lines = queue.Queue()

        def read_stdout():
            lines.put(process.stdout.readline(65536))
            for _ in process.stdout:
                pass

        reader = threading.Thread(target=read_stdout, daemon=True)
        reader.start()
        try:
            base = _ready_url(lines)
            client = AppClient(base)
            if client.bootstrap['offline'] is not True or len(client.bootstrap['history']) != outcome['history_cases']:
                raise RuntimeError('安装物离线状态或独立Store检查未通过')
            if 'echarts' not in client.call('/reports/semi.html', text=True):
                raise RuntimeError('安装物缺少离线报告资源')
            if reports:
                outcome['reports'] = accept_app_reports(base, client=client)
        finally:
            graceful = _stop_backend(process)
            reader.join(timeout=2)
            if not reader.is_alive():
                process.stdout.close()
    if not graceful or reader.is_alive():
        raise RuntimeError('临时安装物的后端未正常退出')
    return dict(outcome, backend_stopped=True, graceful_shutdown=True, temporary_store_removed=True)


def verify_installed_reports(executable, resources, skill_archive, workspace, root):
    """Check real output from the installed backend and the matching Skill ZIP."""
    from accept_current import accept_skill_reports, verify_report_parity
    from source_snapshot import digest

    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=False)
    app = verify_backend(executable, resources, workspace / 'backend', reports=True)
    app_reports = app.pop('reports')
    archive = {'path': str(skill_archive), 'sha256': digest(skill_archive)}
    with TemporaryDirectory(prefix='skill-', dir=workspace) as folder:
        skill = accept_skill_reports({'platform': 'windows', 'artifacts': {'skill': archive}}, root, Path(folder))
    verify_report_parity(app_reports, skill)
    return app, {'version': 1, 'status': 'passed', 'dataset_parity': True, 'skill_sha256': archive['sha256'],
                 'app_reports': app_reports, 'skill_reports': skill, 'fixtures_only': True}
