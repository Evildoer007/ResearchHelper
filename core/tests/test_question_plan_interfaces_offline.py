"""Local API and isolated Skill acceptance for question-led research."""

import base64
import hashlib
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from researchhelper.contracts import evidence_requirements
from researchhelper.cases import CUTOFF
from tests.skill_fixture_support import build_skill_fixture
from tests.test_autonomous_supplement_interfaces_offline import mock_host_review
from tests.host_fixture_support import PUBLICATION, policy_spec
from researchhelper.service import make_server

ROOT = Path(__file__).resolve().parents[2]
PYTHON = "/opt/anaconda3/envs/MachineLearning/bin/python"


class QuestionPlanInterfaces(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="question-interface-", dir=ROOT / "result" / "acceptance")
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.server = make_server(self.directory / "store", ROOT)
        mock_host_review(self, self.server.agent)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.thread = thread
        self.addCleanup(self.stop)
        self.address = f"http://127.0.0.1:{self.server.server_port}"
        self.token = json.load(urlopen(self.address + "/api/bootstrap", timeout=5))["token"]
        self.questions = [{"id": "q1", "question": "原文日期", "kind": "fact"}]

    def stop(self):
        for worker in list(self.server.agent.workers.values()):
            worker.join(5)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def post(self, route, body, headers=None):
        fields = {"Content-Type": "application/json", "X-ResearchHelper-Token": self.token}
        fields.update(headers or {})
        req = Request(self.address + route, data=json.dumps(body).encode(), headers=fields)
        return json.load(urlopen(req, timeout=10))

    def task(self):
        return self.post("/api/tasks", {"question": "核查原文日期", "questions": self.questions, "cutoff": CUTOFF})

    def test_authenticated_plan_and_host_result_reach_same_engine(self):
        task = self.task()
        revised = self.post(f"/api/tasks/{task['id']}/plan", {
            "questions": [dict(self.questions[0], evidence_needs=["原文条款"])], "reason": "补充定位需求",
            "expected_input_hash": task["input_hash"]})
        self.assertEqual(revised["plan_version"], 2)
        text = "原文于2026年7月19日生效。"
        saved = self.post(f"/api/tasks/{task['id']}/material", {"name": "policy.txt",
            "base64": base64.b64encode((PUBLICATION + "\n\n" + text).encode()).decode()})
        original = policy_spec(saved["materials"][-1], "local", "原文", text, ["q1"])
        original["review"]["evidence_need_ids"] = {"q1": [row["id"] for row in evidence_requirements(revised["questions"][0])]}
        self.post(f"/api/tasks/{task['id']}/start", {"host_result": {
            "evidence_specs": [original], "answers": {"q1": {"answer": "2026年7月19日生效。", "refs": ["local"]}}}})
        worker = self.server.agent.workers[task["id"]]
        worker.join(10)
        self.assertFalse(worker.is_alive())
        final = self.server.agent.archive.get(task["id"])
        self.assertEqual(final["status"], "已完成", (final.get("error"), final["gaps"], final["rejected_evidence"]))
        self.assertEqual(final["theses"], [])
        self.assertEqual(final["ledger"]["used"]["model_calls"], 0)
        self.assertEqual(final["ledger"]["used"]["data_requests"], 0)

    def test_plan_rejects_cross_origin_and_stale_input_without_mutation(self):
        task = self.task()
        body = {"questions": [dict(self.questions[0], evidence_needs=["原文"])], "reason": "补充定位"}
        for headers, extra in (({"Origin": "https://example.invalid"}, {}), ({}, {"expected_input_hash": "stale"})):
            with self.subTest(headers=headers), self.assertRaises(HTTPError) as error:
                self.post(f"/api/tasks/{task['id']}/plan", dict(body, **extra), headers)
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(self.server.agent.archive.get(task["id"]), task)

    def test_isolated_skill_plan_run_and_export_do_not_write_installation(self):
        installation = self.directory / "installation"
        build_skill_fixture(installation)
        before = {str(p.relative_to(installation)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in installation.rglob("*") if p.is_file()}
        project = self.directory / "independent-project"
        script = r'''
import json, socket, sys
from pathlib import Path
socket.socket.connect = lambda *_: (_ for _ in ()).throw(AssertionError('External call forbidden'))
installation = Path(INSTALLATION_PLACEHOLDER)
sys.path.insert(0, str(installation))
import core.session as module
session = module.Session(PROJECT_PLACEHOLDER)
from researchhelper.cases import CUTOFF
try:
 questions = [{'id':'q1','question':'原文日期','kind':'fact'}]
 task = session.create('核查原文日期', questions=questions, cutoff=CUTOFF)
 revised = session.plan(task['id'], [dict(questions[0], evidence_needs=['原文条款'])], '补充条款定位', task['input_hash'])
 publication = '发布日期：2026年7月19日。'
 text = '2026年7月19日生效。'
 material = session.material(task['id'], 'policy.txt', (publication+'\n\n'+text).encode())
 record = material
 spec = dict(id='local', artifact_id=record['id'], object='原文', text=text,
  record_locator={'part':'段落2'}, period='合成条款期', nature='合同政策条款', unit='不适用', question_ids=['q1'],
  published_at='2026-07-19', publication=dict(at='2026-07-19',precision='日',timezone='Asia/Shanghai',certainty='已确认',
   basis='合成原件的明确日期',basis_ref=dict(kind='material',artifact_id=record['id'],snapshot_hash=record['snapshot_hash'],part='段落1',quote=publication)),
  review=dict(question_ids=['q1'],allowed_natures=['合同政策条款'],conditions=['固定合成原件的软件协议检查'],reason='逐字核对'))
 from researchhelper.contracts import evidence_requirements
 spec['review']['evidence_need_ids'] = {'q1':[row['id'] for row in evidence_requirements(revised['questions'][0])]}
 checkpoint = session.run(task['id'], evidence_specs=[spec], answers={'q1':{'answer':text,'refs':['local']}}, defer_delivery=True)
 request = session.argument_review_inputs(task['id'])['request']
 assert request['targets'] and all(target['text']==text for target in request['targets'])
 wire = dict(version=request['version'],review_hash=request['review_hash'],questions=[dict(question_id='q1',status='answered',missing_evidence=[],reason='固定合成原件，不代表金融质量')],
  reviews=[dict(target_id=target['id'],segments=[dict(text=target['text'],verdict='supported',basis_refs=['evidence:local'],explanation='固定合成条款逐字对应')]) for target in request['targets']])
 session.review_arguments(task['id'],wire)
 final = session.deliver(task['id'])
 assert final['status'] == '已完成', (final.get('error'),final['gaps'])
 assert not final['theses'] and final['plan_version'] == 2
 assert final['ledger']['used']['model_calls'] == final['ledger']['used']['data_requests'] == 0
 target = session.export(task['id'])
 assert target.is_file() and target.with_suffix('.json').is_file()
 assert '2026年7月19日生效' in target.read_text()
 assert Path(module.ResearchAgent.__module__.replace('.', '/')).name == 'engine'
 import researchhelper.engine
 assert str(researchhelper.engine.__file__).startswith(str(installation))
 assert session.archive.root == Path(PROJECT_STORE_PLACEHOLDER)
finally:
 session.agent.close_runtime()
'''
        script = script.replace("INSTALLATION_PLACEHOLDER", repr(str(installation))).replace("PROJECT_PLACEHOLDER", repr(str(project))).replace("PROJECT_STORE_PLACEHOLDER", repr(str(project / "data/researchhelper")))
        result = subprocess.run([PYTHON, "-I", "-B", "-c", script], cwd=self.directory, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        after = {str(p.relative_to(installation)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in installation.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
