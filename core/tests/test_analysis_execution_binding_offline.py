"""Execution topology and calculation-input regressions, no external calls."""

import copy
import unittest
from unittest.mock import patch

from researchhelper import reasoning
from researchhelper.contracts import ContractError
from researchhelper.planning import build_plan
from tests import test_autonomous_reasoning_offline as fixtures


class AnalysisExecutionBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AutonomousReasoningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.task = self.fixture.policy()
        self.task['questions'] = [
            dict(self.task['questions'][0], kind='fact', group='price'),
            dict(self.task['questions'][0], id='q2', question='该政策的影响机制是什么',
                 kind='mechanism', group='mechanism'),
        ]
        self.task['nodes'] = build_plan(self.task)
        self.network = patch('socket.socket.connect', side_effect=AssertionError('External network forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_contract_matches_actual_thesis_node_not_display_group(self):
        contract = reasoning.analysis_execution_contract(self.task)
        self.assertEqual(contract['thesis_question_sets'], [['q2']])
        self.assertEqual(contract['answer_question_ids'], ['q1', 'q2'])
        self.assertFalse(contract['automatic_repair'])

    def test_shared_analysis_contract_exposes_same_node_grammar_to_skill_and_app(self):
        contract = reasoning.analysis_contract(self.task)
        self.assertEqual(contract['execution'], reasoning.analysis_execution_contract(self.task))

    def test_cross_node_thesis_is_rejected_without_mutation(self):
        proposal = {'answers': {}, 'theses': [{'question_ids': ['q1', 'q2']}]}
        before = copy.deepcopy(self.task)
        with self.assertRaisesRegex(ContractError, '执行节点'):
            reasoning.validate_analysis_execution(None, self.task, proposal, 0)
        self.assertEqual(self.task, before)

    def test_explicit_same_node_subset_is_allowed(self):
        reasoning.validate_analysis_execution(None, self.task,
            {'answers': {'q1': {}}, 'theses': [{'question_ids': ['q2']}]}, 0)

    def test_supplement_scope_cannot_reference_another_question(self):
        with self.assertRaises(ContractError):
            reasoning.validate_analysis_execution(None, self.task,
                {'answers': {'q1': {}}, 'theses': []}, 0, selected=['q2'])
        with self.assertRaises(ContractError):
            reasoning.analysis_execution_contract(self.task, selected=['unknown'])

    def test_requested_combined_thesis_uses_real_synthesis_node(self):
        task = copy.deepcopy(self.task)
        task['questions'][1]['kind'] = 'fact'
        task['requested_theses'] = True
        task['nodes'] = build_plan(task)
        reasoning.validate_analysis_execution(None, task,
            {'answers': {}, 'theses': [{'question_ids': ['q1', 'q2']}]}, 0)

    def test_ambiguous_unscoped_thesis_is_rejected(self):
        task = copy.deepcopy(self.task)
        task['questions'][0]['kind'] = 'mechanism'
        task['nodes'] = build_plan(task)
        with self.assertRaises(ContractError):
            reasoning.validate_analysis_execution(None, task, {'answers': {}, 'theses': [{}]}, 0)
        with self.assertRaises(ContractError):
            reasoning.validate_analysis_execution(None, task, {'answers': {}, 'theses': [{}]}, 0, selected=['q2'])

    def test_calculation_contract_attaches_context_to_actual_input_ids(self):
        task = copy.deepcopy(self.task)
        task['calculation_specs'] = [{'id': 'change', 'method': 'observed_price_return_v1',
            'inputs': {'start_price': 'start_value', 'end_price': 'end_value'}}]
        before = copy.deepcopy(task)
        contract = reasoning.calculation_input_requirements(task)
        bindings = contract['evidence_input_bindings']
        self.assertEqual(set(bindings), {'start_value', 'end_value'})
        self.assertEqual(bindings['start_value'][0]['input_name'], 'start_price')
        self.assertIn('observation_date', bindings['end_value'][0]['required_context'])
        bindings['start_value'][0]['required_context'].clear()
        self.assertEqual(task, before)

    def test_analysis_receives_detached_availability_and_registered_field_provenance(self):
        task = copy.deepcopy(self.task)
        task['evidence'] = [{'id': 'price', 'question_ids': ['q1'], 'artifact_id': 'source',
            'snapshot_hash': 'bound-hash', 'obtained_at': '2026-10-08T12:00:00+00:00',
            'availability': {'kind': 'current_snapshot', 'obtained_at': '2026-10-08T12:00:00+00:00'},
            'identity': {'object': '510300.SH'}, 'field_definition': {'version': 1},
            'snapshot': {'not_for_analysis': True}}]
        before = copy.deepcopy(task)
        result = self.fixture.agent._reasoning_evidence(task, ['q1'])
        self.assertEqual(result[0]['availability'], task['evidence'][0]['availability'])
        self.assertEqual(result[0]['field_definition'], {'version': 1})
        self.assertNotIn('snapshot', result[0])
        result[0]['availability'].clear()
        self.assertEqual(task, before)

    def test_bad_model_topology_is_rejected_before_cache_and_resume_does_not_replay_it(self):
        task = self.fixture.policy(limits={'data_requests': 100, 'model_calls': 5, 'seconds': 600})
        plan = self.fixture.plan(task)
        plan['questions'].append({'id': 'q2', 'question': '该政策的影响机制是什么',
                                 'kind': 'mechanism', 'required': True, 'group': 'other'})
        bad = self.fixture.answer()
        bad['theses'] = [{'question_ids': ['q1', 'q2'], 'title': 'DO_NOT_CACHE_CROSS_NODE',
            'judgment': '证据不足', 'support': ['effective_date'], 'counter': [],
            'investment_meaning': '政策影响尚待核验', 'limitation': '机制依据不足'}]
        model = fixtures.FabricatedModel([plan, {'evidence_specs': [self.fixture.policy_spec(task)]}, bad])
        with patch('researchhelper.model.urllib.request.urlopen', side_effect=model.respond):
            failed = self.fixture.agent.start(task['id'])
        self.assertEqual(failed['status'], '失败')
        self.assertNotIn('input_digest', failed.get('reasoning_state', {}).get('analysis', {}))
        self.assertNotIn('DO_NOT_CACHE_CROSS_NODE', str(failed))
        self.assertFalse(failed['ledger']['dispatches'][-1]['output_contract']['execution_binding']['accepted'])
        used = copy.deepcopy(failed['ledger']['used'])
        retry = fixtures.FabricatedModel([self.fixture.answer()])
        with patch('researchhelper.model.urllib.request.urlopen', side_effect=retry.respond):
            result = self.fixture.agent.start(task['id'])
        self.assertEqual(len(retry.requests), 1)
        self.assertIsNotNone(result['delivery'])
        self.assertEqual(result['ledger']['used']['model_calls'], used['model_calls'] + 1)
        self.assertEqual(result['ledger']['used']['data_requests'], used['data_requests'])

    def test_topology_diagnostic_does_not_retain_model_text_or_fabricate_spending(self):
        task = copy.deepcopy(self.task)
        task['ledger']['dispatches'].append({'kind': 'model_calls', 'status': '成功', 'output_contract': {}})
        before = len(task['ledger']['dispatches']) - 1
        result = {'answers': {}, 'theses': [{'question_ids': ['q1', 'q2'], 'title': 'SECRET_REJECTED_TITLE'}]}
        with self.assertRaises(ContractError):
            reasoning.validate_analysis_execution(None, task, result, before)
        self.assertNotIn('SECRET_REJECTED_TITLE', str(task))
        self.assertEqual(len(task['ledger']['dispatches']), before + 1)
        self.assertEqual(task['ledger']['dispatches'][-1]['status'], '成功')
