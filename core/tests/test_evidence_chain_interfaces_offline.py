"""App/Skill use the same original-source pipeline in isolated projects."""

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

from researchhelper.service import make_server

from tests.skill_fixture_support import build_skill_fixture, source_file

ROOT = Path(__file__).resolve().parents[2]
PYTHON = "/opt/anaconda3/envs/MachineLearning/bin/python"


class EvidenceInterfaces(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="evidence-interface-", dir=ROOT / "result/acceptance")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.server = make_server(self.directory / "store", ROOT)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.bootstrap = json.load(urlopen(self.url + "/api/bootstrap", timeout=5))

    def stop(self):
        for worker in self.server.agent.workers.values():
            worker.join(5)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def post(self, route, body, origin=None):
        headers = {"Content-Type": "application/json", "X-ResearchHelper-Token": self.bootstrap["token"]}
        if origin:
            headers["Origin"] = origin
        request = Request(self.url + route, data=json.dumps(body).encode(), headers=headers)
        return json.load(urlopen(request, timeout=5))

    def create(self):
        return self.post("/api/tasks", {"question": "核查政策日期", "cutoff": "2026-07-20T18:00:00+08:00",
                                          "questions": [{"id": "q1", "question": "生效日期", "kind": "fact"}]})

    def test_authenticated_original_import_and_source_checks(self):
        task = self.post("/api/tasks", {"question": "逐字核对政策生效条款", "intent": "原件内容核对",
                                      "questions": [{"id": "q1", "question": "生效日期", "kind": "fact"}]})
        body = "政策自2026年7月19日起生效。"
        route = f"/api/tasks/{task['id']}/material"
        encoded = base64.b64encode(body.encode()).decode()
        imported = self.post(route, {"name": "original.txt", "base64": encoded})
        alias = self.post(route, {"name": "renamed.txt", "base64": encoded})
        self.assertEqual(alias["materials"], imported["materials"])
        record = imported["materials"][0]
        spec = {"id": "original", "artifact_id": record["id"], "object": "政策", "record_locator": {"part": "段落1"}, "text": body,
                "period": "原件条款", "nature": "合同政策条款", "unit": "不适用", "published_at": None,
                "usage_scope": "document_content",
                "publication": {"at": None, "basis": "首次公开时间未核验", "basis_ref": None,
                                "timezone": "未知", "precision": "未知", "certainty": "未知"},
                "question_ids": ["q1"], "review": {"question_ids": ["q1"], "allowed_natures": ["合同政策条款"], "conditions": ["仅核查该政策原文"], "reason": "生效条款与原问题相符"}}
        self.post(f"/api/tasks/{task['id']}/start", {"host_result": {"evidence_specs": [spec]}})
        self.server.agent.workers[task["id"]].join(5)
        final = self.server.agent.archive.get(task["id"])
        self.assertEqual(final["status"], "已完成", final.get("error"))
        self.assertEqual(final["evidence"][0]["source_receipt"]["component"], "EvidenceCollector")
        self.assertEqual(final["evidence"][0]["applicability_receipt"]["component"], "ResearchAnalyzer")
        self.assertEqual(final["ledger"]["used"]["data_requests"], 0)

    def test_source_endpoints_reject_cross_origin_without_mutation(self):
        task = self.create()
        for action, body in (("snapshot", {"source": "tinyshare", "operation": "daily", "parameters": {}, "response": {"code": 0, "data": []}}),
                             ("filing", {"directory": {}})):
            with self.subTest(action=action), self.assertRaises(HTTPError) as error:
                self.post(f"/api/tasks/{task['id']}/{action}", body, "https://example.invalid")
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(self.server.agent.archive.get(task["id"]), task)

    def test_skill_original_import_run_export_is_independent_and_read_only(self):
        installation = self.directory / "installation"
        build_skill_fixture(installation)

        for relative in ("app/web/report.css", "assets/vendor/echarts.min.js"):
            target = installation / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file(relative), target)
        before = {str(p.relative_to(installation)): hashlib.sha256(p.read_bytes()).hexdigest() for p in installation.rglob("*") if p.is_file()}
        project = self.directory / "research-project"
        script = f"""
import importlib.util, socket
from pathlib import Path
socket.socket.connect = lambda *_: (_ for _ in ()).throw(AssertionError('External network forbidden'))
entry = Path({str(installation / 'core/session.py')!r})
spec = importlib.util.spec_from_file_location('isolated_source_skill', entry)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
session = module.Session({str(project)!r})
task = session.create('核验费用原文', intent='原件内容核对', cutoff='2026-07-20T18:00:00+08:00',
    questions=[{{'id':'q1','question':'年度费率','kind':'fact'}}])
body = '管理费为0.15%/年。'
record = session.material(task['id'], 'fee.txt', body.encode())
alias = session.material(task['id'], 'fee-copy.txt', body.encode())
assert record == alias
assert len(session.status(task['id'])['materials']) == 1
evidence = {{'id':'fee','artifact_id':record['id'],'object':'离线ETF材料','record_locator':{{'part':'段落1'}},'text':body,
 'period':'原件条款','nature':'合同政策条款','published_at':None,'usage_scope':'document_content',
 'publication':{{'at':None,'basis':'首次公开时间未核验','basis_ref':None,'timezone':'未知','precision':'未知','certainty':'未知'}},
 'raw_token':'0.15','raw_value':.15,'value':.15,'raw_unit':'%/年','unit':'%/年','conversion_factor':1,'question_ids':['q1'],
 'review':{{'question_ids':['q1'],'allowed_natures':['合同政策条款'],'conditions':['仅核查管理费，不是持有收益'],'reason':'原文条款口径符合问题'}}}}
final = session.run(task['id'], evidence_specs=[evidence])
assert final['status'] == '已完成', final.get('error')
assert final['evidence'][0]['artifact']['snapshot']['original_base64']
assert final['evidence'][0]['source_receipt']['component'] == 'EvidenceCollector'
assert final['evidence'][0]['applicability_receipt']['component'] == 'ResearchAnalyzer'
assert final['ledger']['used']['model_calls'] == final['ledger']['used']['data_requests'] == 0
target = session.export(task['id'])
assert target.is_file() and target.with_suffix('.json').is_file()
assert final['evidence'][0]['value'] == .15
assert body in target.read_text()
assert session.archive.root == Path({str(project / 'data/researchhelper')!r})
"""
        result = subprocess.run([PYTHON, "-I", "-B", "-c", script], cwd=self.directory, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        after = {str(p.relative_to(installation)): hashlib.sha256(p.read_bytes()).hexdigest() for p in installation.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
