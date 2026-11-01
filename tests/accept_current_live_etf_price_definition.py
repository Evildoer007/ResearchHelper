"""One fresh current ETF fact-plus-definition case in the actual DMG.

The checker verifies retained iFinD cells separately from the model. No expected
price is in its prompt; no user PDF, OH credential, retry or production writes.
Only the in-memory refresh token supplied by the user is accepted.
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
    AuthorizedRuntime, PrivateAppClient, PRODUCTION, eligible_configuration, file_identity, task_context,
)
from tests.accept_packaged_credential_separation import PrivateRuntime

ROOT = Path(__file__).resolve().parents[1]
CASE = '510500-close-and-nav-distinction-20261012-v1'
AUTHORIZATION = 'fresh-ifind-etf-fact-definition-four-model-four-data'
CODE, OBSERVATION = '510500.SH', '2026-10-12'
LIMITS = {'data_requests': 4, 'model_calls': 5, 'seconds': 300}
QUESTION = ('请优先用iFinD核对510500.SH在2026年10月12日的未复权收盘价，并解释这个交易价格是否能当作基金单位净值。'
            '只核对交易价格和说明通用口径，不查询基金净值，不计算折溢价，不判断买卖或未来表现。')


def selected_candidate():
    if os.environ.get('RESEARCHHELPER_AUTHORIZED_LIVE_CASE') != AUTHORIZATION:
        raise RuntimeError('未选择本次ETF事实及口径有限验收范围')
    value = os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
    if not value:
        raise RuntimeError('需要明确的当前候选')
    for file in (ROOT / 'result/acceptance').glob('live-etf-price-definition-*/receipt.json'):
        if json.loads(file.read_text()).get('case') == CASE:
            raise RuntimeError('此案例已登记，不重开、重试或重置预算')
    return Path(value).resolve()


def verify_price_definition(task):
    """Check actual source cells and required citations, never report fluency."""
    from researchhelper.contracts import fingerprint
    assert task['question'] == QUESTION and task['entry'] == 'ETF'
    assert task['status'] == '已完成'
    required = [item for item in task['questions'] if item['required']]
    assert required and all(item['status'] == '已回答' for item in required)
    assert task['questions'][0]['question'] == QUESTION
    assert not any(task.get(name) for name in ('calculations', 'theses', 'tracking'))
    originals = {item['id']: item for item in task['source_records']}
    qualified = []
    for evidence in task['evidence']:
        source = originals[evidence['artifact_id']]
        assert source['task_id'] == evidence['task_id'] == task['id']
        assert source['research_version'] == evidence['research_version'] == task['version']
        if source['source'] != 'ifind' or source['operation'] != 'cmd_history_quotation':
            continue
        locator = evidence['record_locator']
        table = source['snapshot']['tables'][locator['table']]
        if table['thscode'] != CODE or locator['field'] != 'close' or table['time'][locator['row']] != OBSERVATION:
            continue
        assert fingerprint(source['snapshot']) == source['hash'] == evidence['snapshot_hash']
        value = table['table']['close'][locator['row']]
        assert type(value) in (int, float) and math.isfinite(value) and value > 0
        assert evidence['value'] == evidence['raw_value'] == value
        assert evidence['raw_unit'] == evidence['unit'] == '元' and evidence['conversion_factor'] == 1
        assert evidence['source_check'] == evidence['applicability_check'] == '通过'
        assert evidence['nature'] == '已实现事实' and evidence.get('usage_scope') != 'document_content'
        assert source['parameters']['functionpara'] == {'Currency': 'RMB', 'CPS': '1', 'Fill': 'Omit'}
        qualified.append((evidence['id'], value, source['id']))
    assert qualified, '无对应证券、日期及字段的已核验价格'
    assert len({value for _, value, _ in qualified}) == 1, '原值存在冲突'
    ids = {ident for ident, _, _ in qualified}
    assert any(ids.intersection(item.get('refs', [])) for item in required), '必要回答未引用该价格'
    answers = '\n'.join(item['answer'] for item in required)
    assert all(token in answers for token in ('交易价格', '单位净值'))
    definitions = [item for item in required if item.get('answer_scope') == 'general_definition']
    assert definitions, '口径说明未采用独立定义范围'
    knowledge = next(item for item in task['knowledge']['entries'] if item['id'] == 'rh_k_etf_measures')
    assert any(knowledge['content'] in item['answer'] for item in definitions)
    return {'code': CODE, 'observation_date': OBSERVATION, 'raw_close': qualified[0][1], 'unit': '元',
            'source_ids': sorted({source for _, _, source in qualified}), 'evidence_ids': sorted(ids),
            'scope': 'observed_close_and_general_definition_not_nav_or_investment',
            'independent_investment_quality': False}


def run(refresh_token):
    selected = selected_candidate()
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise ValueError('需要本次研究授权，不读取或借用其他产品凭据')
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
    output = Path(tempfile.mkdtemp(prefix='live-etf-price-definition-', dir=ROOT / 'result/acceptance'))
    receipt = output / 'receipt.json'
    record = {'status': 'executing', 'case': CASE, 'candidate': str(selected), 'limits': LIMITS,
              'scope': 'actual_dmg_ifind_observed_price_and_general_definition',
              'max_real_model_requests': 4, 'max_real_data_requests': 4, 'model': configuration['model'],
              'automatic_retry': False, 'user_documents_sent': False, 'native_window_opened': False,
              'installed': False, 'production_store_touched': False, 'independent_financial_quality': False,
              'checks': []}
    receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    models, sources = ModelSettings(output / 'store'), DataSettings(output / 'store')
    runtime = client = task = None
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        record['checks'].append(name)
    try:
        models.save(configuration)
        sources.save({'ifind': {'refresh_token': refresh_token}})
        runtime = AuthorizedRuntime(output, record)
        client = PrivateAppClient(runtime.start())
        boot = client.request('api/bootstrap')
        check('fresh isolated host starts offline', boot['execution']['mode'] == 'offline' and not boot['tasks'])
        client.request('api/settings/execution', {'enabled': True, 'confirm': True})
        execution = client.request('api/bootstrap')['execution']
        task = client.request('api/tasks', {'question': QUESTION, 'entry': 'ETF', 'intent': '综合研究',
                                          'objects': [CODE], 'research_mode': 'current', 'limits': LIMITS})
        route = 'api/tasks/' + task['id']
        task = client.request(route + '/start', {'expected_task': task_context(task),
            'expected_execution': {key: execution[key] for key in ('mode', 'policy_version')}})
        deadline = time.monotonic() + LIMITS['seconds'] + 10
        while task['status'] == '运行中' and time.monotonic() < deadline:
            time.sleep(1)
            task = client.request(route)
        record.update(task_id=task['id'], research_status=task['status'], ledger=task['ledger'],
                      evidence_count=len(task['evidence']), question_statuses=[item['status'] for item in task['questions']])
        check('finite dispatches preserve delivery reserve', 0 < task['ledger']['used']['model_calls'] <= 4 and
              0 < task['ledger']['used']['data_requests'] <= 4)
        record['independent_price_check'] = verify_price_definition(task)
        check('actual price cell and separate definition scope verified', True)
        report = client.request(route + '/report', html=True)
        check('real report contains security and source', CODE in report and 'iFinD' in report and '合成示例' not in report)
        (output / 'report.html').write_text(report)
        record['report_sha256'] = hashlib.sha256(report.encode()).hexdigest()
        client.request('api/settings/execution', {'enabled': False, 'confirm': True})
        runtime.stop()
        runtime = None
        models.save(dict(configuration, api_key='', clear_key=True))
        sources.save({'ifind': {'clear_access_token': True, 'clear_refresh_token': True}})
        runtime = PrivateRuntime(output, record)
        client = PrivateAppClient(runtime.start())
        reopened = client.request(route)
        check('offline restart retains report and ledger', reopened['ledger'] == task['ledger'] and
              hashlib.sha256(client.request(route + '/report', html=True).encode()).hexdigest() == record['report_sha256'])
        check('offline restart stays offline', client.request('api/bootstrap')['execution']['mode'] == 'offline')
        check('candidate identity unchanged', verify_current_build(selected, ROOT) == candidate)
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
                              error=latest.get('error'), gaps=latest.get('gaps'),
                              diagnostics=latest.get('reasoning_state', {}))
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
            record['probe_credentials_removed'] = not list((output / 'store/credentials').glob('*.secret'))
            record['production_configuration_unchanged'] = all(file_identity(PRODUCTION / name) == value for name, value in protected.items())
            assert record['probe_credentials_removed'] and record['production_configuration_unchanged']
            for file in output.rglob('*'):
                if file.is_file():
                    data = file.read_bytes()
                    assert configuration['api_key'].encode() not in data and refresh_token.encode() not in data
            serialized = json.dumps(record)
            assert configuration['api_key'] not in serialized and refresh_token not in serialized
        except Exception:
            record = {key: value for key, value in record.items() if key not in ('diagnostics', 'error', 'gaps')}
            record.update(status='failed', cleanup_error='credential_or_protected_identity_failure')
        configuration['api_key'] = ''
        receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({'status': record['status'], 'research_status': record.get('research_status'),
                      'used': record.get('ledger', {}).get('used'), 'receipt': str(receipt)}))
    if record['status'] != 'scoped_passed':
        raise SystemExit(1)


if __name__ == '__main__':
    selected_candidate()
    run(getpass.getpass('本次iFinD研究授权：'))
