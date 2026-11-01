"""Task-state evidence never becomes proof that a document omits information."""
import copy
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper import argument_support as review
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import canonical
from researchhelper.engine import ResearchAgent
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import analysis_contract
from researchhelper.reports import compose

POLICY = 'negative_claim_scope_version'
RETAINED = ROOT / 'result/acceptance/one-sentence-research-f0zv6cul/task.json'


class NegativeClaimReviewScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_hash = hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        cls.old = json.loads(RETAINED.read_text())
        cls.task = copy.deepcopy(cls.old)
        cls.task[POLICY] = 1
        cls.task['input_hash'] = input_fingerprint(cls.task)
        cls.proposal = cls.task['host_result']
        with patch('socket.socket.connect', side_effect=AssertionError('Offline negative-claim review')):
            cls.original = review.review_request(cls.old, cls.old['host_result'])
            cls.request = review.review_request(cls.task, cls.proposal)

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(RETAINED.read_bytes()).hexdigest() == cls.original_hash

    def test_scope_separates_task_processing_state_from_document_omission(self):
        rule = self.request['scope']['absence_claim_scope']
        self.assertEqual(rule['version'], 1)
        self.assertFalse(rule['automatic_approval'])
        self.assertEqual(rule['coverage_field'], 'reading_coverage')
        self.assertIn('没有提供', rule['task_scope_does_not_prove'])
        self.assertTrue(any(row['omitted_characters'] > 0 for row in self.request['reading_coverage']))
        self.assertEqual(self.request['reading_coverage'], self.original['reading_coverage'])

    def test_every_actual_field_and_source_remains_verbatim_in_the_review(self):
        for key in ('targets','evidence','calculations','knowledge','proposal'):
            self.assertEqual(self.request[key], self.original[key], key)
        text = ''.join(target['text'] for target in self.request['targets'])
        self.assertIn('未提供将财务公司存款业务与酒类销售收现拆分核算的调节', text)
        self.assertEqual(self.task['questions'], self.old['questions'])
        self.assertEqual(self.task['ledger'], self.old['ledger'])

    def test_both_review_transports_receive_the_bound_rule_without_rewriting_copy(self):
        for short in (False, True):
            messages = review.review_messages(self.request, use_reference_units=short)
            system = messages[0]['content']
            shown = json.loads(messages[1]['content'])
            self.assertIn('即使该句在limitation中也不能放行', system)
            self.assertIn('否则标insufficient', system)
            self.assertEqual(shown['scope']['absence_claim_scope'], self.request['scope']['absence_claim_scope'])
            self.assertEqual(shown['reading_coverage'], self.request['reading_coverage'])
            for original, target in zip(self.request['targets'], shown['targets']):
                if short:
                    self.assertEqual(''.join(unit['text'] for unit in target['units']), original['text'])
                else:
                    self.assertEqual(target['text'], original['text'])

    def test_an_old_review_binding_does_not_authorize_the_new_rule(self):
        self.assertNotEqual(self.original['review_hash'], self.request['review_hash'])
        self.assertNotEqual(review.reference_review_schema(self.original), review.reference_review_schema(self.request))
        self.assertNotIn('absence_claim_scope', self.original['scope'])
        for value in (None, True, False, 0, 2, 1.0):
            task = copy.deepcopy(self.old)
            if value is not None: task[POLICY] = value
            task['input_hash'] = input_fingerprint(task)
            request = review.review_request(task, task['host_result'])
            self.assertNotIn('absence_claim_scope', request['scope'])
            self.assertNotIn('即使该句在limitation中也不能放行', review.review_messages(request)[0]['content'])

    def test_authoring_contract_distinguishes_unverified_from_not_disclosed(self):
        # A new policy must not relabel the retained evidence approvals. Use a
        # fresh empty task for authoring; the actual old proposal above remains
        # read-only and only supplies the independent review-input fixture.
        with tempfile.TemporaryDirectory(prefix='negative-claim-authoring-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('核对本次研究的资料边界')
                before = canonical(task)
                rule = analysis_contract(task)['client_writing']['absence_review_boundary']
                self.assertFalse(rule['automatic_approval'])
                self.assertIn('不等于公司未提供或报告未披露', rule['rule'])
                self.assertEqual(canonical(task), before)
            finally: agent.close_runtime()
        self.assertNotIn('absence_review_boundary', analysis_contract(self.old)['client_writing'])

    def test_formal_policy_is_input_bound_and_frozen_without_model_calls(self):
        with tempfile.TemporaryDirectory(prefix='negative-claim-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('解释ETF折溢价', entry='ETF', questions=[dict(id='q1',kind='fact',
                    question='解释ETF折溢价',answer_scope='general_definition',knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task[POLICY], 1)
                old = copy.deepcopy(task); old.pop(POLICY)
                self.assertNotEqual(input_fingerprint(old), task['input_hash'])
                completed = agent.start(task['id'])
                self.assertEqual(compose(completed)[POLICY], 1)
                self.assertEqual(completed['ledger']['used']['model_calls'], 0)
                self.assertNotIn(POLICY, agent.create_case('mechanism'))
            finally: agent.close_runtime()
