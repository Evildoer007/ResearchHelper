"""A supported answer is retained even if internal need coverage is incomplete.

Synthetic local fixture only. A review never fills a missing obligation,
completes the question or makes it a complete thesis prerequisite.
"""
import unittest

try:
    import test_reviewed_partial_answer as fixtures
except ModuleNotFoundError:
    from tests import test_reviewed_partial_answer as fixtures
from researchhelper import argument_support as review


class SupportedAnswerWithPendingNeedsTests(unittest.TestCase):
    setUp = fixtures.ReviewedPartialAnswerTests.setUp

    def deliver_supported(self, status):
        request = review.review_request(self.task, self.proposal)
        wire = dict(version=review.VERSION, review_hash=request['review_hash'],
            questions=[dict(question_id='q1', status=status, missing_evidence=[],
                reason='机械夹具：提交的日期句有依据，不表示缺少的主体已经查明')],
            reviews=[dict(target_id=target['id'], segments=[dict(text=target['text'], verdict='supported',
                basis_refs=['evidence:effective_date'], explanation='仅核对已提交的日期句')])
                for target in request['targets']])
        self.session.review_arguments(self.task['id'], wire)
        return self.session.deliver(self.task['id'])

    def test_supported_finding_survives_pending_coverage_without_fake_completion(self):
        result = self.deliver_supported('answered')
        question = result['questions'][0]
        self.assertEqual(result['status'], '部分完成')
        self.assertEqual(question['status'], '未回答')
        self.assertEqual(question.get('answer'), self.proposal['answers']['q1']['answer'])
        self.assertTrue(any('适用主体' in gap for gap in result['gaps']))
        self.assertEqual(review.thesis_question_contexts(result, ['q1']), {})
        report = self.session.archive.report(result['id'], result['version'])
        self.assertIn(question['answer'], report['html'])
        self.assertEqual(result['ledger']['used']['model_calls'], 0)

    def test_unidentifiable_review_does_not_erase_supported_text_or_fill_missing_facts(self):
        result = self.deliver_supported('unidentifiable')
        self.assertEqual(result['status'], '部分完成')
        self.assertEqual(result['questions'][0]['status'], '未回答')
        self.assertEqual(result['questions'][0].get('answer'), self.proposal['answers']['q1']['answer'])
        self.assertEqual(len(result['evidence']), 1)
        self.assertTrue(any('适用主体' in gap for gap in result['gaps']))


def accept_packaged():
    """Run the same six cases against the selected installed Skill only."""
    import hashlib
    import json
    import os
    from pathlib import Path
    import sys
    import tempfile
    from tests import ROOT
    from tests.accept_delivered_ifind_no_data import run_packaged_tests
    sys.path.insert(0, str(ROOT / 'packaging'))
    from build_current import verify_current_build
    from skill.install_project_skill import verify_installation
    selected = Path(os.environ['RESEARCHHELPER_CURRENT_RECEIPT']).resolve(strict=True)
    verify_current_build(selected, ROOT)
    installation = ROOT / '.skills/researchhelper'
    before = verify_installation(installation)
    assert before == hashlib.sha256((selected.parent / 'skill-manifest.json').read_bytes()).hexdigest()
    output = Path(tempfile.mkdtemp(prefix='current-supported-answer-', dir=ROOT / 'result/acceptance'))
    record = dict(status='failed', candidate=str(selected), model_calls=0, network_calls=0,
                  production_store_touched=False, native_window_opened=False)
    try:
        checked = run_packaged_tests('skill', installation, ROOT, output,
            ['test_reviewed_partial_answer', 'test_supported_answer_with_pending_needs'], 6)
        assert verify_installation(installation) == before
        record.update(status='scoped_passed', checks=checked['tests_run'],
                      module_paths=checked['module_paths'], installation_unchanged=True)
    except Exception as error:
        record['error'] = str(error)
    target = output / 'receipt.json'
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(dict(status=record['status'], receipt=str(target), checks=record.get('checks'))))
    if record['status'] != 'scoped_passed':
        raise SystemExit(1)
    return target


if __name__ == '__main__':
    unittest.main()
