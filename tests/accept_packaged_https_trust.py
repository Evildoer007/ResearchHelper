"""Verify actual DMG TLS before authentication, with an invalid fabricated key.

A 401 proves certificate-verified HTTPS reached the provider, not model access
or financial quality. Each execution makes at most one HTTPS attempt, no retry.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from tests.accept_current_live_document_reading import AuthorizedRuntime, PrivateAppClient, task_context

ROOT = Path(__file__).resolve().parents[1]


def verified_before_authentication(record):
    """The shared model gateway categorizes a 401 as authentication_failed."""
    return (record.get('failure_code') in ('http_failure', 'authentication_failed')
        and record.get('http_status') == 401 and record.get('https_attempts') == 1
        and record.get('credential_is_fabricated') is True
        and record.get('fabricated_key_removed') is True
        and record.get('production_paths_denied') is True
        and record.get('service_stopped') is True and record.get('mount_detached') is True)


def run():
    selected = os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
    if not selected:
        raise RuntimeError('必须指定当次候选')
    sys.path[:0] = [str(ROOT / 'packaging'), str(ROOT / 'core')]
    from build_current import verify_current_build
    from bootstrap import configure_imports
    configure_imports()
    from researchhelper.model import ModelSettings
    verify_current_build(selected, ROOT)
    folder = Path(tempfile.mkdtemp(prefix='packaged-https-trust-', dir=ROOT / 'result/acceptance'))
    record = {'status': 'failed', 'scope': 'actual_dmg_tls_before_authentication',
        'candidate': str(Path(selected).resolve()), 'model_calls': 0, 'real_data_requests': 0,
        'production_store_touched': False, 'native_window_opened': False, 'installed': False,
        'credential_is_fabricated': True, 'automatic_retry': False, 'max_https_attempts': 1}
    settings = ModelSettings(folder / 'store')
    configuration = {'endpoint': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'api_key': 'RH_TLS_CHECK_INVALID_NOT_A_REAL_KEY'}
    runtime = None
    try:
        settings.save(configuration)
        runtime = AuthorizedRuntime(folder, record)
        client = PrivateAppClient(runtime.start())
        client.request('api/settings/execution', {'enabled': True, 'confirm': True})
        execution = client.request('api/bootstrap')['execution']
        task = client.request('api/tasks', {'question': '仅用于接口传输验收，不开展投资研究',
            'intent': '原件内容核对', 'limits': {'data_requests': 1, 'model_calls': 2, 'seconds': 90}})
        route = 'api/tasks/' + task['id']
        task = client.request(route + '/start', {'expected_task': task_context(task),
            'expected_execution': {field: execution[field] for field in ('mode', 'policy_version')}})
        deadline = time.monotonic() + 95
        while task['status'] == '运行中' and time.monotonic() < deadline:
            time.sleep(1)
            task = client.request(route)
        record.update(task_id=task['id'], research_status=task['status'], ledger=task['ledger'])
        events = task['ledger']['dispatches']
        if len(events) != 1 or task['ledger']['used']['data_requests'] or task['status'] != '失败':
            raise AssertionError('固定假凭据应在首个请求失败，不允许续跑或重试')
        event = events[0]
        record.update(failure_code=event.get('failure_code'), http_status=event.get('http_status'),
            https_attempts=task['ledger']['used']['model_calls'])
        record['status'] = 'authentication_reached' if event.get('failure_code') in ('http_failure', 'authentication_failed') and event.get('http_status') == 401 else 'tls_or_transport_failed'
    except Exception as error:
        record['failure_type'] = type(error).__name__
    finally:
        if runtime:
            try:
                runtime.stop()
            except Exception:
                record.update(status='failed', cleanup_error='runtime_cleanup_failed')
        try:
            settings.save(dict(configuration, api_key='', clear_key=True))
            record['fabricated_key_removed'] = not list((folder / 'store/credentials').glob('*.secret'))
        except Exception:
            record.update(status='failed', cleanup_error='credential_cleanup_failed')
        if record['status'] == 'authentication_reached':
            record['status'] = 'scoped_passed' if verified_before_authentication(record) else 'failed'
        target = folder / 'receipt.json'
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({key: record.get(key) for key in ('status', 'failure_code', 'http_status', 'https_attempts')}
        | {'receipt': str(target)}, ensure_ascii=False))
    if record['status'] != 'scoped_passed':
        raise SystemExit(1)


if __name__ == '__main__':
    run()
