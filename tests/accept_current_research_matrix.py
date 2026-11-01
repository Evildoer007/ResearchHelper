"""Eight fixed branches via current DMG and project Skill; no real research."""
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

from tests.accept_native_workbench_render import RenderRuntime
from tests.research_matrix_skill_fixture import CASE_EXPECTATIONS, verify_case

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'packaging')]
from build_current import verify_current_build
from skill.install_project_skill import verify_installation


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run():
    selected = Path(os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve()
    candidate = verify_current_build(selected, ROOT)
    installation = ROOT / '.skills/researchhelper'
    installation_hash = verify_installation(installation)
    assert installation_hash == candidate['project_skill_installation']['manifest_sha256']
    output = Path(tempfile.mkdtemp(prefix='current-research-matrix-', dir=ROOT / 'result/acceptance'))
    record = {'status': 'failed', 'candidate': str(selected), 'cases': {}, 'synthetic_only': True,
              'model_calls': 0, 'real_data_requests': 0, 'production_store_touched': False,
              'native_window_opened': False, 'independent_financial_quality': False}
    runtime = RenderRuntime(output, record)
    try:
        project = output / 'independent-skill-project'
        project.mkdir()
        policy = '(version 1)(allow default)(deny network*)'
        policy += '(deny file-write* (subpath ' + json.dumps(str(installation)) + '))'
        for path in (ROOT / 'core', ROOT / 'modules', ROOT / 'products',
                     Path('/Users/haoranxu/Desktop/OptionHelper'), Path('/Users/haoranxu/Desktop/DataFetcher'),
                     Path('/Users/haoranxu/Library/Application Support/ResearchHelper')):
            policy += '(deny file-read* (subpath ' + json.dumps(str(path)) + '))'
        worker = subprocess.run(['/usr/bin/sandbox-exec', '-p', policy, sys.executable, '-I', '-B',
                                 str(ROOT / 'tests/research_matrix_skill_fixture.py'), str(installation), str(project)],
                                cwd=project, env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'},
                                capture_output=True, text=True, timeout=40)
        (output / 'skill.log').write_text(worker.stdout + worker.stderr)
        assert worker.returncode == 0, worker.stderr[:1000]
        skill = json.loads(worker.stdout)
        assert skill['status'] == 'passed' and skill['installed_engine']
        assert verify_installation(installation) == installation_hash

        def connect():
            base = runtime.start().rstrip('/')
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            def request(route, payload=None, token=None):
                headers = {'Content-Type': 'application/json', 'Origin': base}
                if token:
                    headers['X-ResearchHelper-Token'] = token
                data = json.dumps(payload).encode() if payload is not None else None
                call = urllib.request.Request(base + route, data=data, headers=headers)
                with opener.open(call, timeout=10) as response:
                    return json.loads(response.read())
            token = request('/api/auth/status')['token']
            request('/api/auth/login', {'account': 'admin', 'password': '66666666'}, token)
            bootstrap = request('/api/bootstrap')
            assert bootstrap['offline'] is True and bootstrap['execution']['mode'] == 'offline'
            return request, token, bootstrap

        request, token, bootstrap = connect()
        assert bootstrap['tasks'] == []
        for key in CASE_EXPECTATIONS:
            task = request('/api/cases', {'id': key}, token)
            request('/api/tasks/' + task['id'] + '/start',
                    {'expected_execution': {name: bootstrap['execution'][name] for name in ('mode', 'policy_version')}}, token)
            deadline = time.monotonic() + 20
            final = request('/api/tasks/' + task['id'])
            while final['status'] == '运行中' and time.monotonic() < deadline:
                time.sleep(.04)
                final = request('/api/tasks/' + task['id'])
            assert final['status'] == '已完成', final.get('error')
            database = output / 'store/researchhelper.sqlite3'
            with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
                saved = connection.execute('SELECT content,html,digest FROM deliveries WHERE task_id=? ORDER BY version DESC LIMIT 1',
                                           (task['id'],)).fetchone()
            assert saved
            content, paper = json.loads(saved[0]), saved[1]
            projection = verify_case(key, final, content, paper)
            assert projection == skill['cases'][key]['projection'], key + ' App/Skill semantic mismatch'
            (output / (key + '.html')).write_text(paper)
            record['cases'][key] = {'task_id': task['id'], 'entry': content['entry'], 'intent': content['intent'],
                                    'status': final['status'], 'ledger': final['ledger'], 'content_digest': saved[2],
                                    'html_sha256': hashlib.sha256(paper.encode()).hexdigest(),
                                    'app_skill_semantics_equal': True}
        runtime.stop()
        request, token, bootstrap = connect()
        for case in record['cases'].values():
            restored = request('/api/tasks/' + case['task_id'])
            assert restored['status'] == case['status'] and restored['ledger'] == case['ledger']
            with sqlite3.connect((output / 'store/researchhelper.sqlite3').as_uri() + '?mode=ro', uri=True) as connection:
                saved = connection.execute('SELECT digest,html FROM deliveries WHERE task_id=? ORDER BY version DESC LIMIT 1',
                                           (case['task_id'],)).fetchone()
            assert saved[0] == case['content_digest'] and hashlib.sha256(saved[1].encode()).hexdigest() == case['html_sha256']
        assert verify_installation(installation) == installation_hash
        assert verify_current_build(selected, ROOT) == candidate
        record.update(status='scoped_passed', installed_skill_unchanged=True, offline_restart_verified=True,
                      entries_verified=8, intents_verified=5)
    except Exception as error:
        record['error'] = str(error)
    finally:
        runtime.stop()
        (output / 'receipt.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({'status': record['status'], 'cases': len(record['cases']),
                      'receipt': str(output / 'receipt.json'), 'error': record.get('error')}, ensure_ascii=False))
    if record['status'] != 'scoped_passed':
        raise SystemExit(1)


if __name__ == '__main__':
    run()
