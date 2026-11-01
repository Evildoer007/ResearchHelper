"""Schema-derived mixed-source field guidance, with no model or data calls."""
import copy
import json
import unittest

from researchhelper.output_schema import object_schema, selection_field_guide


class SelectionFieldGuideTests(unittest.TestCase):
    def branch(self, kind='text', source='source_3', **fields):
        return object_schema(dict(source_ref=dict(type='string', enum=[source]),
            kind=dict(type='string', enum=[kind]), **fields))

    def test_source_namespace_is_preserved_not_merged_by_kind(self):
        a = selection_field_guide(self.branch(source='source_1'))
        b = selection_field_guide(self.branch(source='source_2'))
        self.assertNotEqual(a, b)
        self.assertEqual(a['source_refs'], ['source_1'])

    def test_complete_part_and_range_remain_separate_formats(self):
        scalar = dict(type='integer', minimum=0)
        branch = self.branch(text_span=dict(anyOf=[object_schema(dict(part_ref=scalar)),
            object_schema(dict(part=scalar, start=scalar, end=scalar))]))
        shapes = selection_field_guide(branch)['nested_field_alternatives']['text_span']
        self.assertEqual(shapes, [['part_ref'], ['part', 'start', 'end']])
        self.assertNotIn(['part_ref', 'start', 'end'], shapes)

    def test_api_fields_stay_inside_selection_and_evidence_specs(self):
        branch = self.branch('api', selection=object_schema(dict(
            version=dict(type='integer', enum=[3]), evidence_specs=dict(type='array',
                items=object_schema(dict(id=dict(type='string'), period=dict(type='string'),
                    api_cell_selections=dict(type='array', items=dict(type='string'))))))),
            additional_contexts=dict(type='array', items=object_schema(dict(context_ref=dict(type='string')))))
        guide = selection_field_guide(branch)
        self.assertNotIn('period', guide['fields'])
        self.assertEqual(guide['nested_field_alternatives']['selection.evidence_specs[]'],
            [['id', 'period', 'api_cell_selections']])
        self.assertEqual(guide['nested_field_alternatives']['additional_contexts[]'], [['context_ref']])

    def test_guide_never_selects_values_or_changes_schema(self):
        branch = self.branch(period=dict(type='string', enum=['2026-10-21']),
            value=dict(type='number', enum=[4.1856]))
        original = copy.deepcopy(branch)
        encoded = json.dumps(selection_field_guide(branch))
        self.assertNotIn('2026-10-21', encoded)
        self.assertNotIn('4.1856', encoded)
        self.assertEqual(branch, original)
        self.assertNotIn('approved', encoded)

    def test_no_review_ownership_fields_are_synthesized(self):
        branch = self.branch(review=object_schema(dict(scope=dict(type='string'))))
        guide = selection_field_guide(branch)
        self.assertEqual(guide['nested_field_alternatives']['review'], [['scope']])
        self.assertNotIn('need_links', json.dumps(guide))

    def test_known_review_location_is_not_flattened(self):
        branch = self.branch(review=object_schema(dict(need_links=dict(type='array',
            items=object_schema(dict(question_id=dict(type='string'),
                need_ids=dict(type='array', items=dict(type='string'))))))))
        guide = selection_field_guide(branch)
        self.assertEqual(guide['nested_field_alternatives']['review.need_links[]'],
            [['question_id', 'need_ids']])
        self.assertNotIn('need_ids', guide['fields'])

    def test_existing_api_prompt_uses_source_specific_projection(self):
        try:
            from . import test_api_context_guide as fixtures
        except ImportError:
            import test_api_context_guide as fixtures
        control = fixtures.APIContextGuideTests()
        control.setUp()
        self.addCleanup(control.doCleanups)
        control.test_model_sees_selection_location_and_no_automatic_context_rule()


if __name__ == '__main__':
    unittest.main()
