"""One new bounded two-security comparison in the actual DMG, no retries.

The model chooses its plan. Expected values remain in the independent checker,
not its prompt. No production writes, user PDFs, OH credentials or App windows.
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
AUTHORIZATION = 'ifind-two-etf-comparison-four-model-six-data'
CASE = '510300-159919-close-comparison-20260921-20260930-v1'
CODES = ('510300.SH', '159919.SZ')
START, END = '2026-09-21', '2026-09-30'
LIMITS = {'data_requests': 6, 'model_calls': 5, 'seconds': 360}
QUESTION = ('请优先使用iFinD比较510300.SH与159919.SZ在2026年9月21日至9月30日的每日未复权收盘价，'
            '分别计算区间价格变化并比较观察结果，输出带来源和价格图的完整报告。'
            '只比较实际观测到的价格变化，不解释为持有总收益、跟踪差异、因果或未来表现。'
            '公司行为未核验时保留口径限制，不假设不存在；不额外判断哪只基金值得买入。')


def selected_candidate():
    if os.environ.get('RESEARCHHELPER_AUTHORIZED_LIVE_CASE') != AUTHORIZATION:
        raise RuntimeError('未选择本次双ETF有限验收范围')
    name = os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
    if not name:
        raise RuntimeError('须指定当前候选回执')
    for file in (ROOT / 'result/acceptance').glob('live-market-comparison-*/receipt.json'):
        if json.loads(file.read_text()).get('case') == CASE:
            raise RuntimeError('此比较案例已登记，不重开、重试或重置预算')
    return Path(name).resolve()


def verified_comparison(task):
    """Verify both codes, common endpoints and source cells before comparing."""
    evidence = {item['id']: item for item in task['evidence']}
    sources = {item['id']: item for item in task['source_records']}
    results = {}
    for calculation in task['calculations']:
        if calculation['method'] != 'observed_price_return_v1':
            continue
        prices, codes = [], []
        for field, date in (('start_price', START), ('end_price', END)):
            item = evidence[calculation['input_bindings'][field]]
            source = sources[item['artifact_id']]
            locator = item['record_locator']
            table = source['snapshot']['tables'][locator['table']]
            row = locator['row']
            assert source['source'] == 'ifind' and source['operation'] == 'cmd_history_quotation'
            assert table['thscode'] in CODES and table['time'][row] == date
            assert locator['field'] == 'close' and item['unit'] == '元'
            value = table['table']['close'][row]
            assert type(value) in (int, float) and math.isfinite(value) and value > 0
            assert item['value'] == value == calculation['inputs'][field]
            assert item['source_check'] == item['applicability_check'] == '通过'
            assert item.get('usage_scope') != 'document_content' and item['nature'] == '已实现事实'
            prices.append(value); codes.append(table['thscode'])
        assert codes[0] == codes[1], '不能跨证券拼接价格端点'
        assert codes[0] not in results, '同一证券重复计算不能充当第二证券'
        expected = (prices[1] / prices[0] - 1) * 100
        output = calculation['output']
        value = output['value'] if isinstance(output, dict) else output
        assert type(value) in (int, float) and math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-12)
        results[codes[0]] = {'calculation_id': calculation['id'], 'start_price': prices[0],
                            'end_price': prices[1], 'price_change_percent': expected}
    assert set(results) == set(CODES), '比较须分别使用两只证券的真实端点和计算'
    return {'objects': results, 'difference_percentage_points':
            results[CODES[0]]['price_change_percent'] - results[CODES[1]]['price_change_percent'],
            'scope': 'unadjusted_price_change_only_not_total_return'}


def run(refresh_token):
    selected = selected_candidate()
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise ValueError('需要当前研究授权，不借用其他产品凭据')
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
    folder = Path(tempfile.mkdtemp(prefix='live-market-comparison-', dir=ROOT / 'result/acceptance'))
    target = folder / 'receipt.json'
    record = {'status': 'executing', 'case': CASE, 'candidate': str(selected),
              'scope': 'actual_dmg_autonomous_two_etf_observation_comparison', 'independent_financial_quality': False,
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
        models.save(configuration); sources.save({'ifind': {'refresh_token': refresh_token}})
        runtime = AuthorizedRuntime(folder, record); client = PrivateAppClient(runtime.start())
        initial = client.request('api/bootstrap')
        check('fresh private host starts offline with no tasks', not initial['tasks'] and initial['execution']['mode'] == 'offline')
        client.request('api/settings/execution', {'enabled': True, 'confirm': True})
        execution = client.request('api/bootstrap')['execution']
        task = client.request('api/tasks', {'question': QUESTION, 'entry': 'ETF', 'intent': '比较对象',
                                          'objects': list(CODES), 'research_mode': 'current', 'limits': LIMITS})
        route = 'api/tasks/' + task['id']
        task = client.request(route + '/start', {'expected_task': task_context(task),
            'expected_execution': {field: execution[field] for field in ('mode', 'policy_version')}})
        deadline = time.monotonic() + LIMITS['seconds'] + 10
        while task['status'] == '运行中' and time.monotonic() < deadline:
            time.sleep(1); task = client.request(route)
        record.update(task_id=task['id'], research_status=task['status'], ledger=task['ledger'],
                      question_statuses=[item['status'] for item in task['questions']],
                      evidence_count=len(task['evidence']), calculation_count=len(task['calculations']))
        check('bounded model calls preserve delivery reserve', 0 < task['ledger']['used']['model_calls'] <= 4)
        check('finite data requests', 0 < task['ledger']['used']['data_requests'] <= 6)
        check('actual ifind records only', bool(task['source_records']) and all(item['source'] == 'ifind' for item in task['source_records']))
        record['independent_numeric_check'] = verified_comparison(task)
        check('both original price windows independently reproduce calculations', True)
        check('required comparison questions answered', task['status'] == '已完成' and all(
            not item['required'] or item['status'] == '已回答' for item in task['questions']))
        ids = {value['calculation_id'] for value in record['independent_numeric_check']['objects'].values()}
        check('comparison conclusion cites both calculations', any(
            ids.issubset(item.get('calculation_refs', [])) for item in task['theses']))
        report = client.request(route + '/report', html=True)
        check('both codes appear in real report', all(code in report for code in CODES) and '合成示例' not in report)
        check('report renders offline charts', 'echarts' in report.lower())
        (folder / 'report.html').write_text(report)
        record['report_sha256'] = hashlib.sha256(report.encode()).hexdigest()
        client.request('api/settings/execution', {'enabled': False, 'confirm': True})
        runtime.stop(); runtime = None
        models.save(dict(configuration, api_key='', clear_key=True))
        sources.save({'ifind': {'clear_access_token': True, 'clear_refresh_token': True}})
        runtime = PrivateRuntime(folder, record); client = PrivateAppClient(runtime.start())
        reopened = client.request(route)
        check('offline restart retains report and ledger', reopened['ledger'] == task['ledger'] and
              hashlib.sha256(client.request(route + '/report', html=True).encode()).hexdigest() == record['report_sha256'])
        check('restart does not resume research', client.request('api/bootstrap')['execution']['mode'] == 'offline')
        check('candidate remains intact', verify_current_build(selected, ROOT) == candidate)
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
            try: runtime.stop()
            except Exception: record.update(status='failed', cleanup_error='runtime_cleanup_failed')
        try:
            models.save(dict(configuration, api_key='', clear_key=True))
            sources.save({'ifind': {'clear_access_token': True, 'clear_refresh_token': True}})
            record['probe_credentials_removed'] = not list((folder / 'store/credentials').glob('*.secret'))
            record['production_configuration_unchanged'] = all(file_identity(PRODUCTION / name) == value for name, value in protected.items())
            assert record['probe_credentials_removed'] and record['production_configuration_unchanged']
            for file in folder.rglob('*'):
                if file.is_file():
                    contents = file.read_bytes()
                    assert configuration['api_key'].encode() not in contents and refresh_token.encode() not in contents
            serialized = json.dumps(record, ensure_ascii=False)
            assert configuration['api_key'] not in serialized and refresh_token not in serialized
        except Exception:
            record = {key: value for key, value in record.items() if key not in ('model_diagnostics', 'error', 'gaps', 'rejected_evidence')}
            record.update(status='failed', cleanup_error='credential_or_protected_identity_failure')
        configuration['api_key'] = ''
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({'status': record['status'], 'research_status': record.get('research_status'),
                      'failed_check': record.get('failed_check'), 'used': record.get('ledger', {}).get('used'),
                      'receipt': str(target)}, ensure_ascii=False))
    return record


if __name__ == '__main__':
    selected_candidate()
    outcome = run(getpass.getpass('iFinD当前研究授权：'))
    if outcome['status'] != 'scoped_passed':
        raise SystemExit(1)
