"""Replay the real manual API proposals through ordinary binding and arithmetic.

Only detached tasks are projected into the new policy. No original study is
resumed or rewritten, and neither external data nor model calls are allowed.
"""
import copy
import importlib
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

_prefix = __package__+'.' if __package__ else ''
retained_task = importlib.import_module(_prefix+'test_fixed_financial_context_inheritance').retained_task
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import candidate_evidence
from researchhelper.knowledge import ResearchKnowledge
from researchhelper.planning import input_fingerprint


class FinancialContextExecutionTests(unittest.TestCase):
    def test_real_api_proposals_execute_two_planned_methods_without_model(self):
        task = copy.deepcopy(retained_task())
        task['fixed_financial_context_version'] = 1
        record = next(row for row in task['source_records'] if row.get('operation') == 'basic_data_service')
        task['evidence'] = []
        task['calculation_specs'] = []
        task['calculations'] = []
        task['reasoning_state'] = {}
        task['input_hash'] = input_fingerprint(task)
        for spec in task['host_result']['evidence_specs']:
            if spec.get('artifact_id') != record['id']:
                continue
            item = candidate_evidence(task, record, spec)
            ResearchAnalyzer().assess(task, item, spec['review'], task['source_records'])
            task['evidence'].append(item)
        agent = ResearchAgent.__new__(ResearchAgent)
        agent.knowledge = ResearchKnowledge()
        agent.model = SimpleNamespace()
        agent._save_model_checkpoint = Mock()
        agent._reasoning_available = Mock(return_value=False)
        ledger = copy.deepcopy(task['ledger'])
        with patch('socket.socket.connect', side_effect=AssertionError('Offline replay only')):
            agent._bind_initial_calculations(task)
            self.assertEqual(task['reasoning_state']['initial_calculation_binding']['status'], '已登记')
            self.assertEqual(len(task['calculation_specs']), 2)
            for spec in task['calculation_specs']:
                agent._calculate_spec(task, spec)
        self.assertEqual(task['ledger'], ledger)
        self.assertEqual(len(task['calculations']), 2)
        amounts = {e['calculation_context']['metric']: Decimal(str(e['value'])) for e in task['evidence']}
        expected = {'net_profit_margin_v1': amounts['net_profit']/amounts['revenue']*100,
                    'operating_cash_profit_ratio_v1': amounts['operating_cash_flow']/amounts['net_profit']*100}
        for calculation in task['calculations']:
            difference = abs(Decimal(str(calculation['output']['value']))-expected[calculation['method']])
            self.assertLessEqual(difference, Decimal(str(calculation['output']['tolerance'])))
        self.assertFalse(task['reasoning_state']['initial_calculation_binding']['receipt']['model_dispatched'])

    def test_new_policy_is_bound_to_input_identity(self):
        task = copy.deepcopy(retained_task())
        original = input_fingerprint(task)
        task['fixed_financial_context_version'] = 1
        self.assertNotEqual(input_fingerprint(task), original)
        task.pop('fixed_financial_context_version')
        self.assertEqual(input_fingerprint(task), original)
