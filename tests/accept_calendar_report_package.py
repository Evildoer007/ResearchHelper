"""Actual packaged calendar labels and normalization context preserve raw data."""
from tests.accept_financial_period_scope_package import configuration as base_configuration
from tests.accept_discovery_navigation_package import run


def check_calendar_report(task,name,profile,exported,resources,output,mode):
    from pathlib import Path
    import csv,io,json,re,unicodedata,zipfile
    from lxml import html,etree
    from pypdf import PdfReader
    from researchhelper.artifact_exporter import export_report
    from researchhelper.publication_content import reader_period_label
    tree=html.document_fromstring(Path(exported['path']).read_text())
    document=json.loads(tree.get_element_by_id('rh-publication-document').text)
    facts=task['draft_report']['facts'];periods=sorted({fact['period'] for fact in facts})
    assert {row[2] for row in document['dataset']['rows']}==set(periods)
    assert len(document['dataset']['rows'])==len(facts)
    partial=name in ('wrong_subject','missing_period')
    if profile=='detailed':
        rows=tree.xpath('//main//tr[@data-observation-id]');assert {row.get('data-observation-id') for row in rows}=={fact['id'] for fact in facts}
        for period in periods:assert reader_period_label(period) in tree.xpath('//main')[0].text_content()
        headers=[node.text_content() for node in tree.xpath('//main//table//th')]
        assert not any(period in headers for period in periods)
        assert '报告期' in headers
        if partial:assert len(tree.xpath('//main//table'))==2
    if name=='interim':
        specs=[block['spec'] for block in document['blocks'] if block['kind']=='chart']
        indexed=next(spec for spec in specs if spec.get('kind')=='indexed_comparison')
        assert indexed['periods']==['2024-06-30','2025-06-30']
        assert indexed['plot_scale']['baseline_period']=='20240630'
        assert '2024-06-30=100' in tree.xpath('//main')[0].text_content()
        assert len(facts)==6
    converted={}
    if name in ('missing_period','interim'):
        for format in ('csv','docx','pdf'):
            saved=export_report(task['qa_scope_content'],resources,output/(mode+'-converted'),profile=profile,format=format)
            payload=Path(saved['path']).read_bytes();item={}
            if format=='csv':
                rows=list(csv.reader(io.StringIO(payload.decode('utf-8-sig'))))
                assert {row[2] for row in rows[1:]}==set(periods)
                item['raw_periods']=periods
            else:
                if format=='docx':
                    with zipfile.ZipFile(io.BytesIO(payload)) as archive:text=''.join(etree.fromstring(archive.read('word/document.xml')).itertext())
                else:
                    pages=PdfReader(io.BytesIO(payload)).pages;texts=[re.sub(r'\s+','',unicodedata.normalize('NFKC',page.extract_text() or '')) for page in pages];text=''.join(texts)
                    assert not any(text.startswith('来源') and len(text)<80 for text in texts)
                    assert all(abs(float(page.mediabox.width)-595.28)<2 and abs(float(page.mediabox.height)-841.89)<2 for page in pages)
                    if name=='missing_period':assert len(pages)==1
                    item.update(pages=len(pages),footer_only_page=False)
                text=re.sub(r'\s+','',unicodedata.normalize('NFKC',text))
                assert all(reader_period_label(period) in text for period in periods)
                if name=='interim':assert '2024-06-30=100' in text
                item['readable_periods_present']=True
            converted[format]=item
    return dict(raw_periods=periods,readable_periods=[reader_period_label(period) for period in periods],
        report_period_heading=profile=='detailed',normalization_context_visible=name=='interim',converted=converted)


def configuration():
    value=base_configuration();worker=value['worker']
    old="for name,wrong,missing in [('sample',False,False),('wrong_subject',True,False),('missing_period',False,True)]:"
    new="for name,wrong,missing,interim in [('sample',False,False,False),('wrong_subject',True,False,False),('missing_period',False,True,False),('interim',False,False,True)]:"
    assert worker.count(old)==1;worker=worker.replace(old,new,1)
    old='wrong_financial_code=wrong,missing_period=missing)'
    new="wrong_financial_code=wrong,missing_period=missing,periods=('20240630','20250630') if interim else None,question='研究证券行业，比较选定样本2024年及2025年中报的收入、净利润和经营现金流。' if interim else None,include_profit=interim)"
    assert worker.count(old)==1;worker=worker.replace(old,new,1)
    old="    expected=[(field,'20241231',value)"
    new="    before,after=('20240630','20250630') if interim else ('20241231','20251231')\n    expected=[(field,before,value)"
    assert worker.count(old)==1;worker=worker.replace(old,new,1)
    old="expected += [(field,'20251231',value)";assert worker.count(old)==1;worker=worker.replace(old,"expected += [(field,after,value)",1)
    value.update(worker=worker,worker_report_check=check_calendar_report,output_folder='calendar-labels-20261105',browser_case='interim',
        browser_options=dict(logic_count=3,calendar_periods=['2024-06-30','2025-06-30'],raw_periods=['20240630','20250630']))
    return value


if __name__=='__main__':
    run(configuration())
