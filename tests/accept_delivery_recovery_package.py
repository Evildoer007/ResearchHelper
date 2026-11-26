"""Retained public data recover from a controlled presentation fault, without API calls."""
from tests.accept_official_observation_package import CONFIGURATION,INPUTS,PROPOSAL
from tests.accept_report_source_labels_package import check_report,check_exports
from tests.accept_financial_subject_package import run


def seed_recovery(archive,task,good=True,*,resources):
    from copy import deepcopy
    from unittest.mock import patch
    from researchhelper.contracts import fingerprint
    from researchhelper.engine import ResearchAgent
    records=[]
    for value in INPUTS:
        value=deepcopy(value)
        records.append(dict(id='saved-'+value['operation'],kind='api',source='official',content_version=1,operation=value['operation'],
            task_id=task['id'],research_version=task['version'],status='成功待核验' if good else '返回结构异常',
            parameters=value['parameters'],snapshot=value['snapshot'],hash=fingerprint(value['snapshot']),
            request_hash=fingerprint(dict(source='official',operation=value['operation'],parameters=value['parameters'])),
            fixture_origin='retained_official_public_response'))
    task.update(synthetic=True,source_records=records);archive.save(task)
    agent=ResearchAgent(archive,resources,offline=True,require_argument_support=True,draft_first=True)
    try:
        with patch('researchhelper.reports.render',side_effect=OSError('Controlled local presentation failure')):
            failed=agent.start(task['id'],host_result=dict(data_requests=[],draft_report=PROPOSAL))
        assert failed['status']=='失败' and failed.get('pending_content') and not failed.get('pending_html')
        assert failed['ledger']['used']['model_calls']==failed['ledger']['used']['data_requests']==0
    finally:agent.close_runtime()
    return failed


def check_recovery(task,good=True):
    from decimal import Decimal
    from researchhelper.contracts import fingerprint
    assert task.get('delivery'),task.get('error')
    assert task.get('error') is None
    assert task['ledger']['used']['model_calls']==task['ledger']['used']['data_requests']==0
    assert not task.get('pending_html') and not task.get('pending_content')
    node=next(row for row in task['nodes'] if row['id']=='render')
    assert node['status']=='成功' and not node.get('error')
    facts=task['draft_report']['facts'];assert len(facts)==(301 if good else 0)
    if good:
        assert (facts[0]['code'],facts[0]['period'],facts[0]['raw_value'],facts[0]['unit'])==('510300.SH','2026-10-21','4.1856','元')
        assert sum(Decimal(str(row['raw_value'])) for row in facts[1:])==Decimal('100.001')
        assert facts[1]['raw_value']==0.476 or str(facts[1]['raw_value'])=='0.476'
        assert facts[-1]['raw_value']==0.855 or str(facts[-1]['raw_value'])=='0.855'
    for record in task['source_records']:assert fingerprint(record['snapshot'])==record['hash']
    return dict(facts=len(facts),originals=len(task['source_records']),report_saved=True,
        fatal_error_cleared=True,render_node_saved=True,model_calls=0,data_requests=0)


if __name__=='__main__':
    run(dict(CONFIGURATION,output_folder='delivery-recovery-20261105',seed=seed_recovery,
        check_task=check_recovery,check_report=check_report,after_app=check_exports,
        local_delivery_recovery=True,recovery_browser='tests/accept_delivery_recovery_browser.mjs'))
