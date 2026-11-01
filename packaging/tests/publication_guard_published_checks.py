"""Callable published-Skill and authenticated packaged-HTTP publication checks.

No App startup, build, CLI, credentials or production archive management lives
here. HTTP GET reads frozen delivery; compose is separately exercised in PYZ.
"""

import base64
import copy
import hashlib
import importlib.util
import json
import marshal
from pathlib import Path
import subprocess
import sys
import time
import types

PYTHON = '/opt/anaconda3/envs/MachineLearning/bin/python'
ROOT = Path(__file__).resolve().parents[2]


def scenarios():
    return [{'name': name, 'text': text, 'intent': intent, 'expected': expected}
        for name, text, intent, expected in (
            ('unknown', '营业状态正常。', '综合研究', '部分完成'),
            ('future_backdated', '营业状态正常。发布日期2026年7月25日。', '综合研究', '部分完成'),
            ('trade_date', None, '综合研究', '部分完成'),
            ('anchored', '营业状态正常。发布日期2026年7月19日。', '综合研究', '已完成'),
            ('document_content', '营业状态正常。', '原件内容核对', '已完成'))]


def create_payload(case):
    return {'question': '核对原件所述营业状态。', 'objects': ['600000.SH'], 'intent': case['intent'],
        'cutoff': '2026-07-20T18:00:00+08:00',
        'questions': [{'id': 'q1', 'question': '原件所述营业状态是什么？', 'kind': 'fact'}]}


def snapshot_payload():
    return {'source': 'tinyshare', 'operation': 'daily',
        'parameters': {'ts_code': '600000.SH', 'start_date': '20260719', 'end_date': '20260719',
                       'fields': 'ts_code,trade_date,close'},
        'response': {'code': 0, 'data': [{'ts_code': '600000.SH', 'trade_date': '20260719', 'close': 10}]}}


def host_payload(case, record):
    spec = {'id': 'fact1', 'artifact_id': record['id'], 'object': '600000.SH',
        'text': '营业状态正常。', 'record_locator': {'part': '段落1'}, 'published_at': '2026-07-19',
        'period': '2026-07-19', 'nature': '已实现事实', 'unit': '不适用', 'question_ids': ['q1'],
        'publication': {'at': '2026-07-19', 'basis': '宿主借用其他资料日期',
            'precision': '日', 'timezone': 'Asia/Shanghai', 'certainty': '已确认'},
        'review': {'question_ids': ['q1'], 'allowed_natures': ['已实现事实'],
            'conditions': ['仅核查本份原件'], 'reason': '原件文字直接回答当前事实问题'}}
    if case['name'] == 'anchored':
        spec['publication']['basis_ref'] = {'kind': 'material', 'artifact_id': record['id'],
            'snapshot_hash': record['snapshot_hash'], 'part': '段落1', 'quote': '发布日期2026年7月19日。'}
    elif case['name'] == 'document_content':
        spec.update(usage_scope='document_content', published_at=None,
            publication={'at': None, 'basis': '公开时间未核验', 'basis_ref': None,
                'precision': '未知', 'timezone': '未知', 'certainty': '未知'})
    elif case['name'] == 'trade_date':
        spec.update(record_locator={'row': 0, 'field': 'close'}, value=10, raw_value=10,
            raw_unit='元', unit='元', conversion_factor=1,
            identity={'provider_code': '600000.SH', 'object': '600000.SH', 'basis': '明确代码',
                'valid_from': '2026-01-01', 'valid_to': '2026-12-31'})
    return {'evidence_specs': [spec], 'answers': {} if case['name'] == 'document_content' else {
        'q1': {'answer': '营业状态正常。', 'refs': ['fact1']}}}


def check_final(case, final, report):
    assert final['status'] == case['expected'], (case['name'], final.get('error'), final['status'])
    assert final['run_mode'] == 'host'
    assert final['ledger']['used']['model_calls'] == final['ledger']['used']['data_requests'] == 0
    good = case['expected'] == '已完成'
    assert len(final['evidence']) == int(good)
    assert final['delivery'] and report
    if good:
        assert final['questions'][0]['status'] == '已回答'
        assert '营业状态正常' in report
    else:
        assert final['questions'][0]['status'] == '未回答'
        assert any('首次公开时间缺少原始来源定位' in item.get('rejection', '') for item in final['rejected_evidence'])
    return {'name': case['name'], 'status': final['status'], 'evidence_count': len(final['evidence']),
        'model_calls': 0, 'data_requests': 0, 'frozen_report_read': True}


def tamper_compose_check(done):
    from researchhelper.contracts import ContractError, fingerprint
    from researchhelper.evidence import verification_receipt
    from researchhelper.reports import compose
    before = fingerprint(done)
    compose(done)
    changed = copy.deepcopy(done)
    item = changed['evidence'][0]
    item['publication'].pop('basis_ref')
    for component, key in [('EvidenceCollector', 'source_receipt'), ('ResearchAnalyzer', 'applicability_receipt')]:
        item[key] = verification_receipt(item, changed, component, '通过', '仅重签既有事实，不能批准时间')
    try:
        compose(changed)
    except ContractError as error:
        assert '首次公开时间缺少原始来源定位' in str(error), str(error)
    else:
        raise AssertionError('Removed publication anchor accepted by compose')
    assert fingerprint(done) == before
    return {'name': 'resigned_without_anchor', 'status': 'rejected', 'original_unchanged': True,
            'actual_compose_executed': True}


def _skill_worker(installation, project):
    installation, project = Path(installation).resolve(), Path(project).resolve()
    assert not project.is_relative_to(installation)
    source = importlib.util.spec_from_file_location('published_guard_session', installation / 'scripts/session.py')
    module = importlib.util.module_from_spec(source)
    source.loader.exec_module(module)
    session = module.Session(project)
    from researchhelper.archive import ResearchArchive
    cases, anchored = [], None
    for case in scenarios():
        task = session.create(**create_payload(case))
        record = session.snapshot(task['id'], **snapshot_payload()) if case['text'] is None else \
            session.material(task['id'], 'original.txt', case['text'].encode())
        final = session.run(task['id'], **host_payload(case, record))
        reopened = ResearchArchive(session.archive.root).get(task['id'])
        assert reopened == final
        frozen = ResearchArchive(session.archive.root).report(task['id'], final['version'])
        cases.append(check_final(case, reopened, frozen['html']))
        cases[-1]['archive_reopened'] = True
        if case['name'] == 'anchored':
            anchored = reopened
    cases.append(tamper_compose_check(anchored))
    paths = {name: str(Path(value.__file__).resolve()) for name, value in sys.modules.items()
             if name.startswith('researchhelper') and getattr(value, '__file__', None)}
    assert paths and all(Path(path).is_relative_to(installation) for path in paths.values()), paths
    return {'status': 'passed', 'cases': cases, 'input_semantics': scenarios(),
        'source_module_paths': paths, 'archive': str(session.archive.root), 'network_disabled': True}


def _tree_hashes(path):
    return {str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest()
            for item in Path(path).rglob('*') if item.is_file()}


def skill_checks(installation, project):
    """Run installed Session in an isolated interpreter and read-only sandbox."""
    installation, project = Path(installation).resolve(), Path(project).resolve()
    assert installation.is_dir() and not project.is_relative_to(installation)
    project.mkdir(parents=True, exist_ok=True)
    before = _tree_hashes(installation)
    program = 'import runpy,json,sys\nm=runpy.run_path(sys.argv[1])\nprint(json.dumps(m["_skill_worker"](sys.argv[2],sys.argv[3]),ensure_ascii=False))'
    profile = '(version 1)(allow default)(deny network*)(deny file-write* (subpath ' + json.dumps(str(installation)) + '))'
    result = subprocess.run(['/usr/bin/sandbox-exec', '-p', profile, PYTHON, '-I', '-B', '-c', program,
        str(Path(__file__).resolve()), str(installation), str(project)], cwd=project,
        env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, text=True, timeout=45)
    assert before == _tree_hashes(installation), 'Installed Skill changed'
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    value['installation_unchanged'] = True
    value['installation_hashes'] = before
    return value


def http_checks(base, request, auth):
    """request(base,route,cookie=,token=,payload=,raw=) returns (body,headers).

    Caller owns the sandboxed packaged server and its authentication. Returned
    compose_input must next be checked with frozen_module_check, not GET.
    """
    boot, _ = request(base, '/api/bootstrap', cookie=auth['cookie'])
    assert boot['offline']
    execution = boot['execution']
    expected_execution = {key: execution[key] for key in ('mode', 'policy_version')}
    cases, anchored = [], None
    for case in scenarios():
        task, _ = request(base, '/api/tasks', payload=create_payload(case), **auth)
        path = '/api/tasks/' + task['id']
        if case['text'] is None:
            imported, _ = request(base, path + '/snapshot', payload=snapshot_payload(), **auth)
            record = imported['source_records'][-1]
        else:
            imported, _ = request(base, path + '/material', payload={'name': 'original.txt',
                'base64': base64.b64encode(case['text'].encode()).decode()}, **auth)
            record = imported['materials'][-1]
        request(base, path + '/start', payload={'host_result': host_payload(case, record),
            'expected_execution': expected_execution}, **auth)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            final, _ = request(base, path, cookie=auth['cookie'])
            if final['status'] != '运行中':
                break
            time.sleep(.04)
        else:
            raise TimeoutError('Packaged host task did not finish: ' + case['name'])
        reopened, _ = request(base, path, cookie=auth['cookie'])
        assert reopened['evidence'] == final['evidence'] and reopened['delivery'] == final['delivery']
        page, _ = request(base, path + '/report/' + str(final['version']), cookie=auth['cookie'], raw=True)
        cases.append(check_final(case, reopened, page.decode() if isinstance(page, bytes) else page))
        cases[-1].update(authenticated_http=True, archive_reread=True)
        if case['name'] == 'anchored':
            anchored = reopened
    return {'status': 'pending_frozen_recheck', 'cases': cases, 'input_semantics': scenarios(),
            'compose_input': anchored, 'frozen_get_is_compose_recheck': False}


def frozen_module_check(backend, done):
    """Compare current sources and execute actual copied-backend PYZ compose."""
    from PyInstaller.archive.readers import CArchiveReader
    backend = Path(backend).resolve()
    archive = CArchiveReader(str(backend))
    pyz = archive.open_embedded_archive(next(n for n, entry in archive.toc.items() if entry[-1] == 'z'))
    def normalize(code):
        return code.replace(co_filename='<verified>', co_consts=tuple(
            normalize(item) if isinstance(item, types.CodeType) else item for item in code.co_consts))
    hashes = {}
    for name in ('researchhelper.evidence', 'researchhelper.reports', 'researchhelper.contracts'):
        code = pyz.extract(name)
        source = ROOT / (name.replace('.', '/') + '.py')
        assert normalize(code) == normalize(compile(source.read_bytes(), str(source), 'exec', dont_inherit=True)), name
        hashes[name] = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                        'frozen_bytecode_sha256': hashlib.sha256(marshal.dumps(code)).hexdigest()}
    program = '''
import importlib.abc,importlib.util,json,runpy,sys
from pathlib import Path
from PyInstaller.archive.readers import CArchiveReader
backend=Path(sys.argv[2]).resolve()
archive=CArchiveReader(str(backend))
pyz=archive.open_embedded_archive(next(n for n,e in archive.toc.items() if e[-1]=='z'))
loaded={}
class FrozenResearch(importlib.abc.MetaPathFinder,importlib.abc.Loader):
 def find_spec(self,fullname,path=None,target=None):
  if fullname=='researchhelper' or fullname.startswith('researchhelper.'):
   assert fullname in pyz.toc,fullname
   return importlib.util.spec_from_loader(fullname,self,is_package=bool(pyz.toc[fullname][0]))
 def create_module(self,spec): return None
 def exec_module(self,module):
  name=module.__name__
  module.__file__=str(backend)+'!/PYZ.pyz/'+name.replace('.','/')+'.pyc'
  loaded[name]=module.__file__
  exec(pyz.extract(name),module.__dict__)
sys.meta_path.insert(0,FrozenResearch())
helpers=runpy.run_path(sys.argv[1])
result=helpers['tamper_compose_check'](json.loads(sys.stdin.read()))
assert 'researchhelper.evidence' in loaded and 'researchhelper.reports' in loaded
assert all(p.startswith(str(backend)+'!/PYZ.pyz/') for p in loaded.values())
result['frozen_import_paths']=loaded
print(json.dumps(result))
'''
    profile = '(version 1)(allow default)(deny network*)(deny file-write* (subpath ' + json.dumps(str(backend.parent)) + '))'
    result = subprocess.run(['/usr/bin/sandbox-exec', '-p', profile, PYTHON, '-I', '-B', '-c', program,
        str(Path(__file__).resolve()), str(backend)], input=json.dumps(done),
        capture_output=True, text=True, timeout=30, env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'})
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    value['module_hashes'] = hashes
    return value


def compare_checks(skill, http, frozen):
    assert skill['status'] == 'passed' and http['status'] == 'pending_frozen_recheck'
    assert skill['input_semantics'] == http['input_semantics'] == scenarios()
    app_cases = http['cases'] + [frozen]
    assert [(c['name'], c['status']) for c in skill['cases']] == [(c['name'], c['status']) for c in app_cases]
    assert frozen['actual_compose_executed'] and frozen['frozen_import_paths']
    return {'status': 'passed', 'same_six_semantics_and_results': True, 'cases': app_cases,
            'frozen_get_is_compose_recheck': False}
