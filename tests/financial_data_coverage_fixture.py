"""Pure address coverage from the retained financial-data acceptance.

No live harness is imported or started by offline module tests.
"""
from researchhelper.financial_sources import context_binding

PERIODS=['20260630','20250630']


def planned_data_coverage(task, product):
    """Bind the actual nine q2/q4 needs; do not manufacture accepted evidence."""
    assert product['binding'] == context_binding(task), 'Data product belongs to another task or version'
    core = product['core']; fields = product['supplements']['fields']
    coverage = []
    for question in task['questions']:
        for need in question.get('evidence_coverage', {}).get('items', []):
            text = need['text']; details = []
            if '报表主体、合并范围、币种、金额单位及会计准则' in text:
                ready = not product['missing_context_periods']
                details = ['/data/report_contexts/'+period+'/0' for period in PERIODS]
            elif '净利润调节至经营活动现金流量净额' in text:
                ready = fields['cash_reconciliation']['status'] == 'decoded' and fields['cash_reconciliation']['data_checks_passed']
                details = ['/data/supplements/fields/cash_reconciliation']
            elif '非经常性损益项目及金额' in text:
                details = ['/data/supplements/fields/nonrecurring_'+period for period in PERIODS]
                ready = all(fields['nonrecurring_'+period]['data_checks_passed'] for period in PERIODS)
            elif '购建固定资产、无形资产和其他长期资产支付的现金' in text:
                details = ['/data/supplements/fields/capital_expenditure']
                ready = fields['capital_expenditure']['status'] == 'decoded'
            elif '变动原因及业务构成的管理层说明' in text:
                details = ['/data/supplements/fields/management_'+period for period in PERIODS]
                ready = all(fields['management_'+period]['complete_section'] for period in PERIODS)
            else:
                metric = next((metric for token, metric in (
                    ('合并利润表营业收入', 'revenue'), ('合并利润表营业成本', 'operating_cost'),
                    ('合并利润表净利润', 'net_profit'),
                    ('合并现金流量表经营活动产生的现金流量净额', 'operating_cash_flow')) if token in text), None)
                assert metric is not None, 'Unmapped actual data need: '+text
                details = ['/data/core/values/'+str(index) for index,row in enumerate(core['values'])
                           if row['metric'] == metric and row['period'] in PERIODS]
                ready = all(any(row['metric'] == metric and row['period'] == period
                                for row in core['values']) for period in PERIODS)
            coverage.append(dict(task_id=task['id'], research_version=task['version'],
                input_hash=task['input_hash'], question_id=question['id'], need_id=need['id'],
                text=text, original_data_available=ready, data_paths=details,
                evidence_adopted=False, question_answered=False))
    assert len(coverage) == 9 and all(item['original_data_available'] for item in coverage)
    for item in coverage:
        for pointer in item['data_paths']:
            value = {'data':product}
            for token in pointer.lstrip('/').split('/'):
                value = value[int(token)] if isinstance(value, list) else value[token]
            assert value, 'Empty data address: '+pointer
    return coverage
