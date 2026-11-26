"""Replay an explicit two-scope question from the DMG and independent Skill."""
from tests.accept_discovery_navigation_package import run,WORKER_SETUP
from tests.test_financial_visual_cohorts import run_cohort_research
from tests.test_draft_discovery_navigation import retained_inputs

WORKER=WORKER_SETUP+r"""
import csv,io,unicodedata,zipfile
from decimal import Decimal
from lxml import html,etree
from pypdf import PdfReader
from researchhelper.artifact_exporter import export_report
from researchhelper.contracts import fingerprint
replay=run_cohort_research(Path(store),resources,policy=INPUTS[0])
task=replay['task'];content=replay['paper']['content'];facts=content['draft']['facts']
assert task['synthetic'] and task['delivery'] and len(facts)==8
assert replay['operations']==['get_limit_policy']+['basic_data_service']*4
assert replay['phases']==['draft_plan','draft_report']
assert not task['draft_acquisition_coverage']['blocks_report']
by_id={row['id']:row for row in facts};reports={};exports={}
for profile in ('standard','detailed'):
    exported=export_report(content,resources,Path(destination).parent/(mode+'-reports'),profile=profile)
    tree=html.document_fromstring(Path(exported['path']).read_text());document=json.loads(tree.get_element_by_id('rh-publication-document').text)
    specs=[block['spec'] for block in document['blocks'] if block['kind']=='chart']
    assert len(specs)==4 and all(len({(by_id[ref]['code'],by_id[ref].get('series_basis'),by_id[ref]['unit']) for ref in spec['data_refs']})==1 for spec in specs)
    assert len(document['dataset']['rows'])==8 and [row[3] for row in document['dataset']['rows']]==[row['raw_value'] for row in facts]
    assert all('合并报表' in row[1] or '母公司报表' in row[1] for row in document['dataset']['rows'])
    assert sum('合并报表' in spec['name'] for spec in specs)==2 and sum('母公司报表' in spec['name'] for spec in specs)==2
    metrics=dict(rows=8,charts=4,dataset_sha256=fingerprint(document['dataset']),scope_labels=sorted({row[1] for row in document['dataset']['rows']}),formats={})
    exports[profile]={'html':exported}
    for format in ('csv','xlsx','docx','pdf'):
        saved=export_report(content,resources,Path(destination).parent/(mode+'-reports'),profile=profile,format=format);exports[profile][format]=saved
        raw=Path(saved['path']).read_bytes();check={}
        if format=='csv':
            rows=list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))[1:];assert len(rows)==8
            for row,fact in zip(rows,facts):
                assert row[0]==fact['code'] and row[2]==fact['period'] and Decimal(row[3])==Decimal(str(fact['raw_value']))
                assert '合并报表' in row[1] or '母公司报表' in row[1]
            check.update(rows=8,scope_labels_present=True,raw_periods_preserved=True)
        elif format=='xlsx':
            from openpyxl import load_workbook
            sheet=load_workbook(io.BytesIO(raw),data_only=False)['研究数据'];assert sheet.max_row==9
            for index,fact in enumerate(facts,2):
                assert sheet.cell(index,1).value==fact['code'] and str(sheet.cell(index,3).value)==fact['period']
                assert Decimal(str(sheet.cell(index,4).value))==Decimal(str(fact['raw_value']))
                assert '合并报表' in sheet.cell(index,2).value or '母公司报表' in sheet.cell(index,2).value
            check.update(rows=8,scope_labels_present=True,raw_periods_preserved=True)
        else:
            if format=='docx':
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    xml=etree.fromstring(archive.read('word/document.xml'));text=''.join(xml.itertext())
                    images=[name for name in archive.namelist() if name.startswith('word/media/')];assert len(images)==4
                    if profile=='detailed':assert len(xml.xpath('//*[local-name()="oMath"]'))>=len(content['draft']['calculations'])
                check.update(images=4,editable_text=True)
            else:
                pages=PdfReader(io.BytesIO(raw)).pages;text=''.join(page.extract_text() or '' for page in pages)
                assert all(abs(float(page.mediabox.width)-595.28)<2 and abs(float(page.mediabox.height)-841.89)<2 for page in pages)
                compact=[unicodedata.normalize('NFKC',''.join((page.extract_text() or '').split())) for page in pages]
                assert not any(value.startswith('来源') and len(value)<80 for value in compact)
                check.update(pages=len(pages),a4=True,footer_only_page=False)
            text=unicodedata.normalize('NFKC',text)
            assert '合并报表' in text and '母公司报表' in text
            check['scope_labels_present']=True
        metrics['formats'][format]=check
    reports[profile]=metrics
browser_content=Path(destination).parent/(mode+'-browser-content.json')
browser_content.write_text(json.dumps(dict(draft=dict(facts=facts)),ensure_ascii=False,indent=2))
result=dict(cases=dict(comparison=dict(task_id=task['id'],facts=8,logics=2,reports=reports,operations=replay['operations'],phases=replay['phases'],
    simulated_model_calls=task['ledger']['used']['model_calls'],simulated_data_requests=task['ledger']['used']['data_requests'],blocks_report=False)),
    exports=exports,browser_content=str(browser_content),loaded_frozen_modules=finder.loaded if finder else [])
Path(destination).write_text(json.dumps(result,ensure_ascii=False,indent=2))
"""


def configuration():
    return dict(runner=run_cohort_research,inputs=[retained_inputs()[3]['snapshot']],worker=WORKER,
        output_folder='report-cohorts-20261105',browser_case='comparison',
        required_report_text='分口径经营观察',browser_script='tests/accept_financial_visual_cohorts_app.mjs')


if __name__=='__main__':run(configuration())
