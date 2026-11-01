"""Versioned interface fixtures use actual originals and explicit need links.

The former fixtures planned an obligation but supplied only synthetic approval
flags. Preserve their plan, API, isolation, export and budget assertions with
the now-required excerpt-to-obligation mapping. No old test files are edited.
"""

import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from tests import test_question_plan_interfaces_offline as previous
from tests import test_autonomous_reasoning_offline as policy_fixture
from tests.test_research_coverage_offline import TEXT
from researchhelper.contracts import evidence_requirements

from tests.skill_fixture_support import build_skill_fixture, source_file

ROOT = Path(__file__).resolve().parents[2]


class ResearchCoverageInterfaces(previous.QuestionPlanInterfaces):
    def test_authenticated_plan_and_host_result_reach_same_engine(self):
        task = self.task()
        revised = self.post('/api/tasks/' + task['id'] + '/plan', {
            'questions': [dict(self.questions[0], evidence_needs=['原文条款'])], 'reason': '补充定位需求',
            'expected_input_hash': task['input_hash']})
        self.assertEqual(revised['plan_version'], 2)
        saved = self.post('/api/tasks/' + task['id'] + '/material',
                          {'name': 'policy.txt', 'base64': base64.b64encode(TEXT.encode()).decode()})
        spec = policy_fixture.AutonomousReasoningTests.policy_spec(saved)
        spec['review']['evidence_need_ids'] = {'q1': [evidence_requirements(saved['questions'][0])[0]['id']]}
        self.post('/api/tasks/' + task['id'] + '/start', {'host_result': {'evidence_specs': [spec],
            'answers': {'q1': {'answer': '2026年7月21日生效。', 'refs': ['effective_date']}}}})
        worker = self.server.agent.workers[task['id']]
        worker.join(10)
        self.assertFalse(worker.is_alive())
        final = self.server.agent.archive.get(task['id'])
        self.assertEqual(final['status'], '已完成', final.get('error'))
        self.assertEqual(final['theses'], [])
        self.assertEqual(final['ledger']['used']['model_calls'], 0)
        self.assertEqual(final['ledger']['used']['data_requests'], 0)
        self.assertEqual(final['questions'][0]['evidence_coverage']['items'][0]['refs'], ['effective_date'])

    def test_isolated_skill_plan_run_and_export_do_not_write_installation(self):
        installation = self.directory / 'installation'
        build_skill_fixture(installation)

        for relative in ('app/web/report.css', 'assets/vendor/echarts.min.js'):
            target = installation / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file(relative), target)
        before = {str(p.relative_to(installation)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in installation.rglob('*') if p.is_file()}
        project = self.directory / 'independent-project'
        program = '''
import importlib.util,json,socket,sys
from pathlib import Path
socket.socket.connect=lambda *_: (_ for _ in ()).throw(AssertionError('External call forbidden'))
install=Path(sys.argv[1]);project=Path(sys.argv[2]);text=sys.argv[3]
spec=importlib.util.spec_from_file_location('isolated_skill',install/'core/session.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
session=module.Session(project)
from researchhelper.cases import CUTOFF
from researchhelper.contracts import evidence_requirements
questions=[{'id':'q1','question':'原文日期','kind':'fact'}]
task=session.create('核查原文日期',questions=questions,cutoff=CUTOFF)
revised=session.plan(task['id'],[dict(questions[0],evidence_needs=['原文条款'])],'补充条款定位',task['input_hash'])
original=session.material(task['id'],'policy.txt',text.encode());saved=session.archive.get(task['id'])
entry={'id':'effective_date','artifact_id':original['id'],'object':'材料示例政策','record_locator':{'part':'段落3'},
 'text':'政策自2026年7月21日起生效。','period':'政策条款','nature':'合同政策条款','unit':'不适用','question_ids':['q1'],
 'published_at':'2026-07-19','publication':{'at':'2026-07-19','precision':'日','timezone':'Asia/Shanghai','certainty':'已确认',
 'basis':'原件明确公布日','basis_ref':{'artifact_id':original['id'],'snapshot_hash':original['snapshot_hash'],
 'part':'段落2','quote':'发布日期：2026年7月19日。'}},'review':{'question_ids':['q1'],'allowed_natures':['合同政策条款'],
 'conditions':['仅核对本次条款'],'reason':'原件生效条款与问题直接对应',
 'evidence_need_ids':{'q1':[evidence_requirements(saved['questions'][0])[0]['id']]}}}
final=session.run(task['id'],evidence_specs=[entry],answers={'q1':{'answer':'2026年7月21日生效。','refs':['effective_date']}})
assert final['status']=='已完成',final.get('error')
assert not final['theses'] and final['plan_version']==2
assert final['ledger']['used']['model_calls']==final['ledger']['used']['data_requests']==0
target=session.export(task['id'])
assert target.is_file() and target.with_suffix('.json').is_file() and '2026年7月21日' in target.read_text()
import researchhelper.engine
assert Path(researchhelper.engine.__file__).resolve().is_relative_to(install.resolve())
assert session.archive.root==project/'data/researchhelper'
print(json.dumps({'status':final['status'],'report':str(target),'plan_version':final['plan_version']}))
'''
        # Inherit the full suite's network sandbox; macOS cannot apply another
        # sandbox inside it. Match the original independent-host hash gate.
        result = subprocess.run([previous.PYTHON, '-I', '-B', '-c', program,
            str(installation), str(project), TEXT], cwd=project.parent, capture_output=True, text=True, timeout=30,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'})
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output['status'], '已完成')
        self.assertEqual(output['plan_version'], 2)
        self.assertTrue(Path(output['report']).is_file())
        after = {str(p.relative_to(installation)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in installation.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
