"""Connect the installed App's actual default Store and retain one real report.

Explicit user authorization covers RH-owned configuration and this one bounded
research task. No native window, alternative model, task retry or OH access.
The saved configuration remains available on the user's next ordinary launch.
"""
from decimal import Decimal
import getpass
import hashlib
import json
import os
from pathlib import Path
import selectors
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
APP = Path('/Applications/ResearchHelper.app')
LEGACY = Path.home() / 'Library/Application Support/ResearchHelper'
STORE = LEGACY / 'research-v1'
CANDIDATE = ROOT / 'result/build-candidates/20261014-214553-363916/candidate.json'
CASE = 'actual-default-store-price-research-20261014-v1'
LIMITS = dict(data_requests=8, model_calls=8, seconds=600)
sys.path.insert(0, str(ROOT))
from core.bootstrap import configure_imports
configure_imports()
from researchhelper.archive_codec import decode_body
from researchhelper.model import ModelSettings
from researchhelper.settings import DataSettings
from researchhelper.provenance import query_requirements, validate_query_answer
from tests.accept_current_live_document_reading import PrivateAppClient, task_context
from tests.accept_live_source_requirements import QUESTION


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def archive_snapshot():
    """Read old rows without creating/migrating the user's archive."""
    path = STORE / 'researchhelper.sqlite3'
    if not path.exists():
        return {}
    result = {}
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        for table in ('tasks', 'versions', 'deliveries', 'originals', 'corrections',
                      'task_presentations', 'archive_values'):
            rows = db.execute('SELECT * FROM ' + table).fetchall()
            result[table] = {hashlib.sha256(repr(row).encode()).hexdigest() for row in rows}
        for body, in db.execute('SELECT body FROM tasks'):
            if decode_body(db, body)['status'] == '运行中':
                raise RuntimeError('existing_research_running')
    return result


class DefaultAppRuntime:
    """Launch exactly the installed executable, with no Store override."""
    def __init__(self, output):
        self.output, self.process, self.errors = output, None, None

    def start(self):
        active = subprocess.run(['/usr/bin/pgrep', '-f',
            '/Applications/ResearchHelper.app/Contents/'], capture_output=True)
        if active.returncode == 0:
            raise RuntimeError('installed_app_already_running')
        policy = '(version 1)(allow default)'
        policy += '(deny file-write* (subpath ' + json.dumps(str(APP)) + '))'
        for target in ('OptionHelper', 'DataFetcher'):
            policy += '(deny file-read* (subpath ' + json.dumps(str(Path.home() / 'Desktop' / target)) + '))'
        executable = APP / 'Contents/Resources/backend/researchhelper-server/researchhelper-server'
        self.errors = (self.output / 'installed-backend.log').open('a')
        environment = dict(PATH='/usr/bin:/bin', LANG='en_US.UTF-8', PYTHONDONTWRITEBYTECODE='1')
        self.process = subprocess.Popen(['/usr/bin/sandbox-exec', '-p', policy, str(executable)],
            cwd=APP / 'Contents/Resources', env=environment,
            stdout=subprocess.PIPE, stderr=self.errors)
        with selectors.DefaultSelector() as watcher:
            watcher.register(self.process.stdout, selectors.EVENT_READ)
            if not watcher.select(timeout=20):
                raise RuntimeError('installed_backend_not_ready')
            ready = json.loads(self.process.stdout.readline())
        if not ready['url'].startswith('http://127.0.0.1:'):
            raise RuntimeError('invalid_backend_address')
        return PrivateAppClient(ready['url'])

    def stop(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process.stdout.close()
            self.process = None
        if self.errors:
            self.errors.close()
            self.errors = None


def run(refresh_token):
    if not refresh_token or any(char.isspace() for char in refresh_token):
        raise ValueError('current_authorized_refresh_required')
    sys.path[:0] = [str(ROOT / 'packaging'), str(ROOT / 'packaging/app/macos')]
    from build_current import verify_current_build, write_record
    from install_app import recorded_manifest, verify_bundle, signature
    for previous in (ROOT / 'result/acceptance').glob('default-app-online-*/receipt.json'):
        if json.loads(previous.read_text()).get('case') == CASE:
            raise RuntimeError('case_already_dispatched')
    candidate = verify_current_build(CANDIDATE, ROOT)
    manifest = recorded_manifest(CANDIDATE)
    verify_bundle(APP, manifest)
    signature(APP)
    old_rows = archive_snapshot()
    protected = {str(file): digest(file) for file in LEGACY.iterdir()
                 if file.is_file()}
    protected.update({str(file): digest(file) for file in (LEGACY / 'credentials').glob('*.secret')})
    output = Path(tempfile.mkdtemp(prefix='default-app-online-', dir=ROOT / 'result/acceptance'))
    record = dict(status='executing', case=CASE, candidate=str(CANDIDATE),
        storage_location=str(STORE), default_store_override=False, native_window_opened=False,
        installed_backend=True, task_retry=False, model_settings_overridden=False,
        credentials_copied_to_demo=False, production_configuration_saved=False,
        production_task_created=False, supplied_evidence=False, supplied_answers=False,
        full_investment_research_accepted=False, service_stopped=False)
    receipt = output / 'receipt.json'
    runtime, client, task = DefaultAppRuntime(output), None, None
    write_record(receipt, record)
    try:
        client = runtime.start()
        boot = client.request('api/bootstrap')
        assert Path(boot['storage_location']) == STORE
        current = ModelSettings(STORE).read()
        if not current['has_key'] and not current['model'] and not current['endpoint']:
            config = ModelSettings(LEGACY).read(private=True)
            assert config['api_key'] and config['model'] and config['endpoint']
            client.request('api/settings/model', config)
            config['api_key'] = ''
            record['rh_legacy_model_configuration_connected'] = True
        else:
            assert current['has_key'] and current['model'] and current['endpoint']
        prior = DataSettings(STORE).read(private=True)['ifind']['refresh_token']
        assert not prior or prior == refresh_token, 'existing_source_configuration_preserved'
        client.request('api/settings/data', {'ifind': {'refresh_token': refresh_token}})
        client.request('api/settings/execution', {'enabled': True, 'confirm': True})
        boot = client.request('api/bootstrap')
        assert boot['research_readiness']['model_configured'] and boot['execution']['mode'] == 'live'
        assert boot['source_settings']['ifind']['has_refresh_token']
        record.update(production_configuration_saved=True, online_enabled=True,
            model_configured=True, ifind_configured=True)
        write_record(receipt, record)
        task = client.request('api/tasks', dict(question=QUESTION, entry='ETF', intent='综合研究',
            objects=['510300.SH'], research_mode='current', limits=LIMITS))
        record.update(production_task_created=True, task_id=task['id'])
        route = 'api/tasks/' + task['id']
        execution = boot['execution']
        client.request(route + '/start', {'expected_task': task_context(task),
            'expected_execution': {name: execution[name] for name in ('mode', 'policy_version')}})
        deadline = time.monotonic() + LIMITS['seconds'] + 10
        while True:
            task = client.request(route)
            record.update(research_status=task['status'], ledger=task['ledger'],
                evidence_count=len(task['evidence']), calculation_count=len(task['calculations']))
            write_record(receipt, record)
            if task['status'] in ('已完成', '部分完成', '失败', '已取消'):
                break
            if time.monotonic() >= deadline:
                client.request(route + '/cancel', {})
                raise RuntimeError('bounded_research_timeout')
            time.sleep(1)
        write_record(output / 'task.json', task)
        assert task['status'] == '已完成' and task['delivery'] and not task['synthetic']
        assert not task['gaps'] and not task.get('gap_records')
        assert all(q['status'] == '已回答' for q in task['questions'] if q['required'])
        assert len(task['calculations']) == 1
        calculation = task['calculations'][0]
        assert calculation['method'] == 'observed_price_return_v1'
        facts = {fact['id']: fact for fact in task['evidence']}
        prices = [facts[calculation['input_bindings'][key]] for key in ('start_price', 'end_price')]
        expected = (Decimal(str(prices[1]['value'])) / Decimal(str(prices[0]['value'])) - 1) * 100
        assert abs(Decimal(str(calculation['output']['value'])) - expected) < Decimal('0.000000001')
        for fact in facts.values():
            source = next(s for s in task['source_records'] if s['id'] == fact['artifact']['id'])
            locator = fact['record_locator']
            assert fact['raw_value'] == source['snapshot']['tables'][locator['table']]['table'][locator['field']][locator['row']]
        needs = [q for q in task['questions'] if query_requirements(q)]
        assert needs and all(validate_query_answer(task, q)['refs'] for q in needs)
        paper = client.request(route + '/report', html=True)
        assert '<details' not in paper and "data-reader-version='rh-reader-10'" in paper
        assert not any(message in paper for message in ('初始计算输入绑定未通过', '必要计算尚未完成'))
        (output / 'report.html').write_text(paper)
        report_hash = hashlib.sha256(paper.encode()).hexdigest()
        original_ledger = task['ledger']
        runtime.stop()
        client = runtime.start()
        restored = client.request(route)
        restored_boot = client.request('api/bootstrap')
        assert restored_boot['execution']['mode'] == 'live' and restored_boot['research_readiness']['model_configured']
        assert restored_boot['source_settings']['ifind']['has_refresh_token']
        assert restored['status'] == '已完成' and restored['ledger'] == original_ledger
        assert hashlib.sha256(client.request(route + '/report', html=True).encode()).hexdigest() == report_hash
        record.update(status='scoped_passed', natural_question_completed=True,
            report=str(output / 'report.html'), report_sha256=report_hash,
            app_restart_report_restored=True, app_restart_configuration_restored=True,
            source_requirements_bound=sum(len(query_requirements(q)) for q in needs),
            expected_rows=sorted([dict(date=f['period'], value=f['value'], ref=f['id']) for f in facts.values()],
                key=lambda row: row['date']), expected_price_change_percent=str(expected))
    except Exception as error:
        record.update(status='failed', failure_type=type(error).__name__)
        if client and task:
            try:
                actual = client.request('api/tasks/' + task['id'])
                if actual['status'] == '运行中':
                    client.request('api/tasks/' + task['id'] + '/cancel', {})
                write_record(output / 'task.json', actual)
                record.update(research_status=actual['status'], ledger=actual['ledger'])
            except Exception:
                record['task_read_failed'] = True
    finally:
        runtime.stop()
        record['service_stopped'] = True
        record['legacy_configuration_unchanged'] = all(digest(file) == value for file, value in protected.items())
        current_rows = archive_snapshot()
        record['existing_archive_rows_preserved'] = all(rows <= current_rows.get(table, set()) for table, rows in old_rows.items())
        verify_bundle(APP, manifest)
        assert verify_current_build(CANDIDATE, ROOT) == candidate
        assert record['legacy_configuration_unchanged'] and record['existing_archive_rows_preserved']
        for file in output.iterdir():
            if file.is_file() and refresh_token.encode() in file.read_bytes():
                raise RuntimeError('credential_in_acceptance_output')
        write_record(receipt, record)
    print(json.dumps({key: record.get(key) for key in ('status', 'research_status',
        'production_configuration_saved', 'app_restart_report_restored', 'service_stopped')}
        | {'receipt': str(receipt)}, ensure_ascii=False))
    return record


if __name__ == '__main__':
    secret = getpass.getpass('Private iFinD input (echo disabled): ')
    try:
        raise SystemExit(0 if run(secret)['status'] == 'scoped_passed' else 1)
    finally:
        secret = ''
