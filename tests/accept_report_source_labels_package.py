"""Bound source names survive actual DMG, independent Skill and native export."""
from tests.accept_official_observation_package import CONFIGURATION
from tests.accept_detailed_compaction_package import check_native_exports
from tests.accept_financial_subject_package import run


def check_report(text,profile,good=True):
    import hashlib,json
    from lxml import html
    tree=html.document_fromstring(text);document=json.loads(tree.get_element_by_id('rh-publication-document').text)
    rows=document['dataset']['rows'];main=tree.find('.//main').text_content()
    assert document['template']['id']=='report-'+profile
    assert len(rows)==(301 if good else 0)
    charts=[block for block in document['blocks'] if block['kind']=='chart']
    if good:
        assert rows[0][-1]=='上交所ETF申赎清单'
        assert all(row[-1]=='中证指数成分权重' for row in rows[1:])
        assert all(label in main for label in ('上交所ETF申赎清单','中证指数成分权重'))
        assert '公开来源' not in main
        assert '上交所ETF申赎清单' in tree.get_element_by_id('draft-basis-1').text_content()
        assert len(charts)==2
        if profile=='detailed':
            raw=tree.xpath('//main//*[@id="data"]//tr[@data-observation-id]')
            assert len(raw)==301 and len(tree.xpath('//main//tbody/tr'))<340
            assert tree.xpath('//main//*[@id="data"]//tr[@data-observation-id="D2"]/td')[4].text_content()=='0.476'
    return dict(rows=len(rows),charts=len(charts),source_labels=sorted(set(row[-1] for row in rows)),
        dataset_sha256=hashlib.sha256(json.dumps(document['dataset'],ensure_ascii=False,sort_keys=True).encode()).hexdigest())


def check_exports(client,task_id,output):
    import base64,csv,hashlib,io,zipfile
    from openpyxl import load_workbook
    from pypdf import PdfReader
    from tests.accept_native_hidden_print import pdf_text_key
    result=check_native_exports(client,task_id,output)
    for profile in ('standard','detailed'):
        pages=PdfReader(output/('native-'+profile+'.pdf')).pages
        text=pdf_text_key(''.join(page.extract_text() or '' for page in pages))
        for label in ('上交所ETF申赎清单','中证指数成分权重'):assert pdf_text_key(label) in text
        result[profile]['source_labels_present']=True
        exports={}
        for format in ('csv','xlsx','docx'):
            exported=client.call('/api/export',dict(task_id=task_id,profile=profile,format=format))
            raw=base64.b64decode(exported['content_base64'],validate=True)
            assert hashlib.sha256(raw).hexdigest()==exported['sha256']
            if format=='csv':
                rows=list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
            elif format=='xlsx':
                book=load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
                try:rows=[list(row) for row in book['研究数据'].iter_rows(values_only=True)]
                finally:book.close()
            else:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:text=archive.read('word/document.xml').decode()
                for label in ('上交所ETF申赎清单','中证指数成分权重'):assert label in text
                exports[format]=dict(status='passed',source_labels_present=True,sha256=exported['sha256']);continue
            assert len(rows)==302 and rows[0][-1]=='来源'
            assert rows[1][-1]=='上交所ETF申赎清单'
            assert all(row[-1]=='中证指数成分权重' for row in rows[2:])
            exports[format]=dict(status='passed',rows=len(rows)-1,source_labels_present=True,sha256=exported['sha256'])
        result[profile]['exports']=exports
    return result


if __name__=='__main__':
    run(dict(CONFIGURATION,output_folder='report-source-labels-20261104',check_report=check_report,after_app=check_exports))
