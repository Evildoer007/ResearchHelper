"""The actual App and independent Skill keep calculation methods readable."""
from tests.accept_currency_display_package import CONFIGURATION as BASE_CONFIGURATION,check_exports
from tests.accept_financial_subject_package import run


def check_calculation_report(text,profile,good=True):
    import hashlib,json
    from lxml import html
    tree=html.document_fromstring(text);document=json.loads(tree.get_element_by_id('rh-publication-document').text)
    assert document['template']['id']=='report-'+profile and len(document['dataset']['rows'])==16
    assert sum(row[4]=='人民币元' for row in document['dataset']['rows'])==(8 if good else 0)
    charts=[block for block in document['blocks'] if block['kind']=='chart'];assert len(charts)==6
    qualified=[block for block in charts if block['spec'].get('unit')=='人民币元'];assert bool(qualified)==good
    assert all(block['spec']['auto_visual']['data']['unit']=='人民币元' for block in qualified)
    groups=tree.xpath('//main//*[@class="calculation-reading publication-detail-unit"]')
    group_ids=list(dict.fromkeys(row['calculation_group'] for row in document['blocks'] if row.get('calculation_group')))
    assert len(groups)==len(group_ids)==(14 if profile=='detailed' else 0)
    assert all(node.xpath('.//*[local-name()="math"]') and '结果：' in node.xpath('./p')[-1].text_content() for node in groups)
    return dict(rows=16,charts=len(charts),calculation_groups=len(groups),qualified_charts=len(qualified),dataset_sha256=hashlib.sha256(json.dumps(document['dataset'],ensure_ascii=False,sort_keys=True).encode()).hexdigest())


def check_calculation_exports(client,task_id,output):
    import base64,hashlib,json
    from lxml import html
    from tests.test_calculation_reading_groups import pdf_calculation_pages,word_calculation_groups
    before=client.call('/api/tasks/'+task_id);exports=check_exports(client,task_id,output)
    tree=html.document_fromstring(client.call('/api/tasks/'+task_id+'/report?profile=detailed',text=True))
    document=json.loads(tree.get_element_by_id('rh-publication-document').text)
    pdf_checks=pdf_calculation_pages(output/'native-detailed.pdf',document)
    assert len(pdf_checks)==14 and all(row['together'] for row in pdf_checks)
    exported=client.call('/api/export',dict(task_id=task_id,profile='detailed',format='docx'))
    raw=base64.b64decode(exported['content_base64'],validate=True);assert hashlib.sha256(raw).hexdigest()==exported['sha256']
    word_checks=word_calculation_groups(raw,document);assert len(word_checks)==14
    (output/'native-detailed.docx').write_bytes(raw)
    after=client.call('/api/tasks/'+task_id);assert after['ledger']['used']==before['ledger']['used'] and after['delivery']==before['delivery']
    (output/'calculation-pages.json').write_text(json.dumps(dict(pdf=pdf_checks,word=word_checks,model_calls=0,data_requests=0),ensure_ascii=False,indent=2)+'\n')
    return dict(exports=exports,pdf_groups=14,pdf_all_groups_same_page=True,word_groups=14,word_keep_chain_verified=True)


CONFIGURATION=dict(BASE_CONFIGURATION,output_folder='calculation-page-group-20261105',check_report=check_calculation_report,
    after_app=check_calculation_exports,browser_options=dict(check_calculation_groups=True))
if __name__=='__main__':run(CONFIGURATION)
