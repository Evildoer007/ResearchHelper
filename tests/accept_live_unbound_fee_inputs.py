"""One new annual-word case after the deferred input contract correction.

Different material, rates and question from retained failed cases. No answer
or method IDs are sent to the model. A failed/partial task is never restarted.
"""
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
from tests import accept_live_controlled_fee_comparison as retained

ROOT = Path(__file__).resolve().parents[1]
CASE = 'deferred-input-fee-comparison-annual-words-v1'
AUTHORIZATION = 'deferred-fee-six-model-no-supplier'
METHOD = 'management_custody_fee_comparison_v1'
RATES = {'management_a': '.21', 'custody_a': '.04', 'management_b': '.32', 'custody_b': '.06'}
QUESTION = ('仅依据所附合成条款，比较样例ETF甲与样例ETF乙在2026年度规则下的管理费及托管费合计，'
            '说明差异与适用边界。这是软件验收材料，不是实际产品，不查询行情、不预测表现、不推荐买卖。')
TEXT = ('合成软件验收材料，不对应真实产品，不能用于现实投资决策。\n\n'
        '合成条款的发布日期：2026年10月12日。\n\n'
        '样例ETF甲，份额类别为样例ETF甲A份额，期间为2026年度规则。管理费年费率为0.21%，计费基数为该份额基金净资产。\n\n'
        '样例ETF甲，份额类别为样例ETF甲A份额，期间为2026年度规则。托管费年费率为0.04%，计费基数为该份额基金净资产。\n\n'
        '样例ETF乙，份额类别为样例ETF乙A份额，期间为2026年度规则。管理费年费率为0.32%，计费基数为该份额基金净资产。\n\n'
        '样例ETF乙，份额类别为样例ETF乙A份额，期间为2026年度规则。托管费年费率为0.06%，计费基数为该份额基金净资产。\n\n'
        '材料只列管理费与托管费两项名义年费率，不包含申赎费用、交易佣金、销售服务费或其他开支。'
        '未提供实际扣费、持有期、净值、分红或业绩数据。')


def selected_candidate():
    if os.environ.get('RESEARCHHELPER_AUTHORIZED_LIVE_CASE') != AUTHORIZATION:
        raise RuntimeError('未选择本次有限模型验收范围')
    value = os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
    if not value:
        raise RuntimeError('须明确当前候选')
    for file in (ROOT / 'result/acceptance').glob('live-controlled-fees-*/receipt.json'):
        if json.loads(file.read_text()).get('case') == CASE:
            raise RuntimeError('已有本次案例，不重开、重试或重置预算')
    return Path(value).resolve()


def verify_comparison(task, report):
    """Recompute privately; these expectations are never research inputs."""
    assert task['question'] == QUESTION and task['entry'] == 'ETF' and task['intent'] == '比较对象'
    assert task['status'] == '已完成'
    required = [item for item in task['questions'] if item['required']]
    assert required and all(item['status'] == '已回答' for item in required)
    assert task['questions'][0]['question'] == QUESTION
    assert not task['source_records']
    assert len(task['calculations']) == 1
    calculation = task['calculations'][0]
    assert calculation['method'] == METHOD
    assert calculation['task_id'] == task['id'] and calculation['research_version'] == task['version']
    assert calculation['parameters'] == {'unit': '%/年', 'period': '2026年度规则'}
    assert set(calculation['input_bindings']) == set(RATES)
    assert len(set(calculation['input_bindings'].values())) == 4
    assert set(calculation['input_refs']) == set(calculation['input_bindings'].values())
    evidence = {item['id']: item for item in task['evidence']}
    for field, ref in calculation['input_bindings'].items():
        item = evidence[ref]
        side = '甲' if field.endswith('_a') else '乙'
        metric = 'management_fee' if field.startswith('management') else 'custody_fee'
        assert Decimal(str(item['raw_value'])) == Decimal(str(item['value'])) == Decimal(RATES[field])
        # This material explicitly supplies percent and separately names the
        # annual frequency. No annualization or unit rewrite is permitted.
        assert item['unit'] == item['raw_unit'] == '%' and item['conversion_factor'] == 1
        assert item['object'] == '样例ETF' + side
        assert item['source_check'] == item['applicability_check'] == '通过'
        assert item['task_id'] == task['id'] and item['research_version'] == task['version']
        assert item['calculation_context'] == {'fund_id': '样例ETF' + side,
            'share_class': '样例ETF' + side + 'A份额', 'period': '2026年度规则',
            'fee_basis': 'net_assets', 'rate_period': 'annual', 'metric': metric}
        assert set(item['calculation_context_basis']) == set(item['calculation_context'])
        assert item.get('usage_scope') != 'document_content'
        assert Decimal(str(calculation['inputs'][field])) == Decimal(RATES[field])
    expected = Decimal(RATES['management_a']) + Decimal(RATES['custody_a']) - Decimal(RATES['management_b']) - Decimal(RATES['custody_b'])
    assert abs(Decimal(str(calculation['value'])) - expected) < Decimal('0.000000000001')
    assert calculation['unit'] == '百分点/年'
    answers = [item for item in required if calculation['id'] in item.get('calculation_refs', [])]
    assert answers and any(set(calculation['input_refs']).issubset(item.get('refs', [])) for item in answers)
    assert '合成' in report and '样例ETF甲' in report and '样例ETF乙' in report
    return {'expected_difference': str(expected), 'actual_difference': calculation['value'],
        'unit': calculation['unit'], 'method': METHOD, 'input_evidence': calculation['input_refs'],
        'raw_unit_preserved': True, 'annual_frequency_independently_anchored': True,
        'synthetic_only': True, 'independent_investment_quality': False}


def run():
    file = Path(retained.__file__)
    original_hash = hashlib.sha256(file.read_bytes()).hexdigest()
    original_configuration = retained.eligible_configuration
    def private_beta(configuration):
        value = original_configuration(configuration)
        value['endpoint'] = 'https://api.deepseek.com/beta/chat/completions'
        return value
    retained.CASE, retained.AUTHORIZATION = CASE, AUTHORIZATION
    retained.RATES, retained.QUESTION, retained.TEXT = RATES, QUESTION, TEXT
    retained.selected_candidate = selected_candidate
    retained.eligible_configuration = private_beta
    retained.verify_comparison = verify_comparison
    try:
        retained.run()
    finally:
        assert hashlib.sha256(file.read_bytes()).hexdigest() == original_hash


if __name__ == '__main__':
    run()
