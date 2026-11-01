"""Registered navigation saves a call, not source or financial verification."""
import copy
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests import ROOT
from researchhelper.contracts import ContractError, canonical, charge
from researchhelper.engine import ResearchAgent
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import extractable_source_views
from researchhelper.source_reader import (CONTEXT_LIMIT, MAX_REQUESTS, binding, context_sources,
    read, registered_reading_requests, validate_reading)

POLICY = 'registered_source_reading_version'
RETAINED = ROOT / 'result/acceptance/one-sentence-research-9bcz84uu/task.json'


class RegisteredSourceReadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_hash = hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        cls.old = json.loads(RETAINED.read_text())
        cls.task = copy.deepcopy(cls.old)
        cls.task[POLICY] = 1
        cls.task['input_hash'] = input_fingerprint(cls.task)
        cls.identifiers = [v['id'] for v in extractable_source_views(cls.task)]
        cls.context = context_sources(cls.task, cls.identifiers)
        assert cls.context['mode'] == 'selected'
        cls.questions = [q['id'] for q in cls.task['questions']]
        cls.requests = registered_reading_requests(cls.task, cls.context['catalog'], cls.questions)
        cls.bundle = read(cls.task, cls.requests, binding(cls.task), cls.identifiers)

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(RETAINED.read_bytes()).hexdigest() == cls.original_hash

    def test_registered_requests_keep_actual_source_hashes_and_question_scope(self):
        items = {i['id']: i for i in self.context['catalog']['items']}
        self.assertTrue(self.requests)
        for request in self.requests:
            self.assertEqual({k: request[k] for k in ('artifact_id', 'snapshot_hash')},
                items[request['artifact_id']]['request_binding'])
            self.assertEqual(request['question_ids'], self.questions)
            self.assertLessEqual(len(request['queries']), 8)
            self.assertEqual(request['parts'], [])

    def test_original_reading_is_bounded_exact_and_does_not_approve_finance(self):
        self.assertLessEqual(len(canonical(self.bundle['views'])), CONTEXT_LIMIT)
        validate_reading(self.task, self.bundle)
        traces = [row['registered_input_reading'] for row in self.bundle['coverage']
            if row.get('registered_input_reading')]
        self.assertTrue(traces)
        self.assertTrue(all(not row['financial_approval'] for row in traces))
        self.assertTrue(any('net_profit' in g['metrics'] and g['read_in_full'] for row in traces for g in row['groups']))

    def test_missing_or_invalid_policy_does_not_upgrade_old_reading(self):
        for value in (None, True, False, 0, 2, 1.0):
            task = dict(self.old)
            if value is not None:
                task[POLICY] = value
            self.assertIsNone(registered_reading_requests(task, self.context['catalog'], self.questions))

    def test_unregistered_financial_inputs_keep_model_selected_reading(self):
        task = dict(self.task, questions=[], calculation_specs=[])
        self.assertIsNone(registered_reading_requests(task, self.context['catalog'], self.questions))

    def test_partial_catalogue_or_too_many_documents_are_not_silently_selected(self):
        catalogue = copy.deepcopy(self.context['catalog'])
        catalogue['next_offset'] = 24
        self.assertIsNone(registered_reading_requests(self.task, catalogue, self.questions))
        catalogue = copy.deepcopy(self.context['catalog'])
        catalogue['items'] *= MAX_REQUESTS + 1
        catalogue['total_artifacts'] = len(catalogue['items'])
        self.assertIsNone(registered_reading_requests(self.task, catalogue, self.questions))

    def test_unparsed_originals_are_not_relabelled_readable(self):
        catalogue = copy.deepcopy(self.context['catalog'])
        next(i for i in catalogue['items'] if i['kind'] in ('material', 'filing'))['status'] = '未解析'
        self.assertIsNone(registered_reading_requests(self.task, catalogue, self.questions))

    def test_stale_binding_and_incomplete_source_binding_are_rejected(self):
        catalogue = copy.deepcopy(self.context['catalog'])
        catalogue['binding']['input_hash'] = 'foreign'
        with self.assertRaises(ContractError):
            registered_reading_requests(self.task, catalogue, self.questions)
        catalogue = copy.deepcopy(self.context['catalog'])
        next(i for i in catalogue['items'] if i['kind'] in ('material', 'filing')).pop('request_binding')
        with self.assertRaises(ContractError):
            registered_reading_requests(self.task, catalogue, self.questions)

    def test_unknown_or_empty_question_scope_cannot_enter_navigation(self):
        for questions in ([], ['foreign'], self.questions * 2):
            with self.assertRaises(ContractError):
                registered_reading_requests(self.task, self.context['catalog'], questions)

    def test_engine_uses_one_extraction_call_and_keeps_the_delivery_reserve(self):
        task = copy.deepcopy(self.task)
        task.update(status='运行中', run_mode='model', reasoning_state={})
        task['ledger']['used']['model_calls'] = 6
        task['ledger']['dispatches'] = []
        protected = copy.deepcopy((task['evidence'], task['questions'], task['source_records'], task['calculations']))
        agent = ResearchAgent.__new__(ResearchAgent)
        agent.model = SimpleNamespace()
        agent._active_supplement = Mock(return_value=None)
        agent._active = Mock()
        agent._reasoning_available = Mock(return_value=True)
        agent._save_model_checkpoint = Mock()
        agent._select_model_reading = Mock(side_effect=AssertionError('Unnecessary reading-model call'))
        agent._complete_initial_fee_inputs = Mock(return_value={'evidence_specs': []})
        def extract(model, actual, phase, payload, expected):
            self.assertEqual(phase, 'extract')
            self.assertIsNotNone(payload['source_reading'])
            charge(actual['ledger'], 'model_calls')
            return {'evidence_specs': []}, {}
        with patch('researchhelper.engine.model_object', side_effect=extract) as model:
            agent._extract_model_sources(task)
            self.assertEqual(model.call_count, 1)
        self.assertEqual(task['ledger']['used']['model_calls'], 7)
        self.assertEqual(task['ledger']['reserve']['model_calls'], 1)
        self.assertEqual(task['reasoning_state']['reading']['receipt']['model_calls'], 0)
        self.assertEqual((task['evidence'], task['questions'], task['source_records'], task['calculations']), protected)
        agent._select_model_reading.assert_not_called()
