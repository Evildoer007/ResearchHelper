"""Authenticated App and independent Skill agree on retained filing evidence.

The same synthetic directory/PDF is supplied to both. No model, supplier,
production archive or foreground App is used.
"""
import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError

from tests.test_autonomous_supplement_interfaces_offline import SupplementInterfacesTests
from tests.test_filing_workflow_offline import FilingWorkflowTests, PARAMETERS, RESPONSE, policy_pdf
from researchhelper.evidence import FILING_REQUEST_REGISTRY

from tests.skill_fixture_support import build_skill_fixture, source_file

ROOT = Path(__file__).resolve().parents[2]


def semantics(task):
    return {"status": task["status"],
            "answers": [{key: q.get(key) for key in ("question", "status", "answer", "refs", "calculation_refs")}
                        for q in task["questions"]],
            "evidence": [{key: e.get(key) for key in ("id", "object", "text", "period", "nature", "source_check", "applicability_check")}
                         for e in task["evidence"]],
            "model_calls": task["ledger"]["used"]["model_calls"],
            "data_requests": task["ledger"]["used"]["data_requests"]}


class FilingInterfacesTests(unittest.TestCase):
    # Reuse the established authenticated-loopback harness, not its test cases.
    setUp = SupplementInterfacesTests.setUp
    stop = SupplementInterfacesTests.stop
    request = SupplementInterfacesTests.request
    login = SupplementInterfacesTests.login

    def test_authenticated_app_and_isolated_skill_deliver_same_filing_fact(self):
        self.shared_filing_case(RESPONSE)

    def test_signed_directory_never_exports_authorization_in_app_or_skill(self):
        response = copy.deepcopy(RESPONSE)
        response["tables"][0]["pdfURL"] = "http://ft.10jqka.com.cn/standardgwapi/api/news_service/pubnote/download/resource?seq=10001&token=fabricated-private-signature&userid=fixture-user&ip=192.0.2.1"
        self.shared_filing_case(response)

    def shared_filing_case(self, response):
        bootstrap = self.request("/api/bootstrap")[0]
        self.assertEqual(bootstrap["filing_operations"], FILING_REQUEST_REGISTRY)
        self.assertEqual(bootstrap["filing_operations"]["ifind.report_query"]["permission"], "未验收")
        options = {"question": "600000.SH公告中的政策何时生效？", "entry": "个股", "objects": ["600000.SH"],
                   "cutoff": "2026-10-02T18:00:00+08:00",
                   "questions": [{"id": "q1", "question": "600000.SH公告中的政策何时生效？", "kind": "fact"}]}
        task = self.request("/api/tasks", options)[0]
        route = "/api/tasks/" + task["id"]
        task = self.request(route + "/snapshot", {"source": "ifind", "operation": "report_query",
                            "parameters": PARAMETERS, "response": response})[0]
        self.assertNotIn("fabricated-private-signature", json.dumps(task))
        directory = task["source_records"][0]["catalog_entries"][0]
        task = self.request(route + "/filing", {"directory": directory, "name": "original.pdf",
                            "base64": base64.b64encode(policy_pdf()).decode()})[0]
        record = next(r for r in task["source_records"] if r["kind"] == "filing")
        self.assertEqual(record["stages"]["source_check"], "待核验")
        spec = FilingWorkflowTests.spec(None, record)
        execution = {key: bootstrap["execution"][key] for key in ("mode", "policy_version")}
        self.request(route + "/start", {"expected_execution": execution,
                     "host_result": {"evidence_specs": [spec], **FilingWorkflowTests.answer()}})
        for worker in self.server.agent.workers.values():
            worker.join(10)
            self.assertFalse(worker.is_alive())
        final = self.request(route)[0]
        self.assertEqual(final["status"], "已完成", final.get("error") or final["gaps"])
        self.assertTrue(self.server.agent.export(task["id"])["html"])
        self.login("sales")
        with self.assertRaises(HTTPError) as denied:
            self.request(route)
        self.assertIn(denied.exception.code, (403, 404))
        denied.exception.close()

        installation = self.root / "installation"
        build_skill_fixture(installation)

        for relative in ("app/web/report.css", "assets/vendor/echarts.min.js"):
            target = installation / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file(relative), target)
        def hashes():
            return {str(p.relative_to(installation)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in installation.rglob("*") if p.is_file()}
        before = hashes()
        script = '''import importlib.util,json,sys
from pathlib import Path
from unittest.mock import patch
installation,project,repository=map(Path,sys.argv[1:])
sys.path.insert(0,str(installation))
sys.path.append(str(repository))
spec=importlib.util.spec_from_file_location("isolated_session",installation/"core/session.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
import researchhelper
assert Path(researchhelper.__file__).resolve().is_relative_to(installation)
from tests.test_filing_workflow_offline import FilingWorkflowTests,PARAMETERS,RESPONSE,policy_pdf
from tests.test_filing_interfaces_offline import semantics
payload=json.loads(sys.stdin.read());options=payload["options"];response=payload["response"];question=options.pop("question")
with patch("socket.socket.connect",side_effect=AssertionError("No network")),patch("researchhelper.model.ModelService.reason",side_effect=AssertionError("No second model")):
 session=module.Session(project)
 task=session.create(question,**options)
 catalog=session.snapshot(task["id"],"ifind","report_query",PARAMETERS,response)
 original=session.filing(task["id"],catalog["catalog_entries"][0],"original.pdf",policy_pdf())
 final=session.run(task["id"],evidence_specs=[FilingWorkflowTests.spec(None,original)],**FilingWorkflowTests.answer())
 assert final["delivery"] and session.export(task["id"]).is_file(),final.get("error")
 print(json.dumps(semantics(final),ensure_ascii=False))
'''
        environment = dict(os.environ, PYTHONPATH=str(installation), PYTHONDONTWRITEBYTECODE="1")
        run = subprocess.run([sys.executable, "-c", script, str(installation), str(self.root / "project"), str(ROOT)],
                             cwd=self.root, env=environment, input=json.dumps({"options": options, "response": response}), capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        self.assertEqual(semantics(final), json.loads(run.stdout))
        self.assertEqual(before, hashes())
        for path in (self.root / "project/data/researchhelper/researchhelper.sqlite3", self.server.agent.archive.path):
            self.assertNotIn(b"fabricated-private-signature", path.read_bytes())


if __name__ == "__main__":
    unittest.main()
