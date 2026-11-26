"""Actual signed App: live iFinD prices, registered calculation and report.

The authenticated local API prepares only a natural question and finite limits.
Research start and frozen report reading use the unchanged App's native UI.
No provider responses, evidence specs, calculation results or answers are injected.
Credentials belong to this private acceptance store and are removed afterwards.
"""
import getpass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from tests.accept_native_app_report_flow import ActualNativeApp, ROOT, PROBE
from tests.accept_current_live_market_research import QUESTION, LIMITS, verified_price_change
from tests.accept_current_live_document_reading import (
    PRODUCTION, PrivateAppClient, eligible_configuration, file_identity, task_context,
)

CASE = 'actual-native-ifind-510300-price-report-v1'


def select_candidate():
    if os.environ.get('RESEARCHHELPER_ALLOW_VISIBLE_ACCEPTANCE') != '1':
        raise RuntimeError('Actual native acceptance has not been explicitly selected')
    selected = os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
    if not selected:
        raise RuntimeError('An exact current candidate is required')
    path = Path(selected).resolve()
    for receipt in (ROOT / 'result/acceptance').glob('actual-native-market-report-*/receipt.json'):
        previous = json.loads(receipt.read_text())
        if previous.get('candidate') == str(path) and previous.get('case') == CASE:
            raise RuntimeError('This candidate already has this case; no automatic retry or ledger reset')
    return path


def run(refresh_token):
    selected = select_candidate()
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise ValueError('Current private iFinD authorization is required')
    sys.path[:0] = [str(ROOT / 'packaging'), str(ROOT / 'core')]
    from build_current import verify_current_build
    from bootstrap import configure_imports
    configure_imports()
    from researchhelper.model import ModelSettings
    from researchhelper.runtime import ExecutionPolicy
    from researchhelper.settings import DataSettings
    from researchhelper.reports import research_figures

    candidate = verify_current_build(selected, ROOT)
    protected = {name: file_identity(PRODUCTION / name) for name in
                 ('model-settings.json', 'source-settings.json', 'execution-policy.json')}
    configuration = eligible_configuration(ModelSettings(PRODUCTION).read(private=True))
    configuration.update(endpoint='https://api.deepseek.com/beta/chat/completions', reasoning_effort='none')
    output = Path(tempfile.mkdtemp(prefix='actual-native-market-report-', dir=ROOT / 'result/acceptance'))
    record = {'status': 'executing', 'case': CASE, 'checks': [], 'candidate': str(selected),
        'scope': 'actual_native_app_live_ifind_price_observation_not_investment_quality',
        'natural_question': QUESTION, 'limits': LIMITS, 'max_real_model_calls': LIMITS['model_calls'] - 1,
        'task_setup': 'actual_app_authenticated_local_api', 'research_started_from_native_ui': False,
        'supplied_answers': False, 'supplied_evidence_specs': False, 'supplied_calculations': False,
        'native_file_picker_accepted': False, 'native_export_accepted': False, 'native_print_accepted': False,
        'full_investment_research_accepted': False, 'independent_financial_quality': False,
        'production_store_touched': False, 'installed': False, 'user_documents_sent': False,
        'automatic_retry': False, 'supplier': 'ifind', 'explicit_private_beta': True,
        'test_sources': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in (Path(__file__), PROBE, ROOT / 'tests/accept_native_app_report_flow.py',
                                      ROOT / 'tests/accept_current_live_market_research.py')}}
    models, sources = ModelSettings(output / 'store'), DataSettings(output / 'store')
    policy = ExecutionPolicy(output / 'store', configurable=True)
    app = ActualNativeApp(output, record, candidate)
    client = task = None

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        record['checks'].append(name)

    def observe_task(latest):
        record.update(task_id=latest['id'], research_status=latest['status'], ledger=latest['ledger'],
            model_calls=latest['ledger']['used']['model_calls'], error=latest.get('error'), gaps=latest['gaps'],
            evidence_count=len(latest['evidence']), calculation_count=len(latest['calculations']),
            rejected_evidence=latest['rejected_evidence'], model_diagnostics=latest.get('reasoning_state', {}),
            questions=[{key: item.get(key) for key in ('id', 'question', 'status', 'kind')} for item in latest['questions']],
            nodes=[{key: item.get(key) for key in ('id', 'title', 'status')} for item in latest['nodes']])

    (output / 'receipt.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    try:
        app.prepare()
        app.launch()
        app.login()
        client = PrivateAppClient(app.backend_url())
        bootstrap = client.request('api/bootstrap')
        check('actual App starts with a private empty offline store', not bootstrap['tasks']
              and bootstrap['execution']['mode'] == 'offline')
        task = client.request('api/tasks', {'question': QUESTION, 'entry': 'ETF', 'intent': '解释变化',
            'objects': ['510300.SH'], 'research_mode': 'current', 'limits': LIMITS})
        route = 'api/tasks/' + task['id']
        check('natural question has no prefilled research result', not task['evidence']
              and not task['calculations'] and not task['theses'] and not task['delivery'])
        client.request('api/settings/model', configuration)
        client.request('api/settings/data', {'ifind': {'refresh_token': refresh_token}})
        client.request('api/settings/execution', {'enabled': True, 'confirm': True})
        app.close()
        app.launch()
        app.login()
        client = PrivateAppClient(app.backend_url())
        app.press('~请优先使用iFinD研究510300.SH')
        app.observe('research-ready')
        app.press('发送研究问题')
        record['research_started_from_native_ui'] = True
        app.control('hide')
        if app.previous_pid:
            subprocess.run([str(app.binary), 'activate', str(app.previous_pid)], capture_output=True, timeout=10)
        deadline = time.monotonic() + LIMITS['seconds'] + 10
        while time.monotonic() < deadline:
            task = client.request(route)
            if task['status'] in ('已完成', '部分完成', '失败', '已取消'):
                break
            time.sleep(.7)
        observe_task(task)
        # A partial report is still observed, but never accepted as complete.
        # Retain the actual frozen rendering before checking research quality,
        # so failure evidence is not reduced to a status label alone.
        report = None
        if task['delivery']:
            app.control('activate')
            app.capture('live-result')
            report = app.open_report('live', client, task)
        check('real model and provider calls stay within finite reserved budgets',
              0 < record['model_calls'] <= record['max_real_model_calls']
              and 0 < task['ledger']['used']['data_requests'] <= LIMITS['data_requests'])
        check('natural research scope unchanged and not synthetic', task['question'] == QUESTION
              and task['objects'] == ['510300.SH'] and not task['synthetic'] and not task.get('fixture'))
        check('only actual iFinD records retained', bool(task['source_records'])
              and all(item['source'] == 'ifind' for item in task['source_records']))
        numeric = verified_price_change(task)
        record['independent_numeric_check'] = numeric
        check('independent original cells equal registered calculation', bool(numeric))
        check('required questions complete and report durably saved', task['status'] == '已完成'
              and bool(task['delivery']) and all(item['status'] == '已回答'
              for item in task['questions'] if item['required']))
        figures = research_figures({key: task[key] for key in ('evidence', 'calculations', 'theses')})
        record['figure_count'] = len(figures)
        check('figures derive from verified actual observations', bool(figures))
        check('native report includes security and offline chart renderer', '510300.SH' in report
              and 'echarts' in report.lower() and '合成示例' not in report)
        client.request('api/settings/execution', {'enabled': False, 'confirm': True})
        client.request('api/settings/model', dict(configuration, api_key='', clear_key=True))
        sources.save({'ifind': {'clear_access_token': True, 'clear_refresh_token': True}})
        app.close()
        app.launch()
        app.login()
        client = PrivateAppClient(app.backend_url())
        reopened = client.request(route)
        check('offline actual App restart retains ledger and frozen report', reopened['ledger'] == task['ledger']
              and reopened['delivery'] == task['delivery']
              and client.request('api/bootstrap')['execution']['mode'] == 'offline')
        app.press('~请优先使用iFinD研究510300.SH')
        check('actual UI reopens identical report without fresh research',
              app.open_report('restart', client, reopened) == report)
        check('delivered candidate unchanged', verify_current_build(selected, ROOT) == candidate)
        record['status'] = 'scoped_passed'
    except Exception as error:
        record.update(status='failed', failure_type=type(error).__name__)
        # Errors can contain third-party response details. Keep diagnostic
        # types and our static assertion names, never an arbitrary exception.
        if isinstance(error, AssertionError):
            record['failed_check'] = str(error)
        if client and task:
            try:
                observe_task(client.request('api/tasks/' + task['id']))
            except Exception:
                record['task_observation_failed'] = True
        if app.process and app.process.poll() is None:
            try:
                app.observe('failure-state')
                app.capture('failure-state')
            except Exception:
                record['failure_observation_unavailable'] = True
    finally:
        if client and task:
            try:
                latest = client.request('api/tasks/' + task['id'])
                if latest['status'] == '运行中':
                    client.request('api/tasks/' + task['id'] + '/cancel', {'expected_task': task_context(latest)})
            except Exception:
                record['cancel_observation_failed'] = True
        try:
            app.stop()
        except Exception:
            record.update(status='failed', cleanup_error='owned_runtime_cleanup_failed')
        try:
            policy.save({'enabled': False, 'confirm': True})
            models.save(dict(configuration, api_key='', clear_key=True))
            sources.save({'ifind': {'clear_access_token': True, 'clear_refresh_token': True}})
            record['probe_credentials_removed'] = not list((output / 'store/credentials').glob('*.secret'))
            record['production_configuration_unchanged'] = all(file_identity(PRODUCTION / name) == value
                                                              for name, value in protected.items())
            assert record['probe_credentials_removed'] and record['production_configuration_unchanged']
            for path in output.rglob('*'):
                if path.is_file():
                    contents = path.read_bytes()
                    assert configuration['api_key'].encode() not in contents and refresh_token.encode() not in contents
            serialized = json.dumps(record, ensure_ascii=False)
            assert configuration['api_key'] not in serialized and refresh_token not in serialized
        except Exception:
            record = {key: value for key, value in record.items() if key not in
                      ('model_diagnostics', 'error', 'gaps', 'rejected_evidence')}
            record.update(status='failed', cleanup_error='credential_or_protected_identity_failure')
        configuration['api_key'] = ''
        (output / 'receipt.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({key: record.get(key) for key in
        ('status', 'research_status', 'model_calls', 'failed_check', 'failure_type')}
        | {'receipt': str(output / 'receipt.json')}, ensure_ascii=False))
    return record


if __name__ == '__main__':
    select_candidate()
    outcome = run(getpass.getpass('Private iFinD authorization: '))
    raise SystemExit(0 if outcome['status'] == 'scoped_passed' else 1)
