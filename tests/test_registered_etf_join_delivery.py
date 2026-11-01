"""A missing explicit share selector still follows both native validators."""
try:
    from . import test_flat_api_delivery_chain as chain
except ImportError:
    import test_flat_api_delivery_chain as chain


class RegisteredETFJoinDeliveryTests(chain.FlatAPIDeliveryChainTests):
    def selections(self):
        specs = super().selections()
        for spec in specs:
            if spec['record_locator']['field'] != 'close':
                continue
            spec['calculation_context'].pop('share_class', None)
            spec.get('calculation_context_basis', {}).pop('share_class', None)
        return specs

    def test_selected_api_cells_reach_checkpoint_calculation_save_and_export(self):
        super().test_selected_api_cells_reach_checkpoint_calculation_save_and_export()
        saved = self.agent._get(self.task['id'])
        price = next(e for e in saved['evidence'] if e['record_locator']['field'] == 'close')
        self.assertEqual(price['calculation_context_joins'][0]['registry'], 'same_code_same_valuation_date_etf_share')
        self.assertFalse(price['calculation_context_joins'][0]['financial_approval'])
        self.assertEqual(self.agent.export(saved['id'])['content']['registered_etf_share_join_version'], 1)
        self.assertEqual(saved['ledger']['used']['model_calls'], 0)
