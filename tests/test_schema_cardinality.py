"""Finite schema length/uniqueness constraints match the actual wire checks."""
import unittest
from researchhelper.output_schema import _matches,schema_diagnostic


class SchemaCardinalityTests(unittest.TestCase):
    def test_declared_array_lower_and_upper_bounds_are_enforced(self):
        schema=dict(type='array',items=dict(type='string'),minItems=1,maxItems=2)
        self.assertFalse(_matches([],schema))
        self.assertTrue(_matches(['a'],schema))
        self.assertFalse(_matches(['a','b','c'],schema))

    def test_undeclared_lower_bound_does_not_invent_required_items(self):
        self.assertTrue(_matches([],dict(type='array',items=dict(type='string'))))

    def test_global_array_capacity_is_not_expanded(self):
        schema=dict(type='array',items=dict(type='integer'),maxItems=1000)
        self.assertFalse(_matches(list(range(201)),schema))

    def test_string_bounds_are_checked_without_changing_values(self):
        schema=dict(type='string',minLength=1,maxLength=3)
        self.assertFalse(_matches('',schema))
        self.assertTrue(_matches('abc',schema))
        self.assertFalse(_matches('abcd',schema))

    def test_duplicate_strings_and_nested_objects_are_rejected(self):
        schema=dict(type='array',items=dict(type='object',properties=dict(a=dict(type='integer'),
            b=dict(type='string'))),uniqueItems=True)
        self.assertFalse(_matches([dict(a=1,b='x'),dict(b='x',a=1)],schema))
        self.assertTrue(_matches([dict(a=1,b='x'),dict(a=2,b='x')],schema))

    def test_json_numbers_compare_equally_but_boolean_is_distinct(self):
        schema=dict(type='array',items=dict(anyOf=[dict(type='number'),dict(type='boolean')]),uniqueItems=True)
        self.assertFalse(_matches([1,1.0],schema))
        self.assertTrue(_matches([True,1],schema))

    def test_anyof_branches_keep_their_own_bounds(self):
        schema=dict(anyOf=[dict(type='array',items=dict(type='string'),minItems=1),
                           dict(type='string',minLength=1)])
        self.assertFalse(_matches([],schema))
        self.assertFalse(_matches('',schema))
        self.assertTrue(_matches(['a'],schema))

    def test_finite_diagnostics_do_not_record_submitted_values(self):
        schema=dict(type='array',items=dict(type='string'),minItems=1,uniqueItems=True)
        self.assertEqual(schema_diagnostic([],schema)['schema_issues'][0]['code'],'too_few_items')
        result=schema_diagnostic(['secret-probe','secret-probe'],schema)
        self.assertEqual(result['schema_issues'][0]['code'],'duplicate_items')
        self.assertNotIn('secret-probe',str(result))
        self.assertFalse(result['submitted_values_retained'])
