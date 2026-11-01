"""One bounded actual-DMG market case; private credentials and no retries.

This exercises autonomous planning, iFinD, verified numeric evidence,
registered calculation, analysis and frozen delivery. It is not an independent
investment-quality assessment or permission for production research.
"""
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

from tests.accept_current_live_document_reading import (
    AuthorizedRuntime, PrivateAppClient, PRODUCTION, eligible_configuration,
    file_identity, task_context,
)
from tests.accept_packaged_credential_separation import PrivateRuntime

ROOT = Path(__file__).resolve().parents[1]
AUTHORIZATION = 'ifind-market-four-model-six-data'
CASE = '510300-close-20260921-20260930-v1'
QUESTION = ('请优先使用iFinD研究510300.SH在2026年9月21日至9月30日的每日收盘价格走势，'
            '计算区间未复权价格变化，说明公司行为与总收益口径的限制，'
            '输出带来源和图表的报告。不推断未来收益或价格变化的原因。')
LIMITS = {'data_requests': 6, 'model_calls': 5, 'seconds': 360}


def selected_candidate():
    if os.environ.get('RESEARCHHELPER_AUTHORIZED_LIVE_CASE') != AUTHORIZATION:
        raise RuntimeError('未选择本次实际行情验收范围')
    selected = os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
    if not selected:
        raise RuntimeError('须明确当前候选，不重开旧任务')
    path = Path(selected).resolve()
    for receipt in (ROOT / 'result/acceptance').glob('live-market-research-*/receipt.json'):
        record = json.loads(receipt.read_text())
        if record.get('candidate') == str(path) and record.get('case') == CASE:
            raise RuntimeError('本候选此案例已登记，不重复派发或重置预算')
    return path


def verified_price_change(task):
    """Recompute from independently inspected retained API cells, not prose."""
    by_id = {item['id']: item for item in task['evidence']}
    sources = {item['id']: item for item in task['source_records']}
    answers = []
    for calculation in task['calculations']:
        if calculation['method'] != 'observed_price_return_v1':
            continue
        prices = []
        for field, date in (('start_price', '2026-09-21'), ('end_price', '2026-09-30')):
            item = by_id[calculation['input_bindings'][field]]
            source = sources[item['artifact_id']]
            locator = item['record_locator']
            table = source['snapshot']['tables'][locator['table']]
            row = locator['row']
            assert source['source'] == 'ifind' and source['operation'] == 'cmd_history_quotation'
            assert table['thscode'] == '510300.SH' and table['time'][row] == date
            assert locator['field'] == 'close' and item['unit'] == '元'
            original = table['table']['close'][row]
            assert item['value'] == original == calculation['inputs'][field]
            assert item['source_check'] == item['applicability_check'] == '通过'
            assert item.get('usage_scope') != 'document_content' and item['nature'] == '已实现事实'
            prices.append(original)
        expected = (prices[1] / prices[0] - 1) * 100
        output = calculation['output']
        value = output['value'] if isinstance(output, dict) else output
        assert math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-12)
        answers.append({'calculation_id': calculation['id'], 'start_price': prices[0],
                        'end_price': prices[1], 'price_change_percent': expected})
    return answers


def run(refresh_token):
    selected = selected_candidate()
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise ValueError('需要当前用户授权，不能借用其他产品凭据')
    sys.path[:0] = [str(ROOT / 'packaging'), str(ROOT / 'core')]
    from build_current import verify_current_build
    from bootstrap import configure_imports
    configure_imports()
    from researchhelper.model import ModelSettings
    from researchhelper.settings import DataSettings
    candidate = verify_current_build(selected, ROOT)
    protected = {name: file_identity(PRODUCTION / name) for name in
                 ('model-settings.json', 'source-settings.json', 'execution-policy.json')}
    configuration = eligible_configuration(ModelSettings(PRODUCTION).read(private=True))
    folder = Path(tempfile.mkdtemp(prefix='live-market-research-', dir=ROOT / 'result/acceptance'))
    target = folder / 'receipt.json'
    record = {'status': 'executing', 'case': CASE, 'candidate': str(selected),
        'scope': 'actual_dmg_autonomous_ifind_price_research', 'independent_financial_quality': False,
        'model': configuration['model'], 'limits': LIMITS, 'max_real_model_requests': 4,
        'native_window_opened': False, 'installed': False, 'production_store_touched': False,
        'user_documents_sent': False, 'automatic_retry': False, 'supplier': 'ifind', 'checks': []}
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    models, sources = ModelSettings(folder / 'store'), DataSettings(folder / 'store')
    runtime = client = task = None
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        record['checks'].append(name)
    try:
        models.save(configuration)
        sources.save({'ifind': {'refresh_token': refresh_token}})
        runtime = AuthorizedRuntime(folder, record)
        client = PrivateAppClient(runtime.start())
        initial = client.request('api/bootstrap')
        check('fresh private host has no task and starts offline', not initial['tasks'] and initial['execution']['mode'] == 'offline')
        client.request('api/settings/execution', {'enabled': True, 'confirm': True})
        execution = client.request('api/bootstrap')['execution']
        task = client.request('api/tasks', {'question': QUESTION, 'entry': 'ETF',
            'intent': '解释变化', 'objects': ['510300.SH'], 'research_mode': 'current', 'limits': LIMITS})
        route = 'api/tasks/' + task['id']
        task = client.request(route + '/start', {'expected_task': task_context(task),
            'expected_execution': {field: execution[field] for field in ('mode', 'policy_version')}})
        deadline = time.monotonic() + LIMITS['seconds'] + 10
        while task['status'] == '运行中' and time.monotonic() < deadline:
            time.sleep(1)
            task = client.request(route)
        record.update(task_id=task['id'], research_status=task['status'], ledger=task['ledger'],
            question_statuses=[item['status'] for item in task['questions']],
            evidence_count=len(task['evidence']), calculation_count=len(task['calculations']))
        check('research model requests preserve delivery reserve', 0 < task['ledger']['used']['model_calls'] <= 4)
        check('finite data requests', 0 < task['ledger']['used']['data_requests'] <= 6)
        check('actual ifind records only', bool(task['source_records']) and all(item['source'] == 'ifind' for item in task['source_records']))
        numeric = verified_price_change(task)
        record['independent_numeric_check'] = numeric
        check('original cells and registered price change agree', bool(numeric))
        check('required research questions complete', task['status'] == '已完成' and all(
            not item['required'] or item['status'] == '已回答' for item in task['questions']))
        report = client.request(route + '/report', html=True)
        check('report identifies researched security and real output', '510300.SH' in report and '合成示例' not in report)
        check('report contains local chart rendering', 'echarts' in report.lower())
        from researchhelper.reports import research_figures
        figures = research_figures({'evidence': task['evidence'], 'calculations': task['calculations'],
                                    'theses': task['theses']})
        check('verified observations produce actual figure data', bool(figures))
        record['figure_count'] = len(figures)
        (folder / 'report.html').write_text(report)
        report_hash = hashlib.sha256(report.encode()).hexdigest()
        record['report_sha256'] = report_hash
        client.request('api/settings/execution', {'enabled': False, 'confirm': True})
        runtime.stop()
        runtime = None
        models.save(dict(configuration, api_key='', clear_key=True))
        sources.save({'ifind': {'clear_access_token': True, 'clear_refresh_token': True}})
        runtime = PrivateRuntime(folder, record)
        client = PrivateAppClient(runtime.start())
        reopened = client.request(route)
        check('offline restart retains report and ledger', reopened['ledger'] == task['ledger'] and
            hashlib.sha256(client.request(route + '/report', html=True).encode()).hexdigest() == report_hash)
        check('restart does not research', client.request('api/bootstrap')['execution']['mode'] == 'offline')
        check('candidate unchanged', verify_current_build(selected, ROOT) == candidate)
        record['status'] = 'scoped_passed'
    except Exception as error:
        record.update(status='failed', failure_type=type(error).__name__)
        if isinstance(error, AssertionError):
            record['failed_check'] = str(error)
        if client and task:
            try:
                latest = client.request('api/tasks/' + task['id'])
                if latest['status'] == '运行中':
                    latest = client.request('api/tasks/' + task['id'] + '/cancel', {'expected_task': task_context(latest)})
                record.update(task_id=latest['id'], research_status=latest['status'], ledger=latest['ledger'],
                    evidence_count=len(latest['evidence']), calculation_count=len(latest['calculations']),
                    error=latest.get('error'), gaps=latest.get('gaps'),
                    model_diagnostics=latest.get('reasoning_state', {}), rejected_evidence=latest['rejected_evidence'])
            except Exception:
                record['task_observation_failed'] = True
    finally:
        if runtime:
            try:
                runtime.stop()
            except Exception:
                record.update(status='failed', cleanup_error='runtime_cleanup_failed')
        try:
            models.save(dict(configuration, api_key='', clear_key=True))
            sources.save({'ifind': {'clear_access_token': True, 'clear_refresh_token': True}})
            record['probe_credentials_removed'] = not list((folder / 'store/credentials').glob('*.secret'))
            record['production_configuration_unchanged'] = all(file_identity(PRODUCTION / name) == value for name, value in protected.items())
            assert record['probe_credentials_removed'] and record['production_configuration_unchanged']
            for path in folder.rglob('*'):
                if path.is_file():
                    contents = path.read_bytes()
                    assert configuration['api_key'].encode() not in contents and refresh_token.encode() not in contents
            serialized = json.dumps(record, ensure_ascii=False)
            assert configuration['api_key'] not in serialized and refresh_token not in serialized
        except Exception:
            # Do not publish any diagnostics if credential-free identity fails.
            record = {key: value for key, value in record.items() if key not in
                      ('model_diagnostics', 'error', 'gaps', 'rejected_evidence')}
            record.update(status='failed', cleanup_error='credential_or_protected_identity_failure')
        configuration['api_key'] = ''
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({'status': record['status'], 'research_status': record.get('research_status'),
        'failed_check': record.get('failed_check'), 'used': record.get('ledger', {}).get('used'),
        'receipt': str(target)}, ensure_ascii=False))
    return record


if __name__ == '__main__':
    # Internal acceptance only; token input is not echoed or a command argument.
    selected_candidate()
    outcome = run(getpass.getpass('iFinD当前授权：'))
    if outcome['status'] != 'scoped_passed':
        raise SystemExit(1)
