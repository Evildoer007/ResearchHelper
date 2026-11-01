"""Keep financial assertions while adapting only migrated paths/catalogue.

Fixture cleanup is registered before setup so a fixture setup error cannot
leave its private HTTP server alive and hang the test interpreter.
"""
import shutil
from pathlib import Path
from unittest.mock import patch

from researchhelper.contracts import METHOD_REGISTRY
from researchhelper.knowledge import ResearchKnowledge
from tests import test_earnings_cash_methods_offline as original
from tests.skill_fixture_support import source_file

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = {
    'rh_k_management_custody_fee', 'rh_k_cost_pass', 'rh_k_fee', 'rh_k_residual',
    'rh_k_fact', 'rh_k_fee_difference', 'rh_k_price_change', 'rh_k_nav_premium',
    'rh_k_gross_margin', 'rh_k_cash_profit', 'rh_k_rate_transmission',
    'rh_k_index_structure', 'rh_k_etf_exposure', 'rh_k_net_margin',
    'rh_k_cash_profit_ratio', 'rh_k_bank_interest_metrics', 'rh_k_bank_rate_repricing',
    'rh_k_index_change', 'rh_k_etf_measures',
}


class CurrentEarningsCashMethodsTests(original.EarningsCashMethodsTests):
    def test_required_method_brings_cash_flow_mechanism_dependency(self):
        for method in original.METHODS:
            package = ResearchKnowledge().read('个股', '公司研究', methods=[method])
            self.assertIn(METHOD_REGISTRY[method]['knowledge_id'], package['index'])
            self.assertIn('rh_k_cash_profit', package['index'])
            self.assertTrue(ResearchKnowledge().method_binding(package, method))
        self.assertEqual({entry['id'] for entry in ResearchKnowledge().catalog()}, CATALOGUE)


class CurrentEarningsCashInterfacesTests(original.EarningsCashInterfacesTests):
    def test_app_and_independent_skill_deliver_identical_new_method_semantics(self):
        fixture = original.interfaces.FinancialMethodInterfacesTests()
        self.addCleanup(fixture.doCleanups)
        copyfile = shutil.copyfile

        def current_copy(source, destination, *args, **kwargs):
            source = Path(source)
            if source.is_relative_to(ROOT / 'app/web'):
                source = source_file(source.relative_to(ROOT).as_posix())
            return copyfile(source, destination, *args, **kwargs)

        with patch.object(shutil, 'copyfile', side_effect=current_copy):
            fixture.setUp()
        with patch.object(original.legacy_workflow.MethodDependencyWorkflowTests, 'prepare',
                          lambda current, method: original.prepare_financial(current.agent, method)):
            for method in original.METHODS:
                fixture.run_method(method)

    def test_updated_catalog_preserves_original_boundary_assertions(self):
        fixture = original.catalog_fixture.KnowledgeMarketInterfacesTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        bootstrap = fixture.fixture.request('/api/bootstrap')[0]
        self.assertEqual({entry['id'] for entry in bootstrap['knowledge']}, CATALOGUE)
        self.assertEqual(bootstrap['field_registry']['tinyshare.daily_basic']['permission'], '未验收')
        task = fixture.app_task()
        self.assertEqual(task['status'], '部分完成', task.get('error'))
        self.assertIn('rh_k_cash_profit', task['knowledge']['index'])
        self.assertNotIn('rh_k_rate_transmission', task['knowledge']['index'])
        self.assertEqual(task['ledger']['used']['model_calls'], 0)
        self.assertFalse(task['evidence'] or task['theses'])
