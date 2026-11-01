"""Partial numeric proposals must not skip a source-bound missing counterpart.

All originals and decoding come from retained fixtures. Model calls are mocked;
these tests neither contact a supplier nor approve financial evidence.
"""
import copy
import tempfile
import unittest
from unittest.mock import patch

from tests import test_initial_fee_input_completion as fixtures
from researchhelper.contracts import fingerprint
from researchhelper.planning import input_fingerprint
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.reports import compose
from tests import ROOT

POLICY = 'partial_fee_input_completion_version'


class PartialFeeInputCompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.InitialFeeInputCompletionTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.InitialFeeInputCompletionTests.tearDownClass()

    def setUp(self):
        self.fixture = fixtures.InitialFeeInputCompletionTests()
        self.fixture.setUp()
        self.task = self.fixture.task
        self.task[POLICY] = 1
        self.task['input_hash'] = input_fingerprint(self.task)
        self.numeric = [self.fixture.base.decode(self.fixture.base.wire(index)) for index in (0, 1)]

    def tearDown(self):
        self.fixture.tearDown()

    def select(self, numeric):
        specs = copy.deepcopy(self.fixture.initial + numeric)
        self.task['host_result']['evidence_specs'] = specs
        self.task['reasoning_state']['extraction']['evidence_specs_hash'] = fingerprint(specs)
        return specs

    def test_unpaired_numeric_input_is_eligible_without_discarding_text(self):
        self.select(self.numeric[:1])
        self.assertTrue(self.fixture.agent._initial_fee_completion_available(self.task, self.task['host_result']))

    def test_complete_pair_does_not_get_another_call(self):
        self.select(self.numeric)
        self.assertFalse(self.fixture.agent._initial_fee_completion_available(self.task, self.task['host_result']))

    def test_complete_custody_pair_cannot_hide_unpaired_management_fee(self):
        proposals = copy.deepcopy(self.numeric)
        for proposal in proposals:
            proposal['calculation_context']['metric'] = 'custody_fee'
        management = copy.deepcopy(self.numeric[0])
        management['id'] = 'unpaired_management'
        proposals.append(management)
        self.select(proposals)
        groups = self.fixture.agent._unpaired_fee_input_groups(proposals)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['context']['metric'], 'management_fee')
        self.assertEqual(groups[0]['observed_input_ids'], ['unpaired_management'])
        self.assertTrue(self.fixture.agent._initial_fee_completion_available(self.task, self.task['host_result']))

    def test_legacy_or_invalid_policy_cannot_change_partial_extraction(self):
        self.select(self.numeric[:1])
        for value in (None, True, False, 0, 2, 1.0):
            self.task[POLICY] = value
            with self.subTest(value=value):
                self.assertFalse(self.fixture.agent._initial_fee_completion_available(self.task, self.task['host_result']))

    def test_only_one_new_pass_and_existing_numbers_remain_immutable(self):
        initial = self.select(self.numeric[:1])
        with patch('researchhelper.engine.model_object', side_effect=self.fixture.submit) as model, \
             patch('researchhelper.engine.record_extraction_validation'):
            result = self.fixture.complete()
            self.fixture.complete()
        self.assertEqual(model.call_count, 1)
        self.assertEqual(result['evidence_specs'][:len(initial)], initial)
        self.assertEqual(self.task['ledger']['used']['model_calls'], 5)

    def test_noncomparable_groups_are_not_mistaken_for_a_complete_pair(self):
        changed = copy.deepcopy(self.numeric)
        changed[1]['calculation_context']['period'] = '2025-01-01/2025-06-30'
        self.select(changed)
        self.assertTrue(self.fixture.agent._initial_fee_completion_available(self.task, self.task['host_result']))

    def test_partial_policy_is_an_explicit_input_dependency(self):
        current = input_fingerprint(self.task)
        self.task.pop(POLICY)
        self.assertNotEqual(current, input_fingerprint(self.task))

    def test_new_formal_tasks_freeze_the_explicit_policy_without_external_calls(self):
        with tempfile.TemporaryDirectory(prefix='partial-fee-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('解释ETF折溢价', entry='ETF', questions=[dict(id='q1', kind='fact',
                    question='解释ETF折溢价', answer_scope='general_definition', knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task[POLICY], 1)
                complete = agent.start(task['id'])
                self.assertEqual(complete['status'], '已完成')
                self.assertEqual(compose(complete)[POLICY], 1)
                self.assertEqual(complete['ledger']['used']['model_calls'], 0)
                self.assertEqual(complete['ledger']['used']['data_requests'], 0)
            finally:
                agent.close_runtime()
