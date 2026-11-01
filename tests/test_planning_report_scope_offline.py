"""Report format must not introduce judgment obligations; no live calls."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.output_schema import planning_output_schema, decode_planning_output
from researchhelper.planning import (input_fingerprint, planning_scope_policy,
    question_contract, question_specs, research_brief, validate_question_revision)

ROOT = Path(__file__).resolve().parents[1]
QUESTION = ('用iFinD核对600519.SH2026年上半年合并营业收入、报表净利润和经营现金流净额，'
            '计算净利润率和经营现金流对净利润的覆盖，并基于这三个指标生成一页带图表的研究报告。')


class PlanningReportScopeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        network = patch('socket.create_connection', side_effect=AssertionError('No network allowed'))
        network.start()
        self.addCleanup(network.stop)
        agent = ResearchAgent(ResearchArchive(Path(folder.name)), ROOT, offline=True)
        self.task = agent.create(QUESTION, entry='个股', objects=['600519.SH'])
        self.task['planning_scope_version'] = 1
        self.task['input_hash'] = input_fingerprint(self.task)

    def test_policy_is_versioned_and_bound_without_mutating_task(self):
        original = copy.deepcopy(self.task)
        scope = planning_scope_policy(self.task)
        self.assertEqual(research_brief(self.task)['requested_content_policy'], scope)
        self.assertEqual(question_contract(self.task)['requested_content_policy'], scope)
        self.assertEqual(self.task, original)
        legacy = copy.deepcopy(self.task)
        legacy.pop('planning_scope_version')
        self.assertNotEqual(input_fingerprint(legacy), self.task['input_hash'])
        for value in (None, True, '1', 2):
            legacy['planning_scope_version'] = value
            self.assertIsNone(planning_scope_policy(legacy))
        self.assertNotIn('requested_content_policy', question_contract())

    def test_schema_and_validator_accept_fact_without_losing_calculations(self):
        schema = planning_output_schema(self.task)
        if 'main_question' in schema['properties']:
            branches = schema['properties']['main_question']['anyOf']
        else:
            branches = schema['properties']['questions']['items']['anyOf']
        self.assertEqual(branches[0]['properties']['kind']['description'],
                         planning_scope_policy(self.task)['rule'])
        questions = question_specs(self.task)
        questions[0].update(kind='fact', method_needs=[
            'net_profit_margin_v1', 'operating_cash_profit_ratio_v1'])
        validate_question_revision(self.task, questions)
        wire = dict(version=schema['properties']['version']['enum'][0],
                    data_requests=[], calculations=[])
        if wire['version'] == 3:
            wire.update(main_question=questions[0], additional_questions=[])
        else:
            wire['questions'] = questions
        decoded = decode_planning_output(wire, schema)
        self.assertEqual(decoded['questions'][0]['question'], QUESTION)
        self.assertEqual(decoded['questions'][0]['method_needs'], questions[0]['method_needs'])

    def test_real_judgments_remain_available_and_adopted_kind_is_protected(self):
        rule = planning_scope_policy(self.task)['rule']
        for requirement in ('投资判断', '估值', '盈利质量评价', '原因或驱动解释', '开放式综合研究'):
            self.assertIn(requirement, rule)
        questions = question_specs(self.task)
        questions[0]['kind'] = 'investment'
        validate_question_revision(self.task, questions)
        adopted = copy.deepcopy(self.task)
        adopted['plan_version'] = 2
        downgraded = copy.deepcopy(questions)
        downgraded[0]['kind'] = 'fact'
        with self.assertRaises(ContractError):
            validate_question_revision(adopted, downgraded)


if __name__ == '__main__':
    unittest.main()
