"""Start only the candidate's embedded backend with an isolated disposable store."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import tempfile
import urllib.request
from candidate_acceptance import selected_candidate, verify_candidate, verify_mounted_bundle

ROOT = Path(__file__).resolve().parents[1]
RECEIPTS = Path(os.environ.get('RESEARCHHELPER_ACCEPTANCE_DIR') or ROOT / 'result/maintenance/oh-layout-20261010').resolve()


def run():
    if not RECEIPTS.is_relative_to(ROOT / 'result/maintenance'):
        raise RuntimeError('Acceptance receipts must stay in the ResearchHelper maintenance area')
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    receipt_path = selected_candidate(ROOT)
    candidate = verify_candidate(receipt_path, ROOT)
    payload = Path(candidate['payload'])
    snapshot = Path(candidate['snapshot'])
    bundle = Path(os.environ.get('RESEARCHHELPER_ACCEPTANCE_BUNDLE', '')).resolve()
    if not os.environ.get('RESEARCHHELPER_ACCEPTANCE_BUNDLE') or bundle.name != 'ResearchHelper.app':
        raise RuntimeError('包内验收须由上层传入同一只读DMG中的App路径')
    verify_mounted_bundle(bundle, candidate)
    # Refuse a mutable build directory in place of a read-only distribution.
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)],
                   check=True, capture_output=True, timeout=30)
    executable = bundle / 'Contents/Resources/backend/researchhelper-server/researchhelper-server'
    policy = ('(version 1)(allow default)(deny network*)'
              '(allow network* (local ip "localhost:*"))(allow network* (remote ip "localhost:*"))'
              '(allow network* (local unix-socket))(allow network* (remote unix-socket))')
    for directory in (ROOT / 'core', ROOT / 'modules', ROOT / 'products', snapshot / 'core',
                      snapshot / 'modules', snapshot / 'products', Path('/opt/anaconda3')):
        policy += '(deny file-read* (subpath ' + json.dumps(str(directory)) + '))'
    with tempfile.TemporaryDirectory(prefix='migrated-runtime-', dir=ROOT / 'result/acceptance') as folder:
        environment = {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8',
                       'RESEARCHHELPER_STORE': str(Path(folder) / 'store'),
                       'PYTHONTZPATH': str(Path(folder) / 'absent-system-zoneinfo'),
                       'PYTHONDONTWRITEBYTECODE': '1'}
        log = Path(folder) / 'stderr.txt'
        with log.open('w') as errors:
            process = subprocess.Popen(['/usr/bin/sandbox-exec', '-p', policy, str(executable)],
                cwd=folder, env=environment, stdout=subprocess.PIPE, stderr=errors)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    if not selector.select(timeout=20):
                        raise RuntimeError('Packaged backend readiness timeout')
                    line = process.stdout.readline()
                    if not line:
                        errors.flush()
                        diagnostic = log.read_text()
                        (RECEIPTS / 'embedded-runtime-failure.txt').write_text(diagnostic)
                        raise RuntimeError(diagnostic)
                    ready = json.loads(line)
                base = ready['url'].rstrip('/')
                with urllib.request.urlopen(base + '/api/auth/status', timeout=5) as response:
                    auth = json.load(response)
                data = json.dumps({'account': 'admin', 'password': '66666666', 'remember': False}).encode()
                request = urllib.request.Request(base + '/api/auth/login', data=data,
                    headers={'Content-Type': 'application/json', 'X-ResearchHelper-Token': auth['token']})
                with urllib.request.urlopen(request, timeout=5) as response:
                    cookie = response.headers.get('Set-Cookie').split(';')[0]
                request = urllib.request.Request(base + '/api/bootstrap', headers={'Cookie': cookie})
                with urllib.request.urlopen(request, timeout=5) as response:
                    bootstrap = json.load(response)
                assert len(bootstrap['history']) == 6
                assert bootstrap['offline'] is True
                assert not bootstrap['tasks']
                request = urllib.request.Request(base + '/reports/semi.html', headers={'Cookie': cookie})
                with urllib.request.urlopen(request, timeout=5) as response:
                    assert b'echarts' in response.read()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                process.stdout.close()
        if verify_candidate(receipt_path, ROOT) != candidate:
            raise RuntimeError('验收期间候选回执身份变化')
        record = {'status': 'scoped_passed', 'candidate': str(receipt_path.relative_to(ROOT)),
                  'manifest_sha256': candidate['manifest_sha256'], 'bundle': str(bundle),
                  'backend_stopped': process.poll() is not None, 'development_source_read_denied': True,
                  'anaconda_read_denied': True, 'public_reports': 6, 'offline': True,
                  'production_store_touched': False, 'native_window_opened': False,
                  'real_data_requests': 0, 'model_calls': 0}
        (RECEIPTS / 'embedded-runtime.json').write_text(json.dumps(record, indent=2))
        print(json.dumps(record))


if __name__ == '__main__':
    run()
