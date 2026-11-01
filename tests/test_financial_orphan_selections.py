"""An unowned local item is rejected whole, without inventing an owner."""
import copy
import unittest

try:
    import test_financial_amount_selection as fixtures
except ModuleNotFoundError:
    from tests import test_financial_amount_selection as fixtures
from researchhelper.output_schema import (original_batch_output_schema, original_batch_prompt,
    decode_original_batch, ORIGINAL_SELECTION_REJECTIONS, validated_original_selection_rejections,
    StructuredOutputError)


class FinancialOrphanSelectionTests(unittest.TestCase):
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
        wire = dict(version=1, binding_ref=prompt['batch_binding_ref'], sources=[dict(
            source_ref=prompt['source_choices'][0]['source_ref'], selection=self.wire())])
        return schema, wire

    def test_one_unowned_item_never_erases_three_independent_valid_amounts(self):
        schema, wire = self.batch()
        wire['sources'][0]['selection']['amount_cells'][0]['review']['need_links'] = {qid: None for qid in self.contract['question_ids']}
        before = copy.deepcopy(wire)
        result = decode_original_batch(wire, schema, (self.task, self.views))
        self.assertEqual([item['value'] for item in result['evidence_specs']], [-37859085, -33026925, 71690439])
        diagnostic = validated_original_selection_rejections(result[ORIGINAL_SELECTION_REJECTIONS], schema)
        self.assertEqual((diagnostic['submitted_items'], diagnostic['accepted_items']), (4, 3))
        self.assertEqual(diagnostic['rejected_items'][0]['category'], 'selection_decode_rejected')
        self.assertEqual(wire, before)
        self.assertFalse(self.task['evidence'] or self.task['calculations'])

    def test_all_unowned_items_return_no_evidence_and_no_coverage_approval(self):
        schema, wire = self.batch()
        for item in wire['sources'][0]['selection']['amount_cells']:
            item['review']['need_links'] = {qid: None for qid in self.contract['question_ids']}
        result = decode_original_batch(wire, schema, (self.task, self.views))
        self.assertEqual(result['evidence_specs'], [])
        diagnostic = validated_original_selection_rejections(result[ORIGINAL_SELECTION_REJECTIONS], schema)
        self.assertEqual((diagnostic['submitted_items'], diagnostic['accepted_items']), (4, 0))
        self.assertFalse(diagnostic['automatic_repair'] or diagnostic['evidence_approved'] or diagnostic['coverage_approved'])

    def test_explicit_background_owner_is_not_confused_with_no_owner(self):
        schema, wire = self.batch()
        first = wire['sources'][0]['selection']['amount_cells'][0]
        first['review']['need_links'] = {qid: ([] if qid == 'q2' else None) for qid in self.contract['question_ids']}
        result = decode_original_batch(wire, schema, (self.task, self.views))
        self.assertEqual(len(result['evidence_specs']), 4)
        self.assertNotIn(ORIGINAL_SELECTION_REJECTIONS, result)
        self.assertEqual(result['evidence_specs'][0]['question_ids'], ['q2'])
        self.assertFalse(result['evidence_specs'][0]['review'].get('evidence_need_texts'))

    def test_changed_authority_is_never_hidden_by_an_unowned_item(self):
        schema, wire = self.batch()
        first = wire['sources'][0]['selection']['amount_cells'][0]
        first['review']['need_links'] = {qid: None for qid in self.contract['question_ids']}
        first['object'] = 'foreign_subject'
        with self.assertRaises(StructuredOutputError):
            decode_original_batch(wire, schema, (self.task, self.views))


if __name__ == '__main__': unittest.main()
