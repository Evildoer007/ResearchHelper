"""Actual duplicate-dispatch fingerprints differed only by answer order."""
import copy
import json
from pathlib import Path
import unittest

from researchhelper import argument_support as review

ROOT=Path(__file__).resolve().parents[1]
RETAINED=ROOT/'result/acceptance/one-sentence-research-s89qizr5/task.json'


class ReviewTargetOrderTests(unittest.TestCase):
    def setUp(self):
        self.original=RETAINED.read_bytes();self.task=json.loads(self.original)
        self.proposal=copy.deepcopy(self.task['host_result'])

    def tearDown(self):self.assertEqual(RETAINED.read_bytes(),self.original)

    def reordered(self):
        proposal=copy.deepcopy(self.proposal)
        proposal['answers']={ident:proposal['answers'][ident] for ident in ('q2','q3','q4','q1')}
        return proposal

    def test_same_answers_have_identical_binding_units_and_schema_after_archive_sorting(self):
        first=review.review_request(self.task,self.reordered())
        second=review.review_request(self.task,self.proposal)
        self.assertEqual(first,second)
        self.assertEqual(review.reference_units(first),review.reference_units(second))
        self.assertEqual(review.reference_review_schema(first),review.reference_review_schema(second))

    def test_original_two_dispatch_keys_are_reproduced_by_only_target_order(self):
        request=review.review_request(self.task,self.proposal)
        answers={item['id']:item for item in request['targets'] if item['id'].startswith('answer:')}
        others=[item for item in request['targets'] if not item['id'].startswith('answer:')]
        before=dict(request,targets=[answers['answer:'+ident+'.answer'] for ident in ('q2','q3','q4','q1')]+others)
        reproduced={review.review_key(before),review.review_key(request)}
        self.assertEqual(reproduced,set(self.task['reasoning_state']['argument_support_attempts']))

    def test_completed_mock_review_is_reusable_after_json_round_trip(self):
        request=review.review_request(self.task,self.reordered())
        related={ident for target in request['targets'] for ident in target['question_ids']}
        wire=dict(version=review.VERSION,review_hash=request['review_hash'],questions=[dict(
            question_id=q['id'],status='answered',reason='仅检查归档身份，不是金融质量批准。',missing_evidence=[])
            for q in request['scope']['questions'] if q['id'] in related],reviews=[dict(
                target_id=target['id'],segments=[dict(text=target['text'],verdict='supported',
                    basis_refs=['scope:task'],explanation='模拟标签，只验证顺序与缓存身份。')]) for target in request['targets']])
        result=review.decode_review(wire,review.review_schema(request),request)
        record=review.make_record(self.task,self.reordered(),result,actor='host')
        self.task['argument_support_reviews']=[record]
        restored=json.loads(json.dumps(self.task,ensure_ascii=False,sort_keys=True))
        self.assertEqual(review.find_record(restored,restored['host_result'])['result'],result)
        self.assertFalse(result['source_approval'])

    def test_real_text_change_still_invalidates_the_input(self):
        before=review.review_request(self.task,self.proposal)
        proposal=self.reordered()
        proposal['answers']['q1']['numeric_segments']['answer'].append(dict(text='新增陈述。'))
        self.assertNotEqual(before['review_hash'],review.review_request(self.task,proposal)['review_hash'])


if __name__=='__main__':unittest.main()
