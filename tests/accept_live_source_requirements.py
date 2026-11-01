"""Fresh natural research with saved RH service and actual iFinD responses.

This is a new price-study task after the repair, not a retry or rewritten
delivery. No answers, evidence, calculations or plan are injected.
"""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.bootstrap import configure_imports
configure_imports()
from researchhelper.provenance import query_requirements, validate_query_answer
from researchhelper.reports import compose
from tests import accept_installed_natural_research as acceptance

CASE = 'saved-endpoint-source-requirement-price-study-20261014-v3'
QUESTION = ('核对510300.SH在2026年9月21日至30日的实际收盘价格，'
    '计算该窗口的未复权价格变化，明确实际起终交易日，并生成含价格图、可复算方法和来源的报告。'
    '数据来源须能核对实际接口、请求参数与返回记录，不以这些元数据替代价格事实。'
    '公司行为未核验时说明限制，不假定没有分红，不把价格变化当总收益，不预测未来收益或推荐买卖。')


def run(refresh_token):
    acceptance.CASE, acceptance.QUESTION = CASE, QUESTION
    acceptance.LIMITS = dict(data_requests=8, model_calls=8, seconds=600)
    actual = acceptance.run(refresh_token)
    matches = [path for path in (ROOT/'result/acceptance').glob('installed-natural-research-*/receipt.json')
               if json.loads(path.read_text()).get('case')==CASE]
    assert len(matches)==1
    receipt = matches[0]
    record = dict(status='failed', case=CASE, candidate=actual['candidate'],
        source_receipt=str(receipt), source_receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
        full_investment_research_accepted=False, financial_prediction_accepted=False,
        natural_question_completed=False, native_window_opened=False, original_tests_modified=False,
        extraction_mode=actual.get('explicit_private_extraction_mode','default'),
        original_investment_task_reopened=False, task_retry=False)
    try:
        assert actual['status']=='collected_for_review' and actual['research_status']=='已完成'
        assert actual['credentials_removed'] and actual['production_store_unchanged'] and actual['service_stopped']
        task = json.loads((receipt.parent/'task.json').read_text())
        if actual.get('explicit_private_extraction_mode')=='direct':
            extraction_calls = [d for d in task['ledger']['dispatches'] if d.get('research_phase')=='extract']
            assert extraction_calls and all(d.get('extraction_mode')=='direct' for d in extraction_calls)
        assert task['question']==QUESTION and not task['synthetic'] and task['delivery']
        assert not task['gaps'] and not task.get('gap_records')
        assert all(q['status']=='已回答' for q in task['questions'] if q['required'])
        assert len(task['calculations'])==1
        calculation = task['calculations'][0]
        assert calculation['method']=='observed_price_return_v1'
        evidence = {item['id']:item for item in task['evidence']}
        prices = [evidence[calculation['input_bindings'][name]] for name in ('start_price','end_price')]
        assert all(p['object']=='510300.SH' and p['identity']['mode']=='direct_provider_observation'
            and p['source_receipt']['version']==3 and p['source_check']==p['applicability_check']=='通过'
            and p['published_at'] is None for p in prices)
        expected = (Decimal(str(prices[1]['value']))/Decimal(str(prices[0]['value']))-1)*100
        assert abs(Decimal(str(calculation['output']['value']))-expected)<Decimal('0.000000001')
        for p in evidence.values():
            source = next(r for r in task['source_records'] if r['id']==p['artifact']['id'])
            locator = p['record_locator']
            assert p['raw_value']==source['snapshot']['tables'][locator['table']]['table'][locator['field']][locator['row']]
        metadata_needs = sum(len(query_requirements(q)) for q in task['questions'])
        bound = [validate_query_answer(task,q) for q in task['questions'] if query_requirements(q)]
        assert metadata_needs>0 and all(item['refs'] for item in bound)
        assert all(len(item['covered_need_ids'])==len(query_requirements(q))
            for q,item in zip([q for q in task['questions'] if query_requirements(q)],bound))
        content = compose(task)
        assert not content['limitations']
        paper = (receipt.parent/'report.html').read_text()
        assert hashlib.sha256(paper.encode()).hexdigest()==actual['report_sha256']
        assert '<details' not in paper and 'echarts' in paper and "id='query-" in paper
        assert "data-inspection-title='查询留痕'" in paper
        assert '初始计算输入绑定未通过' not in paper and '必要计算尚未完成' not in paper
        rows = sorted([{'date':p['period'],'value':p['value'],'ref':p['id']} for p in evidence.values()
            if p.get('record_locator',{}).get('field')=='close'], key=lambda row:row['date'])
        record.update(status='scoped_passed', natural_question_completed=True, research_status='已完成',
            source_requirements_bound=metadata_needs, pending_source_requirement=False,
            expected_rows=rows, evidence_count=len(evidence), calculation_count=1,
            real_model_calls=task['ledger']['used']['model_calls'], real_data_requests=task['ledger']['used']['data_requests'],
            ledger=task['ledger'], report=str(receipt.parent/'report.html'),
            report_sha256=actual['report_sha256'], source_task=str(receipt.parent/'task.json'),
            production_store_unchanged=True, stale_failure_messages_present=False,
            expected_price_change_percent=str(expected), actual_price_change_percent=calculation['output']['value'],
            dates=[p['period'] for p in prices], provider_values=[p['raw_value'] for p in prices])
    except (AssertionError, KeyError, TypeError, ValueError) as error:
        record.update(failure_type=type(error).__name__, research_status=actual.get('research_status'),
            evidence_count=actual.get('evidence_count'), calculation_count=actual.get('calculation_count'))
    target = receipt.parent/'source-requirement-review.json'
    assert not target.exists()
    target.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print(json.dumps({'status':record['status'],'research_status':record.get('research_status'),
        'review':str(target),'natural_question_completed':record['natural_question_completed']},ensure_ascii=False))
    return record


if __name__=='__main__':
    run(input().strip())
