"""Forty synthetic host workflows, not forty independently judged studies.

Each entry uses different original statements. Intent selects the requested
output; unsupported buying claims remain opposed, not invented opportunities.
The existing positive valuation fixture remains a separate acceptance case.
"""
import copy
import importlib.util
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import DOCUMENT_CONTENT_INTENT, ENTRIES, INTENTS, evidence_requirements
from researchhelper.engine import ResearchAgent
from researchhelper.planning import research_brief

ROOT = Path(__file__).resolve().parents[2]
CUTOFF = "2026-07-20T18:00:00+08:00"
PUBLICATION = "发布日期：2026年7月19日。"
SCENARIOS = {
    "个股": ("合成公司甲", "合成公司乙", "公司甲主营订单已进入交付，回款条件已确认。", "公司乙订单仍为意向，利润改善主要来自资产处置。", "订单与盈利兑现不能由意向或一次性收益替代"),
    "ETF": ("合成基金甲", "合成基金乙", "基金甲披露组合确有用户研究的目标业务暴露。", "基金乙仅名称相近，披露组合不包含目标业务暴露。", "基金主题名称不能替代实际暴露"),
    "指数": ("合成市值指数甲", "合成等权指数乙", "市值指数甲上涨集中在高权重成分，多数样本未改善。", "等权指数乙同期下跌，两者采用同一观察期及样本集合。", "权重集中与市场宽度必须分开判断"),
    "行业": ("合成行业样本甲", "合成行业样本乙", "行业样本甲需求改善，渠道和竞争条件保持稳定。", "行业样本乙受到渠道调整影响，份额和经营表现恶化。", "行业需求改善不能覆盖企业经营反证"),
    "产业链": ("合成资源环节甲", "合成加工环节乙", "资源环节甲受产品涨价支持，产量及成本条件未改变。", "加工环节乙原料随市价采购，加工费下降且产量不变。", "同一价格变化可能使不同环节受到相反影响"),
    "事件": ("合成资格群体甲", "合成资格群体乙", "已生效政策仅适用于资格群体甲，执行资格已核对。", "资格群体乙不满足政策条件，未披露其他直接受益渠道。", "政策存在不证明所有对象直接受益"),
    "宏观": ("合成浮息对象甲", "合成固定利率对象乙", "浮息对象甲合同按相关基准重定价，融资渠道可能改善。", "固定利率对象乙本期不重定价，盈利预期另受需求走弱影响。", "利率传导依赖敞口与期限，不能预设净收益方向"),
    "混合": ("合成政策对象甲", "合成费用对象乙", "政策对象甲资格满足，但材料没有价格及盈利兑现依据。", "费用对象乙仅披露合同费用，费用差异不代表实际持有收益。", "独立政策与费用问题不强造因果或重复机会"),
}


def specification(entry, intent):
    first, second, statement_a, statement_b, finding = SCENARIOS[entry]
    comparative = intent == "比较对象"
    text = {
        "综合研究": "研究材料中的现状与适用边界，不推断材料外的价格结论",
        "解释变化": "解释材料中对象表现或条件不同的渠道及限制",
        "比较对象": "比较材料中两个对象的已披露条件，不给买入建议",
        "验证观点": "验证两个对象可因相近名称或同一驱动被视为同样受益的观点",
        "发现投资机会": "现有材料是否足以支持直接买入判断，若不足说明限制，不补造价格依据",
    }[intent]
    questions = [
        dict(id="basis", kind="fact", group="basis", question="两个合成对象各自披露了什么条件",
             evidence_needs=["对象甲的原始陈述", "对象乙的原始陈述"]),
        dict(id="result", kind="comparison" if comparative else "investment" if intent == "发现投资机会" else "mechanism",
             group="result", question=text, dependencies=["basis"],
             evidence_needs=["对象甲的原始陈述", "对象乙的原始陈述"]),
        dict(id="extension", kind="fact", group="extension", question="可选的其他样本材料", required=False),
    ]
    answer = finding + "。仅限合成原件披露，未提供材料外价格、交易或净收益依据。"
    judgment = "反对" if intent in ("验证观点", "发现投资机会") else "支持"
    thesis = dict(title="不能据此形成直接买入判断" if intent == "发现投资机会" else finding,
                  judgment=judgment, question_ids=["result"], support=["original_a"], counter=["original_b"],
                  calculation_refs=[], investment_meaning=answer,
                  limitation="没有覆盖其他样本或材料外的价格条件，不推出目标收益。")
    return dict(entry=entry, intent=intent, question=text, objects=[first, second], questions=questions,
                statements=[statement_a, statement_b], finding=finding,
                answers={"basis": dict(answer=statement_a + statement_b, refs=["original_a", "original_b"]),
                         "result": dict(answer=answer, refs=["original_a", "original_b"])},
                theses=[] if comparative else [thesis])


def execute(agent, case):
    """Run through normal materials and two-class verification, no raw approvals."""
    task = agent.create(case["question"], entry=case["entry"], intent=case["intent"], objects=case["objects"],
                        cutoff=CUTOFF, questions=case["questions"])
    task["synthetic"] = True
    agent.archive.save(task)
    specs = []
    for index, text in enumerate(case["statements"]):
        task = agent.add_material(task["id"], "synthetic-original-" + str(index) + ".txt",
                                  (PUBLICATION + "\n\n" + text).encode())
        artifact = task["materials"][-1]
        need_ids = {question["id"]: [evidence_requirements(question)[index]["id"]]
                    for question in task["questions"] if question["id"] in ("basis", "result")}
        specs.append(dict(id="original_a" if index == 0 else "original_b", artifact_id=artifact["id"],
                          object=case["objects"][index], text=text, record_locator={"part": "段落2"},
                          period="合成材料期", nature="合同政策条款" if case["entry"] == "宏观" else "已实现事实", unit="不适用", question_ids=["basis", "result"],
                          published_at="2026-07-19", publication=dict(at="2026-07-19", precision="日", timezone="Asia/Shanghai",
                              certainty="已确认", basis="合成原件明确发布日期", basis_ref=dict(kind="material", artifact_id=artifact["id"],
                                  snapshot_hash=artifact["snapshot_hash"], part="段落1", quote=PUBLICATION)),
                          review=dict(question_ids=["basis", "result"], allowed_natures=["合同政策条款", "已实现事实"],
                                      conditions=["仅用于合成材料的声明、差异和适用限制"],
                                      reason="逐字原件与公开时点匹配，开发样本不对应真实证券", evidence_need_ids=need_ids)))
    brief = research_brief(task)
    result = agent.start(task["id"], dict(evidence_specs=specs, answers=case["answers"], theses=case["theses"]))
    return result, brief


def semantic_projection(task):
    return dict(entry=task["entry"], intent=task["intent"], question=task["question"], objects=task["objects"], status=task["status"],
                answers=[{key: q.get(key) for key in ("id", "kind", "required", "status", "answer")} for q in task["questions"]],
                theses=[{key: thesis.get(key) for key in ("title", "judgment", "question_ids", "investment_meaning", "limitation")}
                        for thesis in task["theses"]])


class ResearchWorkflowMatrixTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="research-matrix-", dir=ROOT / "result/acceptance")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.agent = ResearchAgent(ResearchArchive(self.directory / "app"), ROOT, offline=True)
        denied = patch.object(socket.socket, "connect", side_effect=AssertionError("No external calls"))
        denied.start()
        self.addCleanup(denied.stop)

    def test_forty_actual_archived_host_workflows_and_source_skill_parity(self):
        original_paths = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), original_paths))
        # The source guide folder is not an installed Skill: rendering assets
        # are assembled by the builder. Use its genuine disposable layout,
        # without changing the project installation or patching render paths.
        builder_spec = importlib.util.spec_from_file_location("matrix_skill_builder", ROOT / "packaging/assemble_release.py")
        builder = importlib.util.module_from_spec(builder_spec)
        builder_spec.loader.exec_module(builder)
        builder.OUTPUT = self.directory / "skill-candidate"
        builder.OUTPUT.mkdir()
        builder.skill(install=False)
        spec = importlib.util.spec_from_file_location("matrix_source_skill", builder.OUTPUT / "researchhelper/core/session.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        session = module.Session(self.directory / "skill-project")
        self.assertIsNone(session.agent.model)
        combinations = []
        for entry in ENTRIES:
            for intent in INTENTS:
                if intent == DOCUMENT_CONTENT_INTENT:
                    continue
                with self.subTest(entry=entry, intent=intent):
                    case = specification(entry, intent)
                    before = copy.deepcopy(case)
                    actual, brief = execute(self.agent, case)
                    hosted, hosted_brief = execute(session.agent, case)
                    self.assertEqual(actual["status"], "已完成", actual.get("error"))
                    self.assertEqual(hosted["status"], "已完成", hosted.get("error"))
                    self.assertEqual(case, before)
                    self.assertEqual(semantic_projection(actual), semantic_projection(hosted))
                    self.assertEqual(brief["entry_focus"], hosted_brief["entry_focus"])
                    self.assertEqual(next(q for q in actual["questions"] if q["id"] == "extension")["status"], "未回答")
                    self.assertEqual(len(actual["theses"]), 0 if intent == "比较对象" else 1)
                    for agent, task in ((self.agent, actual), (session.agent, hosted)):
                        self.assertTrue(task["synthetic"])
                        self.assertEqual(task["ledger"]["used"]["model_calls"], 0)
                        self.assertEqual(task["ledger"]["used"]["data_requests"], 0)
                        report = agent.export(task["id"])
                        self.assertIn(case["finding"], report["html"])
                        for statement in case["statements"]:
                            self.assertIn(statement, report["html"])
                        recovered = ResearchAgent(ResearchArchive(agent.archive.root), ROOT, offline=True)
                        self.assertEqual(semantic_projection(recovered.archive.get(task["id"])), semantic_projection(task))
                    combinations.append((entry, intent))
        self.assertEqual(len(combinations), 40)
        self.assertEqual(len(set(combinations)), 40)


if __name__ == "__main__":
    unittest.main()
