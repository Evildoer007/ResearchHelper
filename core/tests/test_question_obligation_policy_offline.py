"""Independent checks for output-led planning, not model-quality acceptance."""

import copy
import tempfile
import unittest
from pathlib import Path

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, METHOD_REGISTRY
from researchhelper.engine import ResearchAgent
from researchhelper.planning import question_contract
from researchhelper.planning import question_specs
from researchhelper.reasoning import validate_plan

ROOT = Path(__file__).resolve().parents[2]


class QuestionObligationPolicyTests(unittest.TestCase):
    def test_price_check_does_not_imply_nav_return_or_investment(self):
        policy = question_contract()["obligation_policy"]
        example = policy["requested_outputs"]["区间未复权价格变化"]
        self.assertEqual(example["kind"], "fact")
        self.assertEqual(example["method"], "observed_price_return_v1")
        self.assertIn("净值收益", example["not_implied"])
        self.assertIn("公司行为状态", example["necessary"])
        self.assertFalse(policy["examples_are_facts"])

    def test_other_requested_outputs_keep_their_real_requirements(self):
        outputs = question_contract()["obligation_policy"]["requested_outputs"]
        self.assertIn("官方净值", outputs["ETF折溢价"]["necessary"])
        self.assertIn("现金分配", outputs["持有人总回报"]["necessary"])
        self.assertIsNone(outputs["持有人总回报"]["method"])
        self.assertIn("竞争解释", outputs["事件影响机制"]["necessary"])
        self.assertEqual(outputs["投资机会判断"]["kind"], "investment")
        self.assertIn("反证", outputs["投资机会判断"]["necessary"])

    def test_examples_do_not_register_new_methods_or_mutate_contract(self):
        before = copy.deepcopy(METHOD_REGISTRY)
        contract = question_contract()
        contract["obligation_policy"]["requested_outputs"].clear()
        self.assertEqual(len(question_contract()["obligation_policy"]["requested_outputs"]), 5)
        self.assertEqual(before, METHOD_REGISTRY)
        for item in question_contract()["obligation_policy"]["requested_outputs"].values():
            if item["method"] is not None:
                self.assertIn(item["method"], METHOD_REGISTRY)

    def test_initial_default_can_classify_price_question_without_changing_text(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = ResearchAgent(ResearchArchive(Path(directory)), ROOT, offline=True)
            text = "核查指定ETF两日价格变化，说明不等于总回报，不作买卖推荐。"
            task = agent.create(text, entry="ETF", objects=["合成测试ETF"])
            self.assertEqual(task["question_origin"], "default")
            questions = [{"id": "q1", "question": text, "kind": "fact", "required": True,
                "evidence_needs": ["同证券两端原价与必要口径"], "method_needs": ["observed_price_return_v1"]}]
            revised = agent.revise_plan(task["id"], questions, "按要求核对价格，不扩大分析", task["input_hash"])
            self.assertEqual(revised["question"], text)
            self.assertEqual(revised["questions"][0]["kind"], "fact")
            self.assertEqual(revised["ledger"]["used"]["model_calls"], 0)

    def test_policy_cannot_erase_an_adopted_necessary_requirement(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = ResearchAgent(ResearchArchive(Path(directory)), ROOT, offline=True)
            questions = [{"id": "q1", "question": "比较真实净值收益", "kind": "comparison",
                "evidence_needs": ["两日官方净值"], "required": True}]
            task = agent.create("比较真实净值收益", questions=questions)
            replacement = copy.deepcopy(questions)
            replacement[0]["evidence_needs"] = []
            with self.assertRaises(ContractError):
                agent.revise_plan(task["id"], replacement, "不得以新策略绕过缺口", task["input_hash"])
            self.assertEqual(agent.archive.get(task["id"]), task)

    def test_model_and_public_host_plan_apply_the_same_first_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = ResearchAgent(ResearchArchive(Path(directory)), ROOT, offline=True)
            task = agent.create("政策何时生效？")
            questions = question_specs(task)
            questions[0]["kind"] = "fact"
            expected = validate_plan(task, {"questions": questions, "data_requests": [], "calculations": []})
            revised = agent.revise_plan(task["id"], questions, "首次核查条款日期", task["input_hash"])
            self.assertEqual(question_specs(revised), expected)
            self.assertEqual(revised["ledger"], task["ledger"])
            self.assertEqual(revised["cutoff"], task["cutoff"])

    def test_initial_host_plan_cannot_delete_rewrite_or_downgrade_main_question(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = ResearchAgent(ResearchArchive(Path(directory)), ROOT, offline=True)
            task = agent.create("政策何时生效？")
            for field, value in (("id", "other"), ("question", "政策是否值得投资？"), ("required", False)):
                questions = question_specs(task)
                questions[0][field] = value
                with self.subTest(field=field), self.assertRaises(ContractError):
                    agent.revise_plan(task["id"], questions, "非法修改主问题", task["input_hash"])
                self.assertEqual(agent.archive.get(task["id"]), task)

    def test_adopted_host_plan_cannot_reclassify_again(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = ResearchAgent(ResearchArchive(Path(directory)), ROOT, offline=True)
            task = agent.create("政策何时生效？")
            questions = question_specs(task)
            questions[0]["kind"] = "fact"
            revised = agent.revise_plan(task["id"], questions, "首次确定问题类型", task["input_hash"])
            changed = question_specs(revised)
            changed[0]["kind"] = "investment"
            with self.assertRaises(ContractError):
                agent.revise_plan(task["id"], changed, "已采用问题不得改写", revised["input_hash"])
            self.assertEqual(agent.archive.get(task["id"]), revised)
