"""A case-sharded source receipt cannot hide omitted or duplicate tests."""
import unittest

from tests.recheck_argument_skill_runtime import validate_source_cases


class SkillRecheckCaseCoverageTests(unittest.TestCase):
    def test_whole_module_and_case_shards_prove_the_same_coverage(self):
        module = 'test_schema_cardinality'
        suite = unittest.defaultTestLoader.loadTestsFromName('tests.'+module)
        names = [case.id() for nested in suite for case in nested]
        shards = [{'modules':names[:4]}, {'modules':names[4:]}]
        self.assertEqual(validate_source_cases(shards, [module]), len(names))
        self.assertEqual(validate_source_cases([{'modules':['tests.'+module]}], [module]), len(names))

    def test_omitted_case_is_rejected(self):
        module = 'test_schema_cardinality'
        suite = unittest.defaultTestLoader.loadTestsFromName('tests.'+module)
        names = [case.id() for nested in suite for case in nested]
        with self.assertRaises(AssertionError):
            validate_source_cases([{'modules':names[:-1]}], [module])

    def test_duplicate_case_is_rejected(self):
        module = 'test_schema_cardinality'
        suite = unittest.defaultTestLoader.loadTestsFromName('tests.'+module)
        names = [case.id() for nested in suite for case in nested]
        with self.assertRaises(AssertionError):
            validate_source_cases([{'modules':names+[names[0]]}], [module])


if __name__=='__main__':
    unittest.main()
