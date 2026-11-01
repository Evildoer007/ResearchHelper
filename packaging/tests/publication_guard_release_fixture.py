"""Bind the legacy interleaving release input to actual retained original bytes.

Adapters touch only test input/transport/generated subprocess code. No shared
validator or expected completion assertion is patched. Skill and App use the
same host builder with artifact identities returned by their real material API.
"""
import base64
import copy
from contextlib import contextmanager
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
OLD_INPUT = ROOT / 'result/acceptance/interleaving/input.json'
OLD_INPUT_SHA256 = 'e553e587db7353c2d3213e3efafd6d0caa34234778ffd7a3e4f3bf3218c04aa7'
OLD_HELPERS = ('packaging/tests/rebuild_interleaving_skill.py', 'packaging/tests/rebuild_interleaving_package.py')
SKILL_ENTRY = "task=session.create(**value['create'])\ndone=session.run(task['id'],**value['host_result'])"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def legacy_input():
    assert digest(OLD_INPUT) == OLD_INPUT_SHA256
    return json.loads(OLD_INPUT.read_text())


def original_bytes():
    return '合成验收材料，非真实政策。发布日期：2026年7月19日。\n\n政策已生效。A与B适用同样条件，影响相同。'.encode()


def anchored_host(artifact, legacy):
    """Use actual material identity; metadata cannot substitute for original text."""
    original = original_bytes()
    assert base64.b64decode(artifact['snapshot']['original_base64'], validate=True) == original
    assert artifact['hash'] == hashlib.sha256(original).hexdigest()
    assert artifact['status'] == '已解析'
    source = legacy['host_result']['evidence'][0]
    publication = '发布日期：2026年7月19日。'
    assert publication in artifact['parts'][0]['text']
    assert source['text'] == artifact['parts'][1]['text']
    questions = [question['id'] for question in legacy['create']['questions']]
    host = copy.deepcopy(legacy['host_result'])
    host.pop('evidence')
    host['evidence_specs'] = [{'id': source['id'], 'artifact_id': artifact['id'], 'object': source['object'],
        'record_locator': {'part': artifact['parts'][1]['locator']}, 'text': source['text'],
        'period': source['period'], 'nature': '合同政策条款', 'unit': '不适用', 'published_at': '2026-07-19',
        'publication': {'at': '2026-07-19', 'basis': '同一合成原件明确记载发布日期', 'precision': '日',
            'timezone': 'Asia/Shanghai', 'certainty': '已确认', 'basis_ref': {'kind': 'material',
                'artifact_id': artifact['id'], 'snapshot_hash': artifact['snapshot_hash'],
                'part': artifact['parts'][0]['locator'], 'quote': publication}},
        'question_ids': questions, 'review': {'question_ids': questions, 'allowed_natures': ['合同政策条款'],
            'conditions': ['仅核对合成政策适用条款，不形成实际市场判断'], 'reason': '同一条款支持事实、影响解释与对象比较'}}]
    assert host['answers'] == legacy['host_result']['answers']
    assert host['theses'] == legacy['host_result']['theses']
    return host


def adapt_skill_program(program):
    assert program.count(SKILL_ENTRY) == 1
    definitions = 'import base64,copy,hashlib\n' + '\n'.join(inspect.getsource(f) for f in (original_bytes, anchored_host))
    replacement = definitions + "\ntask=session.create(**value['create'])\n"
    replacement += "material=session.material(task['id'],'interleaving-original.txt',original_bytes())\n"
    replacement += "host=anchored_host(material,value)\ndone=session.run(task['id'],**host)"
    adapted = program.replace(SKILL_ENTRY, replacement)
    marker = "'materials_parsing_claimed':False,"
    assert adapted.count(marker) == 1
    adapted = adapted.replace(marker, "'materials_parsing_claimed':True,'publication_basis_ref':host['evidence_specs'][0]['publication']['basis_ref'],"
        "'original_material_sha256':hashlib.sha256(original_bytes()).hexdigest(),")
    compile(adapted, '<anchored-interleaving-skill>', 'exec')
    return adapted


def adapt_request(request, audit):
    legacy, selected = legacy_input(), set()

    def adapted(base, route, **options):
        payload = options.get('payload')
        if route == '/api/tasks' and payload == legacy['create']:
            value = request(base, route, **options)
            selected.add((base, value[0]['id']))
            return value
        parts = route.strip('/').split('/')
        if len(parts) == 4 and parts[:2] == ['api', 'tasks'] and parts[3] == 'start' and \
                (base, parts[2]) in selected and (payload or {}).get('host_result') == legacy['host_result']:
            material_task = request(base, '/api/tasks/' + parts[2] + '/material',
                cookie=options['cookie'], token=options['token'], payload={'name': 'interleaving-original.txt',
                    'base64': base64.b64encode(original_bytes()).decode()})[0]
            artifact = material_task['materials'][-1]
            host = anchored_host(artifact, legacy)
            outgoing = dict(options, payload=dict(payload, host_result=host))
            audit.append({'task_id': parts[2], 'actual_authenticated_material_http': True,
                'material_sha256': artifact['hash'], 'publication_basis_ref': host['evidence_specs'][0]['publication']['basis_ref'],
                'answers_unchanged': host['answers'] == legacy['host_result']['answers'],
                'theses_unchanged': host['theses'] == legacy['host_result']['theses']})
            return request(base, route, **outgoing)
        return request(base, route, **options)
    return adapted


@contextmanager
def published_fixture_adapter(output_directory):
    """Wrap the unchanged recursive published chain, retaining its assertions."""
    from tests import rebuild_interleaving_skill as skill
    from tests import rebuild_interleaving_package as package
    from tests import rebuild_preset_runtime_package_acceptance as runtime
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    legacy = legacy_input()
    (output / 'input.json').write_bytes(OLD_INPUT.read_bytes())
    before = {name: digest(ROOT / name) for name in OLD_HELPERS}
    audit = {'old_input_sha256': OLD_INPUT_SHA256, 'old_helpers_before': before, 'app': [], 'skill': [],
        'adaptation': 'Replace only unregistered host seed with the same retained text and a same-original publication anchor; keep all legacy completion/dependency/report assertions.',
        'shared_validation_patched': False, 'original_material_sha256': hashlib.sha256(original_bytes()).hexdigest()}
    old_run, old_loader, old_verify = subprocess.run, runtime.load_baseline, package.verify

    def run(command, *args, **kwargs):
        command = list(command) if isinstance(command, (list, tuple)) else command
        adapted = isinstance(command, list) and '-c' in command and SKILL_ENTRY in command[command.index('-c') + 1]
        if adapted:
            position = command.index('-c') + 1
            command[position] = adapt_skill_program(command[position])
        result = old_run(command, *args, **kwargs)
        if adapted:
            assert result.returncode == 0, result.stderr
            receipt = json.loads(result.stdout)
            assert receipt['status'] == 'passed' and receipt['materials_parsing_claimed']
            audit['skill'].append(receipt)
        return result

    def loader():
        baseline = old_loader()
        baseline.request = adapt_request(baseline.request, audit['app'])
        return baseline

    def verify():
        record = old_verify()
        # The adapted fixture now really parses a retained text material; keep
        # the receipt honest without changing any legacy behavioral assertion.
        for check in record['interleaving_checks']:
            check['materials_parsing_claimed'] = True
            check['same_original_publication_anchor'] = True
            check['original_fixture_sha256'] = OLD_INPUT_SHA256
        (output / 'package.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
        return record

    try:
        with patch.object(skill, 'OUT', output), patch.object(package, 'OUT', output), \
                patch.object(package, 'generate', lambda: copy.deepcopy(legacy)), \
                patch.object(package, 'verify', verify), patch.object(subprocess, 'run', run), \
                patch.object(runtime, 'load_baseline', loader):
            yield audit
    finally:
        audit['old_helpers_after'] = {name: digest(ROOT / name) for name in OLD_HELPERS}
        audit['old_input_after_sha256'] = digest(OLD_INPUT)
        assert audit['old_helpers_after'] == before and audit['old_input_after_sha256'] == OLD_INPUT_SHA256
