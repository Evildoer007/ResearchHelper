"""Real authenticated loopback and isolated Skill; fabricated transports only."""
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from researchhelper.service import make_server

ROOT = Path(__file__).resolve().parents[2]
CUTOFF = "2026-10-02T18:00:00+08:00"
QUESTIONS = [
    {"id": "q1", "question": "材料披露日期是什么", "kind": "fact", "group": "publication"},
    {"id": "q2", "question": "600000.SH在2026-09-30的收盘价是多少", "kind": "fact", "group": "price"},
]
TEXT = "合成验收材料，非真实行情。600000.SH收盘数据发布于2026-10-01。"


def need(day, ident):
    return {"id": ident, "source": "ifind", "operation": "cmd_history_quotation", "question_ids": ["q2"],
            "parameters": {"codes": "600000.SH", "indicators": "close", "startdate": day, "enddate": day,
                           "functionpara": {"Currency": "RMB", "CPS": "1", "Fill": "Omit"}}}


def proposal(**changes):
    value = {"question_ids": ["q2"], "reason": "缺少指定交易日原值", "expected_change": "核验原值后回答q2",
             "data_requests": [], "calculations": [], "artifact_ids": [], "stop_reason": ""}
    value.update(changes)
    return value


def material_spec(record, ident="publication", question="q1", text=TEXT):
    return {"id": ident, "artifact_id": record["id"], "object": "600000.SH", "record_locator": {"part": "段落1"},
            "text": text, "period": "2026-10-01", "nature": "已实现事实", "unit": "不适用", "question_ids": [question],
            "published_at": "2026-10-01", "publication": {"at": "2026-10-01", "precision": "日", "timezone": "Asia/Shanghai",
                "certainty": "已确认", "basis": "合成原件明确披露日期", "basis_ref": {"artifact_id": record["id"],
                    "snapshot_hash": record["snapshot_hash"], "part": "段落1", "quote": text}},
            "review": {"question_ids": [question], "allowed_natures": ["已实现事实"],
                       "conditions": ["仅核验合成材料所载事实"], "reason": "不作真实市场结论"}}


def answer(ident, text):
    return {"answer": text, "refs": [ident], "calculation_refs": []}


class SupplementInterfacesTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="supplement-interface-", dir=ROOT / "result/acceptance")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        connect = socket.socket.connect
        def loopback(sock, address):
            if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
                raise AssertionError("External network forbidden")
            return connect(sock, address)
        guard = patch("socket.socket.connect", loopback)
        guard.start()
        self.addCleanup(guard.stop)
        self.server = make_server(self.root / "app", ROOT, authentication=True, runtime_configurable=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01})
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.cookie = None
        self.token = self.request("/api/auth/status")[0]["token"]
        with self.assertRaises(HTTPError) as unauthorized:
            self.request("/api/bootstrap")
        self.assertEqual(unauthorized.exception.code, 401)
        unauthorized.exception.close()
        self.request("/api/auth/setup", {"admin_password": "supplement-admin-test", "sales_password": "supplement-sales-test"})
        self.login("admin")

    def stop(self):
        for worker in self.server.agent.workers.values():
            worker.join(10)
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
        req = Request(self.url + route, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        with urlopen(req, timeout=10) as response:
            return json.load(response), response.headers

    def login(self, account):
        self.cookie = None
        identity, headers = self.request("/api/auth/login", {"account": account, "password": f"supplement-{account}-test"})
        self.assertEqual(identity["role"], account)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]

    def test_authenticated_app_directed_supplier_round_preserves_independent_answer(self):
        self.request("/api/settings/model", {"endpoint": "https://example.invalid/model", "model": "fabricated-model",
                                            "api_key": "fabricated-model-key"})
        self.request("/api/settings/data", {"ifind": {"access_token": "fabricated-source-key"}, "tinyshare": {}})
        self.request("/api/settings/execution", {"enabled": True, "confirm": True})
        execution = {key: value for key, value in self.request("/api/bootstrap")[0]["execution"].items()
                     if key in ("mode", "policy_version")}
        task = self.request("/api/tasks", {"question": "核查600000.SH披露日期与2026-09-30收盘价", "objects": ["600000.SH"],
                                          "cutoff": CUTOFF, "questions": QUESTIONS})[0]
        route = f"/api/tasks/{task['id']}"
        task = self.request(route + "/material", {"name": "publication.txt", "base64": base64.b64encode(TEXT.encode()).decode()})[0]
        initial = material_spec(task["materials"][0])
        first_answer = answer("publication", "合成原件披露日期为2026-10-01。")
        calls, supplier, prior = [], [], {}
        def extraction(payload):
            live = self.server.agent.archive.get(task["id"])
            record = next(r for r in live["source_records"] if "second_close" in r.get("data_need_ids", []))
            spec = dict(initial, id="close_value", artifact_id=record["id"], period="2026-09-30", unit="元", question_ids=["q2"],
                        record_locator={"table": 0, "row": 0, "field": "close"}, raw_value=12, value=12, raw_unit="元", conversion_factor=1,
                        identity={"provider_code": "600000.SH", "object": "600000.SH", "basis": "原任务明确同一代码",
                                  "valid_from": "2026-01-01", "valid_to": "2026-12-31"})
            spec.pop("text")
            spec["review"] = dict(initial["review"], question_ids=["q2"])
            return {"evidence_specs": [spec]}
        def supplement_response(payload):
            live = self.server.agent.archive.get(task["id"])
            prior.update(copy.deepcopy(next(n for n in live["nodes"] if n["questions"] == ["q1"] and n["kind"] == "answer")))
            return proposal(data_requests=[need("2026-09-30", "second_close")])
        outputs = [
            {"questions": QUESTIONS, "data_requests": [need("2026-09-29", "first_close")], "calculations": []},
            {"evidence_specs": [initial]}, {"answers": {"q1": first_answer}, "theses": []}, supplement_response,
            extraction, {"answers": {"q2": answer("close_value", "合成响应指定日收盘价为12元。")}, "theses": []},
        ]
        def transport(req, timeout):
            payload = json.loads(req.data)
            if req.full_url.startswith("https://quantapi.51ifind.com/api/v1/cmd_history_quotation"):
                supplier.append(payload)
                value = {"errorcode": 0, "tables": [{"thscode": "600000.SH", "time": [payload["startdate"]], "table": {"close": [12]}}]}
            elif req.full_url == "https://example.invalid/model":
                calls.append(payload)
                self.assertTrue(outputs, "Unexpected model call")
                output = outputs.pop(0)
                if callable(output):
                    output = output(payload)
                value = {"choices": [{"message": {"content": json.dumps(output, ensure_ascii=False)}}]}
            else:
                raise AssertionError("Unexpected external transport: " + req.full_url)
            return io.BytesIO(json.dumps(value).encode())
        with patch("researchhelper.model.urllib.request.urlopen", side_effect=transport):
            self.request(route + "/start", {"expected_execution": execution})
            self.server.agent.workers[task["id"]].join(10)
        final = self.request(route)[0]
        self.assertEqual(final["status"], "已完成", {"error": final.get("error"), "gaps": final["gaps"], "rejected": final["rejected_evidence"]})
        self.assertEqual((final["version"], final["cutoff"], final["ledger"]["supplements"]), (1, CUTOFF, 1))
        self.assertEqual(len(supplier), 2)
        self.assertEqual(len(calls), 6)
        self.assertEqual(final["ledger"]["used"]["data_requests"], 2)
        self.assertEqual(final["ledger"]["used"]["model_calls"], 6)
        self.assertEqual(final["ledger"]["reserve"]["model_calls"], 1)
        self.assertEqual(final["ledger"]["limits"]["model_calls"], 8)
        self.assertEqual(final["questions"][0]["answer"], first_answer["answer"])
        current = next(n for n in final["nodes"] if n["id"] == prior["id"])
        preserved = final["supplement_history"][0]["preserved_nodes"][prior["id"]]
        self.assertEqual(current["output_hash"], prior["output_hash"])
        self.assertEqual(preserved["basis"], current["reuse"]["prior_basis"])
        self.assertEqual(preserved["basis"], current["reuse"]["current_basis"])
        self.assertEqual(prior["output_hash"], current["reuse"]["prior_output_hash"])
        self.assertEqual(preserved["basis"], self.server.agent._branch_basis(final, current))
        self.assertEqual(current["reuse"]["prior_input_hash"], prior["input_hash"])
        self.assertIsNotNone(final["delivery"])
        for item in final["evidence"]:
            self.assertEqual(item["source_receipt"]["component"], "EvidenceCollector")
            self.assertEqual(item["applicability_receipt"]["component"], "ResearchAnalyzer")
        self.login("sales")
        for suffix, body in (("", None), ("/resume", {"expected_execution": execution})):
            with self.assertRaises(HTTPError) as denied:
                self.request(route + suffix, body)
            self.assertIn(denied.exception.code, (403, 404))
            denied.exception.close()

    def test_isolated_skill_checkpoint_then_explicit_round_keeps_version_and_ledger(self):
        from tests.skill_fixture_support import build_skill_fixture, source_file
        installation = self.root / "installation"
        build_skill_fixture(installation)

        for relative in ("app/web/report.css", "assets/vendor/echarts.min.js"):
            target = installation / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file(relative), target)
        before = {str(p.relative_to(installation)): hashlib.sha256(p.read_bytes()).hexdigest() for p in installation.rglob("*") if p.is_file()}
        script = '''import importlib.util,json,socket,sys
from pathlib import Path
from unittest.mock import patch
installation, project, repository = map(Path, sys.argv[1:])
sys.path.insert(0,str(installation))
spec=importlib.util.spec_from_file_location("isolated_session",installation/"core/session.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
helpers=importlib.util.spec_from_file_location("fixtures",repository/"core/tests/test_autonomous_supplement_interfaces_offline.py")
f=importlib.util.module_from_spec(helpers);helpers.loader.exec_module(f)
with patch("socket.socket.connect",side_effect=AssertionError("No network")), patch("researchhelper.model.ModelService.reason",side_effect=AssertionError("No second model")):
 s=module.Session(project)
 t=s.create("核查600000.SH披露日期与2026-09-30收盘价",objects=["600000.SH"],cutoff=f.CUTOFF,questions=f.QUESTIONS)
 r=s.material(t["id"],"publication.txt",f.TEXT.encode())
 text="合成验收材料，非真实行情。600000.SH在2026-09-30收盘价12元。发布于2026-10-01。"
 r2=s.material(t["id"],"price.txt",text.encode())
 first=s.run(t["id"],evidence_specs=[f.material_spec(r)],answers={"q1":f.answer("publication","披露日期2026-10-01")},defer_delivery=True)
 assert first["status"]=="待开始" and first.get("host_checkpoint") and not first.get("delivery"),first.get("error")
 old=first["ledger"]["used"].copy()
 second=s.run(t["id"],supplement=f.proposal(artifact_ids=[r2["id"]]),evidence_specs=[f.material_spec(r2,"close_text","q2",text)],answers={"q2":f.answer("close_text","指定日收盘价12元")},defer_delivery=True)
 assert second["status"]=="待开始" and second.get("host_checkpoint") and not second.get("delivery"),second.get("error")
 final=s.deliver(t["id"])
 assert final["status"]=="已完成",(final.get("error"),final["gaps"])
 assert final["version"]==first["version"]==1 and final["cutoff"]==first["cutoff"]==f.CUTOFF
 assert final["ledger"]["supplements"]==1 and final["ledger"]["used"]["model_calls"]==0 and final["ledger"]["used"]["data_requests"]==0
 assert final["ledger"]["used"]["seconds"]>=old["seconds"] and final["ledger"]["limits"]==first["ledger"]["limits"]
 assert final["questions"][0]["answer"]==first["questions"][0]["answer"]
 assert final["delivery"] and s.export(t["id"]).is_file()
 frozen=s.archive.report(t["id"],1)
 try:
  s.run(t["id"],supplement=f.proposal(artifact_ids=[r2["id"]]))
 except module.ContractError:
  pass
 else:
  raise AssertionError("Frozen report accepted supplementation")
 assert s.archive.report(t["id"],1)==frozen
 print(json.dumps({"status":final["status"],"model_calls":0,"supplements":1,"version":1}))
'''
        environment = dict(os.environ, PYTHONPATH=str(installation), PYTHONDONTWRITEBYTECODE="1")
        run = subprocess.run([sys.executable, "-c", script, str(installation), str(self.root / "project"), str(ROOT)],
                             cwd=self.root, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        self.assertEqual(json.loads(run.stdout)["model_calls"], 0)
        after = {str(p.relative_to(installation)): hashlib.sha256(p.read_bytes()).hexdigest() for p in installation.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
