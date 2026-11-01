"""App/Skill regression over an authored NAV series and one retained NAV.

No model or supplier call. The authored series is explicitly synthetic; the
retained NAV is a presentation replay, not a new download or financial approval.
"""
from pathlib import Path
from copy import deepcopy
import base64,hashlib,inspect,json,os,subprocess,sys,tempfile,time,zipfile
from lxml import html
from tests import ROOT
sys.path.insert(0,str(ROOT/'packaging'))
from accept_current import AppClient
from build_current import verify_current_build
from tests.accept_native_workbench_render import RenderRuntime


def inputs():
    from tests.test_draft_arithmetic import nav_fixture
    from tests.test_draft_source_types import NAV
    from researchhelper.etf_sources import parse_snapshot
    task=nav_fixture('ths_unit_nvnf_fund')
    report=dict(title='单位净值读数观察',conclusion='比较单位净值读数，不推断持有人总回报。',
        logics=[dict(title='净值变化与实际回报分开',analysis='沿用实际起终与中间读数，不补分红或现金流。',
            risk='分红和份额折算可能影响净值读数。',data_refs=['D1','D3'],calculation_refs=['A1'])],risks=['单位和完整收益口径仍需分别核对。'])
    result=[dict(id='nav_series',question=task['question'],entry='ETF',intent='解释变化',objects=task['objects'],
        cutoff='2026-10-30T16:00:00+08:00',records=task['source_records'],report=report)]
    snapshot=parse_snapshot(NAV.read_bytes(),'510300.SH','2026-10-27T00:00:00+08:00')
    result.append(dict(id='retained_nav',question='核对已取得的ETF单位净值，不推断区间收益',entry='ETF',intent='解释变化',
        objects=['510300.SH'],cutoff='2026-10-30T16:00:00+08:00',records=[dict(id='retained-nav',kind='api',source='official',
        operation='etf_pcf',parameters=dict(ts_code='510300.SH'),snapshot=snapshot,status='成功待核验')],
        report=dict(title='单点净值观察',conclusion='只有一个净值所属日，不据此计算区间表现。',
        logics=[dict(title='保留当前读数',analysis='区分净值所属日与公布日；不补造序列。',data_refs=['D1'])],risks=['单点读数不足以识别区间收益。'])))
    comparison=nav_fixture('ths_unit_nvnf_fund',values=['4.0000',None,'4.4000'])
    second=nav_fixture('ths_unit_nvnf_fund',values=['7.0000','7.1000','7.2000'],code='510500.SH')
    for record in second['source_records']:record['id']='second-'+record['id']
    comparison['source_records'].extend(second['source_records'])
    result.append(dict(id='nav_comparison',question='分别观察两只ETF单位净值读数，不比较净值高低或推断持有人收益',entry='ETF',intent='比较对象',
        objects=['510300.SH','510500.SH'],cutoff='2026-10-30T16:00:00+08:00',records=comparison['source_records'],
        report=dict(title='两只ETF净值读数观察',conclusion='分别核对两个对象，不据净值高低比较基金优劣。',
            logics=[dict(title='对象分别定位，缺失点留空',analysis='两条序列分别保留实际观察；净值所属日一致不代表收益口径已经统一。',
                data_refs=['D'+str(i) for i in range(1,7)],calculation_refs=['A1','A2','A3','A4'])],risks=['读数变化不代表分红再投资总回报。'])))
    return result


def seed(archive,task,packet):
    from researchhelper.contracts import fingerprint
    task=archive.get(task['id']);task['synthetic']=True
    task['messages'][0]['synthetic']=True
    task['source_records']=deepcopy(packet['records'])
    for record in task['source_records']:
        record.update(task_id=task['id'],research_version=task['version'],input_hash=task['input_hash'])
        record['hash']=fingerprint(record['snapshot'])
        record['request_hash']=fingerprint({key:record[key] for key in ('source','operation','parameters')})
    return archive.save(task)


def check(task,packet):
    assert task.get('delivery'),(packet['id'],task.get('error'),task.get('gaps'))
    assert task['ledger']['used']['model_calls']==task['ledger']['used']['data_requests']==0
    draft=task['draft_report'];facts=draft['facts'];calculations=draft['calculations']
    if packet['id'] in ('nav_series','nav_comparison'):
        change=next(row for row in calculations if row['method']=='raw_change_v1')
        assert change['display']=='10.00' and change['conditional_equal_scale']
        assert change['inputs']==dict(a='4.0000',b='4.4000')
        assert '不等于持有人总回报' in change['condition']
        if packet['id']=='nav_series':
            assert len(facts)==3 and len(calculations)==2 and facts[1]['raw_value']=='3.8000'
        else:
            assert len(facts)==6 and len(calculations)==4 and facts[1]['raw_value'] is None
            assert {row['code'] for row in facts}=={'510300.SH','510500.SH'}
    else:
        assert len(facts)==1 and not calculations
        assert (facts[0]['period'],facts[0]['raw_value'],facts[0]['unit'])==('2026-10-21','4.1856','元')
    return dict(facts=[{key:row[key] for key in ('id','field','period','raw_value','unit')} for row in facts],
        calculations=[{key:row[key] for key in ('id','method','display','unit','condition','data_refs')} for row in calculations],
        guidance=[row['id'] for row in draft['interpretation_context']['rules']])


def projection(text,packet,profile):
    tree=html.document_fromstring(text);doc=json.loads(tree.get_element_by_id('rh-publication-document').text)
    assert doc['template']['id']=='report-'+profile
    main=tree.find('.//main').text_content()
    assert '元元' not in main and '%/年%/年' not in main and '百分点/年百分点/年' not in main
    charts=[row['spec'] for row in doc['blocks'] if row['kind']=='chart']
    if packet['id']=='nav_series':
        plot=next(row for row in charts if row.get('field')=='ths_unit_nvnf_fund')
        assert plot['values']==[4,3.8,4.4] and plot['point_refs']==[['D1'],['D2'],['D3']]
    if packet['id']=='retained_nav':
        assert not any(row.get('type')=='line' and len(row.get('values',[]))>1 for row in charts)
        assert '4.1856' in main and '2026-10-21' in main
        table=tree.xpath('//main//div[contains(@class,"argument-observations")]//table')
        assert len(table)==1 and table[0].xpath('.//tr[@data-observation-id="D1"]')
        assert table[0].xpath('.//a[@aria-haspopup="dialog"]')
        assert doc['figure_coverage']['logic-1']['status']=='numeric_table'
        assert doc['figure_coverage']['logic-1']['data_refs']==['D1']
    assert 'official' not in main and 'None' not in main
    if packet['id']=='nav_comparison':
        assert len(charts)==2
        assert sorted([row['values'] for row in charts],key=lambda row:row[0])==[[4,None,4.4],[7,7.1,7.2]]
        if profile=='detailed':
            table=tree.xpath('//main//*[@data-logic-detail="logic-1"]//table')[0]
            assert [row.text_content() for row in table.xpath('.//tbody/tr/td[1]')]==['510300.SH：单位净值']*3+['510500.SH：单位净值']*3
            assert '未返回' in table.text_content()
            assert '不等于持有人总回报' in main
            for code in ('510300.SH','510500.SH'):
                assert code+'：单位净值变化，2026-10-20至2026-10-22' in main
            assert '原始单位未明确，比较以各输入尺度一致为前提。' in main
    return dict(dataset=doc['dataset'],charts=len(charts),text=main)


def check_pdf(data,packet,profile):
    from io import BytesIO
    from pypdf import PdfReader
    import unicodedata
    pages=PdfReader(BytesIO(data)).pages
    assert all(abs(float(page.mediabox.width)-595.276)<1 and abs(float(page.mediabox.height)-841.89)<1 for page in pages)
    text=''.join(unicodedata.normalize('NFKC',''.join(page.extract_text() for page in pages)).split())
    if packet['id']=='retained_nav':assert all(value in text for value in ('4.1856','2026-10-21','510300.SH'))
    if packet['id']=='nav_comparison' and profile=='detailed':
        assert all(value in text for value in ('510300.SH','510500.SH','未返回','不等于持有人总回报','4.0000','7.2000'))
    if profile=='standard':assert len(pages)==1
    return dict(pages=len(pages),a4=True,retained_single_value_visible=packet['id']=='retained_nav')


def exercise_skill(session,packets):
    result={}
    for packet in packets:
        task=session.create(**{key:packet[key] for key in ('question','entry','intent','objects','cutoff','questions') if key in packet})
        seed(session.archive,task,packet)
        task=session.draft(task['id'],report=packet['report'],data_requests=[])
        metrics=check(task,packet)
        reports={profile:projection(session.export(task['id'],profile=profile).read_text(),packet,profile)
                 for profile in ('standard','detailed')}
        pdf={profile:check_pdf(session.export(task['id'],profile=profile,format='pdf').read_bytes(),packet,profile) for profile in ('standard','detailed')}
        result[packet['id']]=dict(metrics=metrics,reports=reports,pdf=pdf)
    return result


WORKER=r'''
import base64,hashlib,json,socket,sys
from pathlib import Path
from copy import deepcopy
from lxml import html
install,project,source,out=map(Path,sys.argv[1:]);sys.path.insert(0,str(install))
from core.session import Session

def denied(*args,**kwargs):raise RuntimeError('Offline acceptance only')
socket.socket.connect=denied;socket.socket.connect_ex=denied
session=Session(project)
try:out.write_text(json.dumps(exercise_skill(session,json.loads(source.read_text())),ensure_ascii=False))
finally:session.agent.close_runtime()
'''


def run(candidate_path=None):
    selected=Path(candidate_path or os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve()
    candidate=verify_current_build(selected,ROOT)
    parent=ROOT/'result/acceptance/fund-and-number/packaged';parent.mkdir(parents=True,exist_ok=True)
    output=Path(tempfile.mkdtemp(prefix=selected.parent.name+'-',dir=parent))
    record=dict(status='running',candidate=str(selected),model_calls=0,data_requests=0,
        synthetic_contract_fixtures=True,one_retained_nav_replayed=True,real_provider_permissions_verified=False,
        financial_quality_accepted=False,native_window_opened=False,production_store_touched=False)
    runtime=RenderRuntime(output,record,candidate_path=selected,discard_temporary_store=True)
    packets=inputs();original=deepcopy(packets)
    try:
        client=AppClient(runtime.start());execution={key:client.bootstrap['execution'][key] for key in ('mode','policy_version')};app={}
        from researchhelper.archive import ResearchArchive
        for packet in packets:
            task=client.call('/api/tasks',{key:packet[key] for key in ('question','entry','intent','objects','cutoff','questions') if key in packet})
            seed(ResearchArchive(output/'store'),task,packet)
            host=dict(draft_report=packet['report'],data_requests=[])
            client.call('/api/tasks/'+task['id']+'/start',dict(expected_execution=execution,host_result=host))
            end=time.monotonic()+30
            while time.monotonic()<end:
                task=client.call('/api/tasks/'+task['id'])
                if task.get('delivery'):break
                if task['status'] not in ('运行中','待开始'):raise RuntimeError(str((packet['id'],task.get('error'),task['status'])))
                time.sleep(.05)
            metrics=check(task,packet);reports={}
            for profile in ('standard','detailed'):
                text=client.call('/api/tasks/'+task['id']+'/report?profile='+profile,text=True)
                (output/(packet['id']+'-'+profile+'.html')).write_text(text)
                reports[profile]=projection(text,packet,profile)
            pdf={}
            for profile in ('standard','detailed'):
                exported=client.call('/api/export',dict(task_id=task['id'],profile=profile,format='pdf'))
                raw=base64.b64decode(exported['content_base64'],validate=True)
                assert hashlib.sha256(raw).hexdigest()==exported['sha256']
                (output/(packet['id']+'-'+profile+'.pdf')).write_bytes(raw)
                pdf[profile]=check_pdf(raw,packet,profile)
            app[packet['id']]=dict(metrics=metrics,reports=reports,pdf=pdf)
        with tempfile.TemporaryDirectory(prefix='skill-',dir=output) as temporary:
            workspace=Path(temporary);installation=workspace/'installation'
            with zipfile.ZipFile(candidate['artifacts']['skill']['path']) as archive:archive.extractall(installation)
            source=workspace/'inputs.json';source.write_text(json.dumps(packets,ensure_ascii=False));out=workspace/'outputs.json';worker=workspace/'exercise.py'
            worker.write_text('\n'.join(inspect.getsource(func) for func in (seed,check,projection,check_pdf,exercise_skill))+WORKER)
            policy='(version 1)(allow default)(deny network*)(deny file-write* (subpath '+json.dumps(str(installation))+'))'
            for path in (ROOT/'core',ROOT/'modules',ROOT/'products',ROOT/'.skills',Path.home()/'Library/Application Support/ResearchHelper',Path('/Users/haoranxu/Desktop/OptionHelper'),Path('/Users/haoranxu/Desktop/DataFetcher')):
                policy+='(deny file-read* file-write* (subpath '+json.dumps(str(path))+'))'
            completed=subprocess.run(['/usr/bin/sandbox-exec','-p',policy,sys.executable,'-I','-B',str(worker),str(installation/'researchhelper'),str(workspace/'project'),str(source),str(out)],cwd=workspace,capture_output=True,text=True,timeout=60)
            (output/'skill.log').write_text(completed.stdout+completed.stderr)
            assert completed.returncode==0,'Independent Skill failed; see skill.log'
            skill=json.loads(out.read_text())
            for ident in app:
                assert app[ident]['metrics']==skill[ident]['metrics'],ident
                assert app[ident]['pdf']==skill[ident]['pdf'],ident
                for profile in ('standard','detailed'):
                    assert app[ident]['reports'][profile]['dataset']==skill[ident]['reports'][profile]['dataset'],(ident,profile)
        assert packets==original
        record.update(status='scoped_passed',cases={ident:dict(metrics=value['metrics'],pdf=value['pdf'],profiles={key:dict(rows=len(row['dataset']['rows']),charts=row['charts']) for key,row in value['reports'].items()}) for ident,value in app.items()},
            app_skill_same_projection=True,skill_installation_readonly=True)
    except BaseException as error:record.update(status='failed',error=str(error));raise
    finally:
        runtime.stop();path=output/'receipt.json';path.write_text(json.dumps(record,ensure_ascii=False,indent=2))
        print(json.dumps(dict(status=record['status'],receipt=str(path),error=record.get('error')),ensure_ascii=False))
    return record


if __name__=='__main__':run()
