"""Authenticated candidate workflow with local TLS and spawned transports.

All prices, model responses and credentials are fabricated. No real financial
quality, supplier permissions or installed-App acceptance is asserted.
"""

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from urllib.error import HTTPError

from researchhelper.contracts import context
from researchhelper.engine import ResearchAgent
from researchhelper.reasoning import evidence_contract
from tests import test_authenticated_spawn_workflow_offline as transport

from tests.skill_fixture_support import build_skill_fixture, source_file

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "result/acceptance/question-scope-workflow"


def price_proposals(task):
    """Shared synthetic fixture, not an automatic production extractor."""
    contract = evidence_contract(task)
    source = contract["sources"][0]
    needs = [item["id"] for item in contract["question_requirements"]["q1"]]
    proposals = []
    for row, (date, ident, value) in enumerate((("2026-09-29", "price_start", 10), ("2026-09-30", "price_end", 11))):
        proposals.append({"id": ident, "artifact_id": source["artifact_id"], "object": "600000.SH",
            "record_locator": {"table": 0, "row": row, "field": "close"}, "period": date,
            "nature": "已实现事实", "unit": "元", "published_at": None,
            "publication": contract["current_publication_template"], "availability": source["current_availability_reference"],
            "raw_value": value, "raw_unit": "元", "value": value, "conversion_factor": 1, "question_ids": ["q1"],
            "identity": {"provider_code": "600000.SH", "object": "600000.SH", "basis": "用户明确指定代码与本地响应代码相同",
                "valid_from": date, "valid_to": date},
            "calculation_context": {"security_id": "600000.SH", "currency": "CNY", "price_basis": "unadjusted",
                "price_kind": "close", "observation_date": date, "window_start": "2026-09-29", "window_end": "2026-09-30",
                "corporate_action_status": "unknown"}, "api_context_reference": source["compact_context_reference"],
            "review": {"question_ids": ["q1"], "allowed_natures": ["已实现事实"],
                "conditions": ["本地合成接口响应，不是真实市场价格", "公司行为未知，只解释未复权价格变化"],
                "reason": "逐行核对代码、日期及合成原值，请求口径明确", "evidence_need_ids": {"q1": needs}}})
    return proposals


class QuestionScopeWorkflowTests(unittest.TestCase):
    setUp = transport.AuthenticatedSpawnWorkflowTests.setUp
    stop = transport.AuthenticatedSpawnWorkflowTests.stop
    stop_vendor = transport.AuthenticatedSpawnWorkflowTests.stop_vendor
    request = transport.AuthenticatedSpawnWorkflowTests.request
    login = transport.AuthenticatedSpawnWorkflowTests.login

    def reply(self, route, payload, headers):
        if route == "/api/v1/get_access_token":
            self.assertEqual(headers.get("refresh_token"), transport.REFRESH)
            return 200, {"errorcode": 0, "data": {"access_token": transport.ACCESS}}
        if route == "/api/v1/cmd_history_quotation":
            self.assertEqual(headers.get("access_token"), transport.ACCESS)
            self.assertEqual(payload["codes"], "600000.SH")
            return 200, {"errorcode": 0, "tables": [{"thscode": "600000.SH",
                "time": ["2026-09-29", "2026-09-30"], "table": {"close": [10, 11]}}]}
        self.assertEqual(route, "/model")
        self.assertEqual(headers.get("Authorization"), "Bearer " + transport.MODEL_KEY)
        supplied = json.loads(payload["messages"][-1]["content"])
        task = self.server.agent.archive.get(self.ident)
        if "question_contract" in supplied:
            self.phases.append("plan")
            self.assertEqual(supplied["question_contract"]["obligation_policy"]["version"], 1)
            result = {"questions": [{"id": "q1", "question": task["question"], "kind": "fact", "required": True,
                "evidence_needs": ["两日同代码收盘原值及共同口径"], "method_needs": ["observed_price_return_v1"]}],
                "data_requests": [{"id": "two_prices", "source": "ifind", "operation": "cmd_history_quotation",
                    "question_ids": ["q1"], "parameters": {"codes": "600000.SH", "indicators": "close",
                        "startdate": "2026-09-29", "enddate": "2026-09-30",
                        "functionpara": {"Currency": "RMB", "CPS": "1", "Fill": "Omit"}}}],
                "calculations": [{"id": "price_change", "method": "observed_price_return_v1",
                    "inputs": {"start_price": "price_start", "end_price": "price_end"}, "question_ids": ["q1"],
                    "parameters": {"start_date": "2026-09-29", "end_date": "2026-09-30", "currency": "CNY",
                        "price_basis": "unadjusted", "return_kind": "price_change", "unit": "元"}, "assumptions": {}}]}
        elif "evidence_contract" in supplied:
            self.phases.append("extract")
            result = {"evidence_specs": price_proposals(task)}
        elif "analysis_contract" in supplied:
            self.phases.append("analyze")
            if self.invalid_answer:
                value = "10.00" if self.invalid_answer == "return_kind" else "99"
                result = {"answers": {"q1": {"answer": "持有人总回报为" + value + "%。", "refs": ["price_start", "price_end"],
                    "calculation_refs": ["price_change"]}}, "theses": []}
            else:
                result = {"answers": {"q1": {"refs": ["price_start", "price_end"], "calculation_refs": ["price_change"],
                    "numeric_segments": {"answer": [{"text": "本地合成接口所载两端未复权价格变化为"},
                        {"source": "calculation", "ref": "price_change", "path": "value", "format": "signed", "digits": 2},
                        {"text": "。公司行为未核验，不代表净值收益、持有人总回报或买卖结论。"}]}}}, "theses": []}
        elif "extraction_repair_contract" in supplied:
            self.phases.append("supplement")
            result = {"question_ids": ["q1"], "reason": "", "expected_change": "", "data_requests": [],
                "calculations": [], "artifact_ids": [], "stop_reason": "本地负向检查没有新的依据，不补造总回报"}
        else:
            raise AssertionError("Unexpected local model phase")
        return 200, {"model": "local-fixture", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(result, ensure_ascii=False)}}]}

    def workflow(self, *, invalid_answer=False):
        self.invalid_answer, self.phases = invalid_answer, []
        task = self.request("/api/tasks", {"question": "本地合成接口验收：核查600000.SH两日未复权价格变化，说明口径边界，不作买卖推荐。",
            "objects": ["600000.SH"], "entry": "个股", "research_mode": "current",
            "limits": {"data_requests": 4, "model_calls": 6, "seconds": 120}})[0]
        self.ident = task["id"]
        self.assertEqual(task["question_origin"], "default")
        self.assertEqual(task["research_mode"], "current")
        execution = self.request("/api/bootstrap")[0]["execution"]
        self.request("/api/tasks/" + self.ident + "/start", {"expected_task": context(task),
            "expected_execution": {key: execution[key] for key in ("mode", "policy_version")}})
        self.server.agent.workers[self.ident].join(40)
        self.assertFalse(self.server.agent.workers[self.ident].is_alive())
        done = self.request("/api/tasks/" + self.ident)[0]
        self.assertFalse(self.fixture_errors, self.fixture_errors)
        self.assertEqual(done["questions"][0]["kind"], "fact")
        self.assertEqual(len(done["evidence"]), 2)
        self.assertEqual(len(done["calculations"]), 1)
        self.assertAlmostEqual(done["calculations"][0]["output"]["value"], 10)
        self.assertEqual(done["ledger"]["used"]["data_requests"], 2)
        self.assertEqual(done["theses"], [])
        self.assertIsNotNone(done["delivery"])
        report = self.server.agent.export(self.ident)
        for secret in (transport.ACCESS, transport.REFRESH, transport.MODEL_KEY, transport.SDK_TOKEN):
            self.assertNotIn(secret, json.dumps(done))
            self.assertNotIn(secret, report["html"])
        for event in done["ledger"]["dispatches"]:
            if event.get("network_request_started"):
                self.assertTrue(event["network_worker_reaped"])
                self.assertTrue(event["transport_receiver_stopped"])
        restarted = ResearchAgent(self.server.agent.archive, ROOT, offline=True)
        self.assertEqual(restarted.export(self.ident)["digest"], report["digest"])
        self.assertEqual(restarted.archive.get(self.ident)["ledger"], done["ledger"])
        BASE.mkdir(parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix="negative-" if invalid_answer else "positive-", dir=BASE))
        (output / "report.html").write_text(report["html"])
        (output / "receipt.json").write_text(json.dumps({"status": done["status"], "phases": self.phases,
            "task_id": self.ident, "evidence_count": 2, "calculation_count": 1, "question_status": done["questions"][0]["status"],
            "model_fixture_calls": done["ledger"]["used"]["model_calls"], "data_fixture_calls": 2,
            "real_model_calls": 0, "real_supplier_calls": 0, "restarted_report_unchanged": True,
            "report_sha256": hashlib.sha256(report["html"].encode()).hexdigest(), "workflow_fixture": True,
            "negative_case": str(invalid_answer)}, ensure_ascii=False, indent=2))
        return done, report

    def test_authenticated_price_workflow_delivers_and_restores_without_invented_obligations(self):
        done, report = self.workflow()
        self.assertEqual(done["status"], "已完成", done.get("error") or done["gaps"])
        self.assertEqual(done["questions"][0]["status"], "已回答")
        self.assertEqual(self.phases, ["plan", "extract", "analyze"])
        self.assertEqual(done["ledger"]["used"]["model_calls"], 3)
        self.assertIn("10.00%", report["html"])
        self.assertIn("不代表净值收益", report["html"])
        self.login("sales")
        with self.assertRaises(HTTPError) as denied:
            self.request("/api/tasks/" + self.ident)
        self.assertIn(denied.exception.code, (403, 404))
        denied.exception.close()

    def test_wrong_return_answer_cannot_be_frozen_as_completed_research(self):
        done, report = self.workflow(invalid_answer=True)
        self.assertEqual(done["status"], "部分完成", done.get("error") or done["gaps"])
        self.assertEqual(done["questions"][0]["status"], "未回答")
        self.assertNotIn("持有人总回报为99%", report["html"])
        self.assertLessEqual(done["ledger"]["used"]["model_calls"], 4)

    def test_correct_number_with_false_return_kind_is_not_a_valid_answer(self):
        done, report = self.workflow(invalid_answer="return_kind")
        self.assertEqual(done["status"], "部分完成", done.get("error") or done["gaps"])
        self.assertEqual(done["questions"][0]["status"], "未回答")
        self.assertNotIn("持有人总回报为10.00%", report["html"])

    def test_independent_skill_matches_app_without_a_second_model(self):
        done, report = self.workflow()
        BASE.mkdir(parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix="parity-", dir=BASE))
        installation = output / "installation/researchhelper"
        build_skill_fixture(installation)

        for relative in ("app/web/report.css", "assets/vendor/echarts.min.js"):
            target = installation / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file(relative), target)
        installed_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in installation.rglob("*") if path.is_file()}
        for name in ("planning.py", "engine.py", "reasoning.py", "numeric_integrity.py"):
            self.assertEqual(hashlib.sha256((installation / "researchhelper" / name).read_bytes()).hexdigest(),
                             hashlib.sha256((ROOT / "core/src/researchhelper" / name).read_bytes()).hexdigest())
        fixed = {"question": done["question"], "plan": done["reasoning_state"]["plan"]["proposed"],
                 "expected_answer": done["questions"][0]["answer"]}
        (output / "fixed-input.json").write_text(json.dumps(fixed, ensure_ascii=False))
        program = '''
import importlib.util,json,sys
from pathlib import Path
root,install,out=map(Path,sys.argv[1:]);sys.path.insert(0,str(root))
spec=importlib.util.spec_from_file_location('scope_skill',install/'core/session.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
from tests.test_authenticated_spawn_workflow_offline import LocalCollector,REFRESH,restrict_network
from tests.test_question_scope_workflow_offline import price_proposals
restrict_network()
fixed=json.loads((out/'fixed-input.json').read_text());plan=fixed['plan']
session=module.Session(out/'project',allow_network=True,source_credentials={'ifind':{'refresh_token':REFRESH}})
session.agent.collector=LocalCollector(offline=False,journal=session.agent._journal,active=session.agent._check_run_control)
session.agent.collector.original_keeper=session.archive.retain_original
task=session.create(fixed['question'],entry='个股',objects=['600000.SH'],limits={'data_requests':4,'model_calls':6,'seconds':120})
task=session.plan(task['id'],plan['questions'],'相同固定输入首次分类',task['input_hash'])
first=session.run(task['id'],data_requests=plan['data_requests'],calculations=plan['calculations'],defer_delivery=True)
assert first['status']=='待开始' and not first['delivery'] and first['host_checkpoint']
specs=price_proposals(first)
answers={'q1':{'refs':['price_start','price_end'],'calculation_refs':['price_change'],
 'numeric_segments':{'answer':[{'text':'本地合成接口所载两端未复权价格变化为'},
 {'source':'calculation','ref':'price_change','path':'value','format':'signed','digits':2},
 {'text':'。公司行为未核验，不代表净值收益、持有人总回报或买卖结论。'}]}}}
second=session.run(task['id'],evidence_specs=specs,answers=answers,calculations=plan['calculations'],defer_delivery=True)
assert second['status']=='待开始' and not second['delivery'] and second['questions'][0]['status']=='已回答',second.get('error') or second['gaps']
final=session.deliver(task['id'])
assert final['status']=='已完成',final.get('error') or final['gaps']
assert final['questions'][0]['answer']==fixed['expected_answer']
assert final['ledger']['used']['model_calls']==0 and final['ledger']['used']['data_requests']==2
assert session.agent.model is None and len(final['evidence'])==2 and len(final['calculations'])==1
assert final['calculations'][0]['output']['value']>9.999999 and final['calculations'][0]['output']['value']<10.000001
target=session.export(task['id']);stored=session.archive.report(task['id'],1)
restored=module.Session(out/'project')
assert restored.archive.report(task['id'],1)==stored and restored.status(task['id'])['ledger']==final['ledger']
import researchhelper.engine
assert Path(researchhelper.engine.__file__).is_relative_to(install)
for event in final['ledger']['dispatches']:
 if event.get('network_request_started'):assert event['network_worker_reaped'] and event['transport_receiver_stopped']
(out/'skill-result.json').write_text(json.dumps({'status':final['status'],'answer':final['questions'][0]['answer'],
 'calculation':final['calculations'][0]['output'],'data_fixture_calls':2,'model_fixture_calls':0,
 'restarted_report_unchanged':True,'report_path':str(target),'model_is_none':True},ensure_ascii=False,indent=2))
'''
        child = subprocess.run(["/opt/anaconda3/envs/MachineLearning/bin/python", "-I", "-B", "-c", program,
            str(ROOT), str(installation), str(output)], cwd=output, capture_output=True, text=True, timeout=40)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertTrue(all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == value for path, value in installed_hashes.items()))
        skill = json.loads((output / "skill-result.json").read_text())
        self.assertEqual(skill["answer"], done["questions"][0]["answer"])
        self.assertEqual(skill["calculation"], done["calculations"][0]["output"])
        self.assertTrue(skill["model_is_none"])
        self.assertEqual(skill["data_fixture_calls"], 2)
        self.assertEqual(skill["model_fixture_calls"], 0)
        self.assertFalse(self.fixture_errors, self.fixture_errors)
        (output / "receipt.json").write_text(json.dumps({"status": "passed", "same_fixed_input": True,
            "numeric_outputs_equal": True, "answers_equal": True, "skill_has_no_second_model": True,
            "real_model_calls": 0, "real_supplier_calls": 0, "candidate_only": True,
            "app_model_fixture_calls": 3, "skill_model_fixture_calls": 0,
            "app_data_fixture_calls": 2, "skill_data_fixture_calls": 2,
            "skill_result": str(output / "skill-result.json")}, ensure_ascii=False, indent=2))
