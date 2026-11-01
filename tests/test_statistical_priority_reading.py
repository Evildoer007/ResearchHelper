"""Replay real omitted PMI paragraphs with bounded literal priority locators."""
import copy
import hashlib
import json
import socket
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import ContractError, canonical
from researchhelper.source_reader import (read, binding, validate_reading, reader_version_for,
    statistical_reader_terms, CONTEXT_LIMIT, MAX_QUERY_CANDIDATES)

RETAINED = ROOT / 'result/acceptance/one-sentence-research-v4zeo1mq/task.json'


class StatisticalPriorityReadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protected = hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        cls.old_task = json.loads(RETAINED.read_text())
        cls.old = cls.old_task['reasoning_state']['reading']['bundle']
        cls.task = copy.deepcopy(cls.old_task)
        cls.task['statistical_reading_version'] = 1
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline replay')):
            cls.new = read(cls.task, cls.old['selection'], binding(cls.task), cls.old['artifact_ids'])

    @classmethod
    def tearDownClass(cls):
        if hashlib.sha256(RETAINED.read_bytes()).hexdigest() != cls.protected:
            raise AssertionError('Closed real research changed')

    def test_explicit_production_and_orders_paragraphs_are_actually_provided(self):
        source = self.old['views'][0]['id']
        before = next(view for view in self.old['views'] if view['id'] == source)
        after = next(view for view in self.new['views'] if view['id'] == source)
        # The official HTML repeats the same two paragraphs in its layouts.
        # Either exact original occurrence is valid, never an invented excerpt.
        for locators, expected in [(('段落26', '段落66'), '生产指数为51.7%'),
                                   (('段落27', '段落67'), '新订单指数为50.5%')]:
            self.assertFalse(any(part['locator'] in locators for part in before['parts']))
            self.assertTrue(any(part['locator'] in locators and expected in part['text'] for part in after['parts']))

    def test_selection_originals_and_budget_unchanged_no_financial_approval(self):
        self.assertEqual(self.new['selection'], self.old['selection'])
        self.assertEqual(self.task['source_records'], self.old_task['source_records'])
        self.assertEqual(self.task['ledger'], self.old_task['ledger'])
        self.assertEqual(self.new['reader_version'], 5)
        self.assertLessEqual(len(canonical(self.new['views'])), CONTEXT_LIMIT)
        for entry in self.new['coverage']:
            self.assertLessEqual(entry['candidate_counts']['query'], MAX_QUERY_CANDIDATES)
            trace = entry['required_indicator_queries']
            self.assertFalse(trace['financial_approval'])
            self.assertEqual([term['term'] for term in trace['terms']], ['生产指数', '新订单指数'])
            self.assertGreater(entry['candidate_counts']['exception'], 0)

    def test_trace_is_content_bound_and_omissions_remain_explicit(self):
        validate_reading(self.task, self.new)
        for entry in self.new['coverage']:
            self.assertEqual(entry['total_characters'], entry['read_characters'] + entry['omitted_characters'])
        forged = copy.deepcopy(self.new)
        forged['coverage'][0]['required_indicator_queries']['financial_approval'] = True
        with self.assertRaises(ContractError):
            validate_reading(self.task, forged)

    def test_legacy_bundle_replays_exactly_without_automatic_upgrade(self):
        replay = read(self.old_task, self.old['selection'], self.old['binding'], self.old['artifact_ids'],
                      reader_version=self.old['reader_version'])
        self.assertEqual(replay, self.old)

    def test_unrelated_or_legacy_tasks_cannot_request_new_policy(self):
        self.assertEqual(statistical_reader_terms(self.old_task), [])
        for question in ('比较基金生产经营风险', '分析非制造业PMI新订单', '分析制造业PMI总体变化'):
            task = dict(self.task, question=question)
            self.assertEqual(statistical_reader_terms(task), [])
            self.assertNotEqual(reader_version_for(task), 5)
        with self.assertRaises(ContractError):
            read(self.old_task, self.old['selection'], binding(self.old_task), self.old['artifact_ids'], reader_version=5)

    def test_only_user_requested_terms_are_required_not_model_added_risks(self):
        self.assertEqual(statistical_reader_terms(self.task), ['生产指数', '新订单指数'])
        task = dict(self.task, question='分析制造业PMI的生产表现')
        self.assertEqual(statistical_reader_terms(task), ['生产指数'])
        self.assertNotIn('新出口订单指数', statistical_reader_terms(self.task))
