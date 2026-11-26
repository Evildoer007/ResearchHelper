"""Actual packaged industry sampling; financial numbers are explicit fixtures."""
from tests.test_industry_sample_workflow import run_industry_sample
from tests.test_draft_discovery_navigation import retained_inputs
from tests.accept_discovery_navigation_package import run,WORKER_SETUP

WORKER=WORKER_SETUP+r'''
from lxml import html
from researchhelper.artifact_exporter import export_report
from researchhelper.contracts import fingerprint
from researchhelper.draft_sources import VERSION
from researchhelper.object_discovery import retained_discovery_results
assert VERSION==14
result={}
for name,wrong,missing in [('sample',False,False),('wrong_subject',True,False),('missing_period',False,True)]:
    replay=run_industry_sample(Path(store),resources,INPUTS,wrong_financial_code=wrong,missing_period=missing)
    task=replay['task'];facts=task['draft_report']['facts'];selected=replay['selected']['code']
    assert len(facts)==(3 if wrong or missing else 6)
    assert {row['code'] for row in facts}=={selected} and replay['sample_codes']==[selected]
    assert task['objects']==[] and task['entry']=='行业'
    assert replay['phases']==['draft_plan']*3+['draft_report']
    assert replay['operations']==['get_limit_policy','smart_stock_picking','get_thscode','basic_data_service','basic_data_service']
    assert any(row['status']=='代码映射已核验' and row['codes']==[selected] for row in replay['mapped'])
    assert 'basic_data_service' in replay['plans'][2]['tools']
    assert not task['draft_acquisition_coverage']['blocks_report']
    assert all(row['hash']==fingerprint(row['snapshot']) for row in task['source_records'])
    expected=[(field,'20241231',value) for field,value in [('ths_revenue_stock',100),('ths_np_stock',9),('ths_ncf_from_oa_stock',15)]]
    if not (wrong or missing):expected += [(field,'20251231',value) for field,value in [('ths_revenue_stock',120),('ths_np_stock',12),('ths_ncf_from_oa_stock',10)]]
    assert sorted((row['field'],row['period'],float(row['raw_value'])) for row in facts)==sorted(expected)
    reports={}
    for profile in ('standard','detailed'):
        exported=export_report(replay['paper']['content'],resources,Path(destination).parent/(mode+'-reports'),profile=profile)
        tree=html.document_fromstring(Path(exported['path']).read_text());document=json.loads(tree.get_element_by_id('rh-publication-document').text)
        assert len(document['dataset']['rows'])==len(facts)
        assert len(tree.xpath('//main//section[starts-with(@id,"logic-")]'))==3
        charts=sum(block['kind']=='chart' for block in document['blocks'])
        if not (wrong or missing):assert charts>=1
        reports[profile]=dict(rows=len(facts),charts=charts,dataset_sha256=hashlib.sha256(json.dumps(document['dataset'],ensure_ascii=False,sort_keys=True).encode()).hexdigest())
        if WORKER_REPORT_CHECK:reports[profile]['scope_check']=WORKER_REPORT_CHECK(task,name,profile,exported,resources,Path(destination).parent,mode)
    discovery=retained_discovery_results(task)[0]
    result[name]=dict(task_id=task['id'],reader_version=VERSION,candidate_rows=discovery['row_count'],selected_code=selected,financial_values_synthetic=True,
        facts=len(facts),phases=replay['phases'],operations=replay['operations'],simulated_model_budget=task['ledger']['used']['model_calls'],simulated_data_budget=task['ledger']['used']['data_requests'],
        discovery_source=discovery['artifact_id'],original_first_candidate=discovery['candidates'][0]['name'],reports=reports,original_hashes=[row['hash'] for row in task['source_records']])
Path(destination).write_text(json.dumps(dict(cases=result,loaded_frozen_modules=finder.loaded if finder else []),ensure_ascii=False,indent=2))
'''


def configuration():
    inputs=retained_inputs()
    return dict(runner=run_industry_sample,inputs=[inputs[3],inputs[0]],worker=WORKER,
        output_folder='industry-sample-20261105',browser_case='sample',
        required_report_text='盈利与现金兑现',browser_options=dict(logic_count=3,inspect_navigation=True))


if __name__=='__main__':run(configuration())
