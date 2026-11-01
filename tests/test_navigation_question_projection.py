"""Navigation offers unresolved source questions, not the entire research graph."""
import copy
import json
import socket
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.acquisition import (initial_acquisition_coverage, navigation_question_projection,
                                       validate_navigation_question_scope)
from researchhelper.contracts import ContractError
from researchhelper.evidence import data_request_contract
from researchhelper.planning import question_specs

RETAINED = ROOT / 'result/acceptance/one-sentence-research-k36b86du/task.json'


class NavigationQuestionProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = RETAINED.read_bytes()
        cls.saved = json.loads(cls.original)

    @classmethod
    def tearDownClass(cls):
        if RETAINED.read_bytes() != cls.original:
            raise AssertionError('Closed research changed')

    def setUp(self):
        self.task = dict(copy.deepcopy(self.saved), navigation_question_scope_version=1)
        self.coverage = initial_acquisition_coverage(self.task)
        self.contract = data_request_contract(self.task)
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline scope test'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_real_failed_navigation_offers_only_its_pending_questions(self):
        before = copy.deepcopy((self.task, self.contract))
        contract, questions = navigation_question_projection(self.task, self.contract, self.coverage)
        expected = self.coverage['pending_original_question_ids']
        self.assertEqual(contract['question_ids'], expected)
        self.assertEqual([q['id'] for q in questions], expected)
        self.assertLess(len(questions), len(self.task['questions']))
        self.assertFalse(contract['navigation_question_scope']['automatic_assignment'])
        self.assertFalse(contract['navigation_question_scope']['financial_approval'])
        self.assertEqual((self.task, self.contract), before)

    def test_stale_example_ids_are_omitted_not_automatically_replaced(self):
        self.contract['tools'].append(dict(source='official', operation='release_original',
            example=dict(question_ids=['q5'], parameters=dict(selection_ref='private_fixture'))))
        contract, _ = navigation_question_projection(self.task, self.contract, self.coverage)
        example = contract['tools'][-1]['example']
        self.assertNotIn('question_ids', example)
        self.assertEqual(example['parameters'], {'selection_ref': 'private_fixture'})
        self.assertEqual(self.contract['tools'][-1]['example']['question_ids'], ['q5'])

    def test_mixed_price_and_original_pending_branches_are_both_routable(self):
        coverage = dict(self.coverage, pending_original_question_ids=['q1'], pending_method_question_ids=['q2'])
        contract, _ = navigation_question_projection(self.task, self.contract, coverage)
        for ident in ('q1', 'q2'):
            validate_navigation_question_scope(self.task, [dict(question_ids=[ident])], coverage, contract['question_ids'])
        with self.assertRaises(ContractError):
            validate_navigation_question_scope(self.task, [dict(question_ids=['q3'])], coverage, contract['question_ids'])

    def test_unknown_ready_empty_and_malformed_scopes_are_not_dispatched(self):
        contract, _ = navigation_question_projection(self.task, self.contract, self.coverage)
        for refs in ([], ['q5'], ['unknown'], 'q1', [dict(id='q1')], [1]):
            with self.subTest(refs=refs), self.assertRaises(ContractError):
                validate_navigation_question_scope(self.task, [dict(question_ids=refs)], self.coverage, contract['question_ids'])

    def test_initial_unresolved_lookup_without_declared_pending_keeps_required_scope(self):
        coverage = dict(self.coverage, pending_original_question_ids=[], pending_method_question_ids=[])
        self.task['questions'][0]['status'] = '已回答'
        self.task['questions'][-1]['answer_scope'] = 'general_definition'
        contract, questions = navigation_question_projection(self.task, self.contract, coverage)
        expected = [q['id'] for q in self.task['questions'] if q.get('required', True)
                    and q.get('status') != '已回答' and q.get('answer_scope') != 'general_definition']
        self.assertEqual(contract['question_ids'], expected)
        self.assertEqual([q['id'] for q in questions], expected)

    def test_legacy_scope_and_examples_keep_their_original_meaning(self):
        for version in (None, True, 0, 2):
            task = dict(self.task)
            task.pop('navigation_question_scope_version')
            if version is not None:
                task['navigation_question_scope_version'] = version
            contract, questions = navigation_question_projection(task, self.contract, self.coverage)
            self.assertEqual(contract, self.contract)
            self.assertEqual(questions, question_specs(task))
            with self.assertRaises(ContractError):
                validate_navigation_question_scope(task, [dict(question_ids=['q3'])], self.coverage, contract['question_ids'])

    def test_tools_parameters_and_original_selections_are_not_rewritten(self):
        contract, _ = navigation_question_projection(self.task, self.contract, self.coverage)
        for before, after in zip(self.contract['tools'], contract['tools']):
            for key, value in before.items():
                if key not in ('example', 'examples'):
                    self.assertEqual(after[key], value)
