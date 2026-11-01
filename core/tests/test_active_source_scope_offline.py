"""Current provider scope excludes Wind without rewriting historical records."""

import unittest

from researchhelper.evidence import CAPABILITIES, active_source_capabilities


class ActiveSourceScopeTests(unittest.TestCase):
    def test_only_current_providers_and_local_materials_are_public(self):
        self.assertEqual(set(active_source_capabilities()), {"ifind", "tinyshare", "material"})

    def test_legacy_identifier_is_explicitly_excluded(self):
        self.assertTrue(CAPABILITIES["wind"]["excluded"])
        self.assertEqual(CAPABILITIES["wind"]["registered_operations"], [])

    def test_public_inventory_cannot_mutate_the_internal_registry(self):
        inventory = active_source_capabilities()
        inventory["ifind"]["fields"].clear()
        self.assertTrue(CAPABILITIES["ifind"]["fields"])


if __name__ == "__main__":
    unittest.main()
