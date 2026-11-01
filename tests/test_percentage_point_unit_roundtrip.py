"""Literal percentage-point aliases must survive the existing number scanner.

No conversion to percentages/basis points, no changed evidence, no approval of
retained prose or automatic correction of a downward change's positive token.
"""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch
from tests import ROOT
from researchhelper.numeric_integrity import _same_unit,_quantities,bind_numeric_statement
from researchhelper.contracts import fingerprint

STUDY=ROOT/'result/acceptance/one-sentence-research-8x1tzgha/task.json'


class PercentagePointRoundtripTests(unittest.TestCase):
    def test_both_literal_spellings_match_without_changing_scale(self):
        for source in ('百分点','个百分点'):
            for text in ('1.3个百分点','1.3百分点'):
                quantity=_quantities(text)[0]
                self.assertTrue(_same_unit(source,quantity['unit']))
        for source,display in [('%','百分点'),('bp','百分点'),('基点','百分点'),('点','百分点'),('百分点','%')]:
            with self.subTest(source=source,display=display):self.assertFalse(_same_unit(source,display))

    def test_exact_retained_proposals_bind_without_rewriting_any_segment_or_source(self):
        raw=STUDY.read_bytes();before=hashlib.sha256(raw).hexdigest()
        task=json.loads(raw);original=fingerprint(task)
        proposals=[entry['proposal'] for entry in task['reasoning_state']['rejected_analysis_submissions']]
        proposals.append(task['host_result'])
        with patch('socket.socket.connect',side_effect=AssertionError('Offline replay only')):
            for proposal in proposals:
                answer=proposal['answers']['q1']
                bound=bind_numeric_statement(task,dict(copy.deepcopy(answer),question_ids=['q1']),
                    ['answer'],answer['refs'],answer.get('calculation_refs',[]))
                self.assertTrue(bound['fields']['answer'])
                self.assertIn('个百分点',bound['fields']['answer'])
                self.assertTrue(bound['record'])
        self.assertEqual(fingerprint(task),original)
        self.assertEqual(hashlib.sha256(STUDY.read_bytes()).hexdigest(),before)


if __name__=='__main__':unittest.main()
