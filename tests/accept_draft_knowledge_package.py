"""Packaged App and independent Skill cite the same locked mechanism, offline.

This is a supplied qualitative host answer, not a live-model quality test.
No supplier response, credential, production Store or native window is used.
"""
from pathlib import Path
import json,os,subprocess,sys,tempfile,time,zipfile
from lxml import html
from tests import ROOT
sys.path.insert(0,str(ROOT/'packaging'))
from accept_current import AppClient
from build_current import verify_current_build
from tests.accept_native_workbench_render import RenderRuntime

QUESTION='解释利润与经营现金流为什么可能不同，形成简短研究报告，不查询实时市场数据。'
WORKER=r'''
import json,socket,sys
from pathlib import Path
installation,project,report_file,output=map(Path,sys.argv[1:])
sys.path.insert(0,str(installation))
from core.session import Session

def denied(*args,**kwargs):raise RuntimeError('No external I/O in this acceptance')
socket.socket.connect=denied;socket.socket.connect_ex=denied
wire=json.loads(report_file.read_text());session=Session(project)
try:
 task=session.create(wire['question'],entry='个股',intent='解释变化',research_mode='current')
 task=session.draft(task['id'],defer_delivery=True)
 packet=session.tools(task['id'])['first_report']['knowledge_context']
 assert any(row['id']==wire['reference']['id'] and row['version']==wire['reference']['version'] for row in packet['entries'])
 task=session.draft(task['id'],report=wire['report'])
 assert task.get('delivery'),task.get('error')
 assert task['ledger']['used']['model_calls']==task['ledger']['used']['data_requests']==0
 result=dict(refs=task['draft_report']['logics'][0]['knowledge_refs'],reports={})
 for profile in ('standard','detailed'):
  result['reports'][profile]=session.export(task['id'],profile=profile).read_text()
 output.write_text(json.dumps(result,ensure_ascii=False))
finally:session.agent.close_runtime()
'''


def check_report(text,entry,profile):
    tree=html.document_fromstring(text);main=tree.xpath('//main')[0].text_content()
    document=json.loads(tree.get_element_by_id('rh-publication-document').text)
    assert document['template']['id']=='report-'+profile
    if profile=='standard':
        assert entry['title'] not in main
        assert entry['content'] in tree.text_content()
    else:
        for key in ('content','conditions','counterexample','source'):assert entry[key] in main
    assert not tree.xpath('//script[@src]|//link[@href]')
    return dict(dataset=document['dataset'],template=document['template'],theory_in_main=entry['content'] in main)


def run(candidate_path=None):
    selected=Path(candidate_path or os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve()
    candidate=verify_current_build(selected,ROOT)
    parent=ROOT/'result/acceptance/draft-knowledge/packaged';parent.mkdir(parents=True,exist_ok=True)
    output=Path(tempfile.mkdtemp(prefix=selected.parent.name+'-',dir=parent))
    record=dict(status='running',candidate=str(selected),model_calls=0,data_requests=0,
        production_store_touched=False,native_window_opened=False,host_answer_supplied=True,
        real_model_quality_verified=False,provider_permission_verified=False)
    runtime=RenderRuntime(output,record,candidate_path=selected,discard_temporary_store=True)
    try:
        client=AppClient(runtime.start())
        catalogue=client.bootstrap['knowledge']
        entries=catalogue['entries'] if isinstance(catalogue,dict) else catalogue
        entry=next(row for row in entries if row['id']=='rh_k_cash_profit')
        reference={key:entry[key] for key in ('id','version')}
        report=dict(title='利润与现金流差异',conclusion='利润确认与现金收付时点可能不同，应区分非现金项目和营运资金变化。',
            logics=[dict(title='区分确认与收付',analysis='先核对确认口径，再检查非现金项目和营运资金变化，不能把余额变化直接当作现金流贡献。',
                implication='现金兑现判断取决于同主体、同期间与可比口径。',risk='缺少调节表时不能确认主导原因。',knowledge_refs=[reference])],
            risks=['通用机制不代表任何具体公司已经发生相应变化。'])
        task=client.call('/api/tasks',dict(question=QUESTION,entry='个股',intent='解释变化',research_mode='current'))
        execution={key:client.bootstrap['execution'][key] for key in ('mode','policy_version')}
        client.call('/api/tasks/'+task['id']+'/start',dict(expected_execution=execution,host_result=dict(data_requests=[],draft_report=report)))
        end=time.monotonic()+30
        while time.monotonic()<end:
            task=client.call('/api/tasks/'+task['id'])
            if task.get('delivery'):break
            if task['status'] not in ('运行中','待开始'):raise RuntimeError(task.get('error') or task['status'])
            time.sleep(.1)
        assert task.get('delivery'),task.get('error')
        assert task['ledger']['used']['model_calls']==task['ledger']['used']['data_requests']==0
        app={profile:check_report(client.call('/api/tasks/'+task['id']+'/report?profile='+profile,text=True),entry,profile)
             for profile in ('standard','detailed')}
        app_refs=task['draft_report']['logics'][0]['knowledge_refs'];assert len(app_refs)==1
        with tempfile.TemporaryDirectory(prefix='skill-',dir=output) as folder:
            folder=Path(folder)
            with zipfile.ZipFile(candidate['artifacts']['skill']['path']) as archive:archive.extractall(folder/'installation')
            wire=folder/'host-answer.json';wire.write_text(json.dumps(dict(question=QUESTION,reference=reference,report=report),ensure_ascii=False))
            script=folder/'worker.py';script.write_text(WORKER)
            result=folder/'result.json'
            policy='(version 1)(allow default)(deny network*)'
            for denied in (ROOT/'core',ROOT/'modules',ROOT/'products',Path.home()/'Library/Application Support/ResearchHelper',Path('/Users/haoranxu/Desktop/OptionHelper'),Path('/Users/haoranxu/Desktop/DataFetcher')):
                policy+='(deny file-read* file-write* (subpath '+json.dumps(str(denied))+'))'
            process=subprocess.run(['/usr/bin/sandbox-exec','-p',policy,'/opt/anaconda3/envs/MachineLearning/bin/python','-I','-B',str(script),str(folder/'installation/researchhelper'),str(folder/'project'),str(wire),str(result)],capture_output=True,text=True,timeout=60)
            (output/'skill-worker.log').write_text(process.stdout+process.stderr)
            if process.returncode:raise RuntimeError('Independent Skill failed; see skill-worker.log')
            skill=json.loads(result.read_text())
            assert skill['refs']==app_refs
            for profile in ('standard','detailed'):assert check_report(skill['reports'][profile],entry,profile)==app[profile]
        record.update(status='scoped_passed',knowledge_reference=app_refs[0],app_skill_parity=True,profiles=app)
    except BaseException as error:
        record.update(status='failed',error=str(error));raise
    finally:
        runtime.stop();(output/'receipt.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(dict(status=record['status'],receipt=str(output/'receipt.json'),error=record.get('error')),ensure_ascii=False))
    return record


if __name__=='__main__':run()
