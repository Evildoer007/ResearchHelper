"""Authenticated App and isolated installed Skill must deliver equal financial semantics."""
import base64
import hashlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from researchhelper.service import make_server
from tests import test_method_dependency_workflow_offline as workflow_fixtures

from tests.skill_fixture_support import build_skill_fixture, source_file

ROOT = Path(__file__).resolve().parents[2]


def semantic(task):
    return {"status": task["status"], "evidence": [{key: e.get(key) for key in
        ("id", "object", "period", "raw_value", "raw_unit", "value", "unit", "calculation_context")} for e in task["evidence"]],
        "calculations": [{key: c.get(key) for key in ("method", "parameters", "inputs", "output", "method_binding")} for c in task["calculations"]],
        "answers": [{key: q.get(key) for key in ("question", "status", "answer", "refs", "calculation_refs")} for q in task["questions"]]}


class FinancialMethodInterfacesTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="financial-method-interfaces-", dir=ROOT / "result/acceptance")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        original_connect = socket.socket.connect
        def loopback_only(sock, address):
            if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
                raise AssertionError("Non-loopback networking forbidden")
            return original_connect(sock, address)
        guard = patch("socket.socket.connect", loopback_only)
        guard.start()
        self.addCleanup(guard.stop)
        self.server = make_server(self.root / "app-store", ROOT, authentication=True, runtime_configurable=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01})
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.cookie = None
        self.token = self.request("/api/auth/status")[0]["token"]
        with self.assertRaises(HTTPError) as unauthorized:
            self.request("/api/bootstrap")
        self.assertEqual(unauthorized.exception.code, 401)
        self.request("/api/auth/setup", {"admin_password": "interface-admin-test", "sales_password": "interface-sales-test"})
        identity, headers = self.request("/api/auth/login", {"account": "admin", "password": "interface-admin-test"})
        self.assertEqual(identity["role"], "admin")
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.installation = self.root / "installed-skill"
        build_skill_fixture(self.installation)

        for name in ("app/web/report.css", "assets/vendor/echarts.min.js"):
            target = self.installation / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)

    def stop(self):
        for worker in self.server.agent.workers.values():
            worker.join(5)
            self.assertFalse(worker.is_alive())
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def request(self, route, payload=None):
        headers = {"Content-Type": "application/json"}
        if getattr(self, "token", None):
            headers["X-ResearchHelper-Token"] = self.token
        if self.cookie:
            headers["Cookie"] = self.cookie
        request = Request(self.url + route, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        with urlopen(request, timeout=5) as response:
            return json.load(response), response.headers

    def run_method(self, method):
        outer = self
        class AppSources:
            def create(self, question, **options):
                return outer.request("/api/tasks", dict(question=question, **options))[0]
            def add_material(self, ident, name, payload):
                return outer.request(f"/api/tasks/{ident}/material", {"name": name, "base64": base64.b64encode(payload).decode()})[0]
        fixture = workflow_fixtures.MethodDependencyWorkflowTests()
        fixture.agent = AppSources()
        task, host, expected = fixture.prepare(method)
        route = f"/api/tasks/{task['id']}"
        with self.assertRaises(HTTPError) as missing_version:
            self.request(route + "/start", {"host_result": host})
        self.assertEqual(missing_version.exception.code, 400)
        execution = self.request("/api/bootstrap")[0]["execution"]
        self.assertEqual(execution["mode"], "offline")
        self.request(route + "/start", {"host_result": host, "expected_execution":
            {key: execution[key] for key in ("mode", "policy_version")}})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            app = self.request(route)[0]
            if app["status"] != "运行中":
                break
            time.sleep(.01)
        self.assertEqual(app["status"], "已完成", app.get("error"))
        self.assertAlmostEqual(app["calculations"][0]["output"]["value"], expected)
        before = {str(p.relative_to(self.installation)): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.installation.rglob("*") if p.is_file()}
        program = r'''
import importlib.util,json,socket,sys
from pathlib import Path
socket.socket.connect=lambda *_: (_ for _ in ()).throw(AssertionError('Network forbidden'))
data=json.load(sys.stdin)
entry=Path(data['installation'])/'core/session.py'
spec=importlib.util.spec_from_file_location('independent_method_skill',entry)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
session=module.Session(data['project'])
questions=[{key:q[key] for key in ('id','question','kind','method_needs')} for q in data['task']['questions']]
task=session.create(data['task']['question'],objects=data['task']['objects'],cutoff=data['task']['cutoff'],questions=questions)
host=data['host']
for index,item in enumerate(host['evidence_specs']):
    record=session.material(task['id'],f'original_{index}.txt',item['text'].encode())
    item['artifact_id']=record['id']
    anchors=[item['publication']['basis_ref'],*item['calculation_context_basis'].values()]
    for anchor in anchors:
        if anchor['kind']!='unverified':
            anchor.update(artifact_id=record['id'],snapshot_hash=record['snapshot_hash'])
final=session.run(task['id'],**host)
target=session.export(task['id'])
assert target.is_file() and target.with_suffix('.json').is_file()
assert session.archive.root==Path(data['project']).resolve()/'data/researchhelper'
print(json.dumps({'task':final,'content':json.loads(target.with_suffix('.json').read_text())},ensure_ascii=False))
'''
        packet = {"installation": str(self.installation), "project": str(self.root / "independent-project"), "task": task, "host": host}
        result = subprocess.run([sys.executable, "-I", "-B", "-c", program], input=json.dumps(packet), text=True,
                                capture_output=True, cwd=self.root, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        skill = json.loads(result.stdout)
        self.assertEqual(semantic(app), semantic(skill["task"]))
        app_content = self.server.agent.archive.report(app["id"], app["version"])["content"]
        report_request = Request(self.url + route + f"/report/{app['version']}", headers={"Cookie": self.cookie})
        with urlopen(report_request, timeout=5) as response:
            app_html = response.read().decode()
        self.assertEqual(app_html, self.server.agent.archive.report(app["id"], app["version"])["html"])
        self.assertIn("合成测试主体", app_html)
        self.assertEqual(app_content["calculations"], app["calculations"])
        self.assertEqual(skill["content"]["calculations"], skill["task"]["calculations"])
        self.assertNotEqual(app["id"], skill["task"]["id"])
        for final in (app, skill["task"]):
            self.assertEqual(final["ledger"]["used"]["data_requests"], 0)
            self.assertEqual(final["ledger"]["used"]["model_calls"], 0)
            self.assertTrue(all(e["artifact"]["snapshot"]["original_base64"] for e in final["evidence"]))
        after = {str(p.relative_to(self.installation)): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.installation.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_price_change(self):
        self.run_method("observed_price_return_v1")

    def test_etf_nav_premium(self):
        self.run_method("etf_nav_premium_v1")

    def test_gross_margin(self):
        self.run_method("gross_margin_v1")


if __name__ == "__main__":
    unittest.main()
