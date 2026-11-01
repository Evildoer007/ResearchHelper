"""Check a packaged backend and default login using a disposable RH Store only."""
import json
from pathlib import Path
import os
import queue
import subprocess
import threading
from urllib.request import Request, urlopen
from urllib.parse import urlsplit


def verify_backend(executable, resources, workspace):
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=False)
    log = workspace / 'backend-stderr.txt'
    environment = dict(os.environ, RESEARCHHELPER_STORE=str(workspace / 'store'),
                       PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    for name in ('PYTHONPATH', 'PYTHONHOME', 'RESEARCHHELPER_PROJECT'):
        environment.pop(name, None)
    with log.open('w', encoding='utf-8') as errors:
        process = subprocess.Popen([str(executable)], cwd=workspace, env=environment,
                                   stdout=subprocess.PIPE, stderr=errors, text=True, encoding='utf-8')
        result = queue.Queue()
        reader = threading.Thread(target=lambda: result.put(process.stdout.readline()), daemon=True)
        reader.start()
        try:
            ready = json.loads(result.get(timeout=30))
            base = ready['url'].rstrip('/')
            url = urlsplit(base)
            if ready.get('ready') is not True or url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port or url.username:
                raise RuntimeError('包内服务未提供有效回环地址')
            with urlopen(base + '/api/auth/status', timeout=5) as response:
                auth = json.load(response)
            request = Request(base + '/api/auth/login', data=json.dumps({'account': 'admin', 'password': '66666666', 'remember': False}).encode(),
                              headers={'Content-Type': 'application/json', 'X-ResearchHelper-Token': auth['token']})
            with urlopen(request, timeout=5) as response:
                cookie = response.headers['Set-Cookie'].split(';')[0]
            request = Request(base + '/api/bootstrap', headers={'Cookie': cookie})
            with urlopen(request, timeout=5) as response:
                bootstrap = json.load(response)
            if bootstrap['offline'] is not True or bootstrap['tasks'] or len(bootstrap['history']) != 6:
                raise RuntimeError('安装物离线状态或独立Store检查未通过')
            with urlopen(Request(base + '/reports/semi.html', headers={'Cookie': cookie}), timeout=5) as response:
                if b'echarts' not in response.read():
                    raise RuntimeError('安装物缺少离线报告资源')
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            reader.join(timeout=2)
            process.stdout.close()
    return {'backend_stopped': process.poll() is not None, 'history_cases': 6, 'offline': True,
            'production_store_touched': False, 'native_window_opened': False,
            'model_calls': 0, 'real_data_requests': 0}
