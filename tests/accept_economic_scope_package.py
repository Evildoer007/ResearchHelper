"""Replay retained CPI observations plus synthetic anomalies in App and Skill.

Real source files stay unchanged. The test never fetches data, invokes a model,
or treats current-vintage values as historical publication or causal proof.
"""
from copy import deepcopy
import hashlib
import json

from lxml import html
from tests import ROOT
from tests.accept_financial_subject_package import run as run_replay

SOURCE=ROOT/'result/acceptance/economic-discovery-live-20261023-v1/task.json'
original=json.loads(SOURCE.read_text())
INPUTS={row['operation']:dict(parameters=row['parameters'],snapshot=row['snapshot'])
    for row in original['source_records'] if row['operation'] in ('search_edb','edb_service')}
PROPOSAL=dict(title='CPI读数回放验收',conclusion='比较可用CPI读数的期间变化，不以同期变动推断贡献或因果。',
    logics=[dict(title='期间读数变化',analysis='比较期初与期末的实际统计读数。',data_refs=['D9','D1'],calculation_refs=['A1']),
            dict(title='最近两期变化',analysis='区分最近两期与完整观察区间。',data_refs=['D2','D1'],calculation_refs=['A2']),
            dict(title='完整观察路径',analysis='保留各期实际正负读数，不以端点代替期间路径。',data_refs=['D1','D2','D3','D4','D5','D6','D7','D8','D9'])],
    risks=['统计所属期不等于首发时间，当前版本不证明历史当时已知信息。'])


def seed(archive,task,good=True):
    from copy import deepcopy
    from researchhelper.contracts import fingerprint
    records=[]
    for operation in ('edb_service','search_edb'):
        original=INPUTS[operation];response=deepcopy(original['snapshot'])
        if operation=='edb_service':
            table=response['tables'][0]
            if good:
                for field,values in table.items():
                    if isinstance(values,list) and field!='id':
                        values.append('2026-12-31' if field=='time' else 987654321 if field=='value' else values[-1])
            else:
                table.update(time=['2026-12-31'],value=[987654321],index_name=['CPI:当月同比'],rtime=['2026-10-14 09:31:18'])
            response['tables'].append(dict(id=['M999999999'],time=['2026-09-30'],value=[987654321]))
        parameters=deepcopy(original['parameters'])
        records.append(dict(id='saved-'+operation,kind='api',source='ifind',operation=operation,
            task_id=task['id'],research_version=task['version'],status='成功待核验',
            parameters=parameters,snapshot=response,hash=fingerprint(response),
            request_hash=fingerprint(dict(source='ifind',operation=operation,parameters=parameters)),
            fixture_origin='retained_public_cpi_plus_synthetic_exclusions'))
    task.update(synthetic=True,source_records=records)
    return archive.save(task)


def check_task(task,good=True):
    from researchhelper.contracts import fingerprint
    from researchhelper.draft_sources import VERSION,source_context,writing_context
    assert task.get('delivery'),task.get('error')
    assert task['draft_source_reader_version']==task['draft_report']['source_reader_version']==VERSION
    assert task['ledger']['used']['model_calls']==task['ledger']['used']['data_requests']==0
    facts=task['draft_report']['facts'];calculations=task['draft_report']['calculations']
    assert len(facts)==(9 if good else 0)
    assert {row['code'] for row in facts}==({'M002826730'} if good else set())
    assert [row['display'] for row in calculations]==(['0.70','0.10'] if good else [])
    assert all(row['period']<='2026-09-30' for row in facts)
    assert len(task['draft_source_issues'])==1 and task['draft_source_issues'][0]['partial']
    assert '987654321' in json.dumps(task['source_records'])
    for record in task['source_records']:assert fingerprint(record['snapshot'])==record['hash']
    for context in (source_context(task),writing_context(task,facts)):
        assert '987654321' not in json.dumps(context)
    return dict(facts=len(facts),calculations=len(calculations),originals=len(task['source_records']),report_saved=True)


def check_report(text,profile,good=True):
    from lxml import html
    tree=html.document_fromstring(text)
    document=json.loads(tree.get_element_by_id('rh-publication-document').text)
    assert document['template']['id']=='report-'+profile
    assert len(document['dataset']['rows'])==(9 if good else 0)
    assert '987654321' not in json.dumps(document['dataset'])
    assert '987654321' not in tree.xpath('//main')[0].text_content()
    charts=[block for block in document['blocks'] if block['kind']=='chart']
    if good:assert len(charts)>=3
    return dict(rows=len(document['dataset']['rows']),charts=len(charts),
        dataset_sha256=hashlib.sha256(json.dumps(document['dataset'],ensure_ascii=False,sort_keys=True).encode()).hexdigest())


CONFIGURATION=dict(output_folder='economic-window-scope-20261104',
    task_options=dict(question='回放验收：比较中国CPI统计读数',entry='宏观',objects=['M002826730'],
        research_mode='current'),
    proposal=PROPOSAL,seed=seed,check_task=check_task,check_report=check_report,
    worker_globals=dict(INPUTS=INPUTS),browser_options=dict(linked_counts=[9],original_row=10))


if __name__=='__main__':
    before=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    try:run_replay(CONFIGURATION)
    finally:assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()==before
