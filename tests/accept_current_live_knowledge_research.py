"""One fresh, bounded App research through RH's own authorized model service.

General ETF definitions only. No market values, user PDFs, supplier credential,
retry, production write or foreground window. This is not investment-quality
acceptance. Expected conditions stay in the checker, not the model prompt.
"""
import hashlib
import json
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
AUTHORIZATION = 'fresh-etf-definition-two-model-no-supplier'
CASE = 'etf-price-nav-total-return-general-definitions-v1'
QUESTION = '解释ETF市场价格、单位净值和总回报有什么区别。只说明通用口径，不查询实际基金数字，不判断买卖或未来表现。'
LIMITS = {'model_calls': 3, 'data_requests': 1, 'seconds': 180}


def selected_candidate():
    if os.environ.get('RESEARCHHELPER_AUTHORIZED_LIVE_CASE') != AUTHORIZATION:
        raise RuntimeError('未选择本次基础解释有限验收范围')
    selected = os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
    if not selected:
        raise RuntimeError('必须指定当前候选')
    for file in (ROOT / 'result/acceptance').glob('live-knowledge-research-*/receipt.json'):
        if json.loads(file.read_text()).get('case') == CASE:
            raise RuntimeError('本案例已登记，不重开、重试或重置预算')
    return Path(selected).resolve()


def verify_definition(task, report):
    """Verify the required answer, not just appended knowledge in HTML."""
    from researchhelper.contracts import fingerprint
    from researchhelper.knowledge import ENTRIES
    assert task['question'] == QUESTION and task['entry'] == 'ETF'
    assert task['status'] == '已完成'
    required = [item for item in task['questions'] if item['required']]
    assert required and all(item['status'] == '已回答' for item in required)
    assert task['questions'][0]['question'] == QUESTION
    assert not any(task.get(name) for name in ('source_records', 'materials', 'evidence', 'calculations', 'theses', 'tracking'))
    knowledge = next(item for item in task['knowledge']['entries'] if item['id'] == 'rh_k_etf_measures')
    original = next(item for item in ENTRIES if item['id'] == knowledge['id'] and item['version'] == knowledge['version'])
    # Identity/version/hash must be authentic; a successful parser is not approval.
    assert fingerprint(knowledge) == fingerprint(original)
    combined = '\n'.join(item['answer'] for item in required)
    assert all(token in combined for token in ('市场价格', '单位净值', '总回报', '分配'))
    assert all(item.get('answer_scope') == 'general_definition' for item in required)
    assert all(knowledge['id'] in item.get('knowledge_needs', []) for item in required)
    assert knowledge['content'] in combined and knowledge['conditions'] in combined
    assert combined in report or all(item['answer'] in report for item in required)
    sources = [knowledge['source_reference'], *knowledge.get('additional_source_references', [])]
    assert len(sources) >= 2 and all('href="' + source['url'] + '"' in report for source in sources)
    assert not any(token in report for token in ('衍生品产品推荐', '合成示例'))
    return {'knowledge_id': knowledge['id'], 'knowledge_version': knowledge['version'],
            'knowledge_sha256': fingerprint(knowledge), 'public_source_count': len(sources),
            'general_definition_only': True, 'market_value_approval': False,
            'independent_investment_quality': False}


def run():
    selected = selected_candidate()
    sys.path[:0] = [str(ROOT / 'packaging'), str(ROOT / 'core')]
    from build_current import verify_current_build
    from bootstrap import configure_imports
    configure_imports()
    from researchhelper.model import ModelSettings
    candidate = verify_current_build(selected, ROOT)
    protected = {name: file_identity(PRODUCTION / name) for name in
                 ('model-settings.json', 'source-settings.json', 'execution-policy.json')}
    configuration = eligible_configuration(ModelSettings(PRODUCTION).read(private=True))
    output = Path(tempfile.mkdtemp(prefix='live-knowledge-research-', dir=ROOT / 'result/acceptance'))
    receipt = output / 'receipt.json'
    record = {'status': 'executing', 'case': CASE, 'candidate': str(selected),
              'scope': 'actual_dmg_autonomous_general_definition_not_investment_research',
              'max_real_model_requests': 2, 'limits': LIMITS, 'model': configuration['model'],
              'automatic_retry': False, 'supplier_requests': 0, 'user_documents_sent': False,
              'native_window_opened': False, 'installed': False, 'production_store_touched': False,
              'independent_financial_quality': False, 'checks': []}
    receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    settings = ModelSettings(output / 'store')
    runtime = client = task = None
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        record['checks'].append(name)
    try:
        settings.save(configuration)
        runtime = AuthorizedRuntime(output, record)
        client = PrivateAppClient(runtime.start())
        boot = client.request('api/bootstrap')
        check('fresh private host starts offline without tasks', boot['execution']['mode'] == 'offline' and not boot['tasks'])
        client.request('api/settings/execution', {'enabled': True, 'confirm': True})
        execution = client.request('api/bootstrap')['execution']
        task = client.request('api/tasks', {'question': QUESTION, 'entry': 'ETF', 'intent': '综合研究',
                                         'research_mode': 'current', 'limits': LIMITS})
        route = 'api/tasks/' + task['id']
        task = client.request(route + '/start', {'expected_task': task_context(task),
            'expected_execution': {key: execution[key] for key in ('mode', 'policy_version')}})
        deadline = time.monotonic() + LIMITS['seconds'] + 10
        while task['status'] == '运行中' and time.monotonic() < deadline:
            time.sleep(1)
            task = client.request(route)
        record.update(task_id=task['id'], research_status=task['status'], ledger=task['ledger'],
                      question_statuses=[item['status'] for item in task['questions']])
        check('finite real model calls preserve delivery reserve', 0 < task['ledger']['used']['model_calls'] <= 2)
        check('no supplier request for general concepts', task['ledger']['used']['data_requests'] == 0)
        report = client.request(route + '/report', html=True)
        record['definition_check'] = verify_definition(task, report)
        check('actual required answer and public sources verified', True)
        (output / 'report.html').write_text(report)
        record['report_sha256'] = hashlib.sha256(report.encode()).hexdigest()
        client.request('api/settings/execution', {'enabled': False, 'confirm': True})
        runtime.stop()
        runtime = None
        settings.save(dict(configuration, api_key='', clear_key=True))
        runtime = PrivateRuntime(output, record)
        client = PrivateAppClient(runtime.start())
        reopened = client.request(route)
        check('offline restart retains ledger and saved report', reopened['ledger'] == task['ledger'] and
              hashlib.sha256(client.request(route + '/report', html=True).encode()).hexdigest() == record['report_sha256'])
        check('offline restart does not start a new research', client.request('api/bootstrap')['execution']['mode'] == 'offline')
        check('candidate is unchanged', verify_current_build(selected, ROOT) == candidate)
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
            settings.save(dict(configuration, api_key='', clear_key=True))
            record['probe_credentials_removed'] = not list((output / 'store/credentials').glob('*.secret'))
            record['production_configuration_unchanged'] = all(file_identity(PRODUCTION / name) == value for name, value in protected.items())
            assert record['probe_credentials_removed'] and record['production_configuration_unchanged']
            for file in output.rglob('*'):
                if file.is_file():
                    assert configuration['api_key'].encode() not in file.read_bytes()
            assert configuration['api_key'] not in json.dumps(record)
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
    run()
