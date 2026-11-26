"""Exact extrema through the actual DMG and independent installed Skill.

The authored proposal and input snapshots are explicitly synthetic. They prove
the saved-data/analysis/report/export path, not live research quality.
"""
from tests.accept_financial_subject_package import run as replay

VALUES=['1.000000000000000002','1.000000000000000001','1.000000000000000003']
PROPOSAL=dict(title='原值区间观察',conclusion='只描述已取得的单位净值原值范围。',
    logics=[dict(title='单位净值范围',analysis='区间依据实际取得的原值确定，净值读数不等于持有人总回报。',
        data_refs=['D1','D2','D3'],calculation_refs=['A2'])],
    risks=['这是合成边界输入，不是实际基金行情或投资建议。'])


def seed(archive,task,good=True):
    from researchhelper.contracts import fingerprint
    records=[]
    for index,value in enumerate(VALUES if good else [None,None,None],1):
        parameters=dict(ts_code='510300.SH')
        snapshot=dict(code=0,data=[dict(ts_code='510300.SH',trade_date='2026-10-'+str(19+index),unit_nav=value)])
        records.append(dict(id='synthetic-range-'+str(index),kind='api',source='official',operation='etf_pcf',
            task_id=task['id'],research_version=task['version'],status='成功待核验',
            parameters=parameters,snapshot=snapshot,hash=fingerprint(snapshot),
            request_hash=fingerprint(dict(source='official',operation='etf_pcf',parameters=parameters)),
            fixture_origin='synthetic_decimal_boundary'))
    task.update(synthetic=True,source_records=records)
    return archive.save(task)


def check_task(task,good=True):
    from researchhelper.contracts import fingerprint
    assert task.get('delivery'),task.get('error')
    assert task['status']=='部分完成' and task['delivery']['kind']=='research_draft'
    assert task['ledger']['used']['model_calls']==task['ledger']['used']['data_requests']==0
    facts=task['draft_report']['facts'];calculations=task['draft_report']['calculations']
    assert len(facts)==3
    if good:
        assert [row['raw_value'] for row in facts]==VALUES and {row['value'] for row in facts}=={1.0}
        bounds=next(row for row in calculations if row['method']=='raw_range_v1')
        assert (bounds['minimum'],bounds['maximum'])==(VALUES[1],VALUES[2])
        assert bounds['data_refs']==['D2','D3']
        assert task['draft_report']['logics'][0]['calculation_refs']==[bounds['id']]
    else:
        assert all(row['raw_value'] is None for row in facts) and not calculations
    for record in task['source_records']:assert fingerprint(record['snapshot'])==record['hash']
    return dict(facts=len(facts),calculations=len(calculations),missing_data_delivered=not good,
        correct_exact_range=good,model_calls=0,data_requests=0)


def check_report(text,profile,good=True):
    import hashlib,json,tempfile
    from pathlib import Path
    from lxml import html
    from io import BytesIO
    from openpyxl import load_workbook
    from researchhelper.artifact_exporter import convert_saved_html
    tree=html.document_fromstring(text);document=json.loads(tree.get_element_by_id('rh-publication-document').text)
    assert document['template']['id']=='report-'+profile
    assert len(document['dataset']['rows'])==3
    ranges=[row for row in document['dataset']['calculations'] if row['method']=='raw_range_v1']
    if good:
        assert len(ranges)==1 and (ranges[0]['minimum'],ranges[0]['maximum'])==(VALUES[1],VALUES[2])
        assert [row[3] for row in document['dataset']['rows']]==VALUES
    else:
        assert not document['dataset']['calculations'] and all(row[3] is None for row in document['dataset']['rows'])
    with tempfile.TemporaryDirectory(prefix='rh-range-convert-') as folder:
        path=Path(folder)/'report.html';path.write_text(text)
        workbook_bytes,converted=convert_saved_html(path,'xlsx',expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        assert converted['dataset']==document['dataset']
        book=load_workbook(BytesIO(workbook_bytes))
        try:
            if good:
                sheet=book['计算依据'];row=next(row for row in sheet.iter_rows(min_row=2) if row[1].value=='raw_range_v1')
                assert (row[4].value,row[5].value)==(VALUES[1],VALUES[2])
                assert row[4].data_type==row[5].data_type=='s'
            else:assert '计算依据' not in book.sheetnames
        finally:book.close()
    assert not tree.xpath('//main//details')
    return dict(rows=3,ranges=len(ranges),xlsx_from_same_html=True,
        dataset_sha256=hashlib.sha256(json.dumps(document['dataset'],sort_keys=True).encode()).hexdigest())


def check_exports(client,task_id,output):
    import base64,csv,hashlib,io,json
    from openpyxl import load_workbook
    from lxml import html
    from pypdf import PdfReader
    from docx import Document
    before=client.call('/api/tasks/'+task_id);results={}
    for profile in ('standard','detailed'):
        results[profile]={}
        for format in ('html','xlsx','csv','pdf','docx'):
            receipt=client.call('/api/export',dict(task_id=task_id,version=before['version'],profile=profile,format=format))
            raw=base64.b64decode(receipt['content_base64'],validate=True)
            assert hashlib.sha256(raw).hexdigest()==receipt['sha256']
            if format=='html':
                document=json.loads(html.document_fromstring(raw).get_element_by_id('rh-publication-document').text)
                bounds=next(row for row in document['dataset']['calculations'] if row['method']=='raw_range_v1')
                assert (bounds['minimum'],bounds['maximum'])==(VALUES[1],VALUES[2])
            elif format=='xlsx':
                book=load_workbook(io.BytesIO(raw));sheet=book['计算依据']
                row=next(row for row in sheet.iter_rows(min_row=2) if row[1].value=='raw_range_v1')
                assert (row[4].value,row[5].value)==(VALUES[1],VALUES[2]);book.close()
            elif format=='csv':
                rows=list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))));assert [row[3] for row in rows[1:]]==VALUES
            elif format=='pdf':
                paper=PdfReader(io.BytesIO(raw));assert paper.pages
                assert all(abs(float(page.mediabox.width)-595.28)<2 for page in paper.pages)
            else:assert Document(io.BytesIO(raw)).paragraphs
            (output/('range-'+profile+'.'+format)).write_bytes(raw)
            results[profile][format]=dict(status='passed',sha256=receipt['sha256'],bytes=len(raw))
    assert client.call('/api/tasks/'+task_id)==before
    return results


def run():
    return replay(dict(output_folder='exact-range-selection-20261111',
        task_options=dict(question='合成验收：核对510300.SH单位净值原值的区间及来源',entry='ETF',objects=['510300.SH'],research_mode='current'),
        proposal=PROPOSAL,seed=seed,check_task=check_task,check_report=check_report,
        browser_script='tests/accept_exact_range_browser.mjs',after_app=check_exports,
        worker_globals=dict(VALUES=VALUES)))


if __name__=='__main__':run()
