"""A source audit cannot waive new errors or authorize installation."""
import unittest
from tests.review_harness_source_candidate import classify, DELIVERY_CHECKS


class HarnessReleaseClassificationTests(unittest.TestCase):
    def test_historical_failure_requires_executed_passing_current_checks(self):
        record = {'id': 'old.method (case=1)', 'kind': 'error'}
        pairs = {'old.method': ['current.positive', 'current.negative']}
        for executions, failures in (({'current.positive'}, set()),
                                     ({'current.positive', 'current.negative'}, {'current.negative'})):
            self.assertEqual(classify([record], executions, failures, pairs)[2], [record])
        resolved, deferred, unexpected = classify([record], {'current.positive', 'current.negative'}, set(), pairs)
        self.assertEqual(len(resolved), 1)
        self.assertEqual((deferred, unexpected), ([], []))

    def test_delivery_deferral_accepts_only_exact_old_snapshot_assertions(self):
        old = next(iter(DELIVERY_CHECKS))
        assertion = {'id': old, 'kind': 'failure'}
        error = {'id': old, 'kind': 'error'}
        unrelated = {'id': old+'.new_bug', 'kind': 'failure'}
        _, deferred, unexpected = classify([assertion, error, unrelated], set(), set(), {})
        self.assertEqual(len(deferred), 1)
        self.assertEqual(unexpected, [error, unrelated])
