"""Independent published Skill host fixture, using only installed imports."""

import copy
import importlib.util
import json
from pathlib import Path
import sys

installation, project = map(Path, sys.argv[1:3])
spec = importlib.util.spec_from_file_location('installed_skill', installation / 'scripts/session.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
session = module.Session(project)
from researchhelper.contracts import evidence_requirements
from researchhelper import engine

assert Path(engine.__file__).resolve().is_relative_to(installation.resolve())
text = '材料示例政策\n\n发布日期：2026年7月19日。\n\n政策自2026年7月21日起生效。\n\n适用对象为符合条件的企业。'
checks = []
for complete in (False, True):
    task = session.create('政策何时生效，适用哪些企业？', objects=['材料示例政策'],
        cutoff='2026-07-20T18:00:00+08:00', questions=[{'id': 'q1', 'question': '政策何时生效，适用哪些企业？',
            'kind': 'fact', 'evidence_needs': ['生效日期', '适用范围']}])
    original = session.material(task['id'], 'policy.txt', text.encode())
    needs = evidence_requirements(task['questions'][0])
    first = {'id': 'effective_date', 'artifact_id': original['id'], 'object': '材料示例政策',
        'record_locator': {'part': '段落3'}, 'text': '政策自2026年7月21日起生效。', 'period': '政策条款',
        'nature': '合同政策条款', 'unit': '不适用', 'question_ids': ['q1'], 'published_at': '2026-07-19',
        'publication': {'at': '2026-07-19', 'precision': '日', 'timezone': 'Asia/Shanghai', 'certainty': '已确认',
            'basis': '原件明确公布日', 'basis_ref': {'artifact_id': original['id'], 'snapshot_hash': original['snapshot_hash'],
                'part': '段落2', 'quote': '发布日期：2026年7月19日。'}},
        'review': {'question_ids': ['q1'], 'allowed_natures': ['合同政策条款'], 'conditions': ['仅核对本次条款'],
            'reason': '原件生效条款与问题直接对应', 'evidence_need_ids': {'q1': [needs[0]['id']]}}}
    second = copy.deepcopy(first)
    second.update(id='eligibility', record_locator={'part': '段落4'}, text='适用对象为符合条件的企业。')
    second['review']['evidence_need_ids'] = {'q1': [needs[1]['id']]}
    second['review']['reason'] = '本段明确列示政策适用对象'
    refs = ['effective_date', 'eligibility'] if complete else ['effective_date']
    final = session.run(task['id'], evidence_specs=[first, second], answers={'q1': {
        'answer': '政策自2026年7月21日起生效，适用于符合条件的企业。', 'refs': refs}})
    assert final['status'] == ('已完成' if complete else '部分完成'), final.get('error')
    assert final['ledger']['used']['data_requests'] == final['ledger']['used']['model_calls'] == 0
    assert [len(item['refs']) for item in final['questions'][0]['evidence_coverage']['items']] == [1, int(complete)]
    target = session.export(task['id'])
    assert target.is_file() and target.with_suffix('.json').is_file()
    assert '2026年7月21日' in target.read_text()
    assert module.Session(project).archive.get(task['id'])['questions'] == final['questions']
    checks.append({'status': final['status'], 'archived_evidence': len(final['evidence']),
        'answer_refs': len(refs), 'report_saved': True, 'data_requests': 0, 'model_calls': 0})
assert session.agent.model is None
print(json.dumps({'status': 'passed', 'engine_in_installed_skill': True, 'checks': checks}, ensure_ascii=False))
