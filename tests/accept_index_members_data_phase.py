"""Fresh name-only index acquisition, never supply a plan or a member code.

This deliberately stops before analysis. A returned member list is only a
query scope; it is not verified membership completeness, weights or returns.
"""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from tests import accept_autonomous_data_phase as driver
from tests.audit_data_acquisition_layers import inspect_source
from tests.probe_data_acquisition_current import table_inventory
from researchhelper.evidence import current_query_reference
from researchhelper.index_sources import index_response

CASE = 'autonomous-index-members-data-20261018-v1'
QUESTION = ('查询上证50在2026年9月30日的成分名单，再选择一家实际成分股获取当天收盘价，'
            '只核对数据来源、所属日期和实际取数，不做买卖建议。')


def acquisition_inventory(task):
    """Check actual code/date/value/dispatch linkage, never infer approval."""
    pools = []
    quote_sources = []
    source_checks = []
    for source in task.get('source_records', []):
        source_checks.append(inspect_source(source))
        if source['source'] == 'ifind' and source['operation'] == 'data_pool':
            # Rebuild the contract from actual response bytes, not just the
            # model answer or a detached index_contract field.
            rebuilt = index_response(task, source['parameters'], source['snapshot'])
            pools.append(dict(source_id=source['id'],
                real_query=current_query_reference(task, source) is not None,
                index_code=rebuilt['index_code'], observation_date=rebuilt['observation_date'],
                rows=rebuilt['observed_rows'], raw_members=rebuilt['members'],
                complete_set_verified=rebuilt['complete_set_verified'],
                weights_available=rebuilt['weights_available']))
        elif source['source'] == 'ifind' and source['operation'] == 'cmd_history_quotation':
            tables = table_inventory(source)
            params = source['parameters']
            pool_sources = {row['id']: row for row in task['source_records']}
            matching = [pool for pool in pools
                if pool['real_query'] and current_query_reference(task, source) is not None
                and any(member['code'] == params.get('codes') for member in pool['raw_members'])
                and params.get('startdate') == params.get('enddate') == pool['observation_date']
                and params.get('indicators') == 'close'
                and pool_sources[pool['source_id']]['acquisition']['dispatch_index']
                    < source['acquisition']['dispatch_index']
                and tables == [dict(code=params['codes'], dates=[pool['observation_date']],
                    columns=[dict(field='close', count=1, numeric_count=1, missing_count=0, aligned=True)])]]
            quote_sources.append(dict(source_id=source['id'],
                parameters=params, tables=tables,
                matching_pool_ids=[pool['source_id'] for pool in matching],
                real_query=current_query_reference(task, source) is not None))
    return dict(pools=pools, quote_sources=quote_sources, source_checks=source_checks,
        autonomous_member_followup_observed=any(row['matching_pool_ids'] for row in quote_sources))


def run():
    with patch.object(driver, 'CASE', CASE), patch.object(driver, 'QUESTION', QUESTION):
        receipt = driver.run()
    task_path = Path(receipt).parent/'task.json'
    protected = hashlib.sha256(task_path.read_bytes()).hexdigest()
    task = json.loads(task_path.read_text())
    inventory = acquisition_inventory(task)
    result = dict(status='index_data_review_not_research_approval', acquisition_receipt=str(receipt),
        task_status=task['status'], **inventory, study_resumed=False, supplied_codes=False,
        supplied_plan=False, supplied_member_selection=False,
        additional_model_calls=0, additional_data_calls=0,
        complete_membership_accepted=False, financial_calculation_approved=False,
        strategy_accepted=False, customer_report_accepted=False, installed_app_accepted=False,
        retained_task_unchanged=hashlib.sha256(task_path.read_bytes()).hexdigest()==protected)
    target = Path(receipt).parent/'index-data-review.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert result['retained_task_unchanged']
    print(json.dumps(dict(receipt=str(target), pools=len(inventory['pools']), quotes=len(inventory['quote_sources']),
        task_status=task['status']), ensure_ascii=False), flush=True)
    return target
