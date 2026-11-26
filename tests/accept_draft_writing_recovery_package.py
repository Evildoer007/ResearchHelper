"""The actual offline App and Skill finish a completed, simulated model reply."""
from tests.accept_official_observation_package import CONFIGURATION,INPUTS,PROPOSAL
from tests.accept_report_source_labels_package import check_report,check_exports
from tests.accept_financial_subject_package import run


def seed_writing(archive,task,good=True,*,resources):
    from copy import deepcopy
    from types import SimpleNamespace
    from unittest.mock import Mock,patch
    from researchhelper.contracts import ContractError,canonical,charge,fingerprint
    from researchhelper.engine import ResearchAgent
    from researchhelper.draft_workflow import retained_submission
    records=[]
    for value in INPUTS:
        value=deepcopy(value)
        records.append(dict(id='saved-'+value['operation'],kind='api',source='official',content_version=1,operation=value['operation'],
            task_id=task['id'],research_version=task['version'],status='成功待核验' if good else '返回结构异常',
            parameters=value['parameters'],snapshot=value['snapshot'],hash=fingerprint(value['snapshot']),
            request_hash=fingerprint(dict(source='official',operation=value['operation'],parameters=value['parameters'])),
            fixture_origin='retained_official_public_response'))
    task.update(synthetic=True,source_records=records);archive.save(task)
    phases=[]
    def reply(current,messages,**options):
        phases.append(options['phase']);charge(current['ledger'],'model_calls',1,delivery=options.get('delivery',False))
        return canonical(dict(data_requests=[])) if options['phase']=='draft_plan' else canonical(PROPOSAL)
    agent=ResearchAgent(archive,resources,offline=False,model=SimpleNamespace(reason=reply),
        source_credentials={},require_argument_support=True,draft_first=True)
    agent.collector.external=Mock(side_effect=ContractError('Retained replay never acquires new data'))
    try:
        with patch('researchhelper.draft_workflow.normalize_report',side_effect=OSError('Controlled local composition failure')):
            failed=agent.start(task['id'])
        assert phases==['draft_plan','draft_report']
        assert failed['status']=='失败' and not failed.get('pending_content') and not failed.get('pending_html')
        assert retained_submission(failed) is not None and failed['ledger']['used']['model_calls']==2
    finally:agent.close_runtime()
    return failed


def check_writing(task,good=True):
    from decimal import Decimal
    from researchhelper.contracts import fingerprint
    assert task.get('delivery') and task.get('error') is None
    assert task['ledger']['used']['model_calls']==2 and task['ledger']['used']['data_requests']==0
    assert task['draft_report']['origin']=='model' and task['draft_report']['conclusion']==PROPOSAL['conclusion']
    assert len(task['draft_submissions'])==1 and task['draft_submissions'][0]['version']==2
    assert sum(row['action']=='复用已保存原文' for row in task['revisions'])==1
    assert not task.get('pending_content') and not task.get('pending_html')
    assert all(row['status']=='成功' and not row.get('error') for row in task['nodes'] if row['id'] in ('compose','render'))
    facts=task['draft_report']['facts'];assert len(facts)==(301 if good else 0)
    if good:
        assert (facts[0]['code'],facts[0]['period'],facts[0]['raw_value'],facts[0]['unit'])==('510300.SH','2026-10-21','4.1856','元')
        assert sum(Decimal(str(row['raw_value'])) for row in facts[1:])==Decimal('100.001')
        assert str(facts[1]['raw_value'])=='0.476' and str(facts[-1]['raw_value'])=='0.855'
    for record in task['source_records']:assert fingerprint(record['snapshot'])==record['hash']
    return dict(facts=len(facts),originals=len(task['source_records']),report_saved=True,original_prose_preserved=True,
        completed_reply_records=1,simulated_model_budget=2,new_model_calls=0,data_requests=0)


if __name__=='__main__':
    run(dict(CONFIGURATION,output_folder='draft-writing-recovery-20261105',seed=seed_writing,
        check_task=check_writing,check_report=check_report,after_app=check_exports,
        local_delivery_recovery=True,recovery_browser='tests/accept_delivery_recovery_browser.mjs',
        recovery_browser_options=dict(expected_model_budget=2)))
