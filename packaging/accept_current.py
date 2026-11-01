"""Post-build acceptance against the explicit DMG and independent Skill ZIP.

Uses shipped synthetic cases, not old QA snapshots, credentials, models or
supplier APIs. Native windows and the production research Store stay closed.
"""
import base64
import hashlib
import json
import os
import math
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

from build_current import verify_current_build, write_record
from build_progress import stage
from source_snapshot import digest

ROOT = Path(__file__).resolve().parents[1]
CASES = ('mechanism', 'etf', 'value')
PROFILES = ('standard', 'detailed')
FORMATS = ('html', 'pdf', 'docx', 'xlsx', 'csv')


def document(text, profile):
    from lxml import html
    tree = html.document_fromstring(text)
    nodes = tree.xpath('//script[@id="rh-publication-document"]')
    if len(nodes) != 1 or len(tree.xpath('//main')) != 1:
        raise RuntimeError('报告未包含唯一正文及冻结呈现数据')
    value = json.loads(nodes[0].text)
    if (value.get('template', {}).get('id') != 'report-' + profile or
            value.get('profile') != profile or not value.get('blocks') or
            not value.get('dataset', {}).get('rows')):
        raise RuntimeError('报告模板、数据或正文不完整')
    return value


def dataset_hash(value):
    return hashlib.sha256(json.dumps(value['dataset'], ensure_ascii=False,
                                    sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class AppClient:
    def __init__(self, base):
        self.base, self.cookie, self.token = base.rstrip('/'), '', ''
        self.token = self.call('/api/auth/status')['token']
        self.call('/api/auth/login', {'account': 'admin', 'password': '66666666', 'remember': False})
        self.bootstrap = self.call('/api/bootstrap')
        if not self.bootstrap['offline'] or self.bootstrap['tasks']:
            raise RuntimeError('包后验收未使用空白离线研究库')
        self.token = self.bootstrap['token']

    def call(self, route, payload=None, *, text=False):
        headers = {'Cookie': self.cookie}
        if payload is not None:
            headers.update({'Content-Type': 'application/json', 'X-ResearchHelper-Token': self.token})
        request = urllib.request.Request(self.base + route, headers=headers,
            data=json.dumps(payload).encode() if payload is not None else None)
        with urllib.request.urlopen(request, timeout=20) as response:
            cookie = response.headers.get('Set-Cookie')
            if cookie:
                self.cookie = cookie.split(';')[0]
            raw = response.read()
        return raw.decode() if text else json.loads(raw)


def accept_app_reports(base, html_directory=None):
    client = AppClient(base)
    execution = {key: client.bootstrap['execution'][key] for key in ('mode', 'policy_version')}
    result = []
    for case in CASES:
        task = client.call('/api/cases', {'id': case})
        client.call('/api/tasks/' + task['id'] + '/start', {'expected_execution': execution})
        until = time.monotonic() + 30
        while True:
            task = client.call('/api/tasks/' + task['id'])
            if task.get('delivery'):
                break
            if task['status'] != '运行中' or time.monotonic() >= until:
                raise RuntimeError('包内研究未交付：' + case + '，状态：' + task['status'])
            time.sleep(.05)
        if any(task['ledger']['used'][key] != 0 for key in ('model_calls', 'data_requests')):
            raise RuntimeError('离线包后验收出现真实调用')
        rows = []
        for profile in PROFILES:
            rendered = client.call('/api/tasks/' + task['id'] + '/report?profile=' + profile, text=True)
            frozen = document(rendered, profile)
            for format in FORMATS:
                export = client.call('/api/export', {'task_id': task['id'], 'profile': profile, 'format': format})
                raw = base64.b64decode(export['content_base64'], validate=True)
                if hashlib.sha256(raw).hexdigest() != export['sha256'] or not export['filename'].endswith('.' + format):
                    raise RuntimeError('App导出文件或摘要不一致')
                if format == 'html' and document(raw.decode(), profile) != frozen:
                    raise RuntimeError('App预览和HTML导出不是同版内容')
                if format == 'html' and profile == 'standard' and html_directory is not None:
                    html_directory.mkdir(parents=True, exist_ok=True)
                    (html_directory / (case + '.html')).write_bytes(raw)
                if format == 'pdf' and not raw.startswith(b'%PDF-'):
                    raise RuntimeError('App未导出真实PDF')
                if format in ('docx', 'xlsx') and not raw.startswith(b'PK'):
                    raise RuntimeError('App未导出真实Office文件')
                rows.append({'profile': profile, 'format': format, 'bytes': len(raw), 'sha256': export['sha256']})
        result.append({'case': case, 'dataset_sha256': dataset_hash(frozen), 'exports': rows})
    return result



def accept_native_print_geometry(worker, source, output):
    """Check real print preflight without changing installed tools or HTML."""
    worker, source, output = Path(worker), Path(source), Path(output)
    if worker.is_symlink() or not worker.is_file() or source.is_symlink() or not source.is_file():
        raise RuntimeError('打印验收组件或HTML无效')
    original = source.read_bytes()
    from lxml import html
    tree = html.document_fromstring(original)
    expected = len(tree.xpath('//main//*[contains(concat(" ",normalize-space(@class)," ")," chart ")]'))
    output.mkdir(parents=True, exist_ok=False)
    executable = output / 'ResearchHelperHTMLPDF'
    shutil.copyfile(worker, executable); executable.chmod(0o755)
    target = output / 'report.pdf'
    result = subprocess.run([str(executable), str(source), str(target)], cwd=output,
        capture_output=True, text=True, timeout=40,
        env={key:value for key,value in os.environ.items() if key not in {'DYLD_INSERT_LIBRARIES','PYTHONPATH'}})
    state = json.loads(result.stdout) if result.stdout else {}
    layouts = state.get('print_layout')
    if (result.returncode or state.get('status') != 'saved' or state.get('engine') != 'webkit'
            or any(state.get(key) is not False for key in ('foreground_opened','system_panels_shown','physical_job_sent'))
            or type(state.get('print_rules')) is not int or state['print_rules'] < 1
            or type(state.get('charts')) is not int or state['charts'] != expected
            or not isinstance(layouts,list) or len(layouts) != expected):
        raise RuntimeError('后台打印预排版未通过：' + json.dumps(state, ensure_ascii=False)[:1800])
    for row in layouts:
        values = [row.get('width'),row.get('rendered')] if isinstance(row,dict) else []
        if (len(values) != 2 or any(type(value) not in (int,float) or not math.isfinite(value) or value <= 0 for value in values)
                or abs(values[0]-values[1]) > 1):
            raise RuntimeError('打印图表仍使用其他布局尺寸：' + str(row))
    if source.read_bytes() != original:
        raise RuntimeError('打印验收期间HTML发生变化')
    raw = target.read_bytes()
    if not raw.startswith(b'%PDF-') or len(raw) < 1024:
        raise RuntimeError('打印预排版未生成有效PDF')
    return dict(status='passed',charts=expected,print_layout=layouts,print_rules=state['print_rules'],
        source_sha256=hashlib.sha256(original).hexdigest(),pdf_sha256=hashlib.sha256(raw).hexdigest(),
        worker_sha256=digest(worker),native_window_opened=False)


def accept_shared_native_print(bundle, install, sources, output):
    workers=[path for path in bundle.rglob('ResearchHelperHTMLPDF') if path.is_file() and not path.is_symlink()]
    skill=install/'app/tools/ResearchHelperHTMLPDF'
    if not workers or any(digest(worker) != digest(skill) for worker in workers):
        raise RuntimeError('App与Skill没有使用同一打印组件')
    results=[]
    for case in CASES:
        source=sources/(case+'.html')
        app=accept_native_print_geometry(workers[0],source,output/('app-'+case))
        other=accept_native_print_geometry(skill,source,output/('skill-'+case))
        if app['print_layout'] != other['print_layout'] or app['source_sha256'] != other['source_sha256']:
            raise RuntimeError('App与Skill打印图表尺寸不一致：'+case)
        results.append(dict(case=case,app=app,skill=other))
    return dict(status='passed',cases=results,shared_worker_sha256=digest(skill),
        model_calls=0,real_data_requests=0,native_window_opened=False)


def accept_package(candidate, root, bundle):
    from tests.test_current_candidate_privacy_offline import allowed
    with zipfile.ZipFile(candidate['artifacts']['skill']['path']) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Skill ZIP校验失败')
        manifest = json.loads(archive.read('researchhelper/manifest.json'))
        if set(archive.namelist()) != {'researchhelper/' + n for n in manifest['files']} | {'researchhelper/manifest.json'}:
            raise RuntimeError('Skill ZIP包含额外或缺失文件')
        for name, expected in manifest['files'].items():
            if not allowed(name) or hashlib.sha256(archive.read('researchhelper/' + name)).hexdigest() != expected:
                raise RuntimeError('Skill ZIP文件校验失败')
    app = json.loads((Path(candidate['payload']) / 'app-manifest.json').read_text())
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file()}
    if actual != set(app['files']):
        raise RuntimeError('DMG内App文件集合不一致')
    for name, expected in app['files'].items():
        file = bundle / name
        if not allowed(name) or not file.resolve().is_relative_to(bundle) or digest(file) != expected:
            raise RuntimeError('DMG内App文件校验失败')
    if not all(allowed(name) for name in candidate['directory_artifacts']['demo']['files']):
        raise RuntimeError('候选demo含私密文件')
    return {'app_files': len(actual), 'skill_files': len(manifest['files']), 'private_files_absent': True}


SKILL_WORKER = r'''
import hashlib,json,socket,sys
from pathlib import Path
install,project,output=map(Path,sys.argv[1:]);sys.path.insert(0,str(install))
from core.session import Session
from researchhelper.artifact_exporter import convert_saved_html

def denied(*a,**k):raise RuntimeError('Post-build Skill is offline')
socket.socket.connect=denied;socket.socket.connect_ex=denied
s=Session(project);results=[]
for case in ('mechanism','etf','value'):
 task=s.agent.create_case(case);task=s.agent.start(task['id']);assert task.get('delivery')
 assert task['ledger']['used']['model_calls']==task['ledger']['used']['data_requests']==0
 exports=[]
 for profile in ('standard','detailed'):
  for format in ('html','pdf','docx','xlsx','csv'):
   file=s.export(task['id'],profile=profile,format=format);raw=file.read_bytes()
   source=file.parent/'report.html';_,frozen=convert_saved_html(source,'html',expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
   assert frozen['template']['id']=='report-'+profile
   if format=='pdf':assert raw.startswith(b'%PDF-')
   if format in ('docx','xlsx'):assert raw.startswith(b'PK')
   exports.append(dict(profile=profile,format=format,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
 digest=hashlib.sha256(json.dumps(frozen['dataset'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 results.append(dict(case=case,dataset_sha256=digest,exports=exports))
output.write_text(json.dumps(results,ensure_ascii=False))
'''


def accept_skill_reports(candidate, root, workspace):
    with zipfile.ZipFile(candidate['artifacts']['skill']['path']) as archive:
        archive.extractall(workspace)  # Complete safe names and hashes were verified above.
    install = workspace / 'researchhelper'
    before = {str(p.relative_to(install)): digest(p) for p in install.rglob('*') if p.is_file()}
    worker = workspace / 'host.py'; worker.write_text(SKILL_WORKER)
    output = workspace / 'skill-results.json'
    policy = '(version 1)(allow default)(deny network*)(deny file-write* (subpath ' + json.dumps(str(install)) + '))'
    for path in (root/'core', root/'modules', root/'products', root/'.skills',
                 Path.home()/'Library/Application Support/ResearchHelper'):
        policy += '(deny file-read* (subpath ' + json.dumps(str(path)) + '))'
    process = subprocess.run(['/usr/bin/sandbox-exec', '-p', policy, sys.executable, '-I', '-B', str(worker),
        str(install), str(workspace/'project'), str(output)], cwd=workspace,
        capture_output=True, text=True, timeout=120)
    (workspace.parent/'skill.log').write_text(process.stdout + process.stderr)
    if process.returncode:
        raise RuntimeError('独立Skill研究或导出失败，详见skill.log')
    after = {str(p.relative_to(install)): digest(p) for p in install.rglob('*') if p.is_file()}
    if before != after:
        raise RuntimeError('独立Skill安装目录被修改')
    return json.loads(output.read_text())


def accept_current(selected, root=ROOT):
    from tests.accept_native_workbench_render import RenderRuntime
    root, selected = Path(root).resolve(), Path(selected).resolve()
    candidate = verify_current_build(selected, root)
    if candidate['platform'] != 'macos':
        raise RuntimeError('当前包后运行验收仅支持macOS；未声称Windows实机通过')
    out = Path(tempfile.mkdtemp(prefix='postbuild-' + selected.parent.name + '-', dir=root/'result/acceptance'))
    record = {'status': 'running', 'candidate': str(selected), 'manifest_sha256': candidate['manifest_sha256'],
              'model_calls': 0, 'real_data_requests': 0, 'production_store_touched': False,
              'native_window_opened': False, 'installed': False, 'all_prd_accepted': False,
              'browser_ui_verified': False, 'steps': [], 'receipt': str(out/'receipt.json')}
    previous = os.environ.get('RESEARCHHELPER_CURRENT_RECEIPT')
    os.environ['RESEARCHHELPER_CURRENT_RECEIPT'] = str(selected)
    runtime = None
    try:
        with tempfile.TemporaryDirectory(prefix='runtime-', dir=out) as folder:
            workspace = Path(folder); runtime = RenderRuntime(workspace, record)
            try:
                with stage('验收1/3', '核对实际DMG、独立Skill及隔离启动'):
                    base = runtime.start()
                    record['package'] = accept_package(candidate, root, runtime.mount/'ResearchHelper.app')
                    record['steps'].append('package')
                with stage('验收2/3', '包内App完成三类研究、两版报告与五格式导出'):
                    record['app_reports'] = accept_app_reports(base, workspace/'print-html')
                    record['steps'].append('app_reports')
                with stage('验收3/3', '独立Skill完成相同案例及导出，核对数据一致性'):
                    record['skill_reports'] = accept_skill_reports(candidate, root, workspace)
                    app_data = {row['case']: row['dataset_sha256'] for row in record['app_reports']}
                    skill_data = {row['case']: row['dataset_sha256'] for row in record['skill_reports']}
                    if app_data != skill_data:
                        raise RuntimeError('App与Skill的同案例研究数据不一致')
                    record['steps'].append('skill_reports')
                    record['native_print_layout'] = accept_shared_native_print(
                        runtime.mount/'ResearchHelper.app', workspace/'researchhelper',
                        workspace/'print-html', workspace/'print-layout')
                    record['steps'].append('native_print_layout')
            finally:
                if runtime:
                    runtime.stop()
                log = workspace/'backend.log'
                if log.is_file():
                    (out/'backend.log').write_bytes(log.read_bytes())
        if verify_current_build(selected, root) != candidate:
            raise RuntimeError('验收过程中候选身份发生变化')
        record.update(status='scoped_passed', temporary_research_removed=True, dataset_parity=True)
    except BaseException as error:
        record.update(status='cancelled' if isinstance(error, KeyboardInterrupt) else 'failed', error=str(error))
        if isinstance(error, KeyboardInterrupt):raise
        raise RuntimeError('包后验收未通过：' + str(error) + '\n验收回执：' + str(out/'receipt.json')) from error
    finally:
        if previous is None:
            os.environ.pop('RESEARCHHELPER_CURRENT_RECEIPT', None)
        else:
            os.environ['RESEARCHHELPER_CURRENT_RECEIPT'] = previous
        write_record(out/'receipt.json', record)
    return record
