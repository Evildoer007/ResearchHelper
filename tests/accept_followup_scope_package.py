"""Actual Chat follow-up changes an old opportunity task into a direct comparison."""
from copy import deepcopy
from tests.accept_official_observation_package import CONFIGURATION,PROPOSAL
from tests.accept_report_source_labels_package import check_exports
from tests.accept_financial_subject_package import run

MESSAGE='改为比较ETF510300.SH净值与000300.SH成分权重的资料日期和口径，只核对事实，不做投资判断。'
PARENT_REPORT=dict(title='离线父研究',conclusion='这是验证旧研究保持不变的合成控制文本。',
    logics=[dict(title='原研究范围',analysis='原个股任务仅作上下文，不是下一份报告的事实依据。')])
PAPER=dict(deepcopy(PROPOSAL),title='净值与权重资料比较',report_scope='comparison')


def check_task(task,good=True):
    from decimal import Decimal
    from researchhelper.contracts import fingerprint
    from researchhelper.planning import draft_research_scope,input_fingerprint
    assert task.get('delivery'),task.get('error')
    assert task['entry']=='混合' and task['intent']=='综合研究'
    assert task['objects']==['510300.SH','000300.SH']
    assert task.get('parent') and task['conversation_context']['scope_relation']=='replace'
    assert task['input_hash']==input_fingerprint(task)
    assert draft_research_scope(task)['allowed_report_scopes']==['fact','comparison','mechanism','research']
    assert task['ledger']['used']['model_calls']==task['ledger']['used']['data_requests']==0
    report=task['draft_report'];facts=report['facts']
    assert report['organization']=='comparison'
    assert len(facts)==(301 if good else 0) and not report['calculations']
    if good:
        assert (facts[0]['code'],facts[0]['raw_value'],facts[0]['unit'])==('510300.SH','4.1856','元')
        assert sum(Decimal(str(row['raw_value'])) for row in facts[1:])==Decimal('100.001')
    for record in task['source_records']:
        assert record['task_id']==task['id'] and record['research_version']==task['version']
        assert fingerprint(record['snapshot'])==record['hash']
    return dict(entry=task['entry'],intent=task['intent'],objects=task['objects'],organization=report['organization'],
        facts=len(facts),calculations=len(report['calculations']),originals=len(task['source_records']),report_saved=True)


def check_report(text,profile,good=True):
    import hashlib,json
    from lxml import html
    tree=html.document_fromstring(text);document=json.loads(tree.get_element_by_id('rh-publication-document').text)
    rows=document['dataset']['rows'];main=tree.find('.//main').text_content()
    assert document['template']['id']=='report-'+profile and len(rows)==(301 if good else 0)
    assert '策略逻辑' not in main and '原研究范围' not in main
    charts=[block for block in document['blocks'] if block['kind']=='chart']
    if good:
        assert rows[0][-1]=='上交所ETF申赎清单'
        assert all(row[-1]=='中证指数成分权重' for row in rows[1:])
        assert len(charts)==2
        assert all(name in main for name in ('上交所ETF申赎清单','中证指数成分权重'))
    return dict(rows=len(rows),charts=len(charts),scope='comparison',
        dataset_sha256=hashlib.sha256(json.dumps(document['dataset'],ensure_ascii=False,sort_keys=True).encode()).hexdigest())


if __name__=='__main__':
    run(dict(CONFIGURATION,output_folder='followup-scope-20261104',
        task_options=dict(question='离线控制：研究600519.SH的投资机会',entry='个股',intent='发现投资机会',objects=['600519.SH'],research_mode='current'),
        followup=dict(message=MESSAGE,parent_report=PARENT_REPORT),proposal=PAPER,
        check_task=check_task,check_report=check_report,after_app=check_exports,
        browser_options=dict(CONFIGURATION['browser_options'],expected_report_scope='comparison',expected_message=MESSAGE)))
