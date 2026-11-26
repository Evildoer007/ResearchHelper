"""Actual acquisition payload retains candidates after executable-tool filtering."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests import test_object_discovery_contract as fixture
from researchhelper.acquisition import complete_initial_acquisition, navigation_question_projection
from researchhelper.evidence import data_request_contract
from researchhelper.planning import input_fingerprint
from researchhelper.financial_sources import STRUCTURED_SOURCE_POLICY


class DiscoveryNavigationContextTests(unittest.TestCase):
    def setUp(self):
        self.base = fixture.ObjectDiscoveryTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.task = self.base.task
        self.task['run_mode'] = 'model'
        self.task['questions'][0].update(evidence_needs=['本期营业收入与报表净利润'],
                                        method_needs=['net_profit_margin_v1'])
        self.task['input_hash'] = input_fingerprint(self.task)
        # Retain the synthetic response only after the case has chosen its input policy.

    def navigation_payload(self, *, legacy=False):
        if legacy:
            self.task.pop(STRUCTURED_SOURCE_POLICY)
            self.task['input_hash']=input_fingerprint(self.task)
        self.source=self.base.retained()
        captured = []
        engine = SimpleNamespace(offline=False, model=object(), source_credentials={'ifind': {'refresh_token': 'SYNTHETIC_NOT_SENT'}},
            _active_supplement=Mock(return_value=None), _reasoning_available=Mock(return_value=True),
            _save_model_checkpoint=Mock(), _gap=Mock())

        def choose(model, task, phase, payload, fields):
            captured.append(copy.deepcopy(payload))
            return dict(data_requests=[], stop_reason='离线载荷检查到此结束，不派发网络'), dict()

        with patch('researchhelper.acquisition.extractable_source_views', return_value=[self.source]), \
                patch('researchhelper.acquisition.model_object', side_effect=choose):
            complete_initial_acquisition(engine, self.task)
        self.assertEqual(len(captured), 1)
        contract = captured[0]['data_request_contract']
        self.assertIn('get_thscode', [tool['operation'] for tool in contract['tools']])
        self.assertEqual(contract['object_discovery']['results'][0]['candidates'][0]['name'], '测试样本甲')
        self.assertEqual(contract['callable_codes'], ['000001.SZ', '600001.SH'])
        return contract

    def test_actual_navigation_payload_keeps_candidate_choice_when_discovery_tool_removed(self):
        contract=self.navigation_payload(legacy=True)
        self.assertNotIn('smart_stock_picking',[tool['operation'] for tool in contract['tools']])
        self.assertNotIn(STRUCTURED_SOURCE_POLICY,self.task)

    def test_current_api_priority_keeps_numeric_navigation_and_retained_candidates(self):
        contract=self.navigation_payload()
        self.assertIn('smart_stock_picking',[tool['operation'] for tool in contract['tools']])
        self.assertEqual(contract['source_preference']['primary_provider'],'ifind')
        self.assertFalse(contract['source_preference']['financial_approval'])

    def test_projection_preserves_choices_without_inventing_scope_or_codes(self):
        self.source=self.base.retained()
        contract = data_request_contract(self.task)
        expected = copy.deepcopy(contract['object_discovery'])
        contract['tools'] = [tool for tool in contract['tools'] if tool['operation'] == 'get_thscode']
        projected, questions = navigation_question_projection(self.task, contract, dict(
            pending_original_question_ids=[], pending_method_question_ids=[], pending_discovery_question_ids=['q1']))
        self.assertEqual(projected['object_discovery'], expected)
        self.assertEqual(projected['callable_codes'], ['000001.SZ', '600001.SH'])
        self.assertEqual([row['id'] for row in questions], ['q1'])
