"""Partial passing shards do not become complete without every missing case."""
import unittest

from tests.recover_skill_loopback_shards import missing_cases, case_ids


class SkillIncrementalCoverageTests(unittest.TestCase):
    def test_remaining_cases_preserve_expected_order(self):
        self.assertEqual(missing_cases(['a', 'b', 'c'], ['b']), ['a', 'c'])

    def test_duplicate_or_foreign_passes_do_not_count(self):
        for expected, passed in ((['a', 'a'], ['a']), (['a', 'b'], ['a', 'a']), (['a'], ['foreign'])):
            with self.assertRaises(RuntimeError): missing_cases(expected, passed)

    def test_unknown_test_cannot_be_counted_as_one_covered_case(self):
        with self.assertRaises(RuntimeError): case_ids(['missing_module_for_coverage'])
