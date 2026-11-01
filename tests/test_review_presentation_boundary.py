"""Actual retained review inputs, never a replayed or approved live study."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from researchhelper import argument_support as support
from researchhelper.contracts import ContractError

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / 'result/acceptance/one-sentence-research-tqtuu8a1/task.json'


class ReviewPresentationBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_hash = hashlib.sha256(STUDY.read_bytes()).hexdigest()
        cls.saved = json.loads(STUDY.read_text())
        cls.proposal = cls.saved['argument_support_reviews'][0]['proposal']

    @classmethod
    def tearDownClass(cls):
        if hashlib.sha256(STUDY.read_bytes()).hexdigest() != cls.original_hash:
            raise AssertionError('Retained failed task changed')

    def setUp(self):
        self.task = dict(self.saved, argument_context_version=2)
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external calls'))
        guard.start()
        self.addCleanup(guard.stop)

    def request(self):
        return support.review_request(self.task, self.proposal)

    def test_existing_quoted_accounting_basis_is_visible_under_its_own_evidence(self):
        request = self.request()
        evidence = {item['id']: item for item in request['evidence']}
        source = next(item for item in self.task['evidence'] if item.get('calculation_context', {}).get('metric') == 'revenue')
        fact = evidence[source['id']]
        self.assertEqual(fact['calculation_context_basis'], source['calculation_context_basis'])
        self.assertIn('企业会计准则', fact['calculation_context_basis']['accounting_standard']['quote'])
        self.assertEqual(fact['calculation_context_basis']['accounting_standard']['artifact_id'], source['artifact_id'])
        self.assertEqual(request['binding_version'], 5)

    def test_presentation_and_research_have_separate_obligations(self):
        policy = self.request()['scope']['review_responsibilities']
        self.assertEqual(policy['current_stage'], 'research_content_review')
        self.assertIn('基础数据', policy['reviewer'])
        self.assertIn('另行验收', policy['downstream'])
        self.assertIn('不豁免必要分析', policy['completion'])
        self.assertIn('尚未渲染的图表', policy['excluded_as_current_missing_evidence'])

    def test_actual_reviewer_prompt_states_limits_without_preapproving_outputs(self):
        messages = support.review_messages(self.request(), use_reference_units=True)
        self.assertIn('不能因为本阶段尚无已渲染图表', messages[0]['content'])
        self.assertIn('基础数据、口径或必要分析若确实缺少', messages[0]['content'])
        self.assertIn('不强迫给出正负投资方向', messages[0]['content'])
        self.assertIn('未被正文引用的证据', messages[0]['content'])
        presented = json.loads(messages[1]['content'])
        self.assertEqual(presented['scope']['review_responsibilities']['version'], 2)

    def test_legacy_request_and_record_are_unchanged(self):
        request = support.review_request(self.saved, self.proposal)
        self.assertEqual(request['binding_version'], 4)
        self.assertNotIn('review_responsibilities', request['scope'])
        self.assertTrue(all('calculation_context_basis' not in item for item in request['evidence']))
        support.validate_record(self.saved, self.saved['argument_support_reviews'][0])

    def test_new_context_cannot_reuse_the_old_semantic_review(self):
        with self.assertRaises(ContractError):
            support.validate_record(self.task, self.saved['argument_support_reviews'][0])

    def test_context_does_not_add_citations_change_proposal_or_complete_failed_task(self):
        before = copy.deepcopy(self.proposal)
        request = self.request()
        self.assertEqual(request['proposal'], before)
        legacy_targets = {item['id']: item for item in support.review_request(self.saved, self.proposal)['targets']}
        for target in request['targets']:
            self.assertEqual(target, legacy_targets[target['id']])
        self.assertEqual(self.task['status'], '部分完成')
        self.assertNotIn('supported', request)

    def test_forged_accounting_quote_is_not_exposed_as_approved_basis(self):
        self.task['evidence'] = list(self.saved['evidence'])
        index = next(index for index, item in enumerate(self.task['evidence']) if 'calculation_context_basis' in item)
        item = copy.copy(self.task['evidence'][index])
        item['calculation_context_basis'] = copy.deepcopy(item['calculation_context_basis'])
        item['calculation_context_basis']['accounting_standard']['quote'] = '原件不存在的会计准则说明'
        self.task['evidence'][index] = item
        with self.assertRaises(ContractError):
            self.request()

    def test_missing_basis_is_not_filled_from_another_evidence(self):
        self.task['evidence'] = list(self.saved['evidence'])
        index = next(index for index, item in enumerate(self.task['evidence']) if 'calculation_context_basis' in item)
        item = copy.copy(self.task['evidence'][index])
        item.pop('calculation_context_basis')
        self.task['evidence'][index] = item
        with self.assertRaises(ContractError):
            self.request()
