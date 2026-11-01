"""Verify prerequisite enforcement through the actual App and installed Skill APIs.

These new tests use isolated archives and fabricated, retained TXT originals.
They prove shared workflow behavior, not investment quality or supplier access.
The existing tests, installed App and production stores remain unchanged.
"""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from researchhelper.service import make_server
from tests.test_thesis_obligations_offline import ThesisObligationTests as PolicyCase
from tests.test_method_dependency_workflow_offline import MethodDependencyWorkflowTests as MethodCase

from tests.skill_fixture_support import build_skill_fixture, source_file

ROOT = Path(__file__).resolve().parents[2]
PYTHON = '/opt/anaconda3/envs/MachineLearning/bin/python'


def semantic_result(task):
    """Compare meanings, not random task/artifact IDs or execution timestamps."""
    return dict(status=task['status'],
        questions={q['id']: q['status'] for q in task['questions']},
        theses=[dict(title=t['title'], judgment=t['judgment'], question_ids=t['question_ids'])
                for t in task['theses']],
        gaps=task['gaps'], used={key: task['ledger']['used'][key]
                                for key in ('data_requests', 'model_calls')},
        saved=bool(task.get('delivery')))


class AppClient:
    def __init__(self, server):
        self.server = server
        self.archive = server.agent.archive
        self.address = f'http://127.0.0.1:{server.server_port}'
        with urlopen(self.address+'/api/bootstrap', timeout=5) as response:
            self.token = json.load(response)['token']

    def post(self, route, payload):
        request = Request(self.address+route, data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json', 'X-ResearchHelper-Token': self.token})
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    def create(self, question, **options):
        return self.post('/api/tasks', dict(question=question, **options))

    def add_material(self, task_id, name, payload):
        return self.post('/api/tasks/'+task_id+'/material',
            dict(name=name, base64=base64.b64encode(payload).decode()))

    def start(self, task_id, payload):
        self.post('/api/tasks/'+task_id+'/start', dict(host_result=payload))
        worker = self.server.agent.workers[task_id]
        worker.join(10)
        if worker.is_alive():
            raise AssertionError('App research worker did not finish')
        return self.archive.get(task_id)


class ThesisInterfaceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='thesis-interfaces-', dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)

    def policy_runs(self, client):
        case = PolicyCase()
        case.agent = client
        results = []
        for options in ({}, {'ready': True}, {'ready': True, 'needs_method': True}):
            task, _ = case.run_policy(**options)
            results.append(semantic_result(task))
            restored = client.archive.get(task['id'], task['version'])
            self.assertEqual(semantic_result(restored), semantic_result(task))
            report = client.archive.report(task['id'], task['version'])
            self.assertEqual(report['content']['theses'], task['theses'])
        return results

    def test_app_service_and_independent_skill_preserve_the_same_branches(self):
        server = make_server(self.directory/'app-store', ROOT)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            app_results = self.policy_runs(AppClient(server))
        finally:
            for worker in server.agent.workers.values():
                worker.join(10)
            server.shutdown()
            server.server_close()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(any(worker.is_alive() for worker in server.agent.workers.values()))

        installation = self.directory/'skill-installation'
        build_skill_fixture(installation)
        shutil.copytree(ROOT/'core/src/researchhelper', installation/'researchhelper',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for relative in ('app/web/report.css', 'assets/vendor/echarts.min.js'):
            target = installation/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/relative, target)
        project = self.directory/'independent-project'
        project.mkdir()
        # Plain fixture inputs sit outside the read-only Skill installation.
        workload = project/'case_input.py'
        shutil.copyfile(ROOT/'core/tests/test_thesis_obligations_offline.py', workload)
        before = {str(p.relative_to(installation)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in installation.rglob('*') if p.is_file()}
        script = f'''
import importlib.util, json, socket
from pathlib import Path
socket.socket.connect = lambda *_: (_ for _ in ()).throw(AssertionError('No network'))
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded
entry = module('installed_session', {str(installation/'core/session.py')!r})
session = entry.Session({str(project)!r})
class Client:
    archive = session.archive
    create = staticmethod(session.create)
    def add_material(self, ident, name, payload):
        session.material(ident, name, payload)
        return session.status(ident)
    def start(self, ident, payload):
        return session.run(ident, **payload)
case = module('case_input', {str(workload)!r}).ThesisObligationTests()
case.agent = Client()
results = []
for options in ({{}}, {{'ready': True}}, {{'ready': True, 'needs_method': True}}):
    task, _ = case.run_policy(**options)
    restored = session.status(task['id'], task['version'])
    assert restored['theses'] == task['theses']
    target = session.export(task['id'])
    assert target.is_file() and target.with_suffix('.json').is_file()
    results.append(dict(status=task['status'],
        questions={{q['id']:q['status'] for q in task['questions']}},
        theses=[dict(title=t['title'], judgment=t['judgment'], question_ids=t['question_ids'])
                for t in task['theses']],
        gaps=task['gaps'], used={{key:task['ledger']['used'][key]
                                for key in ('data_requests','model_calls')}},
        saved=bool(task.get('delivery'))))
import researchhelper.engine
assert Path(researchhelper.engine.__file__).is_relative_to({str(installation)!r})
assert session.archive.root == Path({str(project/'data/researchhelper')!r})
print(json.dumps(results))
'''
        run = subprocess.run([PYTHON, '-I', '-B', '-c', script], cwd=project,
                             capture_output=True, text=True, timeout=45)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), app_results)
        after = {str(p.relative_to(installation)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in installation.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual([r['status'] for r in app_results], ['部分完成', '已完成', '部分完成'])
        for result in app_results:
            self.assertTrue(result['saved'])
            self.assertEqual(result['used']['data_requests'], 0)
            self.assertEqual(result['used']['model_calls'], 0)

    def test_completed_registered_method_requires_explicit_thesis_reference(self):
        case = MethodCase()
        case.setUp()
        self.addCleanup(case.doCleanups)
        for referenced in (False, True):
            with self.subTest(referenced=referenced):
                create = case.agent.create
                def create_mechanism(question, **options):
                    options['questions'][0]['kind'] = 'mechanism'
                    return create(question, **options)
                # Declare the intended question at creation; an adopted fact
                # plan must not be rewritten later to another question kind.
                with patch.object(case.agent, 'create', side_effect=create_mechanism):
                    task, payload, _ = case.prepare('gross_margin_v1')
                payload['theses'] = [dict(title='合成经营口径的解释', judgment='支持',
                    support=list(payload['calculations'][0]['inputs'].values()), counter=[],
                    question_ids=['q1'], calculation_refs=['calc_main'] if referenced else [],
                    investment_meaning='仅解释合成原件经营口径，不外推证券收益',
                    limitation='合成材料不证明真实经营或投资判断')]
                done = case.agent.start(task['id'], payload)
                self.assertEqual(len(done['calculations']), 1, done.get('error'))
                self.assertEqual(len(done['theses']), int(referenced), done.get('error'))
                self.assertEqual(done['status'], '已完成' if referenced else '部分完成', done.get('error'))
                self.assertTrue(done.get('delivery'), done.get('error'))
                self.assertEqual(done['ledger']['used']['model_calls'], 0)
                self.assertEqual(done['ledger']['used']['data_requests'], 0)


if __name__ == '__main__':
    unittest.main()
