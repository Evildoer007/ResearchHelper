"""The App and host Skill present retained RMB prices without revaluing data."""
from tests.accept_active_update_package import INPUTS,PROPOSAL
from tests.accept_financial_subject_package import run


def seed_currency(archive,task,good=True):
    from copy import deepcopy
    from researchhelper.contracts import fingerprint
    from researchhelper.planning import input_fingerprint
    task.update(synthetic=True,draft_source_reader_version=13 if good else 12)
    task['input_hash']=input_fingerprint(task);records=[]
    for index,row in enumerate(row for row in INPUTS if row['operation'] in ('basic_data_service','cmd_history_quotation')):
        params=deepcopy(row['parameters']);snapshot=deepcopy(row['snapshot'])
        records.append(dict(id='retained-'+str(index),kind='api',source='ifind',content_version=1,
            task_id=task['id'],research_version=task['version'],operation=row['operation'],parameters=params,
            snapshot=snapshot,hash=fingerprint(snapshot),request_hash=fingerprint(dict(source='ifind',operation=row['operation'],parameters=params)),
            status='成功待核验',fixture_origin='retained_public_response'))
    task['source_records']=records;return archive.save(task)


def check_currency(task,good=True):
    from researchhelper.contracts import fingerprint
    assert task.get('delivery') and task.get('error') is None
    assert task['draft_source_reader_version']==(13 if good else 12)
    assert task['ledger']['used']['model_calls']==task['ledger']['used']['data_requests']==0
    facts=task['draft_report']['facts'];assert len(facts)==16
    prices=[row for row in facts if row['field'] in ('open','high','low','close')]
    assert len(prices)==8 and all(row['unit']=='元' and (row.get('currency')=='RMB')==good for row in prices)
    assert len(task['source_records'])==3 and all(fingerprint(row['snapshot'])==row['hash'] for row in task['source_records'])
    return dict(facts=16,originals=3,raw_units_unchanged=True,reader_version=task['draft_source_reader_version'],qualified_currency_rows=8 if good else 0,model_calls=0,data_requests=0)


def check_report(text,profile,good=True):
    import hashlib,json
    from lxml import html
    tree=html.document_fromstring(text);document=json.loads(tree.get_element_by_id('rh-publication-document').text)
    assert document['template']['id']=='report-'+profile and len(document['dataset']['rows'])==16
    assert sum(row[4]=='人民币元' for row in document['dataset']['rows'])==(8 if good else 0)
    charts=[block for block in document['blocks'] if block['kind']=='chart'];assert charts
    qualified=[block for block in charts if block['spec'].get('unit')=='人民币元']
    assert bool(qualified)==good
    assert all(block['spec']['auto_visual']['data']['unit']=='人民币元' for block in qualified)
    return dict(rows=16,charts=len(charts),qualified_charts=len(qualified),dataset_sha256=hashlib.sha256(json.dumps(document['dataset'],ensure_ascii=False,sort_keys=True).encode()).hexdigest())


def check_exports(client,task_id,output):
    import base64,csv,hashlib,io,json,zipfile
    from lxml import html
    from openpyxl import load_workbook
    from pypdf import PdfReader
    from tests.accept_native_hidden_print import pdf_text_key
    before=client.call('/api/tasks/'+task_id);result={}
    for profile in ('standard','detailed'):
        tree=html.document_fromstring(client.call('/api/tasks/'+task_id+'/report?profile='+profile,text=True))
        data=json.loads(tree.get_element_by_id('rh-publication-document').text)['dataset']
        for format in ('csv','xlsx','docx','pdf'):
            exported=client.call('/api/export',dict(task_id=task_id,profile=profile,format=format))
            raw=base64.b64decode(exported['content_base64'],validate=True);assert hashlib.sha256(raw).hexdigest()==exported['sha256']
            item=dict(status='passed',sha256=exported['sha256'])
            if format=='csv':
                rows=list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))));assert len(rows)==17
                assert rows[1:]==[[str(value) if value is not None else '' for value in row] for row in data['rows']];item['rows']=16
            elif format=='xlsx':
                book=load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
                try:rows=list(book['研究数据'].iter_rows(values_only=True));assert len(rows)==17 and sum(row[4]=='人民币元' for row in rows[1:])==8
                finally:book.close()
                item['rows']=16
            elif format=='docx':
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:body=archive.read('word/document.xml').decode()
                assert '价格窗口' in body and '经营比较' in body
                if profile=='detailed':assert '人民币元' in body
            else:
                (output/('native-'+profile+'.pdf')).write_bytes(raw);pages=PdfReader(io.BytesIO(raw)).pages
                assert 1<=len(pages)<=(3 if profile=='standard' else 8)
                assert all(abs(float(page.mediabox.width)-595.28)<2 and abs(float(page.mediabox.height)-841.89)<2 for page in pages)
                text=pdf_text_key(''.join(page.extract_text() or '' for page in pages));assert '人民币元' in text
                item.update(pages=len(pages),a4=True,currency_label_visible=True)
            result[profile+'_'+format]=item
    after=client.call('/api/tasks/'+task_id);assert after['ledger']['used']==before['ledger']['used'] and after['delivery']==before['delivery']
    return result


CONFIGURATION=dict(output_folder='currency-display-20261105',
    task_options=dict(question='核对比亚迪002594.SZ两期经营数据与腾讯0700.HK两日人民币行情，分别解释经营与价格变化。',entry='混合',objects=['002594.SZ','0700.HK'],research_mode='current'),
    proposal=PROPOSAL,seed=seed_currency,check_task=check_currency,check_report=check_report,
    worker_globals=dict(INPUTS=INPUTS),browser_script='tests/accept_currency_display_browser.mjs',after_app=check_exports)
if __name__=='__main__':run(CONFIGURATION)
