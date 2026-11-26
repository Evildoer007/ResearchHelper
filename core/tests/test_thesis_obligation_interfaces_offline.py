"""Verify prerequisite enforcement through the actual App and installed Skill APIs.

These new tests use isolated archives and fabricated, retained TXT originals.
They prove shared workflow behavior, not investment quality or supplier access.
The existing tests, installed App and production stores remain unchanged.
"""
import base64
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import socket
import sys
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from researchhelper.service import make_server
from tests import test_thesis_obligations_offline as policy_cases
from tests import test_method_dependency_workflow_offline as method_cases

from tests.skill_fixture_support import build_skill_fixture

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable


def policy_review(request):
    """Fixed synthetic host verdicts; prerequisites remain the engine's job."""
    expected = {
        'answer:basis.answer': '合成基金政策条款仅适用于满足资格的企业。另一条款独立适用。',
        'answer:dependent.answer': '限定合成条款核对，不推断市场价格。',
        'answer:independent.answer': '限定合成条款核对，不推断市场价格。',
        'thesis:0.title': '依赖条件的政策影响',
        'thesis:0.investment_meaning': '仅解释合成条款适用影响',
        'thesis:0.limitation': '资格和执行仍需按原条件核对',
        'thesis:1.title': '独立条款的影响',
        'thesis:1.investment_meaning': '独立的合成条款说明',
        'thesis:1.limitation': '不延伸为价格判断',
    }
    actual = {target['id']: target['text'] for target in request['targets']}
    assert actual in (expected, {key:value for key,value in expected.items() if key!='answer:basis.answer'})
    assert all(target['evidence_refs']==['original'] and not target['calculation_refs']
               for target in request['targets'])
    identifiers = sorted({ident for target in request['targets'] for ident in target['question_ids']})
    assert set(identifiers).issubset({'basis','dependent','independent'})
    return dict(version=request['version'],review_hash=request['review_hash'],
        questions=[dict(question_id=ident,status='answered',missing_evidence=[],
            reason='固定合成条款的软件流程检查，不代表真实金融判断。') for ident in identifiers],
        reviews=[dict(target_id=target['id'],segments=[dict(text=target['text'],verdict='supported',
            basis_refs=['evidence:original'],explanation='仅核对事先声明的合成条款及其适用限制。')])
            for target in request['targets']])


def semantic_result(task):
    """Compare meanings, not random task/artifact IDs or execution timestamps."""
    return dict(status=task['status'],
        questions={q['id']: q['status'] for q in task['questions']},
        theses=[dict(title=t['title'], judgment=t['judgment'], question_ids=t['question_ids'])
                for t in task['theses']],
        gaps=sorted(task['gaps']), used={key: task['ledger']['used'][key]
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
        folder = tempfile.TemporaryDirectory(prefix='thesis-interfaces-')
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name).resolve()
        connect = socket.socket.connect
        def loopback(sock,address):
            if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1'):
                raise AssertionError('External networking forbidden')
            return connect(sock,address)
        guard=patch.object(socket.socket,'connect',loopback)
        guard.start();self.addCleanup(guard.stop)

    def policy_runs(self, client):
        case = policy_cases.ThesisObligationTests()
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
        from researchhelper import argument_support
        server = make_server(self.directory/'app-store', ROOT)
        original_prepare=server.agent._prepare_argument_review
        def prepare(task,proposal,selected):
            if argument_support.enabled(task) and argument_support.find_record(task,proposal) is None:
                self.assertEqual([item['text'] for item in task['evidence']],
                    ['合成基金政策条款仅适用于满足资格的企业。另一条款独立适用。'])
                request=argument_support.review_request(task,proposal)
                wire=policy_review(request)
                result=argument_support.decode_review(wire,argument_support.review_schema(request),request)
                task.setdefault('argument_support_reviews',[]).append(
                    argument_support.make_record(task,proposal,result,actor='host'))
            return original_prepare(task,proposal,selected)
        reviewer=patch.object(server.agent,'_prepare_argument_review',side_effect=prepare)
        reviewer.start();self.addCleanup(reviewer.stop)
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
            server.agent.close_runtime()
        self.assertFalse(thread.is_alive())
        self.assertFalse(any(worker.is_alive() for worker in server.agent.workers.values()))

        installation = self.directory/'skill-installation'
        build_skill_fixture(installation)
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
{inspect.getsource(policy_review)}
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded
entry = module('installed_session', {str(installation/'core/session.py')!r})
session = entry.Session({str(project)!r})
assert session.agent.model is None
class Client:
    archive = session.archive
    create = staticmethod(session.create)
    def add_material(self, ident, name, payload):
        session.material(ident, name, payload)
        return session.status(ident)
    def start(self, ident, payload):
        task = session.run(ident, defer_delivery=True, **payload)
        assert task['status']=='待开始' and not task.get('delivery'),task.get('gaps')
        request = session.argument_review_inputs(ident)['request']
        session.review_arguments(ident,policy_review(request))
        return session.deliver(ident)
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
        gaps=sorted(task['gaps']), used={{key:task['ledger']['used'][key]
                                for key in ('data_requests','model_calls')}},
        saved=bool(task.get('delivery'))))
import researchhelper.engine
assert Path(researchhelper.engine.__file__).is_relative_to({str(installation)!r})
assert session.archive.root == Path({str(project/'data/researchhelper')!r})
assert all(row['used']['model_calls']==row['used']['data_requests']==0 for row in results)
session.agent.close_runtime()
print(json.dumps(results))
'''
        policy='(version 1)(allow default)(deny network*)'
        for path in (ROOT,ROOT.parent/'OptionHelper',ROOT.parent/'DataFetcher',
                     Path.home()/'Library/Application Support/ResearchHelper'):
            policy+='(deny file-read* file-write* (subpath '+json.dumps(str(path))+'))'
        policy+='(deny file-write* (subpath '+json.dumps(str(installation))+'))'
        run = subprocess.run(['/usr/bin/sandbox-exec','-p',policy,PYTHON, '-I', '-B', '-c', script], cwd=project,
                             capture_output=True, text=True, timeout=45)
        self.assertEqual(run.returncode, 0, run.stderr[-3000:])
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
        case = method_cases.MethodDependencyWorkflowTests()
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
