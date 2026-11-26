"""Replay the amount-correction counterexample in the actual DMG and Skill."""
from tests.accept_discovery_navigation_package import run,WORKER_SETUP
from tests.test_client_amount_metric_identity import run_amount_identity_research
from tests.test_draft_discovery_navigation import retained_inputs

WORKER=WORKER_SETUP+r'''
import csv,io,unicodedata,zipfile
from decimal import Decimal
from lxml import html,etree
from pypdf import PdfReader
from researchhelper.artifact_exporter import export_report
from researchhelper.contracts import fingerprint
replay=run_amount_identity_research(Path(store),resources,INPUTS[0])
task=replay['task'];content=replay['paper']['content'];facts=content['draft']['facts']
assert task['synthetic'] and task['delivery'] and len(facts)==4
assert replay['phases']==['draft_plan','draft_report']
assert replay['operations']==['get_limit_policy']+['basic_data_service']*4
assert not task['draft_acquisition_coverage']['blocks_report']
assert {row['unit'] for row in facts if row['field']=='ths_np_atoopc_stock'}=={'元'}
assert {row['unit'] for row in facts if row['field']=='ths_np_stock'}=={'原始单位待核对'}
conclusion=content['draft']['conclusion'];assert '归母净利润12元' in conclusion and '归母净利润同比20.00%' not in conclusion
assert '母公司报表净利润同比20.00%' in conclusion
byid={row['id']:row for row in facts}
for change in content['draft'].get('copy_adjustments',[]):
    assert change.get('financial_approval') is False
    assert all(byid[ref]['field']=='ths_np_stock' for ref in change.get('data_refs',[]))
context=replay['writing']['data']['observations']
assert all('value' in row['columns'] for row in context if row['field']=='ths_np_atoopc_stock')
assert all('raw_value_available' in row['columns'] for row in context if row['field']=='ths_np_stock')
reports={};exports={}
def correct_text(value):
    value=unicodedata.normalize('NFKC',''.join(value.split()))
    return '归母净利润12元' in value and '归母净利润同比20.00%' not in value and '母公司报表净利润同比20.00%' in value
for profile in ('standard','detailed'):
    exported=export_report(content,resources,Path(destination).parent/(mode+'-reports'),profile=profile)
    tree=html.document_fromstring(Path(exported['path']).read_text());document=json.loads(tree.get_element_by_id('rh-publication-document').text)
    assert correct_text(tree.xpath('//main')[0].text_content())
    assert all(section.xpath('.//figure') for section in tree.xpath('//section[starts-with(@id,"logic-")]'))
    charts=sum(block['kind']=='chart' for block in document['blocks']);assert charts>=2
    dataset=document['dataset']['rows'];assert len(dataset)==4
    assert [row[3] for row in dataset]==[row['raw_value'] for row in facts]
    assert [row[4] for row in dataset]==[row['unit'] for row in facts]
    metrics=dict(rows=4,charts=charts,dataset_sha256=fingerprint(document['dataset']),known_amount_preserved=True,wrong_measure_growth_absent=True,formats={})
    exports[profile]={'html':exported}
    for format in ('csv','xlsx','docx','pdf'):
        saved=export_report(content,resources,Path(destination).parent/(mode+'-reports'),profile=profile,format=format);exports[profile][format]=saved
        raw=Path(saved['path']).read_bytes();check={}
        if format in ('csv','xlsx'):
            if format=='csv':rows=list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))[1:]
            else:
                from openpyxl import load_workbook
                rows=list(load_workbook(io.BytesIO(raw),data_only=False)['研究数据'].values)[1:]
            assert len(rows)==4
            for row,fact in zip(rows,facts):
                assert row[0]==fact['code'] and str(row[2])==fact['period'] and Decimal(str(row[3]))==Decimal(str(fact['raw_value'])) and row[4]==fact['unit']
            check.update(rows=4,original_values_periods_units_retained=True)
        elif format=='docx':
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                xml=etree.fromstring(archive.read('word/document.xml'));text=''.join(xml.itertext())
                images=sum(name.startswith('word/media/') for name in archive.namelist());assert images==charts
            assert correct_text(text);check.update(images=images,correct_measure_text=True)
        else:
            pages=PdfReader(io.BytesIO(raw)).pages;assert all(abs(float(page.mediabox.width)-595.28)<2 and abs(float(page.mediabox.height)-841.89)<2 for page in pages)
            text=''.join(page.extract_text() or '' for page in pages);assert correct_text(text)
            compact=[unicodedata.normalize('NFKC',''.join((page.extract_text() or '').split())) for page in pages]
            assert not any(value.startswith('来源') and len(value)<80 for value in compact)
            check.update(pages=len(pages),a4=True,correct_measure_text=True,footer_only_page=False)
        metrics['formats'][format]=check
    reports[profile]=metrics
Path(destination).write_text(json.dumps(dict(cases=dict(profit_identity=dict(task_id=task['id'],facts=4,logics=2,
    conclusion=conclusion,reports=reports,phases=replay['phases'],operations=replay['operations'],
    simulated_model_calls=task['ledger']['used']['model_calls'],simulated_data_requests=task['ledger']['used']['data_requests'],blocks_report=False)),
    exports=exports,loaded_frozen_modules=finder.loaded if finder else []),ensure_ascii=False,indent=2))
'''


def configuration():
    return dict(runner=run_amount_identity_research,inputs=[retained_inputs()[3]['snapshot']],worker=WORKER,
        output_folder='client-metric-identity-20261105',browser_case='profit_identity',required_report_text='归母净利润12元',
        browser_script='tests/accept_client_metric_identity_browser.mjs')


if __name__=='__main__':run(configuration())
