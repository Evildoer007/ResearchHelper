"""Replay bad source envelopes through the DMG and independent Skill.

Only small synthetic records are seeded in temporary stores. Acquisition and
model quality are outside this test; the packaged first-report workflow itself
must keep usable data, quarantine failed or changed responses, and still save.
"""
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import zipfile

from lxml import html
from tests import ROOT
sys.path.insert(0, str(ROOT / 'packaging'))
from accept_current import AppClient
from build_current import verify_current_build
from tests.accept_native_workbench_render import RenderRuntime

QUESTION = '来源隔离验收，使用已提供的测试行情记录生成简短报告，不另行查询。'
REPORT = dict(title='行情与财务口径观察', conclusion='已取得的有效收盘价为42元，单个时点不足以判断趋势；现金流与归母净利润应分别核对。',
    logics=[dict(title='保留有效观测', analysis='同一任务内区分可用观测与不可用来源。',
                 implication='补齐可比序列后再判断价格路径。', risk='单点读数缺少历史对照。',
                 data_refs=['D1'], source_refs=['good']),
            dict(title='区分现金流与利润范围',analysis='现金流与归母净利润的归属范围不同，不据此构造现金含量比率。',
                 implication='使用相同期间和范围的数据后，再讨论现金兑现。',risk='单期金额不能解释变化原因。',
                 data_refs=['D4','D5'],source_refs=['financial-scope']),
            dict(title='日期序列保持同标的同窗口',analysis='比较实际取得的两次PE读数，不用其他标的、窗口外日期或未请求的字段。',
                 implication='仅讨论观察窗口内的读数变化，不判断估值高低。',risk='两次观测不足以识别估值区间。',
                 data_refs=['D6','D7'],source_refs=['sequence-partial'])],risks=['缺少历史序列。'])


def seed_sources(archive, task):
    """Originals only: normalization and report generation stay in the product."""
    from researchhelper.contracts import fingerprint
    records = []
    for ident, value in [('good', 42), ('failed', 98765), ('changed', 87654), ('old_input', 76543)]:
        params = dict(codes='600519.SH', indicators='close',
                      startdate='2026-10-30', enddate='2026-10-30',
                      functionpara=dict(Currency='RMB', CPS='1'))
        snapshot = dict(errorcode=0, tables=[dict(thscode='600519.SH', time=['2026-10-30'],
                                                 table=dict(close=[value]))])
        row = dict(id=ident, task_id=task['id'], research_version=task['version'],
                   kind='api', source='ifind', operation='cmd_history_quotation',
                   parameters=params, snapshot=snapshot, hash=fingerprint(snapshot),
                   request_hash=fingerprint(dict(source='ifind', operation='cmd_history_quotation', parameters=params)),
                   status='成功待核验')
        if ident == 'failed':
            row['status'] = '业务失败待按官方错误定义核查'
        elif ident == 'changed':
            row['snapshot']['tables'][0]['table']['close'][0] += 1
        elif ident == 'old_input':
            row['input_hash'] = 'a-different-test-input'
        records.append(row)
    params = dict(codes='600519.SH', indicators='open', startdate='2026-10-29', enddate='2026-10-30',
                  functionpara=dict(Currency='RMB', CPS='1', Fill='Omit'))
    snapshot = dict(errorcode=0, tables=[
        dict(thscode='600519.SH', time=['2026-10-29','2026-10-30','2026-11-02'],
             table=dict(open=[40,None,94765], target_price=[99999,99999,99999])),
        dict(thscode='600036.SH', time=['2026-10-30'], table=dict(open=[93654]))])
    records.append(dict(id='quote-partial', task_id=task['id'], research_version=task['version'],
        input_hash=task['input_hash'], kind='api', source='ifind', operation='cmd_history_quotation',
        parameters=params, snapshot=snapshot, hash=fingerprint(snapshot),
        request_hash=fingerprint(dict(source='ifind', operation='cmd_history_quotation', parameters=params)),
        status='成功待核验'))
    params=dict(codes='600519.SH',report_period='20251231',statement_type='1')
    snapshot=dict(errorcode=0,tables=[dict(thscode='600519.SH',
        table=dict(ths_np_atoopc_stock=[500],ths_ncf_from_oa_stock=[400]))])
    records.append(dict(id='financial-scope',task_id=task['id'],research_version=task['version'],
        kind='api',source='ifind',operation='basic_data_service',parameters=params,snapshot=snapshot,
        hash=fingerprint(snapshot),request_hash=fingerprint(dict(source='ifind',operation='basic_data_service',parameters=params)),
        status='成功待核验'))
    params=dict(codes='600519.SH',startdate='2026-10-29',enddate='2026-10-30',functionpara=dict(Fill='Blank'),
                indipara=[dict(indicator='ths_pe_ttm_stock',indiparams=['','100'])])
    snapshot=dict(errorcode=0,tables=[dict(thscode='600519.SH',time=['2026-10-29','2026-10-30','2026-11-02'],
        table=dict(ths_pe_ttm_stock=[20,21,98766],not_requested=[94444,94444,94444])),
        dict(thscode='600036.SH',time=['2026-10-30'],table=dict(ths_pe_ttm_stock=[95854]))])
    records.append(dict(id='sequence-partial',task_id=task['id'],research_version=task['version'],kind='api',
        source='ifind',operation='date_sequence',parameters=params,snapshot=snapshot,hash=fingerprint(snapshot),
        request_hash=fingerprint(dict(source='ifind',operation='date_sequence',parameters=params)),status='成功待核验'))
    task.update(synthetic=True, source_records=records)
    return archive.save(task)


def seed_linked_source(archive, task):
    from researchhelper.evidence import snapshot_record
    parameters=dict(codes='600519.SH',indicators='close',startdate='2026-10-29',enddate='2026-10-30',
                    functionpara=dict(Currency='RMB',CPS='1',Fill='Omit'))
    response=dict(errorcode=0,tables=[dict(thscode='600519.SH',time=['2026-10-29','2026-10-30'],
                                         table=dict(close=['101.25','102.50']))])
    source=snapshot_record(task,'ifind','cmd_history_quotation',parameters,response)
    task.update(synthetic=True,source_records=[source])
    return archive.save(task)


def check_task(task):
    from researchhelper.contracts import fingerprint
    assert task.get('delivery'), task.get('error')
    assert task['ledger']['used']['model_calls'] == task['ledger']['used']['data_requests'] == 0
    submission = task['draft_submissions'][-1]
    assert submission['origin'] == 'host'
    assert submission['payload'] == task['host_result']['draft_report']
    assert submission['payload_hash'] == fingerprint(submission['payload'])
    assert submission['task_id'] == task['id'] and submission['research_version'] == task['version']
    assert submission['batch'] == task['batch'] and submission['input_hash'] == task['input_hash']
    assert task.get('draft_quote_scope_version') == 2
    assert len(task['source_records']) == 7
    assert task['source_records'][1]['snapshot']['tables'][0]['table']['close'] == [98765]
    facts = task['draft_report']['facts']
    assert [(row['artifact_id'], row['raw_value']) for row in facts] == [('good',42),('quote-partial',40),('quote-partial',None),('financial-scope',400),('financial-scope',500),('sequence-partial',20),('sequence-partial',21)]
    assert {row['artifact_id'] for row in task['draft_source_issues']} == {'failed', 'changed', 'old_input', 'quote-partial','sequence-partial'}
    assert next(row for row in task['draft_source_issues'] if row['artifact_id']=='quote-partial')['partial']
    partial = next(row for row in task['source_records'] if row['id']=='quote-partial')
    assert partial['snapshot']['tables'][0]['table']['open']==[40,None,94765]
    assert partial['hash']==fingerprint(partial['snapshot'])
    from researchhelper.draft_sources import source_context, writing_context
    for context in (source_context(task),writing_context(task,facts)):
        assert not any(value in json.dumps(context) for value in ('94765','93654','99999','98766','94444','95854'))
    assert next(row['status'] for row in task['nodes'] if row['id'] == 'evidence') == '部分成功'
    guide=task['draft_report']['interpretation_context']
    assert guide['binding']['task_id']==task['id'] and guide['binding']['facts_hash']==fingerprint(facts)
    assert not guide['financial_approval'] and not guide['delivery_blocking']
    rules={row['id']:row for row in guide['rules']}
    assert rules['parent_profit_scope']['objects']==['600519.SH']
    assert '不能用归母净利润代替' in rules['parent_profit_scope']['text']
    assert not task['draft_report']['calculations']
    return dict(observations=len(facts), originals=len(task['source_records']),
        quarantined=sum(not row.get('partial',False) for row in task['draft_source_issues']),
        partial_sources=sum(bool(row.get('partial')) for row in task['draft_source_issues']),
        report_saved=True,interpretation_rules=guide['rules'])


def check_report(text, profile):
    tree = html.document_fromstring(text)
    document = json.loads(tree.get_element_by_id('rh-publication-document').text)
    assert 'draft_submissions' not in text
    assert document['template']['id'] == 'report-' + profile
    assert len(document['dataset']['rows']) == 7
    main = tree.xpath('//main')[0].text_content()
    assert '传导逻辑一' in main and '42' in main
    for rejected in ('98765', '87655', '76543', '94765', '93654', '99999','98766','94444','95854'):
        assert rejected not in main and rejected not in json.dumps(document['dataset'])
    return document['dataset']


def check_outline(task):
    assert task.get('delivery'),task.get('error')
    assert task['ledger']['used']['model_calls']==task['ledger']['used']['data_requests']==0
    assert not task['draft_report']['facts']
    assert task['draft_report']['interpretation_context']['rules']==[]
    titles=[row['title'] for row in task['draft_report']['logics']]
    assert titles
    if task['entry']=='事件':assert '事件变化与实际暴露' in titles
    if task['entry']=='指数':assert '跟踪与交易约束' not in titles
    if task['entry']=='宏观':assert '利率与风险溢价传导' not in titles
    return titles


def seed_legacy(archive,task):
    from researchhelper.planning import input_fingerprint
    task=archive.get(task['id'])
    task.pop('draft_quote_scope_version')
    task.pop('draft_direct_scope_version',None)
    task.pop('draft_question_scope_version',None)
    task['input_hash']=input_fingerprint(task)
    task=seed_sources(archive,task)
    # Match actual transport records: only the deliberate wrong-input fixture
    # carries an input hash. Current records retain their original snapshots.
    next(row for row in task['source_records'] if row['id']=='quote-partial').pop('input_hash',None)
    return archive.save(task)


def check_legacy_first(task):
    assert task.get('delivery'),task.get('error')
    assert 'draft_quote_scope_version' not in task
    assert any(row['raw_value']==94765 for row in task['draft_report']['facts'])


def check_legacy_update(archive,task,digest):
    assert task['version']==2 and task['draft_quote_scope_version']==2
    assert archive.report(task['id'],1)['digest']==digest
    assert 'draft_quote_scope_version' not in archive.get(task['id'],1)
    assert archive.report(task['id'],2)['content']['draft_quote_scope_version']==2
    return dict(result=check_task(task),prior_frozen=True,new_reader_applied=True)


def wait_report(client,ident,version):
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        task=client.call('/api/tasks/'+ident)
        if (task.get('delivery') or {}).get('version')==version:return task
        if task['status'] not in ('运行中','待开始'):raise RuntimeError(str((task['status'],task.get('error'))))
        time.sleep(.05)
    raise RuntimeError('Packaged research did not save the requested version')


def check_direct(task,kind):
    assert task.get('delivery'),task.get('error')
    assert task['draft_direct_scope_version']==1
    draft=task['draft_report']
    assert draft.get('organization')==kind and len(draft['logics'])==1
    assert draft['logics'][0]['data_refs']==[row['id'] for row in draft['facts']]
    assert task['ledger']['used']['model_calls']==task['ledger']['used']['data_requests']==0
    return dict(organization=kind,rows=len(draft['facts']),logics=len(draft['logics']))


def check_direct_report(text,kind):
    tree=html.document_fromstring(text)
    main=tree.xpath('//main')[0].text_content()
    assert '策略逻辑' not in main
    assert ('原始观测' if kind=='fact' else '同口径对照') in main
    doc=json.loads(tree.get_element_by_id('rh-publication-document').text)
    return len(doc['dataset']['rows'])


SCOPE_CASES = [
    dict(name='initial_fact', kind=None, selected='fact', mode='by_question', organization='fact'),
    dict(name='initial_comparison', kind=None, selected='comparison', mode='by_question', organization='comparison'),
    dict(name='initial_mechanism', kind=None, selected='mechanism', mode='by_question', organization='mechanism'),
    dict(name='explicit_mechanism', kind='mechanism', selected=None, mode='mechanism', organization='mechanism'),
    dict(name='linked_mechanism',kind='mechanism',selected=None,mode='mechanism',organization='mechanism',linked_data=True),
    dict(name='six_step_mechanism',kind='mechanism',selected=None,mode='mechanism',organization='mechanism',steps=['需求变化', '订单调整', '产能反应', '交付变化', '库存变化', '定价调整']),
    dict(name='long_mechanism',kind='mechanism',selected=None,mode='mechanism',organization='mechanism',steps=['需求变化', '订单调整', '产能反应', '交付变化', '库存变化', '定价调整', '成本传导', '利润变化', '现金回收', '融资反馈', '投资调整', '供给反应', '后续验证']),
    dict(name='protected_mechanism', kind='mechanism', selected='research', mode='mechanism', organization='mechanism'),
    dict(name='protected_investment', kind='investment', selected='mechanism', mode='research', organization=None),
    dict(name='initial_research', kind=None, selected='research', mode='by_question', organization='research'),
    dict(name='required_investment', kind='investment', selected='fact', mode='research', organization=None),
    dict(name='explicit_fact', kind='fact', selected=None, mode='fact', organization='fact'),
    dict(name='explicit_comparison', kind='comparison', selected=None, mode='comparison', organization='comparison'),
]


def scoped_options(case):
    question = '按原问题组织已取得的资料，仅验收展示范围，不查询外部服务'
    options = dict(question=question, entry='个股', intent='综合研究',cutoff='2026-10-30T18:00:00+08:00')
    if case.get('linked_data'):options['objects']=['600519.SH']
    if case['kind']:
        options['questions'] = [dict(id='q1', question=question, kind=case['kind'], required=True)]
    return options


def scoped_report(case):
    logic = dict(title='提供的论述', analysis='本段正文应当原样保留。')
    if case['organization'] == 'mechanism':
        labels=case.get('steps',['潜在变化','传导条件','可能影响'])
        logic['mechanism'] = dict(steps=[dict(label=label,kind='condition' if index==1 else 'hypothesis')
            for index,label in enumerate(labels)])
    if case.get('linked_data'):
        logic['mechanism']['steps'][0]['data_refs']=['D1']
        logic['mechanism']['steps'][1]['data_refs']=['D2']
    return dict(title='范围专项', report_scope=case['selected'], conclusion='只回答原问题。',logics=[logic])


def check_scoped(task, case):
    from researchhelper.planning import draft_research_scope
    assert task.get('delivery'), task.get('error')
    assert task['draft_question_scope_version'] == 2
    scope = draft_research_scope(task)
    assert scope['mode'] == case['mode']
    assert not scope['changes_question_obligations'] and scope['extra_model_calls'] == 0
    assert task['draft_report'].get('organization') == case['organization']
    assert task['draft_report']['logics'][0]['analysis'] == '本段正文应当原样保留。'
    if case['organization']=='mechanism':
        assert [step['label'] for step in task['draft_report']['logics'][0]['mechanism']['steps']]==case.get('steps',['潜在变化','传导条件','可能影响'])
    if case.get('linked_data'):
        assert task['draft_report']['logics'][0]['data_refs']==['D1','D2']
        assert [row['raw_value'] for row in task['draft_report']['facts']]==['101.25','102.50']
    assert task['ledger']['used']['model_calls'] == task['ledger']['used']['data_requests'] == 0
    return dict(mode=scope['mode'], organization=task['draft_report'].get('organization'),
        question_kind=task['questions'][0]['kind'], paragraph_preserved=True)


def check_scoped_html(raw, case):
    tree = html.document_fromstring(raw)
    main = tree.xpath('//main')[0].text_content()
    assert '本段正文应当原样保留。' in main
    direct = case['organization'] in ('fact', 'comparison')
    assert ('一、提供的论述' in main) == direct
    if case['organization'] == 'mechanism':
        assert '传导逻辑一' in main and '策略逻辑' not in main
        assert '传导路径示意' in main and '非数值预测' in main
        document=json.loads(tree.get_element_by_id('rh-publication-document').text)
        charts=[block for block in document['blocks'] if block['kind']=='chart']
        labels=case.get('steps',['潜在变化','传导条件','可能影响'])
        paths=[block for block in charts if block['spec'].get('coverage_kind')=='mechanism']
        assert len(paths)==(len(labels)+2)//4
        nodes={};edges=[]
        for block in paths:
            data=block['spec']['auto_visual']['data'];assert data['qualitative']
            nodes.update({node['id']:node['name'] for node in data['nodes']})
            edges.extend((edge['source'],edge['target']) for edge in data['links'])
        assert [nodes['step-'+str(i)] for i in range(len(labels))]==labels
        assert edges==[('step-'+str(i),'step-'+str(i+1)) for i in range(len(labels)-1)]
        if case.get('linked_data'):
            assert any(block['spec'].get('coverage_kind')=='numeric' for block in charts)
            assert [row[3] for row in document['dataset']['rows']]==['101.25','102.50']
            assert all(set(block['spec'].get('data_refs',[]))<=set(block['spec'].get('argument_data_refs',[])) for block in charts)
        else:assert document['dataset']['rows']==[]
    else:
        assert ('策略逻辑一' in main) != direct


def check_mechanism_pdf(raw, case=None):
    from io import BytesIO
    import unicodedata
    from pypdf import PdfReader
    assert raw.startswith(b'%PDF-')
    pages=PdfReader(BytesIO(raw)).pages
    assert 1<=len(pages)<=(2 if case else 1)
    text=''.join(unicodedata.normalize('NFKC',''.join(page.extract_text() for page in pages)).split())
    labels=case.get('steps',['潜在变化','传导条件','可能影响']) if case else ['潜在变化','传导条件','可能影响']
    assert all(label in text for label in ['传导逻辑一','传导路径示意',*labels]), 'Qualitative graph labels missing from WebKit PDF'
    assert 'report_scope' not in text
    if case and case.get('linked_data'):assert '101.25' in text and '102.5' in text
    return dict(pages=len(pages),mechanism_steps=len(labels),qualitative_path_labels_preserved=True)


WORKER = r'''
import json,socket,sys
from lxml import html
from pathlib import Path
installation,project,wire_path,output=map(Path,sys.argv[1:])
sys.path.insert(0,str(installation))
from core.session import Session
def denied(*a,**k):raise RuntimeError('No external I/O in source replay')
socket.socket.connect=denied;socket.socket.connect_ex=denied
wire=json.loads(wire_path.read_text());session=Session(project)
try:
 task=session.create(wire['question'],entry='个股',intent='解释变化',research_mode='current')
 task=seed_sources(session.archive,task)
 task=session.draft(task['id'],report=wire['report'],data_requests=[])
 result=dict(task=check_task(task),reports={})
 for profile in ('standard','detailed'):
  result['reports'][profile]=session.export(task['id'],profile=profile).read_text()
 legacy=session.create(wire['question'],entry='个股',intent='解释变化',cutoff='2026-10-30T18:00:00+08:00')
 seed_legacy(session.archive,legacy)
 legacy=session.draft(legacy['id'],report={},data_requests=[])
 check_legacy_first(legacy);prior=session.archive.report(legacy['id'],1)['digest']
 session.update(legacy['id'],cutoff='2026-10-31T00:00:00+08:00')
 legacy=session.draft(legacy['id'],report=wire['report'],data_requests=[])
 result['legacy_update']=check_legacy_update(session.archive,legacy,prior)
 for profile in ('standard','detailed'):
  check_report(session.export(legacy['id'],profile=profile).read_text(),profile)
 from researchhelper.contracts import ENTRIES
 result['outlines']={}

 for entry in ENTRIES:
  question='检验'+entry+'异常出稿的条件性投资研究，无实时资料'
  empty=session.create(question,entry=entry,intent='综合研究',research_mode='current',questions=[dict(id='q1',question=question,kind='investment',required=True)])
  empty=session.draft(empty['id'],report={},data_requests=[])
  result['outlines'][entry]=check_outline(empty)
 result['direct']={}
 for entry in ENTRIES:
  for kind in ('fact','comparison'):
   question='核对已经取得的数据'
   direct=session.create(question,entry=entry,intent='综合研究',questions=[dict(id='q1',question=question,kind=kind,required=True)])
   direct=session.draft(direct['id'],report={},data_requests=[])
   result['direct'][entry+kind]=check_direct(direct,kind)
   for profile in ('standard','detailed'):
    assert check_direct_report(session.export(direct['id'],profile=profile).read_text(),kind)==0
 question='核对已取得的600519.SH数据，不判断投资价值'
 direct=session.create(question,entry='个股',intent='综合研究',questions=[dict(id='q1',question=question,kind='fact',required=True)])
 seed_sources(session.archive,direct)
 direct=session.draft(direct['id'],report={},data_requests=[])
 result['direct']['with_data']=check_direct(direct,'fact')
 for profile in ('standard','detailed'):
  assert check_direct_report(session.export(direct['id'],profile=profile).read_text(),'fact')==7
 result['question_scopes'] = {}
 from researchhelper.publication_content import client_report_brief
 for case in wire['scope_cases']:
  scoped = session.create(**scoped_options(case))
  if case.get('linked_data'):scoped=seed_linked_source(session.archive,scoped)
  first = session.tools(scoped['id'])['first_report']
  assert first['research_scope']['mode'] == case['mode']
  assert 'report_scope' in first['optional_report_fields']
  assert first['writing_brief'] == client_report_brief(scoped)
  scoped = session.draft(scoped['id'], report=scoped_report(case), data_requests=[])
  result['question_scopes'][case['name']] = check_scoped(scoped, case)
  for profile in ('standard', 'detailed'):
   check_scoped_html(session.export(scoped['id'], profile=profile).read_text(), case)
  if case['name'] in ('explicit_mechanism','long_mechanism','linked_mechanism'):
   kind=case['name'].removeprefix('explicit_')
   key=kind+'_pdf';prefix=kind.replace('_','-')+'-'
   result[key] = {}
   for profile in ('standard','detailed'):
    raw=session.export(scoped['id'],profile=profile,format='pdf').read_bytes()
    result[key][profile]=check_mechanism_pdf(raw,case if case['name']!='explicit_mechanism' else None)
    output.with_name(prefix+profile+'.pdf').write_bytes(raw)
   check_scoped(session.status(scoped['id']), case)
 output.write_text(json.dumps(result,ensure_ascii=False))
finally:session.agent.close_runtime()
'''


def run(candidate_path=None):
    selected = Path(candidate_path or os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve()
    candidate = verify_current_build(selected, ROOT)
    parent = ROOT / 'result/acceptance/draft-source-integrity'
    parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='package-', dir=parent))
    record = dict(status='running', candidate=str(selected), synthetic_source_replay=True,
                  real_acquisition_verified=False, real_model_quality_verified=False,
                  model_calls=0, data_requests=0, production_store_touched=False,
                  native_window_opened=False)
    runtime = RenderRuntime(output, record, candidate_path=selected, discard_temporary_store=True)
    try:
        client = AppClient(runtime.start())
        task = client.call('/api/tasks', dict(question=QUESTION, entry='个股', intent='解释变化', research_mode='current'))
        from researchhelper.archive import ResearchArchive
        seed_sources(ResearchArchive(output / 'store'), task)
        execution = {key: client.bootstrap['execution'][key] for key in ('mode', 'policy_version')}
        client.call('/api/tasks/' + task['id'] + '/start',
                    dict(expected_execution=execution, host_result=dict(data_requests=[], draft_report=REPORT)))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            task = client.call('/api/tasks/' + task['id'])
            if task.get('delivery'):
                break
            if task['status'] not in ('运行中', '待开始'):
                raise RuntimeError(task.get('error') or task['status'])
            time.sleep(.1)
        app_task = check_task(task)
        app = {profile: check_report(client.call('/api/tasks/' + task['id'] + '/report?profile=' + profile, text=True), profile)
               for profile in ('standard', 'detailed')}
        from researchhelper.contracts import ENTRIES
        outlines={}
        for entry in ENTRIES:
            question='检验'+entry+'异常出稿的条件性投资研究，无实时资料'
            empty=client.call('/api/tasks',dict(question=question,entry=entry,intent='综合研究',research_mode='current',
                questions=[dict(id='q1',question=question,kind='investment',required=True)]))
            client.call('/api/tasks/'+empty['id']+'/start',dict(expected_execution=execution,
                host_result=dict(data_requests=[],draft_report={})))
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                empty=client.call('/api/tasks/'+empty['id'])
                if empty.get('delivery'):break
                if empty['status'] not in ('运行中','待开始'):raise RuntimeError(empty.get('error') or empty['status'])
                time.sleep(.05)
            outlines[entry]=check_outline(empty)
        archive=ResearchArchive(output/'store')
        legacy=client.call('/api/tasks',dict(question=QUESTION,entry='个股',intent='解释变化',cutoff='2026-10-30T18:00:00+08:00'))
        seed_legacy(archive,legacy)
        client.call('/api/tasks/'+legacy['id']+'/start',dict(expected_execution=execution,
            host_result=dict(draft_report={},data_requests=[])))
        legacy=wait_report(client,legacy['id'],1);check_legacy_first(legacy)
        prior=archive.report(legacy['id'],1)['digest']
        context={key:legacy[key] for key in ('id','version','batch','cutoff','input_hash')}
        client.call('/api/tasks/'+legacy['id']+'/update',dict(expected_execution=execution,expected_task=context,
            cutoff='2026-10-31T00:00:00+08:00',host_result=dict(draft_report=REPORT,data_requests=[])))
        legacy=wait_report(client,legacy['id'],2)
        legacy_update=check_legacy_update(archive,legacy,prior)
        for profile in ('standard','detailed'):
            check_report(client.call('/api/tasks/'+legacy['id']+'/report?profile='+profile,text=True),profile)
        direct_results={}
        for entry in ENTRIES:
            for kind in ('fact','comparison'):
                question='核对已经取得的数据'
                direct=client.call('/api/tasks',dict(question=question,entry=entry,intent='综合研究',
                    questions=[dict(id='q1',question=question,kind=kind,required=True)]))
                client.call('/api/tasks/'+direct['id']+'/start',dict(expected_execution=execution,host_result=dict(data_requests=[],draft_report={})))
                direct=wait_report(client,direct['id'],1)
                direct_results[entry+kind]=check_direct(direct,kind)
                for profile in ('standard','detailed'):
                    assert check_direct_report(client.call('/api/tasks/'+direct['id']+'/report?profile='+profile,text=True),kind)==0
        question='核对已取得的600519.SH数据，不判断投资价值'
        direct=client.call('/api/tasks',dict(question=question,entry='个股',intent='综合研究',
            questions=[dict(id='q1',question=question,kind='fact',required=True)]))
        seed_sources(archive,direct)
        client.call('/api/tasks/'+direct['id']+'/start',dict(expected_execution=execution,host_result=dict(data_requests=[],draft_report={})))
        direct=wait_report(client,direct['id'],1);direct_results['with_data']=check_direct(direct,'fact')
        for profile in ('standard','detailed'):
            assert check_direct_report(client.call('/api/tasks/'+direct['id']+'/report?profile='+profile,text=True),'fact')==7
        question_scopes = {}
        for case in SCOPE_CASES:
            scoped = client.call('/api/tasks', scoped_options(case))
            if case.get('linked_data'):seed_linked_source(archive,scoped)
            client.call('/api/tasks/' + scoped['id'] + '/start', dict(expected_execution=execution,
                host_result=dict(data_requests=[], draft_report=scoped_report(case))))
            scoped = wait_report(client, scoped['id'], 1)
            question_scopes[case['name']] = check_scoped(scoped, case)
            for profile in ('standard', 'detailed'):
                check_scoped_html(client.call('/api/tasks/' + scoped['id'] + '/report?profile=' + profile, text=True), case)
            if case['name'] in ('explicit_mechanism','long_mechanism','linked_mechanism'):
                import base64
                kind=case['name'].removeprefix('explicit_');results={}
                prefix=kind.replace('_','-')+'-'
                for profile in ('standard','detailed'):
                    exported=client.call('/api/export',dict(task_id=scoped['id'],profile=profile,format='pdf'))
                    raw=base64.b64decode(exported['content_base64'])
                    results[profile]=check_mechanism_pdf(raw,case if case['name']!='explicit_mechanism' else None)
                    (output/('app-'+prefix+profile+'.pdf')).write_bytes(raw)
                if kind=='long_mechanism':long_mechanism_pdf=results
                elif kind=='linked_mechanism':linked_mechanism_pdf=results
                else:mechanism_pdf=results
                check_scoped(client.call('/api/tasks/'+scoped['id']), case)
        with tempfile.TemporaryDirectory(prefix='skill-', dir=output) as folder:
            folder = Path(folder)
            with zipfile.ZipFile(candidate['artifacts']['skill']['path']) as package:
                package.extractall(folder / 'installation')
            wire = folder / 'input.json'
            wire.write_text(json.dumps(dict(question=QUESTION, report=REPORT, scope_cases=SCOPE_CASES), ensure_ascii=False))
            worker = folder / 'worker.py'
            worker.write_text('\n'.join(inspect.getsource(func) for func in (seed_sources,seed_linked_source,check_task,check_outline,check_report,seed_legacy,check_legacy_first,check_legacy_update,check_direct,check_direct_report,scoped_options,scoped_report,check_scoped,check_scoped_html,check_mechanism_pdf))+WORKER)
            target = folder / 'output.json'
            policy = '(version 1)(allow default)(deny network*)(deny file-write* (subpath ' + json.dumps(str(folder / 'installation')) + '))'
            for denied in (ROOT / 'core', ROOT / 'modules', ROOT / 'products',
                           Path.home() / 'Library/Application Support/ResearchHelper',
                           Path('/Users/haoranxu/Desktop/OptionHelper'), Path('/Users/haoranxu/Desktop/DataFetcher')):
                policy += '(deny file-read* file-write* (subpath ' + json.dumps(str(denied)) + '))'
            process = subprocess.run(['/usr/bin/sandbox-exec', '-p', policy,
                '/opt/anaconda3/envs/MachineLearning/bin/python', '-I', '-B', str(worker),
                str(folder / 'installation/researchhelper'), str(folder / 'project'), str(wire), str(target)],
                capture_output=True, text=True, timeout=60)
            (output / 'skill-worker.log').write_text(process.stdout + process.stderr)
            if process.returncode:
                raise RuntimeError('Independent Skill source replay failed; see skill-worker.log')
            skill = json.loads(target.read_text())
            assert skill['task'] == app_task
            assert skill['outlines']==outlines
            assert skill['legacy_update']==legacy_update
            assert skill['direct']==direct_results
            assert skill['question_scopes']==question_scopes
            assert skill['mechanism_pdf']==mechanism_pdf and skill['long_mechanism_pdf']==long_mechanism_pdf
            assert skill['linked_mechanism_pdf']==linked_mechanism_pdf
            for profile in ('standard','detailed'):
                for prefix in ('mechanism-','long-mechanism-','linked-mechanism-'):
                    (output/('skill-'+prefix+profile+'.pdf')).write_bytes(target.with_name(prefix+profile+'.pdf').read_bytes())
            for profile in ('standard', 'detailed'):
                assert check_report(skill['reports'][profile], profile) == app[profile]
        record.update(status='scoped_passed',app_skill_parity=True,skill_installation_readonly=True,result=app_task,legacy_update=legacy_update,direct=direct_results,question_scopes=question_scopes,mechanism_pdf=mechanism_pdf,long_mechanism_pdf=long_mechanism_pdf,linked_mechanism_pdf=linked_mechanism_pdf,profiles=list(app),outlines_without_data=outlines)
    except BaseException as error:
        record.update(status='failed', error=str(error))
        raise
    finally:
        runtime.stop()
        (output / 'receipt.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(dict(status=record['status'], receipt=str(output / 'receipt.json'), error=record.get('error')), ensure_ascii=False))
    return record


if __name__ == '__main__':
    run()
