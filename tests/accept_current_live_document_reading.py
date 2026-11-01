"""One bounded, explicitly authorized model case against the actual DMG.

Synthetic text only. No supplier calls, user PDFs, production writes, native
windows or retries. The real RH key is read only from RH's own private store;
the probe's credential is cleared before its final receipt is written.
"""
import base64
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlparse
from unittest.mock import patch

from tests.accept_packaged_credential_separation import PrivateRuntime

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = Path('/Users/haoranxu/Library/Application Support/ResearchHelper')
AUTHORIZATION = 'document-content-two-calls'
TEXT = '合成验收政策，仅用于软件验收，不是真实政策。\n\n政策自2027年1月1日起生效。\n\n费用上限为100百万元。'
QUESTION = '仅核对合成验收政策原件记载的生效日期和费用上限，不判断政策真实性或投资价值。'


def eligible_configuration(value):
    """Do not silently switch provider/model or borrow another product's key."""
    parsed = urlparse(value.get('endpoint', ''))
    if (parsed.scheme != 'https' or parsed.hostname != 'api.deepseek.com'
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or parsed.query or parsed.fragment
            or parsed.path.rstrip('/') not in ('', '/v1', '/chat/completions', '/v1/chat/completions')
            or value.get('model') != 'deepseek-flash' or not value.get('api_key')):
        raise ValueError('需要RH独立的已授权官方Flash配置，不自动换源')
    return {field: value[field] for field in ('endpoint', 'model', 'api_key')}


def file_identity(path):
    if path.is_symlink():
        raise ValueError('不读取软链接配置')
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


class AuthorizedRuntime(PrivateRuntime):
    def start(self):
        original = subprocess.Popen
        def guarded(arguments, *values, **options):
            if isinstance(arguments, list) and arguments[0] == '/usr/bin/sandbox-exec':
                arguments = list(arguments)
                if arguments[2].count('(deny network*)') != 1:
                    raise RuntimeError('私有检查网络边界不符')
                arguments[2] = arguments[2].replace('(deny network*)',
                    '(deny network*)(allow network-outbound (remote tcp "*:443"))'
                    '(allow network-outbound (remote udp "*:53"))')
                self.record['probe_https_outbound_allowed'] = True
            return original(arguments, *values, **options)
        with patch.object(subprocess, 'Popen', side_effect=guarded):
            return super().start()


class PrivateAppClient:
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.token = self.request('api/auth/status')['token']
        self.request('api/auth/login', {'account': 'admin', 'password': '66666666'})

    def request(self, route, body=None, html=False):
        headers = {'Content-Type': 'application/json'}
        if body is not None:
            headers['X-ResearchHelper-Token'] = self.token
        request = urllib.request.Request(self.base + route, headers=headers,
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None)
        with self.opener.open(request, timeout=15) as response:
            value = response.read().decode()
        return value if html else json.loads(value)


def task_context(task):
    return {field: task[field] for field in ('id', 'version', 'batch', 'cutoff', 'input_hash')}


def run():
    if os.environ.get('RESEARCHHELPER_AUTHORIZED_LIVE_CASE') != AUTHORIZATION:
        raise RuntimeError('未明确选择本次两调用范围，不派发真实模型')
    selected = os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
    if not selected:
        raise RuntimeError('必须指定当次候选，不猜测或重开历史任务')
    sys.path[:0] = [str(ROOT / 'packaging'), str(ROOT / 'core')]
    from build_current import verify_current_build
    from bootstrap import configure_imports
    configure_imports()
    from researchhelper.model import ModelSettings
    candidate = verify_current_build(selected, ROOT)
    protected = {name: file_identity(PRODUCTION / name)
                 for name in ('model-settings.json', 'execution-policy.json')}
    configuration = eligible_configuration(ModelSettings(PRODUCTION).read(private=True))
    folder = Path(tempfile.mkdtemp(prefix='live-document-reading-', dir=ROOT / 'result/acceptance'))
    record = {'status': 'executing', 'scope': 'actual_dmg_model_document_content_only',
        'candidate': str(Path(selected).resolve()), 'model': configuration['model'],
        'synthetic_material': True, 'independent_financial_quality': False,
        'native_window_opened': False, 'installed': False, 'production_store_touched': False,
        'user_documents_sent': False, 'supplier_requests': 0, 'automatic_retry': False,
        'max_real_model_requests': 2, 'checks': []}
    target = folder / 'receipt.json'
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    settings = ModelSettings(folder / 'store')
    runtime = None
    client = None
    task = None
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        record['checks'].append(name)
    try:
        settings.save(configuration)
        runtime = AuthorizedRuntime(folder, record)
        client = PrivateAppClient(runtime.start())
        bootstrap = client.request('api/bootstrap')
        check('private host starts offline without research', bootstrap['execution']['mode'] == 'offline' and not bootstrap['tasks'])
        client.request('api/settings/execution', {'enabled': True, 'confirm': True})
        bootstrap = client.request('api/bootstrap')
        task = client.request('api/tasks', {'question': QUESTION, 'entry': '事件',
            'intent': '原件内容核对', 'objects': ['合成验收政策'], 'research_mode': 'current',
            'limits': {'data_requests': 1, 'model_calls': 3, 'seconds': 180},
            'questions': [
                {'id': 'q1', 'kind': 'fact', 'question': '原件记载的生效日期是什么？', 'evidence_needs': ['原件生效日期条款']},
                {'id': 'q2', 'kind': 'fact', 'question': '原件记载的费用上限是什么？', 'evidence_needs': ['原件费用上限条款']}]})
        route = 'api/tasks/' + task['id']
        task = client.request(route + '/material', {'name': '合成验收政策.txt',
            'base64': base64.b64encode(TEXT.encode()).decode(), 'expected_version': task['version']})
        task = client.request(route + '/start', {'expected_task': task_context(task),
            'expected_execution': {field: bootstrap['execution'][field] for field in ('mode', 'policy_version')}})
        deadline = time.monotonic() + 185
        while task['status'] == '运行中' and time.monotonic() < deadline:
            time.sleep(1)
            task = client.request(route)
        record.update(task_id=task['id'], research_status=task['status'], ledger=task['ledger'],
            question_statuses=[question['status'] for question in task['questions']],
            evidence_count=len(task['evidence']), rejected_evidence_count=len(task['rejected_evidence']))
        check('real model ledger never exceeds two requests', 0 < task['ledger']['used']['model_calls'] <= 2)
        check('no supplier requests', task['ledger']['used']['data_requests'] == 0)
        check('both required original questions completed', task['status'] == '已完成' and all(question['status'] == '已回答' for question in task['questions']))
        check('document scope cannot create investment output', not any(task[field] for field in ('theses', 'calculations', 'tracking')))
        check('evidence remains document-only with unknown publication', all(item.get('usage_scope') == 'document_content' and item.get('published_at') is None for item in task['evidence']))
        report = client.request(route + '/report', html=True)
        check('both original clauses present in frozen report', '2027年1月1日' in report and '100百万元' in report)
        report_hash = hashlib.sha256(report.encode()).hexdigest()
        (folder / 'report.html').write_text(report)
        record['report_sha256'] = report_hash
        client.request('api/settings/execution', {'enabled': False, 'confirm': True})
        runtime.stop()
        runtime = None
        settings.save(dict(configuration, api_key='', clear_key=True))
        runtime = PrivateRuntime(folder, record)
        client = PrivateAppClient(runtime.start())
        reopened = client.request(route)
        check('offline restart retains ledger and completed report', reopened['ledger'] == task['ledger'] and hashlib.sha256(client.request(route + '/report', html=True).encode()).hexdigest() == report_hash)
        check('restart does not dispatch research', client.request('api/bootstrap')['execution']['mode'] == 'offline')
        check('candidate remains current', verify_current_build(selected, ROOT) == candidate)
        record['status'] = 'scoped_passed'
    except Exception as error:
        record.update(status='failed', failure_type=type(error).__name__)
        if isinstance(error, AssertionError):
            record['failed_check'] = str(error)
        if task and client:
            try:
                latest = client.request('api/tasks/' + task['id'])
                if latest['status'] == '运行中':
                    latest = client.request('api/tasks/' + task['id'] + '/cancel', {'expected_task': task_context(latest)})
                record.update(task_id=latest['id'], research_status=latest['status'], ledger=latest['ledger'],
                    rejected_evidence=latest.get('rejected_evidence', []), model_diagnostics=latest.get('reasoning_state', {}))
            except Exception:
                record['task_observation_failed'] = True
    finally:
        if runtime:
            try:
                runtime.stop()
            except Exception:
                record.update(status='failed', cleanup_error='runtime_cleanup_failed')
        try:
            settings.save(dict(configuration, api_key='', clear_key=True))
            record['probe_credential_removed'] = not list((folder / 'store/credentials').glob('*.secret'))
            if not record['probe_credential_removed']:
                raise RuntimeError('检查凭据未清除')
            record['production_configuration_unchanged'] = all(file_identity(PRODUCTION / name) == expected for name, expected in protected.items())
            if not record['production_configuration_unchanged']:
                raise RuntimeError('生产配置在核验期间发生变化')
            for path in folder.rglob('*'):
                if path.is_file() and configuration['api_key'].encode() in path.read_bytes():
                    raise RuntimeError('检查产物含凭据，不能交付')
        except Exception:
            record.update(status='failed', cleanup_error='credential_or_protected_identity_failure')
        configuration['api_key'] = ''
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({'status': record['status'], 'research_status': record.get('research_status'),
        'model_calls': record.get('ledger', {}).get('used', {}).get('model_calls'),
        'failed_check': record.get('failed_check'), 'receipt': str(target)}, ensure_ascii=False))
    if record['status'] != 'scoped_passed':
        raise SystemExit(1)


if __name__ == '__main__':
    run()
