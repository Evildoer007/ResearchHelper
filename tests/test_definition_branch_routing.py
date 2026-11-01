"""Definition routing must not abort an independent fact branch.

All cases use isolated stores and block network access. No retained study is
resumed, and a deterministic concept explanation never approves market facts.
"""
import copy
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import ResearchKnowledge, RETAINED_VERSIONS
from researchhelper.output_schema import planning_output_schema, analysis_output_schema
from researchhelper.reasoning import analysis_contract


class DefinitionBranchRoutingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='definition-routing-', dir=ROOT / 'result/acceptance')
        self.addCleanup(folder.cleanup)
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline checks only'))
        guard.start(); self.addCleanup(guard.stop)
        self.agent = ResearchAgent(ResearchArchive(Path(folder.name) / 'store'), ROOT,
                                   offline=True, require_argument_support=True)
        self.knowledge = ResearchKnowledge()

    def task(self, knowledge_id='rh_k_price_return_measures', only_definition=False):
        questions = [] if only_definition else [dict(id='q1', kind='fact', question='查询600519.SH收盘价',
            evidence_needs=['同一原件的未复权收盘价'], method_needs=['observed_price_return_v1'])]
        questions.append(dict(id='q2', kind='fact', question='价格变化与含分红总收益有何区别',
            answer_scope='general_definition', knowledge_needs=[knowledge_id]))
        task = self.agent.create('研究600519.SH未复权价格，并解释价格变化与总收益的区别',
            entry='个股', questions=questions)
        self.agent._adopt_method_knowledge(task)
        return task

    def test_new_formal_tasks_version_definition_projection(self):
        task = self.task('rh_k_price_change')
        self.assertEqual(task.get('definition_projection_version'), 1)

    def test_planner_can_select_only_published_applicable_definitions(self):
        task = self.task('rh_k_price_change')
        general = planning_output_schema(task)['properties']['questions']['items']['anyOf'][2]
        choices = general['properties']['knowledge_needs']['items']['enum']
        self.assertIn('rh_k_price_return_measures', choices)
        self.assertNotIn('rh_k_price_change', choices)
        self.assertNotIn('rh_k_cost_pass', choices)
        self.assertNotIn('rh_k_etf_measures', choices)

    def test_wrong_role_remains_unready_without_erasing_independent_fact_scope(self):
        task = self.task('rh_k_price_change')
        before = fingerprint(task)
        contract = analysis_contract(task)['by_question']
        self.assertIn('q1', contract)
        self.assertFalse(contract['q2']['definition']['generation_ready'])
        self.assertFalse(contract['q2']['definition']['fact_approval'])
        self.assertEqual(contract['q2']['definition']['missing_knowledge_ids'], ['rh_k_price_change'])
        self.assertEqual(fingerprint(task), before)
        with self.assertRaises(ContractError):
            self.knowledge.definition_answer(task, task['questions'][1])

    def test_legacy_contract_still_rejects_wrong_role(self):
        task = self.task('rh_k_price_change')
        task.pop('definition_projection_version', None)
        with self.assertRaises(ContractError):
            analysis_contract(task)

    def test_tampered_package_still_aborts_instead_of_becoming_missing(self):
        task = self.task('rh_k_price_change')
        task['knowledge']['entries'][0]['content'] += '保证盈利'
        with self.assertRaises(ContractError):
            analysis_contract(task)

    def test_mixed_model_container_excludes_deterministic_definitions(self):
        task = self.task('rh_k_price_change')
        answers = analysis_output_schema(task)['properties']['answers']
        self.assertEqual(answers['items']['properties']['question_id']['enum'], ['q1'])

    def test_definition_only_container_forbids_model_answers(self):
        task = self.task(only_definition=True)
        self.assertEqual(analysis_output_schema(task)['properties']['answers']['maxItems'], 0)

    def test_valid_concept_is_projected_without_facts_calculation_or_model(self):
        task = self.task(only_definition=True)
        before = fingerprint(RETAINED_VERSIONS[('rh_k_price_change', 1)])
        # Persist the explicitly prepared isolated task, never a real study.
        self.agent.archive.save(task)
        done = self.agent.start(task['id'])
        self.assertEqual(done['status'], '已完成', done.get('error'))
        self.assertEqual(done['questions'][0]['status'], '已回答')
        self.assertIn('不代表当前研究对象事实', done['questions'][0]['answer'])
        self.assertFalse(done['questions'][0]['knowledge_integrity']['fact_approval'])
        self.assertFalse(done['evidence'] or done['calculations'] or done['theses'])
        self.assertEqual(done['ledger']['used']['model_calls'], 0)
        self.assertEqual(done['ledger']['used']['data_requests'], 0)
        self.assertEqual(fingerprint(RETAINED_VERSIONS[('rh_k_price_change', 1)]), before)

    def test_valid_concept_never_accepts_appended_investment_conclusion(self):
        task = self.task(only_definition=True)
        question = task['questions'][0]
        bound = self.knowledge.definition_answer(task, question)
        self.assertIn('不等于', bound['answer'])
        with self.assertRaises(ContractError):
            self.knowledge.definition_answer(task, question,
                dict(bound, answer=bound['answer'] + '因此应当买入'))

    def test_legacy_planning_schema_is_unchanged(self):
        task = self.task('rh_k_price_change')
        task.pop('definition_projection_version', None)
        general = planning_output_schema(task)['properties']['questions']['items']['anyOf'][2]
        self.assertNotIn('enum', general['properties']['knowledge_needs']['items'])


if __name__ == '__main__':
    unittest.main()
