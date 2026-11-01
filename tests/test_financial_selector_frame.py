"""Reject malformed local assignments without losing independent selections."""
import copy
import unittest

try:
    import test_financial_amount_selection as fixtures
except ModuleNotFoundError:
    from tests import test_financial_amount_selection as fixtures
from researchhelper.contracts import fingerprint
from researchhelper.output_schema import (StructuredOutputError, _matches,
    _financial_selector_frame_schema, object_schema, array_schema,
    original_batch_output_schema, original_batch_prompt, decode_original_batch,
    ORIGINAL_SELECTION_REJECTIONS)


class FinancialSelectorFrameTests(unittest.TestCase):
    def schema(self):
        return object_schema(dict(object=dict(type='string', enum=['300750.SZ']),
            quote_reference=dict(type='string', enum=['read_quote']),
            review=object_schema(dict(scope=dict(type='string', enum=['current_original_descriptive_only']),
                need_links=object_schema(dict(q4=dict(array_schema(dict(type='string')), maxItems=0)))))))

    def item(self):
        return dict(object='300750.SZ', quote_reference='read_quote',
            review=dict(scope='current_original_descriptive_only', need_links=dict(q4=['ineligible_need'])))

    def test_zero_capacity_need_link_is_local_not_an_authority_change(self):
        schema = self.schema(); frame = _financial_selector_frame_schema(schema, ['q4'])
        item = self.item(); before = copy.deepcopy(item)
        self.assertFalse(_matches(item, schema))
        self.assertTrue(_matches(item, frame))
        self.assertEqual(item, before)

    def test_unknown_question_authority_fields_and_wrong_types_remain_hard_failures(self):
        frame = _financial_selector_frame_schema(self.schema(), ['q4'])
        for issue in ('question', 'scope', 'object', 'authority', 'type'):
            item = self.item()
            if issue == 'question': item['review']['need_links']['foreign'] = []
            elif issue == 'scope': item['review']['scope'] = 'approved'
            elif issue == 'object': item['object'] = 'foreign'
            elif issue == 'authority': item['review']['financial_approval'] = True
            else: item['review']['need_links']['q4'] = 'not_an_array'
            with self.subTest(issue=issue): self.assertFalse(_matches(item, frame))

    def test_local_reference_is_only_classified_not_repaired_or_approved(self):
        schema = self.schema(); item = self.item()
        item['quote_reference'] = 'unknown_quote'
        self.assertTrue(_matches(item, _financial_selector_frame_schema(schema, ['q4'])))
        self.assertFalse(_matches(item, schema))
        self.assertEqual(item['quote_reference'], 'unknown_quote')

    def test_unbounded_local_arrays_remain_hard_failures(self):
        item = self.item(); item['review']['need_links']['q4'] *= 201
        self.assertFalse(_matches(item, _financial_selector_frame_schema(self.schema(), ['q4'])))


class FinancialNeedCardinalityBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.FinancialAmountSelectionTests.setUpClass()
        cls.raw, cls.original = fixtures.FinancialAmountSelectionTests.raw, fixtures.FinancialAmountSelectionTests.original

    setUp = fixtures.FinancialAmountSelectionTests.setUp
    tearDown = fixtures.FinancialAmountSelectionTests.tearDown
    wire = fixtures.FinancialAmountSelectionTests.wire
    selection = fixtures.FinancialAmountSelectionTests.selection

    def batch(self):
        schema = original_batch_output_schema(self.task, self.views)
        prompt = original_batch_prompt(self.payload, self.task)
        selected = self.wire()
        selected['amount_cells'][0]['review']['need_links']['q2'] = [fingerprint(self.need)] * 2
        wire = dict(version=1, binding_ref=prompt['batch_binding_ref'],
            sources=[dict(source_ref=prompt['source_choices'][0]['source_ref'], selection=selected)])
        return schema, wire

    def test_cardinality_error_discards_only_that_item_without_shortening_its_links(self):
        schema, wire = self.batch(); before = copy.deepcopy(wire)
        result = decode_original_batch(wire, schema, (self.task, self.views))
        self.assertEqual([item['value'] for item in result['evidence_specs']], [-37859085, -33026925, 71690439])
        diagnostic = result[ORIGINAL_SELECTION_REJECTIONS]
        self.assertEqual((diagnostic['submitted_items'], diagnostic['accepted_items']), (4, 3))
        self.assertFalse(diagnostic['automatic_repair'] or diagnostic['evidence_approved'])
        self.assertEqual(wire, before)

    def test_later_type_error_cannot_be_hidden_by_truncated_local_diagnostics(self):
        schema, wire = self.batch()
        item = wire['sources'][0]['selection']['amount_cells'][0]
        item['review']['need_links']['q1'] = ['unknown'] * 8
        item['review']['need_links']['q2'] = 'not_an_array'
        with self.assertRaises(StructuredOutputError):
            decode_original_batch(wire, schema, (self.task, self.views))


if __name__ == '__main__':
    unittest.main()
