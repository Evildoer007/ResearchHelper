"""Author/reviewer scope alignment does not approve facts or loosen adoption."""
import copy
import hashlib
import json
import tempfile
import unittest

from tests import ROOT
from researchhelper import argument_support
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import canonical
from researchhelper.engine import ResearchAgent
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import analysis_contract
from researchhelper.reports import compose

POLICY = 'research_synthesis_version'
RETAINED = ROOT / 'result/acceptance/one-sentence-research-9bcz84uu/task.json'


class ResearchSynthesisScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_hash = hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        cls.old = json.loads(RETAINED.read_text())
        cls.task = copy.deepcopy(cls.old)
        cls.task[POLICY] = 1
        cls.task['input_hash'] = input_fingerprint(cls.task)
        cls.original = argument_support.review_request(cls.old, cls.old['host_result'])
        cls.request = argument_support.review_request(cls.task, cls.task['host_result'])

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(RETAINED.read_bytes()).hexdigest() == cls.original_hash

    def test_author_and_reviewer_share_one_non_approving_scope(self):
        rule = self.request['scope']['research_synthesis']
        self.assertEqual(rule, argument_support.synthesis_scope(self.task))
        self.assertFalse(rule['automatic_approval'])
        self.assertFalse(rule['scope_expansion'])
        with tempfile.TemporaryDirectory(prefix='research-synthesis-author-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('研究盈利质量')
                before = canonical(task)
                self.assertEqual(analysis_contract(task)['client_writing']['research_synthesis'], rule)
                self.assertEqual(canonical(task), before)
            finally:
                agent.close_runtime()

    def test_actual_positive_and_negative_materials_stay_in_review_unchanged(self):
        for key in ('targets', 'evidence', 'calculations', 'knowledge', 'proposal', 'reading_coverage'):
            self.assertEqual(self.original[key], self.request[key], key)
        self.assertEqual(self.old['questions'], self.task['questions'])
        self.assertEqual(self.old['ledger'], self.task['ledger'])

    def test_old_completed_review_cannot_approve_new_input_binding(self):
        self.assertNotEqual(self.original['review_hash'], self.request['review_hash'])
        self.assertIsNone(argument_support.find_record(self.task, self.task['host_result']))
        self.assertNotIn('research_synthesis', self.original['scope'])

    def test_policy_is_exact_and_legacy_tasks_are_not_upgraded(self):
        for value in (None, True, False, 0, 2, 1.0, '1'):
            task = dict(self.old)
            if value is not None:
                task[POLICY] = value
            self.assertIsNone(argument_support.synthesis_scope(task))
        self.assertNotIn(POLICY, self.old)

    def test_both_review_transports_preserve_all_submitted_text(self):
        for short in (False, True):
            messages = argument_support.review_messages(self.request, use_reference_units=short)
            shown = json.loads(messages[1]['content'])
            self.assertEqual(shown['scope']['research_synthesis'], self.request['scope']['research_synthesis'])
            for original, target in zip(self.request['targets'], shown['targets']):
                rendered = ''.join(unit['text'] for unit in target['units']) if short else target['text']
                self.assertEqual(original['text'], rendered)

    def test_shared_rule_does_not_relabel_missing_analysis_as_completion(self):
        rule = argument_support.synthesis_scope(self.task)
        self.assertIn('前两者仍未完成', rule['completeness'])
        self.assertIn('真正需要新资料', rule['review'])
        self.assertIn('不凭原文中的数字另做未登记算术', rule['comparison'])
        self.assertEqual(self.task['status'], '部分完成')
        self.assertEqual(self.task['questions'][0]['research_coverage']['status'], 'requires_evidence')

    def test_policy_is_default_input_bound_and_frozen_in_report(self):
        with tempfile.TemporaryDirectory(prefix='research-synthesis-freeze-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('解释ETF折溢价', entry='ETF', questions=[dict(id='q1', kind='fact',
                    question='解释ETF折溢价', answer_scope='general_definition',
                    knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task[POLICY], 1)
                old = copy.deepcopy(task)
                old.pop(POLICY)
                self.assertNotEqual(input_fingerprint(old), task['input_hash'])
                completed = agent.start(task['id'])
                self.assertEqual(compose(completed)[POLICY], 1)
                self.assertEqual(completed['ledger']['used']['model_calls'], 0)
            finally:
                agent.close_runtime()

    def test_synthetic_cases_and_original_limits_are_unchanged(self):
        with tempfile.TemporaryDirectory(prefix='research-synthesis-synthetic-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                self.assertNotIn(POLICY, agent.create_case('mechanism'))
                task = agent.create('研究宏观事件')
                self.assertEqual(task['ledger']['limits'], dict(data_requests=100, model_calls=8, seconds=600))
                self.assertEqual(task['ledger']['reserve'], dict(model_calls=1, seconds=60.0))
            finally:
                agent.close_runtime()
