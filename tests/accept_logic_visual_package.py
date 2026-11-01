"""Replay retained observations through the actual DMG service and isolated Skill.

Host-edited prose and recorded snapshots are inputs, never claimed as a new
model research run. Both products must produce the same four-chart projection, including the full retained market window.
"""
from pathlib import Path
import base64,hashlib,inspect,json,os,subprocess,sys,tempfile,zipfile,unicodedata
from io import BytesIO
from pypdf import PdfReader
from lxml import html,etree
from tests import ROOT
sys.path.insert(0,str(ROOT/'packaging'))
from accept_current import AppClient
from build_current import verify_current_build
from tests.accept_native_workbench_render import RenderRuntime

FOLDER=ROOT/'result/acceptance/window-and-reconciliation/packaged'
SOURCE=ROOT/'result/reports/maotai-20261031/reviewed/content.json'
SENTINEL='source-fixture-sentinel'


def report_copy(content):
    return {key:content['draft'][key] for key in ('title','conclusion','logics','risks','reader_notes','workpaper_notes')}


def check_report(text,content,profile):
    assert SENTINEL not in text, 'The package must render the named profile itself'
    tree=html.document_fromstring(text)
    document=json.loads(tree.xpath('//script[@id="rh-publication-document"]')[0].text)
    assert document['template']['id']=='report-'+profile
    counts={key:row['figures'] for key,row in document['figure_coverage'].items()}
    assert counts=={'logic-1':1,'logic-2':1,'logic-3':2},counts
    assert len(document['dataset']['rows'])==len(content['draft']['facts'])==40
    assert len(tree.xpath('//main//figure'))==4
    coverage=document['figure_coverage']['logic-3']
    assert set(coverage['argument_data_refs'])==set(content['draft']['logics'][2]['data_refs'])
    assert len(coverage['context_data_refs'])==30
    charts=[block['spec'] for block in document['blocks'] if block['kind']=='chart']
    windows={}
    for field in ('close','ths_pe_ttm_stock'):
        spec=next(row for row in charts if row.get('field')==field)
        facts=sorted((row for row in content['draft']['facts'] if row['field']==field),key=lambda row:row['period'])
        assert spec['dates']==[row['period'] for row in facts]
        assert spec['values']==[row['value'] for row in facts]
        assert spec['point_refs']==[[row['id']] for row in facts]
        assert len(spec['values'])==17 and spec['auto_visual']['scheme']=='line'
        assert len(spec['plot_context']['context_data_refs'])==15
        assert spec['plot_context']['source_ref']==facts[0]['artifact_id']
        windows[field]={key:spec[key] for key in ('dates','values','point_refs','plot_context')}
    if profile=='detailed':
        tables=[block for block in document['blocks'] if block['kind']=='table' and
            block['rows'][0]==['对象与指标','观测期间','原值','单位']]
        assert len(tables)==3 and all(len(row['rows'])==5 for row in tables)
    return dict(coverage=document['figure_coverage'],dataset=document['dataset'],charts=4,windows=windows)


def check_pdf(data,content,profile):
    from io import BytesIO
    from pypdf import PdfReader
    import unicodedata
    def key(value):
        value=unicodedata.normalize('NFKC',value).translate(str.maketrans({'⻅':'见','⻓':'长','⻛':'风'}))
        return ''.join(value.split()).replace(',','')
    pages=PdfReader(BytesIO(data)).pages
    assert all(abs(float(page.mediabox.width)-595.276)<1 and abs(float(page.mediabox.height)-841.89)<1 for page in pages)
    text=key(''.join(page.extract_text() for page in pages))
    assert all(label in text for label in ('策略逻辑一','策略逻辑二','策略逻辑三','风险提示'))
    if profile=='standard':assert len(pages)==1
    else:
        for row in content['draft']['facts']:
            value=row.get('raw_value',row.get('value'))
            if value is not None:assert key(str(value)) in text, row['id']
    table_pages=[]
    if profile=='detailed':
        for index,page in enumerate(pages,1):
            page_text=key(page.extract_text())
            # Raw market rows are in the long ledger, beyond the narrative plots.
            if '600519.SH' in page_text and any(str(row['period']) in page_text and key(str(row.get('raw_value',row.get('value')))) in page_text for row in content['draft']['facts'] if row['field']=='close'):
                assert all(label in page_text for label in ('标的','指标','期间','原值','单位')),index
                table_pages.append(index)
        assert len(table_pages)>=2,table_pages
    return dict(pages=len(pages),a4=True,text_characters=len(text),all_frozen_values_visible=profile=='detailed',table_pages_with_headers=table_pages)


def check_paper_width(path):
    raw=subprocess.check_output(['/opt/homebrew/bin/pdftotext','-bbox',str(path),'-'],stderr=subprocess.DEVNULL)
    tree=etree.fromstring(raw);pages=tree.xpath('//*[local-name()="page"]')
    words=pages[0].xpath('.//*[local-name()="word"]')
    left=min(float(word.get('xMin')) for word in words);right=max(float(word.get('xMax')) for word in words)
    assert left<30 and right>560,(left,right)
    return dict(content_left=left,content_right=right,full_a4_width=True)


def seed_copy(archive,task,content,resources,marker=None):
    """Seed a clearly synthetic saved-report fixture, not a data-acquisition test."""
    from copy import deepcopy
    from researchhelper.drafts import source_binding
    from researchhelper.reports import compose,render
    task=archive.get(task['id']);task['synthetic']=True
    task['source_records']=deepcopy(content['source_records']);task['materials']=deepcopy(content.get('materials',[]))
    for record in task['source_records']+task['materials']:
        record.update(task_id=task['id'],research_version=task['version'])
    task['draft_report']=deepcopy(content['draft']);task['draft_report']['source_binding']=source_binding(task)
    task['draft_report']['origin']='retained_report_fixture';task['gaps']=['离线呈现回放，不代表新增研究或下载。']
    task=archive.save(task);frozen=compose(task);text=render(frozen,resources)
    if marker:text+='<!-- '+marker+' -->'
    task['pending_html']=text;task=archive.save(task)
    return archive.deliver(task,frozen,text)


WORKER=r'''
import json,socket,sys
from pathlib import Path
install,project,source,out=map(Path,sys.argv[1:]);sys.path.insert(0,str(install))
from core.session import Session

def denied(*a,**k):raise RuntimeError('Offline replay only')
socket.socket.connect=denied;socket.socket.connect_ex=denied
content=json.loads(source.read_text());s=Session(project)
t=s.create(content['question'],entry=content['entry'],intent=content['intent'],objects=content['objects'],research_mode='current')
t=seed_copy(s.archive,t,content,s.agent.resources,marker='source-fixture-sentinel')
assert t.get('delivery'),t.get('error')
assert t['ledger']['used']['model_calls']==t['ledger']['used']['data_requests']==0
result={'reports':{},'pdf':{}}
for profile in ('standard','detailed'):
 file=s.export(t['id'],profile=profile);result['reports'][profile]=file.read_text()
 pdf=s.export(t['id'],profile=profile,format='pdf');receipt=json.loads((pdf.parent/'receipt.json').read_text())
 assert receipt['conversion_engine']=='webkit'
 result['pdf'][profile]=dict(check_pdf(pdf.read_bytes(),content,profile),temporary_path=str(pdf))
out.write_text(json.dumps(result,ensure_ascii=False))
'''


def run(candidate_path=None):
    selected=Path(candidate_path or os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve()
    candidate=verify_current_build(selected,ROOT)
    content=json.loads(SOURCE.read_text());before=SOURCE.read_bytes()
    FOLDER.mkdir(parents=True,exist_ok=True)
    output=Path(tempfile.mkdtemp(prefix=selected.parent.name+'-',dir=FOLDER))
    record=dict(status='running',candidate=str(selected),model_calls=0,real_data_requests=0,
        seeded_saved_report_fixture=True,source_origin='host_editorial_review',
        raw_source_import_verified=False,new_research_quality_verified=False,
        native_window_opened=False,production_store_touched=False)
    runtime=RenderRuntime(output,record,candidate_path=selected,discard_temporary_store=True)
    try:
        client=AppClient(runtime.start())
        task=client.call('/api/tasks',dict(question=content['question'],entry=content['entry'],intent=content['intent'],objects=content['objects'],research_mode='current'))
        from researchhelper.archive import ResearchArchive
        current=seed_copy(ResearchArchive(output/'store'),task,content,ROOT,marker=SENTINEL)
        assert current['ledger']['used']['model_calls']==current['ledger']['used']['data_requests']==0
        app={}
        for profile in ('standard','detailed'):
            text=client.call('/api/tasks/'+task['id']+'/report?profile='+profile,text=True)
            app[profile]=check_report(text,content,profile)
            (output/('app-'+profile+'.html')).write_text(text)
        record['pdf']={}
        for profile in ('standard','detailed'):
            exported=client.call('/api/export',dict(task_id=task['id'],profile=profile,format='pdf'))
            pdf=base64.b64decode(exported['content_base64'],validate=True)
            assert hashlib.sha256(pdf).hexdigest()==exported['sha256'] and pdf.startswith(b'%PDF-')
            path=output/('app-'+profile+'.pdf');path.write_bytes(pdf)
            record['pdf'][profile]=dict(check_pdf(pdf,content,profile),sha256=exported['sha256'],path=str(path))
            if profile=='standard':record['pdf'][profile].update(check_paper_width(path))
        with tempfile.TemporaryDirectory(prefix='skill-',dir=output) as directory:
            workspace=Path(directory);install=workspace/'installation'
            with zipfile.ZipFile(candidate['artifacts']['skill']['path']) as archive:archive.extractall(install)
            worker=workspace/'replay.py';worker.write_text(inspect.getsource(seed_copy)+'\n'+inspect.getsource(check_pdf)+'\n'+WORKER);out=workspace/'skill.json'
            policy='(version 1)(allow default)(deny network*)(deny file-write* (subpath '+json.dumps(str(install))+'))'
            for path in (ROOT/'core',ROOT/'modules',ROOT/'products',ROOT/'.skills',Path.home()/'Library/Application Support/ResearchHelper',Path('/Users/haoranxu/Desktop/OptionHelper'),Path('/Users/haoranxu/Desktop/DataFetcher')):
                policy+='(deny file-read* file-write* (subpath '+json.dumps(str(path))+'))'
            result=subprocess.run(['/usr/bin/sandbox-exec','-p',policy,sys.executable,'-I','-B',str(worker),str(install/'researchhelper'),str(workspace/'project'),str(SOURCE),str(out)],cwd=workspace,capture_output=True,text=True,timeout=60)
            (output/'skill-worker.log').write_text(result.stdout+result.stderr)
            if result.returncode:raise RuntimeError('Isolated Skill replay failed; see skill-worker.log')
            wire=json.loads(out.read_text())
            skill={profile:check_report(text,content,profile) for profile,text in wire['reports'].items()}
            for profile in ('standard','detailed'):
                pdf=Path(wire['pdf'][profile].pop('temporary_path')).resolve()
                assert pdf.is_relative_to(workspace/'project')
                if profile=='standard':wire['pdf'][profile].update(check_paper_width(pdf))
            record['skill_pdf']=wire['pdf'];record['skill_installation_readonly']=True
            for profile in ('standard','detailed'):assert wire['pdf'][profile]['pages']==record['pdf'][profile]['pages']
            for profile in ('standard','detailed'):assert app[profile]==skill[profile]
        assert SOURCE.read_bytes()==before
        record.update(status='scoped_passed',profiles={profile:dict(charts=value['charts'],coverage=value['coverage'],observations=len(value['dataset']['rows']),windows=value['windows']) for profile,value in app.items()},
            app_skill_same_projection=True,actual_packaged_renderers_verified=True,frozen_source_unchanged=True)
    except BaseException as error:
        record.update(status='failed',error=str(error));raise
    finally:
        runtime.stop();path=output/'receipt.json';path.write_text(json.dumps(record,ensure_ascii=False,indent=2))
        print(json.dumps(dict(status=record['status'],receipt=str(path),model_calls=0,real_data_requests=0,error=record.get('error')),ensure_ascii=False))
    return record

if __name__=='__main__':run()
