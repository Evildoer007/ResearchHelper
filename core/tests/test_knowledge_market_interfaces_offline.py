"""Authenticated local service and standalone Skill share the new knowledge path."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from researchhelper.contracts import fingerprint
from researchhelper.knowledge import ResearchKnowledge
from tests.test_autonomous_supplement_interfaces_offline import SupplementInterfacesTests

from tests.skill_fixture_support import build_skill_fixture, source_file

ROOT = Path(__file__).resolve().parents[2]
QUESTION = "利润增长与现金流下降，资本开支和营运资金如何影响"
OPTIONS = {"entry": "个股", "cutoff": "2026-10-02T18:00:00+08:00"}


class KnowledgeMarketInterfacesTests(unittest.TestCase):
    def setUp(self):
        self.fixture = SupplementInterfacesTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def app_task(self):
        fixture = self.fixture
        bootstrap = fixture.request("/api/bootstrap")[0]
        task = fixture.request("/api/tasks", dict(question=QUESTION, **OPTIONS))[0]
        execution = {key: bootstrap["execution"][key] for key in ("mode", "policy_version")}
        fixture.request(f"/api/tasks/{task['id']}/start", {"host_result": {}, "expected_execution": execution})
        worker = fixture.server.agent.workers.get(task["id"])
        if worker:
            worker.join(10)
            self.assertFalse(worker.is_alive())
        return fixture.request(f"/api/tasks/{task['id']}")[0]

    def test_bootstrap_catalog_and_task_adoption_are_separate(self):
        bootstrap = self.fixture.request("/api/bootstrap")[0]
        catalog = {entry["id"] for entry in bootstrap["knowledge"]}
        self.assertEqual(catalog, {entry["id"] for entry in ResearchKnowledge().catalog()})
        self.assertEqual(len(catalog), len(bootstrap["knowledge"]))
        self.assertTrue({"rh_k_cash_profit", "rh_k_rate_transmission", "rh_k_index_structure", "rh_k_etf_exposure"}.issubset(catalog))
        self.assertEqual(bootstrap["field_registry"]["tinyshare.daily_basic"]["permission"], "未验收")
        task = self.app_task()
        self.assertEqual(task["status"], "部分完成", task.get("error"))
        self.assertIn("rh_k_cash_profit", task["knowledge"]["index"])
        self.assertNotIn("rh_k_rate_transmission", task["knowledge"]["index"])
        self.assertEqual(task["ledger"]["used"]["model_calls"], 0)
        self.assertFalse(task["evidence"] or task["theses"])

    def test_independent_skill_uses_same_pack_without_model_or_source_directory(self):
        task = self.app_task()
        installation = self.fixture.root / "knowledge-installation"
        build_skill_fixture(installation)

        for relative in ("app/web/report.css", "assets/vendor/echarts.min.js"):
            target = installation / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file(relative), target)
        before = {str(path.relative_to(installation)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in installation.rglob("*") if path.is_file()}
        script = '''import importlib.util,json,socket,sys
from pathlib import Path
from unittest.mock import patch
installation,project=map(Path,sys.argv[1:])
sys.path.insert(0,str(installation))
spec=importlib.util.spec_from_file_location('independent_session',installation/'core/session.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
with patch('socket.socket.connect',side_effect=AssertionError('No external network')),patch('researchhelper.model.ModelService.reason',side_effect=AssertionError('No second model')):
 session=module.Session(project)
 task=session.create('利润增长与现金流下降，资本开支和营运资金如何影响',entry='个股',cutoff='2026-10-02T18:00:00+08:00')
 result=session.run(task['id'])
 assert result['status']=='部分完成',result.get('error')
 assert session.agent.model is None and result['ledger']['used']['model_calls']==result['ledger']['used']['data_requests']==0
 assert session.export(task['id']).is_file()
 print(json.dumps(result['knowledge'],ensure_ascii=False))
'''
        process = subprocess.run([sys.executable, "-c", script, str(installation), str(self.fixture.root / "independent-project")],
            cwd=self.fixture.root, env=dict(os.environ, PYTHONPATH=str(installation), PYTHONDONTWRITEBYTECODE="1"),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(fingerprint(json.loads(process.stdout)), fingerprint(task["knowledge"]))
        after = {str(path.relative_to(installation)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in installation.rglob("*") if path.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
