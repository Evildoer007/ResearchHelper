"""Discard bad scalar addresses whole, never convert or approve them."""
import copy
import unittest

try:
    import test_financial_orphan_selections as fixtures
except ModuleNotFoundError:
    from tests import test_financial_orphan_selections as fixtures
from researchhelper.output_schema import (decode_original_batch, ORIGINAL_SELECTION_REJECTIONS,
    StructuredOutputError, validated_original_selection_rejections, _financial_selector_frame_schema, _matches)
from researchhelper.planning import input_fingerprint


class FinancialAddressIsolationTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.FinancialOrphanSelectionTests.setUpClass.__func__)
    setUp = fixtures.FinancialOrphanSelectionTests.setUp
    tearDown = fixtures.FinancialOrphanSelectionTests.tearDown
    wire = fixtures.FinancialOrphanSelectionTests.wire
    selection = fixtures.FinancialOrphanSelectionTests.selection
    batch = fixtures.FinancialOrphanSelectionTests.batch

    def enable_address_isolation(self):
        # Extraction-only rejection cannot change the research/source scope.
        before = input_fingerprint(self.task)
        self.task['financial_address_isolation_version'] = 1
        self.assertEqual(input_fingerprint(self.task), before)

    def test_scalar_address_errors_cannot_erase_three_valid_independent_rows(self):
        self.enable_address_isolation()
        schema, base = self.batch()
        shape = schema['properties']['sources']['items']['anyOf'][0]['properties']['selection']['properties']['amount_cells']['items']
        frame = _financial_selector_frame_schema(shape, self.contract['question_ids'], isolate_address_scalars=True)
        for invalid in (7, 7.5, None, True):
            wire = copy.deepcopy(base)
            wire['sources'][0]['selection']['amount_cells'][0]['row_reference'] = invalid
            item = wire['sources'][0]['selection']['amount_cells'][0]
            self.assertFalse(_matches(item, shape))
            self.assertTrue(_matches(item, frame))
        # One full decoding pass proves independent records survive; all four
        # scalar spellings were classified against the actual same schema.
        for invalid in (7,):
            wire = copy.deepcopy(base)
            wire['sources'][0]['selection']['amount_cells'][0]['row_reference'] = invalid
            before = copy.deepcopy(wire)
            result = decode_original_batch(wire, schema, (self.task, self.views))
            self.assertEqual([item['value'] for item in result['evidence_specs']], [-37859085, -33026925, 71690439])
            diagnostic = validated_original_selection_rejections(result[ORIGINAL_SELECTION_REJECTIONS], schema)
            self.assertEqual((diagnostic['submitted_items'], diagnostic['accepted_items']), (4, 3))
            self.assertFalse(diagnostic['automatic_repair'] or diagnostic['evidence_approved'])
            self.assertEqual(wire, before)

    def test_all_invalid_addresses_produce_no_facts_calculations_or_coverage(self):
        self.enable_address_isolation()
        schema, wire = self.batch()
        for item in wire['sources'][0]['selection']['amount_cells']: item['row_reference'] = None
        result = decode_original_batch(wire, schema, (self.task, self.views))
        self.assertEqual(result['evidence_specs'], [])
        self.assertEqual(result[ORIGINAL_SELECTION_REJECTIONS]['accepted_items'], 0)
        self.assertFalse(self.task['evidence'] or self.task['calculations'])

    def test_bad_address_never_hides_wrong_authority_need_types_or_containers(self):
        self.enable_address_isolation()
        schema, base = self.batch()
        shape = schema['properties']['sources']['items']['anyOf'][0]['properties']['selection']['properties']['amount_cells']['items']
        frame = _financial_selector_frame_schema(shape, self.contract['question_ids'], isolate_address_scalars=True)
        for issue in ('authority', 'object', 'scope', 'need_type', 'unknown_question', 'array_address', 'object_address', 'missing_address'):
            wire = copy.deepcopy(base)
            item = wire['sources'][0]['selection']['amount_cells'][0]
            item['row_reference'] = None
            if issue == 'authority': item['financial_approval'] = True
            elif issue == 'object': item['object'] = 'foreign'
            elif issue == 'scope': item['review']['scope'] = 'approved'
            elif issue == 'need_type': item['review']['need_links']['q2'] = 'not_an_array'
            elif issue == 'unknown_question': item['review']['need_links']['foreign'] = []
            elif issue == 'array_address': item['row_reference'] = [1]
            elif issue == 'object_address': item['row_reference'] = {'line': 1}
            else: del item['row_reference']
            with self.subTest(issue=issue): self.assertFalse(_matches(item, frame))
        # The real batch rejects a malformed container as well. The remaining
        # authority failures use the same full rejection classifier above.
        with self.assertRaises(StructuredOutputError):
            decode_original_batch(wire, schema, (self.task, self.views))

    def test_legacy_version_still_rejects_wrong_address_types_for_the_whole_batch(self):
        schema, wire = self.batch()
        wire['sources'][0]['selection']['amount_cells'][0]['row_reference'] = 7
        with self.assertRaises(StructuredOutputError):
            decode_original_batch(wire, schema, (self.task, self.views))


if __name__ == '__main__': unittest.main()
