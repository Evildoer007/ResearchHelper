"""Public catalog metadata is versioned, not a retroactive evidence approval."""

import copy
import unittest

from researchhelper.contracts import fingerprint
from researchhelper.knowledge import ENTRIES, RETAINED_VERSIONS, ResearchKnowledge
from tests import test_general_definition_answers_offline as fixtures


class ETFDefinitionMetadataTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GeneralDefinitionAnswersTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_published_metadata_has_complete_public_provenance_and_unknown_time(self):
        entry = next(item for item in ENTRIES if item['id'] == 'rh_k_etf_measures')
        self.assertEqual(entry['version'], 2)
        self.assertEqual(entry['previous_version'], 1)
        self.assertEqual(entry['access'], 'public')
        self.assertIsNone(entry['publication_date'])
        self.assertIn('未知', entry['publication_date_status'])
        self.assertIn('不是历史当时规则', entry['temporal_scope'])
        for ref in [entry['source_reference'], *entry['additional_source_references']]:
            self.assertTrue(ref['url'].startswith('https://'))
            self.assertTrue(ref['sections'] and ref['publisher'])
        self.assertTrue(entry['dependencies'] and entry['verification_indicators'])

    def test_original_version_and_financial_text_remain_unchanged(self):
        old = RETAINED_VERSIONS[('rh_k_etf_measures', 1)]
        current = RETAINED_VERSIONS[('rh_k_etf_measures', 2)]
        self.assertIsInstance(old['source_reference'], list)
        self.assertEqual(old['version'], 1)
        self.assertNotIn('access', old)
        for field in ('title', 'content', 'conditions', 'counterexample', 'source'):
            self.assertEqual(old[field], current[field])

    def test_new_definition_does_not_reorder_the_retained_base_catalog(self):
        self.assertEqual([entry['id'] for entry in ENTRIES[:8]], [
            'rh_k_management_custody_fee', 'rh_k_cost_pass', 'rh_k_fee', 'rh_k_residual',
            'rh_k_fact', 'rh_k_fee_difference', 'rh_k_price_change', 'rh_k_nav_premium'])
        self.assertEqual(ENTRIES[-1]['id'], 'rh_k_etf_measures')

    def test_adopted_v1_pack_is_readable_and_not_upgraded_on_export(self):
        task = self.fixture.create()
        self.fixture.agent._adopt_method_knowledge(task)
        package = task['knowledge']
        index = next(i for i, entry in enumerate(package['entries']) if entry['id'] == 'rh_k_etf_measures')
        package['entries'][index] = copy.deepcopy(RETAINED_VERSIONS[('rh_k_etf_measures', 1)])
        package['index']['rh_k_etf_measures'] = 1
        before = fingerprint(package)
        ResearchKnowledge().validate_package(package)
        answer = ResearchKnowledge().definition_answer(task, task['questions'][0])
        self.assertEqual(answer['knowledge_refs'], [{'id': 'rh_k_etf_measures', 'version': 1}])
        self.assertFalse(answer['knowledge_integrity']['fact_approval'])
        self.assertEqual(fingerprint(package), before)
