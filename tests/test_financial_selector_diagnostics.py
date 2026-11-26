"""Local selector failures identify registered fields without retaining values.

These synthetic transport checks do not approve any financial evidence. The
unchanged source/applicability gates are checked by the separate data suites.
"""
import copy
import json
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError
from researchhelper.output_schema import (object_schema, array_schema,
    _decode_independent_financial_batch, ORIGINAL_SELECTION_REJECTIONS)


class FinancialSelectorDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.task = dict(questions=[dict(id='q2')], financial_address_isolation_version=1,
            financial_need_map_version=1, financial_question_owner_version=1)
        literal = lambda value: dict(type='string', enum=[value])
        self.item = object_schema(dict(id=dict(type='string'), object=literal('002594.SZ'),
            financial_context_reference=literal('context_1'), observation_reference=literal('observation_1'),
            shared_context_references=object_schema(dict(entity_id=literal('entity_1'), accounting_standard=literal('standard_1'))),
            review=object_schema(dict(scope=literal('current_original_descriptive_only'),
                need_links=object_schema(dict(q2=array_schema(literal('need_1'))))))))
        self.selection = object_schema(dict(version=dict(type='integer', enum=[9]),
            financial_cells=array_schema(self.item), text_quotes=dict(array_schema(object_schema({})), maxItems=0),
            amount_cells=dict(array_schema(object_schema({})), maxItems=0)))
        self.schema = object_schema(dict(version=dict(type='integer', enum=[1]),
            binding_ref=literal('bound'), sources=array_schema(object_schema(dict(source_ref=literal('source_1'), selection=self.selection)))))
        self.valid = dict(id='valid', object='002594.SZ', financial_context_reference='context_1',
            observation_reference='observation_1', shared_context_references=dict(entity_id='entity_1', accounting_standard='standard_1'),
            review=dict(scope='current_original_descriptive_only', need_links=dict(q2=['need_1'])))
        self.choices = [dict(source_ref='source_1', kind='financial', schema=self.selection, view={})]

    def decode(self, items):
        wire = dict(version=1, binding_ref='bound', sources=[dict(source_ref='source_1',
            selection=dict(version=9, financial_cells=items, text_quotes=[], amount_cells=[]))])
        before = copy.deepcopy(wire)
        def local_decoder(task, choice, selection):
            return [dict(id=item['id']) for item in selection['financial_cells']]

        choices = {choice['source_ref']: choice for choice in self.choices}
        with patch('researchhelper.output_schema._decode_batch_selection', side_effect=local_decoder):
            result = _decode_independent_financial_batch(wire, self.schema, self.task, choices)
        self.assertEqual(wire, before)
        return result

    def rejected(self, field, value):
        item = copy.deepcopy(self.valid)
        item[field] = value
        return self.decode([item])[ORIGINAL_SELECTION_REJECTIONS]['rejected_items'][0]

    def test_bad_cell_reports_its_registered_path_without_the_submitted_reference(self):
        row = self.rejected('financial_context_reference', 'private_cell_marker')
        self.assertEqual(row['diagnostic']['schema_issues'], [dict(path='$.financial_context_reference', code='enum_mismatch')])
        self.assertNotIn('private_cell_marker', json.dumps(row))
        self.assertFalse(row['diagnostic']['submitted_values_retained'])

    def test_bad_need_reports_the_question_local_field(self):
        item = copy.deepcopy(self.valid)
        item['review']['need_links']['q2'] = ['private_need_marker']
        result = self.decode([item])
        issue = result[ORIGINAL_SELECTION_REJECTIONS]['rejected_items'][0]['diagnostic']['schema_issues']
        self.assertEqual(issue, [dict(path='$.review.need_links.q2[*]', code='enum_mismatch')])
        self.assertNotIn('private_need_marker', json.dumps(result))

    def test_bad_shared_standard_and_observation_remain_separate(self):
        for field, value, path in (
            ('observation_reference', 'private_date_marker', '$.observation_reference'),
            ('shared_context_references', dict(entity_id='entity_1', accounting_standard='private_standard_marker'), '$.shared_context_references.accounting_standard')):
            with self.subTest(field=field):
                row = self.rejected(field, value)
                self.assertEqual(row['diagnostic']['schema_issues'], [dict(path=path, code='enum_mismatch')])
                self.assertFalse(row['diagnostic']['automatic_repair'])

    def test_one_rejected_reference_does_not_erase_a_valid_other_item(self):
        bad = dict(self.valid, id='bad', financial_context_reference='private_cell_marker')
        result = self.decode([bad, self.valid])
        self.assertEqual(result['evidence_specs'], [dict(id='valid')])
        self.assertEqual(result[ORIGINAL_SELECTION_REJECTIONS]['accepted_items'], 1)

    def test_all_rejected_items_remain_zero_adoption_with_individual_diagnostics(self):
        items = [dict(self.valid, id='bad_'+str(index), financial_context_reference='private_cell_marker') for index in range(21)]
        result = self.decode(items)
        rejected = result[ORIGINAL_SELECTION_REJECTIONS]
        self.assertEqual(result['evidence_specs'], [])
        self.assertEqual((rejected['submitted_items'], rejected['accepted_items']), (21, 0))
        self.assertTrue(all(row['diagnostic']['schema_issues'] for row in rejected['rejected_items']))
        self.assertFalse(rejected['evidence_approved'] or rejected['coverage_approved'] or rejected['automatic_repair'])

    def test_changed_authority_is_still_a_batch_failure(self):
        with self.assertRaises(ContractError):
            self.decode([dict(self.valid, object='600519.SH')])

    def test_valid_records_have_no_rejection_metadata(self):
        result = self.decode([self.valid])
        self.assertEqual(result, dict(evidence_specs=[dict(id='valid')]))


if __name__ == '__main__':
    unittest.main()
