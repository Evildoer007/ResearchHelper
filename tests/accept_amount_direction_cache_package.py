"""Extend the actual App/Skill replay with direction and damaged-cache checks."""
from tests.accept_discovery_navigation_package import run
from tests.accept_client_metric_identity_package import WORKER as BASE_WORKER,configuration as base_configuration

CHECKS=r'''
from copy import deepcopy
from researchhelper.draft_analysis import current_analysis
from researchhelper.draft_sources import observations,client_writing_context
from researchhelper.publication_content import prepare_draft_client_copy
calculation_facts=observations(task)
assert len(current_analysis(task,calculation_facts)['results'])==2
checks={}
for key,text,expected in [
    ('single_direction','母公司报表净利润降至12元。','母公司报表净利润降至12（单位未明）。'),
    ('reversed_direction','母公司报表净利润由12元下降至10元。','母公司报表净利润由12（单位未明）下降至10（单位未明）。'),
    ('positive_variant','母公司报表净利润由10元上升到12元。','母公司报表净利润同比20.00%。'),
    ('unknown_range','母公司报表净利润从12元变动至10元。','母公司报表净利润从12（单位未明）变动至10元。')]:
    prepared,changes=prepare_draft_client_copy(task,dict(title='表达核对',conclusion=text,logics=[]),calculation_facts)
    assert prepared['conclusion']==expected,(key,prepared['conclusion']);checks[key]=True
original=fingerprint(task);by_fact={row['id']:row for row in calculation_facts}
bad_id=next(row['id'] for row in task['draft_analysis']['results'] if by_fact[row['data_refs'][0]]['field']=='ths_np_stock')
for key,value in [('display','999.00'),('exact',None),('inputs',dict(a=1,b=12))]:
    damaged=deepcopy(task);next(row for row in damaged['draft_analysis']['results'] if row['id']==bad_id)[key]=value
    retained=current_analysis(damaged,calculation_facts)['results'];assert len(retained)==1 and all(row['id']!=bad_id for row in retained)
    context=client_writing_context(damaged,calculation_facts);assert len(context['calculations'])==1 and context['calculations'][0]['id']!=bad_id
    prepared,_=prepare_draft_client_copy(damaged,dict(title='缓存核对',conclusion='母公司报表净利润12元。',logics=[]),calculation_facts)
    assert prepared['conclusion']=='母公司报表净利润12（单位未明）。'
    checks['invalid_'+key]=True
assert fingerprint(task)==original;checks['frozen_task_unchanged']=True
'''


def configuration():
    config=base_configuration()
    assert BASE_WORKER.count('reports={};exports={}')==1
    assert BASE_WORKER.count('conclusion=conclusion,reports=reports')==1
    assert BASE_WORKER.count("for format in ('csv','xlsx','docx','pdf'):")==1
    config.update(output_folder='amount-direction-cache-20261105',worker=BASE_WORKER.replace('reports={};exports={}',CHECKS+'\nreports={};exports={}').replace('conclusion=conclusion,reports=reports','conclusion=conclusion,cache_direction_checks=checks,reports=reports').replace("for format in ('csv','xlsx','docx','pdf'):",'for format in ():'))
    return config


if __name__=='__main__':run(configuration())
