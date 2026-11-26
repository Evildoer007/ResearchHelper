"""Actual packaged reports compact raw tables without losing facts or figures."""
from tests.accept_financial_period_scope_package import configuration as scope_configuration
from tests.accept_discovery_navigation_package import run


def check_inline_evidence(task,name,profile,exported,resources,output,mode):
    from pathlib import Path
    import json,io,re,unicodedata,zipfile
    from lxml import html,etree
    from pypdf import PdfReader
    from researchhelper.artifact_exporter import export_report
    tree=html.document_fromstring(Path(exported['path']).read_text())
    draft=task['draft_report'];facts=draft['facts'];partial=name!='sample'
    original=json.loads(task['draft_submissions'][0]['payload'])
    assert '两期经营原值' in original['conclusion']
    notes=tree.xpath('//main//p[@data-report-scope="financial-period"]')
    if partial:
        assert len(notes)==1 and '2025-12-31' in notes[0].text_content() and '尚缺' in notes[0].text_content()
        assert '两期经营原值' not in tree.get_element_by_id('summary').text_content()
    else:assert not notes
    if profile=='detailed':
        rows=tree.xpath('//main//tr[@data-observation-id]')
        assert sorted(row.get('data-observation-id') for row in rows)==sorted(fact['id'] for fact in facts)
        assert not tree.xpath('//main//*[@id="data"]/div[@aria-label="完整原始数据，可横向滚动"]')
        if partial:assert len(tree.xpath('//main//table'))==2
        else:assert len(tree.xpath('//main//*[contains(concat(" ",normalize-space(@class)," ")," chart ")]'))==4
    converted={}
    if name=='missing_period':
        for format in ('docx','pdf'):
            saved=export_report(task['qa_scope_content'],resources,output/(mode+'-converted'),profile=profile,format=format)
            payload=Path(saved['path']).read_bytes()
            if format=='docx':
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    body=etree.fromstring(archive.read('word/document.xml'))
                    text=''.join(body.itertext())
                    tables=len(body.xpath('//*[local-name()="tbl"]'))
                    assert tables==2,tables
                pages=None
            else:
                reader=PdfReader(io.BytesIO(payload));pages=len(reader.pages);assert pages==1,pages
                page_text=[re.sub(r'\s+','',unicodedata.normalize('NFKC',page.extract_text() or '')) for page in reader.pages]
                assert not any(text.startswith('来源') and len(text)<80 for text in page_text)
                text=''.join(page_text)
                assert all(abs(float(page.mediabox.width)-595.28)<2 and abs(float(page.mediabox.height)-841.89)<2 for page in reader.pages)
                tables=None
            text=re.sub(r'\s+','',unicodedata.normalize('NFKC',text))
            assert '2024-12-31' in text and '2025-12-31' in text and '尚缺' in text
            assert '两期经营原值' not in text
            converted[format]=dict(pages=pages,tables=tables,scope_preserved=True,footer_only_pages=False)
    return dict(partial=partial,raw_rows=len(facts),original_reply_preserved=True,converted=converted,
        raw_tables_compacted=profile=='detailed')


def configuration():
    value=scope_configuration()
    value.update(output_folder='inline-evidence-20261105',worker_report_check=check_inline_evidence)
    value['browser_options']['compact_raw_data']=True
    return value


if __name__=='__main__':
    run(configuration())
