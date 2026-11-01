"""Existing run deadline and cancellation also bound local page-context scans."""
import copy
import unittest
from unittest.mock import Mock, patch

from tests import ROOT
from researchhelper.contracts import ContractError
from researchhelper.financial_sources import passage_contexts
from researchhelper.material_parser import begin_verification, end_verification
from tests import test_linear_passage_context as fixture


class PassageRunDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.source = fixture.LinearPassageContextTests().source()
        self.task = dict(id='private_deadline', version=1, batch=1)

    def test_expired_run_does_not_begin_source_indexing(self):
        token = begin_verification(self.task, Mock(), 0)
        try:
            with patch('researchhelper.financial_sources.fingerprint',
                       side_effect=AssertionError('Expired run must stop first')):
                with self.assertRaisesRegex(ContractError, '时限'):
                    passage_contexts(self.source, ['第5页'], version=2)
        finally:
            end_verification(token)

    def test_local_scan_checks_deadline_between_pages_without_resetting_it(self):
        clock = [0.0]
        calls = []
        def active(task):
            self.assertIs(task, self.task)
            calls.append(task['id'])
            clock[0] += 1
        before = copy.deepcopy(self.source)
        with patch('researchhelper.material_parser.time.monotonic', side_effect=lambda: clock[0]):
            token = begin_verification(self.task, active, 3)
            try:
                with self.assertRaisesRegex(ContractError, '时限'):
                    passage_contexts(self.source, ['第5页'], version=2)
            finally:
                end_verification(token)
        self.assertLessEqual(len(calls), 3)
        self.assertEqual(self.source, before)
        # A closed context cannot deny a different ordinary reader.
        self.assertEqual(passage_contexts(self.source, ['第5页'], version=2)[0]['part'], '第5页')

    def test_cancelled_batch_does_not_supply_context_or_cache_approval(self):
        active = Mock(side_effect=ContractError('任务已停止'))
        token = begin_verification(self.task, active, 60)
        try:
            with self.assertRaisesRegex(ContractError, '任务已停止'):
                passage_contexts(self.source, ['第2页'], version=2)
        finally:
            end_verification(token)
        self.assertFalse(passage_contexts(self.source, ['第2页'], version=2)[0]['financial_approval'])


if __name__ == '__main__':
    unittest.main()
