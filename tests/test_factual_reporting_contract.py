"""Offline contract regression; synthetic verdicts are not semantic approval."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from researchhelper import argument_support as review
from researchhelper.reasoning import factual_reporting_contract
from researchhelper.planning import input_fingerprint

ROOT = Path(__file__).resolve().parents[1]


class FactualReportingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / 'result/acceptance/one-sentence-research-sn729z8h/task.json'
        cls.raw = cls.path.read_bytes()
        cls.old = json.loads(cls.raw)

    @classmethod
    def tearDownClass(cls):
        assert cls.path.read_bytes() == cls.raw

    def setUp(self):
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline only'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_policy_is_new_task_only_and_does_not_accept_boolean_versions(self):
        for version in (None, False, True, 0, 2, 1.0):
            self.assertIsNone(factual_reporting_contract({'planning_scope_version': version}))
        contract = factual_reporting_contract({'planning_scope_version': 1})
        self.assertFalse(contract['automatic_approval'])
        self.assertIn('数学描述', contract['mathematical_conclusion'])
        self.assertIn('实际任务记录', contract['task_limitation'])
        self.assertIn('直接适用依据', contract['causal_claim'])
        self.assertIn('不等于已定位报表正文', contract['source_location'])

    def test_review_binds_new_guidance_without_changing_inputs_or_verdicts(self):
        task = copy.deepcopy(self.old)
        proposal = task['host_result']
        original = review.review_request(task, proposal)
        task['planning_scope_version'] = 1
        task['input_hash'] = input_fingerprint(task)
        before = copy.deepcopy(task)
        request = review.review_request(task, proposal)
        self.assertEqual(task, before)
        self.assertNotEqual(request['review_hash'], original['review_hash'])
        self.assertNotIn('factual_reporting', original['scope'])
        self.assertEqual(request['scope']['factual_reporting'], factual_reporting_contract(task))
        for key in ('targets', 'evidence', 'calculations', 'knowledge', 'proposal'):
            self.assertEqual(request[key], original[key])
        for compact in (False, True):
            messages = review.review_messages(request, use_reference_units=compact)
            self.assertIn('无原件定位的该类语句标insufficient', messages[0]['content'])
            self.assertIn('不因partial或限制段自动批准', messages[0]['content'])
        self.assertEqual(task['status'], '部分完成')

    def test_retained_failure_and_success_are_distinct_from_final_completion(self):
        records = self.old['argument_support_reviews']
        rejected = records[0]['result']
        unsupported = [s['text'] for a in rejected['assessments']
                       for s in a['segments'] if s['verdict'] != 'supported']
        self.assertFalse(rejected['supported'])
        self.assertTrue(any('来源位置为合并利润表' in text for text in unsupported))
        self.assertTrue(records[-1]['result']['supported'])
        self.assertEqual(self.old['status'], '部分完成')

