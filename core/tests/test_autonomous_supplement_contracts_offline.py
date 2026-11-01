"""Directed supplementation proposals cannot widen scope or repeat spending."""
import copy
import unittest

from researchhelper.contracts import ContractError, fingerprint
from researchhelper.planning import normalize_questions
from researchhelper.reasoning import PHASE_RULES, validate_supplement


class SupplementContractsTests(unittest.TestCase):
    def setUp(self):
        self.task = {"id": "supplement_contract", "version": 1, "batch": 1, "plan_version": 1,
            "question": "核查600000.SH收盘价", "intent": "综合研究", "objects": ["600000.SH"],
            "cutoff": "2026-09-30T18:00:00+08:00", "ledger": {"dispatches": []}}
        self.task["questions"] = normalize_questions([{"id": "q1", "question": self.task["question"], "kind": "fact"}], self.task["question"], self.task["intent"])
        self.task["questions"][0]["status"] = "未回答"
        self.need = {"id": "new_close", "source": "ifind", "operation": "cmd_history_quotation",
            "parameters": {"codes": "600000.SH", "indicators": "close", "startdate": "2026-09-29", "enddate": "2026-09-30",
                           "functionpara": {"Currency": "RMB", "CPS": "1", "Fill": "Omit"}},
            "question_ids": ["q1"], "selection_reason": "优先iFinD核查缺失原值"}
        self.proposal = {"question_ids": ["q1"], "reason": "q1缺少原始收盘值", "expected_change": "新原值可能完成q1的事实核查",
            "data_requests": [self.need], "calculations": [], "artifact_ids": [], "stop_reason": ""}

    def test_valid_increment_and_explicit_stop_are_nonmutating(self):
        self.assertIn("supplement", PHASE_RULES)
        before = copy.deepcopy(self.task)
        result = validate_supplement(self.task, self.proposal)
        result["data_requests"][0]["parameters"]["codes"] = "forged"
        self.assertEqual(self.need["parameters"]["codes"], "600000.SH")
        self.assertEqual(self.task, before)
        stop = dict(self.proposal, data_requests=[], stop_reason="现有工具不能证明首次公开时间")
        self.assertEqual(validate_supplement(self.task, stop), stop)
        stop["stop_reason"] = ""
        with self.assertRaises(ContractError):
            validate_supplement(self.task, stop)

    def test_scope_time_tools_and_unanswered_core_binding(self):
        mutations = [lambda p: p.update(cutoff="2027-01-01"), lambda p: p.update(questions=[]),
            lambda p: p.update(reason=""), lambda p: p.update(expected_change=""),
            lambda p: p.update(stop_reason="停止却仍有动作"), lambda p: p.update(question_ids=["missing"]),
            lambda p: p["data_requests"][0]["parameters"].update(codes="000001.SZ"),
            lambda p: p["data_requests"][0]["parameters"].update(enddate="2026-10-01"),
            lambda p: p["data_requests"][0]["parameters"].update(indicators="invented"),
            lambda p: p["data_requests"][0].update(operation="get_access_token")]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                proposed = copy.deepcopy(self.proposal)
                mutate(proposed)
                with self.assertRaises(ContractError):
                    validate_supplement(self.task, proposed)
        for change in ({"status": "已回答"}, {"required": False}):
            task = copy.deepcopy(self.task)
            task["questions"][0].update(change)
            with self.assertRaises(ContractError):
                validate_supplement(task, self.proposal)

    def test_duplicate_requests_from_plan_snapshot_ledger_and_history_rejected(self):
        digest = fingerprint({key: self.need[key] for key in ("source", "operation", "parameters")})
        sources = [{"data_need_specs": [self.need]}, {"host_result": {"data_requests": [self.need]}},
                   {"source_records": [{"request_hash": digest}]}, {"ledger": {"dispatches": [{"kind": "data_requests", "fingerprint": digest}]}},
                   {"supplement_history": [{"proposal": self.proposal}]}]
        for source in sources:
            with self.subTest(source=source):
                task = dict(self.task, **source)
                proposed = copy.deepcopy(self.proposal)
                proposed["data_requests"][0]["id"] = "renamed_request"
                with self.assertRaises(ContractError):
                    validate_supplement(task, proposed)
        duplicate = copy.deepcopy(self.proposal)
        duplicate["data_requests"].append(dict(self.need, id="same_call_new_id"))
        with self.assertRaises(ContractError):
            validate_supplement(self.task, duplicate)

    def test_permission_and_auth_failure_cannot_be_retried_or_switched(self):
        for event in ({"status": "鉴权失败"}, {"failure_code": "permission_denied"}, {"status": "HTTP请求未通过"}):
            task = dict(self.task, ledger={"dispatches": [event]})
            with self.assertRaises(ContractError):
                validate_supplement(task, self.proposal)
            switched = copy.deepcopy(self.proposal)
            switched["data_requests"][0].update(source="tinyshare", operation="daily", selection_reason="改数据源重试",
                parameters={"ts_code": "600000.SH", "fields": "close", "start_date": "20260929", "end_date": "20260930"})
            with self.assertRaises(ContractError):
                validate_supplement(task, switched)

    def test_calculation_definition_and_assumptions_cannot_be_overwritten(self):
        calculation = {"id": "fee_calc", "method": "fee_difference_v1", "inputs": {"fee_a": "e_a", "fee_b": "e_b"},
                       "parameters": {"unit": "%/年"}, "assumptions": {}, "question_ids": ["q1"]}
        proposal = dict(self.proposal, data_requests=[], calculations=[calculation])
        validate_supplement(self.task, proposal)
        task = dict(self.task, calculation_specs=[copy.deepcopy(calculation)])
        with self.assertRaises(ContractError):
            validate_supplement(task, proposal)
        renamed = copy.deepcopy(proposal)
        renamed["calculations"][0]["id"] = "renamed"
        with self.assertRaises(ContractError):
            validate_supplement(task, renamed)
        forged = copy.deepcopy(proposal)
        forged["calculations"][0]["assumptions"] = {"rate": .1}
        with self.assertRaises(ContractError):
            validate_supplement(self.task, forged)

    def test_reread_requires_owned_intact_original_and_new_action(self):
        record = {"id": "material_a", "task_id": self.task["id"], "research_version": 1, "kind": "material", "snapshot": {"text": "合成原文"}}
        record["snapshot_hash"] = fingerprint(record["snapshot"])
        task = dict(self.task, materials=[record])
        proposal = dict(self.proposal, data_requests=[], artifact_ids=[record["id"]])
        validate_supplement(task, proposal)
        for change in ({"task_id": "other"}, {"research_version": 2}, {"snapshot_hash": "forged"}):
            with self.assertRaises(ContractError):
                validate_supplement(dict(task, materials=[dict(record, **change)]), proposal)
        with self.assertRaises(ContractError):
            validate_supplement(dict(task, supplement_history=[{"proposal": proposal}]), proposal)
        with self.assertRaises(ContractError):
            validate_supplement(task, dict(proposal, artifact_ids=["missing"]))

    def test_permission_failure_q2_does_not_block_independent_q3_other_source(self):
        task = copy.deepcopy(self.task)
        task["questions"] += [dict(task["questions"][0], id=ident, question=ident) for ident in ("q2", "q3")]
        failed = copy.deepcopy(self.need)
        failed["question_ids"] = ["q2"]
        task["data_need_specs"] = [failed]
        digest = fingerprint({key: failed[key] for key in ("source", "operation", "parameters")})
        task["ledger"]["dispatches"] = [{"kind": "data_requests", "fingerprint": digest, "failure_code": "permission_denied", "status": "权限未通过"}]
        proposal = copy.deepcopy(self.proposal)
        proposal.update(question_ids=["q3"], reason="独立q3待核验", expected_change="核实q3独立字段")
        proposal["data_requests"][0].update(id="q3_independent", question_ids=["q3"], source="tinyshare", operation="daily",
            selection_reason="原问题独立q3使用已授权Tinyshare", parameters={"ts_code": "600000.SH", "fields": "open", "start_date": "20260929", "end_date": "20260930"})
        validate_supplement(task, proposal)
        same_question = copy.deepcopy(proposal)
        same_question["question_ids"] = ["q2"]
        same_question["data_requests"][0]["question_ids"] = ["q2"]
        with self.assertRaises(ContractError):
            validate_supplement(task, same_question)
        same_source = copy.deepcopy(self.proposal)
        same_source["question_ids"] = ["q3"]
        same_source["data_requests"][0].update(id="new_q3", question_ids=["q3"])
        same_source["data_requests"][0]["parameters"]["indicators"] = "open"
        with self.assertRaises(ContractError):
            validate_supplement(task, same_source)

    def test_source_attempts_and_reordered_field_semantics_remain_spent(self):
        digest = fingerprint({key: self.need[key] for key in ("source", "operation", "parameters")})
        for status in ("待响应", "失败或中断"):
            with self.assertRaises(ContractError):
                validate_supplement(dict(self.task, source_attempts={digest: {"status": status}}), self.proposal)
        old = copy.deepcopy(self.need)
        old["parameters"]["indicators"] = "open,close"
        proposal = copy.deepcopy(self.proposal)
        proposal["data_requests"][0]["id"] = "different_name"
        proposal["data_requests"][0]["parameters"]["indicators"] = "close,open"
        with self.assertRaises(ContractError):
            validate_supplement(dict(self.task, data_need_specs=[old]), proposal)

    def test_same_material_can_be_reread_for_different_unanswered_question(self):
        task = copy.deepcopy(self.task)
        task["questions"].append(dict(task["questions"][0], id="q2", question="独立q2"))
        snapshot = {"text": "同一原件的两个问题"}
        record = {"id": "material_a", "task_id": task["id"], "research_version": 1, "kind": "material", "snapshot": snapshot, "snapshot_hash": fingerprint(snapshot)}
        task["materials"] = [record]
        prior = dict(self.proposal, data_requests=[], artifact_ids=["material_a"])
        task["supplement_history"] = [{"proposal": prior}]
        proposal = dict(prior, question_ids=["q2"], reason="独立q2定位", expected_change="回答q2")
        validate_supplement(task, proposal)
        with self.assertRaises(ContractError):
            validate_supplement(task, dict(proposal, question_ids=["q1", "q2"]))


if __name__ == "__main__":
    unittest.main()
