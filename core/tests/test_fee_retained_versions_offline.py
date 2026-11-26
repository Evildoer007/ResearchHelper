"""Versioned fee knowledge remains immutable under current extraction rules."""
import copy
import unittest

from researchhelper.contracts import METHOD_REGISTRY, fingerprint
from researchhelper.knowledge import RETAINED_VERSIONS, ResearchKnowledge
from researchhelper.reasoning import PHASE_RULES
from tests.test_fee_source_units_offline import METHOD


class FeeRetainedVersionTests(unittest.TestCase):
    def test_current_rules_keep_original_knowledge_and_arithmetic_versions(self):
        identifier = METHOD_REGISTRY[METHOD]['knowledge_id']
        old = RETAINED_VERSIONS[(identifier, 1)]
        before = fingerprint(old)
        self.assertEqual(old['version'], 1)
        self.assertNotIn('previous_version', old)
        current = ResearchKnowledge().read('ETF', '比较费率', [METHOD])
        self.assertEqual(current['index'][identifier], 2)
        historical = copy.deepcopy(current)
        historical['entries'] = [copy.deepcopy(old) if entry['id'] == identifier else entry
                                 for entry in historical['entries']]
        historical['index'][identifier] = 1
        ResearchKnowledge().validate_package(historical)
        self.assertEqual(fingerprint(RETAINED_VERSIONS[(identifier, 1)]), before)
        self.assertEqual(METHOD_REGISTRY[METHOD]['implementation_version'], 1)
        self.assertIn('raw_unit与unit仍填%', PHASE_RULES['extract'])


if __name__ == '__main__':
    unittest.main()
