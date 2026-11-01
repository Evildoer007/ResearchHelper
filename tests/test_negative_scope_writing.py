"""Writing guidance cannot turn selective reading into proof of absence."""
import json
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import canonical
from researchhelper.reasoning import analysis_contract


class NegativeScopeWritingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path=ROOT/'result/acceptance/one-sentence-research-na5hnmjr/task.json'
        cls.raw=cls.path.read_bytes();cls.task=json.loads(cls.raw)

    @classmethod
    def tearDownClass(cls):assert cls.path.read_bytes()==cls.raw

    def test_guidance_keeps_question_and_financial_obligations_unchanged(self):
        before=canonical(self.task)
        with patch('socket.socket.connect',side_effect=AssertionError('Offline contract audit')):
            contract=analysis_contract(self.task)
        guard=contract['client_writing']['absence_scope']
        self.assertEqual(guard['version'],1)
        self.assertTrue(guard['negative_claim_requires_coverage'])
        self.assertFalse(guard['automatic_approval'])
        self.assertEqual(set(contract['by_question']),{q['id'] for q in self.task['questions']})
        self.assertEqual(canonical(self.task),before)

    def test_old_answerable_question_still_has_unsupported_negative_sentence(self):
        review=self.task['argument_support_reviews'][-1]['result']
        self.assertEqual(review['question_assessments'][0]['status'],'answered')
        self.assertEqual(review['question_assessments'][0]['missing_evidence'],[])
        unsupported=[segment for item in review['assessments'] for segment in item['segments']
                     if segment['verdict']!='supported']
        self.assertEqual(len(unsupported),1)
        self.assertIn('报告未明确',unsupported[0]['text'])
        self.assertFalse(review['supported']);self.assertEqual(self.task['status'],'部分完成')

    def test_omitted_material_is_not_erased_to_support_a_negative_claim(self):
        coverage=self.task['reasoning_state']['reading']['bundle']['coverage']
        self.assertTrue(any(row.get('omitted_characters',0)>0 for row in coverage))
        before=canonical(coverage)
        analysis_contract(self.task)
        self.assertEqual(canonical(coverage),before)
