"""Partial financial windows remain visible in actual packaged deliveries."""
from tests.accept_industry_sample_package import configuration as base_configuration
from tests.accept_discovery_navigation_package import run


def check_period_scope(task,name,profile,exported,resources,output,mode):
    from pathlib import Path
    import io,json,re,zipfile
    from lxml import html
    from pypdf import PdfReader
    from researchhelper.artifact_exporter import export_report
    source=Path(exported['path']);tree=html.document_fromstring(source.read_text())
    draft=task['draft_report'];partial=name!='sample'
    from researchhelper.draft_sources import CLIENT_COPY_VERSIONS
    assert task['draft_client_copy_version'] in CLIENT_COPY_VERSIONS[1:]
    original=json.loads(task['draft_submissions'][0]['payload'])
    assert '两期经营原值' in original['conclusion']
    main=tree.xpath('//main')[0].text_content();notes=tree.xpath('//main//p[@class="financial-period-scope"]')
    if partial:
        assert '两期经营原值' not in draft['conclusion'] and '两期经营原值' not in tree.get_element_by_id('summary').text_content()
        assert '不能据此推断全行业' in draft['conclusion'] and '单一样本不代表行业' in main
        assert len(notes)==1 and '2024-12-31' in notes[0].text_content() and '2025-12-31' in notes[0].text_content()
        assert any(change['reason']=='financial_period_scope' for change in draft['copy_adjustments'])
        assert not draft['financial_period_scope']['financial_approval'] and not draft['financial_period_scope']['blocks_report']
    else:
        assert '两期经营原值' in draft['conclusion'] and not notes
    converted={}
    if name=='missing_period':
        for format in ('docx','pdf'):
            result=export_report(task['qa_scope_content'],resources,output/(mode+'-converted'),profile=profile,format=format)
            payload=Path(result['path']).read_bytes()
            if format=='docx':
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:body=archive.read('word/document.xml').decode()
                root=__import__('xml.etree.ElementTree',fromlist=['fromstring']).fromstring(body)
                text=''.join(root.itertext());pages=None
            else:
                reader=PdfReader(io.BytesIO(payload));text=''.join(page.extract_text() for page in reader.pages);pages=len(reader.pages)
                assert pages and pages<=4
            compact=re.sub(r'\s+','',text)
            assert '2024-12-31' in compact and '2025-12-31' in compact and '尚缺' in compact
            assert '两期经营原值' not in compact
            converted[format]=dict(scope_present=True,pages=pages)
    return dict(partial=partial,scope_notes=len(notes),raw_rows=len(draft['facts']),original_reply_preserved=True,converted=converted)


def configuration():
    value=base_configuration()
    # Pass the same frozen content to format conversion, never rebuild a research.
    value['worker']=value['worker'].replace("task=replay['task'];facts=", "task=replay['task'];task['qa_scope_content']=replay['paper']['content'];facts=")
    value.update(output_folder='financial-period-scope-20261105',browser_case='missing_period',worker_report_check=check_period_scope,
        browser_options=dict(logic_count=3,scope_notice=True,expect_charts=False))
    return value


if __name__=='__main__':run(configuration())
