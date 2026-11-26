"""Retained iFinD inputs through an updated report in the actual App and isolated Skill."""
from copy import deepcopy
import gzip,json
from tests import ROOT
from tests.accept_financial_subject_package import run
from tests.market_observation_fixtures import FIXED_FINANCIAL_RESPONSES, read_captures

_public=read_captures(FIXED_FINANCIAL_RESPONSES.read_bytes(), grouped=True)
with gzip.open(ROOT/'result/acceptance/source-matrix-20261031/followup/sources.json.gz','rt') as stream:_hk=json.load(stream)
if isinstance(_hk,dict):_hk=_hk['source_records']
INPUTS=[{key:deepcopy(row[key]) for key in ('operation','parameters','snapshot')} for row in _public.values()]
INPUTS += [{key:deepcopy(row[key]) for key in ('operation','parameters','snapshot')} for row in _hk if row['operation'] in ('get_thscode','cmd_history_quotation')]
PROPOSAL=dict(title='经营表现与港股行情观察',
    conclusion='经营指标与价格窗口分别回答企业变化和市场表现，不能直接把其中一项等同于投资价值。',
    logics=[dict(title='经营比较',analysis='按两期收入、成本与利润分别观察变化，保留同口径比较条件。',data_refs=['D1','D2','D3','D4','D5','D6','D7','D8']),
        dict(title='现金兑现',analysis='经营现金流与利润分开观察；两期数据不足以证明未来持续性。',data_refs=['D3','D4','D7','D8']),
        dict(title='价格窗口',analysis='证券行情按实际交易日和登记币种展示，不能把两日价格变化归因为经营变化。',data_refs=['D9','D10','D11','D12','D13','D14','D15','D16'])],
    risks=['各项数据对应其原始观测期间；这些资料不能单独证明未来表现或因果关系。'])


def seed_update(archive,task,good=True,*,resources):
    from collections import Counter
    from copy import deepcopy
    import hashlib
    from types import SimpleNamespace
    from unittest.mock import patch
    from researchhelper.contracts import canonical,charge,fingerprint,now
    from researchhelper.engine import ResearchAgent
    from researchhelper.evidence import snapshot_record,_record_current_acquisition,_controlled_financial_definitions,identity_resolutions
    from researchhelper.draft_workflow import retained_submission
    calls=[]
    def period(params):return params.get('report_period') or next((str(row['indiparams'][0]) for row in params.get('indipara',[]) if row.get('indiparams')),'')
    def source(task,provider,operation,params,credentials):
        assert provider=='ifind' and not credentials
        original=next(row for row in INPUTS if row['operation']==operation and (operation!='basic_data_service' or period(row['parameters'])==period(params)))
        payload=deepcopy(original['snapshot'])
        if not good and task['version']==2 and operation in ('basic_data_service','cmd_history_quotation'):payload['errorcode']=-1
        charge(task['ledger'],'data_requests',1);record=snapshot_record(task,provider,operation,params,payload)
        event=dict(kind='data_requests',source=provider,operation=operation,batch=task['batch'],fingerprint=record['request_hash'],status='成功' if record['status']=='成功待核验' else '失败',time=now(),transport_isolated=True,network_request_started=True,network_worker_reaped=True,synthetic_test_transport=True)
        task['ledger']['dispatches'].append(event)
        if operation=='basic_data_service':record.update(fixed_financial_catalog_version=1,fixed_financial_catalog_digest=fingerprint(_controlled_financial_definitions()))
        _record_current_acquisition(task,record,event);calls.append(dict(version=task['version'],operation=operation));return record
    def reply(task,messages,*,phase,delivery=False,**options):
        charge(task['ledger'],'model_calls',1,delivery=delivery)
        if phase=='draft_plan':
            requests=[dict(id='query_'+str(i),source='ifind',operation=row['operation'],parameters=deepcopy(row['parameters']),question_ids=[task['questions'][0]['id']],selection_reason='使用登记API核对问题要求的财务与行情。') for i,row in enumerate(INPUTS) if row['operation'] in ('basic_data_service','cmd_history_quotation')]
            return canonical(dict(data_requests=requests))
        return canonical(PROPOSAL)
    task['synthetic']=True;archive.save(task)
    agent=ResearchAgent(archive,resources,offline=False,model=SimpleNamespace(reason=reply),source_credentials={},require_argument_support=True,draft_first=True)
    agent.collector.external=source
    try:
        first=agent.start(task['id']);assert first.get('delivery'),first.get('error')
        prior=archive.report(task['id'],1);frozen=deepcopy(prior)
        with patch('researchhelper.draft_workflow.normalize_report',side_effect=OSError('Controlled active update composition failure')):
            failed=agent.update(task['id'])
        assert failed['version']==2 and failed['status']=='失败' and retained_submission(failed)
        assert archive.report(task['id'],1)==frozen
        operations=[row['operation'] for row in calls if row['version']==2]
        assert Counter(operations)==Counter(['get_limit_policy','get_thscode','basic_data_service','basic_data_service','cmd_history_quotation'])
        assert operations.index('get_limit_policy')<operations.index('basic_data_service')
        assert operations.index('get_thscode')<operations.index('cmd_history_quotation')
        failed['qa_update_baseline']=dict(version=1,reader_version=failed['draft_source_reader_version'],html_sha256=hashlib.sha256(prior['html'].encode()).hexdigest(),source_hashes={row['id']:row['hash'] for row in first['source_records']},calls=calls)
        archive.save(failed)
        return failed
    finally:agent.close_runtime()


def check_update(task,good=True):
    from researchhelper.evidence import current_query_reference,identity_resolutions
    assert task.get('delivery') and task.get('error') is None and task['version']==2
    expected_reader=task['qa_update_baseline'].get('reader_version',12)
    assert task['draft_source_reader_version']==expected_reader and task['draft_report']['conclusion']==PROPOSAL['conclusion']
    assert task['ledger']['used']['model_calls']==2 and task['ledger']['used']['data_requests']==5
    facts=task['draft_report']['facts'];assert len(facts)==16
    by_id={row['id']:row for row in facts}
    cash_logic=next(row for row in task['draft_report']['logics'] if row['title']=='现金兑现')
    cash_facts=[by_id[ref] for ref in cash_logic['data_refs']]
    assert {row['field'] for row in cash_facts}=={'ths_np_stock','ths_ncf_from_oa_stock'}
    assert {row['code'] for row in cash_facts}=={'002594.SZ'}
    assert {row['period'] for row in cash_facts}=={'20260630','20250630'}
    current={row['id'] for row in task['source_records'] if current_query_reference(task,row)}
    assert all((row['artifact_id'] in current)==good for row in facts)
    baseline=task['qa_update_baseline']
    assert len(task['source_records'])==10
    for row in task['source_records']:
        if row['id'] in baseline['source_hashes']:assert row['hash']==baseline['source_hashes'][row['id']] and row['retained_from_version']==1
    mapped=[row for row in identity_resolutions(task) if row['status']=='代码映射已核验']
    assert len(mapped)==1 and mapped[0]['codes']==['0700.HK'] and mapped[0]['binding']['research_version']==2
    assert len(task['draft_submissions'])==1 and sum(row['action']=='复用已保存原文' for row in task['revisions'])==1
    return dict(version=2,reader_version=expected_reader,facts=16,originals=10,old_originals_preserved=5,
        new_query_state_used=good,mapping_refreshed=True,simulated_model_budget=2,simulated_data_budget=5,new_model_calls=0,real_data_requests=0)


def check_report(text,profile,good=True):
    import hashlib,json
    from lxml import html
    tree=html.document_fromstring(text);document=json.loads(tree.get_element_by_id('rh-publication-document').text)
    assert document['template']['id']=='report-'+profile and len(document['dataset']['rows'])==16
    assert len(tree.xpath('//main//*[@id="logic-1" or @id="logic-2" or @id="logic-3"]'))==3
    assert 'Controlled active update composition failure' not in tree.xpath('//main')[0].text_content()
    charts=[block for block in document['blocks'] if block['kind']=='chart'];assert charts
    return dict(rows=16,charts=len(charts),dataset_sha256=hashlib.sha256(json.dumps(document['dataset'],ensure_ascii=False,sort_keys=True).encode()).hexdigest())


def check_exports(client,task_id,output):
    import base64,csv,hashlib,io,zipfile
    from lxml import html
    from openpyxl import load_workbook
    before=client.call('/api/tasks/'+task_id);result={}
    for profile in ('standard','detailed'):
        text=client.call('/api/tasks/'+task_id+'/report?profile='+profile,text=True)
        tree=html.document_fromstring(text);document=json.loads(tree.get_element_by_id('rh-publication-document').text)
        for format in ('csv','xlsx','docx'):
            exported=client.call('/api/export',dict(task_id=task_id,profile=profile,format=format))
            raw=base64.b64decode(exported['content_base64'],validate=True);assert hashlib.sha256(raw).hexdigest()==exported['sha256']
            if format=='csv':
                rows=list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))));assert len(rows)==17
                assert rows[1:]==[[str(value) if value is not None else '' for value in row] for row in document['dataset']['rows']]
            elif format=='xlsx':
                book=load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
                try:assert len(list(book['研究数据'].iter_rows(values_only=True)))==17
                finally:book.close()
            else:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:body=archive.read('word/document.xml').decode()
                assert '经营比较' in body and '价格窗口' in body
            result[profile+'_'+format]=dict(status='passed',rows=16 if format!='docx' else None,sha256=exported['sha256'])
    after=client.call('/api/tasks/'+task_id)
    assert before['ledger']['used']==after['ledger']['used'] and before['delivery']==after['delivery']
    return result


CONFIGURATION=dict(output_folder='research-update-refresh-20261105',
    task_options=dict(question='核对比亚迪002594.SZ的2026年与2025年上半年经营指标，以及腾讯00700.HK在2026年10月29日至30日的行情，分开形成研究逻辑。',entry='混合',objects=['002594.SZ','00700.HK'],research_mode='current',limits=dict(model_calls=2,data_requests=100,seconds=600)),
    proposal=PROPOSAL,seed=seed_update,check_task=check_update,check_report=check_report,worker_globals=dict(INPUTS=INPUTS),
    local_delivery_recovery=True,recovery_browser='tests/accept_delivery_recovery_browser.mjs',
    recovery_browser_options=dict(expected_model_budget=2,expected_data_budget=5,expected_fact_count=16),
    browser_script='tests/accept_active_update_browser.mjs',after_app=check_exports)
if __name__=='__main__':run(CONFIGURATION)
