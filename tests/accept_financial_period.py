"""App/Skill report flow over a controlled, unapproved raw-response fixture."""
from copy import deepcopy
from pathlib import Path
import base64,hashlib,json,os,shutil,subprocess,sys,tempfile,threading,time,zipfile
from lxml import html
from tests import ROOT
sys.path.insert(0,str(ROOT/'packaging'))
from accept_current import AppClient
from tests.accept_native_workbench_render import RenderRuntime
from tests.accept_installed_report_delivery import task as production_task
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError,context,fingerprint,now
from researchhelper.draft_sources import VERSION as READER_VERSION
from researchhelper.service import make_server


def seed_fixture(archive,ident,wire):
    """Prepare unapproved test inputs, not a vendor acquisition or import pass."""
    from researchhelper.financial_sources import metadata_response,validate_metadata_request
    current=archive.get(ident);current['synthetic']=True;current['messages'][0]['synthetic']=True
    records=[];mapping={}
    for source in wire['records']:
        request={key:deepcopy(source[key]) for key in ('source','operation','parameters')}
        payload=deepcopy(source['response']);digest=fingerprint(payload);request_hash=fingerprint(request)
        if source['operation']=='basic_data_service':metadata_response(source['operation'],source['parameters'],payload,allow_partial=True)
        ident='rh_source_'+fingerprint([current['id'],current['version'],request_hash,digest])[:24]
        record=dict(id=ident,kind='api',**request,snapshot=payload,hash=digest,request_hash=request_hash,
            content_version=1,task_id=current['id'],research_version=current['version'],obtained_at=source['obtained_at'],imported_at=now(),
            status='成功待核验',source_check='待核验',applicability_check='待核验',fixture_unapproved=True)
        records.append(record);mapping[source['old_id']]=ident
    current['source_records']=records;archive.save(current)
    # Copied policy bytes must never grant permission to dispatch a real call.
    financial=next(row for row in records if row['operation']=='basic_data_service')
    try:validate_metadata_request(current,'basic_data_service',financial['parameters'])
    except ContractError as error:assert '实际账号策略' in str(error)
    else:raise AssertionError('Raw fixture minted acquisition authority')
    return current,mapping


def metrics(current,wire):
    assert current['draft_source_reader_version']==READER_VERSION and current.get('delivery')
    assert all(row['task_id']==current['id'] and row['research_version']==current['version'] for row in current['source_records'])
    assert all(row['hash']==fingerprint(row['snapshot']) for row in current['source_records'])
    assert [row['snapshot'] for row in current['source_records']]==[row['response'] for row in wire['records']]
    facts=current['draft_report']['facts'];calculations=current['draft_report']['calculations']
    selected=[{key:row[key] for key in ('field','code','period','raw_value','unit')} for row in facts]
    assert selected==wire['expected_facts']
    assert [{key:row[key] for key in ('method','display','period','unit')} for row in calculations]==wire['expected_calculations']
    assert current['ledger']['used']['model_calls']==current['ledger']['used']['data_requests']==0
    assert not any(row['financial_approval'] for row in calculations)
    financial=[row for row in facts if row.get('period_basis')]
    assert len(financial)==6 and {row['period'] for row in financial}=={'20241231','20251231'}
    return dict(facts=len(facts),calculations=len(calculations),financial_periods=['20241231','20251231'],
        financial_observations=6,new_model_calls=0,new_data_requests=0,financial_approval=False)


def publication(raw,source_aliases):
    tree=html.document_fromstring(raw);doc=json.loads(tree.get_element_by_id('rh-publication-document').text)
    assert len(doc['dataset']['rows'])==40
    charts=[block for block in doc['blocks'] if block['kind']=='chart'];assert len(charts)==4
    assert tree.xpath('//main') and not tree.xpath('//main//details')
    # Compare semantic inputs across independent task namespaces. Every actual
    # plot reference must first belong to this task's own copied input record.
    for block in charts:
        plot=block.get('spec',{}).get('plot_context',{})
        if 'source_ref' in plot:
            assert plot['source_ref'] in source_aliases,'Plot references another task source'
            plot['source_ref']=source_aliases[plot['source_ref']]

    blocks=doc['blocks']
    assert blocks[1]['kind']=='paragraph' and blocks[1]['text'].startswith('北京时间')
    return dict(dataset=doc['dataset'],blocks=blocks[:1]+blocks[2:])


WORKER=r'''
from pathlib import Path
import json,sys
from copy import deepcopy
from lxml import html
installation,project,input_path,target=map(Path,sys.argv[1:]);sys.path.insert(0,str(installation))
from core.session import Session
from researchhelper.contracts import fingerprint,now,ContractError
from researchhelper.draft_sources import VERSION as READER_VERSION
session=Session(project)
try:
    wire=json.loads(input_path.read_text());created=session.create(wire['question'],entry='个股',objects=['600519.SH'],research_mode='current')
    current,mapping=seed_fixture(session.archive,created['id'],wire)
    report=deepcopy(wire['report'])
    for logic in report['logics']:logic['source_refs']=[mapping[ref] for ref in logic.get('source_refs',[])]
    current=session.draft(created['id'],report=report,data_requests=[])
    aliases={mapping[row['old_id']]:'input-source-'+str(i+1) for i,row in enumerate(wire['records'])}
    result=dict(metrics=metrics(current,wire),reports={})
    for profile in ('standard','detailed'):
        result['reports'][profile]=publication(session.export(created['id'],profile=profile).read_bytes(),aliases)
    assert all(record['research_version']==1 and record['task_id']==created['id'] for record in current['source_records'])
    target.write_text(json.dumps(result,ensure_ascii=False))
finally:session.agent.close_runtime()
'''


def independent_skill(candidate,base,output,wire,expected):
    import inspect
    from build_current import verify_current_build
    selected=verify_current_build(candidate,ROOT);artifact=selected['artifacts']['skill'];archive=Path(artifact['path'])
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==artifact['sha256']
    install=base/'installation'
    with zipfile.ZipFile(archive) as source:source.extractall(install)
    def inventory():return {str(p.relative_to(install)):hashlib.sha256(p.read_bytes()).hexdigest() for p in install.rglob('*') if p.is_file()}
    before=inventory();worker=base/'skill-worker.py';worker.write_text('\n'.join(inspect.getsource(fn) for fn in (seed_fixture,metrics,publication))+WORKER)
    source=base/'input.json';source.write_text(json.dumps(wire,ensure_ascii=False));target=base/'skill-result.json'
    policy='(version 1)(allow default)(deny network*)(deny file-write* (subpath '+json.dumps(str(install))+'))'
    for path in (ROOT/'core',ROOT/'modules',ROOT/'products',ROOT/'.skills',Path.home()/'Library/Application Support/ResearchHelper',Path('/Users/haoranxu/Desktop/OptionHelper'),Path('/Users/haoranxu/Desktop/DataFetcher')):
        policy+='(deny file-read* file-write* (subpath '+json.dumps(str(path))+'))'
    done=subprocess.run(['/usr/bin/sandbox-exec','-p',policy,sys.executable,'-I','-B',str(worker),str(install/'researchhelper'),
        str(base/'project'),str(source),str(target)],cwd=base,capture_output=True,text=True,timeout=90)
    (output/'skill.log').write_text(done.stdout+done.stderr)
    assert done.returncode==0,'Independent Skill financial-period case failed; inspect skill.log'
    actual=json.loads(target.read_text())
    if actual!=expected:
        differences=[]
        def compare(left,right,path=''):
            if len(differences)>=24:return
            if type(left)!=type(right):differences.append(dict(path=path,app=left,skill=right));return
            if isinstance(left,dict):
                if set(left)!=set(right):differences.append(dict(path=path,app_keys=list(left),skill_keys=list(right)));return
                for key in left:compare(left[key],right[key],path+'/'+str(key))
            elif isinstance(left,list):
                if len(left)!=len(right):differences.append(dict(path=path,app_length=len(left),skill_length=len(right)))
                for index,(a,b) in enumerate(zip(left,right)):compare(a,b,path+'/'+str(index))
            elif left!=right:differences.append(dict(path=path,app=left,skill=right))
        compare(expected,actual)
        (output/'parity-diff.json').write_text(json.dumps(differences,ensure_ascii=False,indent=2)+'\n')
        raise AssertionError('App/Skill dataset, calculations or report blocks differ; inspect parity-diff.json')
    assert inventory()==before
    return dict(status='passed',installation_readonly=True,network_denied=True,repository_imports_denied=True,
        app_skill_report_parity=True,comparison_normalizes_only_generation_header_and_task_owned_source_ids=True,model_calls=0,data_requests=0)


def run():
    candidate=os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
    default=ROOT/'result/acceptance/financial-period-20261101'/('package' if candidate else 'source')
    output=Path(os.environ.get('RESEARCHHELPER_FINANCIAL_PERIOD_QA_OUTPUT',str(default))).resolve()
    if not output.is_relative_to(ROOT/'result/acceptance'):raise ValueError('Financial-period QA output must stay in this project acceptance directory')
    output.mkdir(parents=True,exist_ok=True)
    wire=json.loads((ROOT/'result/acceptance/financial-period-20261101/input.json').read_text());original_fingerprint=fingerprint(production_task())
    record=dict(status='running',model_calls=0,data_requests=0,pdf_downloads=0,production_store_touched=False,
        native_window_opened=False,synthetic_response_time_case=True,retained_real_values=True,real_model_research=False,
        scope='Raw-fixture reader, calculation, report and export acceptance; not a new vendor acquisition or import-interface pass')
    runtime=server=thread=None
    try:
        with tempfile.TemporaryDirectory(dir=output) as temporary:
            base=Path(temporary)
            try:
                if candidate:
                    runtime=RenderRuntime(base,record,candidate_path=candidate,discard_temporary_store=True);url=runtime.start().rstrip('/')
                else:
                    server=make_server(base/'store',ROOT,offline=True,authentication=True,preset_accounts=True,runtime_configurable=True)
                    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url='http://127.0.0.1:'+str(server.server_port)
                    record['backend_mode']='development_source'
                client=AppClient(url);archive=ResearchArchive(base/'store')
                created=client.call('/api/tasks',dict(question=wire['question'],entry='个股',objects=['600519.SH'],research_mode='current'))
                current,mapping=seed_fixture(archive,created['id'],wire)
                report=deepcopy(wire['report'])
                for logic in report['logics']:logic['source_refs']=[mapping[ref] for ref in logic.get('source_refs',[])]
                execution={key:client.bootstrap['execution'][key] for key in ('mode','policy_version')}
                client.call('/api/tasks/'+created['id']+'/start',dict(expected_task=context(current),expected_execution=execution,
                    host_result=dict(draft_report=report,data_requests=[])))
                deadline=time.monotonic()+35
                while time.monotonic()<deadline:
                    current=client.call('/api/tasks/'+created['id'])
                    if current.get('delivery'):break
                    if current['status'] not in ('待开始','运行中'):raise RuntimeError(current.get('error') or current['status'])
                    time.sleep(.05)
                aliases={mapping[row['old_id']]:'input-source-'+str(i+1) for i,row in enumerate(wire['records'])}
                expected=dict(metrics=metrics(current,wire),reports={})
                for profile in ('standard','detailed'):
                    result=client.call('/api/export',dict(task_id=current['id'],profile=profile,format='html'))
                    raw=base64.b64decode(result['content_base64'],validate=True);assert hashlib.sha256(raw).hexdigest()==result['sha256']
                    expected['reports'][profile]=publication(raw,aliases)
                exported=client.call('/api/export',dict(task_id=current['id'],profile='standard',format='pdf'))
                raw=base64.b64decode(exported['content_base64'],validate=True);assert hashlib.sha256(raw).hexdigest()==exported['sha256']
                pdf=base/'standard.pdf';pdf.write_bytes(raw)
                from pypdf import PdfReader
                pages=PdfReader(pdf).pages;assert len(pages)==1 and abs(float(pages[0].mediabox.width)-595.28)<1
                tool=shutil.which('pdftoppm')
                if not tool:
                    binaries=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/bin'
                    tool=next(str(binaries/name/'pdftoppm') for name in ('override','fallback') if (binaries/name/'pdftoppm').is_file())
                subprocess.run([tool,'-f','1','-singlefile','-scale-to','1500','-png',str(pdf),str(output/'paper')],capture_output=True,check=True,timeout=30)
                if candidate:record['independent_skill']=independent_skill(candidate,base,output,wire,expected)
                assert archive.report(current['id'],1)['content']['draft_source_reader_version']==READER_VERSION
                record.update(status='scoped_passed',metrics=expected['metrics'],report_profiles=2,charts=4,standard_pdf_pages=1,
                    source_snapshot_bytes_preserved=True,source_ownership_bound=True,financial_quality_accepted=False,source_units_unverified=True)
            finally:
                if runtime:runtime.stop()
                if server:server.shutdown();server.server_close();thread.join(timeout=10);record['service_stopped']=not thread.is_alive()
        assert fingerprint(production_task())==original_fingerprint;record['production_task_unchanged']=True
        record['temporary_store_removed']=not Path(temporary).exists()
    except BaseException as error:record.update(status='failed',error_type=type(error).__name__,error=str(error));raise
    finally:
        receipt=output/'receipt.json';receipt.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(dict(status=record['status'],receipt=str(receipt))),flush=True)


if __name__=='__main__':run()
