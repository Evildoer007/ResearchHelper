"""Collect exactly one ordinary ResChat study from a frozen candidate.

Run only after review. Importing this driver never starts a service or research.
Only the temporary Store uses direct extraction; production settings stay intact.
"""
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import selectors
import signal
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = Path('/Users/haoranxu/Library/Application Support/ResearchHelper/research-v1')
CANDIDATE = ROOT / 'result/build-candidates/20261015-205434-791827/candidate.json'
NODE = Path('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')
CASE = 'one-sentence-financial-research-20261015-v1'
QUESTION = '研究贵州茅台2026年半年报的盈利质量，计算净利润率及经营现金流对净利润的覆盖，生成一份带图表和数据来源的报告。'


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def production_digest():
    """Hash configuration bytes and logical database rows without migrations."""
    files = [PRODUCTION / name for name in ('model-settings.json', 'source-settings.json', 'execution-policy.json')]
    files += list((PRODUCTION / 'credentials').glob('*.secret'))
    result = {'configuration': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}}
    database = PRODUCTION / 'researchhelper.sqlite3'
    if database.exists():
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            db.execute('BEGIN')
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            result['database'] = {table: sorted(hashlib.sha256(repr(row).encode()).hexdigest()
                for row in db.execute('SELECT * FROM "' + table.replace('"', '""') + '"')) for table in tables}
    return result


class AppClient:
    def __init__(self, base):
        import urllib.request
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.token = self.request('api/auth/status')['token']
        self.request('api/auth/login', {'account': 'admin', 'password': '66666666'})

    def request(self, route, body=None, html=False):
        import urllib.request
        headers = {'Content-Type': 'application/json'}
        if body is not None:
            headers['X-ResearchHelper-Token'] = self.token
        request = urllib.request.Request(self.base + route, headers=headers,
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None)
        with self.opener.open(request, timeout=15) as response:
            value = response.read().decode()
        return value if html else json.loads(value)


def stop_group(process):
    """Reap this launch's process group, including bounded child workers."""
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        if process.poll() is None:
            process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


class OneSentenceRuntime:
    """No source-backend fallback, native window, or production Store access."""
    def __init__(self, output, record):
        self.output, self.record = output, record
        self.process = self.mount_folder = None
        self.attached = False

    def start(self):
        sys.path[:0] = [str(ROOT / 'packaging'), str(ROOT / 'maintenance')]
        from build_current import verify_current_build
        from candidate_acceptance import verify_mounted_bundle
        candidate = verify_current_build(self.record['candidate'], ROOT)
        self.mount_folder = tempfile.TemporaryDirectory(prefix='mount-', dir=self.output)
        self.mount = Path(self.mount_folder.name)
        subprocess.run(['/usr/bin/hdiutil', 'attach', candidate['artifacts']['dmg']['path'],
            '-readonly', '-nobrowse', '-mountpoint', str(self.mount)], check=True, capture_output=True, timeout=30)
        self.attached = True
        bundle = self.mount / 'ResearchHelper.app'
        verify_mounted_bundle(bundle, candidate)
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)],
            check=True, capture_output=True, timeout=30)
        policy = '(version 1)(allow default)'
        denied = [ROOT / name for name in ('core', 'modules', 'products')]
        denied += [Path('/opt/anaconda3'), PRODUCTION.parent,
            Path('/Users/haoranxu/Desktop/OptionHelper'), Path('/Users/haoranxu/Desktop/DataFetcher')]
        for target in denied:
            policy += '(deny file-read* file-write* (subpath ' + json.dumps(str(target)) + '))'
        executable = bundle / 'Contents/Resources/backend/researchhelper-server/researchhelper-server'
        environment = dict(PATH='/usr/bin:/bin', LANG='en_US.UTF-8', PYTHONDONTWRITEBYTECODE='1',
            RESEARCHHELPER_STORE=str(self.output / 'store'), PYTHONTZPATH=str(self.output / 'no-system-tzdata'))
        # Do not persist raw backend diagnostics, which may contain provider details.
        self.process = subprocess.Popen(['/usr/bin/sandbox-exec', '-p', policy, str(executable)],
            cwd=self.output, env=environment, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=20):
                raise RuntimeError('bundled_backend_not_ready')
            base = json.loads(self.process.stdout.readline())['url']
        if not base.startswith('http://127.0.0.1:'):
            raise RuntimeError('unexpected_backend_origin')
        self.record.update(backend_mode='readonly_dmg', signature_verified=True,
            bundled_backend_isolated=True, external_network_allowed=True)
        return AppClient(base.rstrip('/') + '/')

    def stop(self):
        try:
            stop_group(self.process)
            if self.process:
                self.process.stdout.close()
            self.record['service_stopped'] = self.process is None or self.process.poll() is not None
        finally:
            if self.attached:
                subprocess.run(['/usr/bin/hdiutil', 'detach', str(self.mount)],
                    check=True, capture_output=True, timeout=30)
                self.attached = False
            self.record['mount_detached'] = not self.attached
            if self.mount_folder:
                self.mount_folder.cleanup()


def run(*, case=CASE, model_overrides=None, candidate_path=CANDIDATE, runtime_class=OneSentenceRuntime, question=QUESTION):
    overrides = dict(model_overrides or {'extraction_mode': 'direct'})
    allowed = {'extraction_mode', 'reasoning_effort', 'planning_effort'}
    if not overrides or set(overrides) - allowed:
        raise ValueError('Only explicit non-secret model phase controls are supported')
    acceptance = ROOT / 'result/acceptance'
    # Atomic persistent guard: a failed or interrupted dispatch is never retried.
    guard = acceptance / (case + '.guard')
    descriptor = os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    output = Path(tempfile.mkdtemp(prefix='one-sentence-research-', dir=acceptance))
    output.chmod(0o700)
    before = production_digest()
    record = dict(case=case, candidate=str(Path(candidate_path).resolve()), status='preparing', question=question,
        temporary_model_controls=overrides, temporary_extraction_override=overrides.get('extraction_mode') == 'direct',
        production_settings_modified=False,
        native_window_opened=False, supplied_plan=False, supplied_evidence=False, supplied_answers=False,
        task_retry=False, financial_research_completed=False, financial_quality_accepted=False,
        ordinary_chat_report_completed=False)
    receipt = output / 'receipt.json'
    runtime, browser, client = runtime_class(output, record), None, None
    store = output / 'store'
    sys.path.insert(0, str(ROOT))
    from core.bootstrap import configure_imports
    configure_imports()
    from researchhelper.model import ModelSettings
    from researchhelper.settings import DataSettings
    from researchhelper.runtime import ExecutionPolicy
    model_settings, data_settings = ModelSettings(store), DataSettings(store)
    policy = ExecutionPolicy(store, configurable=True)
    save_json(receipt, record)
    try:
        model = ModelSettings(PRODUCTION).read(private=True)
        if not model.get('api_key') or not ModelSettings.supports_direct_extraction(model):
            raise RuntimeError('saved_model_does_not_support_direct_extraction')
        # Resolve only iFinD refresh: never resolve Tinyshare or saved access tokens.
        source_settings = DataSettings(PRODUCTION)
        saved = source_settings._configuration()
        reference = saved['ifind']['refresh_token']
        refresh = (source_settings.secrets.resolve(reference, 'ifind-refresh_token')
            if 'schema' in saved and reference else source_settings._credential(reference or ''))
        if not refresh:
            raise RuntimeError('missing_saved_ifind_refresh')
        public_before = ModelSettings(PRODUCTION).read()
        model_settings.save(dict(model, **overrides))
        public_after = model_settings.read()
        unchanged = all(public_before.get(key, default) == public_after.get(key, default)
            for key, default in (('endpoint', ''), ('model', ''),
                                 ('reasoning_effort', 'default'), ('planning_effort', 'inherit')) if key not in overrides)
        if not unchanged:
            raise RuntimeError('temporary_model_settings_drift')
        record['unmodified_model_controls_preserved'] = True
        record['model_and_endpoint_preserved'] = all(public_before[key] == public_after[key] for key in ('model', 'endpoint'))
        data_settings.save({'ifind': {'refresh_token': refresh}})
        model.clear()
        refresh = ''
        policy.save({'enabled': True, 'confirm': True})
        record['temporary_execution_policy_enabled'] = True
        client = runtime.start()
        boot = client.request('api/bootstrap')
        if Path(boot['storage_location']).resolve() != store.resolve() or boot['tasks']:
            raise RuntimeError('temporary_store_not_empty_or_wrong')
        environment = dict(PATH='/usr/bin:/bin', LANG='en_US.UTF-8',
            RESEARCHHELPER_PACKAGE_URL=client.base, RESEARCHHELPER_ACCEPTANCE_DIR=str(output),
            RESEARCHHELPER_SUBMISSION_QUESTION=question,
            RESEARCHHELPER_PHASE_PROFILE=json.dumps(overrides))
        browser = subprocess.Popen([str(NODE), str(ROOT / 'tests/accept_one_sentence_research.mjs')],
            cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            browser.wait(timeout=710)
        except subprocess.TimeoutExpired:
            record['outer_deadline_reached'] = True
        record['status'] = 'collected_for_independent_review' if browser.poll() == 0 else 'collection_failed'
    except Exception as error:
        record.update(status='collection_failed', error_type=type(error).__name__)
    finally:
        try:
            stop_group(browser)
            record['browser_stopped'] = browser is None or browser.poll() is not None
            progress = output / 'browser-receipt.json'
            if progress.exists():
                record['browser'] = json.loads(progress.read_text())
                record['task_id'] = record['browser'].get('task_id')
            if client:
                # Recovery covers a browser death between creation and receipt write.
                tasks = client.request('api/bootstrap')['tasks']
                if len(tasks) > 1:
                    raise RuntimeError('more_than_one_task_created')
                if tasks:
                    record['task_id'] = tasks[0]['id']
                    route = 'api/tasks/' + record['task_id']
                    task = client.request(route)
                    if task['status'] == '运行中':
                        binding = {key: task[key] for key in ('id', 'version', 'batch', 'cutoff', 'input_hash')}
                        client.request(route + '/cancel', {'expected_task': binding})
                        task = client.request(route)
                    save_json(output / 'task.json', task)
                    record.update(research_status=task['status'], ledger=task['ledger'],
                        evidence_count=len(task['evidence']), calculation_count=len(task['calculations']), gaps=task['gaps'])
                    methods = {item['method'] for item in task['calculations']}
                    record['financial_research_completed'] = (task['status'] == '已完成' and bool(task['delivery'])
                        and {'net_profit_margin_v1', 'operating_cash_profit_ratio_v1'}.issubset(methods))
                    record['ordinary_chat_report_completed'] = (task['status'] == '已完成' and bool(task['delivery'])
                        and bool(task['evidence']) and all(q['status'] == '已回答' for q in task['questions'] if q['required']))
                    if task['delivery']:
                        html = client.request(route + '/report', html=True)
                        (output / 'report.html').write_text(html)
                        record['report_sha256'] = hashlib.sha256(html.encode()).hexdigest()
                        with sqlite3.connect((store / 'researchhelper.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
                            content = db.execute('SELECT content FROM deliveries WHERE task_id=? AND version=?',
                                (task['id'], task['version'])).fetchone()[0]
                        save_json(output / 'report.json', json.loads(content))
        except Exception as error:
            record.update(status='collection_failed', collection_error_type=type(error).__name__)
        finally:
            try:
                runtime.stop()
            except Exception as error:
                record.update(status='cleanup_failed', runtime_cleanup_error=type(error).__name__)
            try:
                policy.save({'enabled': False, 'confirm': True})
                current = model_settings.read()
                model_settings.save(dict(endpoint=current['endpoint'], model=current['model'], clear_key=True))
                data_settings.save({'ifind': {'clear_access_token': True, 'clear_refresh_token': True},
                                    'tinyshare': {'clear_token': True}})
                record['temporary_credentials_cleared'] = not any((store / 'credentials').glob('*.secret'))
                record['temporary_execution_policy_disabled'] = policy.read()['enabled'] is False
                if not record['temporary_credentials_cleared']:
                    raise RuntimeError('temporary_secret_cleanup_incomplete')
            except Exception as error:
                record.update(status='cleanup_failed', credential_cleanup_error=type(error).__name__)
            record['production_unchanged'] = before == production_digest()
            if not record['production_unchanged']:
                record['status'] = 'protection_failed'
            save_json(receipt, record)
    print(json.dumps({'status': record['status'], 'receipt': str(receipt),
        'ordinary_chat_report_completed': record['ordinary_chat_report_completed'],
        'financial_research_completed': record['financial_research_completed']}, ensure_ascii=False))
    return record


if __name__ == '__main__':
    run()
