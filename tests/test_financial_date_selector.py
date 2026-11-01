"""Finite date addresses have the same domain locally and at the provider."""
import copy
import importlib
import unittest

fixtures = importlib.import_module((__package__ + '.' if __package__ else '') + 'test_financial_context_choices')
from researchhelper.financial_sources import original_date_anchors
from researchhelper.output_schema import _matches, decode_financial_extraction_output
from researchhelper.contracts import ContractError


class FinancialDateSelectorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FinancialContextChoicesTests('test_actual_original_contexts_pass_source_and_applicability_without_mocking')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_every_offered_index_is_in_the_exact_finite_schema(self):
        fixture = self.fixture
        dates = original_date_anchors(fixture.views[0])
        for group in ('financial_cells', 'text_quotes'):
            shape = fixture.schema['properties'][group]['items']['properties']['observation_anchor']
            self.assertEqual(shape, {'type': 'integer', 'enum': list(range(len(dates)))})

    def test_wrong_index_or_line_number_is_rejected_not_clipped(self):
        fixture = self.fixture
        for value in (-1, len(original_date_anchors(fixture.views[0])), 999999, True, '0'):
            wire = copy.deepcopy(fixture.wire)
            wire['financial_cells'][0]['observation_anchor'] = value
            self.assertFalse(_matches(wire, fixture.schema))
            with self.assertRaises(ContractError):
                decode_financial_extraction_output(wire, fixture.schema, (fixture.task, fixture.views))

    def test_existing_valid_wire_still_decodes_exact_observed_date(self):
        fixture = self.fixture
        decoded = fixture.decode()
        self.assertTrue(decoded['evidence_specs'])
        self.assertTrue(all(item['observation_date'] == '2026-06-30' for item in decoded['evidence_specs']))
