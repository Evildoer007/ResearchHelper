"""The actual App and independent Skill retain complete, compact workpapers."""
from pathlib import Path
import json
from tests.accept_official_observation_package import CONFIGURATION,INPUTS,review_native_pdf
from tests.accept_financial_subject_package import run


def check_report(text,profile,good=True):
    import hashlib,json
    from lxml import html
    tree=html.document_fromstring(text);document=json.loads(tree.get_element_by_id('rh-publication-document').text)
    assert document['template']['id']=='report-'+profile
    assert len(document['dataset']['rows'])==(301 if good else 0)
    charts=[block for block in document['blocks'] if block['kind']=='chart']
    if good:
        assert len(charts)==2
        if profile=='detailed':
            rows=tree.xpath('//main//*[@id="data"]//tr[@data-observation-id]')
            assert len(rows)==301
            assert len(tree.xpath('//main//tbody/tr'))<340
            assert tree.xpath('//main//*[@id="logic-2"]//a[@href="#data"]')
            assert tree.xpath('//main//*[@id="data"]//tr[@data-observation-id="D2"]/td')[4].text_content()=='0.476'
        else:assert len(tree.xpath('//main//tbody/tr'))<60
    return dict(rows=len(document['dataset']['rows']),charts=len(charts),
        dataset_sha256=hashlib.sha256(json.dumps(document['dataset'],ensure_ascii=False,sort_keys=True).encode()).hexdigest())


def check_native_exports(client,task_id,output):
    import base64,hashlib,io,re
    from pypdf import PdfReader
    from tests.accept_native_hidden_print import pdf_text_key
    before=client.call('/api/tasks/'+task_id)
    result={}
    expected=[dict(code='510300.SH',name='',period='2026-10-21',value='4.1856',unit='元')]+[
        dict(code=row['code'],name=row['name'],period=row['observation_date'],value=str(row['weight']),unit='%')
        for row in INPUTS[1]['snapshot']['parsed']['members']]
    for profile in ('standard','detailed'):
        export=client.call('/api/export',dict(task_id=task_id,profile=profile,format='pdf'))
        raw=base64.b64decode(export['content_base64'],validate=True)
        assert hashlib.sha256(raw).hexdigest()==export['sha256']
        (output/('native-'+profile+'.pdf')).write_bytes(raw)
        pages=PdfReader(io.BytesIO(raw)).pages
        for page in pages:
            assert abs(float(page.mediabox.width)-595.28)<2 and abs(float(page.mediabox.height)-841.89)<2
        if profile=='standard':result[profile]=review_native_pdf(raw)
        else:
            assert len(pages)<=12
            text=pdf_text_key(''.join(page.extract_text() or '' for page in pages))
            assert '关键数据' in text
            raw_table=text.split('关键数据',1)[1]
            matched=[]
            for row in expected:
                pattern=re.escape(row['code'])+'.{0,100}?'+re.escape(row['period']+row['value']+row['unit'])
                if re.search(pattern,raw_table):matched.append(row['code'])
            assert len(matched)==301,(len(matched),[row['code'] for row in expected if row['code'] not in matched][:12])
            result[profile]=dict(status='passed',pages=len(pages),a4=True,literal_raw_rows_matched=len(matched),sha256=export['sha256'])
    current=client.call('/api/tasks/'+task_id)
    assert current['ledger']['used']==before['ledger']['used']
    assert current['version']==before['version'] and current['delivery']['digest']==before['delivery']['digest']
    return result


if __name__=='__main__':
    run(dict(CONFIGURATION,output_folder='detailed-report-compaction-20261104',check_report=check_report,
        after_app=check_native_exports))
