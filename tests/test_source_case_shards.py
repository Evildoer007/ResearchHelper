"""Source runs use the same bounded actual-case expansion as packaged runs."""
import unittest
from unittest.mock import patch
from tests.accept_initial_acquisition_runtime import case_shard_groups


class SourceCaseShardTests(unittest.TestCase):
    def test_selected_modules_expand_every_actual_case_once(self):
        modules=['test_review_target_order','test_registered_ratio_reference']
        groups=case_shard_groups([[module] for module in modules],modules)
        count=unittest.defaultTestLoader.loadTestsFromNames(['tests.'+module for module in modules]).countTestCases()
        actual=[name for group in groups for name in group]
        self.assertTrue(all(1<=len(group)<=4 for group in groups))
        self.assertEqual(len(actual),count)
        self.assertEqual(len(set(actual)),count)
        self.assertEqual(unittest.defaultTestLoader.loadTestsFromNames(['tests.'+name for name in actual]).countTestCases(),count)

    def test_module_groups_not_selected_for_case_mode_stay_unchanged(self):
        groups=[['test_review_target_order','test_schema_cardinality']]
        self.assertEqual(case_shard_groups(groups,()),groups)

    def test_missing_module_is_not_counted_as_a_real_test(self):
        with self.assertRaises(AssertionError):
            case_shard_groups([['test_module_that_does_not_exist']],['test_module_that_does_not_exist'])

    def test_duplicate_actual_identities_are_rejected(self):
        class Duplicate(unittest.TestCase):
            def runTest(self):pass
            def id(self):return 'tests.fixture.Case.test_duplicate'
        suite=unittest.TestSuite([Duplicate(),Duplicate()])
        with patch.object(unittest.defaultTestLoader,'loadTestsFromName',return_value=suite),self.assertRaises(AssertionError):
            case_shard_groups([['fixture']],['fixture'])
