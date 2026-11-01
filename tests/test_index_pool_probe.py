"""Offline diagnostic-shape checks, not a new production adapter."""
import unittest
from tests.probe_index_pool_current import parameters, inventory, retained_response
from researchhelper.contracts import ContractError


class IndexPoolProbeTests(unittest.TestCase):
    def test_only_explicit_example_variants_and_documented_block(self):
        for report in ('p03425','p03291'):
            params=parameters(report)
            self.assertEqual(params['reportname'],report)
            self.assertEqual(params['functionpara'],dict(date='20260930',blockname='001005260',iv_type='allcontract'))
        with self.assertRaises(ContractError):
            parameters('unknown')

    def test_unknown_columns_are_described_not_approved_or_renamed(self):
        value=inventory(dict(errorcode=0,tables=[dict(table={'unknown':[None,1]})]))
        self.assertEqual(value['tables'][0]['field_lengths'],{'unknown':2})
        self.assertNotIn('weight',value)
        self.assertNotIn('approved',value)

    def test_unexpected_response_shape_is_not_an_empty_valid_constituent_set(self):
        self.assertEqual(inventory(None),dict(shape='not_object'))
        self.assertEqual(inventory(dict(tables={})),dict(errorcode=None,top_fields=['tables'],tables_type='dict',tables=[]))

    def test_response_checkpoint_keeps_json_without_pretending_it_is_pdf_material(self):
        payload=dict(errorcode=0,tables=[dict(table={'unknown':[None,1]})])
        retained=retained_response(payload)
        self.assertEqual(retained['business_snapshot'],payload)
        self.assertFalse(retained['research_evidence'])
        self.assertEqual(len(retained['business_sha256']),64)

    def test_followup_quote_acceptance_rejects_empty_or_misaligned_observations(self):
        from tests.probe_index_pool_adapter import quote_inventory_matches
        valid=[dict(code='600028.SH',dates=['2026-09-30'],columns=[dict(
            field='close',count=1,numeric_count=1,missing_count=0,aligned=True)])]
        self.assertTrue(quote_inventory_matches(valid,'600028.SH'))
        self.assertFalse(quote_inventory_matches([],'600028.SH'))
        self.assertFalse(quote_inventory_matches(valid,'600030.SH'))
        self.assertFalse(quote_inventory_matches([dict(valid[0],dates=['2026-09-29'])],'600028.SH'))
