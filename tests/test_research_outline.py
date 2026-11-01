"""Shared research guidance must not become an approval or another model run."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError
from researchhelper.engine import ResearchAgent
from researchhelper.planning import input_fingerprint, research_outline_contract
from researchhelper.reasoning import analysis_contract, model_object, PHASE_FIELDS
from researchhelper import argument_support

ROOT = Path(__file__).resolve().parents[1]


class ResearchOutlineTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='research-outline-')
        self.addCleanup(folder.cleanup)
        self.agent = ResearchAgent(ResearchArchive(folder.name), ROOT, offline=True, require_argument_support=True)
        self.task = self.agent.create('研究600519.SH的经营情况与投资逻辑')

    def test_new_task_and_analysis_share_the_same_detached_guide(self):
        before = copy.deepcopy(self.task)
        guide = research_outline_contract(self.task)
        self.assertEqual(guide['main_question_ids'], [self.task['questions'][0]['id']])
        self.assertFalse(guide['automatic_approval'])
        self.assertFalse(guide['requires_extra_model_call'])
        self.assertEqual(analysis_contract(self.task)['client_writing']['research_outline'], guide)
        guide['main_question_ids'].append('foreign')
        self.assertEqual(self.task, before)
        self.assertNotIn('foreign', research_outline_contract(self.task)['main_question_ids'])

    def test_legacy_hash_and_unknown_policy_are_not_silently_upgraded(self):
        self.task.pop('research_outline_version')
        old_hash = input_fingerprint(self.task)
        self.assertIsNone(research_outline_contract(self.task))
        self.task['research_outline_version'] = 1
        self.assertNotEqual(input_fingerprint(self.task), old_hash)
        for value in (True, 2, 1.0, '1'):
            self.task['research_outline_version'] = value
            with self.assertRaises(ContractError): research_outline_contract(self.task)

    def test_document_content_scope_never_requires_strategy(self):
        from researchhelper.contracts import DOCUMENT_CONTENT_INTENT
        self.task['intent'] = DOCUMENT_CONTENT_INTENT
        self.assertIsNone(research_outline_contract(self.task))

    def test_forged_planning_guide_is_rejected_before_transport(self):
        class NoCall:
            def request(self, *args, **kwargs):
                raise AssertionError('No model call is permitted')
        before = copy.deepcopy(self.task['ledger'])
        with self.assertRaisesRegex(ContractError, '研究论证结构'):
            model_object(NoCall(), self.task, 'plan', {'research_outline': {'version': 999}}, PHASE_FIELDS['plan'])
        self.assertEqual(self.task['ledger'], before)

    def test_same_policy_is_frozen_with_completed_synthetic_content(self):
        task = self.agent.create_case('etf')
        task['research_outline_version'] = 1
        task['input_hash'] = input_fingerprint(task)
        self.agent.archive.save(task)
        done = self.agent.start(task['id'])
        self.assertEqual(done['status'], '已完成')
        self.assertEqual(self.agent.export(done['id'])['content']['research_outline_version'], 1)
        self.assertEqual(done['ledger']['used']['model_calls'], 0)

    def test_review_binds_guidance_without_granting_financial_support(self):
        source = ROOT/'result/acceptance/one-sentence-research-sn729z8h/task.json'
        raw = source.read_bytes()
        task = json.loads(raw)
        before = argument_support.review_request(task, task['host_result'])
        task['research_outline_version'] = 1
        task['input_hash'] = input_fingerprint(task)
        after = argument_support.review_request(task, task['host_result'])
        self.assertNotEqual(before['review_hash'], after['review_hash'])
        self.assertEqual(after['scope']['research_outline'], research_outline_contract(task))
        self.assertFalse(after['scope']['research_outline']['automatic_approval'])
        for key in ('targets', 'evidence', 'calculations', 'proposal'):
            self.assertEqual(before[key], after[key])
        self.assertEqual(source.read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
