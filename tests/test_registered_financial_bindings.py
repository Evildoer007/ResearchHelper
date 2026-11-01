"""Unique verified contexts reduce orchestration, never financial gates."""
import copy
import json
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from researchhelper.argument_support import VERSION
from researchhelper.contracts import ContractError, registered_financial_bindings
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.planning import input_fingerprint

ROOT = Path(__file__).resolve().parents[1]
RETAINED = ROOT/'result/acceptance/one-sentence-research-220kvryl/task.json'


class RegisteredFinancialBindingTests(unittest.TestCase):
    def setUp(self):
        self.original = RETAINED.read_bytes()
        self.task = json.loads(self.original)
        self.expected = copy.deepcopy(self.task['calculations'])
        self.task['calculation_specs'] = []
        self.task['calculations'] = []
        self.task['host_result']['calculations'] = []
        self.task['reasoning_state'] = {}
        self.task['input_hash'] = input_fingerprint(self.task)
        self.ids = [q['id'] for q in self.task['questions']]
        self.agent = ResearchAgent.__new__(ResearchAgent)
        self.agent.knowledge = ResearchKnowledge()
        self.agent._save_model_checkpoint = Mock()
        self.agent.model = SimpleNamespace()
        self.agent._reasoning_available = Mock(return_value=False)
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No network'))
        guard.start(); self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertEqual(RETAINED.read_bytes(), self.original)

    def bind(self):
        return registered_financial_bindings(self.task, self.ids)

    def amount(self, metric='net_profit'):
        return next(e for e in self.task['evidence'] if
                    (e.get('calculation_context') or {}).get('metric') == metric)

    def test_unique_metrics_preserve_registered_method_inputs_and_context(self):
        proposal = self.bind()
        self.assertEqual(len(proposal['calculations']), 2)
        for spec in proposal['calculations']:
            expected = next(c for c in self.expected if c['method']==spec['method'])
            self.assertEqual(spec['inputs'], expected['input_bindings'])
            self.assertEqual(spec['parameters'], expected['parameters'])
            self.assertEqual(spec['question_ids'], expected['question_ids'])

    def test_projection_does_not_mutate_evidence_questions_ledger_or_knowledge(self):
        before = copy.deepcopy(self.task)
        self.bind()
        self.assertEqual(self.task, before)

    def test_ambiguous_same_metric_is_not_ranked_or_deduplicated(self):
        duplicate = copy.deepcopy(self.amount()); duplicate['id'] += '_other'
        self.task['evidence'].append(duplicate)
        self.assertIsNone(self.bind())

    def test_another_period_is_not_chosen_from_question_prose(self):
        duplicate = copy.deepcopy(self.amount()); duplicate['id'] += '_prior'
        duplicate['period'] = '2025年半年度'
        duplicate['calculation_context']['period'] = duplicate['period']
        self.task['evidence'].append(duplicate)
        self.assertIsNone(self.bind())

    def test_missing_metric_does_not_choose_nearby_report_quantity(self):
        self.amount()['calculation_context']['metric'] = 'attributable_net_profit'
        self.assertIsNone(self.bind())

    def test_different_units_are_not_silently_converted(self):
        self.amount()['unit'] = '百万元'
        self.assertIsNone(self.bind())

    def test_mismatched_verified_context_never_gets_a_proposal(self):
        for field, value in (('currency','USD'), ('entity_id','other'),
                             ('accounting_scope','parent'), ('accounting_standard','IFRS'),
                             ('period','2025年半年度')):
            with self.subTest(field=field):
                item = self.amount(); saved = copy.deepcopy(item)
                item['calculation_context'][field] = value
                self.assertIsNone(self.bind())
                item.clear(); item.update(saved)

    def test_unapproved_cross_task_and_cross_version_are_not_eligible(self):
        for field, value in (('source_check','待核验'), ('applicability_check','待核验'),
                             ('task_id','foreign'), ('research_version',99),
                             ('usage_scope','document_content'), ('nature','预测')):
            with self.subTest(field=field):
                item = self.amount(); saved = copy.deepcopy(item)
                item[field] = value
                self.assertIsNone(self.bind())
                item.clear(); item.update(saved)

    def test_unplanned_method_is_never_added(self):
        for q in self.task['questions']: q['method_needs'] = []
        self.assertIsNone(self.bind())

    def test_other_pending_method_keeps_full_analyst_binding_path(self):
        self.task['questions'][0]['method_needs'].append('observed_price_return_v1')
        self.assertIsNone(self.bind())

    def test_existing_spec_is_not_replaced(self):
        self.task['calculation_specs'] = self.bind()['calculations']
        before = copy.deepcopy(self.task['calculation_specs'])
        self.assertIsNone(self.bind())
        self.assertEqual(self.task['calculation_specs'], before)

    def test_scope_limits_binding_to_requested_question(self):
        ident = self.task['questions'][0]['id']
        result = registered_financial_bindings(self.task, [ident])
        self.assertEqual(len(result['calculations']), 1)
        self.assertEqual(result['calculations'][0]['question_ids'], [ident])
        with self.assertRaises(ContractError): registered_financial_bindings(self.task, ['foreign'])

    def test_engine_registers_without_model_budget_or_new_data(self):
        self.task['argument_support_contract'] = VERSION
        ledger = copy.deepcopy(self.task['ledger'])
        self.agent._bind_initial_calculations(self.task)
        binding = self.task['reasoning_state']['initial_calculation_binding']
        self.assertEqual(binding['status'], '已登记')
        self.assertEqual(binding['selection_policy'], 'registered_unique_financial_v1')
        self.assertFalse(binding['receipt']['model_dispatched'])
        self.assertFalse(binding['receipt']['source_approved'])
        self.assertEqual(self.task['ledger'], ledger)
        self.assertEqual(len(self.task['calculation_specs']), 2)

    def test_registered_specs_still_execute_through_ordinary_gates(self):
        self.task['argument_support_contract'] = VERSION
        self.agent._bind_initial_calculations(self.task)
        for spec in self.task['calculation_specs']: self.agent._calculate_spec(self.task, spec)
        self.assertEqual(len(self.task['calculations']), 2)
        for result in self.task['calculations']:
            expected = next(c for c in self.expected if c['method']==result['method'])
            self.assertEqual(result['output'], expected['output'])
        self.assertTrue(all(q['status']=='未回答' for q in self.task['questions']))
        self.assertFalse(self.task.get('argument_support_reviews'))

    def test_resume_does_not_duplicate_registered_calculations(self):
        self.task['argument_support_contract'] = VERSION
        self.agent._bind_initial_calculations(self.task)
        before = copy.deepcopy(self.task['calculation_specs'])
        self.task['batch'] += 1
        self.agent._bind_initial_calculations(self.task)
        self.assertEqual(self.task['calculation_specs'], before)

    def test_legacy_tasks_are_not_silently_migrated_by_binding(self):
        self.task.pop('argument_support_contract', None)
        self.agent._bind_initial_calculations(self.task)
        self.assertEqual(self.task['calculation_specs'], [])


if __name__=='__main__': unittest.main()
