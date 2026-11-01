"""Bounded same-version supplements with mocked models and retained originals."""

import copy
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

import test_method_dependency_workflow_offline as workflow
import test_autonomous_reasoning_offline as autonomous
from researchhelper.contracts import ContractError, charge, fingerprint
from researchhelper.model import ModelService, ModelSettings
from researchhelper.planning import question_specs


class AutonomousSupplementWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workflow.MethodDependencyWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.agent = self.fixture.agent
        self.agent.offline = False
        self.agent.collector.offline = False
        settings = ModelSettings(self.fixture.archive.root)
        settings.save({"endpoint": "https://example.invalid/model", "model": "fabricated-fixture-model", "api_key": "fabricated-only"})
        self.agent.model = ModelService(settings, offline=False, journal=self.agent._journal)

    def prepare(self):
        task, host, _ = self.fixture.prepare("gross_margin_v1")
        questions = question_specs(task) + [{"id": "q2", "question": "独立政策何时生效？", "kind": "fact", "group": "independent"}]
        task = self.agent.revise_plan(task["id"], questions, "登记独立问题")
        text = "合成测试主体政策。发布日期：2026年10月1日。政策自2026年10月2日起生效。"
        task = self.agent.add_material(task["id"], "independent-policy.txt", text.encode())
        material = task["materials"][-1]
        spec = {"id": "effective_date", "artifact_id": material["id"], "object": "合成测试主体", "record_locator": {"part": "段落1"},
                "text": text, "period": "政策条款", "nature": "合同政策条款", "unit": "不适用", "question_ids": ["q2"],
                "published_at": "2026-10-01", "publication": {"at": "2026-10-01", "precision": "日", "timezone": "Asia/Shanghai",
                    "certainty": "已确认", "basis": "原件明确发布日", "basis_ref": {"artifact_id": material["id"],
                    "snapshot_hash": material["snapshot_hash"], "part": "段落1", "quote": text}},
                "review": {"question_ids": ["q2"], "allowed_natures": ["合同政策条款"], "conditions": ["仅确认合成主体政策条款"], "reason": "发布与生效时点分别定位"}}
        plan = {"questions": question_specs(task), "data_requests": [], "calculations": []}
        extraction = {"evidence_specs": host["evidence_specs"] + [spec]}
        first = {"answers": {"q2": {"answer": "政策自2026年10月2日起生效。", "refs": ["effective_date"], "calculation_refs": []}}, "theses": []}
        proposal = {"question_ids": ["q1"], "reason": "原值齐备但未执行必要毛利率计算", "expected_change": "按已核验同口径原值补齐q1毛利率回答",
                    "data_requests": [], "calculations": host["calculations"], "artifact_ids": [], "stop_reason": ""}
        final = {"answers": host["answers"], "theses": []}
        return task, [plan, extraction, first, proposal, final]

    def run_model(self, task, outputs):
        harness = autonomous.FabricatedModel(outputs)
        with patch("researchhelper.model.urllib.request.urlopen", side_effect=harness.respond):
            result = self.agent.start(task["id"])
        return result, harness

    def test_model_adds_registered_calculation_and_preserves_independent_branch(self):
        task, outputs = self.prepare()
        calls, snapshots = [], {}
        original = self.agent._step
        def observe(current, node, host):
            calls.append(node)
            value = original(current, node, host)
            if node == "answer_independent":
                snapshots["question"] = copy.deepcopy(next(q for q in value["questions"] if q["id"] == "q2"))
            return value
        with patch.object(self.agent, "_step", side_effect=observe):
            result, harness = self.run_model(task, outputs)
        self.assertEqual(result["status"], "已完成", result.get("error") or result["gaps"])
        self.assertEqual(result["ledger"]["supplements"], 1)
        self.assertEqual(len(harness.requests), 5)
        self.assertEqual(calls.count("answer_independent"), 1)
        self.assertEqual(calls.count("evidence"), 1)
        self.assertEqual(next(q for q in result["questions"] if q["id"] == "q2"), snapshots["question"])
        node = next(n for n in result["nodes"] if n["id"] == "answer_independent")
        self.assertEqual(node["reuse"]["prior_output_hash"], node["output_hash"])
        self.assertEqual(node["reuse"]["prior_basis"], node["reuse"]["current_basis"])
        self.assertEqual(result["calculations"][0]["output"]["value"], 40)
        self.assertEqual(result["supplement_history"][0]["results"]["questions_answered"], ["q1"])

    def test_stop_proposal_does_not_consume_round_or_research_reserve(self):
        task, outputs = self.prepare()
        outputs[3] = {"question_ids": ["q1"], "reason": "", "expected_change": "", "data_requests": [],
                      "calculations": [], "artifact_ids": [], "stop_reason": "当前无新增必要动作"}
        result, harness = self.run_model(task, outputs[:4])
        self.assertEqual(result["status"], "部分完成", result.get("error"))
        self.assertEqual(result["ledger"]["supplements"], 0)
        self.assertEqual(len(harness.requests), 4)
        self.assertIn("无新增", result["supplement_stop_reason"])

    def test_invalid_proposal_never_counts_round_or_creates_calculation(self):
        task, outputs = self.prepare()
        outputs[3]["question_ids"] = ["q2"]
        result, harness = self.run_model(task, outputs[:4])
        self.assertEqual(result["status"], "部分完成", result.get("error"))
        self.assertEqual(result["ledger"]["supplements"], 0)
        self.assertEqual(result["calculations"], [])
        self.assertEqual(len(harness.requests), 4)

    def test_host_checkpoint_supplements_and_final_delivery_share_version_budget(self):
        task, outputs = self.prepare()
        host = {**outputs[2], **outputs[1], "calculations": [], "data_requests": []}
        first = self.agent.start(task["id"], host, defer_delivery=True)
        self.assertEqual(first["status"], "待开始", first.get("error"))
        self.assertIsNone(first["delivery"])
        self.assertTrue(first["host_checkpoint"])
        second = self.agent.start(task["id"], {"supplement": outputs[3], **outputs[4]}, defer_delivery=True)
        self.assertEqual(second["status"], "待开始", second.get("error"))
        self.assertEqual(second["ledger"]["supplements"], 1)
        completed = self.agent.start(task["id"])
        self.assertEqual(completed["status"], "已完成", completed.get("error") or completed["gaps"])
        self.assertEqual(completed["version"], first["version"])
        self.assertEqual(completed["ledger"]["supplements"], 1)
        self.assertEqual(completed["ledger"]["used"]["model_calls"], 0)
        with self.assertRaises(ContractError):
            self.agent.start(task["id"], {"supplement": outputs[3]})

    def test_cancel_after_round_checkpoint_resumes_without_replanning(self):
        task, outputs = self.prepare()
        original = self.agent._step
        cancelled = []
        def interrupt(current, node, host):
            if node == "calculation" and not cancelled:
                cancelled.append(True)
                self.agent.cancel(current["id"])
                raise ContractError("测试取消已登记轮")
            return original(current, node, host)
        harness = autonomous.FabricatedModel(outputs)
        with patch("researchhelper.model.urllib.request.urlopen", side_effect=harness.respond):
            with patch.object(self.agent, "_step", side_effect=interrupt):
                stopped = self.agent.start(task["id"])
            self.assertEqual(stopped["status"], "已取消")
            self.assertEqual(stopped["ledger"]["supplements"], 1)
            resumed = self.agent.start(task["id"])
        self.assertEqual(resumed["status"], "已完成", resumed.get("error") or resumed["gaps"])
        self.assertEqual(resumed["ledger"]["supplements"], 1)
        self.assertEqual(len(harness.requests), 5)
        self.assertEqual(resumed["ledger"]["used"]["model_calls"], 5)

    def test_cancelled_planner_output_is_discarded_and_not_planned_again(self):
        task, outputs = self.prepare()
        proposal = outputs[3]
        def cancelled(_):
            self.agent.cancel(task["id"])
            return proposal
        outputs[3] = cancelled
        result, harness = self.run_model(task, outputs[:4])
        self.assertEqual(result["status"], "已取消")
        self.assertEqual(result["ledger"]["supplements"], 0)
        self.assertEqual(result["calculations"], [])
        with patch("researchhelper.model.urllib.request.urlopen", side_effect=AssertionError("No uncertain-plan replay")):
            resumed = self.agent.start(task["id"])
        self.assertEqual(resumed["status"], "部分完成", resumed.get("error"))
        self.assertEqual(resumed["ledger"]["used"]["model_calls"], 4)

    def test_material_change_during_planning_rejects_late_proposal(self):
        task, outputs = self.prepare()
        proposal = outputs[3]
        def changed(_):
            self.agent.add_material(task["id"], "new-scope.txt", "新增未核验材料，不改变历史依据。".encode())
            return proposal
        outputs[3] = changed
        result, _ = self.run_model(task, outputs[:4])
        self.assertEqual(result["status"], "失败")
        self.assertIsNone(result["delivery"])
        self.assertEqual(result["ledger"]["supplements"], 0)
        self.assertEqual(len(result["materials"]), 4)
        self.assertEqual(result["calculations"], [])

    def test_two_rounds_include_host_checkpoints_and_third_round_is_rejected(self):
        task, outputs = self.prepare()
        text = "合成测试主体补充财务口径说明。发布日期：2026年10月1日。营业成本不含期间费用。"
        task = self.agent.add_material(task["id"], "extra-financial-note.txt", text.encode())
        raw = task["materials"][-1]
        spec = copy.deepcopy(outputs[1]["evidence_specs"][-1])
        spec.update(id="cost_note", artifact_id=raw["id"], text=text, question_ids=["q1"])
        spec["publication"]["basis_ref"].update(artifact_id=raw["id"], snapshot_hash=raw["snapshot_hash"], quote=text)
        spec["review"]["question_ids"] = ["q1"]
        host = {**outputs[2], **outputs[1], "calculations": [], "data_requests": []}
        first = self.agent.start(task["id"], host, defer_delivery=True)
        proposal = copy.deepcopy(outputs[3])
        proposal.update(calculations=[], artifact_ids=[raw["id"]], reason="补充核对营业成本附注", expected_change="确认财务口径但仍保留必要计算缺口")
        second = self.agent.start(task["id"], {"supplement": proposal, "evidence_specs": [spec]}, defer_delivery=True)
        self.assertEqual(second["status"], "待开始", second.get("error"))
        self.assertEqual(second["ledger"]["supplements"], 1)
        third = self.agent.start(task["id"], {"supplement": outputs[3]}, defer_delivery=True)
        self.assertEqual(third["status"], "待开始", third.get("error"))
        self.assertEqual(third["ledger"]["supplements"], 2)
        forbidden = copy.deepcopy(proposal)
        forbidden["artifact_ids"] = [task["materials"][0]["id"]]
        with self.assertRaisesRegex(ContractError, "两轮"):
            self.agent.start(task["id"], {"supplement": forbidden}, defer_delivery=True)
        self.assertEqual(self.agent.archive.get(task["id"])["ledger"]["supplements"], 2)
        self.assertEqual(first["version"], third["version"])
        self.assertEqual(third["ledger"]["used"]["model_calls"], 0)

    def test_budget_exhaustion_closes_without_supplement_model_dispatch(self):
        task, outputs = self.prepare()
        task["ledger"]["limits"]["model_calls"] = 4
        self.agent.archive.save(task)
        result, harness = self.run_model(task, outputs[:3])
        self.assertEqual(result["status"], "部分完成", result.get("error"))
        self.assertEqual(len(harness.requests), 3)
        self.assertEqual(result["ledger"]["used"]["model_calls"], 3)
        self.assertEqual(result["ledger"]["supplements"], 0)
        self.assertIn("交付预留", result["supplement_stop_reason"])

    def test_last_research_call_is_not_spent_on_an_incomplete_round(self):
        task, outputs = self.prepare()
        task["ledger"]["limits"]["model_calls"] = 5
        self.agent.archive.save(task)
        result, harness = self.run_model(task, outputs[:3])
        self.assertEqual(result["status"], "部分完成", result.get("error"))
        self.assertEqual(len(harness.requests), 3)
        self.assertEqual(result["ledger"]["used"]["model_calls"], 3)
        self.assertEqual(result["ledger"]["reserve"]["model_calls"], 1)
        self.assertEqual(result["ledger"]["used"]["data_requests"], 0)
        self.assertEqual(result["ledger"]["supplements"], 0)
        self.assertIn("完整补证轮", result["supplement_stop_reason"])

    def test_costlier_proposal_is_rejected_after_planning_before_registration(self):
        task, outputs = self.prepare()
        task["ledger"]["limits"]["model_calls"] = 6
        self.agent.archive.save(task)
        outputs[3]["artifact_ids"] = [task["materials"][0]["id"]]
        result, harness = self.run_model(task, outputs[:4])
        self.assertEqual(result["status"], "部分完成", result.get("error"))
        self.assertEqual(len(harness.requests), 4)
        self.assertEqual(result["ledger"]["supplements"], 0)
        self.assertEqual(result["ledger"]["used"]["data_requests"], 0)
        self.assertEqual(result["calculations"], [])
        self.assertIn("提取与解释额度不足", result["supplement_stop_reason"])

    def test_no_increment_stops_after_one_registered_attempt(self):
        task, outputs = self.prepare()
        outputs[3]["calculations"][0]["inputs"]["cost"] = "missing_cost"
        result, harness = self.run_model(task, outputs[:4])
        self.assertEqual(result["status"], "部分完成", result.get("error"))
        self.assertEqual(result["ledger"]["supplements"], 1)
        self.assertEqual(len(harness.requests), 4)
        self.assertIn("未增加", result["supplement_stop_reason"])
        self.assertEqual(result["supplement_history"][0]["results"]["calculations_added"], [])

    @staticmethod
    def request(ident="close_first", end="2026-09-30"):
        return {"id": ident, "source": "ifind", "operation": "cmd_history_quotation", "question_ids": ["q1"],
                "parameters": {"codes": "600000.SH", "indicators": "close", "startdate": "2026-09-01", "enddate": end,
                               "functionpara": {"Currency": "RMB", "CPS": "1", "Fill": "Omit"}}}

    def test_cancelled_dispatched_request_is_not_repeated_on_resume(self):
        task = self.agent.create("核对600000.SH收盘价格", objects=["600000.SH"], cutoff="2026-10-02T18:00:00+08:00",
                                 questions=[{"id": "q1", "question": "核对600000.SH收盘价格", "kind": "fact"}])
        need = self.request()
        def cancelled(current, source, operation, parameters, credentials):
            charge(current["ledger"], "data_requests")
            current["ledger"]["dispatches"].append({"kind": "data_requests", "source": source, "operation": operation,
                "fingerprint": fingerprint({"source": source, "operation": operation, "parameters": parameters}), "status": "已派发"})
            self.agent._journal(current)
            self.agent.cancel(current["id"])
            raise ContractError("模拟在途请求取消，结果未知")
        with patch.object(self.agent.collector, "external", side_effect=cancelled) as external:
            stopped = self.agent.start(task["id"], {"data_requests": [need], "answers": {}, "theses": []})
            self.assertEqual(stopped["status"], "已取消")
            resumed = self.agent.start(task["id"])
        self.assertEqual(resumed["status"], "部分完成", resumed.get("error"))
        self.assertEqual(external.call_count, 1)
        self.assertEqual(resumed["ledger"]["used"]["data_requests"], 1)
        self.assertEqual(resumed["ledger"]["supplements"], 0)

    def test_permission_failure_blocks_supplement_request_without_budget_refund(self):
        task = self.agent.create("核对600000.SH收盘价格", objects=["600000.SH"], cutoff="2026-10-02T18:00:00+08:00",
                                 questions=[{"id": "q1", "question": "核对600000.SH收盘价格", "kind": "fact"}])
        def denied(current, source, operation, parameters, credentials):
            charge(current["ledger"], "data_requests")
            current["ledger"]["dispatches"].append({"kind": "data_requests", "source": source, "operation": operation,
                "fingerprint": fingerprint({"source": source, "operation": operation, "parameters": parameters}),
                "status": "权限失败", "failure_code": "permission_denied"})
            self.agent._journal(current)
            raise ContractError("模拟权限失败")
        with patch.object(self.agent.collector, "external", side_effect=denied) as external:
            first = self.agent.start(task["id"], {"data_requests": [self.request()], "answers": {}, "theses": []}, defer_delivery=True)
            proposal = {"question_ids": ["q1"], "reason": "缩小请求窗口", "expected_change": "补收盘价",
                        "data_requests": [self.request("close_second", "2026-09-29")], "calculations": [], "artifact_ids": [], "stop_reason": ""}
            with self.assertRaisesRegex(ContractError, "权限"):
                self.agent.start(task["id"], {"supplement": proposal}, defer_delivery=True)
        saved = self.agent.archive.get(task["id"])
        self.assertEqual(external.call_count, 1)
        self.assertEqual(saved["ledger"]["used"]["data_requests"], 1)
        self.assertEqual(saved["ledger"]["supplements"], 0)
        self.assertEqual(first["version"], saved["version"])

    def test_new_original_after_checkpoint_requires_declared_supplement_scope(self):
        task, outputs = self.prepare()
        first = self.agent.start(task["id"], {**outputs[1], **outputs[2]}, defer_delivery=True)
        original_node = copy.deepcopy(next(n for n in first["nodes"] if n["id"] == "answer_independent"))
        self.agent.add_material(task["id"], "new-note.txt", "新增附注原件，待核验。".encode())
        with self.assertRaisesRegex(ContractError, "显式登记補证|显式登记补证"):
            self.agent.start(task["id"])
        after = self.agent.archive.get(task["id"])
        self.assertEqual(next(n for n in after["nodes"] if n["id"] == "answer_independent"), original_node)
        result = self.agent.start(task["id"], {"supplement": outputs[3], **outputs[4]})
        self.assertEqual(result["status"], "已完成", result.get("error") or result["gaps"])
        self.assertEqual(next(n for n in result["nodes"] if n["id"] == "answer_independent")["output_hash"], original_node["output_hash"])

    def provider_task(self):
        self.agent.source_credentials = {"ifind": {"access_token": "fabricated-only"}}
        return self.agent.create("核对600000.SH收盘价格", objects=["600000.SH"], cutoff="2026-10-02T18:00:00+08:00",
                                 questions=[{"id": "q1", "question": "核对600000.SH收盘价格", "kind": "fact"}])

    @staticmethod
    def provider_response():
        return io.BytesIO(json.dumps({"errorcode": 0, "tables": [{"thscode": "600000.SH", "time": ["2026-09-30"],
                                       "table": {"close": [10]}}]}).encode())

    def test_known_returned_cancelled_response_allows_explicit_bounded_resume(self):
        task = self.provider_task()
        def returned_after_cancel(*_, **__):
            self.agent.cancel(task["id"])
            return self.provider_response()
        with patch("researchhelper.evidence.urllib.request.urlopen", side_effect=returned_after_cancel) as transport:
            stopped = self.agent.start(task["id"], {"data_requests": [self.request()]})
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(stopped["status"], "已取消")
        self.assertEqual(stopped["source_records"], [])
        attempt = next(iter(stopped["source_attempts"].values()))
        self.assertEqual(attempt["status"], "响应已返回但取消丢弃")
        with patch("researchhelper.evidence.urllib.request.urlopen", side_effect=lambda *a, **k: self.provider_response()) as transport:
            resumed = self.agent.start(task["id"])
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(resumed["ledger"]["used"]["data_requests"], 2)
        self.assertEqual(resumed["ledger"]["supplements"], 0)
        self.assertEqual(resumed["cutoff"], task["cutoff"])
        self.assertEqual(len(resumed["source_records"]), 1)
        self.assertIn("用户恢复", next(iter(resumed["source_attempts"].values()))["recovery_reason"])

    def test_repeated_known_cancellations_cannot_reset_three_dispatch_limit(self):
        task = self.provider_task()
        def returned_after_cancel(*_, **__):
            self.agent.cancel(task["id"])
            return self.provider_response()
        with patch("researchhelper.evidence.urllib.request.urlopen", side_effect=returned_after_cancel) as transport:
            for index in range(3):
                result = self.agent.start(task["id"], {"data_requests": [self.request()]} if index == 0 else None)
                self.assertEqual(result["status"], "已取消")
                self.assertEqual(result["ledger"]["used"]["data_requests"], index + 1)
            final = self.agent.start(task["id"])
        self.assertEqual(transport.call_count, 3)
        self.assertEqual(final["status"], "部分完成", final.get("error"))
        self.assertEqual(final["ledger"]["used"]["data_requests"], 3)
        self.assertEqual(final["ledger"]["supplements"], 0)
        self.assertEqual(final["source_records"], [])

    def test_internal_transport_retries_share_limit_with_cancelled_return(self):
        task = self.provider_task()
        def returned_after_cancel(*_, **__):
            self.agent.cancel(task["id"])
            return self.provider_response()
        with patch("researchhelper.evidence.urllib.request.urlopen", side_effect=returned_after_cancel):
            self.agent.start(task["id"], {"data_requests": [self.request()]})
        with patch("researchhelper.evidence.urllib.request.urlopen", side_effect=URLError("synthetic timeout")) as transport, \
             patch("researchhelper.evidence.time.sleep"):
            result = self.agent.start(task["id"])
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(result["ledger"]["used"]["data_requests"], 3)
        self.assertEqual(len([d for d in result["ledger"]["dispatches"] if d["kind"] == "data_requests"]), 3)
        self.assertEqual(result["status"], "部分完成", result.get("error"))
        self.assertIn("三次派发上限", "；".join(result["gaps"]))
