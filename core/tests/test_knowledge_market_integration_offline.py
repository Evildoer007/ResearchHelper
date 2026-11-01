"""Question-driven knowledge and bounded market-background planning integration.

Every model response and provider record is fabricated. No supplier, model,
production store or foreground application is used.
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper import knowledge
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.planning import question_specs
from researchhelper.reasoning import COMMON_RULE, PHASE_RULES, validate_plan
from researchhelper.reasoning import validate_supplement

ROOT = Path(__file__).resolve().parents[2]
CUTOFF = "2026-10-02T18:00:00+08:00"


class KnowledgeMarketIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / "result/acceptance/knowledge-market-integration"
        directory.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(prefix="isolated-", dir=directory)
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True)
        network = patch("socket.socket.connect", side_effect=AssertionError("No external networking"))
        network.start()
        self.addCleanup(network.stop)

    def task(self, **values):
        return self.agent.create("分析指定范围", cutoff=CUTOFF, **values)

    @staticmethod
    def question(ident, text, required=True):
        return {"id": ident, "question": text, "kind": "mechanism", "required": required}

    def test_host_adopts_required_subquestion_not_entire_library(self):
        task = self.task(entry="宏观", intent="解释变化", questions=[
            self.question("q1", "分析指定范围"), self.question("q2", "降息经由信贷如何传导"),
            self.question("q3", "ETF跟踪差异", required=False)])
        final = self.agent.start(task["id"], {})
        package = final["knowledge"]
        self.assertIn("rh_k_rate_transmission", package["index"])
        self.assertNotIn("rh_k_etf_exposure", package["index"])
        self.assertIn("q2", package["selection_reasons"]["rh_k_rate_transmission"]["question_ids"])
        self.assertEqual(package["retrieval_context"]["intent"], "解释变化")
        self.assertEqual(final["status"], "部分完成")
        self.assertFalse(final["evidence"] or final["theses"])
        self.assertEqual(final["ledger"]["used"]["model_calls"], 0)

    def test_model_plan_new_subquestion_adopts_mechanism_before_analysis(self):
        task = self.task(entry="个股")
        proposal = {"questions": [self.question("q1", task["question"]),
            self.question("q2", "利润增长与现金流下降的原因")], "data_requests": [], "calculations": []}
        calls = []
        class FabricatedModel:
            def reason(self, task, messages):
                calls.append(copy.deepcopy(messages))
                return json.dumps(proposal, ensure_ascii=False)
        self.agent.offline = False
        self.agent.model = FabricatedModel()
        final = self.agent.start(task["id"])
        self.assertEqual(final["status"], "部分完成", final.get("error"))
        self.assertIn("rh_k_cash_profit", final["knowledge"]["index"])
        self.assertIn("q2", final["knowledge"]["selection_reasons"]["rh_k_cash_profit"]["question_ids"])
        self.assertEqual(len(calls), 1)
        self.assertFalse(final["evidence"])

    def test_incremental_context_does_not_replace_adopted_version(self):
        task = self.agent.create("利率传导", entry="混合", cutoff=CUTOFF)
        self.agent._adopt_method_knowledge(task)
        old = copy.deepcopy(next(item for item in task["knowledge"]["entries"] if item["id"] == "rh_k_rate_transmission"))
        revised = copy.deepcopy(knowledge.ENTRIES)
        next(item for item in revised if item["id"] == old["id"]).update(version=2, content=old["content"] + "普通维护修订。")
        task["questions"].append(dict(self.question("q2", "现金流与资本开支"), status="待回答"))
        with patch.object(knowledge, "ENTRIES", revised):
            self.agent._adopt_method_knowledge(task)
            self.assertEqual(task["knowledge"]["index"][old["id"]], 1)
            self.assertEqual(next(item for item in task["knowledge"]["entries"] if item["id"] == old["id"]), old)
            self.assertIn("rh_k_cash_profit", task["knowledge"]["index"])
            self.agent.knowledge.validate_package(task["knowledge"])
            new = self.agent.create("利率传导", entry="混合", cutoff=CUTOFF)
            self.agent._adopt_method_knowledge(new)
            self.assertEqual(new["knowledge"]["index"][old["id"]], 2)

    def test_no_retrieval_change_preserves_identity_checkpoint(self):
        task = self.agent.create("ETF实际暴露", entry="ETF", cutoff=CUTOFF)
        self.agent._adopt_method_knowledge(task)
        node = next(node for node in task["nodes"] if node["id"] == "identity")
        node["status"] = "成功"
        before = fingerprint(task["knowledge"])
        self.agent._adopt_method_knowledge(task)
        self.assertEqual(fingerprint(task["knowledge"]), before)
        self.assertEqual(node["status"], "成功")

    def test_newly_adopted_knowledge_invalidates_identity_manifest(self):
        task = self.task(entry="混合")
        self.agent._adopt_method_knowledge(task)
        node = next(node for node in task["nodes"] if node["id"] == "identity")
        node["status"] = "成功"
        task["questions"].append(dict(self.question("q2", "指数权重与市场宽度"), status="待回答"))
        self.agent._adopt_method_knowledge(task)
        self.assertEqual(node["status"], "待执行")
        self.assertIn("rh_k_index_structure", task["knowledge"]["index"])

    def test_checkpoint_retained_entry_is_not_falsely_reported_as_capacity_omission(self):
        query = "利率 指数 ETF 跟踪 成本 毛利 现金流"
        task = self.agent.create(query, entry="混合", cutoff=CUTOFF, questions=[
            self.question("q1", query), self.question("mixed", "利率与现金流"),
            self.question("other_a", "利率 指数 ETF 跟踪 成本 毛利"),
            self.question("other_b", "利率 指数 ETF 跟踪 成本 毛利"), self.question("rates", "利率传导")])
        task["knowledge"] = self.agent.knowledge.read("混合", "现金流")
        self.assertIn("rh_k_cash_profit", task["knowledge"]["index"])
        self.agent._adopt_method_knowledge(task)
        omitted = [entry["id"] for question in task["knowledge"]["unmatched_questions"] for entry in question.get("omitted_entries", [])]
        self.assertNotIn("rh_k_cash_profit", omitted)
        self.assertFalse(any("未采用：利润、现金流" in reason for reason in task["knowledge"]["unmatched"]))

    def test_catalog_is_browse_only_and_does_not_expand_task_retrieval(self):
        items = self.agent.knowledge.catalog()
        self.assertEqual(len(items), len(knowledge.ENTRIES))
        cash = next(item for item in items if item["id"] == "rh_k_cash_profit")
        cash["content"] = "浏览器伪造内容"
        self.assertNotEqual(cash, next(item for item in self.agent.knowledge.catalog() if item["id"] == "rh_k_cash_profit"))
        adopted = self.agent.knowledge.read("宏观", "利率")
        self.assertNotIn("rh_k_cash_profit", adopted["index"])

    def market_task(self):
        return self.agent.create("核对600000.SH的估值与市值背景", entry="个股", objects=["600000.SH"], cutoff=CUTOFF)

    @staticmethod
    def plan(task, **changes):
        parameters = {"ts_code": "600000.SH", "fields": "ts_code,trade_date,pe_ttm,total_mv",
                      "start_date": "20260929", "end_date": "20260930"}
        parameters.update(changes)
        return {"questions": question_specs(task),
                "data_requests": [{"id": "market_background", "source": "tinyshare", "operation": "daily_basic",
                    "parameters": parameters, "question_ids": ["q1"],
                    "selection_reason": "明确选择Tinyshare的已登记市场背景口径；未验证权限，不静默切换"}], "calculations": []}

    def test_daily_basic_plan_accepts_only_explicit_registered_background(self):
        task = self.market_task()
        proposed = self.plan(task)
        before = fingerprint(proposed)
        self.assertEqual(validate_plan(task, proposed)[0]["id"], "q1")
        self.assertEqual(fingerprint(proposed), before)
        self.assertEqual(task["ledger"]["used"]["data_requests"], 0)

    def test_plan_and_dispatch_share_keys_fields_dates_and_scope_constraints(self):
        task = self.market_task()
        for changes in ({"fields": "pe_ttm"}, {"fields": "ts_code,trade_date"},
            {"fields": "ts_code,trade_date,close"}, {"fields": "ts_code,trade_date,pe,pe"},
            {"start_date": "2026-09-29"}, {"start_date": "20260230"}, {"end_date": "20261003"},
            {"ts_code": "600001.SH"}, {"ts_code": "600000.SH,600001.SH"}, {"ts_code": "800001.BJ"}):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                validate_plan(task, self.plan(task, **changes))
        self.assertEqual(task["ledger"]["used"]["data_requests"], 0)

    def test_names_or_no_selection_reason_do_not_authorize_provider_calls(self):
        task = self.agent.create("研究腾讯控股", entry="个股", cutoff=CUTOFF)
        with self.assertRaises(ContractError):
            validate_plan(task, self.plan(task))
        task = self.market_task()
        proposed = self.plan(task)
        proposed["data_requests"][0].pop("selection_reason")
        with self.assertRaises(ContractError):
            validate_plan(task, proposed)

    def test_phase_rules_preserve_financial_and_time_boundaries(self):
        self.assertIn("不充当主体事实或历史当时规则", COMMON_RULE)
        for phase in ("plan", "supplement"):
            self.assertIn("YYYYMMDD", PHASE_RULES[phase])
            self.assertIn("不反推利润或收入", PHASE_RULES[phase])
        self.assertIn("空PE不填零", PHASE_RULES["analyze"])
        self.assertIn("不能用交易日", PHASE_RULES["extract"])

    def test_permission_failure_preserves_independent_valid_tinyshare_branch(self):
        from tests.test_autonomous_supplement_contracts_offline import SupplementContractsTests
        fixture = SupplementContractsTests()
        fixture.setUp()
        task = copy.deepcopy(fixture.task)
        task["questions"] += [dict(task["questions"][0], id=ident, question=ident) for ident in ("q2", "q3")]
        failed = dict(copy.deepcopy(fixture.need), question_ids=["q2"])
        task["data_need_specs"] = [failed]
        digest = fingerprint({key: failed[key] for key in ("source", "operation", "parameters")})
        task["ledger"]["dispatches"] = [{"kind": "data_requests", "fingerprint": digest,
            "failure_code": "permission_denied", "status": "权限未通过"}]
        proposed = copy.deepcopy(fixture.proposal)
        proposed.update(question_ids=["q3"], reason="独立q3待核验", expected_change="核实q3独立字段")
        proposed["data_requests"][0].update(id="q3_independent", question_ids=["q3"], source="tinyshare", operation="daily",
            selection_reason="原问题独立q3明确选择Tinyshare", parameters={"ts_code": "600000.SH",
                "fields": "ts_code,trade_date,open", "start_date": "20260929", "end_date": "20260930"})
        validate_supplement(task, proposed)
        same_question = copy.deepcopy(proposed)
        same_question["question_ids"] = ["q2"]
        same_question["data_requests"][0]["question_ids"] = ["q2"]
        with self.assertRaises(ContractError):
            validate_supplement(task, same_question)
        same_source = copy.deepcopy(fixture.proposal)
        same_source["question_ids"] = ["q3"]
        same_source["data_requests"][0].update(id="new_q3", question_ids=["q3"])
        same_source["data_requests"][0]["parameters"]["indicators"] = "open"
        with self.assertRaises(ContractError):
            validate_supplement(task, same_source)


if __name__ == "__main__":
    unittest.main()
