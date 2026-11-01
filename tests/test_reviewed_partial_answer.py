"""Partial, independently reviewed findings survive an unresolved data need.

Synthetic local documents and explicit host reviews test contracts only. No
model, vendor request or semantic-quality acceptance is claimed.
"""
import copy
import tempfile
import unittest
from unittest.mock import patch

from core.session import Session
from researchhelper import argument_support as review
from researchhelper.contracts import ContractError
from researchhelper.reports import compose


class ReviewedPartialAnswerTests(unittest.TestCase):
    def setUp(self):
        guard = patch('socket.socket.connect', side_effect=AssertionError('Offline only'))
        guard.start()
        self.addCleanup(guard.stop)
        directory = tempfile.TemporaryDirectory(prefix='rh-partial-answer-')
        self.addCleanup(directory.cleanup)
        self.session = Session(directory.name)
        self.addCleanup(self.session.agent.close_runtime)
        task = self.session.create('核对政策生效日期及适用主体', objects=['示例政策'],
            cutoff='2026-07-20T18:00:00+08:00', questions=[dict(id='q1', kind='fact',
                question='核对政策生效日期及适用主体', evidence_needs=['生效日期', '适用主体'])])
        source = self.session.material(task['id'], 'policy.txt',
            '示例政策\n\n发布日期：2026年7月19日。\n\n政策自2026年7月21日起生效。'.encode())
        evidence = dict(id='effective_date', artifact_id=source['id'], object='示例政策',
            record_locator=dict(part='段落3'), text='政策自2026年7月21日起生效。', period='政策条款',
            nature='合同政策条款', unit='不适用', question_ids=['q1'], published_at='2026-07-19',
            publication=dict(at='2026-07-19', precision='日', timezone='Asia/Shanghai', certainty='已确认',
                basis='原件公布日', basis_ref=dict(kind='material', artifact_id=source['id'],
                    snapshot_hash=source['snapshot_hash'], part='段落2', quote='发布日期：2026年7月19日。')),
            review=dict(question_ids=['q1'], allowed_natures=['合同政策条款'], conditions=['材料条款核对'],
                reason='机械夹具：本段仅核对日期，不确认适用主体', evidence_need_texts=dict(q1=['生效日期'])))
        self.task = self.session.run(task['id'], evidence_specs=[evidence],
            answers=dict(q1=dict(answer='政策自2026年7月21日起生效。', refs=['effective_date'])),
            defer_delivery=True)
        self.proposal = copy.deepcopy(self.task['host_result'])

    def reviewed(self, supported=True):
        request = review.review_request(self.task, self.proposal)
        wire = dict(version=review.VERSION, review_hash=request['review_hash'],
            questions=[dict(question_id='q1', status='requires_evidence', missing_evidence=['适用主体'],
                reason='机械夹具：仅日期有依据，主体未核实，不能完成整道问题')],
            reviews=[dict(target_id=target['id'], segments=[dict(text=target['text'],
                verdict='supported' if supported else 'insufficient',
                basis_refs=['evidence:effective_date'], explanation='机械协议测试，不是实际研究批准')])
                for target in request['targets']])
        self.session.review_arguments(self.task['id'], wire)
        return self.session.deliver(self.task['id'])

    def test_reviewed_part_survives_without_claiming_full_completion(self):
        result = self.reviewed()
        question = result['questions'][0]
        self.assertEqual(result['status'], '部分完成')
        self.assertEqual(question['status'], '未回答')
        self.assertEqual(question.get('answer'), self.proposal['answers']['q1']['answer'])
        self.assertEqual(question['refs'], ['effective_date'])
        self.assertEqual(question['research_coverage']['status'], 'requires_evidence')
        self.assertTrue(any('适用主体' in gap for gap in result['gaps']))
        self.assertEqual(result['ledger']['used']['model_calls'], 0)
        report = self.session.archive.report(result['id'], result['version'])
        self.assertEqual(report['content']['questions'][0]['answer'], question['answer'])
        self.assertIn(question['answer'], report['html'])
        self.assertEqual(review.thesis_question_contexts(result, ['q1']), {})

    def test_no_independent_review_cannot_preserve_the_proposed_prose(self):
        result = self.session.deliver(self.task['id'])
        self.assertNotIn('answer', result['questions'][0])
        self.assertEqual(len(result['evidence']), 1)

    def test_unsupported_answer_does_not_become_a_partial_finding(self):
        result = self.reviewed(supported=False)
        self.assertNotIn('answer', result['questions'][0])
        self.assertEqual(result['status'], '部分完成')

    def test_changing_preserved_text_is_rejected_at_report_freeze(self):
        result = self.reviewed()
        self.assertIn('answer', result['questions'][0])
        result['questions'][0]['answer'] += '该政策适用全部公司。'
        with self.assertRaises(ContractError):
            compose(result)


if __name__ == '__main__':
    unittest.main()
