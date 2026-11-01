"""One natural-input, real-model calculation case in the actual DMG.

All supplied rates and dates are explicitly synthetic. This exercises autonomous
planning/extraction/calculation/answer delivery, not actual fund facts or returns.
No expected answer or expected difference is sent in the question or materials.
"""
import base64
from decimal import Decimal
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
CASE = 'controlled-management-custody-comparison-natural-input-v1'
AUTHORIZATION = 'controlled-fee-comparison-six-model-no-supplier'
METHOD = 'management_custody_fee_comparison_v1'
LIMITS = {'data_requests': 1, 'model_calls': 7, 'seconds': 360}
QUESTION = ('这是合成软件验收，不是真实基金研究。仅依据附件，比较样例ETF甲与样例ETF乙在2026年度规则下的'
            '管理费与托管费年费率合计，说明差异及其适用边界。不要查询实际行情、判断未来表现或推荐买卖。')
RATES = {'management_a': '.18', 'custody_a': '.04', 'management_b': '.35', 'custody_b': '.08'}
TEXT = ('合成软件验收材料，不对应真实产品，不能用于现实投资决策。\n\n'
        '合成条款的发布日期：2026年10月12日。\n\n'
        '样例ETF甲，份额类别为样例ETF甲A份额，期间为2026年度规则。管理费为0.18%/年，计费基数为该份额基金净资产。\n\n'
        '样例ETF甲，份额类别为样例ETF甲A份额，期间为2026年度规则。托管费为0.04%/年，计费基数为该份额基金净资产。\n\n'
        '样例ETF乙，份额类别为样例ETF乙A份额，期间为2026年度规则。管理费为0.35%/年，计费基数为该份额基金净资产。\n\n'
        '样例ETF乙，份额类别为样例ETF乙A份额，期间为2026年度规则。托管费为0.08%/年，计费基数为该份额基金净资产。\n\n'
        '材料只列两项合同年费率，不包含申赎费用、交易佣金、实际净值、持有期、分红或业绩数据。')


def selected_candidate():
    if os.environ.get('RESEARCHHELPER_AUTHORIZED_LIVE_CASE') != AUTHORIZATION:
        raise RuntimeError('未选择本次有限模型验收范围')
    value = os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
    if not value:
        raise RuntimeError('需要明确的当前候选')
    for file in (ROOT / 'result/acceptance').glob('live-controlled-fees-*/receipt.json'):
        if json.loads(file.read_text()).get('case') == CASE:
            raise RuntimeError('此案例已经登记，不重开、重试或重置预算')
    return Path(value).resolve()


def verify_comparison(task, report):
    """Independent arithmetic and cell/scope checks, not fluency scoring."""
    assert task['question'] == QUESTION and task['entry'] == 'ETF' and task['intent'] == '比较对象'
    assert task['status'] == '已完成'
    required = [item for item in task['questions'] if item['required']]
    assert required and all(item['status'] == '已回答' for item in required)
    assert task['questions'][0]['question'] == QUESTION
    assert not task['source_records'], '合成材料研究不得获取现实数据'
    calculations = [item for item in task['calculations'] if item['method'] == METHOD]
    assert len(calculations) == len(task['calculations']) == 1, '必须实际执行登记的四项费用比较'
    calculation = calculations[0]
    assert calculation['task_id'] == task['id'] and calculation['research_version'] == task['version']
    assert calculation['parameters'] == {'unit': '%/年', 'period': '2026年度规则'}
    evidence = {item['id']: item for item in task['evidence']}
    assert set(calculation['input_bindings']) == set(RATES)
    assert set(calculation['input_refs']) == set(calculation['input_bindings'].values())
    assert len(set(calculation['input_bindings'].values())) == 4
    for field, ref in calculation['input_bindings'].items():
        item = evidence[ref]
        side = '甲' if field.endswith('_a') else '乙'
        component = 'management_fee' if field.startswith('management') else 'custody_fee'
        assert Decimal(str(item['raw_value'])) == Decimal(str(item['value'])) == Decimal(RATES[field])
        assert item['unit'] == item['raw_unit'] == '%/年' and item['conversion_factor'] == 1
        assert item['object'] == '样例ETF' + side
        assert item['source_check'] == item['applicability_check'] == '通过'
        assert item['task_id'] == task['id'] and item['research_version'] == task['version']
        assert item['calculation_context']['fund_id'] == '样例ETF' + side
        assert item['calculation_context']['share_class'] == '样例ETF' + side + 'A份额'
        assert item['calculation_context']['metric'] == component
        assert item['calculation_context']['fee_basis'] == 'net_assets'
        assert item['calculation_context']['rate_period'] == 'annual'
        assert item['calculation_context']['period'] == '2026年度规则'
        assert item.get('usage_scope') != 'document_content'
        assert Decimal(str(calculation['inputs'][field])) == Decimal(RATES[field])
    expected = Decimal(RATES['management_a']) + Decimal(RATES['custody_a']) - Decimal(RATES['management_b']) - Decimal(RATES['custody_b'])
    assert abs(Decimal(str(calculation['value'])) - expected) < Decimal('0.000000000001')
    assert calculation['unit'] == '百分点/年'
    answers = [item for item in required if calculation['id'] in item.get('calculation_refs', [])]
    assert answers, '必要回答必须实际引用登记计算'
    assert any(set(calculation['input_refs']).issubset(item.get('refs', [])) for item in answers)
    assert '合成' in report and '样例ETF甲' in report and '样例ETF乙' in report
    return {'expected_difference': str(expected), 'actual_difference': calculation['value'],
            'unit': calculation['unit'], 'method': METHOD, 'input_evidence': calculation['input_refs'],
            'synthetic_only': True, 'independent_investment_quality': False}


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
    # An explicit private test selection, never a production/default migration.
    configuration['reasoning_effort'] = 'none'
    output = Path(tempfile.mkdtemp(prefix='live-controlled-fees-', dir=ROOT / 'result/acceptance'))
    receipt = output / 'receipt.json'
    record = {'status': 'executing', 'case': CASE, 'candidate': str(selected), 'limits': LIMITS,
              'max_real_model_requests': 6, 'scope': 'autonomous_controlled_fee_calculation_not_real_fund_research',
              'synthetic_only': True, 'independent_financial_quality': False,
              'automatic_retry': False, 'user_documents_sent': False, 'supplier_requests': 0,
              'native_window_opened': False, 'installed': False, 'production_store_touched': False,
              'explicit_private_reasoning_effort': 'none', 'checks': []}
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
        check('fresh private Store starts offline', boot['execution']['mode'] == 'offline' and not boot['tasks'])
        client.request('api/settings/execution', {'enabled': True, 'confirm': True})
        execution = client.request('api/bootstrap')['execution']
        task = client.request('api/tasks', {'question': QUESTION, 'entry': 'ETF', 'intent': '比较对象',
                                         'objects': ['样例ETF甲', '样例ETF乙'], 'research_mode': 'current', 'limits': LIMITS})
        route = 'api/tasks/' + task['id']
        task = client.request(route + '/material', {'name': '合成费用条款.txt',
            'base64': base64.b64encode(TEXT.encode()).decode(), 'expected_version': task['version']})
        record['material_sha256'] = hashlib.sha256(TEXT.encode()).hexdigest()
        task = client.request(route + '/start', {'expected_task': task_context(task),
            'expected_execution': {key: execution[key] for key in ('mode', 'policy_version')}})
        deadline = time.monotonic() + LIMITS['seconds'] + 10
        while task['status'] == '运行中' and time.monotonic() < deadline:
            time.sleep(1)
            task = client.request(route)
        record.update(task_id=task['id'], research_status=task['status'], ledger=task['ledger'],
                      evidence_count=len(task['evidence']), calculation_count=len(task['calculations']))
        check('bounded real calls preserve delivery reserve', 0 < task['ledger']['used']['model_calls'] <= 6)
        check('no supplier calls', task['ledger']['used']['data_requests'] == 0)
        report = client.request(route + '/report', html=True)
        record['comparison_check'] = verify_comparison(task, report)
        check('independent four-rate recomputation and necessary answers verified', True)
        (output / 'report.html').write_text(report)
        record['report_sha256'] = hashlib.sha256(report.encode()).hexdigest()
        record['required_answers'] = [{key: item.get(key) for key in ('id', 'question', 'kind', 'answer', 'refs', 'calculation_refs')}
                                      for item in task['questions'] if item['required']]
        client.request('api/settings/execution', {'enabled': False, 'confirm': True})
        runtime.stop()
        runtime = None
        settings.save(configuration | {'api_key': '', 'clear_key': True})
        runtime = PrivateRuntime(output, record)
        client = PrivateAppClient(runtime.start())
        reopened = client.request(route)
        check('offline restart preserves ledger and report', reopened['ledger'] == task['ledger'] and
              hashlib.sha256(client.request(route + '/report', html=True).encode()).hexdigest() == record['report_sha256'])
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
                record.update(research_status=latest['status'], ledger=latest['ledger'], error=latest.get('error'),
                              gaps=latest.get('gaps'), diagnostics=latest.get('reasoning_state', {}),
                              evidence_count=len(latest['evidence']), calculation_count=len(latest['calculations']))
            except Exception:
                record['task_observation_failed'] = True
    finally:
        if runtime:
            try:
                runtime.stop()
            except Exception:
                record.update(status='failed', cleanup_error='runtime_cleanup_failed')
        try:
            settings.save(configuration | {'api_key': '', 'clear_key': True})
            record['probe_credentials_removed'] = not list((output / 'store/credentials').glob('*.secret'))
            record['production_configuration_unchanged'] = all(file_identity(PRODUCTION / name) == value for name, value in protected.items())
            assert record['probe_credentials_removed'] and record['production_configuration_unchanged']
            for file in output.rglob('*'):
                if file.is_file():
                    assert configuration['api_key'].encode() not in file.read_bytes()
            assert configuration['api_key'] not in json.dumps(record)
        except Exception:
            record = {key: value for key, value in record.items() if key not in ('diagnostics', 'error', 'gaps', 'required_answers')}
            record.update(status='failed', cleanup_error='credential_or_protected_identity_failure')
        configuration['api_key'] = ''
        receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({'status': record['status'], 'research_status': record.get('research_status'),
                      'used': record.get('ledger', {}).get('used'), 'receipt': str(receipt)}))
    if record['status'] != 'scoped_passed':
        raise SystemExit(1)


if __name__ == '__main__':
    run()
