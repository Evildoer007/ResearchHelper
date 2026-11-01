"""Check the assertions hidden behind outdated fixed catalog-size tests."""

import unittest

from researchhelper.contracts import METHOD_REGISTRY
from researchhelper.knowledge import ENTRIES, ResearchKnowledge
from tests import test_earnings_cash_methods_offline as earnings
from tests import test_knowledge_market_interfaces_offline as interfaces


class CurrentCatalogBoundaryTests(unittest.TestCase):
    def test_every_published_domain_definition_has_source_conditions_and_verification(self):
        entries = [item for item in ENTRIES if 'knowledge_role' in item]
        self.assertTrue({'rh_k_bank_interest_metrics', 'rh_k_bank_rate_repricing', 'rh_k_index_change'}.
                        issubset({item['id'] for item in entries}))
        for item in entries:
            with self.subTest(entry=item['id']):
                self.assertIsNone(item['publication_date'])
                self.assertIn('未知', item['publication_date_status'])
                self.assertEqual(item['access'], 'public')
                self.assertTrue(item['source_reference']['sections'])
                self.assertTrue(item['source_reference']['url'].startswith('https://'))
                self.assertTrue(item['conditions'] and item['counterexample'] and item['verification_indicators'])
                self.assertTrue(item['dependencies'])
                self.assertIn('不是历史当时规则', item['temporal_scope'])

    def test_registered_earnings_methods_keep_dependencies_and_fee_definition(self):
        knowledge = ResearchKnowledge()
        for method in earnings.METHODS:
            package = knowledge.read('个股', '公司研究', methods=[method])
            self.assertIn(METHOD_REGISTRY[method]['knowledge_id'], package['index'])
            self.assertIn('rh_k_cash_profit', package['index'])
            self.assertTrue(knowledge.method_binding(package, method))
        self.assertIn(METHOD_REGISTRY['management_custody_fee_comparison_v1']['knowledge_id'],
                      {item['id'] for item in knowledge.catalog()})

    def test_app_catalog_does_not_approve_supplier_permissions_or_task_evidence(self):
        fixture = interfaces.KnowledgeMarketInterfacesTests()
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        bootstrap = fixture.fixture.request('/api/bootstrap')[0]
        ids = {item['id'] for item in bootstrap['knowledge']}
        self.assertEqual(ids, {item['id'] for item in ResearchKnowledge().catalog()})
        self.assertTrue({'rh_k_cash_profit', 'rh_k_rate_transmission', 'rh_k_index_structure',
                         'rh_k_etf_exposure', 'rh_k_index_change', 'rh_k_bank_rate_repricing'} <= ids)
        self.assertEqual(bootstrap['field_registry']['tinyshare.daily_basic']['permission'], '未验收')
        task = fixture.app_task()
        self.assertEqual(task['status'], '部分完成', task.get('error'))
        self.assertIn('rh_k_cash_profit', task['knowledge']['index'])
        self.assertNotIn('rh_k_rate_transmission', task['knowledge']['index'])
        self.assertEqual(task['ledger']['used']['model_calls'], 0)
        self.assertFalse(task['evidence'] or task['theses'])


if __name__ == '__main__':
    unittest.main()
