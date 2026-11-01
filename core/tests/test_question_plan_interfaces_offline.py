"""Local API and isolated Skill acceptance for question-led research."""

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

from researchhelper.cases import CUTOFF, evidence
from researchhelper.service import make_server

ROOT = Path(__file__).resolve().parents[2]
PYTHON = "/opt/anaconda3/envs/MachineLearning/bin/python"


class QuestionPlanInterfaces(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="question-interface-", dir=ROOT / "result" / "acceptance")
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.server = make_server(self.directory / "store", ROOT)
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
        original = evidence("local", "原文", "原文于2026年7月19日生效。")
        self.post(f"/api/tasks/{task['id']}/start", {"host_result": {
            "evidence": [original], "answers": {"q1": {"answer": "2026年7月19日生效。", "refs": ["local"]}}}})
        worker = self.server.agent.workers[task["id"]]
        worker.join(10)
        self.assertFalse(worker.is_alive())
        final = self.server.agent.archive.get(task["id"])
        self.assertEqual(final["status"], "已完成", final.get("error"))
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
        shutil.copytree(ROOT / "skill" / "researchhelper", installation)
        shutil.copytree(ROOT / "core/src/researchhelper", installation / "researchhelper", ignore=shutil.ignore_patterns("__pycache__"))
        for relative in ("app/web/report.css", "assets/vendor/echarts.min.js"):
            target = installation / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        before = {str(p.relative_to(installation)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in installation.rglob("*") if p.is_file()}
        project = self.directory / "independent-project"
        script = f"""
import importlib.util, json, socket
from pathlib import Path
socket.socket.connect = lambda *_: (_ for _ in ()).throw(AssertionError('External call forbidden'))
entry = Path({str(installation / 'scripts/session.py')!r})
spec = importlib.util.spec_from_file_location('isolated_skill', entry)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
session = module.Session({str(project)!r})
from researchhelper.cases import evidence, CUTOFF
questions = [{{'id':'q1','question':'原文日期','kind':'fact'}}]
task = session.create('核查原文日期', questions=questions, cutoff=CUTOFF)
revised = session.plan(task['id'], [dict(questions[0], evidence_needs=['原文条款'])], '补充条款定位', task['input_hash'])
final = session.run(task['id'], [evidence('local','原文','2026年7月19日生效')],
                    {{'q1':{{'answer':'2026年7月19日生效。','refs':['local']}}}})
assert final['status'] == '已完成', final.get('error')
assert not final['theses'] and final['plan_version'] == 2
assert final['ledger']['used']['model_calls'] == final['ledger']['used']['data_requests'] == 0
target = session.export(task['id'])
assert target.is_file() and target.with_suffix('.json').is_file()
assert '2026年7月19日生效' in target.read_text()
assert Path(module.ResearchAgent.__module__.replace('.', '/')).name == 'engine'
import researchhelper.engine
assert str(researchhelper.engine.__file__).startswith({str(installation)!r})
assert session.archive.root == Path({str(project / 'data/researchhelper')!r})
"""
        result = subprocess.run([PYTHON, "-I", "-B", "-c", script], cwd=self.directory, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        after = {str(p.relative_to(installation)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in installation.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
