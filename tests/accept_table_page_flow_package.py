"""The actual package keeps a page-fitting data table whole without extra headers."""
from tests.accept_calculation_reading_package import CONFIGURATION as BASE_CONFIGURATION,check_calculation_exports
from tests.accept_financial_subject_package import run


def check_table_exports(client,task_id,output):
    import json,re
    from pypdf import PdfReader
    from lxml import html
    from tests.accept_native_hidden_print import pdf_text_key
    result=check_calculation_exports(client,task_id,output)
    tree=html.document_fromstring(client.call('/api/tasks/'+task_id+'/report?profile=detailed',text=True))
    expected=tree.xpath('//main//*[@id="data"]//tbody/tr[@data-observation-id]');assert len(expected)==16
    pages=[pdf_text_key(page.extract_text() or '') for page in PdfReader(output/'native-detailed.pdf').pages]
    raw_pages=[text.split('关键数据',1)[1] for text in pages if '关键数据' in text];assert len(raw_pages)==1
    text=raw_pages[0];assert text.count('标的')==1;cursor=0
    for row in expected:
        cells=[node.text_content() for node in row.xpath('./td')]
        pattern=re.escape(pdf_text_key(cells[0]))+'.{0,140}?'+re.escape(pdf_text_key(''.join(cells[-3:])))
        match=re.search(pattern,text[cursor:]);assert match,cells;cursor+=match.end()
    result.update(raw_table_rows_on_one_page=16,raw_table_header_count=1)
    (output/'table-flow.json').write_text(json.dumps(dict(status='passed',raw_rows=16,headers=1,table_kept_on_one_page=True,model_calls=0,data_requests=0),indent=2)+'\n')
    return result


def run_long():
    from tests.accept_official_observation_package import CONFIGURATION as LONG_CONFIGURATION
    from tests.accept_detailed_compaction_package import check_report,check_native_exports
    run(dict(LONG_CONFIGURATION,output_folder='table-page-flow-20261105/long',check_report=check_report,after_app=check_native_exports))


CONFIGURATION=dict(BASE_CONFIGURATION,output_folder='table-page-flow-20261105',after_app=check_table_exports,
    browser_options=dict(check_calculation_groups=True,check_table_page_flow=True))
if __name__=='__main__':run(CONFIGURATION)
