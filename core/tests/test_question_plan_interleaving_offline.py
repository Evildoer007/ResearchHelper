"""Question dependencies survive execution-node grouping without live services."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.cases import CUTOFF, get_case
from tests.host_fixture_support import policy_proposal
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.planning import build_plan, normalize_questions

ROOT = Path(__file__).resolve().parents[2]


def question(ident, kind="fact", dependencies=(), group="main"):
    return dict(id=ident, question=f"核查{ident}", kind=kind,
                dependencies=list(dependencies), group=group)


class InterleavingPlanTests(unittest.TestCase):
    def plan(self, questions, calculations=()):
        normalized = normalize_questions(questions, "核查研究依赖", "综合研究")
        return build_plan(dict(questions=normalized, calculation_specs=list(calculations),
                               ledger={"limits": {}}, input_hash="dependency-test"))

    def owners(self, nodes):
        return {q: node for node in nodes if node["kind"] in ("answer", "thesis")
                for q in node["questions"]}

    def assert_chain(self, nodes, ids):
        owners = self.owners(nodes)
        for before, after in zip(ids, ids[1:]):
            self.assertNotEqual(owners[before]["id"], owners[after]["id"])
            self.assertIn(owners[before]["id"], owners[after]["dependencies"])
            self.assertLess(nodes.index(owners[before]), nodes.index(owners[after]))

    def test_default_group_fact_mechanism_comparison(self):
        nodes = self.plan([question("q1"), question("q2", "mechanism", ["q1"]),
                           question("q3", "comparison", ["q2"])])
        self.assert_chain(nodes, ["q1", "q2", "q3"])

    def test_returning_to_same_route_and_group(self):
        nodes = self.plan([question("q1", group="a"), question("q2", dependencies=["q1"], group="b"),
                           question("q3", dependencies=["q2"], group="a"),
                           question("q4", dependencies=["q3"], group="b")])
        self.assert_chain(nodes, ["q1", "q2", "q3", "q4"])

    def test_real_question_cycle_is_rejected(self):
        with self.assertRaises(ContractError):
            self.plan([question("q1", dependencies=["q2"]), question("q2", dependencies=["q1"])])

    def test_safe_siblings_merge_and_independent_groups_stay_independent(self):
        nodes = self.plan([question("q1", group="a"), question("q2", group="a"),
                           question("q3", group="b")])
        owners = self.owners(nodes)
        self.assertEqual(owners["q1"]["id"], "answer")
        self.assertIs(owners["q1"], owners["q2"])
        self.assertEqual(owners["q3"]["id"], "answer_b")
        self.assertEqual(owners["q1"]["dependencies"], ["evidence"])
        self.assertEqual(owners["q3"]["dependencies"], ["evidence"])

    def test_dependent_siblings_merge_without_delaying_independent_question(self):
        nodes = self.plan([question("q1", "mechanism"), question("q2", dependencies=["q1"]),
                           question("q3", dependencies=["q1"]), question("q4")])
        owners = self.owners(nodes)
        self.assertIs(owners["q2"], owners["q3"])
        self.assertNotEqual(owners["q2"]["id"], owners["q4"]["id"])
        self.assertEqual(owners["q4"]["dependencies"], ["evidence"])

    def test_calculation_dependency_does_not_spread_to_unrelated_question(self):
        spec = dict(get_case("etf")["calculation"], question_ids=["q2"])
        nodes = self.plan([question("q1"), question("q2", "comparison")], [spec])
        owners = self.owners(nodes)
        self.assertEqual(owners["q1"]["dependencies"], ["evidence"])
        self.assertEqual(owners["q2"]["dependencies"], ["evidence", "calculation"])
        self.assertEqual(next(n for n in nodes if n["id"] == "calculation")["questions"], ["q2"])
        self.assertEqual(sum(n["kind"] == "evidence" for n in nodes), 1)

    def test_split_node_ids_do_not_collide_with_user_group_names(self):
        nodes = self.plan([question("q0", group="independent"), question("q1"), question("q2", "mechanism", ["q1"]),
                           question("q3", dependencies=["q2"]), question("q4", group="main_2"),
                           question("q5", group="main")])
        self.assertEqual(len(nodes), len({n["id"] for n in nodes}))
        self.assertEqual(self.owners(nodes)["q4"]["id"], "answer_main_2")
        self.assertEqual(self.owners(nodes)["q3"]["id"], "answer_main_3")
        self.assert_chain(nodes, ["q1", "q2", "q3"])

    def test_build_does_not_change_question_dependencies_or_groups(self):
        questions = [question("q1"), question("q2", "mechanism", ["q1"]),
                     question("q3", "comparison", ["q2"])]
        before = copy.deepcopy(questions)
        self.plan(questions)
        self.assertEqual(questions, before)

    def test_agent_interleaved_execution_saves_and_resumes(self):
        questions = [question("q1"), question("q2", "mechanism", ["q1"]),
                     question("q3", "comparison", ["q2"])]
        with tempfile.TemporaryDirectory(prefix="research-interleaving-") as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True)
            task = agent.create("核查政策、解释影响并比较", cutoff=CUTOFF, questions=questions)
            self.addCleanup(agent.close_runtime)
            text = "政策已生效。A与B适用同样条件，影响相同。"
            source = policy_proposal(agent, task["id"], "original", "合成政策", text, [q["id"] for q in questions])
            host = dict(evidence_specs=[source], answers={
                q["id"]: {"answer": text, "refs": ["original"]} for q in questions},
                theses=[dict(title="政策影响相同", judgment="支持", support=["original"], counter=[],
                             investment_meaning="仅解释适用影响", limitation="合成核验", question_ids=["q2"])])
            step = agent._step

            def cancel_after_first_answer(current, node, host_result):
                result = step(current, node, host_result)
                if node == "answer":
                    agent.cancel(current["id"])
                return result

            with patch.object(agent, "_step", side_effect=cancel_after_first_answer):
                stopped = agent.start(task["id"], host)
            self.assertEqual(stopped["status"], "已取消", stopped.get("error"))
            restored = ResearchAgent(ResearchArchive(folder), ROOT, offline=True)
            self.addCleanup(restored.close_runtime)
            resumed = restored.start(task["id"])
            self.assertEqual(resumed["status"], "已完成", resumed.get("error"))
            self.assertTrue(all(q["status"] == "已回答" for q in resumed["questions"]))
            self.assertEqual(resumed["cutoff"], CUTOFF)
            self.assertEqual(resumed["ledger"]["used"]["model_calls"], 0)
            self.assertEqual(resumed["ledger"]["used"]["data_requests"], 0)
            self.assert_chain(resumed["nodes"], ["q1", "q2", "q3"])
            self.assertEqual(restored.export(task["id"])["digest"], resumed["delivery"]["digest"])
